"""`_should_skip`'s add-only rule for `scripts/seed_prompts.py`.

Regression test for 2026-08-29: one re-run of this script replaced
`supportflow/docs`'s live prompt with this file's own stale baseline,
dropping a rules block that only ever existed in Langfuse. A name
already present in Langfuse must never be touched again, whether or not
its content still matches the baseline.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.seed_prompts import _should_skip  # noqa: E402


def test_a_name_not_yet_in_langfuse_is_seeded() -> None:
    skip, _reason = _should_skip(current=None, baseline="some text")
    assert skip is False


def test_an_existing_name_whose_content_diverged_is_skipped() -> None:
    skip, reason = _should_skip(current="evolved text", baseline="stale baseline")
    assert skip is True
    assert "diverged" in reason


def test_an_existing_name_whose_content_matches_is_still_skipped() -> None:
    skip, reason = _should_skip(current="same text", baseline="same text")
    assert skip is True
    assert "up to date" in reason
