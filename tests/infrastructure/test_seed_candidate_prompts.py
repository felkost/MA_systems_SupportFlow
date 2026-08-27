"""`build_candidate` — where the few-shot block lands, and what happens
when it cannot land anywhere.

Offline: no Langfuse client is constructed here.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.seed_candidate_prompts import CANDIDATES, build_candidate  # noqa: E402

_WITH_INPUT_HEADING = "## Goals\nClassify.\n\n## Input\n<customer_message>\n{{x}}\n"
# supportflow/docs v9's real shape: no "## Input" heading at all.
_WITHOUT_INPUT_HEADING = "## Goals\nAnswer.\n\n<customer_message>\n{{x}}\n"


def test_examples_land_before_the_input_section() -> None:
    """Order is the point: examples after `## Input` would read as part
    of the customer's own message.
    """
    result = build_candidate(_WITH_INPUT_HEADING, "## Examples\ndemo\n\n")

    assert result.index("## Examples") < result.index("## Input")


def test_examples_fall_back_to_the_customer_message_tag() -> None:
    """`supportflow/docs` production has no `## Input` heading — confirmed
    live 2026-08-27 — so the anchor must fall back to the message tag
    itself rather than refuse a real, valid prompt.
    """
    result = build_candidate(_WITHOUT_INPUT_HEADING, "## Examples\ndemo\n\n")

    assert result.index("## Examples") < result.index("<customer_message>")


def test_the_production_text_survives_intact() -> None:
    # The candidate must differ from production by the examples and
    # nothing else, or the comparison measures more than few-shot.
    result = build_candidate(_WITH_INPUT_HEADING, "## Examples\ndemo\n\n")

    assert "## Goals\nClassify." in result
    assert "{{x}}" in result
    assert result.replace("## Examples\ndemo\n\n", "") == _WITH_INPUT_HEADING


def test_a_prompt_with_neither_anchor_is_refused() -> None:
    """Better to fail than to append: with no anchor the examples would
    end up after the customer message.
    """
    with pytest.raises(ValueError, match="no defined insertion point"):
        build_candidate(
            "## Goals\nClassify.\nno message tag here\n", "## Examples\ndemo\n"
        )


@pytest.mark.parametrize("key", sorted(CANDIDATES))
def test_examples_never_reuse_an_evaluation_case(key: str) -> None:
    """Few-shot examples drawn from the scored cases would be training on
    the test set, and the resulting number would mean nothing.
    """
    import json

    root = Path(__file__).resolve().parents[2]
    scored: set[str] = set()
    for path in (
        root / "tests" / "fixtures" / "router_classification_cases.json",
        root / "evals" / "docs_optimization_set.json",
        root / "evals" / "golden_dataset.json",
    ):
        scored |= {
            c["input"].strip()
            for c in json.loads(path.read_text(encoding="utf-8"))["cases"]
        }

    _, examples = CANDIDATES[key]
    for case_text in scored:
        assert case_text not in examples
