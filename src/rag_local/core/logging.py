import json
import logging
import os
import sys
from datetime import UTC, datetime
from typing import Any

from rich.logging import RichHandler


class JsonFormatter(logging.Formatter):
    """Formateador para serializar logs en formato JSON estructurado."""

    def format(self, record: logging.LogRecord) -> str:
        # Obtener el timestamp ISO en UTC
        dt = datetime.fromtimestamp(record.created, tz=UTC)

        log_data: dict[str, Any] = {
            "timestamp": dt.isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
        }

        # Incluir detalles de excepciones si existen
        if record.exc_info:
            log_data["exc_info"] = self.formatException(record.exc_info)

        return json.dumps(log_data)


def setup_logging(level: int = logging.INFO) -> None:
    """Configura el logging global con soporte para formato JSON o Rich."""
    # Evitar configuraciones duplicadas
    logger = logging.getLogger("rag_local")
    if logger.handlers:
        return

    log_format = os.environ.get("RAG_LOG_FORMAT", "rich").lower()

    if log_format == "json":
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(JsonFormatter())
        logging.basicConfig(
            level=level,
            handlers=[handler],
        )
    else:
        from rich.console import Console

        logging.basicConfig(
            level=level,
            format="%(message)s",
            datefmt="[%X]",
            handlers=[
                RichHandler(
                    rich_tracebacks=True,
                    console=Console(stderr=True),
                    show_time=True,
                    show_level=True,
                    show_path=False,
                )
            ],
        )

    # El logger heredará la configuración de basicConfig
    logger.setLevel(level)


# Inicializar el logger con la configuración por defecto
setup_logging()
logger: logging.Logger = logging.getLogger("rag_local")
