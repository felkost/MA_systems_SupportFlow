"""Manual, live smoke check for Escalation Agent: a real OpenRouter LLM
call, a real file write, and (only with `ALLOW_REAL_SEND=true`) a real
Telegram message to the configured test channel — the live verification
Escalation Agent needs, since neither the file write nor the send can be
proven by an offline test.

`pytest --cov=src` never sends a real Telegram message or writes outside
`tmp_path` — this script is the counterpart. Requires the Telegram bot
setup to be done first (real `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` in
`.env`), and `ALLOW_REAL_SEND=true` set for this run only:

    ALLOW_REAL_SEND=true BYPASS_HITL=true \
        .venv/Scripts/python scripts/escalation_agent_smoke.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.application.escalation_agent import (  # noqa: E402
    EscalationContext,
    run_escalation_agent,
)
from src.domain.schemas import ClassificationOutput  # noqa: E402
from src.kernel.settings import settings  # noqa: E402


def main() -> None:
    if not settings.allow_real_send:
        print(
            "ALLOW_REAL_SEND is not set — this run will write a file but "
            "not send Telegram. Set ALLOW_REAL_SEND=true to also verify "
            "the real send."
        )

    context = EscalationContext(
        masked_text="У мене алергічна реакція на ваш продукт!",
        classification=ClassificationOutput(
            category="critical", urgency="critical", language="uk"
        ),
        confidence=None,
        errors=[],
    )
    result = run_escalation_agent(context, request_id="smoke-1", session_id="smoke")

    print("SUMMARY:", result.output.summary)
    print("CUSTOMER_MESSAGE:", result.output.customer_message)
    print("WRITTEN:", result.written)
    print("SENT:", result.sent)
    print("CAPPED:", result.capped)
    print("DEDUPLICATED:", result.deduplicated)


if __name__ == "__main__":
    main()
