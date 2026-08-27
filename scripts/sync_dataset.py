"""Push this project's three evaluation sets to Langfuse Datasets, so a
measured number can name the exact set version that produced it.

Local JSON stays the single source of truth; Langfuse holds a mirror. The
runners are deliberately NOT switched to `get_dataset()` — they run fully
offline today (`tests/evaluation/harness.py` patches the A2A hop so the
gate needs no processes), and making the gate depend on a network fetch
would trade that away for nothing the local file does not already give.
The Langfuse copy exists to group runs in the UI and to be the
addressable, point-in-time-recoverable record a report's content hash
refers to.

Verified live against the installed `langfuse==4.14.4`, not assumed from
docs: `create_dataset_item` upserts by `id`, so re-running is idempotent
rather than duplicating; `DatasetItem` carries no per-item version field,
`get_dataset(name, version=<datetime>)` is Langfuse's own point-in-time
mechanism; and the API exposes no item delete, so removal is
`status=ARCHIVED`.

Run manually by the project author, or automatically by `hooks/post-commit`
(needs LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY in .env):

    .venv/Scripts/python scripts/sync_dataset.py
"""

import hashlib
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langfuse import Langfuse  # noqa: E402
from langfuse.api.commons.types.dataset_status import (  # noqa: E402
    DatasetStatus,
)

from src.kernel.settings import PROJECT_ROOT, settings  # noqa: E402

# A string, in every set that has one. Declared rather than inferred so a
# malformed case is rejected at sync time instead of mid-run.
_STRING_SCHEMA = {"type": "string"}
_ROUTER_LABEL_SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string"},
        "urgency": {"type": "string"},
        "language": {"type": "string"},
    },
    "required": ["category", "urgency", "language"],
}


@dataclass(frozen=True)
class DatasetSpec:
    """One local file and the Langfuse dataset it mirrors.

    Parameters
    ----------
    path : Path
        Local JSON, the source of truth. Read whole — its sha256 is the
        version string a report cites.
    dataset_name : str
        Slash-separated, so all three group into one `supportflow` folder
        in the UI, matching the naming the five prompts already use.
    description : str
    expected_output : Callable or None
        Builds a case's `expected_output`. `None` for a set that has no
        ground-truth answer, which is not the same as an empty one.
    expected_output_schema : dict or None
        Per dataset, never one shared pair: the three sets genuinely do
        not share a shape.
    """

    path: Path
    dataset_name: str
    description: str
    expected_output: Callable[[dict[str, Any]], Any] | None
    expected_output_schema: dict[str, Any] | None
    metadata_excludes: frozenset[str] = field(default_factory=frozenset)


SPECS = (
    DatasetSpec(
        path=PROJECT_ROOT / "evals" / "golden_dataset.json",
        dataset_name="supportflow/golden",
        description="Golden dataset: 6 typical + 6 edge + 6 failure cases.",
        expected_output=lambda case: case["expected_output"],
        expected_output_schema=_STRING_SCHEMA,
        metadata_excludes=frozenset({"id", "input", "expected_output"}),
    ),
    DatasetSpec(
        path=PROJECT_ROOT / "evals" / "docs_optimization_set.json",
        dataset_name="supportflow/docs-optimization",
        description="Docs Agent prompt-optimization set; no ground-truth answer.",
        # This set has no `expected_output` field at all — it is scored by
        # a GEval judge, not by comparison against a reference answer.
        expected_output=None,
        expected_output_schema=None,
        metadata_excludes=frozenset({"id", "input"}),
    ),
    DatasetSpec(
        path=PROJECT_ROOT / "tests" / "fixtures" / "router_classification_cases.json",
        dataset_name="supportflow/router-classification",
        description="Held-out Router gate set, labelled before any prompt edit.",
        expected_output=lambda case: {
            "category": case["category"],
            "urgency": case["urgency"],
            "language": case["language"],
        },
        expected_output_schema=_ROUTER_LABEL_SCHEMA,
        metadata_excludes=frozenset({"id", "input"}),
    ),
)


