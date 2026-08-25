"""Silpo MCP client: tool invocation and the code-level tool allowlist
(docs/decisions.md #24 — closes F24, the seeded Docs prompt's dead
file-path reference). OAuth/token lifecycle lives in
`src.infrastructure.silpo_mcp_auth` (Stage 4 Wave A split — this file was
already over CLAUDE.md's 320-line ceiling before tracing instrumentation).

`OAuthClientProvider` is an `httpx.Auth` subclass that plugs directly into
`langchain_mcp_adapters.sessions.StreamableHttpConnection(auth=...)`.
"""

import json
from collections.abc import Awaitable, Callable
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any

from src.infrastructure.observability import get_langfuse_client

# Re-exported for backward compatibility: `scripts/silpo_mcp_login.py`
# imports `DEFAULT_TOKEN_PATH` from this module, and this module's own
# docstrings reference `SilpoMcpAuthRequiredError` as the error `_mcp_
# connection`'s auth flow can raise — both now live in `silpo_mcp_auth`.
from src.infrastructure.silpo_mcp_auth import (  # noqa: F401
    DEFAULT_TOKEN_PATH,
    DiskTokenStorage,
    SilpoMcpAuthRequiredError,
    callback_handler,
    redirect_handler,
)
from src.kernel.settings import settings

# docs/silpo_mcp_allowlist.md: 17 non-personal, read-only tools, derived
# from each tool's own description (not a name-pattern heuristic) —
# `silpo_get_loyalty_info`/`silpo_get_promo_codes` are personal despite not
# matching the `silpo_get_my_*` prefix, so this list is data, not a filter
# rule Docs Agent could reconstruct from names alone.
SILPO_ALLOWLIST: frozenset[str] = frozenset(
    {
        "silpo_find_address",
        "silpo_find_nova_poshta_offices",
        "silpo_find_nova_poshta_settlements",
        "silpo_find_products_batch",
        "silpo_get_available_delivery_types",
        "silpo_get_categories",
        "silpo_get_categories_tree",
        "silpo_get_category",
        "silpo_get_popular_categories",
        "silpo_get_product_details",
        "silpo_get_product_sets",
        "silpo_get_products",
        "silpo_get_promotions",
        "silpo_get_replacements",
        "silpo_get_similar_products",
        "silpo_get_time_slots",
        "silpo_list_branches",
    }
)


class SilpoMcpToolNotAllowedError(Exception):
    """Attempted to call a tool outside `SILPO_ALLOWLIST` — the code-level
    enforcement docs/decisions.md #24 requires, independent of whatever
    the seeded prompt says.
    """


class SilpoMcpResultParseError(Exception):
    """The tool call succeeded but its result was in a shape
    `_parse_tool_result` doesn't recognise.
    """


async def _mcp_connection() -> Any:
    from langchain_mcp_adapters.sessions import StreamableHttpConnection
    from mcp.client.auth.oauth2 import OAuthClientProvider
    from mcp.shared.auth import OAuthClientMetadata
    from pydantic import AnyUrl

    auth = OAuthClientProvider(
        server_url=settings.silpo_mcp_url,
        client_metadata=OAuthClientMetadata(
            redirect_uris=[AnyUrl("https://localhost/callback")],
            token_endpoint_auth_method="none",
        ),
        storage=DiskTokenStorage(),
        redirect_handler=redirect_handler,
        callback_handler=callback_handler,
    )
    return StreamableHttpConnection(
        transport="streamable_http", url=settings.silpo_mcp_url, auth=auth
    )


async def call_mcp_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """One allowlisted Silpo MCP tool call.

    Parameters
    ----------
    name : str
        Must be in `SILPO_ALLOWLIST`.
    args : dict[str, Any]

    Returns
    -------
    dict[str, Any]
        The tool's parsed result.

    Raises
    ------
    SilpoMcpToolNotAllowedError
        `name` is not allowlisted, or the server doesn't offer it.
    SilpoMcpAuthRequiredError
        No valid token on disk (propagated from `redirect_handler`).

    Notes
    -----
    # ponytail: opens a fresh MCP session per call rather than reusing one
    # across the bootstrap chain — simpler and safe (OAuth token is
    # disk-cached, so no re-login), at the cost of a few extra round
    # trips per Docs Agent request. Revisit if latency measurement
    # (Stage 4) shows this is the bottleneck.
    """
    if name not in SILPO_ALLOWLIST:
        raise SilpoMcpToolNotAllowedError(name)
    from langchain_mcp_adapters.tools import load_mcp_tools

    client = get_langfuse_client()
    span_cm = (
        client.start_as_current_observation(
            name="silpo_mcp.call_tool", as_type="tool", metadata={"tool": name}
        )
        if client is not None
        else nullcontext()
    )
    # Langfuse's own span context manager records an uncaught exception as
    # an error observation — no manual status bookkeeping needed here.
    with span_cm:
        connection = await _mcp_connection()
        tools = await load_mcp_tools(None, connection=connection)
        tool = next((t for t in tools if t.name == name), None)
        if tool is None:
            raise SilpoMcpToolNotAllowedError(f"{name} not offered by the server")
        result = await tool.ainvoke(args)
        return _parse_tool_result(result)


