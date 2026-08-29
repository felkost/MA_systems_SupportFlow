"""Docs Agent's A2A server entrypoint. Hosts
`src.application.docs_agent.run_docs_agent` behind the A2A protocol —
`application/supervisor.py` reaches it only over the network, never by
direct import (`tests/test_layering.py`).

Run standalone for manual testing:

    .venv/Scripts/python -m src.interfaces.docs_a2a_server
"""

import json
import logging
import uuid
from contextlib import nullcontext
from typing import Any

import uvicorn
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types.a2a_pb2 import Message, Part, Role
from fastapi import FastAPI
from langfuse.types import TraceContext

from src.application.docs_agent import (
    DocsInvalidOutputError,
    run_docs_agent,
    warm_up_retriever,
)
from src.infrastructure.silpo_mcp_auth import SilpoMcpAuthRequiredError
from src.infrastructure.a2a_transport import (
    build_agent_card,
    build_server_app,
    read_request_metadata,
    read_request_text,
)
from src.infrastructure.observability import configure_tracing, get_langfuse_client
from src.kernel.settings import load_agent_config

# The caller only ever sees the error payload's type tag; without
# this the provider/tool text that explains the failure is lost.
logger = logging.getLogger(__name__)


class DocsExecutor(AgentExecutor):
    """Bridges one A2A request to one `run_docs_agent` call.

    A failure is reported back as a JSON error payload in the reply text,
    not as a transport-level error — same contract as
    `src.interfaces.web_search_a2a_server.WebSearchExecutor`.
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
                name="docs_agent.a2a_request",
                as_type="span",
                trace_context=trace_context,
            )
        with span_cm:
            try:
                result = await run_docs_agent(
                    query,
                    conversation_history=metadata.get("conversation_history", ""),
                )
                reply_text = json.dumps(
                    {
                        "response": json.loads(result.response.model_dump_json()),
                        "retrieval_context": result.retrieval_context,
                        "tools_called": result.tools_called,
                        "prompt_version": result.prompt_version,
                    }
                )
            except (DocsInvalidOutputError, SilpoMcpAuthRequiredError) as exc:
                # An uncaught SilpoMcpAuthRequiredError here crashes the
                # request-handling task instead of reaching docs_client.py
                # as a readable error.
                logger.exception("%s failed: %s", type(exc).__name__, exc)
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
        raise NotImplementedError("Docs Agent tasks are not cancellable")


def build_app() -> FastAPI:
    """Assemble the FastAPI app for this process — split from `main()` so
    tests can mount it without starting uvicorn.
    """
    config = load_agent_config("docs")
    if config.port is None:
        raise KeyError("config/models.yaml's 'docs' row has no 'port'")
    agent_card = build_agent_card(
        name="SupportFlow Docs Agent",
        description="RAG over the internal knowledge base plus read-only Silpo MCP.",
        url=f"http://localhost:{config.port}",
    )
    return build_server_app(DocsExecutor(), agent_card)


def main() -> None:
    # Eagerly configured here, not in launcher.py — launcher.py spawns
    # this module as a real subprocess (`subprocess.Popen`), so a call in
    # launcher.py's own body would never reach this process.
    configure_tracing()
    config = load_agent_config("docs")
    if config.port is None:
        raise KeyError("config/models.yaml's 'docs' row has no 'port'")
    # Before the port opens, not after: a request that arrives mid-warm-up
    # would still pay the build cost and time out. `launcher.py`'s probe
    # deadline is sized for this.
    warm_up_retriever()
    uvicorn.run(build_app(), host="127.0.0.1", port=config.port)


if __name__ == "__main__":
    main()
