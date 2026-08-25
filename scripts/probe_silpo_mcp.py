"""One-off probe: OAuth into Silpo MCP and record the real tools/list
response verbatim.

Run manually, once, by the project author (this performs a real
phone+OTP login in a browser — it must not run unattended or be called
from application code):

    .venv/Scripts/python scripts/probe_silpo_mcp.py

Ported from a working Colab notebook (Silpo_scenario_lab4.ipynb, author's
own prior experiments against the live server) rather than written against
the `mcp` SDK from documentation, because that notebook is proven to work
against the real server and an SDK-based guess is not. Plain `requests`,
no MCP client library — Streamable HTTP JSON-RPC is a small enough surface
that hand-rolling it here removes a whole class of "which SDK version"
risk for a script that runs exactly once.

Writes the exact tools/list result to docs/silpo_mcp_tools.json. Nothing
in this project may hardcode a Silpo tool name until that file exists.
"""

import base64
import hashlib
import json
import secrets
import urllib.parse
from pathlib import Path

import requests

MCP_URL = "https://mcp.silpo.ua/mcp"
REDIRECT_URI = "https://localhost/callback"
PROTOCOL_VERSION = "2025-06-18"
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "docs" / "silpo_mcp_tools.json"


def _parse_mcp_response(resp: requests.Response) -> dict:
    """Streamable HTTP may answer as SSE or as plain JSON — read whichever
    the server actually sent instead of assuming one.
    """
    content_type = resp.headers.get("content-type", "")
    text = resp.text
    if "text/event-stream" in content_type or text.lstrip().startswith(
        ("event:", "data:")
    ):
        data_lines = [
            line[5:].strip() for line in text.splitlines() if line.startswith("data:")
        ]
        for line in reversed(data_lines):
            try:
                return json.loads(line)
            except ValueError:
                continue
        raise ValueError(f"SSE response had no valid JSON: {text[:300]}")
    return resp.json()


def _discover_oauth_endpoints() -> dict:
    for path in (
        "/.well-known/oauth-protected-resource",
        "/.well-known/oauth-authorization-server",
    ):
        response = requests.get(f"https://mcp.silpo.ua{path}", timeout=20)
        if not response.ok:
            continue
        metadata = response.json()
        if "authorization_endpoint" in metadata:
            return metadata
        for auth_server in metadata.get("authorization_servers", []):
            nested = requests.get(
                f"{auth_server.rstrip('/')}/.well-known/oauth-authorization-server",
                timeout=20,
            )
            if nested.ok:
                return nested.json()
    raise RuntimeError("Silpo MCP did not advertise OAuth metadata")


def _register_client(registration_endpoint: str) -> str:
    response = requests.post(
        registration_endpoint,
        json={
            "client_name": "supportflow-probe",
            "redirect_uris": [REDIRECT_URI],
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        },
        timeout=20,
    )
    response.raise_for_status()
    return response.json()["client_id"]


def _authorize_and_get_token(
    auth_metadata: dict, client_id: str, authorize_endpoint: str, token_endpoint: str
) -> str:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": secrets.token_urlsafe(8),
    }
    if auth_metadata.get("scopes_supported"):
        params["scope"] = " ".join(auth_metadata["scopes_supported"])

    print("1) Open this URL and log in with your Silpo account:\n")
    print(f"{authorize_endpoint}?{urllib.parse.urlencode(params)}")
    print(
        "\n2) The browser will redirect to "
        f"{REDIRECT_URI}?code=... — the page will not load, that's expected."
    )
    redirect_url = input("\n3) Paste the FULL redirected URL here: ").strip()
    code = urllib.parse.parse_qs(urllib.parse.urlparse(redirect_url).query)["code"][0]

    token_response = requests.post(
        token_endpoint,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": client_id,
            "code_verifier": verifier,
        },
        timeout=20,
    )
    token_response.raise_for_status()
    return token_response.json()["access_token"]


def main() -> None:
    auth_metadata = _discover_oauth_endpoints()
    client_id = _register_client(auth_metadata["registration_endpoint"])
    access_token = _authorize_and_get_token(
        auth_metadata,
        client_id,
        auth_metadata["authorization_endpoint"],
        auth_metadata["token_endpoint"],
    )

    session = requests.Session()
    session.headers.update(
        {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        }
    )
    init_response = session.post(
        MCP_URL,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "supportflow-probe", "version": "1.0"},
            },
        },
        timeout=30,
    )
    init_response.raise_for_status()
    session_id = init_response.headers.get("mcp-session-id")
    if session_id:
        session.headers["mcp-session-id"] = session_id
    session.post(
        MCP_URL,
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        timeout=15,
    )

    tools_response = session.post(
        MCP_URL,
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        timeout=30,
    )
    tools_response.raise_for_status()
    tools = _parse_mcp_response(tools_response)["result"]["tools"]

    read_only = [
        t for t in tools if t.get("annotations", {}).get("readOnlyHint") is True
    ]
    write = [t for t in tools if not t.get("annotations", {}).get("readOnlyHint")]
    print(
        f"tools: {len(tools)} | read-only: {len(read_only)} | "
        f"write (never called by this project): {len(write)}"
    )

    payload = {"source": MCP_URL, "protocol_version": PROTOCOL_VERSION, "tools": tools}
    OUTPUT_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"Wrote {len(tools)} tools to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
