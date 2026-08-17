import os
import sys

from rag_local.core import config as core_config
from rag_local.mcp.server import get_lock, mcp
from rag_local.services.project import setup_project_context
from rag_local.services.subprocess import run_cli_subprocess


@mcp.tool()
async def get_config(project_path: str) -> str:
    """Retorna el estado del proyecto, índice de LanceDB y versión de esquema.

    Args:
        project_path: Ruta absoluta al repositorio del proyecto.
    """
    async with get_lock():
        try:
            setup_project_context(project_path)
        except Exception as e:
            return f"Error de configuración: {e!s}"

        try:
            repo_path = str(core_config.REPO_ROOT.resolve())
            cmd = [
                sys.executable,
                "-m",
                "rag_local.cli.config",
                "-p",
                repo_path,
            ]
            env = os.environ.copy()
            env["RAG_REPO_ROOT"] = repo_path

            res = await run_cli_subprocess(cmd, cwd=repo_path, env=env)
            stdout = res.stdout.decode("utf-8", errors="replace")
            if res.returncode != 0:
                stderr = res.stderr.decode("utf-8", errors="replace")
                err_msg = stderr.strip() or stdout.strip()
                return f"ERROR ({res.returncode}): rag-config fallo.\n{err_msg}"
            return stdout.strip()
        except Exception as e:
            return f"Error al ejecutar rag-config: {e!s}"
