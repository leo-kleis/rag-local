from rag_local.core import config as core_config
from rag_local.mcp.tools import (
    config,
    dependencies,
    event_flow,
    ingest,
    metrics,
    project_map,
    query,
    style_audit,
    styles,
)

__all__ = [
    "config",
    "dependencies",
    "event_flow",
    "ingest",
    "metrics",
    "project_map",
    "query",
    "style_audit",
    "styles",
]

# Solo exponer la herramienta manage_daemon fuera de Docker (en modo nativo)
if not core_config.IS_DOCKER:
    from rag_local.mcp.tools import daemon  # noqa: F401

    __all__.append("daemon")
