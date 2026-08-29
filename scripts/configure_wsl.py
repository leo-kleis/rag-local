r"""Script de configuracion automatica de .wslconfig para rag-local en Windows.

Uso:
    uv run python scripts/configure_wsl.py
    uv run python scripts/configure_wsl.py --memory 6GB --swap 4GB
    uv run python scripts/configure_wsl.py --dry-run
"""

import argparse
import configparser
import os
import sys
from pathlib import Path

from rich.console import Console

console = Console()
error_console = Console(stderr=True)

WSL_CONFIG_PATH = Path.home() / ".wslconfig"


def parse_arguments() -> argparse.Namespace:
    """Parsea los argumentos de la línea de comandos."""
    parser = argparse.ArgumentParser(
        description="Configura .wslconfig con límites óptimos para Docker y GPU."
    )
    parser.add_argument(
        "--memory",
        type=str,
        default="6GB",
        help="Límite máximo de RAM asignada a WSL 2 (default: 6GB)",
    )
    parser.add_argument(
        "--swap",
        type=str,
        default="4GB",
        help="Tamaño del archivo de paginación Swap para WSL 2 (default: 4GB)",
    )
    parser.add_argument(
        "--processors",
        type=int,
        default=4,
        help="Número de núcleos lógicos asignados a WSL 2 (default: 4)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Muestra los cambios sin escribir en disco.",
    )
    return parser.parse_args()


def configure_wsl_file(
    memory: str,
    swap: str,
    processors: int,
    dry_run: bool = False,
) -> bool:
    """Genera o actualiza el archivo .wslconfig."""
    if os.name != "nt":
        error_console.print(
            "[yellow][AVISO][/yellow] Este script está diseñado para Windows (WSL 2)."
        )

    config = configparser.ConfigParser()

    if WSL_CONFIG_PATH.is_file():
        try:
            config.read(WSL_CONFIG_PATH, encoding="utf-8")
        except Exception as e:
            error_console.print(
                f"[red]Error al leer {WSL_CONFIG_PATH}: {e}. Nueva configuración.[/red]"
            )

    if not config.has_section("wsl2"):
        config.add_section("wsl2")

    config.set("wsl2", "memory", memory)
    config.set("wsl2", "swap", swap)
    config.set("wsl2", "processors", str(processors))

    if not config.has_section("experimental"):
        config.add_section("experimental")
        config.set("experimental", "sparseVhd", "true")
        config.set("experimental", "autoMemoryReclaim", "gradual")

    console.print(f"\n[bold]Configuración generada para {WSL_CONFIG_PATH}:[/bold]")
    for section in config.sections():
        console.print(f"  [cyan][{section}][/cyan]")
        for key, val in config.items(section):
            console.print(f"    {key} = {val}")

    if dry_run:
        console.print("\n[yellow][DRY-RUN][/yellow] No se guardaron cambios en disco.")
        return True

    try:
        with WSL_CONFIG_PATH.open("w", encoding="utf-8") as f:
            config.write(f)
        console.print(
            f"\n[bold green]Listo.[/bold green] Archivo {WSL_CONFIG_PATH} actualizado."
        )
        console.print(
            "[bold yellow]IMPORTANTE:[/bold yellow] "
            "Para aplicar los cambios, ejecuta en PowerShell:"
        )
        console.print("  [bold cyan]wsl --shutdown[/bold cyan]")
        return True
    except Exception as e:
        error_console.print(
            f"[bold red]Error al guardar {WSL_CONFIG_PATH}: {e}[/bold red]"
        )
        return False


def main() -> None:
    args = parse_arguments()
    success = configure_wsl_file(
        memory=args.memory,
        swap=args.swap,
        processors=args.processors,
        dry_run=args.dry_run,
    )
    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()
