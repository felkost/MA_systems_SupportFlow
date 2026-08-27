"""`scripts/sync_dataset.py`'s pure logic: case-to-item mapping, the
content hash that names a dataset version, and which items get archived.

Offline by construction — no Langfuse client is built here. The network
round-trip is covered separately, behind the `eval` marker.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.sync_dataset import (  # noqa: E402
    SPECS,
    build_items,
    content_sha,
    stale_item_ids,
)

_BY_NAME = {spec.dataset_name: spec for spec in SPECS}


def test_every_set_yields_unique_ids() -> None:
    # The upsert key. A duplicate would silently overwrite a sibling case
    # instead of adding one, and the loss would be invisible in a count.
    for spec in SPECS:
        ids = [item["id"] for item in build_items(spec)]
        assert len(ids) == len(set(ids)), spec.dataset_name


@pytest.mark.parametrize(
    "name,expected_count",
    [("supportflow/golden", 18), ("supportflow/router-classification", 12)],
)
def test_expected_case_counts(name: str, expected_count: int) -> None:
    assert len(build_items(_BY_NAME[name])) == expected_count


def test_golden_case_maps_answer_to_expected_output() -> None:
    item = build_items(_BY_NAME["supportflow/golden"])[0]

    assert isinstance(item["expected_output"], str)
    # Routing labels are metadata, not the expected answer.
    assert "expected_route" in item["metadata"]
    assert "expected_output" not in item["metadata"]


def test_docs_optimization_has_no_expected_output() -> None:
    """Absent ground truth must stay `None`, not become an empty string.

    This set is scored by a GEval judge; an empty reference answer would
    look like a real one to anything reading the dataset later.
    """
    item = build_items(_BY_NAME["supportflow/docs-optimization"])[0]

    assert item["expected_output"] is None
    assert "kb_reference" in item["metadata"]


def test_router_expected_output_is_the_full_label_object() -> None:
    item = build_items(_BY_NAME["supportflow/router-classification"])[0]

    assert set(item["expected_output"]) == {"category", "urgency", "language"}


def test_content_sha_changes_with_content(tmp_path: Path) -> None:
    first = tmp_path / "a.json"
    second = tmp_path / "b.json"
    first.write_text('{"cases": []}', encoding="utf-8")
    second.write_text('{"cases": [] }', encoding="utf-8")

    assert content_sha(first) == content_sha(first)
    assert content_sha(first) != content_sha(second)


def test_only_locally_absent_ids_are_archived() -> None:
    stale = stale_item_ids({"a", "b", "c"}, {"a", "b"})

    assert stale == {"c"}


def test_nothing_is_archived_when_local_has_extra_ids() -> None:
    # A newly added local case is an upsert, never an archive.
    assert stale_item_ids({"a"}, {"a", "b"}) == set()
