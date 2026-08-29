"""One-off: `supportflow/escalation` never forbade the model from just
answering the customer's underlying question itself. Live-confirmed
2026-08-29 (docs/decisions.md #82): given a web_search decline
(confidence=0.0, "this doesn't relate to Silpo support") for "Яка
столиця Англії?", the Escalation LLM composed `customer_message="Столиця
Англії — Лондон. Дякуємо за звернення!"` — an ungrounded fact from its
own training data, defeating the entire point of escalating: Docs/Web
Search are the only agents allowed to answer, and only from retrieved
content. Adds one Constraints bullet forbidding this.

Fetches the *live* production text first (never a hardcoded baseline —
the exact mistake decision #74 is about), anchors on the last existing
Constraints bullet (must match exactly once), and refuses to publish
unless the insertion round-trips losslessly.

Already run once, 2026-08-29 — published as v10. Kept for the record and
because re-running is safe (it skips if the constraint is already
present); not meant to run again unless the prompt regresses.

Run manually, by the project author (needs LANGFUSE_PUBLIC_KEY/
LANGFUSE_SECRET_KEY in .env):

    .venv/Scripts/python scripts/fix_escalation_self_answer.py
"""

import os
from pathlib import Path

from langfuse import Langfuse

_PROMPT_NAME = "supportflow/escalation"

_ANCHOR = (
    "- Always state what was already attempted — an operator should never have\n"
    "  to re-derive it from scratch."
)

_NEW_BULLET = (
    "\n- Never answer the customer's underlying question yourself, even when you\n"
    "  recognise the answer — you have no way to verify it against Silpo's own\n"
    "  sources, and this case only reached you because Docs/Web Search Agent\n"
    "  either declined or was not confident enough. `customer_message`\n"
    "  acknowledges the request and explains what happens next; it must never\n"
    "  resolve the case itself."
)


def _add_constraint(live_text: str) -> str:
    if live_text.count(_ANCHOR) != 1:
        raise ValueError(
            f"expected exactly one occurrence of the anchor bullet, found "
            f"{live_text.count(_ANCHOR)} — refusing to guess where to insert"
        )
    return live_text.replace(_ANCHOR, _ANCHOR + _NEW_BULLET, 1)


def main() -> None:
    langfuse = Langfuse()
    current = langfuse.get_prompt(_PROMPT_NAME, label="production")

    if "Never answer the customer's underlying question yourself" in current.prompt:
        print(f"Skipped: {_PROMPT_NAME} already has the constraint")
        return

    new_text = _add_constraint(current.prompt)

    # Lossless round-trip: removing exactly what was inserted must return
    # the original text byte-for-byte, or this refuses to publish.
    if new_text.replace(_NEW_BULLET, "", 1) != current.prompt:
        raise AssertionError("insertion did not round-trip losslessly — not publishing")

    langfuse.create_prompt(
        name=_PROMPT_NAME,
        prompt=new_text,
        labels=["production"],
        type="text",
    )
    print(f"Published {_PROMPT_NAME}: v{current.version} -> new version")
    langfuse.flush()


if __name__ == "__main__":
    if not os.environ.get("LANGFUSE_PUBLIC_KEY"):
        env_path = Path(__file__).resolve().parent.parent / ".env"
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                key, _, value = line.partition("=")
                if value.strip():
                    os.environ[key.strip()] = value.strip()
    main()
