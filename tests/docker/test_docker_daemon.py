import json
import shutil
import subprocess

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


def test_docker_daemon_status() -> None:
    """Verifica que el daemon en Docker responda con estado activo y dispositivo CUDA."""
    cmd = [
        "docker",
        "compose",
        "exec",
        "-T",
        "rag-daemon",
        "rag-daemon",
        "status",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert res.returncode == 0, f"Error en daemon status: {res.stderr}"
    assert "WORKER_DAEMON: Activo" in res.stdout
    assert "Dispositivo: cuda" in res.stdout
