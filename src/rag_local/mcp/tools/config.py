import contextlib
import os

from rag_local.core import config as core_config
from rag_local.mcp.server import get_lock, mcp
from rag_local.services.project import setup_project_context


@mcp.tool()
async def get_config(project_path: str | None = None) -> str:
    """Retorna la configuración actual de rutas y variables de entorno.

    Args:
        project_path: Ruta absoluta opcional al repositorio del proyecto.
    """
    async with get_lock():
        with contextlib.suppress(Exception):
            setup_project_context(project_path)

        gemini_key = core_config.GEMINI_API_KEY
        key_status = "Configurada" if gemini_key else "No configurada"
        lancedb_exists = core_config.LANCEDB_PATH.exists() and any(
            core_config.LANCEDB_PATH.iterdir()
        )

        return (
            f"RAG_ROOT: {core_config.RAG_ROOT.resolve()}\n"
            f"REPO_ROOT: {core_config.REPO_ROOT.resolve()}\n"
            f"LANCEDB_PATH: {core_config.LANCEDB_PATH.resolve()}\n"
            f"LANCEDB_INDEXADA: "
            f"{'Sí' if lancedb_exists else 'No (ejecuta ingest_codebase)'}\n"
            f"GEMINI_API_KEY: {key_status}\n"
            f"CWD: {os.getcwd()}\n"
            f"ENV RAG_ROOT: {os.getenv('RAG_ROOT')}\n"
            f"ENV RAG_REPO_ROOT: {os.getenv('RAG_REPO_ROOT')}"
        )
