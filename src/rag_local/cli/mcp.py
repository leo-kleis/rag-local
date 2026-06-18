import os
import subprocess
import sys
from typing import Optional
from fastmcp import FastMCP

from rag_local.core import config
from rag_local.services.rag import process_query
from rag_local.services.scanner import detect_project_roots

# Inicializar FastMCP
mcp = FastMCP("rag-local")

@mcp.tool()
def query_codebase(query: str, scope: Optional[str] = None) -> str:
    """Consulta la base de datos vectorial local del RAG para obtener información contextual, clases,
    esquemas de Prisma o lógica de flujo de datos en el monorepo.

    Args:
        query: La consulta o término de búsqueda (ej. '¿Cómo está definido el modelo User?').
        scope: Filtro opcional de scope: 'frontend' (Angular) o 'backend' (NestJS/Prisma).
    """
    # Validar que el proyecto actual tenga la estructura esperada de monorepo
    angular_root, nest_root = detect_project_roots(config.REPO_ROOT)
    if not angular_root and not nest_root:
        return (
            "Error: El proyecto activo en el workspace no parece ser un proyecto estructurado "
            "en Angular o NestJS compatible con este RAG local. "
            f"La ruta escaneada fue: {config.REPO_ROOT.resolve()}"
        )

    try:
        results = process_query(query_text=query, scope=scope, respond_in_english=False)
        return results.get("response", "No se generó ninguna respuesta.")
    except Exception as e:
        return f"Error al procesar la consulta en el RAG local: {str(e)}"


@mcp.tool()
def ingest_codebase() -> str:
    """Indexa e ingesta incrementalmente los archivos del codebase actual en la base de datos vectorial LanceDB.

    Calcula hashes de archivos para actualizar o agregar solo los modificados/nuevos y purga
    los eliminados.
    """
    # Validar estructura antes de proceder a la ingesta
    angular_root, nest_root = detect_project_roots(config.REPO_ROOT)
    if not angular_root and not nest_root:
        return (
            "Error de Ingesta: No se detectó un proyecto de Angular o NestJS válido en la raíz "
            f"del repositorio ({config.REPO_ROOT.resolve()}). Ingesta cancelada."
        )

    try:
        # Ejecutar la ingesta en un subproceso para evitar colisiones en stdout/stdin (comunicación stdio de MCP)
        # y aislar los sys.exit() del comando CLI principal.
        result = subprocess.run(
            ["uv", "run", "rag-ingest"],
            capture_output=True,
            text=True,
            cwd=str(config.RAG_ROOT),
            check=False
        )
        
        output = result.stderr if result.stderr else result.stdout
        if result.returncode == 0:
            return f"Ingesta completada de forma exitosa.\n\nDetalles del proceso:\n{output}"
        else:
            return f"Error durante el proceso de ingesta (código {result.returncode}):\n{output}"
    except Exception as e:
        return f"Excepción al iniciar la ingesta: {str(e)}"


def main() -> None:
    """Punto de entrada principal para el servidor MCP."""
    mcp.run()

if __name__ == "__main__":
    main()
