"""Manual, live smoke check for Stage 4 Wave A: one full customer request
through Supervisor -> Router (in-process) -> Docs Agent (A2A hop, a
second OS process) -> model -> tool, with real Langfuse tracing, then a
read-back via Langfuse's own REST API confirming task §9's readiness
criterion (narrowed to Wave A's own scope, docs/decisions.md Stage 4
decision 38): exactly one trace for this request, spanning both
processes, with no orphaned records and a final answer.

`pytest --cov=src` never makes a live Langfuse/OpenRouter/Silpo MCP call —
this script is the counterpart. Requires:

- `TRACING_ENABLED=true` and real `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY`
  in `.env`.
- Docs Agent's A2A server actually running (`python -m src.interfaces.launcher`
  in a separate terminal, or just `python -m src.interfaces.docs_a2a_server`).
- Langfuse ingestion is asynchronous — `flush()` only guarantees delivery
  to the API, not read visibility. `api.trace.get(trace_id)` can raise
  `NotFoundError` for up to ~30s after flush (confirmed from the
  installed SDK's own `Langfuse.api` property docstring). This script
  retries with a deadline instead of a fixed sleep.

    TRACING_ENABLED=true .venv/Scripts/python scripts/observability_smoke.py

A second run, started with the Docs Agent process killed first, verifies
decision 39's actual point: a connection failure still produces a
client-side error span rather than no record at all.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402
from langfuse import Langfuse  # noqa: E402
from langfuse.api import NotFoundError  # noqa: E402
from langfuse.api import TraceWithFullDetails  # noqa: E402

from src.application.supervisor import handle_request  # noqa: E402
from src.infrastructure.observability import (  # noqa: E402
    flush_and_shutdown,
    get_langfuse_client,
    new_trace_id,
)
from src.kernel.settings import settings  # noqa: E402

_READBACK_TIMEOUT_SECONDS = 45.0
_READBACK_POLL_SECONDS = 3.0


def _wait_for_trace(client: Langfuse, trace_id: str) -> TraceWithFullDetails:
    """Retries on both "not indexed yet" (`NotFoundError`) and a transient
    network hiccup (`httpx.TimeoutException`/`ConnectError`) — confirmed
    live 2026-08-26 that the latter is a real, recurring failure mode of
    this exact call, not hypothetical, and previously crashed the whole
    script with an unhandled traceback instead of retrying within the
    same deadline budget.
    """
    deadline = time.monotonic() + _READBACK_TIMEOUT_SECONDS
    while True:
        try:
            return client.api.trace.get(trace_id)
        except (NotFoundError, httpx.TimeoutException, httpx.ConnectError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(_READBACK_POLL_SECONDS)


def main() -> None:
    if not settings.tracing_enabled:
        print("TRACING_ENABLED is not set — nothing to verify. Aborting.")
        return

    request_id = f"obs-smoke-{int(time.time())}"
    session_id = "obs-smoke"
    trace_id = new_trace_id()

    result = handle_request(
        "Чи є у вас безлактозне молоко?", request_id, session_id, trace_id
    )
    print("NEXT_ACTION:", result["next_action"])
    print("ANSWER:", result.get("answer"))
    print("TRACE_ID:", trace_id)

    client = get_langfuse_client()
    if client is None:
        print("No Langfuse client — cannot read back the trace.")
        return

    client.flush()  # delivery guarantee only, not read visibility
    print(f"Waiting up to {_READBACK_TIMEOUT_SECONDS:.0f}s for Langfuse ingestion...")
    try:
        trace = _wait_for_trace(client, trace_id)
    except NotFoundError:
        print(
            f"FAIL: trace {trace_id} still not readable after "
            f"{_READBACK_TIMEOUT_SECONDS:.0f}s — check LANGFUSE_BASE_URL/keys "
            "and that Docs Agent's A2A server is actually running."
        )
        flush_and_shutdown()
        return

    observation_count = len(trace.observations)
    print(f"OBSERVATIONS_IN_TRACE: {observation_count}")
    print(f"TRACE_URL: {settings.langfuse_base_url}/trace/{trace_id}")
    print(
        "Manually verify at that URL: one connected tree spanning both the "
        "Supervisor process and the Docs Agent A2A server process, no "
        "orphaned records, a final answer present, and no raw PII/tokens "
        "in any span's metadata."
    )
    flush_and_shutdown()


if __name__ == "__main__":
    main()
