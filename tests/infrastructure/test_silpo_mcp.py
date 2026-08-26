"""Silpo MCP client: disk-backed `TokenStorage` round-trip and allowlist
enforcement (docs/decisions.md #5/#24). The MCP session itself is never
touched here — `pytest` mocks it completely (docs/decisions.md #21); a
live check is `scripts/docs_agent_smoke.py`, run manually.
"""

from pathlib import Path

import pytest
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from src.infrastructure import silpo_mcp
from src.infrastructure.silpo_mcp import (
    SILPO_ALLOWLIST,
    DiskTokenStorage,
    SilpoMcpResultParseError,
    _parse_tool_result,
    filter_allowed_tools,
    get_branch_context,
    search_products,
)


class _FakeTool:
    def __init__(self, name: str) -> None:
        self.name = name


@pytest.mark.asyncio
async def test_disk_token_storage_round_trips_tokens(tmp_path: Path) -> None:
    storage = DiskTokenStorage(tmp_path / "silpo_mcp_token.json")
    assert await storage.get_tokens() is None

    tokens = OAuthToken(access_token="a1", token_type="Bearer", refresh_token="r1")
    await storage.set_tokens(tokens)

    loaded = await storage.get_tokens()
    assert loaded is not None
    assert loaded.access_token == "a1"
    assert loaded.refresh_token == "r1"


@pytest.mark.asyncio
async def test_disk_token_storage_round_trips_client_info(tmp_path: Path) -> None:
    storage = DiskTokenStorage(tmp_path / "silpo_mcp_token.json")
    assert await storage.get_client_info() is None

    info = OAuthClientInformationFull(
        redirect_uris=["https://localhost/callback"],
        client_id="c1",
        client_secret=None,
    )
    await storage.set_client_info(info)

    loaded = await storage.get_client_info()
    assert loaded is not None
    assert loaded.client_id == "c1"


@pytest.mark.asyncio
async def test_disk_token_storage_preserves_client_info_when_tokens_set(
    tmp_path: Path,
) -> None:
    storage = DiskTokenStorage(tmp_path / "silpo_mcp_token.json")
    info = OAuthClientInformationFull(
        redirect_uris=["https://localhost/callback"], client_id="c1", client_secret=None
    )
    await storage.set_client_info(info)
    await storage.set_tokens(
        OAuthToken(access_token="a1", token_type="Bearer", refresh_token="r1")
    )

    assert (await storage.get_client_info()).client_id == "c1"
    assert (await storage.get_tokens()).access_token == "a1"


def test_filter_allowed_tools_keeps_only_allowlisted_names() -> None:
    tools = [
        _FakeTool("silpo_get_products"),
        _FakeTool("silpo_get_my_shopping_cart"),  # personal, excluded
        _FakeTool("silpo_add_or_update_cart_products"),  # write, excluded
        _FakeTool("silpo_list_branches"),
    ]

    allowed = filter_allowed_tools(tools)

    assert {t.name for t in allowed} == {"silpo_get_products", "silpo_list_branches"}


def test_allowlist_has_exactly_seventeen_non_personal_read_only_tools() -> None:
    # docs/silpo_mcp_allowlist.md: 17 non-personal, read-only tools.
    assert len(SILPO_ALLOWLIST) == 17
    assert "silpo_get_my_shopping_cart" not in SILPO_ALLOWLIST
    assert "silpo_add_or_update_cart_products" not in SILPO_ALLOWLIST


def test_parse_tool_result_reads_the_real_content_block_shape() -> None:
    # Confirmed live 2026-08-25 (real silpo_list_branches call): a
    # langchain_mcp_adapters-wrapped MCP tool's ainvoke() result is a
    # content-block list, not a bare dict or JSON string.
    result = [{"type": "text", "text": '{"success": true, "branches": []}'}]

    assert _parse_tool_result(result) == {"success": True, "branches": []}


def test_parse_tool_result_reads_a_plain_dict() -> None:
    assert _parse_tool_result({"success": True}) == {"success": True}


def test_parse_tool_result_reads_a_json_string() -> None:
    assert _parse_tool_result('{"success": true}') == {"success": True}


def test_parse_tool_result_raises_on_unrecognised_shape() -> None:
    with pytest.raises(SilpoMcpResultParseError):
        _parse_tool_result(42)


