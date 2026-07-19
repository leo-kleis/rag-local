import os

from fastmcp import Context

from rag_local.core import config as core_config
from rag_local.mcp.server import get_lock, mcp
from rag_local.services.project import setup_project_context
from rag_local.services.subprocess import run_cli_subprocess


@mcp.tool()
async def get_project_map(
    ctx: Context,
    project_path: str | None = None,
) -> str:
    """Returns a structural overview of the indexed codebase.

    Lists all indexed classes, services, controllers, models, and components
    grouped by scope (angular, nestjs, python) with their file paths.

    Call this tool at the start of a session to understand what exists in the
    project before making targeted queries with query_codebase. This prevents
    guessing class or service names that don't match the actual code.

    Args:
        project_path: Absolute path to the project repository.
    """
    async with get_lock():
        try:
            await ctx.report_progress(10, 100, message="Cargando configuración...")
            setup_project_context(project_path)
        except Exception as e:
            return f"Error de configuración: {e!s}"

        if not core_config.LANCEDB_PATH.exists() or not any(
            core_config.LANCEDB_PATH.iterdir()
        ):
            return (
                "NO_INDEX: No indexed database found at "
                f"{core_config.LANCEDB_PATH.resolve()}. "
                "Run ingest_codebase first."
            )

        try:
            repo_path = str(core_config.REPO_ROOT.resolve())
            cmd = [
                "uv",
                "run",
                "--project",
                str(core_config.RAG_ROOT),
                "rag-project-map",
                "--project-path",
                repo_path,
            ]

            env = os.environ.copy()
            env["RAG_REPO_ROOT"] = repo_path
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUTF8"] = "1"

            async def handle_stderr_line(line: str) -> None:
                if "Leyendo metadatos" in line:
                    await ctx.report_progress(
                        50, 100, message="Leyendo metadatos del índice..."
                    )

            await ctx.report_progress(
                20, 100, message="Iniciando subproceso de mapeo..."
            )
            try:
                res = await run_cli_subprocess(
                    cmd=cmd,
                    cwd=repo_path,
                    env=env,
                    timeout=60.0,
                    on_stderr_line=handle_stderr_line,
                )
            except TimeoutError:
                return "Error: El mapeo superó el límite de tiempo de 1 minuto."
            except Exception as sub_err:
                return f"Error al ejecutar el mapeo: {sub_err!s}"

            if res.returncode == 0:
                await ctx.report_progress(100, 100, message="Mapa estructurado listo.")
                return res.stdout.decode("utf-8", errors="replace")
            else:
                err_msg = res.stderr.decode("utf-8", errors="replace")
                if not err_msg:
                    err_msg = res.stdout.decode("utf-8", errors="replace")
                return f"Error en mapeo (código {res.returncode}): {err_msg}"
        except Exception as e:
            return f"Error al procesar el mapeo en el RAG local: {e!s}"
