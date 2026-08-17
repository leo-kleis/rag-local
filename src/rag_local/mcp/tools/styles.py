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
async def get_styles_map(
    ctx: Context,
    project_path: str,
    component_filter: str | None = None,
    class_filter: str | None = None,
    property_filter: str | None = None,
) -> str:
    """Returns a structural overview of the project's CSS styles and rules.

    Includes component map, CSS rules by line numbers, property inspection,
    and dead CSS. Call this tool when working on UI design, CSS styling,
    locating which CSS file defines classes for a component, or querying
    CSS rules by property.

    Args:
        project_path: Absolute path to the project repository.
        component_filter: Optional filter by component file name (e.g. 'ChatMessage').
        class_filter: Optional filter by CSS class name (e.g. 'sys-text').
        property_filter: Filter by CSS property or value (e.g. 'display').
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
                "rag_local.cli.styles",
                "--project-path",
                repo_path,
            ]
            if component_filter:
                cmd.extend(["--component", component_filter])
            if class_filter:
                cmd.extend(["--class-name", class_filter])
            if property_filter:
                cmd.extend(["--property", property_filter])

            env = os.environ.copy()
            env["RAG_REPO_ROOT"] = repo_path
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUTF8"] = "1"

            await ctx.report_progress(30, 100, message="Obteniendo mapa de estilos...")

            sync_msg: str | None = None

            async def handle_stderr_line(line: str) -> None:
                nonlocal sync_msg
                if "AUTO-SYNC" in line:
                    prog, msg, is_final = parse_auto_sync_progress(line)
                    if is_final:
                        sync_msg = f"Auto-Sync: {msg}"
                    await ctx.report_progress(prog, 100, message=f"Auto-Sync: {msg}")

            res = await run_cli_subprocess(
                cmd, cwd=repo_path, env=env, on_stderr_line=handle_stderr_line
            )
            await ctx.report_progress(100, 100, message="Mapa de estilos listo.")

            stdout = res.stdout.decode("utf-8", errors="replace")
            if res.returncode != 0:
                stderr = res.stderr.decode("utf-8", errors="replace")
                err_msg = stderr.strip() or stdout.strip()
                return f"ERROR ({res.returncode}): rag-styles fallo.\n{err_msg}"
            sync_prefix = f"[{sync_msg}]\n\n" if sync_msg else ""
            return sync_prefix + stdout
        except Exception as e:
            return f"Error al ejecutar rag-styles: {e!s}"
