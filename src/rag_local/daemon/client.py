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


class DaemonBusyError(Exception):
    """Excepción lanzada cuando el daemon responde con estado ocupado (503/429)."""


def try_daemon_embed(
    texts: list[str],
    override_dir: Path | None = None,
    is_ingestion: bool = True,
) -> list[list[float]] | None:
    """Intenta generar embeddings usando el Worker Daemon en segundo plano.

    Retorna None si el daemon no está disponible o falla, permitiendo un
    fallback transparente a la carga local.
    """
    if not texts:
        return []

    port_data = read_port_file(override_dir)
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
        body = json.dumps({"texts": texts, "is_ingestion": is_ingestion})
        conn.request("POST", "/embed", body=body, headers=headers)
        resp = conn.getresponse()

        if resp.status == 200:
            resp_data = json.loads(resp.read().decode("utf-8"))
            embeddings = resp_data.get("embeddings")
            if isinstance(embeddings, list):
                return embeddings
        elif resp.status in (429, 503):
            resp_data = json.loads(resp.read().decode("utf-8"))
            raise DaemonBusyError(
                resp_data.get(
                    "message",
                    "El daemon se encuentra ocupado ejecutando una tarea masiva.",
                )
            )
    except DaemonBusyError:
        raise
    except Exception as e:
        logger.debug(f"Fallo al consultar daemon para embeddings (fallback local): {e}")
    finally:
        if conn:
            conn.close()
    return None


def try_daemon_rerank(
    query: str, docs: list[str], override_dir: Path | None = None
) -> list[DaemonRankResult] | None:
    """Intenta re-rankear documentos usando el Worker Daemon en segundo plano.

    Retorna None si el daemon no está disponible o falla.
    """
    if not docs:
        return []

    port_data = read_port_file(override_dir)
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
        elif resp.status in (429, 503):
            resp_data = json.loads(resp.read().decode("utf-8"))
            raise DaemonBusyError(
                resp_data.get(
                    "message",
                    "El daemon se encuentra ocupado ejecutando una tarea masiva.",
                )
            )
    except DaemonBusyError:
        raise
    except Exception as e:
        logger.debug(f"Fallo al consultar daemon para re-ranking (fallback local): {e}")
    finally:
        if conn:
            conn.close()
    return None


def try_daemon_query(
    query: str,
    scope: str | None = None,
    k: int = 4,
    full_block: bool = False,
    generate_response: bool = False,
    respond_in_english: bool = False,
    override_dir: Path | None = None,
) -> dict[str, Any] | None:
    """Ejecuta la consulta completa a través del daemon si está disponible."""
    port_data = read_port_file(override_dir)
    if not port_data or not is_daemon_alive(port_data):
        return None

    port = port_data.get("port")
    token = port_data.get("token")
    if not isinstance(port, int) or not isinstance(token, str):
        return None

    conn = None
    try:
        conn = http.client.HTTPConnection(
            "127.0.0.1", port, timeout=config.DEFAULT_CLI_TIMEOUT
        )
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "Host": "127.0.0.1",
        }
        body = json.dumps(
            {
                "query": query,
                "scope": scope,
                "k": k,
                "full_block": full_block,
                "generate_response": generate_response,
                "respond_in_english": respond_in_english,
            }
        )
        conn.request("POST", "/query", body=body, headers=headers)
        resp = conn.getresponse()

        if resp.status == 200:
            return json.loads(resp.read().decode("utf-8"))
        elif resp.status in (429, 503):
            resp_data = json.loads(resp.read().decode("utf-8"))
            raise DaemonBusyError(
                resp_data.get(
                    "message",
                    "El daemon se encuentra ocupado ejecutando una tarea masiva.",
                )
            )
    except DaemonBusyError:
        raise
    except Exception as e:
        logger.debug(
            f"Fallo al consultar endpoint /query del daemon (fallback CLI): {e}"
        )
    finally:
        if conn:
            conn.close()
    return None


def daemon_healthcheck(
    override_dir: Path | None = None,
) -> dict[str, Any] | None:
    """Consulta el estado del daemon si está activo."""
    port_data = read_port_file(override_dir)
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


def daemon_claim(new_parent_pid: int, override_dir: Path | None = None) -> bool:
    """Reclama la sesión del daemon asociándolo a un nuevo PID padre."""
    port_data = read_port_file(override_dir)
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


def daemon_shutdown(override_dir: Path | None = None) -> bool:
    """Envía la solicitud de apagado al daemon."""
    port_data = read_port_file(override_dir)
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
