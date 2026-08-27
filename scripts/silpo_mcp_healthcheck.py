"""Preflight check for the live Silpo MCP server, independent of the
Supervisor/LangGraph stack — no LLM calls, just the four allowlisted
tools `search_products` actually needs. `silpo_get_available_delivery_types`
was found returning an empty response body live on 2026-08-26; this
script exists to answer "is that still true, and does it look like a
data-shape change or a transient outage" without burning a full
golden-dataset run to find out.

    .venv/Scripts/python scripts/silpo_mcp_healthcheck.py

Exits 0 if every call below returns a usable shape, 1 otherwise. Each
failure prints the raw exception — a `SilpoMcpResultParseError` here
means the server responded but the body didn't parse (a likely format
change), while a connection/auth error means the server or token is the
problem, not the response shape.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.infrastructure.silpo_mcp import (  # noqa: E402
    get_branch_context,
    search_products,
)


async def _run() -> int:
    print(
        "Checking Silpo MCP bootstrap chain (silpo_list_branches -> "
        "silpo_get_available_delivery_types -> silpo_get_time_slots)..."
    )
    try:
        context, tool_names = await get_branch_context(force_refresh=True)
    except Exception as exc:  # noqa: BLE001 — report every failure, not just the first
        print(f"  FAIL: {type(exc).__name__}: {exc}")
        print(
            "  A SilpoMcpResultParseError here means the server responded but "
            "the body didn't parse as expected JSON — check whether the tool's "
            "response shape changed. Any other error means connectivity/auth."
        )
        return 1
    print(f"  OK — branch={context.branch_id}, tools called={tool_names}")

    print("Checking silpo_find_products_batch (real product search)...")
    try:
        products, tool_names = await search_products("молоко")
    except Exception as exc:  # noqa: BLE001
        print(f"  FAIL: {type(exc).__name__}: {exc}")
        return 1
    print(f"  OK — {len(products)} product(s), tools called={tool_names}")

    print("All Silpo MCP checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_run()))
