"""Silpo MCP OAuth: persistent token storage and the manual-login contract
(docs/decisions.md #5). Split out of `silpo_mcp.py` (Stage 4 Wave A) as a
genuine responsibility split — token lifecycle versus tool invocation —
not a constants-only extraction; `silpo_mcp.py` was already over CLAUDE.md's
320-line ceiling before Wave A added tracing instrumentation to it.

Confirmed against the installed `mcp==1.29.0` package by direct source
inspection (see insights.md, 2026-08-25): `TokenStorage` is a
`typing.Protocol` with four async methods, no inheritance required.
"""

import json
from pathlib import Path

from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from src.kernel.settings import PROJECT_ROOT

DEFAULT_TOKEN_PATH = PROJECT_ROOT / ".cache" / "silpo_mcp_token.json"


class SilpoMcpAuthRequiredError(Exception):
    """No valid token on disk and no automated login exists — Silpo's
    OAuth is phone+OTP against a real account (docs/decisions.md #5),
    so this fails loudly rather than attempting to open a browser from
    inside an unattended agent process.
    """


class DiskTokenStorage:
    """`mcp.client.auth.oauth2.TokenStorage` implementation backed by one
    JSON file (docs/decisions.md #5). Never logged, never sent to
    Langfuse — `.cache/` is gitignored.

    Implements `TokenStorage`'s four async methods structurally (it is a
    `Protocol`, not an ABC — no inheritance declared, matching the
    installed SDK's own pattern).
    """

    def __init__(self, path: Path = DEFAULT_TOKEN_PATH) -> None:
        self._path = path

    def _read(self) -> dict:
        if not self._path.exists():
            return {}
        return json.loads(self._path.read_text(encoding="utf-8"))

    def _write(self, data: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data), encoding="utf-8")

    async def get_tokens(self) -> OAuthToken | None:
        raw = self._read().get("tokens")
        return OAuthToken.model_validate(raw) if raw else None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        data = self._read()
        data["tokens"] = json.loads(tokens.model_dump_json())
        self._write(data)

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        raw = self._read().get("client_info")
        return OAuthClientInformationFull.model_validate(raw) if raw else None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        data = self._read()
        data["client_info"] = json.loads(client_info.model_dump_json())
        self._write(data)


async def redirect_handler(authorization_url: str) -> None:
    """Raises — see `SilpoMcpAuthRequiredError`. Run
    `scripts/probe_silpo_mcp.py` (or an equivalent manual login) once to
    establish a session; after that, `DiskTokenStorage`'s refresh token
    keeps the process running without this handler ever firing again.
    """
    raise SilpoMcpAuthRequiredError(
        "No valid Silpo MCP token on disk. A human must complete the "
        "phone+OTP login once (docs/decisions.md #5) — authorization URL: "
        f"{authorization_url}"
    )


async def callback_handler() -> tuple[str, str | None]:
    """Raises — see `redirect_handler`; this is the other half of the
    same manual-login contract `OAuthClientProvider` requires.
    """
    raise SilpoMcpAuthRequiredError(
        "No valid Silpo MCP token on disk — callback_handler was reached, "
        "meaning redirect_handler should already have failed first."
    )
