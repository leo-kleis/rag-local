import asyncio
import contextlib
import time
from collections.abc import AsyncGenerator, Callable
from pathlib import Path
from typing import Any

from filelock import FileLock

from rag_local.core import config


class AsyncRWLock:
    """Bloqueo asíncrono Reader-Writer (Lectura compartida / Escritura exclusiva).

    Permite que múltiples lectores accedan concurrentemente en paralelo,
    mientras que los escritores obtienen acceso exclusivo impidiendo lecturas
    y escrituras simultáneas.
    """

    def __init__(self) -> None:
        self._readers = 0
        self._writer = False
        self._condition = asyncio.Condition()

    async def acquire_read(self) -> None:
        async with self._condition:
            while self._writer:
                await self._condition.wait()
            self._readers += 1

    async def release_read(self) -> None:
        async with self._condition:
            self._readers -= 1
            if self._readers == 0:
                self._condition.notify_all()

    async def acquire_write(self) -> None:
        async with self._condition:
            while self._writer or self._readers > 0:
                await self._condition.wait()
            self._writer = True

    async def release_write(self) -> None:
        async with self._condition:
            self._writer = False
            self._condition.notify_all()

    @property
    def is_writing(self) -> bool:
        return self._writer

    @property
    def active_readers(self) -> int:
        return self._readers


