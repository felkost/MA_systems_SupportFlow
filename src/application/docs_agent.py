"""Docs Agent orchestration: retrieve knowledge-base chunks (always) and
Silpo catalogue results (deterministic heuristic — always attempt one
`search_products` call with an LLM-translated Ukrainian term, docs/decisions.md
#6/#27, never an LLM-driven tool-selection loop), fence both as untrusted
data (docs/decisions.md #24 — F3), then compose a `DocsResponse`.

`async def` throughout: `search_products` is natively async (a real MCP
session per call), and this module runs inside the Docs A2A server
process's already-running event loop (`DocsExecutor.execute()`) — nesting
`asyncio.run()` there would raise. The LLM calls inside stay synchronous
(`ChatOpenAI.invoke`), blocking the event loop for their duration; the
same trade-off Web Search Agent already accepts (docs/decisions.md #20)
and not revisited here.
"""

from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel

from src.domain.schemas import DocsResponse, Source
from src.infrastructure.llm import get_chat_model
from src.infrastructure.observability import get_langfuse_client
from src.infrastructure.prompts import get_prompt, get_prompt_client
from src.infrastructure.retriever import build_retriever, load_knowledge_base
from src.infrastructure.silpo_mcp import search_products as _default_search_products
from src.infrastructure.silpo_mcp_auth import SilpoMcpAuthRequiredError


class DocsInvalidOutputError(Exception):
    """The model call succeeded but did not produce a valid
    `DocsResponse` — refusal, prose, or an out-of-schema value.
    """


@dataclass(frozen=True)
class DocsAgentResult:
    """Parameters
    ----------
    response : DocsResponse
        The mandatory schema (task §6).
    retrieval_context : list[str]
        KB chunk texts plus a text rendering of any Silpo product results
        actually retrieved — populates `SupportFlowState.retrieval_context`
        (docs/decisions.md #22) across the A2A hop, the same pattern Web
        Search Agent already uses.
    tools_called : list[str]
        Silpo MCP tool names actually invoked, in call order (Stage 4
        Wave B decision D-B7) — `[]` when the MCP call was never attempted
        or failed before any tool returned.
    """

    response: DocsResponse
    retrieval_context: list[str]
    tools_called: list[str]


class _SearchTerm(BaseModel):
    """A tiny structured-output schema for the Ukrainian catalogue search
    term extraction step (docs/decisions.md #6) — separate from
    `DocsResponse` because this is an internal translation step, not part
    of the mandatory output contract.
    """

    search_term_uk: str


_retriever_singleton: Any = None


def _get_retriever() -> Any:
    """Lazily builds and caches the hybrid retriever once per Docs Agent
    process (docs/decisions.md #7) — never at module import time.
    """
    global _retriever_singleton
    if _retriever_singleton is None:
        _retriever_singleton = build_retriever(load_knowledge_base())
    return _retriever_singleton


def _translate_to_ukrainian_search_term(masked_query: str) -> str:
    model = get_chat_model("docs")
    structured_model = model.with_structured_output(_SearchTerm)
    result = structured_model.invoke(
        "Extract the core product/category search term from this customer "
        "message and translate it to Ukrainian, 1-4 words, suitable as a "
        "product catalogue search query. If the message names no product "
        "or category, return an empty string.\n\nMessage:\n" + masked_query
    )
    if not isinstance(result, _SearchTerm):
        raise DocsInvalidOutputError("search-term extraction returned no schema")
    return result.search_term_uk


def _kb_chunk_to_source(chunk_metadata: dict[str, Any]) -> Source:
    return Source(
        ref=chunk_metadata["source"],
        retrieved_at=datetime.fromisoformat(chunk_metadata["retrieved_at"]),
        version=chunk_metadata["version"],
    )


