import os
import sys

from fastmcp import Context

from rag_local.core import config as core_config
from rag_local.mcp.server import get_lock, mcp
from rag_local.services.project import setup_project_context
from rag_local.services.subprocess import (
    parse_auto_sync_progress,
    run_cli_subprocess,
)


@mcp.tool()
async def trace_event_flow(
    ctx: Context,
    project_path: str | None = None,
    event_name: str = "",
    limit: int = 15,
) -> str:
    """Traces the complete lifecycle of events across backend and frontend.

    Maps the full cross-stack event chain:
    Backend Definition to Emitter to WebSocket Handler to Reducer to UI Component

    Args:
        project_path: Optional absolute path to the project repository.
        event_name: Optional event or action name to filter by.
        limit: Max number of events to show in global trace (default 15).
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
                sys.executable,
                "-m",
                "rag_local.cli.event_flow",
                "--project-path",
                repo_path,
                "--limit",
                str(limit),
            ]
            if event_name:
                cmd.extend(["--event", event_name])

            env = os.environ.copy()
            env["RAG_REPO_ROOT"] = repo_path
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUTF8"] = "1"

            sync_msg: str | None = None

            async def handle_stderr_line(line: str) -> None:
                nonlocal sync_msg
                if "AUTO-SYNC" in line:
                    prog, msg, is_final = parse_auto_sync_progress(line)
                    if is_final:
                        sync_msg = f"Auto-Sync: {msg}"
                    await ctx.report_progress(prog, 100, message=f"Auto-Sync: {msg}")
                elif "Rastreando flujo de eventos" in line:
                    await ctx.report_progress(
                        75, 100, message="Rastreando flujo de eventos..."
                    )

            await ctx.report_progress(
                20, 100, message="Iniciando subproceso de trazabilidad..."
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
                return "Error: La trazabilidad superó el límite de tiempo de 1 minuto."
            except Exception as sub_err:
                return f"Error al ejecutar la trazabilidad: {sub_err!s}"

            if res.returncode == 0:
                await ctx.report_progress(
                    100, 100, message="Trazabilidad de eventos lista."
                )
                stdout_str = res.stdout.decode("utf-8", errors="replace")
                sync_prefix = f"[{sync_msg}]\n\n" if sync_msg else ""
                return sync_prefix + stdout_str
            else:
                err_msg = res.stderr.decode("utf-8", errors="replace")
                return f"Error en la trazabilidad: {err_msg}"
        except Exception as e:
            return f"Error inesperado al rastrear eventos: {e!s}"
