"""Round-trip `scripts/sync_dataset.py` against a real Langfuse project.

Marked `eval` so `pytest --cov=src` stays offline (pyproject.toml's
`addopts = "-m 'not eval'"`). Run deliberately:

    pytest -m eval tests/infrastructure/test_dataset_sync.py
"""

import sys
from pathlib import Path

import pytest
from langfuse import Langfuse

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.sync_dataset import (  # noqa: E402
    SPECS,
    build_items,
    content_sha,
    sync_one,
)
from src.kernel.settings import settings  # noqa: E402

pytestmark = pytest.mark.eval

_GOLDEN = next(s for s in SPECS if s.dataset_name == "supportflow/golden")


@pytest.fixture(scope="module")
def client() -> Langfuse:
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        pytest.skip("Langfuse keys not configured")
    return Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        host=settings.langfuse_base_url,
    )


def test_sync_uploads_every_local_case(client: Langfuse) -> None:
    sync_one(client, _GOLDEN)
    client.flush()

    remote = client.get_dataset(_GOLDEN.dataset_name)
    local_ids = {item["id"] for item in build_items(_GOLDEN)}
    active = {i.id for i in remote.items if i.status == "ACTIVE"}

    assert local_ids <= active


def test_second_sync_short_circuits_on_unchanged_hash(client: Langfuse) -> None:
    """The guard that keeps a post-commit hook from feeling broken.

    Without it every commit touching an evaluation set costs 38 upserts.
    """
    sync_one(client, _GOLDEN)
    client.flush()

    assert sync_one(client, _GOLDEN) is False


def test_recorded_version_matches_the_local_file(client: Langfuse) -> None:
    # The hash a report cites must name the bytes that produced the run.
    sync_one(client, _GOLDEN)
    client.flush()

    dataset = client.api.datasets.get(dataset_name=_GOLDEN.dataset_name)

    assert dataset.metadata["content_sha"] == content_sha(_GOLDEN.path)
