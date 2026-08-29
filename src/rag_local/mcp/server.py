import os

from fastmcp import FastMCP

from rag_local.services.locks import lock_manager

# Inicializar FastMCP
mcp = FastMCP("rag-local")

# Importar y registrar herramientas del subpaquete
import rag_local.mcp.tools  # noqa: E402, F401

__all__ = ["lock_manager", "main", "mcp"]


def main() -> None:
    """Punto de entrada principal para el servidor MCP."""
    transport = os.getenv("MCP_TRANSPORT", "stdio").lower()
    if transport in ("http", "sse"):
        host = os.getenv("MCP_HOST", "0.0.0.0")  # noqa: S104
        port = int(os.getenv("MCP_PORT", "8000"))
        mcp.run(transport=transport, host=host, port=port, show_banner=False)
    else:
        mcp.run(transport="stdio", show_banner=False)


if __name__ == "__main__":
    main()
