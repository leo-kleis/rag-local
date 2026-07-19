import os

from fastmcp import Context

from rag_local.core import config as core_config
from rag_local.mcp.server import get_lock
from rag_local.services.project import setup_project_context
from rag_local.services.subprocess import run_cli_subprocess


# @mcp.tool()
async def export_project_graph(
    ctx: Context,
    project_path: str | None = None,
) -> str:
    """Generates and updates the interactive 2D/3D and Mermaid project graph.

    Saves the result to <project_path>/.lancedb/project_graph.html.
    Returns the file URL to open directly in the browser.

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
                "rag-graph",
                "--project-path",
                repo_path,
            ]

            env = os.environ.copy()
            env["RAG_REPO_ROOT"] = repo_path
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUTF8"] = "1"

            async def handle_stderr_line(line: str) -> None:
                if "Generando visualizaciones" in line:
                    await ctx.report_progress(
                        50,
                        100,
                        message="Construyendo nodos y estructura de datos...",
                    )

            await ctx.report_progress(
                20, 100, message="Iniciando subproceso de exportación..."
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
                return "Error: La exportación superó el límite de tiempo de 1 minuto."
            except Exception as sub_err:
                return f"Error al ejecutar el subproceso de graficado: {sub_err!s}"

            if res.returncode == 0:
                await ctx.report_progress(100, 100, message="Exportación exitosa.")
                dir_path = core_config.LANCEDB_PATH
                file_3d = dir_path / "project_graph_3d.html"
                file_2d = dir_path / "project_graph_2d.html"
                file_mermaid = dir_path / "project_graph_mermaid.html"

                link_3d = f"file:///{file_3d.resolve().as_posix()}"
                link_2d = f"file:///{file_2d.resolve().as_posix()}"
                link_mermaid = f"file:///{file_mermaid.resolve().as_posix()}"

                return (
                    "¡Grafo exportado con éxito en 3 archivos independientes!\n\n"
                    "Abre los enlaces directamente en tu navegador:\n"
                    f"- **3D Graph (WebGL)**: {link_3d}\n"
                    f"- **2D Graph (Vis.js)**: {link_2d}\n"
                    f"- **Mermaid View**: {link_mermaid}"
                )
            else:
                err_msg = res.stderr.decode("utf-8", errors="replace")
                if not err_msg:
                    err_msg = res.stdout.decode("utf-8", errors="replace")
                return f"Error en graficado (código {res.returncode}): {err_msg}"
        except Exception as e:
            return f"Error al procesar el grafo en el RAG local: {e!s}"
