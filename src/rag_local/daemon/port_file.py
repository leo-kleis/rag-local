import contextlib
import http.client
import json
import os
import secrets
import tempfile
from pathlib import Path
from typing import Any

import psutil

from rag_local.core import config
from rag_local.core.logging import logger


def get_port_file_path(
    override_dir: Path | None = None,
    lancedb_path: Path | None = None,
) -> Path:
    """Retorna la ruta absoluta del archivo daemon.json.

    Por defecto, el port file reside en el directorio global del usuario
    (DAEMON_DATA_DIR), compartido por todos los proyectos.
    El parámetro override_dir o lancedb_path permite sobrescribir
    la ubicación (para tests).
    """
    base_dir = override_dir if override_dir is not None else lancedb_path
    base_dir = base_dir if base_dir is not None else config.DAEMON_DATA_DIR
    return base_dir / config.DAEMON_PORT_FILE


def generate_token() -> str:
    """Genera un token criptográfico seguro de 43 caracteres para autenticación."""
    return secrets.token_urlsafe(32)


def write_port_file(
    data: dict[str, Any],
    override_dir: Path | None = None,
    lancedb_path: Path | None = None,
) -> Path | None:
    """Escribe de forma atómica el archivo de estado del daemon."""
    try:
        target_path = get_port_file_path(override_dir, lancedb_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        dir_name = str(target_path.parent)

        with tempfile.NamedTemporaryFile(
            "w", dir=dir_name, delete=False, encoding="utf-8"
        ) as tf:
            json.dump(data, tf, indent=2)
            tf.flush()
            os.fsync(tf.fileno())
            temp_name = tf.name

        os.replace(temp_name, str(target_path))
        return target_path
    except Exception as e:
        logger.debug(f"No se pudo escribir port file en {override_dir}: {e}")
        return None


def read_port_file(
    override_dir: Path | None = None,
    lancedb_path: Path | None = None,
) -> dict[str, Any] | None:
    """Lee el archivo de estado del daemon.

    Retorna None si no existe o está corrupto.
    """
    target_path = get_port_file_path(override_dir, lancedb_path)
    if target_path.is_file():
        try:
            with open(target_path, encoding="utf-8") as f:
                data = json.load(f)
                if (
                    isinstance(data, dict)
                    and "port" in data
                    and "token" in data
                    and "pid" in data
                ):
                    return data
        except Exception as e:
            logger.debug(f"Error al leer daemon.json ({target_path}): {e}")

    # Fallback para variables de entorno explícitas
    daemon_token = os.getenv("DAEMON_TOKEN")
    if daemon_token:
        port = int(os.getenv("DAEMON_PORT", "21239"))
        return {"port": port, "token": daemon_token, "pid": 1}

    # Probar si el daemon (ej. en Docker o background) responde en el puerto estándar
    default_token = "rag-local-internal-token"  # noqa: S105
    default_port = int(os.getenv("DAEMON_PORT", "21239"))
    candidate = {"port": default_port, "token": default_token, "pid": 1}
    if is_daemon_alive(candidate, timeout=0.3):
        return candidate

    return None


def delete_port_file(
    override_dir: Path | None = None,
    lancedb_path: Path | None = None,
) -> bool:
    """Elimina de forma segura el archivo daemon.json."""
    target_path = get_port_file_path(override_dir, lancedb_path)
    with contextlib.suppress(Exception):
        if target_path.exists():
            target_path.unlink()
            return True
    return False


def is_daemon_alive(port_data: dict[str, Any], timeout: float | None = None) -> bool:
    """Verifica si el daemon registrado en el port_data está activo y respondiendo."""
    pid = port_data.get("pid")
    port = port_data.get("port")
    token = port_data.get("token")

    if (
        not isinstance(pid, int)
        or not isinstance(port, int)
        or not isinstance(token, str)
    ):
        return False

    # 1. Comprobación de PID si es un PID real de proceso local (> 1)
    if pid > 1 and not config.IS_DOCKER and not psutil.pid_exists(pid):
        return False

    # 2. Comprobación HTTP Healthcheck vía socket loopback
    req_timeout = timeout if timeout is not None else config.DAEMON_HEALTH_TIMEOUT
    conn = None
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=req_timeout)
        headers = {
            "Authorization": f"Bearer {token}",
            "Host": "127.0.0.1",
        }
        conn.request("GET", "/health", headers=headers)
        resp = conn.getresponse()
        return resp.status == 200
    except Exception:
        return False
    finally:
        if conn:
            with contextlib.suppress(Exception):
                conn.close()
