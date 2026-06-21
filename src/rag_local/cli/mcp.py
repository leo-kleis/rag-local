import asyncio
import contextlib
import os
import sys
from pathlib import Path

from fastmcp import Context, FastMCP

from rag_local.core import config

# Inicializar FastMCP
mcp = FastMCP("rag-local")

_lock: asyncio.Lock | None = None


def get_lock() -> asyncio.Lock:
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock



def setup_project_context(project_path: str | None = None) -> None:
    """Configura dinámicamente el proyecto activo.

    Mutaciones en config.REPO_ROOT y config.LANCEDB_PATH.
    """
    from rag_local.services.scanner import detect_project_roots

    # Resolver CWD si no se especifica project_path
    repo_path = (
        Path(project_path).resolve()
        if project_path
        else Path(os.getcwd()).resolve()
    )

    # Sanitizar y prevenir Path Traversal o accesos a directorios del sistema/raíz
    repo_path_str = str(repo_path)
    is_system_path = (
        ".gemini" in repo_path_str
        or "AppData" in repo_path_str
        or "Windows" in repo_path_str
        or "Program Files" in repo_path_str
        or "System32" in repo_path_str
        or "Temp" in repo_path_str
        or repo_path_str == "/"
        or repo_path_str.endswith(":\\")
    )
    if is_system_path:
        raise ValueError(
            "Acceso denegado: La ruta especificada es un directorio "
            "del sistema o raíz de disco."
        )

    # Fallback inteligente si estamos en la carpeta de la herramienta RAG
    if not project_path:
        angular_root, nest_root, python_root = detect_project_roots(repo_path)
        if (
            not angular_root
            and not nest_root
            and not python_root
            and (repo_path == config.RAG_ROOT or config.RAG_ROOT in repo_path.parents)
        ):
            repo_path = config.RAG_ROOT

    # Redireccionar repo_path al root real del monorepo si se detectan en subdirectorios
    angular_root, nest_root, python_root = detect_project_roots(repo_path)
    if angular_root and angular_root != repo_path:
        repo_path = angular_root.parent
    elif nest_root and nest_root != repo_path:
        repo_path = nest_root.parent
    elif python_root and python_root != repo_path:
        repo_path = python_root

    # Validar que exista la ruta
    if not repo_path.exists():
        raise FileNotFoundError(f"La ruta especificada no existe: {repo_path}")
    if not repo_path.is_dir():
        raise NotADirectoryError(
            f"La ruta especificada no es un directorio: {repo_path}"
        )

    config.REPO_ROOT = repo_path
    config.LANCEDB_PATH = repo_path / ".lancedb"


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
        scope: Filtro opcional de scope: 'frontend' (Angular),
            'backend' (NestJS) o 'python' (Python).
        project_path: Ruta absoluta opcional al repositorio del proyecto.
    """
    from rag_local.services.rag import process_query
    from rag_local.services.scanner import detect_project_roots

    async with get_lock():
        try:
            await ctx.report_progress(
                10, 100, message="Cargando configuración..."
            )
            setup_project_context(project_path)
        except Exception as e:
            return f"Error de configuración: {e!s}"

        # Validar que exista la base de datos indexada antes de proceder
        if not config.LANCEDB_PATH.exists() or not any(config.LANCEDB_PATH.iterdir()):
            return (
                "Error: No existe una base de datos indexada en "
                f"{config.LANCEDB_PATH.resolve()}. "
                "Ejecuta ingest_codebase primero."
            )

        # Validar que el proyecto actual tenga la estructura esperada
        angular_root, nest_root, python_root = detect_project_roots(config.REPO_ROOT)
        if not angular_root and not nest_root and not python_root:
            return (
                "Error: El proyecto activo en el workspace no parece ser "
                "un proyecto compatible con este RAG local (no se detectó "
                f"Angular, NestJS ni Python). Ruta: {config.REPO_ROOT.resolve()}"
            )

        # Redirigir stdout a stderr antes del hilo para que torch/transformers
        # no corrompan el canal JSON-RPC de stdio durante la carga del modelo.
        original_stdout = sys.stdout
        sys.stdout = sys.stderr
        try:
            await ctx.report_progress(
                40, 100, message="Buscando en base de datos..."
            )
            # Envolver en to_thread para no bloquear el event loop de asyncio
            # mientras carga el modelo de embeddings o hace inferencia en GPU.
            results = await asyncio.to_thread(
                process_query,
                query_text=query,
                scope=scope,
                respond_in_english=False,
                generate_response=False,
            )
            await ctx.report_progress(
                100, 100, message="Búsqueda completada exitosamente."
            )
            return results.get("context", "No se encontró contexto relevante.")
        except Exception as e:
            return f"Error al procesar la consulta en el RAG local: {e!s}"
        finally:
            sys.stdout = original_stdout


@mcp.tool()
async def get_config(project_path: str | None = None) -> str:
    """Retorna la configuración actual de rutas y variables de entorno.

    Args:
        project_path: Ruta absoluta opcional al repositorio del proyecto.
    """
    async with get_lock():
        with contextlib.suppress(Exception):
            setup_project_context(project_path)

        gemini_key = config.GEMINI_API_KEY
        key_status = "Configurada" if gemini_key else "No configurada"
        lancedb_exists = (
            config.LANCEDB_PATH.exists() and any(config.LANCEDB_PATH.iterdir())
        )

        return (
            f"RAG_ROOT: {config.RAG_ROOT.resolve()}\n"
            f"REPO_ROOT: {config.REPO_ROOT.resolve()}\n"
            f"LANCEDB_PATH: {config.LANCEDB_PATH.resolve()}\n"
            f"LANCEDB_INDEXADA: "
            f"{'Sí' if lancedb_exists else 'No (ejecuta ingest_codebase)'}\n"
            f"GEMINI_API_KEY: {key_status}\n"
            f"CWD: {os.getcwd()}\n"
            f"ENV RAG_ROOT: {os.getenv('RAG_ROOT')}\n"
            f"ENV RAG_REPO_ROOT: {os.getenv('RAG_REPO_ROOT')}"
        )


@mcp.tool()
async def ingest_codebase(ctx: Context, project_path: str | None = None) -> str:
    """Indexa e ingesta incrementalmente los archivos del codebase actual.

    Calcula hashes de archivos para actualizar o agregar solo los
    modificados/nuevos y purga los eliminados en LanceDB.

    Args:
        project_path: Ruta absoluta opcional al repositorio del proyecto.
    """
    from rag_local.services.scanner import detect_project_roots

    def progress_callback(progress: int, total: int, message: str) -> None:
        sys.stderr.write(f"PROGRESO: {progress}% - {message}\n")
        sys.stderr.flush()

    async with get_lock():
        try:
            setup_project_context(project_path)
        except Exception as e:
            return f"Error de configuración: {e!s}"

        # Validar estructura antes de proceder a la ingesta
        angular_root, nest_root, python_root = detect_project_roots(config.REPO_ROOT)
        if not angular_root and not nest_root and not python_root:
            return (
                "Error de Ingesta: No se detectó un proyecto de Angular, "
                "NestJS o Python válido en la raíz del repositorio "
                f"({config.REPO_ROOT.resolve()}). Ingesta cancelada."
            )

        try:
            cmd = ["uv", "run", "rag-ingest"]
            repo_path = str(config.REPO_ROOT.resolve())

            # Propagar el repo objetivo al subproceso via env var
            env = os.environ.copy()
            env["RAG_REPO_ROOT"] = repo_path

            # Ejecutar la ingesta en un subproceso independiente
            # usando el repo_path del proyecto como CWD para que uv
            # resuelva el pyproject.toml y el venv correcto.
            process = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=repo_path,
                env=env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            if process.stdout is None or process.stderr is None:
                return "Error: No se abrieron canales de comunicación en el subproceso."

            stdout_lines = []
            stderr_lines = []

            async def read_stdout():
                while True:
                    line_bytes = await process.stdout.readline()
                    if not line_bytes:
                        break
                    stdout_lines.append(line_bytes)

            async def read_stderr():
                while True:
                    line_bytes = await process.stderr.readline()
                    if not line_bytes:
                        break
                    stderr_lines.append(line_bytes)
                    line = line_bytes.decode("utf-8", errors="replace").strip()

                    try:
                        if "1. Escaneando" in line:
                            await ctx.report_progress(
                                10, 100, message="Escaneando archivos..."
                            )
                        elif "2. Procesando" in line:
                            await ctx.report_progress(
                                20, 100, message="Procesando archivos..."
                            )
                        elif "3. Indexando" in line:
                            await ctx.report_progress(
                                30, 100, message="Iniciando indexación..."
                            )
                        elif "Lote " in line:
                            import re

                            match = re.search(r"Lote (\d+)/(\d+)", line)
                            if match:
                                cur = int(match.group(1))
                                tot = int(match.group(2))
                                prog = 30 + int((cur / tot) * 65)
                                msg = f"Indexando lote {cur}/{tot}..."
                                await ctx.report_progress(
                                    prog, 100, message=msg
                                )
                        elif "¡Ingesta completada" in line:
                            await ctx.report_progress(
                                100, 100, message="¡Ingesta completada!"
                            )
                    except Exception as err:
                        from rag_local.core.logging import logger

                        logger.debug(f"Error al reportar progreso del RAG: {err}")

            try:
                await asyncio.wait_for(
                    asyncio.gather(read_stdout(), read_stderr(), process.wait()),
                    timeout=300.0,
                )
            except TimeoutError:
                try:
                    process.kill()
                    await process.wait()
                except Exception as kill_err:
                    from rag_local.core.logging import logger

                    logger.warning(
                        f"No se pudo forzar la finalización del subproceso: {kill_err}"
                    )
                return (
                    "Error de Ingesta: El proceso se congeló o superó "
                    "el tiempo límite de 5 minutos y fue finalizado."
                )

            stdout_data = b"".join(stdout_lines)
            stderr_data = b"".join(stderr_lines)

            if process.returncode == 0:
                output_str = stdout_data.decode("utf-8", errors="replace")
                err_str = stderr_data.decode("utf-8", errors="replace")
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
                err_msg = stderr_data.decode("utf-8", errors="replace")
                if not err_msg:
                    err_msg = stdout_data.decode("utf-8", errors="replace")
                return f"Error en la ingesta (código {process.returncode}): {err_msg}"
        except Exception as e:
            return f"Error al iniciar el subproceso de ingesta: {e!s}"


def main() -> None:
    """Punto de entrada principal para el servidor MCP."""
    mcp.run(show_banner=False)


if __name__ == "__main__":
    main()
