import shutil
import subprocess
import sys

import pytest


def _is_docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        res = subprocess.run(
            ["docker", "info"], capture_output=True, text=True, check=False
        )
        return res.returncode == 0
    except Exception:
        return False


if not _is_docker_available():
    pytest.skip(
        "Docker daemon no está en ejecución en el entorno actual",
        allow_module_level=True,
    )


def test_docker_mcp_clean_stdout() -> None:
    """Verifica que la ejecución en Docker no contamine stdout con banners ni logs."""
    cmd = [
        "docker",
        "--log-level",
        "ERROR",
        "compose",
        "run",
        "--rm",
        "-T",
        "-q",
        "rag-mcp",
        "python",
        "-c",
        "print('PURE_STDOUT_TEST')",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert res.returncode == 0, f"Error ejecutando Docker: {res.stderr}"
    lines = [line.strip() for line in res.stdout.strip().splitlines() if line.strip()]
    assert lines == ["PURE_STDOUT_TEST"], f"stdout contaminado con: {res.stdout}"


def test_docker_mcp_excludes_manage_daemon() -> None:
    """Verifica que manage_daemon no aparezca en las herramientas MCP dentro de Docker."""
    cmd = [
        "docker",
        "compose",
        "run",
        "--rm",
        "rag-mcp",
        "python",
        "-c",
        "import asyncio; from rag_local.mcp.server import mcp; "
        "tools = [t.name for t in asyncio.run(mcp.list_tools())]; "
        "print(','.join(tools))",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert res.returncode == 0, f"Error listando herramientas: {res.stderr}"

    # Extraer la última línea que contiene la lista separada por comas
    last_line = res.stdout.strip().splitlines()[-1]
    tools = [t.strip() for t in last_line.split(",") if t.strip()]

    assert "manage_daemon" not in tools
    assert "query_codebase" in tools
    assert "ingest_codebase" in tools
    assert "get_project_map" in tools
