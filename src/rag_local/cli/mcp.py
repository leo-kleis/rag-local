import asyncio
import contextlib
import os
import sys
import threading
from pathlib import Path

from fastmcp import Context, FastMCP

from rag_local.core import config
from rag_local.services.rag import process_query
from rag_local.services.scanner import detect_project_roots

# Inicializar FastMCP
mcp = FastMCP("rag-local")

# Lock para sincronizar modificaciones a la configuración global en tiempo de ejecución
_lock = threading.Lock()


def setup_project_context(project_path: str | None = None) -> None:
    """Configura dinámicamente el proyecto activo.

    Mutaciones en config.REPO_ROOT y config.LANCEDB_PATH.
    """
    if project_path:
        repo_path = Path(project_path).resolve()
    else:
        # Resolver automáticamente según el directorio de trabajo
        cwd = Path(os.getcwd()).resolve()
        repo_path = cwd

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
            repo_path = config.RAG_ROOT.parent

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
def query_codebase(
    query: str, scope: str | None = None, project_path: str | None = None
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
    with _lock:
        try:
            setup_project_context(project_path)
        except Exception as e:
            return f"Error de configuración: {e!s}"

        # Validar que el proyecto actual tenga la estructura esperada
        angular_root, nest_root, python_root = detect_project_roots(config.REPO_ROOT)
        if not angular_root and not nest_root and not python_root:
            return (
                "Error: El proyecto activo en el workspace no parece ser "
                "un proyecto compatible con este RAG local (no se detectó "
                f"Angular, NestJS ni Python). Ruta: {config.REPO_ROOT.resolve()}"
            )

        # Redirigir stdout a stderr temporalmente para evitar que librerías externas
        # (como rerankers/tqdm/transformers) escriban en stdout y corrompan MCP.
        original_stdout = sys.stdout
        sys.stdout = sys.stderr

        try:
            results = process_query(
                query_text=query,
                scope=scope,
                respond_in_english=False,
                generate_response=False,
            )
            return results.get("context", "No se encontró contexto relevante.")
        except Exception as e:
            return f"Error al procesar la consulta en el RAG local: {e!s}"
        finally:
            sys.stdout = original_stdout


@mcp.tool()
def get_config(project_path: str | None = None) -> str:
    """Retorna la configuración actual de rutas y variables de entorno.

    Args:
        project_path: Ruta absoluta opcional al repositorio del proyecto.
    """
    with _lock:
        with contextlib.suppress(Exception):
            setup_project_context(project_path)

        gemini_key = config.GEMINI_API_KEY
        key_status = "Configurada" if gemini_key else "No configurada"

        return (
            f"RAG_ROOT: {config.RAG_ROOT.resolve()}\n"
            f"REPO_ROOT: {config.REPO_ROOT.resolve()}\n"
            f"LANCEDB_PATH: {config.LANCEDB_PATH.resolve()}\n"
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
    loop = asyncio.get_running_loop()

    def progress_callback(progress: int, total: int, message: str) -> None:
        asyncio.run_coroutine_threadsafe(
            ctx.report_progress(progress=progress, total=total, message=message),
            loop,
        )

    with _lock:
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

        # Redirigir stdout a stderr temporalmente para evitar la corrupción
        # del canal de comunicación stdio de MCP.
        original_stdout = sys.stdout
        sys.stdout = sys.stderr

        try:
            from rag_local.cli.ingest import run_ingestion

            await asyncio.to_thread(run_ingestion, progress_callback, False)
            return "Ingesta completada de forma exitosa."
        except Exception as e:
            return f"Error durante la ingesta: {e!s}"
        finally:
            sys.stdout = original_stdout


def main() -> None:
    """Punto de entrada principal para el servidor MCP."""
    mcp.run(show_banner=False)


if __name__ == "__main__":
    main()