def _parse_tool_result(result: Any) -> dict[str, Any]:
    """Normalize an MCP tool's `ainvoke()` result to a plain dict.

    Confirmed live (2026-08-25, real `silpo_list_branches` call): a
    `langchain_mcp_adapters`-wrapped MCP tool's result is
    `list[{"type": "text", "text": "<json string>"}]` — a content-block
    list, not a bare JSON string or dict as an unverified guess might
    assume.
    """
    if isinstance(result, dict):
        return result
    if isinstance(result, str):
        return dict(json.loads(result))
    if isinstance(result, list):
        for block in result:
            if isinstance(block, dict) and block.get("type") == "text":
                return dict(json.loads(block["text"]))
    raise SilpoMcpResultParseError(f"unrecognised MCP tool result shape: {result!r}")


CallToolFn = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class BranchContext:
    """The non-personal branch/delivery/timeslot context most Silpo MCP
    product tools require (docs/decisions.md #27) — their own
    descriptions say to source this from `silpo_get_shopping_cart_by_id`
    (personal, excluded, docs/decisions.md #2), so this bootstraps the
    same fields from three allowlisted, non-personal tools instead.
    """

    branch_id: str
    delivery_type: str
    timeslot_start: str
    timeslot_end: str


# ponytail: process-lifetime cache, no TTL/staleness check — a branch's
# delivery/timeslot options rarely change within a single process's
# uptime. Upgrade to a timestamped, periodically-refreshed cache if
# Stage 4 measurement shows stale slots causing failed product calls.
_branch_context_cache: BranchContext | None = None


async def get_branch_context(
    call_tool: CallToolFn = call_mcp_tool, force_refresh: bool = False
) -> BranchContext:
    """Bootstrap `BranchContext` from three allowlisted, non-personal
    tools, caching the result for the rest of this process's lifetime.

    Parameters
    ----------
    call_tool : CallToolFn, default=`call_mcp_tool`
        Injected for testing.
    force_refresh : bool, default=False
        Bypass the cache.

    Returns
    -------
    BranchContext
    """
    global _branch_context_cache
    if _branch_context_cache is not None and not force_refresh:
        return _branch_context_cache

    branches = await call_tool("silpo_list_branches", {"hasPickup": True, "limit": 5})
    branch_list = branches["branches"]
    branch = next((b for b in branch_list if b.get("open")), branch_list[0])

    delivery = await call_tool(
        "silpo_get_available_delivery_types",
        {"latitude": branch["latitude"], "longitude": branch["longitude"]},
    )
    option = delivery["options"][0]

    slots = await call_tool(
        "silpo_get_time_slots",
        {"branchId": option["branchId"], "deliveryTypes": [option["deliveryType"]]},
    )
    slot_list = slots["slots"]
    slot = next((s for s in slot_list if s.get("available")), slot_list[0])

    _branch_context_cache = BranchContext(
        branch_id=option["branchId"],
        delivery_type=option["deliveryType"],
        timeslot_start=slot["start"],
        timeslot_end=slot["end"],
    )
    return _branch_context_cache


async def search_products(
    query: str, *, limit: int = 5, call_tool: CallToolFn = call_mcp_tool
) -> list[dict[str, Any]]:
    """Search the Silpo catalogue for `query` via `silpo_find_products_batch`.

    Parameters
    ----------
    query : str
        Already translated to Ukrainian (docs/decisions.md #6) — this
        function has no opinion on language, it only searches.
    limit : int, default=5
    call_tool : CallToolFn, default=`call_mcp_tool`
        Injected for testing.

    Returns
    -------
    list[dict[str, Any]]
        Raw product dicts from the tool's `queries[0].products`, or `[]`
        if nothing matched.
    """
    context = await get_branch_context(call_tool=call_tool)
    result = await call_tool(
        "silpo_find_products_batch",
        {
            "branchId": context.branch_id,
            "deliveryType": context.delivery_type,
            "timeslotStart": context.timeslot_start,
            "timeslotEnd": context.timeslot_end,
            "products": query,
            "limit": limit,
        },
    )
    queries = result.get("queries", [])
    return list(queries[0]["products"]) if queries else []


def filter_allowed_tools(tools: list) -> list:
    """Keep only tools whose `.name` is in `SILPO_ALLOWLIST`.

    Parameters
    ----------
    tools : list
        `list[langchain_core.tools.BaseTool]` in production; any object
        with a `.name` attribute in tests.

    Returns
    -------
    list
        Same element type as `tools`, filtered.
    """
    return [tool for tool in tools if tool.name in SILPO_ALLOWLIST]
