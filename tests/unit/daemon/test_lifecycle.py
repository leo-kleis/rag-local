import asyncio
import time
from unittest.mock import AsyncMock, patch

from rag_local.daemon.lifecycle import LifecycleManager


def test_lifecycle_initial_state():
    mgr = LifecycleManager(parent_pid=1234, idle_timeout=60.0, grace_period=10.0)
    assert mgr.parent_pid == 1234
    assert mgr.idle_timeout == 60.0
    assert mgr.grace_period == 10.0
    assert mgr.get_uptime_s() >= 0.0
    assert mgr.get_idle_s() >= 0.0
    assert not mgr.is_in_grace_period()


def test_lifecycle_claim():
    mgr = LifecycleManager(parent_pid=1234)
    with patch("psutil.pid_exists", return_value=True):
        claimed = mgr.claim(5678)
        assert claimed is True
        assert mgr.parent_pid == 5678
        assert not mgr.is_in_grace_period()

    with patch("psutil.pid_exists", return_value=False):
        failed_claim = mgr.claim(99999)
        assert failed_claim is False
        assert mgr.parent_pid == 5678


def test_lifecycle_record_activity():
    mgr = LifecycleManager()
    time.sleep(0.01)
    prev = mgr.last_activity
    mgr.record_activity()
    assert mgr.last_activity > prev


def test_lifecycle_grace_period_and_shutdown():
    shutdown_mock = AsyncMock()
    mgr = LifecycleManager(
        parent_pid=1234,
        grace_period=0.05,
        idle_timeout=100.0,
        check_interval=0.02,
    )

    # Simular que el padre muere
    with patch("psutil.pid_exists", return_value=False):

        async def _test():
            mgr.start(shutdown_mock)
            await asyncio.sleep(0.2)
            mgr.stop()

        asyncio.run(_test())
        assert shutdown_mock.called


def test_lifecycle_idle_timeout_shutdown():
    shutdown_mock = AsyncMock()
    mgr = LifecycleManager(
        parent_pid=None,
        idle_timeout=0.05,
        check_interval=0.02,
    )

    async def _test():
        mgr.start(shutdown_mock)
        await asyncio.sleep(0.2)
        mgr.stop()

    asyncio.run(_test())
    assert shutdown_mock.called
