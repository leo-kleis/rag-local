import os
import sys

from fastmcp import Context

from rag_local.core import config as core_config
from rag_local.mcp.server import get_lock, mcp
from rag_local.services.project import setup_project_context
from rag_local.services.subprocess import run_cli_subprocess


@mcp.tool()
async def manage_daemon(
    ctx: Context,
    action: str = "status",
    project_path: str | None = None,
) -> str:
    """Gestiona el Worker Daemon para precarga de modelos PyTorch en VRAM.

    Permite iniciar, detener o verificar el estado del servidor de modelos.
    Cuando el daemon está activo, la latencia de búsqueda de 'query_codebase' y la
    ingesta incremental se reducen a ~50ms.

    Args:
        action: La acción a ejecutar: 'status' (consultar estado), 'start'
            (iniciar daemon y precargar modelos en VRAM) o 'stop' (detener daemon).
        project_path: Ruta absoluta opcional al repositorio del proyecto.
    """
    valid_actions = {"status", "start", "stop"}
    act = action.strip().lower()
    if act not in valid_actions:
        actions_str = ", ".join(sorted(valid_actions))
        return f"Acción inválida '{action}'. Acciones permitidas: {actions_str}."

    async with get_lock():
        try:
            setup_project_context(project_path)
        except Exception as e:
            return f"Error de configuración: {e!s}"

        try:
            repo_path = str(core_config.REPO_ROOT.resolve())
            parent_pid = os.getpid()

            cmd = [
                sys.executable,
                "-m",
                "rag_local.cli.daemon",
                act,
            ]
            if act == "start":
                cmd.extend(["--parent-pid", str(parent_pid)])

            env = os.environ.copy()
            env["RAG_REPO_ROOT"] = repo_path
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUTF8"] = "1"

            await ctx.report_progress(
                20, 100, message=f"Ejecutando acción '{act}' en Worker Daemon..."
            )

            res = await run_cli_subprocess(
                cmd=cmd,
                cwd=repo_path,
                env=env,
                timeout=60.0,
            )

            await ctx.report_progress(100, 100, message="Acción completada.")

            stdout_str = res.stdout.decode("utf-8", errors="replace").strip()
            stderr_str = res.stderr.decode("utf-8", errors="replace").strip()

            if res.returncode == 0:
                return (
                    stdout_str
                    if stdout_str
                    else f"Acción '{act}' completada con éxito."
                )
            else:
                err_msg = stderr_str if stderr_str else stdout_str
                return (
                    f"Error al ejecutar '{act}' en el Worker Daemon "
                    f"(código {res.returncode}):\n{err_msg}"
                )

        except TimeoutError:
            return (
                f"Error: La operación '{act}' en el Worker Daemon superó el "
                "tiempo límite (60s)."
            )
        except Exception as e:
            return f"Error inesperado al gestionar el Worker Daemon: {e!s}"
