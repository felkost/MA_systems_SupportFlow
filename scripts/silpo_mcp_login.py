"""One-time interactive Silpo MCP login, writing the resulting OAuth
tokens to disk via `DiskTokenStorage` (docs/decisions.md #5) — this is
what Docs Agent's own process reads on every subsequent startup, so it
never needs this manual step again while the refresh token stays valid.

This performs a real phone+OTP login in a browser. Run it once, by the
project author, never from application code (the same rule
`scripts/probe_silpo_mcp.py` already follows):

    .venv/Scripts/python scripts/silpo_mcp_login.py

Unlike `probe_silpo_mcp.py` (hand-rolled `requests`, in-memory token, run
once to capture `tools/list`), this script drives the real
`mcp.client.auth.oauth2.OAuthClientProvider` flow used by
`src/infrastructure/silpo_mcp.py` in production, so the token it writes
is in the exact format that code expects.
"""

import asyncio
import sys
import urllib.parse
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.infrastructure.silpo_mcp import (  # noqa: E402
    DEFAULT_TOKEN_PATH,
    call_mcp_tool,
)
from src.kernel.settings import settings  # noqa: E402


async def _interactive_redirect_handler(authorization_url: str) -> None:
    print("\n1) Opening your browser to log in with your Silpo account...")
    print(f"   If it doesn't open automatically, visit:\n   {authorization_url}\n")
    webbrowser.open(authorization_url)


async def _interactive_callback_handler() -> tuple[str, str | None]:
    redirect_url = input(
        "2) After logging in, the browser will redirect to a "
        "https://localhost/callback?... URL that will not load — that's "
        "expected. Paste the FULL redirected URL here:\n> "
    ).strip()
    query = urllib.parse.urlparse(redirect_url).query
    params = urllib.parse.parse_qs(query)
    code = params["code"][0]
    state = params.get("state", [None])[0]
    return code, state


async def main() -> None:
    # Monkeypatch the module-level handlers `call_mcp_tool` uses, so this
    # one-off script can drive a real interactive login without adding an
    # interactive code path to production `silpo_mcp.py` itself (that
    # module's own handlers must keep failing loudly — docs/decisions.md
    # #5 — an unattended agent process must never try to open a browser).
    import src.infrastructure.silpo_mcp as silpo_mcp

    silpo_mcp.redirect_handler = _interactive_redirect_handler
    silpo_mcp.callback_handler = _interactive_callback_handler

    print(f"Logging in to {settings.silpo_mcp_url} ...")
    result = await call_mcp_tool("silpo_list_branches", {"limit": 1})
    print(f"\nLogin succeeded — {len(result.get('branches', []))} branch(es) returned.")
    print(f"Token saved to {DEFAULT_TOKEN_PATH}")
    print("Docs Agent's process can now run without this script.")


if __name__ == "__main__":
    asyncio.run(main())
