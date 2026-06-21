import asyncio

from fastmcp import FastMCP

# Inicializar FastMCP
mcp = FastMCP("rag-local")

_lock: asyncio.Lock | None = None


def get_lock() -> asyncio.Lock:
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock


# Importar y registrar herramientas del subpaquete
import rag_local.mcp.tools  # noqa: E402, F401


def main() -> None:
    """Punto de entrada principal para el servidor MCP."""
    mcp.run(show_banner=False)


if __name__ == "__main__":
    main()
