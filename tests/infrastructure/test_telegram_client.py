"""`src.infrastructure.telegram_client` — one `sendMessage` call, tested
against a mocked `httpx.post` so no real network call happens here (the
real one is exercised only by `scripts/escalation_agent_smoke.py`).
"""

from typing import Any

import httpx
import pytest

from src.infrastructure import telegram_client
from src.infrastructure.telegram_client import TelegramSendError, send_telegram_message
from src.kernel.constants import TELEGRAM_MAX_MESSAGE_CHARS


class _FakeResponse:
    def __init__(self, status_code: int, body: dict[str, Any]) -> None:
        self.status_code = status_code
        self._body = body
        self.text = str(body)

    def json(self) -> dict[str, Any]:
        return self._body


def test_send_succeeds_on_ok_true(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def fake_post(url: str, json: dict[str, Any], timeout: float) -> _FakeResponse:
        calls.append((url, json, timeout))
        return _FakeResponse(200, {"ok": True, "result": {"message_id": 1}})

    monkeypatch.setattr(telegram_client.httpx, "post", fake_post)

    send_telegram_message("hi", chat_id="-100", bot_token="TOKEN", timeout=5.0)

    assert calls[0][0] == "https://api.telegram.org/botTOKEN/sendMessage"
    assert calls[0][1] == {"chat_id": "-100", "text": "hi"}


def test_send_raises_on_non_2xx(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        telegram_client.httpx,
        "post",
        lambda *a, **kw: _FakeResponse(400, {"ok": False, "description": "bad"}),
    )

    with pytest.raises(TelegramSendError):
        send_telegram_message("hi", chat_id="-100", bot_token="TOKEN", timeout=5.0)


def test_send_raises_on_ok_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        telegram_client.httpx,
        "post",
        lambda *a, **kw: _FakeResponse(200, {"ok": False, "description": "blocked"}),
    )

    with pytest.raises(TelegramSendError):
        send_telegram_message("hi", chat_id="-100", bot_token="TOKEN", timeout=5.0)


def test_send_raises_on_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_error(*_a: Any, **_kw: Any) -> None:
        raise httpx.ConnectError("no network")

    monkeypatch.setattr(telegram_client.httpx, "post", raise_error)

    with pytest.raises(TelegramSendError):
        send_telegram_message("hi", chat_id="-100", bot_token="TOKEN", timeout=5.0)


def test_send_truncates_text_before_sending(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: dict[str, Any] = {}

    def fake_post(url: str, json: dict[str, Any], timeout: float) -> _FakeResponse:
        sent.update(json)
        return _FakeResponse(200, {"ok": True, "result": {}})

    monkeypatch.setattr(telegram_client.httpx, "post", fake_post)

    long_text = "a" * (TELEGRAM_MAX_MESSAGE_CHARS + 500)
    send_telegram_message(long_text, chat_id="-100", bot_token="TOKEN", timeout=5.0)

    assert len(sent["text"]) == TELEGRAM_MAX_MESSAGE_CHARS
