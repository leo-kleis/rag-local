r"""Script de instalacion automatica de la configuracion MCP para rag-local.

Uso:
    uv run python scripts/install_mcp.py
    uv run python scripts/install_mcp.py --mode local
    uv run python scripts/install_mcp.py --mode docker
    uv run python scripts/install_mcp.py --workspace D:\Projects --gemini-key AIza...
    uv run python scripts/install_mcp.py --target antigravity
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from rich.console import Console

console = Console()
error_console = Console(stderr=True)

MCP_CONFIG_PATHS: dict[str, Path] = {
    "antigravity": Path.home() / ".gemini" / "config" / "mcp_config.json",
    "claude": Path(os.getenv("APPDATA", "~")) / "Claude" / "claude_desktop_config.json",
}

RAG_LOCAL_DIR = Path(__file__).resolve().parents[1]
DEFAULT_WORKSPACE = str(RAG_LOCAL_DIR.parent)


def build_local_entry(workspace_dir: str, gemini_key: str) -> dict[str, Any]:
    return {
        "command": "uv",
        "args": ["run", "--project", str(RAG_LOCAL_DIR), "rag-mcp"],
        "env": {
            "GEMINI_API_KEY": gemini_key,
            "WORKSPACE_DIR": workspace_dir,
        },
    }


def build_docker_entry(workspace_dir: str, gemini_key: str) -> dict[str, Any]:
    return {
        "serverUrl": "http://127.0.0.1:8000/mcp",
    }


def load_or_create(config_path: Path) -> dict[str, Any]:
    if config_path.is_file():
        with config_path.open("r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save(config_path: Path, data: dict[str, Any]) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with config_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def install_to(
    config_path: Path,
    mode: str,
    workspace_dir: str,
    gemini_key: str,
) -> None:
    data = load_or_create(config_path)
    servers: dict[str, Any] = data.setdefault("mcpServers", {})

    if mode in ("local", "both"):
        servers["rag-local"] = build_local_entry(workspace_dir, gemini_key)
    elif "rag-local" in servers:
        del servers["rag-local"]

    if mode in ("docker", "both"):
        servers["rag-local-docker"] = build_docker_entry(workspace_dir, gemini_key)
    elif "rag-local-docker" in servers:
        del servers["rag-local-docker"]

    save(config_path, data)
    console.print(f"  [green]OK[/green]  {config_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Instala la configuracion MCP de rag-local."
    )
    parser.add_argument(
        "--mode",
        choices=["local", "docker", "both"],
        default="docker",
        help="local=uv run, docker=compose, both=ambos (default: docker)",
    )
    parser.add_argument(
        "--workspace",
        default=DEFAULT_WORKSPACE,
        help=f"Directorio raiz de proyectos (default: {DEFAULT_WORKSPACE})",
    )
    parser.add_argument(
        "--gemini-key",
        default=os.getenv("GEMINI_API_KEY", ""),
        help="GEMINI_API_KEY a inyectar en la configuracion",
    )
    parser.add_argument(
        "--target",
        choices=["antigravity", "claude", "all"],
        default="all",
        help="Cliente MCP destino (default: all)",
    )
    args = parser.parse_args()

    console.print("[bold]Instalando rag-local MCP[/bold]")
    console.print(f"  Proyecto  : {RAG_LOCAL_DIR}")
    console.print(f"  Workspace : {args.workspace}")
    console.print(f"  Modo      : {args.mode}")
    console.print(f"  Destino   : {args.target}\n")

    installed = 0
    for client, path in MCP_CONFIG_PATHS.items():
        if args.target not in ("all", client):
            continue
        if client == "claude" and not path.parent.exists():
            console.print(f"  [dim]SKIP {path} (directorio no encontrado)[/dim]")
            continue
        try:
            install_to(path, args.mode, args.workspace, args.gemini_key)
            installed += 1
        except Exception as e:
            error_console.print(f"  [red]ERROR {path}: {e}[/red]")

    console.print()
    if installed:
        console.print(
            f"[bold green]Listo.[/bold green] Instalado en {installed} archivo(s)."
        )
        console.print("Reinicia el cliente MCP para que los cambios surtan efecto.")
    else:
        error_console.print(
            "[red]No se instalo ningun archivo. Verifica --target y las rutas.[/red]"
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
