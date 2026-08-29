"""`src.interfaces.launcher`'s polling, per-role startup budget, and
subprocess cleanup — everything testable without spawning a real OS
process. Actually spawning and measuring one is exercised manually, the
same way `scripts/probe_silpo_mcp.py` and `scripts/run_router_gate.py`
are.
"""

import subprocess

import pytest

from src.interfaces import launcher
from src.interfaces.launcher import (
    _startup_timeout,
    _terminate_all,
    _wait_for_ready,
    launch_agent,
)


class _FakeProcess:
    """Stands in for `subprocess.Popen` — records what was asked of it."""

    def __init__(self, pid: int = 1234, wait_raises: bool = False) -> None:
        self.pid = pid
        self.terminated = False
        self.killed = False
        self.waited = False
        self._wait_raises = wait_raises

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout: float | None = None) -> int:
        self.waited = True
        if self._wait_raises:
            raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout or 0)
        return 0


def test_wait_for_ready_returns_once_probe_succeeds() -> None:
    calls = {"n": 0}

    def probe() -> bool:
        calls["n"] += 1
        return calls["n"] >= 3

    _wait_for_ready(probe, timeout=1.0, poll_interval=0.01)

    assert calls["n"] == 3


def test_wait_for_ready_raises_timeout_when_probe_never_succeeds() -> None:
    with pytest.raises(TimeoutError):
        _wait_for_ready(lambda: False, timeout=0.05, poll_interval=0.01)


def test_docs_gets_a_larger_startup_budget_than_web_search() -> None:
    # docs_a2a_server builds its retriever before opening its port; the
    # web_search server opens immediately. One shared number cannot fit both.
    assert _startup_timeout("docs") > _startup_timeout("web_search")


def test_a_timed_out_agent_is_still_registered_for_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 2026-08-29 orphan: `docs_a2a_server` kept starting after its
    probe timed out, bound port 8801 once the launcher had already exited,
    and blocked the next launch. The process handle has to reach the
    caller's registry *before* the probe can raise, or there is nothing
    left to terminate.
    """
    fake = _FakeProcess()
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda *a, **kw: fake)

    def never_ready(*_a: object, **_kw: object) -> None:
        raise TimeoutError("probe did not succeed")

    monkeypatch.setattr(launcher, "_wait_for_ready", never_ready)

    started: list[object] = []
    with pytest.raises(TimeoutError):
        launch_agent("docs", started)

    assert started == [fake], "the timed-out agent must still be cleanable"


def test_terminate_all_stops_and_reaps_every_process() -> None:
    processes = [_FakeProcess(pid=1), _FakeProcess(pid=2)]

    _terminate_all(processes)  # type: ignore[arg-type]

    assert all(p.terminated for p in processes)
    # The wait is what makes the port actually free by the time this
    # returns — terminate() alone only asks.
    assert all(p.waited for p in processes)


def test_terminate_all_kills_a_process_that_ignores_terminate() -> None:
    stubborn = _FakeProcess(wait_raises=True)

    _terminate_all([stubborn])  # type: ignore[arg-type]

    assert stubborn.killed


def test_terminate_all_continues_past_an_already_dead_process() -> None:
    class _Dead(_FakeProcess):
        def terminate(self) -> None:
            raise OSError("already gone")

    dead, alive = _Dead(), _FakeProcess()

    _terminate_all([dead, alive])  # type: ignore[arg-type]

    assert alive.terminated, "one dead child must not block cleaning up the rest"
