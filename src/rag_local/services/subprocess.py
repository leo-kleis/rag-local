import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

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
    timeout: float = 300.0,
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
