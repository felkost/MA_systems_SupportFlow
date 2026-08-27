"""Cross-check that `tools_called` actually matches what Langfuse's own
`mcp.tool.<name>` spans recorded for the same request — printed side by
side for one live case so the author can compare by eye. Mirrors
`observability_smoke.py`'s own pattern (live read-back with a retry
deadline, since Langfuse ingestion is asynchronous).

    TRACING_ENABLED=true .venv/Scripts/python scripts/golden_dataset_smoke.py

Go/no-go: PRINTED_TOOLS_CALLED and the `mcp.tool.*` span names
found in the trace name the same tool(s), for one product-search case.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langfuse.api import NotFoundError  # noqa: E402

from src.application.supervisor import handle_request  # noqa: E402
from src.infrastructure.observability import (  # noqa: E402
    flush_and_shutdown,
    get_langfuse_client,
    new_trace_id,
)
from src.kernel.settings import settings  # noqa: E402
from tests.evaluation.harness import _bypass_hitl  # noqa: E402

_READBACK_TIMEOUT_SECONDS = 45.0
_READBACK_POLL_SECONDS = 3.0


def main() -> None:
    if not settings.tracing_enabled:
        print("TRACING_ENABLED is not set — nothing to verify. Aborting.")
        return

    request_id = f"golden-smoke-{int(time.time())}"
    session_id = "golden-smoke"
    trace_id = new_trace_id()

    with _bypass_hitl():
        result = handle_request(
            "Скільки коштує хліб бородинський?", request_id, session_id, trace_id
        )
    print("NEXT_ACTION:", result["next_action"])
    print("PRINTED_TOOLS_CALLED:", result.get("tools_called"))
    print("TRACE_ID:", trace_id)

    client = get_langfuse_client()
    if client is None:
        print("No Langfuse client — cannot read back the trace.")
        return

    client.flush()
    deadline = time.monotonic() + _READBACK_TIMEOUT_SECONDS
    trace = None
    while time.monotonic() < deadline:
        try:
            trace = client.api.trace.get(trace_id)
            break
        except NotFoundError:
            time.sleep(_READBACK_POLL_SECONDS)

    if trace is None:
        print(f"FAIL: trace {trace_id} still not readable — check Langfuse keys/URL.")
        flush_and_shutdown()
        return

    mcp_tool_spans = [
        (obs.metadata or {}).get("tool")
        for obs in trace.observations
        if obs.name == "silpo_mcp.call_tool"
    ]
    print("MCP_TOOL_SPANS_IN_TRACE:", mcp_tool_spans)
    print(f"TRACE_URL: {settings.langfuse_base_url}/trace/{trace_id}")
    print(
        "Manually compare PRINTED_TOOLS_CALLED against MCP_TOOL_SPANS_IN_TRACE — "
        "they should name the same tool(s)."
    )
    flush_and_shutdown()


if __name__ == "__main__":
    main()
