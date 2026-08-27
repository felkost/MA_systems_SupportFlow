"""One-time manual Telegram bot setup check — confirms `TELEGRAM_BOT_TOKEN`
works and finds the test channel's `chat_id` from the bot's recent
updates.

Run once, by the project author, never from application code:

    .venv/Scripts/python -m scripts.telegram_bot_setup_check
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from src.kernel.settings import settings  # noqa: E402

_API_BASE = "https://api.telegram.org"


def main() -> None:
    if not settings.telegram_bot_token:
        print("TELEGRAM_BOT_TOKEN is not set in .env.")
        return

    me = httpx.get(f"{_API_BASE}/bot{settings.telegram_bot_token}/getMe", timeout=10)
    me.raise_for_status()
    me_body = me.json()
    if not me_body.get("ok"):
        print(f"getMe failed: {me_body}")
        return
    bot = me_body["result"]
    print(f"Token OK — bot @{bot['username']} ({bot['first_name']}).")

    updates = httpx.get(
        f"{_API_BASE}/bot{settings.telegram_bot_token}/getUpdates", timeout=10
    )
    updates.raise_for_status()
    updates_body = updates.json()
    chats = {
        update["message"]["chat"]["id"]: update["message"]["chat"].get(
            "title", update["message"]["chat"].get("type")
        )
        for update in updates_body.get("result", [])
        if "message" in update
    }
    if not chats:
        print(
            "No recent updates found. Send a message into your test group "
            "(with this bot added to it), then re-run this script."
        )
        return

    print("\nCandidate chat_id values (copy the right one into TELEGRAM_CHAT_ID):")
    for chat_id, label in chats.items():
        print(f"  {chat_id}  —  {label}")


if __name__ == "__main__":
    main()
