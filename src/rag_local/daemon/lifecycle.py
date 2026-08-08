import asyncio
import time
from collections.abc import Awaitable, Callable

import psutil

from rag_local.core import config
from rag_local.core.logging import logger


class LifecycleManager:
    """Gestiona el ciclo de vida del Worker Daemon:

    - Monitoreo del PID del proceso padre (MCP server/IDE)
    - Periodo de gracia de 15s tras pérdida de PID padre
    - Temporizador de inactividad de 30m (idle shutdown)
    - Registro de tiempo activo y última actividad
    """

    def __init__(
        self,
        parent_pid: int | None = None,
        idle_timeout: float | None = None,
        grace_period: float | None = None,
        check_interval: float = 1.0,
    ) -> None:
        self.parent_pid = parent_pid
        self.idle_timeout = (
            idle_timeout
            if idle_timeout is not None
            else float(config.DAEMON_IDLE_TIMEOUT)
        )
        self.grace_period = (
            grace_period
            if grace_period is not None
            else float(config.DAEMON_GRACE_PERIOD)
        )
        self.check_interval = check_interval

        self.started_at: float = time.time()
        self.last_activity: float = time.time()
        self.grace_start: float | None = None

        self._shutdown_callback: Callable[[], Awaitable[None]] | None = None
        self._monitor_task: asyncio.Task[None] | None = None
        self._running: bool = False

    def record_activity(self) -> None:
        """Registra actividad reciente para resetear el temporizador de inactividad."""
        self.last_activity = time.time()

    def claim(self, new_parent_pid: int) -> bool:
        """Reclama la sesión asociando un nuevo PID padre y cancela la gracia."""
        if not isinstance(new_parent_pid, int) or new_parent_pid <= 0:
            return False
        if not psutil.pid_exists(new_parent_pid):
            return False

        old_pid = self.parent_pid
        self.parent_pid = new_parent_pid
        self.grace_start = None
        self.record_activity()
        logger.info(
            f"Sesión del daemon reclamada: parent_pid actualizado de "
            f"{old_pid} a {new_parent_pid}"
        )
        return True

    def get_uptime_s(self) -> float:
        """Retorna el tiempo transcurrido desde el arranque del daemon en segundos."""
        return max(0.0, time.time() - self.started_at)

    def get_idle_s(self) -> float:
        """Retorna el tiempo transcurrido desde la última solicitud en segundos."""
        return max(0.0, time.time() - self.last_activity)

    def is_in_grace_period(self) -> bool:
        """Indica si el daemon se encuentra actualmente en periodo de gracia."""
        return self.grace_start is not None

    def start(self, shutdown_callback: Callable[[], Awaitable[None]]) -> None:
        """Inicia la tarea asíncrona de monitoreo de ciclo de vida."""
        self._shutdown_callback = shutdown_callback
        self._running = True
        self._monitor_task = asyncio.create_task(self._monitor_loop())

    def stop(self) -> None:
        """Detiene la tarea asíncrona de monitoreo."""
        self._running = False
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()

    async def _trigger_shutdown(self, reason: str) -> None:
        """Dispara el callback de apagado limpio del daemon."""
        if not self._running:
            return
        self._running = False
        logger.info(f"Iniciando apagado del Worker Daemon por: {reason}")
        if self._shutdown_callback:
            try:
                await self._shutdown_callback()
            except Exception as e:
                logger.warning(f"Error durante el callback de shutdown del daemon: {e}")

    async def _monitor_loop(self) -> None:
        """Bucle de monitoreo que verifica PID padre y temporizador de inactividad."""
        while self._running:
            try:
                await asyncio.sleep(self.check_interval)
                now = time.time()

                # 1. Monitoreo de inactividad máxima (Idle Timeout)
                if now - self.last_activity >= self.idle_timeout:
                    await self._trigger_shutdown(
                        f"Inactividad superior a {int(self.idle_timeout)}s (30m)"
                    )
                    break

                # 2. Monitoreo de PID padre y Periodo de Gracia
                if self.parent_pid is not None:
                    parent_alive = psutil.pid_exists(self.parent_pid)
                    if not parent_alive:
                        if self.grace_start is None:
                            self.grace_start = now
                            logger.info(
                                f"Proceso padre (PID {self.parent_pid}) finalizado. "
                                f"Iniciando Periodo de Gracia de "
                                f"{int(self.grace_period)}s..."
                            )
                        elif now - self.grace_start >= self.grace_period:
                            await self._trigger_shutdown(
                                f"Expiró el periodo de gracia de "
                                f"{int(self.grace_period)}s sin reclamo de nuevo PID"
                            )
                            break
                    else:
                        # Si el padre sigue vivo, asegurar que la gracia esté inactiva
                        self.grace_start = None

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Error inesperado en monitor_loop de lifecycle: {e}")