def content_sha(path: Path) -> str:
    """sha256 of the file's raw bytes — the dataset's version string.

    Notes
    -----
    Hashing bytes rather than the parsed structure is deliberate: a
    reformatting that changes no case still produces a new version, which
    is the safe direction to be wrong in. A datetime alone does not
    survive being quoted in a report; this does.
    """
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_items(spec: DatasetSpec) -> list[dict[str, Any]]:
    """Map local cases onto Langfuse dataset items.

    Every field other than `id`, `input` and the expected output goes to
    item `metadata`, which Langfuse does not schema-validate — the local
    structural tests in `tests/test_golden_dataset.py` stay the real check
    on those.
    """
    cases = json.loads(spec.path.read_text(encoding="utf-8"))["cases"]
    return [
        {
            "id": case["id"],
            "input": case["input"],
            "expected_output": (
                spec.expected_output(case) if spec.expected_output else None
            ),
            "metadata": {
                key: value
                for key, value in case.items()
                if key not in spec.metadata_excludes
            },
        }
        for case in cases
    ]


def stale_item_ids(remote_ids: set[str], local_ids: set[str]) -> set[str]:
    """Items on Langfuse that no longer exist locally.

    They are archived, never deleted — the API exposes no delete, and an
    archived item keeps the history that point-in-time versioning reads.
    """
    return remote_ids - local_ids


def _remote_sha(client: Langfuse, name: str) -> str | None:
    try:
        dataset = client.api.datasets.get(dataset_name=name)
    except Exception:  # noqa: BLE001 — a missing dataset is the normal first run
        return None
    metadata = getattr(dataset, "metadata", None) or {}
    sha = metadata.get("content_sha") if isinstance(metadata, dict) else None
    return str(sha) if sha else None


def sync_one(client: Langfuse, spec: DatasetSpec) -> bool:
    """Sync one set. Returns `True` if it wrote anything.

    Short-circuits on an unchanged hash: without it every sync is 38
    upsert calls across the three sets, on every commit that touches any
    of them.
    """
    local_sha = content_sha(spec.path)
    if _remote_sha(client, spec.dataset_name) == local_sha:
        print(f"{spec.dataset_name}: unchanged ({local_sha[:12]})")
        return False

    items = build_items(spec)
    client.create_dataset(
        name=spec.dataset_name,
        description=spec.description,
        metadata={
            "content_sha": local_sha,
            "synced_at": datetime.now(timezone.utc).isoformat(),
            "source_path": str(spec.path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        },
        input_schema=_STRING_SCHEMA,
        expected_output_schema=spec.expected_output_schema,
    )
    for item in items:
        client.create_dataset_item(dataset_name=spec.dataset_name, **item)

    local_ids = {item["id"] for item in items}
    remote = client.get_dataset(spec.dataset_name)
    by_id = {i.id: i for i in remote.items}
    stale = stale_item_ids(set(by_id), local_ids)
    for item_id in sorted(stale):
        # Carry the item's own fields back: `create_dataset_item` upserts
        # the whole item, so archiving by id alone would blank the input
        # and expected output it is meant to preserve.
        previous = by_id[item_id]
        client.create_dataset_item(
            dataset_name=spec.dataset_name,
            id=item_id,
            input=previous.input,
            expected_output=previous.expected_output,
            metadata=previous.metadata,
            status=DatasetStatus.ARCHIVED,
        )

    print(f"{spec.dataset_name}: {len(items)} items, sha {local_sha[:12]}")
    if stale:
        print(f"  archived (absent locally): {', '.join(sorted(stale))}")
    return True


def main() -> None:
    sys.stdout.reconfigure(errors="replace")
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        # Not an error: an offline contributor editing a case must not be
        # blocked, and the post-commit hook must not shout at them.
        print("Langfuse keys not set — skipping dataset sync.")
        return

    client = Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        host=settings.langfuse_base_url,
    )
    if not any([sync_one(client, spec) for spec in SPECS]):
        print("All datasets already in sync.")
    client.flush()


if __name__ == "__main__":
    main()
