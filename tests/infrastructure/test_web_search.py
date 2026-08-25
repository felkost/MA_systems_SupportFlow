"""Web Search Agent's tool client: Tavily primary, `ddgs` fallback
(task §3/§13). The fallback control flow is tested via dependency
injection so it needs no real Tavily/DuckDuckGo call and no network —
`_tavily_search`/`_ddgs_search` (the real provider calls) are exercised
only by the manual smoke path (docs/decisions.md #21), not here.
"""

import pytest

from src.infrastructure.web_search import SearchResult, SearchUnavailableError, search


def test_search_uses_tavily_result_without_calling_ddgs_fallback() -> None:
    tavily_result = [SearchResult(title="t", url="https://example.com", content="c")]
    ddgs_calls: list[str] = []

    result = search(
        "query",
        tavily_fn=lambda q: tavily_result,
        ddgs_fn=lambda q: ddgs_calls.append(q) or [],
    )

    assert result == tavily_result
    assert ddgs_calls == []


def test_search_falls_back_to_ddgs_when_tavily_raises() -> None:
    ddgs_result = [SearchResult(title="d", url="https://example.com", content="c")]

    def failing_tavily(_query: str) -> list[SearchResult]:
        raise RuntimeError("tavily quota exceeded")

    result = search("query", tavily_fn=failing_tavily, ddgs_fn=lambda q: ddgs_result)

    assert result == ddgs_result


def test_search_raises_when_both_providers_fail() -> None:
    def failing(_query: str) -> list[SearchResult]:
        raise RuntimeError("unavailable")

    with pytest.raises(SearchUnavailableError):
        search("query", tavily_fn=failing, ddgs_fn=failing)
