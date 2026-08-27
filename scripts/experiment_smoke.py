"""Dry-run ONE case through the live system before spending a full
experiment on a set, and print what a scoring run depends on.

No experiment starts with a full run. `meta_prompt_docs.py` alone is >=48
real Docs calls plus >=48 judge calls per comparison; discovering there
that the candidate prompt was never picked up, or that an evaluator
scored the wrong span, wastes all of it — and the aggregate will not say
*which* thing broke. One case costs one request and answers that.

Mirrors `scripts/observability_smoke.py`'s live read-back pattern: Langfuse
ingestion is asynchronous, so the trace is polled with a deadline rather
than read once and declared missing.

Needs the launcher and the API running — unlike the offline gate, this
deliberately exercises the real A2A hop.

    TRACING_ENABLED=true .venv/Scripts/python scripts/experiment_smoke.py \\
        "Чи є у вас безлактозне молоко?"

Go/no-go: every metric the experiment will report produces exactly one
score on this trace, from the expected evaluator, on the expected span.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from src.kernel.settings import settings  # noqa: E402

_DEFAULT_MESSAGE = "Чи є у вас безлактозне молоко? Яка ціна?"
# Langfuse ingests asynchronously; a single immediate read would report a
# healthy trace as missing.
_READ_BACK_DEADLINE_SECONDS = 45
_POLL_INTERVAL_SECONDS = 3


def _auth() -> tuple[str, str]:
    return settings.langfuse_public_key, settings.langfuse_secret_key


def _fetch_trace(trace_id: str) -> dict | None:
    response = httpx.get(
        f"{settings.langfuse_base_url}/api/public/traces/{trace_id}",
        auth=_auth(),
        timeout=20,
    )
    return response.json() if response.status_code == 200 else None


def _await_trace(trace_id: str) -> dict | None:
    deadline = time.monotonic() + _READ_BACK_DEADLINE_SECONDS
    while time.monotonic() < deadline:
        trace = _fetch_trace(trace_id)
        if trace is not None and trace.get("observations"):
            return trace
        time.sleep(_POLL_INTERVAL_SECONDS)
    return None


def _report(trace: dict) -> None:
    print(f"\ntags: {trace.get('tags') or '(none — EXPERIMENT is unset)'}")

    scores = trace.get("scores") or []
    print(f"\nscores ({len(scores)}):")
    for score in scores:
        # The evaluator name and the span it landed on are the two things
        # a wrong variable mapping or filter shows up in.
        print(
            f"  {score.get('name')} = {score.get('value')} "
            f"on {score.get('observationId') or 'trace'}"
        )
    if not scores:
        print("  none yet — evaluators run asynchronously; re-run to re-read")

    print("\ngenerations:")
    for obs in trace.get("observations") or []:
        if obs.get("type") != "GENERATION":
            continue
        usage = obs.get("usageDetails") or {}
        # Few-shot examples in a prompt are only affordable if the stable
        # prefix is actually cached — verified here, not assumed.
        cached = usage.get("cache_read_input_tokens") or usage.get("cached_tokens") or 0
        prompt_name = obs.get("promptName") or "(not from Prompt Management)"
        print(
            f"  {obs.get('name')}: prompt={prompt_name} "
            f"v{obs.get('promptVersion')} | in={usage.get('input')} "
            f"cached={cached} out={usage.get('output')}"
        )


def main() -> None:
    sys.stdout.reconfigure(errors="replace")
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        print("Langfuse keys not set — nothing to read back.")
        return

    message = sys.argv[1] if len(sys.argv) > 1 else _DEFAULT_MESSAGE
    print(f"sending: {message}")
    response = httpx.post(
        "http://localhost:8000/chat", json={"message": message}, timeout=180
    )
    response.raise_for_status()
    body = response.json()
    trace_id = body.get("trace_id")
    print(f"answered in {body['elapsed_ms']}ms, trace {trace_id}")

    if not trace_id:
        # /chat always returns one; an absent field means an older API
        # build is running, not that tracing is off.
        print("no trace_id in the response — restart the API process")
        return

    trace = _await_trace(trace_id)
    if trace is None:
        print(f"trace {trace_id} did not appear within {_READ_BACK_DEADLINE_SECONDS}s")
        return
    _report(trace)
    print(
        "\nGo/no-go: exactly one score per expected evaluator, each on the "
        "span it belongs to, and the prompt version is the one under test. "
        "Anything else — fix it before running the full set."
    )


if __name__ == "__main__":
    main()
