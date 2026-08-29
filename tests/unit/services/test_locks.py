import asyncio
import time
from pathlib import Path

from rag_local.services.locks import AsyncRWLock, ProjectLockManager


def test_async_rw_lock_parallel_readers() -> None:
    """Verifica que múltiples lectores adquieran el bloqueo en paralelo simultáneamente."""

    async def _run() -> None:
        lock = AsyncRWLock()
        active_readers_record: list[int] = []

        async def reader() -> None:
            await lock.acquire_read()
            try:
                active_readers_record.append(lock.active_readers)
                await asyncio.sleep(0.05)
            finally:
                await lock.release_read()

        start = time.time()
        await asyncio.gather(*(reader() for _ in range(5)))
        elapsed = time.time() - start

        assert max(active_readers_record) == 5
        assert elapsed < 0.2

    asyncio.run(_run())


def test_async_rw_lock_writer_exclusion() -> None:
    """Verifica que un escritor espere a que todos los lectores activos finalicen."""

    async def _run() -> None:
        lock = AsyncRWLock()
        events: list[str] = []

        async def reader() -> None:
            await lock.acquire_read()
            try:
                events.append("reader_start")
                await asyncio.sleep(0.05)
                events.append("reader_end")
            finally:
                await lock.release_read()

        async def writer() -> None:
            await asyncio.sleep(0.01)
            await lock.acquire_write()
            try:
                events.append("writer_start")
                await asyncio.sleep(0.05)
                events.append("writer_end")
            finally:
                await lock.release_write()

        await asyncio.gather(reader(), writer())
        assert events == ["reader_start", "reader_end", "writer_start", "writer_end"]

    asyncio.run(_run())


def test_global_ingest_lock_single_inflight(tmp_path: Path) -> None:
    """Verifica que solo 1 ingesta ocurra a la vez a nivel global entre proyectos."""

    async def _run() -> None:
        manager = ProjectLockManager()
        events: list[str] = []

        repo_a = tmp_path / "project_a"
        repo_b = tmp_path / "project_b"
        repo_a.mkdir(parents=True, exist_ok=True)
        repo_b.mkdir(parents=True, exist_ok=True)

        async def ingest(repo: Path, name: str) -> None:
            async with manager.acquire_global_ingest(repo):
                events.append(f"{name}_start")
                await asyncio.sleep(0.05)
                events.append(f"{name}_end")

        await asyncio.gather(ingest(repo_a, "ingest_A"), ingest(repo_b, "ingest_B"))

        assert events in [
            ["ingest_A_start", "ingest_A_end", "ingest_B_start", "ingest_B_end"],
            ["ingest_B_start", "ingest_B_end", "ingest_A_start", "ingest_A_end"],
        ]

    asyncio.run(_run())


def test_project_read_waits_for_project_ingest(tmp_path: Path) -> None:
    """Verifica que las lecturas en un proyecto esperen a que finalice su ingesta activa."""

    async def _run() -> None:
        manager = ProjectLockManager()
        events: list[str] = []
        repo = tmp_path / "project_a"
        repo.mkdir(parents=True, exist_ok=True)

        async def ingest() -> None:
            async with manager.acquire_global_ingest(repo):
                events.append("ingest_start")
                await asyncio.sleep(0.06)
                events.append("ingest_end")

        async def query() -> None:
            await asyncio.sleep(0.01)
            waited: list[str] = []

            def on_wait(msg: str) -> None:
                waited.append(msg)

            async with manager.acquire_read(repo, on_waiting=on_wait):
                events.append("query_start")
                events.append("query_end")

            assert len(waited) > 0

        await asyncio.gather(ingest(), query())
        assert events == ["ingest_start", "ingest_end", "query_start", "query_end"]

    asyncio.run(_run())


def test_read_waits_for_global_ingest_on_other_project(tmp_path: Path) -> None:
    """Verifica que lecturas en project_b esperen a que termine la ingesta en project_a."""

    async def _run() -> None:
        manager = ProjectLockManager()
        events: list[str] = []
        repo_a = tmp_path / "project_a"
        repo_b = tmp_path / "project_b"
        repo_a.mkdir(parents=True, exist_ok=True)
        repo_b.mkdir(parents=True, exist_ok=True)

        async def ingest_a() -> None:
            async with manager.acquire_global_ingest(repo_a):
                events.append("ingest_a_start")
                await asyncio.sleep(0.06)
                events.append("ingest_a_end")

        async def query_b() -> None:
            await asyncio.sleep(0.01)
            waited: list[str] = []

            def on_wait(msg: str) -> None:
                waited.append(msg)

            async with manager.acquire_read(repo_b, on_waiting=on_wait):
                events.append("query_b_start")
                events.append("query_b_end")

            assert len(waited) > 0

        await asyncio.gather(ingest_a(), query_b())
        assert events == ["ingest_a_start", "ingest_a_end", "query_b_start", "query_b_end"]

    asyncio.run(_run())


def test_global_deps_lock(tmp_path: Path) -> None:
    """Verifica el bloqueo de lectura y escritura para la base de datos de dependencias."""

    async def _run() -> None:
        manager = ProjectLockManager()
        events: list[str] = []

        async def reader() -> None:
            async with manager.acquire_deps("read"):
                events.append("read_deps_start")
                await asyncio.sleep(0.04)
                events.append("read_deps_end")

        async def writer() -> None:
            await asyncio.sleep(0.01)
            async with manager.acquire_deps("write"):
                events.append("write_deps_start")
                await asyncio.sleep(0.04)
                events.append("write_deps_end")

        await asyncio.gather(reader(), writer())
        assert events == [
            "read_deps_start",
            "read_deps_end",
            "write_deps_start",
            "write_deps_end",
        ]

    asyncio.run(_run())
