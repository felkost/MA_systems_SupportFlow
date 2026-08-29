"""`_add_history_block`'s insertion logic for
`scripts/promote_conversation_history_prompts.py` — the piece that does
not need live Langfuse credentials to verify.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.promote_conversation_history_prompts import (  # noqa: E402
    _add_history_block,
)


def test_inserts_the_block_immediately_before_customer_message() -> None:
    live_text = (
        "## Goals\nAnswer.\n\n"
        "<customer_message>\n{{customer_message}}\n</customer_message>\n"
    )

    result = _add_history_block(live_text)

    assert "{{conversation_history}}" in result
    history_index = result.index("<conversation_history>")
    customer_index = result.index("<customer_message>")
    assert history_index < customer_index


def test_never_touches_a_customer_message_after_the_anchor() -> None:
    live_text = (
        "intro\n<customer_message>\n{{customer_message}}\n</customer_message>\ntail"
    )

    result = _add_history_block(live_text)

    assert result.endswith(
        "<customer_message>\n{{customer_message}}\n</customer_message>\ntail"
    )


def test_raises_rather_than_guess_when_the_anchor_is_missing() -> None:
    with pytest.raises(ValueError, match="customer_message"):
        _add_history_block("a prompt with no input section at all")
