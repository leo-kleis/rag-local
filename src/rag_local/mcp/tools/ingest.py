import os
import sys

from fastmcp import Context

from rag_local.core import config as core_config
from rag_local.mcp.server import lock_manager, mcp
from rag_local.services.fast_sync import fast_check_and_refresh
from rag_local.services.project import setup_project_context
from rag_local.services.subprocess import run_cli_subprocess


@mcp.tool()
async def ingest_codebase(
    ctx: Context,
    project_path: str,
    force: bool = False,
) -> str:
    """Indexa e ingesta incrementalmente los archivos del codebase actual.

    Calcula hashes de archivos para actualizar o agregar solo los
    modificados/nuevos y purga los eliminados en LanceDB.

    Args:
        project_path: Ruta absoluta al directorio raíz del proyecto.
        force: Si es True, fuerza la reindexación completa ignorando la caché.
    """
    from rag_local.services.scanner import detect_project_roots

    try:
        setup_project_context(project_path)
    except Exception as e:
        return f"Error de configuración: {e!s}"

    target_repo = core_config.REPO_ROOT.resolve()

    # Validar estructura antes de proceder a la ingesta
    angular_root, nest_root, python_root, nextjs_root = detect_project_roots(
        target_repo
    )
    if not angular_root and not nest_root and not python_root and not nextjs_root:
        return (
            "Error de Ingesta: No se detectó un proyecto de Angular, "
            "NestJS, Python o Next.js válido en la raíz del repositorio "
            f"({target_repo}). Ingesta cancelada."
        )

    async def report_wait(msg: str) -> None:
        await ctx.report_progress(5, 100, message=msg)

    async with lock_manager.acquire_global_ingest(target_repo, on_waiting=report_wait):
        # De-duplicación: si el índice ya está fresco tras la espera, no re-indexar
        if not force:
            refresh_status = fast_check_and_refresh(target_repo)
            if refresh_status.get("reason") == "clean":
                return (
                    "El índice de LanceDB ya se encuentra 100% actualizado "
                    "(sin archivos modificados ni pendientes de sincronización)."
                )

        try:
            repo_path = str(target_repo)
            cmd = [
                sys.executable,
                "-m",
                "rag_local.cli.ingest",
                "--project-path",
                repo_path,
            ]
            if force:
                cmd.append("--force")

            # Propagar el repo objetivo al subproceso via env var
            env = os.environ.copy()
            env["RAG_REPO_ROOT"] = repo_path
            env["RAG_LOCK_HELD"] = "1"
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUTF8"] = "1"

            await ctx.report_progress(
                5, 100, message="Iniciando ingesta del repositorio..."
            )

            async def handle_stderr_line(line: str) -> None:
                from rag_local.core.events import parse_sync_event

                event = parse_sync_event(line)
                if event is not None and event.message:
                    await ctx.report_progress(
                        event.progress, 100, message=event.message
                    )

            try:
                res = await run_cli_subprocess(
                    cmd=cmd,
                    cwd=repo_path,
                    env=env,
                    is_ingestion=True,
                    timeout=core_config.DEFAULT_CLI_TIMEOUT,
                    on_stderr_line=handle_stderr_line,
                )
            except TimeoutError:
                return (
                    "Error de Ingesta: El proceso superó el tiempo límite de 5 minutos."
                )
            except Exception as sub_err:
                return f"Error al iniciar el subproceso de ingesta: {sub_err!s}"

            if res.returncode == 0:
                output_str = res.stdout.decode("utf-8", errors="replace")
                err_str = res.stderr.decode("utf-8", errors="replace")
                combined_output = f"{output_str}\n{err_str}"
                summary_lines = [
                    line.strip()
                    for line in combined_output.splitlines()
                    if line.strip() and not line.strip().startswith("@@RAG_EVENT:")
                ]
                # Tomar últimas 15 líneas combinadas para estadísticas
                summary = (
                    "\n".join(summary_lines[-15:])
                    if summary_lines
                    else "Ingesta finalizada."
                )
                return f"Ingesta completada de forma exitosa.\nResumen:\n{summary}"
            else:
                err_msg = res.stderr.decode("utf-8", errors="replace")
                if not err_msg:
                    err_msg = res.stdout.decode("utf-8", errors="replace")
                return f"Error en la ingesta (código {res.returncode}): {err_msg}"
        except Exception as e:
            return f"Error al procesar la ingesta en el RAG local: {e!s}"
