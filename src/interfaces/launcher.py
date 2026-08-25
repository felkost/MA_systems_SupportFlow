"""Starts every A2A-hosted agent as its own subprocess and prints a port
table with measured startup time and peak memory (docs/decisions.md #7 —
the multi-process topology's own instrument for "does this actually start
up light and fast", not just asserted).

Run manually:

    .venv/Scripts/python -m src.interfaces.launcher

Starts both Web Search Agent and Docs Agent (docs/decisions.md #20's two
waves both landed).
"""

import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx
import psutil

from src.kernel.settings import AgentRole, load_agent_config

_AGENT_ROLES: list[AgentRole] = ["web_search", "docs"]


@dataclass
class LaunchedAgent:
    role: str
    port: int
    pid: int
    startup_seconds: float
    peak_memory_mb: float
    process: subprocess.Popen


def _wait_for_ready(
    probe: Callable[[], bool], timeout: float = 20.0, poll_interval: float = 0.2
) -> None:
    """Poll `probe` until it returns `True` or `timeout` seconds pass.

    Parameters
    ----------
    probe : Callable[[], bool]
    timeout : float, default=20.0
    poll_interval : float, default=0.2

    Raises
    ------
    TimeoutError
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if probe():
            return
        time.sleep(poll_interval)
    raise TimeoutError(f"probe did not succeed within {timeout}s")


def _agent_card_reachable(port: int) -> bool:
    try:
        response = httpx.get(
            f"http://localhost:{port}/.well-known/agent-card.json", timeout=1.0
        )
        return response.status_code < 500
    except httpx.HTTPError:
        return False


def launch_agent(role: AgentRole) -> LaunchedAgent:
    """Start one agent's A2A server as a subprocess and wait for it to
    answer its own agent-card endpoint.

    Parameters
    ----------
    role : str
        Must have a `port` in `config/models.yaml` (docs/decisions.md #26).

    Returns
    -------
    LaunchedAgent
    """
    config = load_agent_config(role)
    if config.port is None:
        raise KeyError(f"config/models.yaml's '{role}' row has no 'port'")
    port: int = config.port

    started = time.monotonic()
    process = subprocess.Popen(
        [sys.executable, "-m", f"src.interfaces.{role}_a2a_server"]
    )
    _wait_for_ready(lambda: _agent_card_reachable(port))
    startup_seconds = time.monotonic() - started
    peak_memory_mb = psutil.Process(process.pid).memory_info().rss / (1024 * 1024)

    return LaunchedAgent(
        role=role,
        port=port,
        pid=process.pid,
        startup_seconds=round(startup_seconds, 2),
        peak_memory_mb=round(peak_memory_mb, 1),
        process=process,
    )


def _print_table(agents: list[LaunchedAgent]) -> None:
    print(f"{'agent':<14}{'port':<8}{'pid':<8}{'startup_s':<12}{'peak_mb':<10}")
    for agent in agents:
        print(
            f"{agent.role:<14}{agent.port:<8}{agent.pid:<8}"
            f"{agent.startup_seconds:<12}{agent.peak_memory_mb:<10}"
        )


def main() -> None:
    agents = [launch_agent(role) for role in _AGENT_ROLES]
    _print_table(agents)
    print("Press Ctrl+C to stop all agents.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        for agent in agents:
            agent.process.terminate()


if __name__ == "__main__":
    main()
