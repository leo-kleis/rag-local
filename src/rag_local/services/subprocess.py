import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from rag_local.core import config
from rag_local.core.logging import logger


@dataclass
class SubprocessResult:
    returncode: int
    stdout: bytes
    stderr: bytes


_INGESTION_PATTERNS: tuple[str, ...] = (
    "AUTO-SYNC",
    "Iniciando re-ingesta",
    "Indexando lote",
    "Indexando",
    "Sincronizando",
    "Detectados",
    "Cambio de esquema",
    "¡Ingesta",
)


def parse_auto_sync_progress(line: str) -> tuple[int, str, bool]:
    """Parsea líneas de AUTO-SYNC para extraer progreso dinámico y mensaje.

    Returns:
        (progress_pct, clean_msg, is_final_summary)
    """
    parts = line.split("AUTO-SYNC]", 1)
    raw_msg = parts[1].strip() if len(parts) > 1 else line.strip()
    clean_msg = re.sub(r"\[/?[a-zA-Z0-9_\s=-]+\]", "", raw_msg).strip()

    prog = 40
    is_final = False

    if "Cambio de esquema" in clean_msg:
        prog = 25
    elif "Detectados" in clean_msg:
        prog = 35
    elif "Actualizados" in clean_msg:
        prog = 60
        is_final = True
    elif (
        "Índice sincronizado" in clean_msg
        or "re-ingesta forzada" in clean_msg
        or "completada" in clean_msg
        or "sincronizado" in clean_msg
    ):
        prog = 65
        is_final = True

    return prog, clean_msg, is_final


async def run_cli_subprocess(
    cmd: list[str],
    cwd: str,
    env: dict[str, str],
    timeout: float | None = config.DEFAULT_CLI_TIMEOUT,
    inactivity_timeout: float = config.INGESTION_INACTIVITY_TIMEOUT,
    is_ingestion: bool = False,
    on_stderr_line: Callable[[str], Awaitable[None]] | None = None,
) -> SubprocessResult:
    """Ejecuta un subproceso CLI asíncrono con timeouts dinámicos y watchdog.

    Si la operación es una consulta normal, aplica timeout de 3 minutos.
    Si se detecta fase de ingesta o sincronización (AUTO-SYNC / indexación),
    anula el timeout estático y activa un watchdog de inactividad de 10 min.
    """
    process = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=cwd,
        env=env,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    if process.stdout is None or process.stderr is None:
        raise RuntimeError("No se abrieron los canales del subproceso.")

    stdout_lines: list[bytes] = []
    stderr_lines: list[bytes] = []

    start_time = time.monotonic()
    last_activity = time.monotonic()
    ingestion_mode = is_ingestion

    async def read_stdout() -> None:
        nonlocal last_activity
        while True:
            line_bytes = await process.stdout.readline()
            if not line_bytes:
                break
            last_activity = time.monotonic()
            stdout_lines.append(line_bytes)

    async def read_stderr() -> None:
        nonlocal last_activity, ingestion_mode
        while True:
            line_bytes = await process.stderr.readline()
            if not line_bytes:
                break
            last_activity = time.monotonic()
            stderr_lines.append(line_bytes)
            line = line_bytes.decode("utf-8", errors="replace").strip()

            # Detección automática de transición a fase de ingesta/sincronización
            if not ingestion_mode and any(p in line for p in _INGESTION_PATTERNS):
                ingestion_mode = True
                logger.info(
                    "[SUBPROCESS] Transición detectada a modo ingesta/sincronización. "
                    "Timeout estático anulado, activado watchdog de inactividad."
                )

            if on_stderr_line:
                try:
                    await on_stderr_line(line)
                except Exception as err:
                    logger.debug(f"Error en el callback de progreso: {err}")

    async def watchdog_monitor() -> None:
        while process.returncode is None:
            await asyncio.sleep(1.0)
            now = time.monotonic()
            if ingestion_mode or timeout is None:
                if now - last_activity > inactivity_timeout:
                    raise TimeoutError(
                        "El proceso de ingesta quedó inactivo por más de "
                        f"{int(inactivity_timeout)}s (10 min) sin registrar progreso."
                    )
            else:
                if now - start_time > timeout:
                    raise TimeoutError(
                        "La operación superó el límite de tiempo estándar "
                        f"de {int(timeout)}s ({int(timeout // 60)} min)."
                    )

    try:
        await asyncio.gather(
            read_stdout(),
            read_stderr(),
            watchdog_monitor(),
            process.wait(),
        )
    except TimeoutError as e:
        try:
            process.kill()
            await process.wait()
        except Exception as kill_err:
            logger.warning(
                f"No se pudo forzar la finalización del subproceso: {kill_err}"
            )
        raise TimeoutError(str(e)) from e

    return SubprocessResult(
        returncode=process.returncode if process.returncode is not None else -1,
        stdout=b"".join(stdout_lines),
        stderr=b"".join(stderr_lines),
    )
