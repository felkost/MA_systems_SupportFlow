"""Web Search Agent's tool client: Tavily primary, `ddgs` (DuckDuckGo)
fallback (task §3/§13). `search()`'s fallback control flow takes injected
provider callables so it is testable without any network call — the real
`_tavily_search`/`_ddgs_search` bodies are exercised only by the manual
smoke path (docs/decisions.md #21), never by `pytest --cov=src`.

Confirmed against the installed `tavily-python==0.8.0` and `ddgs==9.15.0`
packages (`TavilyClient.search`, `DDGS.text`), not assumed from docs —
`insights.md`, 2026-08-25.
"""

from collections.abc import Callable

from pydantic import BaseModel

from src.kernel.settings import settings


class SearchResult(BaseModel):
    """One web search hit, provider-agnostic.

    Parameters
    ----------
    title : str
    url : str
    content : str
        The snippet/summary text the provider returned — this becomes one
        entry of `SupportFlowState.retrieval_context` (docs/decisions.md #22).
    """

    title: str
    url: str
    content: str


class SearchUnavailableError(Exception):
    """Both Tavily and `ddgs` failed — task §7 step 6's "unavailable tool"
    escalation trigger.
    """


SearchFn = Callable[[str], list[SearchResult]]


def _tavily_search(query: str) -> list[SearchResult]:
    # Lazy import per docs/decisions.md #7's "heavy import inside the
    # function that uses it" discipline; no py.typed stubs upstream.
    from tavily import TavilyClient  # type: ignore[import-untyped]

    client = TavilyClient(api_key=settings.tavily_api_key)
    response = client.search(query, max_results=5)
    return [
        SearchResult(
            title=item.get("title", ""),
            url=item.get("url", ""),
            content=item.get("content", ""),
        )
        for item in response.get("results", [])
    ]


def _ddgs_search(query: str) -> list[SearchResult]:
    from ddgs import DDGS  # lazy per docs/decisions.md #7

    with DDGS() as ddgs:
        hits = ddgs.text(query, max_results=5)
    return [
        SearchResult(
            title=hit.get("title", ""),
            url=hit.get("href", ""),
            content=hit.get("body", ""),
        )
        for hit in hits
    ]


def search(
    query: str,
    *,
    tavily_fn: SearchFn | None = None,
    ddgs_fn: SearchFn | None = None,
) -> list[SearchResult]:
    """Tavily first; `ddgs` only if Tavily raises.

    Parameters
    ----------
    query : str
        Already masked/PII-stripped text (task §9) — this function has no
        opinion on masking, it only searches.
    tavily_fn, ddgs_fn : SearchFn, optional
        Injected for testing; default to the real providers.

    Returns
    -------
    list[SearchResult]

    Raises
    ------
    SearchUnavailableError
        Both providers failed.
    """
    tavily_fn = tavily_fn or _tavily_search
    ddgs_fn = ddgs_fn or _ddgs_search
    try:
        return tavily_fn(query)
    except Exception as tavily_exc:  # noqa: BLE001 — provider errors vary
        try:
            return ddgs_fn(query)
        except Exception as ddgs_exc:  # noqa: BLE001
            raise SearchUnavailableError(
                f"Tavily failed ({tavily_exc}); ddgs fallback failed ({ddgs_exc})"
            ) from ddgs_exc
