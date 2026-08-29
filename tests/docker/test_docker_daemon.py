import json
import subprocess

import pytest


def test_docker_daemon_status() -> None:
    """Verifica que el daemon en Docker responda con estado activo y dispositivo CUDA."""
    cmd = [
        "docker",
        "compose",
        "run",
        "--rm",
        "rag-local",
        "rag-daemon",
        "status",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert res.returncode == 0, f"Error en daemon status: {res.stderr}"
    assert "WORKER_DAEMON: Activo" in res.stdout
    assert "Dispositivo: cuda" in res.stdout
