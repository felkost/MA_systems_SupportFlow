# Telegram bot setup for Escalation Agent

One-time manual setup the author does before `scripts/escalation_agent_smoke.py`
can send a real message (task §10). Nothing here is code — it produces two
`.env` values: `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.

## 1. Create the bot via BotFather

1. In Telegram, open `@BotFather` (the official one, blue verification
   checkmark).
2. Send it `/newbot`.
3. BotFather asks for a display name (anything, e.g. `SupportFlow
   Escalation Bot`) and a username (must be unique, must end in `bot`,
   e.g. `supportflow_escalation_bot`).
4. BotFather replies with a line like `Use this token to access the HTTP
   API: 123456789:AAH...` — that is `TELEGRAM_BOT_TOKEN`. Copy it into
   `.env`. Never commit it — `.env` is already gitignored.

## 2. Create a test channel/group

The bot cannot message a chat it has never been added to, so a dedicated
"operator test channel" is needed:

1. Create a new Telegram **group** (simpler than a channel — no extra
   admin setup needed to read its `chat_id`), e.g. `SupportFlow — test
   operator channel`.
2. Add the bot to this group as a member.
3. Send any message into the group **from your own account** (e.g.
   `test`) — the next step needs at least one human message for the
   Telegram API to report the group's `chat_id`.

## 3. Find the test channel's `chat_id`

Run `scripts/telegram_bot_setup_check.py` (requires `TELEGRAM_BOT_TOKEN`
already set in `.env`):

```bash
.venv/Scripts/python -m scripts.telegram_bot_setup_check
```

It calls `getMe` (confirms the token works, prints the bot's username)
then `getUpdates` (lists recent messages the bot has seen) and prints
every `chat.id` found, so you can identify the group from step 2 (group
chat ids are negative, e.g. `-1001234567890`). Copy the right one into
`.env` as `TELEGRAM_CHAT_ID`.

If `getUpdates` returns nothing: the step 2 message wasn't sent, or is
older than 24 hours (Telegram only retains updates for an unconfirmed bot
for 24 hours) — send a new message into the group and re-run.

## 4. Verify the connection manually (no project code)

```bash
curl -X POST "https://api.telegram.org/bot<TOKEN>/sendMessage" \
  -d "chat_id=<CHAT_ID>" -d "text=SupportFlow: connection check"
```

If the message arrives in the test group, `.env` is correct and Stage 3's
`scripts/escalation_agent_smoke.py` can be run with `ALLOW_REAL_SEND=true`.
