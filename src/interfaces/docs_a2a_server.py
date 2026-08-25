"""Docs Agent's A2A server entrypoint (docs/decisions.md #1). Hosts
`src.application.docs_agent.run_docs_agent` behind the A2A protocol —
`application/supervisor.py` reaches it only over the network, never by
direct import (`tests/test_layering.py`).

Run standalone for manual testing:

    .venv/Scripts/python -m src.interfaces.docs_a2a_server
"""

import json
import uuid

import uvicorn
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types.a2a_pb2 import Message, Part, Role
from fastapi import FastAPI

from src.application.docs_agent import DocsInvalidOutputError, run_docs_agent
from src.infrastructure.a2a_transport import (
    build_agent_card,
    build_server_app,
    read_request_text,
)
from src.kernel.settings import load_agent_config


class DocsExecutor(AgentExecutor):
    """Bridges one A2A request to one `run_docs_agent` call.

    A failure is reported back as a JSON error payload in the reply text,
    not as a transport-level error — same contract as
    `src.interfaces.web_search_a2a_server.WebSearchExecutor`
    (docs/decisions.md #23).
    """

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        query = read_request_text(context)
        try:
            result = await run_docs_agent(query)
            reply_text = json.dumps(
                {
                    "response": json.loads(result.response.model_dump_json()),
                    "retrieval_context": result.retrieval_context,
                }
            )
        except DocsInvalidOutputError as exc:
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
    config = load_agent_config("docs")
    if config.port is None:
        raise KeyError("config/models.yaml's 'docs' row has no 'port'")
    uvicorn.run(build_app(), host="127.0.0.1", port=config.port)


if __name__ == "__main__":
    main()
