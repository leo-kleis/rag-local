import os

from fastmcp import Context

from rag_local.core import config as core_config
from rag_local.mcp.server import get_lock, mcp
from rag_local.services.project import setup_project_context
from rag_local.services.subprocess import run_cli_subprocess


@mcp.tool()
async def get_styles_map(
    ctx: Context,
    project_path: str | None = None,
) -> str:
    """Returns a structural overview of the project's CSS styles, variables,

    and dead CSS.

    Lists all indexed CSS files, custom properties (--variables), and declared
    CSS classes not referenced in any UI component (.tsx, .jsx, .html).

    Call this tool when working on UI design, CSS styling, or auditing obsolete
    CSS classes.

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
                "rag-styles",
                "--project-path",
                repo_path,
            ]

            env = os.environ.copy()
            env["RAG_REPO_ROOT"] = repo_path
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUTF8"] = "1"

            await ctx.report_progress(30, 100, message="Obteniendo mapa de estilos...")

            res = await run_cli_subprocess(cmd, cwd=repo_path, env=env)
            await ctx.report_progress(100, 100, message="Completado.")

            stdout = res.stdout.decode("utf-8", errors="replace")
            if res.returncode != 0:
                stderr = res.stderr.decode("utf-8", errors="replace")
                err_msg = stderr.strip() or stdout.strip()
                return f"ERROR ({res.returncode}): rag-styles fallo.\n{err_msg}"
            return stdout
        except Exception as e:
            return f"Error al ejecutar rag-styles: {e!s}"
