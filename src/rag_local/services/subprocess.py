import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from rag_local.core import config
from rag_local.core.events import SyncPhase, parse_sync_event
from rag_local.core.logging import logger


@dataclass
class SubprocessResult:
    returncode: int
    stdout: bytes
    stderr: bytes


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
    Si se detecta fase de ingesta o sincronización (SyncPhase.START / PROGRESS),
    anula el timeout estático y activa un watchdog de inactividad de 10 min.
    Cuando la sincronización concluye (SyncPhase.COMPLETED), restablece los 3 min.
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
        nonlocal last_activity, ingestion_mode, start_time
        while True:
            line_bytes = await process.stderr.readline()
            if not line_bytes:
                break
            last_activity = time.monotonic()
            stderr_lines.append(line_bytes)
            line = line_bytes.decode("utf-8", errors="replace").strip()

            # Procesamiento de eventos IPC estructurados
            event = parse_sync_event(line)
            if event is not None:
                if event.phase in (SyncPhase.START, SyncPhase.PROGRESS):
                    if not ingestion_mode:
                        ingestion_mode = True
                        logger.info(
                            f"[SUBPROCESS] Transición a ingesta ({event.phase}). "
                            "Timeout desactivado, watchdog activo."
                        )
                elif (
                    event.phase == SyncPhase.COMPLETED
                    and ingestion_mode
                    and not is_ingestion
                ):
                    ingestion_mode = False
                    start_time = time.monotonic()
                    logger.info(
                        "[SUBPROCESS] Sincronización completada. "
                        "Restablecido timeout de 3 min para el comando principal."
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
