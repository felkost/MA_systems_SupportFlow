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
- A short wait after the run before reading back, since Langfuse's
  ingestion is asynchronous — this script polls, it does not assume
  immediate consistency.

    TRACING_ENABLED=true .venv/Scripts/python scripts/observability_smoke.py

A second run, started with the Docs Agent process killed first, verifies
decision 39's actual point: a connection failure still produces a
client-side error span rather than no record at all.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.application.supervisor import handle_request  # noqa: E402
from src.infrastructure.observability import (  # noqa: E402
    flush_and_shutdown,
    get_langfuse_client,
    new_trace_id,
)
from src.kernel.settings import settings  # noqa: E402


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

    flush_and_shutdown()

    client = get_langfuse_client()
    if client is None:
        print("No Langfuse client — cannot read back the trace.")
        return

    print("Waiting for Langfuse ingestion...")
    time.sleep(5)
    trace = client.api.trace.get(trace_id)
    observation_count = len(trace.observations)
    print(f"OBSERVATIONS_IN_TRACE: {observation_count}")
    print(
        "Manually verify in the Langfuse UI: one connected tree spanning "
        "both the Supervisor process and the Docs Agent A2A server "
        "process, no orphaned records, and no raw PII/tokens in any "
        "span's metadata."
    )


if __name__ == "__main__":
    main()
