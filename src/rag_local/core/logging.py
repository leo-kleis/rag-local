import logging

from rich.logging import RichHandler


def setup_logging(level: int = logging.INFO) -> None:
    """Configura el logging global usando RichHandler dirigido a stderr."""
    # Evitar configuraciones duplicadas
    logger = logging.getLogger("rag_local")
    if logger.handlers:
        return

    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[
            RichHandler(
                rich_tracebacks=True,
                console=None,  # Por defecto RichHandler usa stderr
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
