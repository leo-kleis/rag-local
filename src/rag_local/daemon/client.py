import http.client
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rag_local.core import config
from rag_local.core.logging import logger
from rag_local.daemon.port_file import is_daemon_alive, read_port_file


@dataclass
class DaemonRankResult:
    """Objeto compatible con la salida de rerankers.Reranker."""

    doc_id: int
    score: float


def try_daemon_embed(
    texts: list[str], lancedb_path: Path | None = None
) -> list[list[float]] | None:
    """Intenta generar embeddings usando el Worker Daemon en segundo plano.

    Retorna None si el daemon no está disponible o falla, permitiendo un
    fallback transparente a la carga local.
    """
    if not texts:
        return []

    port_data = read_port_file(lancedb_path)
    if not port_data:
        return None

    port = port_data.get("port")
    token = port_data.get("token")
    if not isinstance(port, int) or not isinstance(token, str):
        return None

    conn = None
    try:
        conn = http.client.HTTPConnection(
            "127.0.0.1", port, timeout=config.DAEMON_REQUEST_TIMEOUT
        )
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "Host": "127.0.0.1",
        }
        body = json.dumps({"texts": texts})
        conn.request("POST", "/embed", body=body, headers=headers)
        resp = conn.getresponse()

        if resp.status == 200:
            resp_data = json.loads(resp.read().decode("utf-8"))
            embeddings = resp_data.get("embeddings")
            if isinstance(embeddings, list):
                return embeddings
    except Exception as e:
        logger.debug(f"Fallo al consultar daemon para embeddings (fallback local): {e}")
    finally:
        if conn:
            conn.close()
    return None


def try_daemon_rerank(
    query: str, docs: list[str], lancedb_path: Path | None = None
) -> list[DaemonRankResult] | None:
    """Intenta re-rankear documentos usando el Worker Daemon en segundo plano.

    Retorna None si el daemon no está disponible o falla.
    """
    if not docs:
        return []

    port_data = read_port_file(lancedb_path)
    if not port_data:
        return None

    port = port_data.get("port")
    token = port_data.get("token")
    if not isinstance(port, int) or not isinstance(token, str):
        return None

    conn = None
    try:
        conn = http.client.HTTPConnection(
            "127.0.0.1", port, timeout=config.DAEMON_REQUEST_TIMEOUT
        )
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "Host": "127.0.0.1",
        }
        body = json.dumps({"query": query, "docs": docs})
        conn.request("POST", "/rerank", body=body, headers=headers)
        resp = conn.getresponse()

        if resp.status == 200:
            resp_data = json.loads(resp.read().decode("utf-8"))
            raw_results = resp_data.get("results")
            if isinstance(raw_results, list):
                return [
                    DaemonRankResult(
                        doc_id=int(item["doc_id"]),
                        score=float(item["score"]),
                    )
                    for item in raw_results
                    if "doc_id" in item and "score" in item
                ]
    except Exception as e:
        logger.debug(f"Fallo al consultar daemon para re-ranking (fallback local): {e}")
    finally:
        if conn:
            conn.close()
    return None


def daemon_healthcheck(
    lancedb_path: Path | None = None,
) -> dict[str, Any] | None:
    """Consulta el estado del daemon si está activo."""
    port_data = read_port_file(lancedb_path)
    if not port_data or not is_daemon_alive(port_data):
        return None

    port = port_data["port"]
    token = port_data["token"]
    conn = None
    try:
        conn = http.client.HTTPConnection(
            "127.0.0.1", port, timeout=config.DAEMON_HEALTH_TIMEOUT
        )
        headers = {
            "Authorization": f"Bearer {token}",
            "Host": "127.0.0.1",
        }
        conn.request("GET", "/health", headers=headers)
        resp = conn.getresponse()
        if resp.status == 200:
            data = json.loads(resp.read().decode("utf-8"))
            data["port"] = port
            data["pid"] = port_data.get("pid")
            return data
    except Exception as e:
        logger.debug(f"Error en healthcheck del daemon: {e}")
    finally:
        if conn:
            conn.close()
    return None


def daemon_claim(new_parent_pid: int, lancedb_path: Path | None = None) -> bool:
    """Reclama la sesión del daemon asociándolo a un nuevo PID padre."""
    port_data = read_port_file(lancedb_path)
    if not port_data:
        return False

    port = port_data["port"]
    token = port_data["token"]
    conn = None
    try:
        conn = http.client.HTTPConnection(
            "127.0.0.1", port, timeout=config.DAEMON_HEALTH_TIMEOUT
        )
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "Host": "127.0.0.1",
        }
        body = json.dumps({"new_parent_pid": new_parent_pid})
        conn.request("POST", "/claim", body=body, headers=headers)
        resp = conn.getresponse()
        return resp.status == 200
    except Exception as e:
        logger.debug(f"Error al reclamar sesión del daemon: {e}")
        return False
    finally:
        if conn:
            conn.close()


def daemon_shutdown(lancedb_path: Path | None = None) -> bool:
    """Envía la solicitud de apagado al daemon."""
    port_data = read_port_file(lancedb_path)
    if not port_data:
        return False

    port = port_data["port"]
    token = port_data["token"]
    conn = None
    try:
        conn = http.client.HTTPConnection(
            "127.0.0.1", port, timeout=config.DAEMON_HEALTH_TIMEOUT
        )
        headers = {
            "Authorization": f"Bearer {token}",
            "Host": "127.0.0.1",
        }
        conn.request("POST", "/shutdown", headers=headers)
        resp = conn.getresponse()
        return resp.status == 200
    except Exception as e:
        logger.debug(f"Error al enviar shutdown al daemon: {e}")
        return False
    finally:
        if conn:
            conn.close()
