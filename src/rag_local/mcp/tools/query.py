import json
import os
import re

from fastmcp import Context

from rag_local.core import config as core_config
from rag_local.mcp.server import get_lock, mcp
from rag_local.services.project import setup_project_context
from rag_local.services.subprocess import run_cli_subprocess


@mcp.tool()
async def query_codebase(
    ctx: Context,
    query: str,
    scope: str | None = None,
    project_path: str | None = None,
) -> str:
    """Consulta la base de datos vectorial local del RAG para obtener contexto.

    Busca clases, métodos, esquemas de Prisma o lógica de flujo de datos.
    Para mejores resultados y menor consumo de tokens, realiza la consulta
    (parámetro 'query') en inglés.

    Args:
        query: La consulta o término de búsqueda (ej. 'find User model fields').
        scope: Filtro opcional de scope: 'angular' (Angular),
            'nestjs' (NestJS), 'nextjs-app' (Next.js) o 'python' (Python).
        project_path: Ruta absoluta opcional al repositorio del proyecto.
    """
    from rag_local.services.scanner import detect_project_roots

    async with get_lock():
        try:
            await ctx.report_progress(10, 100, message="Cargando configuración...")
            setup_project_context(project_path)
        except Exception as e:
            return f"Error de configuración: {e!s}"

        # Validar que exista la base de datos indexada antes de proceder
        if not core_config.LANCEDB_PATH.exists() or not any(
            core_config.LANCEDB_PATH.iterdir()
        ):
            return (
                "Error: No existe una base de datos indexada en "
                f"{core_config.LANCEDB_PATH.resolve()}. "
                "Ejecuta ingest_codebase primero."
            )

        # Validar que el proyecto actual tenga la estructura esperada
        angular_root, nest_root, python_root, nextjs_root = detect_project_roots(
            core_config.REPO_ROOT
        )
        if not angular_root and not nest_root and not python_root and not nextjs_root:
            return (
                "Error: El proyecto activo en el workspace no parece ser "
                "un proyecto compatible con este RAG local (no se detectó "
                "Angular, NestJS, Python ni Next.js). "
                f"Ruta: {core_config.REPO_ROOT.resolve()}"
            )

        try:
            repo_path = str(core_config.REPO_ROOT.resolve())
            cmd = [
                "uv",
                "run",
                "--project",
                str(core_config.RAG_ROOT),
                "rag-query",
                "--project-path",
                repo_path,
                "--query",
                query,
                "--json",
                "--no-llm",
            ]
            if scope:
                cmd.extend(["--scope", scope])

            env = os.environ.copy()
            env["RAG_REPO_ROOT"] = repo_path
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUTF8"] = "1"

            async def handle_stderr_line(line: str) -> None:
                if "Analizando consulta" in line:
                    await ctx.report_progress(15, 100, message="Analizando consulta...")
                elif "generando embeddings" in line:
                    await ctx.report_progress(
                        30, 100, message="Generando embeddings..."
                    )
                elif (
                    "Loading SentenceTransformer" in line
                    or "Loading TransformerRanker" in line
                ):
                    await ctx.report_progress(
                        60, 100, message="Cargando modelos locales..."
                    )
                elif "Loading weights" in line:
                    await ctx.report_progress(
                        75, 100, message="Cargando pesos en GPU/CPU..."
                    )
                elif "CONTEXTO RECUPERADO" in line:
                    await ctx.report_progress(
                        90, 100, message="Re-rankeando resultados..."
                    )

            try:
                res = await run_cli_subprocess(
                    cmd=cmd,
                    cwd=repo_path,
                    env=env,
                    timeout=300.0,
                    on_stderr_line=handle_stderr_line,
                )
            except TimeoutError:
                return "Error de Consulta: La búsqueda superó el límite de 5 minutos."
            except Exception as sub_err:
                return f"Error al ejecutar la consulta: {sub_err!s}"

            if res.returncode == 0:
                try:
                    output_str = res.stdout.decode("utf-8", errors="replace")
                    # Limpiar secuencias ANSI y banners extra de uv
                    ansi_escape = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
                    output_clean = ansi_escape.sub("", output_str)

                    start_idx = output_clean.find("{")
                    end_idx = output_clean.rfind("}")
                    if start_idx != -1 and end_idx != -1:
                        json_str = output_clean[start_idx : end_idx + 1]
                    else:
                        json_str = output_clean

                    results = json.loads(json_str)
                    await ctx.report_progress(
                        100, 100, message="Búsqueda completada exitosamente."
                    )
                    context = results.get("context", "")
                    chunks = results.get("retrieved_chunks", [])

                    if not chunks:
                        return (
                            "NO_CONTEXT: No relevant information was found in the "
                            "local corpus for this query. Do not guess or fabricate "
                            "an answer — inform the user that the RAG has no indexed "
                            "data about this topic."
                        )

                    lines_info = "\n".join(
                        f"  - {c.get('source', '?')} "
                        f"(L{c.get('start_line', '?')}-{c.get('end_line', '?')})"
                        for c in chunks
                    )
                    unique_files = len({c.get("source", "") for c in chunks})
                    header = f"[Archivos relevantes: {unique_files}]\n{lines_info}\n\n"

                    return header + context
                except Exception as parse_err:
                    output_dbg = res.stdout.decode("utf-8", errors="replace")
                    err_dbg = res.stderr.decode("utf-8", errors="replace")
                    return (
                        f"Error al parsear resultados JSON: {parse_err}\n"
                        f"STDOUT:\n{output_dbg}\n"
                        f"STDERR:\n{err_dbg}"
                    )
            else:
                err_msg = res.stderr.decode("utf-8", errors="replace")
                if not err_msg:
                    err_msg = res.stdout.decode("utf-8", errors="replace")
                return f"Error en consulta (código {res.returncode}): {err_msg}"
        except Exception as e:
            return f"Error al procesar la consulta en el RAG local: {e!s}"