def _compose_docs_response(compiled_prompt: str) -> DocsResponse:
    model = get_chat_model("docs")
    structured_model = model.with_structured_output(DocsResponse, include_raw=True)
    client = get_langfuse_client()
    span_cm = (
        client.start_as_current_observation(
            name="docs_agent.compose",
            as_type="generation",
            prompt=get_prompt_client("supportflow/docs"),
            model=model.model_name,
        )
        if client is not None
        else nullcontext()
    )
    try:
        with span_cm as generation:
            raw = structured_model.invoke(compiled_prompt)
            if generation is not None:
                usage = getattr(raw.get("raw"), "usage_metadata", None) or {}
                # Live-confirmed 2026-08-26 (Stage 4 Wave B, D-B2's go/no-go
                # check): without this, `input`/`output` stay `null` on
                # every one of this project's own 8 named generation spans
                # — harmless for Wave A's own tracing goal, but it means
                # Langfuse's own LLM-as-a-Judge evaluator (task §9) scores
                # nothing but "both empty" every time, since its mapping
                # reads exactly these two fields.
                generation.update(
                    usage_details=dict(usage),
                    input=compiled_prompt,
                    output=str(raw.get("parsed")),
                )
    except Exception as exc:  # noqa: BLE001 — provider errors vary
        raise DocsInvalidOutputError(str(exc)) from exc
    result = raw.get("parsed")
    if not isinstance(result, DocsResponse):
        raise DocsInvalidOutputError(
            f"model returned no valid DocsResponse: {raw.get('parsing_error')}"
        )
    return result


async def run_docs_agent(
    masked_query: str,
    *,
    retriever: Any = None,
    search_products: Any = _default_search_products,
    translate_fn: Any = _translate_to_ukrainian_search_term,
    compose_fn: Any = _compose_docs_response,
    prompt_fn: Any = get_prompt,
) -> DocsAgentResult:
    """Retrieve, then compose a sourced, confidence-scored answer.

    Parameters
    ----------
    masked_query : str
        Already PII-masked (docs/decisions.md #14).
    retriever : Any, optional
        Injected for testing; defaults to the lazily-built hybrid
        retriever (docs/decisions.md #7).
    search_products : Any, optional
        Injected for testing (an async callable `(query) -> list[dict]`);
        defaults to `src.infrastructure.silpo_mcp.search_products`.
    translate_fn, compose_fn, prompt_fn : Any, optional
        Injected for testing the retrieval/degrade-gracefully logic
        without a live LLM or Langfuse call; default to the real
        synchronous OpenRouter/Langfuse calls.

    Returns
    -------
    DocsAgentResult

    Raises
    ------
    DocsInvalidOutputError
        The model's output failed `DocsResponse` validation.
    """
    kb_docs = (retriever or _get_retriever()).invoke(masked_query)
    kb_texts = [doc.page_content for doc in kb_docs]
    kb_sources = [_kb_chunk_to_source(doc.metadata) for doc in kb_docs]

    mcp_products: list[dict[str, Any]] = []
    tools_called: list[str] = []
    try:
        search_term = translate_fn(masked_query)
        if search_term:
            mcp_products, tools_called = await search_products(search_term)
    except SilpoMcpAuthRequiredError:
        # docs/decisions.md Stage 4 Wave B D-B3: this one fails loudly
        # (CLAUDE.md §4 invariant) instead of degrading like every other
        # MCP failure below — a revoked/expired token needs a human to
        # re-authenticate, which silent KB-only degradation would hide.
        raise
    except Exception:  # noqa: BLE001 — MCP unavailability degrades, not fails
        mcp_products = []

    fetched_at = datetime.now(timezone.utc)
    mcp_texts = [
        f"{p.get('name', '')}: {p.get('price', '?')} грн" for p in mcp_products
    ]
    mcp_sources = [
        # Stage 5: an opaque product id (`silpo_mcp:<uuid>`) is unreadable
        # in the chat UI's own "Джерела" list — the product's own name,
        # already fetched for `mcp_texts` above, is the readable identity
        # a customer actually recognises.
        Source(ref=f"Сільпо: {p.get('name', 'товар')}", retrieved_at=fetched_at)
        for p in mcp_products
    ]

    retrieved_block = "\n\n".join(kb_texts + mcp_texts)
    prompt_text, _prompt_version = prompt_fn("supportflow/docs")
    compiled_prompt = prompt_text.replace("{{customer_message}}", masked_query).replace(
        "{{retrieved_content}}", retrieved_block
    )

    result = compose_fn(compiled_prompt)

    # docs/decisions.md #15: authoritative source metadata is what this
    # call actually retrieved, not whatever the model guessed.
    result.sources = kb_sources + mcp_sources
    return DocsAgentResult(
        response=result,
        retrieval_context=kb_texts + mcp_texts,
        tools_called=tools_called,
    )
