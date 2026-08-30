import os
import sys

from fastmcp import Context

from rag_local.core import config as core_config
from rag_local.mcp.server import lock_manager, mcp
from rag_local.services.project import setup_project_context
from rag_local.services.subprocess import run_cli_subprocess


@mcp.tool()
async def get_project_map(
    ctx: Context,
    project_path: str,
    scope: str | None = None,
    path_filter: str | None = None,
    full_tree: bool = False,
    focus: str | None = None,
    max_chars: int | None = None,
) -> str:
    """Returns a structural overview of the indexed codebase prioritized by PageRank.

    Lists indexed classes, functions, models, and interfaces grouped
    by module and scope (python, angular, nestjs, nextjs-app).

    Call this tool at the start of a session to understand what exists in the
    project before making targeted queries with query_codebase. This prevents
    guessing class or service names that don't match the actual code.

    Args:
        project_path: Absolute path to the project repository.
        scope: Filter by scope (python, angular, nestjs, nextjs-app).
        path_filter: Optional filter by path or directory (e.g. 'src/bot_tv').
        full_tree: If true, includes the complete directory file tree.
        focus: Optional comma-separated paths to bias structural PageRank ranking.
        max_chars: Optional character budget limit to prevent token overflow.
    """
    try:
        setup_project_context(project_path)
    except Exception as e:
        return f"Error de configuración: {e!s}"

    target_repo = core_config.REPO_ROOT.resolve()

    async def report_wait(msg: str) -> None:
        await ctx.report_progress(5, 100, message=msg)

    async with lock_manager.acquire_read(target_repo, on_waiting=report_wait):
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
                sys.executable,
                "-m",
                "rag_local.cli.project_map",
                "--project-path",
                repo_path,
            ]
            if scope:
                cmd.extend(["--scope", scope])
            if path_filter and path_filter.strip():
                cmd.extend(["--path-filter", path_filter.strip()])
            if full_tree:
                cmd.append("--full-tree")
            if focus and focus.strip():
                cmd.extend(["--focus", focus.strip()])
            if max_chars is not None and max_chars > 0:
                cmd.extend(["--max-chars", str(max_chars)])

            env = os.environ.copy()
            env["RAG_REPO_ROOT"] = repo_path
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUTF8"] = "1"

            sync_msg: str | None = None

            async def handle_stderr_line(line: str) -> None:
                nonlocal sync_msg
                from rag_local.core.events import parse_sync_event

                event = parse_sync_event(line)
                if event is not None:
                    if event.message:
                        sync_msg = f"Auto-Sync: {event.message}"
                    await ctx.report_progress(
                        event.progress or 30,
                        100,
                        message=f"Auto-Sync: {event.message}",
                    )
                elif "Leyendo metadatos" in line:
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
                    timeout=core_config.DEFAULT_CLI_TIMEOUT,
                    on_stderr_line=handle_stderr_line,
                )
            except TimeoutError:
                return (
                    "Error en mapeo: El mapeo superó el límite de tiempo de 1 minuto."
                )
            except Exception as sub_err:
                return f"Error al ejecutar el mapeo: {sub_err!s}"

            if res.returncode == 0:
                await ctx.report_progress(100, 100, message="Mapa estructurado listo.")
                stdout_str = res.stdout.decode("utf-8", errors="replace")
                sync_prefix = f"[{sync_msg}]\n\n" if sync_msg else ""
                return sync_prefix + stdout_str
            else:
                err_msg = res.stderr.decode("utf-8", errors="replace")
                if not err_msg:
                    err_msg = res.stdout.decode("utf-8", errors="replace")
                return f"Error en mapeo (código {res.returncode}): {err_msg}"
        except Exception as e:
            return f"Error al procesar el mapeo en el RAG local: {e!s}"
