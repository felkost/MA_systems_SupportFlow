"""Web Search Agent's A2A server entrypoint (docs/decisions.md #1). Hosts
`src.application.web_search_agent.run_web_search` behind the A2A protocol
so `application/supervisor.py` reaches it only over the network, never by
direct import (the layer table's single most load-bearing rule,
`tests/test_layering.py`).

Run standalone for manual testing:

    .venv/Scripts/python -m src.interfaces.web_search_a2a_server
"""

import json
import uuid
from contextlib import nullcontext
from typing import Any

import uvicorn
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types.a2a_pb2 import Message, Part, Role
from fastapi import FastAPI
from langfuse.types import TraceContext

from src.application.web_search_agent import WebSearchInvalidOutputError, run_web_search
from src.infrastructure.a2a_transport import (
    build_agent_card,
    build_server_app,
    read_request_metadata,
    read_request_text,
)
from src.infrastructure.observability import configure_tracing, get_langfuse_client
from src.infrastructure.web_search import SearchUnavailableError
from src.kernel.settings import load_agent_config


class WebSearchExecutor(AgentExecutor):
    """Bridges one A2A request to one `run_web_search` call.

    A tool-unavailable or invalid-model-output failure is reported back as
    a JSON error payload in the reply text, not as a transport-level
    error — `src.infrastructure.web_search_client.call_web_search` (the
    caller) reads it and decides the escalation path (task §7 step 6);
    keeping the failure inside the A2A message contract avoids depending
    on `a2a-sdk`'s own exception-to-JSON-RPC-error mapping, which this
    project's SDK probe did not verify (docs/decisions.md #23).
    """

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        query = read_request_text(context)
        metadata = read_request_metadata(context)
        client = get_langfuse_client()
        trace_id = metadata.get("trace_id")
        span_cm: Any = nullcontext()
        if client is not None and trace_id:
            trace_context: TraceContext = {"trace_id": trace_id}
            parent_span_id = metadata.get("parent_span_id")
            if parent_span_id:
                trace_context["parent_span_id"] = parent_span_id
            span_cm = client.start_as_current_observation(
                name="web_search_agent.a2a_request",
                as_type="span",
                trace_context=trace_context,
            )
        with span_cm:
            try:
                result = run_web_search(query)
                reply_text = json.dumps(
                    {
                        "response": json.loads(result.response.model_dump_json()),
                        "retrieval_context": result.retrieval_context,
                    }
                )
            except (SearchUnavailableError, WebSearchInvalidOutputError) as exc:
                reply_text = json.dumps(
                    {"error_type": type(exc).__name__, "error": str(exc)}
                )
        await event_queue.enqueue_event(
            Message(
                role=Role.ROLE_AGENT,
                message_id=str(uuid.uuid4()),
                parts=[Part(text=reply_text)],
            )
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError("Web Search Agent tasks are not cancellable")


def build_app() -> FastAPI:
    """Assemble the FastAPI app for this process — split from `main()` so
    tests can mount it without starting uvicorn.
    """
    config = load_agent_config("web_search")
    if config.port is None:
        raise KeyError("config/models.yaml's 'web_search' row has no 'port'")
    agent_card = build_agent_card(
        name="SupportFlow Web Search Agent",
        description="Tavily-primary, DuckDuckGo-fallback web search for current info.",
        url=f"http://localhost:{config.port}",
    )
    return build_server_app(WebSearchExecutor(), agent_card)


def main() -> None:
    # Eagerly configured here, not in launcher.py — same second-round
    # correction as `src.interfaces.docs_a2a_server.main`.
    configure_tracing()
    config = load_agent_config("web_search")
    if config.port is None:
        raise KeyError("config/models.yaml's 'web_search' row has no 'port'")
    uvicorn.run(build_app(), host="127.0.0.1", port=config.port)


if __name__ == "__main__":
    main()