class GlobalIngestLock:
    """Mutex singleton para asegurar que solo ocurra un Ingest a la vez."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._active_project: str | None = None
        self._started_at: float | None = None

    @property
    def is_locked(self) -> bool:
        return self._lock.locked()

    @property
    def active_project(self) -> str | None:
        return self._active_project

    async def acquire(self, project_name: str = "") -> None:
        await self._lock.acquire()
        self._active_project = project_name
        self._started_at = time.time()

    def release(self) -> None:
        self._active_project = None
        self._started_at = None
        if self._lock.locked():
            self._lock.release()


class ProjectLockManager:
    """Gestor centralizado de bloqueos por proyecto e inter-proceso."""

    def __init__(self) -> None:
        self._project_locks: dict[str, AsyncRWLock] = {}
        self._global_ingest = GlobalIngestLock()
        self._global_deps_lock = AsyncRWLock()
        self._dict_lock = asyncio.Lock()

    async def _get_rwlock(self, repo_path: Path) -> AsyncRWLock:
        canonical = str(repo_path.resolve())
        async with self._dict_lock:
            if canonical not in self._project_locks:
                self._project_locks[canonical] = AsyncRWLock()
            return self._project_locks[canonical]

    @contextlib.asynccontextmanager
    async def acquire_read(
        self,
        repo_path: Path,
        on_waiting: Callable[[str], Any] | None = None,
    ) -> AsyncGenerator[None, None]:
        """Adquiere bloqueo de lectura compartido para un proyecto específico.

        Si hay una ingesta global activa en cualquier proyecto, espera a que finalice.
        """
        waited = False

        # 1. Si hay una ingesta global en curso en cualquier proyecto, esperar
        if self._global_ingest.is_locked:
            waited = True
            if on_waiting:
                other_proj = self._global_ingest.active_project or "otro proyecto"
                msg = (
                    f"En espera: hay una ingesta en curso en '{other_proj}'. "
                    "La operación se reanudará automáticamente al finalizar."
                )
                res = on_waiting(msg)
                if asyncio.iscoroutine(res):
                    await res

            while self._global_ingest.is_locked:
                await asyncio.sleep(0.1)

        rwlock = await self._get_rwlock(repo_path)
        project_name = repo_path.name or str(repo_path)

        if rwlock.is_writing:
            if not waited:
                waited = True
                if on_waiting:
                    msg = (
                        f"En espera: hay una ingesta en curso en '{project_name}'. "
                        "La operación se reanudará automáticamente al finalizar."
                    )
                    res = on_waiting(msg)
                    if asyncio.iscoroutine(res):
                        await res

            while rwlock.is_writing:
                await asyncio.sleep(0.1)

        await rwlock.acquire_read()

        if waited and on_waiting:
            msg = f"Ingesta finalizada. Reanudando lectura en '{project_name}'..."
            res = on_waiting(msg)
            if asyncio.iscoroutine(res):
                await res

        try:
            yield
        finally:
            await rwlock.release_read()

    @contextlib.asynccontextmanager
    async def acquire_write(
        self,
        repo_path: Path,
        on_waiting: Callable[[str], Any] | None = None,
    ) -> AsyncGenerator[None, None]:
        """Adquiere bloqueo exclusivo de escritura para un proyecto específico."""
        rwlock = await self._get_rwlock(repo_path)
        project_name = repo_path.name or str(repo_path)
        waited = False

        if rwlock.is_writing or rwlock.active_readers > 0:
            waited = True
            if on_waiting:
                msg = (
                    "En espera: esperando acceso exclusivo para ingesta "
                    f"en '{project_name}'..."
                )
                res = on_waiting(msg)
                if asyncio.iscoroutine(res):
                    await res

            while rwlock.is_writing or rwlock.active_readers > 0:
                await asyncio.sleep(0.1)

        await rwlock.acquire_write()

        if waited and on_waiting:
            msg = (
                f"Acceso exclusivo obtenido para '{project_name}'. Iniciando ingesta..."
            )
            res = on_waiting(msg)
            if asyncio.iscoroutine(res):
                await res

        # Adquirir también FileLock en disco para excluir procesos CLI externos
        lock_file_path = repo_path / ".lancedb" / ".ingest.lock"
        lock_file_path.parent.mkdir(parents=True, exist_ok=True)
        file_lock = FileLock(str(lock_file_path), timeout=600.0)

        try:
            file_lock.acquire()
            yield
        finally:
            if file_lock.is_locked:
                file_lock.release()
            await rwlock.release_write()

    @contextlib.asynccontextmanager
    async def acquire_global_ingest(
        self,
        repo_path: Path,
        on_waiting: Callable[[str], Any] | None = None,
    ) -> AsyncGenerator[None, None]:
        """Adquiere el bloqueo global de ingesta única y el write lock del proyecto."""
        project_name = repo_path.name or str(repo_path)

        if self._global_ingest.is_locked and on_waiting:
            other_proj = self._global_ingest.active_project or "otro proyecto"
            msg = (
                f"En cola de ingesta global: esperando a que finalice "
                f"la ingesta en '{other_proj}'..."
            )
            res = on_waiting(msg)
            if asyncio.iscoroutine(res):
                await res

        await self._global_ingest.acquire(project_name=project_name)

        # FileLock global en disco para coordinar con subprocesos CLI
        global_lock_path = config.DAEMON_DATA_DIR / ".global_ingest.lock"
        global_lock_path.parent.mkdir(parents=True, exist_ok=True)
        global_file_lock = FileLock(str(global_lock_path), timeout=600.0)

        try:
            global_file_lock.acquire()
            async with self.acquire_write(repo_path, on_waiting=on_waiting):
                yield
        finally:
            if global_file_lock.is_locked:
                global_file_lock.release()
            self._global_ingest.release()

    @contextlib.asynccontextmanager
    async def acquire_deps(
        self,
        mode: str = "read",
        on_waiting: Callable[[str], Any] | None = None,
    ) -> AsyncGenerator[None, None]:
        """Adquiere bloqueo para la base de datos global de dependencias."""
        if mode == "write":
            if (
                self._global_deps_lock.is_writing
                or self._global_deps_lock.active_readers > 0
            ) and on_waiting:
                msg = "Esperando acceso exclusivo a base de dependencias..."
                res = on_waiting(msg)
                if asyncio.iscoroutine(res):
                    await res

            await self._global_deps_lock.acquire_write()
            lock_path = config.GLOBAL_DEPS_LANCEDB_PATH / ".deps.lock"
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            file_lock = FileLock(str(lock_path), timeout=300.0)
            try:
                file_lock.acquire()
                yield
            finally:
                if file_lock.is_locked:
                    file_lock.release()
                await self._global_deps_lock.release_write()
        else:
            if self._global_deps_lock.is_writing and on_waiting:
                msg = "Esperando a que finalice sincronización de dependencias..."
                res = on_waiting(msg)
                if asyncio.iscoroutine(res):
                    await res

            await self._global_deps_lock.acquire_read()
            try:
                yield
            finally:
                await self._global_deps_lock.release_read()


# Instancia singleton global del gestor de bloqueos
lock_manager = ProjectLockManager()
