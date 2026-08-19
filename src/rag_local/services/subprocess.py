import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from rag_local.core import config
from rag_local.core.logging import logger


@dataclass
class SubprocessResult:
    returncode: int
    stdout: bytes
    stderr: bytes


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
        "Actualizados" in clean_msg
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
    timeout: float = config.CLI_SUBPROCESS_TIMEOUT,
    on_stderr_line: Callable[[str], Awaitable[None]] | None = None,
) -> SubprocessResult:
    """Ejecuta un subproceso CLI asíncrono con timeouts y callbacks de progreso."""
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

    async def read_stdout() -> None:
        while True:
            line_bytes = await process.stdout.readline()
            if not line_bytes:
                break
            stdout_lines.append(line_bytes)

    async def read_stderr() -> None:
        while True:
            line_bytes = await process.stderr.readline()
            if not line_bytes:
                break
            stderr_lines.append(line_bytes)
            if on_stderr_line:
                line = line_bytes.decode("utf-8", errors="replace").strip()
                try:
                    await on_stderr_line(line)
                except Exception as err:
                    logger.debug(f"Error en el callback de progreso: {err}")

    try:
        await asyncio.wait_for(
            asyncio.gather(read_stdout(), read_stderr(), process.wait()),
            timeout=timeout,
        )
    except TimeoutError as e:
        try:
            process.kill()
            await process.wait()
        except Exception as kill_err:
            logger.warning(
                f"No se pudo forzar la finalización del subproceso: {kill_err}"
            )
        raise TimeoutError(
            "El proceso superó el tiempo límite y fue finalizado."
        ) from e

    return SubprocessResult(
        returncode=process.returncode if process.returncode is not None else -1,
        stdout=b"".join(stdout_lines),
        stderr=b"".join(stderr_lines),
    )
