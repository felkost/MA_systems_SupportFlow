"""`_wait_for_ready`'s polling logic — the one piece of
`src.interfaces.launcher` testable without actually spawning an OS
process. Spawning and measuring a real subprocess is exercised manually,
the same way `scripts/probe_silpo_mcp.py` and `scripts/run_router_gate.py`
are.
"""

import pytest

from src.interfaces.launcher import _wait_for_ready


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
