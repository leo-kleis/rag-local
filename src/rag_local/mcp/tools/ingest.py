import os
import re
import sys

from fastmcp import Context

from rag_local.core import config as core_config
from rag_local.mcp.server import get_lock, mcp
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
        project_path: Ruta absoluta al repositorio del proyecto.
        force: Si es True, fuerza la reindexación completa ignorando la caché.
    """
    from rag_local.services.scanner import detect_project_roots

    async with get_lock():
        try:
            setup_project_context(project_path)
        except Exception as e:
            return f"Error de configuración: {e!s}"

        # Validar estructura antes de proceder a la ingesta
        angular_root, nest_root, python_root, nextjs_root = detect_project_roots(
            core_config.REPO_ROOT
        )
        if not angular_root and not nest_root and not python_root and not nextjs_root:
            return (
                "Error de Ingesta: No se detectó un proyecto de Angular, "
                "NestJS, Python o Next.js válido en la raíz del repositorio "
                f"({core_config.REPO_ROOT.resolve()}). Ingesta cancelada."
            )

        try:
            repo_path = str(core_config.REPO_ROOT.resolve())
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

            async def handle_stderr_line(line: str) -> None:
                if "1. Escaneando" in line:
                    await ctx.report_progress(10, 100, message="Escaneando archivos...")
                elif "Procesando archivo" in line:
                    match = re.search(r"Procesando archivo (\d+)/(\d+)", line)
                    if match:
                        cur = int(match.group(1))
                        tot = int(match.group(2))
                        prog = 10 + int((cur / max(tot, 1)) * 20)
                        await ctx.report_progress(
                            prog, 100, message=f"Procesando archivo {cur}/{tot}..."
                        )
                elif "2. Procesando" in line:
                    await ctx.report_progress(10, 100, message="Procesando archivos...")
                elif "3. Indexando" in line:
                    await ctx.report_progress(
                        30, 100, message="Iniciando indexación..."
                    )
                elif "Lote " in line or "Indexando lote" in line:
                    match = re.search(r"(\d+)/(\d+)", line)
                    if match:
                        cur = int(match.group(1))
                        tot = int(match.group(2))
                        prog = 30 + int((cur / max(tot, 1)) * 65)
                        msg = f"Indexando lote {cur}/{tot}..."
                        await ctx.report_progress(prog, 100, message=msg)
                elif "¡Ingesta completada" in line:
                    await ctx.report_progress(100, 100, message="¡Ingesta completada!")

            try:
                res = await run_cli_subprocess(
                    cmd=cmd,
                    cwd=repo_path,
                    env=env,
                    timeout=300.0,
                    on_stderr_line=handle_stderr_line,
                )
            except TimeoutError:
                return (
                    "Error de Ingesta: El proceso se congeló o superó "
                    "el tiempo límite de 5 minutos y fue finalizado."
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
                    if line.strip()
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
