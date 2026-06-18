import shutil
import subprocess

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

    Args:
        query: La consulta o término de búsqueda.
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

    try:
        results = process_query(
            query_text=query, scope=scope, respond_in_english=False
        )
        return results.get("response", "No se generó ninguna respuesta.")
    except Exception as e:
        return f"Error al procesar la consulta en el RAG local: {e!s}"


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

    try:
        # Buscar la ruta de uv para evitar advertencias de seguridad
        uv_path = shutil.which("uv") or "uv"

        # Ejecutar la ingesta en un subproceso para evitar colisiones en
        # stdout/stdin (comunicación stdio de MCP) y aislar los sys.exit().
        result = subprocess.run(  # noqa: S603
            [uv_path, "run", "rag-ingest"],
            capture_output=True,
            text=True,
            cwd=str(config.RAG_ROOT),
            check=False,
        )

        output = result.stderr if result.stderr else result.stdout
        if result.returncode == 0:
            return (
                "Ingesta completada de forma exitosa.\n\n"
                f"Detalles del proceso:\n{output}"
            )
        else:
            return (
                f"Error durante la ingesta (código {result.returncode}):\n"
                f"{output}"
            )
    except Exception as e:
        return f"Excepción al iniciar la ingesta: {e!s}"


def main() -> None:
    """Punto de entrada principal para el servidor MCP."""
    mcp.run()


if __name__ == "__main__":
    main()
