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
async def audit_layout_risks(
    ctx: Context,
    project_path: str,
    severity: str = "ALL",
    file_filter: str | None = None,
) -> str:
    """Performs a static CSS and layout risk audit on the project.

    Detects common responsive layout anti-patterns:
    - Flexbox/Grid children missing min-width: 0 or overflow: hidden (CRITICAL).
    - Long text containers and links missing word breaking (WARNING).
    - Fixed pixel width overflows in responsive viewports (WARNING).
    - High z-index values without isolated stacking context (INFO).

    RECOMMENDED: Use 'file_filter' (e.g. 'chat.css') or 'severity' ('CRITICAL').

    Args:
        project_path: Absolute path to the project repository.
        severity: Filter by risk level ('CRITICAL', 'WARNING', 'INFO', or 'ALL').
        file_filter: Optional CSS file name or path to filter (e.g. 'chat.css').
    """
    async with get_lock():
        try:
            await ctx.report_progress(10, 100, message="Cargando configuración...")
            setup_project_context(project_path)
        except Exception as e:
            return f"Error de configuración: {e!s}"

        try:
            repo_path = str(core_config.REPO_ROOT.resolve())
            cmd = [
                sys.executable,
                "-m",
                "rag_local.cli.style_audit",
                "--project-path",
                repo_path,
                "--severity",
                severity,
            ]
            if file_filter:
                cmd.extend(["--file-filter", file_filter])

            env = os.environ.copy()
            env["RAG_REPO_ROOT"] = repo_path
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUTF8"] = "1"

            await ctx.report_progress(
                30, 100, message="Ejecutando auditoría de layout..."
            )

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
            await ctx.report_progress(100, 100, message="Auditoría de layout lista.")

            stdout = res.stdout.decode("utf-8", errors="replace")
            if res.returncode != 0:
                stderr = res.stderr.decode("utf-8", errors="replace")
                err_msg = stderr.strip() or stdout.strip()
                return f"ERROR ({res.returncode}): rag-style-audit falló.\n{err_msg}"
            sync_prefix = f"[{sync_msg}]\n\n" if sync_msg else ""
            return sync_prefix + stdout
        except Exception as e:
            return f"Error al ejecutar rag-style-audit: {e!s}"
