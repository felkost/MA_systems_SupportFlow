"""`run_docs_agent`'s retrieval/composition wiring, fully independent of
any live LLM or Langfuse call (retriever, search_products, translate_fn,
compose_fn, prompt_fn are all injected) — proves the KB+MCP combination
and source/retrieval_context assembly, not model behavior.
"""

from datetime import datetime, timezone

import pytest

from src.application.docs_agent import run_docs_agent
from src.domain.schemas import DocsResponse


class _FakeDocument:
    def __init__(self, text: str, metadata: dict) -> None:
        self.page_content = text
        self.metadata = metadata


class _FakeRetriever:
    def __init__(self, docs: list[_FakeDocument]) -> None:
        self._docs = docs

    def invoke(self, _query: str) -> list[_FakeDocument]:
        return self._docs


def _fake_prompt_fn(_name: str) -> tuple[str, int]:
    return (
        "<customer_message>{{customer_message}}</customer_message>\n"
        "<retrieved_content>{{retrieved_content}}</retrieved_content>",
        1,
    )


@pytest.mark.asyncio
async def test_run_docs_agent_combines_kb_and_mcp_sources() -> None:
    kb_doc = _FakeDocument(
        "Бонусна картка діє без терміну.",
        {
            "id": "faq-02",
            "source": "internal_policy/loyalty_v5.md",
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "version": "5.0",
        },
    )

    async def fake_search_products(_query: str) -> list[dict]:
        return [{"id": "p1", "name": "Молоко безлактозне", "price": 45.5}]

    def fake_compose(_compiled_prompt: str) -> DocsResponse:
        return DocsResponse(answer="Відповідь", sources=[], confidence=0.9)

    result = await run_docs_agent(
        "Чи є безлактозне молоко?",
        retriever=_FakeRetriever([kb_doc]),
        search_products=fake_search_products,
        translate_fn=lambda _q: "безлактозне молоко",
        compose_fn=fake_compose,
        prompt_fn=_fake_prompt_fn,
    )

    assert result.response.answer == "Відповідь"
    assert len(result.response.sources) == 2  # 1 KB + 1 MCP
    assert result.retrieval_context == [
        "Бонусна картка діє без терміну.",
        "Молоко безлактозне: 45.5 грн",
    ]


@pytest.mark.asyncio
async def test_run_docs_agent_degrades_gracefully_when_mcp_search_fails() -> None:
    kb_doc = _FakeDocument(
        "Графік роботи в застосунку.",
        {
            "id": "faq-10",
            "source": "internal_policy/stores_v1.md",
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "version": "1.2",
        },
    )

    async def failing_search_products(_query: str) -> list[dict]:
        raise RuntimeError("Silpo MCP unavailable")

    def fake_compose(_compiled_prompt: str) -> DocsResponse:
        return DocsResponse(answer="Відповідь з бази знань", sources=[], confidence=0.6)

    result = await run_docs_agent(
        "Коли працює магазин?",
        retriever=_FakeRetriever([kb_doc]),
        search_products=failing_search_products,
        translate_fn=lambda _q: "магазин",
        compose_fn=fake_compose,
        prompt_fn=_fake_prompt_fn,
    )

    assert len(result.response.sources) == 1  # KB only — MCP failure degraded silently
    assert result.retrieval_context == ["Графік роботи в застосунку."]
