"""Telegram Bot API client: one `sendMessage` call, nothing else.

No SDK dependency — the endpoint is one JSON POST (confirmed against the
official Bot API docs, `core.telegram.org/bots/api#sendmessage`), and
`httpx` is already a pinned dependency — reuse before adding.
"""

from contextlib import nullcontext

import httpx

from src.infrastructure.observability import get_langfuse_client
from src.kernel.constants import TELEGRAM_MAX_MESSAGE_CHARS

_API_BASE = "https://api.telegram.org"


class TelegramSendError(Exception):
    """A network failure, a non-2xx response, or `ok: false` in the body.
    Never swallowed — Escalation is itself the failure-handling path, so a
    silent send failure here would be invisible to everyone.
    """


def send_telegram_message(
    text: str, *, chat_id: str, bot_token: str, timeout: float
) -> None:
    """Send one message via `POST /bot<token>/sendMessage`.

    Parameters
    ----------
    text : str
        Truncated to `TELEGRAM_MAX_MESSAGE_CHARS` before sending —
        Telegram's own documented per-message limit.
    chat_id : str
    bot_token : str
    timeout : float

    Raises
    ------
    TelegramSendError
    """
    truncated = text[:TELEGRAM_MAX_MESSAGE_CHARS]
    client = get_langfuse_client()
    span_cm = (
        client.start_as_current_observation(
            name="telegram.send_message", as_type="tool"
        )
        if client is not None
        else nullcontext()
    )
    # `TelegramSendError` (and any other exception) always propagates
    # unchanged through this span — never swallowed, since Escalation is
    # itself the failure-handling path (module docstring above).
    with span_cm:
        try:
            response = httpx.post(
                f"{_API_BASE}/bot{bot_token}/sendMessage",
                json={"chat_id": chat_id, "text": truncated},
                timeout=timeout,
            )
        except httpx.HTTPError as exc:
            raise TelegramSendError(f"Telegram request failed: {exc}") from exc

        if response.status_code >= 400:
            raise TelegramSendError(
                f"Telegram API returned {response.status_code}: {response.text}"
            )
        body = response.json()
        if not body.get("ok", False):
            raise TelegramSendError(f"Telegram API reported failure: {body}")
