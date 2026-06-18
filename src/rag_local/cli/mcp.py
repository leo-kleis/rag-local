import sys

from fastmcp import FastMCP

from rag_local.core import config
from rag_local.services.rag import process_query
from rag_local.services.scanner import detect_project_roots

# Inicializar FastMCP
mcp = FastMCP("rag-local")


@mcp.tool()
def query_codebase(query: str, scope: str | None = None) -> str:
    """Consulta la base de datos vectorial local del RAG para obtener contexto.

    Busca clases, métodos, esquemas de Prisma o lógica de flujo de datos.
    Para mejores resultados y menor consumo de tokens, realiza la consulta
    (parámetro 'query') en inglés.

    Args:
        query: La consulta o término de búsqueda (ej. 'find User model fields').
        scope: Filtro opcional de scope: 'frontend' (Angular) o 'backend'.
    """
    # Validar que el proyecto actual tenga la estructura esperada de monorepo
    angular_root, nest_root = detect_project_roots(config.REPO_ROOT)
    if not angular_root and not nest_root:
        return (
            "Error: El proyecto activo en el workspace no parece ser "
            "un proyecto estructurado en Angular o NestJS compatible "
            f"con este RAG local. Ruta: {config.REPO_ROOT.resolve()}"
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
def get_config() -> str:
    """Retorna la configuración actual de rutas y variables de entorno."""
    import os

    gemini_key = os.getenv("GEMINI_API_KEY")
    key_len = len(gemini_key) if gemini_key else 0
    key_status = (
        f"Configurada (largo: {key_len})" if gemini_key else "No configurada"
    )

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
def ingest_codebase() -> str:
    """Indexa e ingesta incrementalmente los archivos del codebase actual.

    Calcula hashes de archivos para actualizar o agregar solo los
    modificados/nuevos y purga los eliminados en LanceDB.
    """
    # Validar estructura antes de proceder a la ingesta
    angular_root, nest_root = detect_project_roots(config.REPO_ROOT)
    if not angular_root and not nest_root:
        return (
            "Error de Ingesta: No se detectó un proyecto de Angular o "
            "NestJS válido en la raíz del repositorio "
            f"({config.REPO_ROOT.resolve()}). Ingesta cancelada."
        )

    # Redirigir stdout a stderr temporalmente para evitar la corrupción
    # del canal de comunicación stdio de MCP.
    original_stdout = sys.stdout
    sys.stdout = sys.stderr

    try:
        from rag_local.cli.ingest import run_ingestion

        run_ingestion()
        return "Ingesta completada de forma exitosa."
    except SystemExit as e:
        if e.code == 0:
            return "Ingesta completada de forma exitosa."
        else:
            return f"La ingesta finalizó con código de salida: {e.code}"
    except Exception as e:
        return f"Error durante la ingesta: {e!s}"
    finally:
        sys.stdout = original_stdout


def main() -> None:
    """Punto de entrada principal para el servidor MCP."""
    mcp.run(show_banner=False)


if __name__ == "__main__":
    main()