def test_parse_tool_result_raises_typed_error_on_malformed_json() -> None:
    # Live-observed 2026-08-26: the MCP server returned an empty text
    # body for a genuinely-called tool, and a raw json.JSONDecodeError
    # would defeat search_products' own degrade-gracefully handling.
    with pytest.raises(SilpoMcpResultParseError):
        _parse_tool_result([{"type": "text", "text": ""}])


async def _fake_call_tool(name: str, args: dict) -> dict:
    if name == "silpo_list_branches":
        # Live-confirmed 2026-08-26: the real MCP server returns these as
        # strings, not numbers (docs/decisions.md #51) — kept as strings
        # here too so a regression that drops get_branch_context's own
        # float() cast fails this fake exactly like the real server did.
        return {
            "branches": [
                {
                    "branchId": "b1",
                    "latitude": "50.4",
                    "longitude": "30.5",
                    "open": True,
                }
            ]
        }
    if name == "silpo_get_available_delivery_types":
        assert isinstance(args["latitude"], float)
        assert isinstance(args["longitude"], float)
        return {"options": [{"branchId": "b1", "deliveryType": "SelfPickup"}]}
    if name == "silpo_get_time_slots":
        return {
            "slots": [
                {
                    "start": "2026-08-26T10:00:00Z",
                    "end": "2026-08-26T11:00:00Z",
                    "available": True,
                }
            ]
        }
    if name == "silpo_find_products_batch":
        # Live-confirmed 2026-08-26 (docs/decisions.md #52): "batch" is
        # literal — the tool requires an array of queries, not a bare
        # string; a caller passing a string gets a -32602 validation
        # error the server returns as unparseable text, not a clean
        # error, so a regression here fails loudly.
        assert isinstance(args["products"], list)
        return {
            "queries": [
                {
                    "query": args["products"][0],
                    "totalFound": 1,
                    "products": [{"id": "p1", "name": "Молоко безлактозне"}],
                }
            ]
        }
    raise AssertionError(f"unexpected tool call: {name}")


@pytest.mark.asyncio
async def test_get_branch_context_bootstraps_from_non_personal_tools_only() -> None:
    silpo_mcp._branch_context_cache = None

    ctx, tool_names = await get_branch_context(call_tool=_fake_call_tool)

    assert ctx.branch_id == "b1"
    assert ctx.delivery_type == "SelfPickup"
    assert ctx.timeslot_start == "2026-08-26T10:00:00Z"
    assert tool_names == [
        "silpo_list_branches",
        "silpo_get_available_delivery_types",
        "silpo_get_time_slots",
    ]


@pytest.mark.asyncio
async def test_get_branch_context_reports_no_tool_names_when_cached() -> None:
    """Stage 4 Wave B decision D-B7.2: a golden-dataset `expected_tools`
    entry must not depend on whether the cache happened to be warm.
    """
    silpo_mcp._branch_context_cache = None
    await get_branch_context(call_tool=_fake_call_tool)

    _ctx, tool_names = await get_branch_context(call_tool=_fake_call_tool)

    assert tool_names == []


@pytest.mark.asyncio
async def test_get_branch_context_is_cached_across_calls() -> None:
    silpo_mcp._branch_context_cache = None
    calls: list[str] = []

    async def counting_call_tool(name: str, args: dict) -> dict:
        calls.append(name)
        return await _fake_call_tool(name, args)

    await get_branch_context(call_tool=counting_call_tool)
    await get_branch_context(call_tool=counting_call_tool)

    assert calls == [
        "silpo_list_branches",
        "silpo_get_available_delivery_types",
        "silpo_get_time_slots",
    ]


@pytest.mark.asyncio
async def test_search_products_returns_products_from_the_batch_query() -> None:
    silpo_mcp._branch_context_cache = None

    products, tool_names = await search_products(
        "безлактозне молоко", call_tool=_fake_call_tool
    )

    assert products == [{"id": "p1", "name": "Молоко безлактозне"}]
    assert tool_names[-1] == "silpo_find_products_batch"


@pytest.mark.asyncio
async def test_search_products_keeps_tool_name_when_result_is_malformed() -> None:
    silpo_mcp._branch_context_cache = None

    async def malformed_call_tool(name: str, args: dict) -> dict:
        if name == "silpo_find_products_batch":
            raise SilpoMcpResultParseError("empty MCP response body")
        return await _fake_call_tool(name, args)

    products, tool_names = await search_products(
        "безлактозне молоко", call_tool=malformed_call_tool
    )

    assert products == []
    assert tool_names[-1] == "silpo_find_products_batch"
