"""Starts every A2A-hosted agent as its own subprocess and prints a port
table with measured startup time and peak memory — the multi-process
topology's own instrument for "does this actually start up light and
fast", not just asserted.

Run manually:

    .venv/Scripts/python -m src.interfaces.launcher

Starts both Web Search Agent and Docs Agent.
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
    probe: Callable[[], bool], timeout: float = 180.0, poll_interval: float = 0.2
) -> None:
    """Poll `probe` until it returns `True` or `timeout` seconds pass.

    Parameters
    ----------
    probe : Callable[[], bool]
    timeout : float, default=180.0
        Callers pass a per-role budget from `_startup_timeout`, which is
        where the reasoning for each number lives; this default only
        applies to a direct call. The history behind sizing it at all:
        20.0s (the original) and 60.0s both proved too tight under real
        machine load, and 180.0s was then exceeded twice by `docs`, whose
        port cannot answer until its retriever is built.
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


def _startup_timeout(role: AgentRole) -> float:
    """How long this role is allowed to take before its port answers.

    Per-role rather than one shared number, because the two agents have
    genuinely different startup work. `web_search` opens its port
    immediately. `docs_a2a_server` calls `warm_up_retriever()` *before*
    `uvicorn.run()` on purpose — the port must not answer until the
    retriever exists — so its probe cannot succeed until the embedding
    model is loaded and the index built.

    The 180s that covered the measured ~72s build was exceeded twice on
    real runs (2026-08-27, 2026-08-29). The second time the model weights
    were being fetched from the HF Hub unauthenticated, which the Hub
    itself warns is rate-limited; the agent finished and bound its port
    correctly, just past the deadline. Sizing this to the slow path costs
    nothing on a warm cache — the probe returns as soon as the port
    answers, so a fast start still returns fast.
    """
    return 420.0 if role == "docs" else 120.0


def _agent_card_reachable(port: int) -> bool:
    try:
        response = httpx.get(
            f"http://localhost:{port}/.well-known/agent-card.json", timeout=1.0
        )
        return response.status_code < 500
    except httpx.HTTPError:
        return False


def launch_agent(
    role: AgentRole, started_processes: list[subprocess.Popen] | None = None
) -> LaunchedAgent:
    """Start one agent's A2A server as a subprocess and wait for it to
    answer its own agent-card endpoint.

    Parameters
    ----------
    role : str
        Must have a `port` in `config/models.yaml`.
    started_processes : list of subprocess.Popen, optional
        Registry the caller owns, appended to the instant the subprocess
        exists — before the readiness probe, not after it. Without this
        the handle lives only in this frame, so an agent whose probe
        times out is the one process the caller can never clean up:
        `main`'s handler iterates the `LaunchedAgent`s it collected, and
        a timed-out agent never became one. Observed live 2026-08-29 — a
        `docs_a2a_server` orphan held port 8801 after the launcher had
        already exited, which then blocked the next launch attempt.

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
    if started_processes is not None:
        started_processes.append(process)
    _wait_for_ready(lambda: _agent_card_reachable(port), timeout=_startup_timeout(role))
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


def _terminate_all(processes: list[subprocess.Popen]) -> None:
    """Stop every subprocess and wait for the OS to actually reap it.

    The `wait` is the point: `terminate()` only requests the stop, so
    returning straight after it can hand the next launcher run a port
    that is still held. Each failure is swallowed per process — one
    already-dead child must not prevent the others from being cleaned up,
    which is the whole reason this runs.
    """
    for process in processes:
        try:
            process.terminate()
        except OSError:
            continue
    for process in processes:
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
        except OSError:
            continue


def main() -> None:
    # Every subprocess is registered the moment it is spawned, not once it
    # is ready. Live-confirmed twice: 2026-08-26 an earlier *successful*
    # agent was orphaned when a later one's probe raised, and 2026-08-29
    # the agent whose own probe timed out was orphaned — it kept starting,
    # bound port 8801 after the launcher had already exited, and blocked
    # the next attempt. The first was fixed by cleaning up collected
    # agents; only tracking raw processes fixes the second, because a
    # timed-out agent never becomes a `LaunchedAgent` at all.
    processes: list[subprocess.Popen] = []
    agents: list[LaunchedAgent] = []
    try:
        for role in _AGENT_ROLES:
            agents.append(launch_agent(role, processes))
    except BaseException:
        # BaseException, not Exception: Ctrl+C during a slow startup is
        # KeyboardInterrupt, and that is exactly when a half-started
        # agent most needs cleaning up.
        _terminate_all(processes)
        raise
    _print_table(agents)
    print("Press Ctrl+C to stop all agents.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        _terminate_all(processes)


if __name__ == "__main__":
    main()
