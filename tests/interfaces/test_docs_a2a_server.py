"""`DocsExecutor`'s own exception handling. A first fix addressed
`docs_agent.py` alone; a follow-up traced the call chain and found that
fix never reached `docs_node` — an uncaught `SilpoMcpAuthRequiredError`
crashed the request-handling task instead of producing the JSON error
payload `docs_client.py` knows how to read. This test exercises the real
chain
end to end over an in-process ASGI transport (no real socket), the same
technique `tests/infrastructure/test_a2a_transport.py` already uses.
"""

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from src.infrastructure.docs_client import DocsUnavailableError, call_docs_agent
from src.infrastructure.silpo_mcp_auth import SilpoMcpAuthRequiredError
from src.interfaces import docs_a2a_server


def _asgi_client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


def test_oauth_error_maps_to_distinct_a2a_error_payload(monkeypatch) -> None:
    async def raising_run_docs_agent(_query: str):
        raise SilpoMcpAuthRequiredError("no cached token, no automated login")

    monkeypatch.setattr(docs_a2a_server, "run_docs_agent", raising_run_docs_agent)
    # config/models.yaml's docs.port must resolve for build_app(); the
    # ASGI transport never actually dials it, but build_app() reads it.
    app = docs_a2a_server.build_app()

    with pytest.raises(DocsUnavailableError, match="SilpoMcpAuthRequiredError"):
        call_docs_agent(
            "Коли працює магазин?",
            request_id="r1",
            session_id="s1",
            trace_id="0123456789abcdef0123456789abcdef",
            deadline=datetime.now(timezone.utc) + timedelta(seconds=10),
            httpx_client=_asgi_client(app),
        )
