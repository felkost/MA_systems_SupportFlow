"""One-iteration meta-prompting cycle for Docs Agent's own prompt,
triggered because Support Resolution Quality ~0.61 (n=8) measured on
`evals/docs_optimization_set.json` against `supportflow/docs`'s current
`production` prompt was still below the 0.70 orientation floor; judges
consistently flag one pattern — relevant, honest answers with no concrete
next step.

One round only, by design. This script measures `production`, sends the
current prompt plus the judges' own low-score reasoning to a strong,
family-independent model for a single rewrite (Docs Agent's own model is
`openai/gpt-5.6-luna`; the rewrite uses `anthropic/claude-sonnet-5` — same
family as the judge but a stronger tier), seeds the result to Langfuse
under the `candidate` label (never `production`), re-measures against
`candidate`, and prints both side by side. It never promotes `candidate`
to `production` — that label swap is the author's own call, made manually
after reading this script's output.

    .venv/Scripts/python scripts/meta_prompt_docs.py

Paid, live: >=3 runs x 8 cases x 2 prompt versions = >=48 real Docs Agent
calls (search-term extraction + compose), >=48 real judge (GEval) calls,
plus one real rewrite call to the meta-prompt model. State cost/time and
get the author's permission before running — this script does not ask
for you.
"""

import asyncio
import json
import statistics
import sys
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deepeval.models import OpenRouterModel  # noqa: E402
from deepeval.test_case import LLMTestCase  # noqa: E402
from langfuse import Langfuse  # noqa: E402

from src.application.docs_agent import run_docs_agent  # noqa: E402
from src.infrastructure.prompts import get_prompt  # noqa: E402
from src.kernel.settings import PROJECT_ROOT, settings  # noqa: E402
from tests.evaluation.harness import (  # noqa: E402
    _judge_model,
    _support_resolution_quality_metric,
)

OPTIMIZATION_SET_PATH = Path(PROJECT_ROOT) / "evals" / "docs_optimization_set.json"
N_RUNS = 3  # n>=3 discipline — a single run is not evidence.
LOW_SCORE_THRESHOLD = 0.70  # the same orientation floor used elsewhere.

# Family-independent from Docs Agent's own model (openai/gpt-5.6-luna) —
# same family as the judge but a stronger tier for a one-off rewrite,
# confirmed live in the project's own catalogue.
META_PROMPT_MODEL = "anthropic/claude-sonnet-5"
DOCS_PROMPT_NAME = "supportflow/docs"


def _load_cases() -> list[dict[str, Any]]:
    raw = json.loads(OPTIMIZATION_SET_PATH.read_text(encoding="utf-8"))
    return list(raw["cases"])


def _prompt_fn_for_label(label: str) -> Callable[[str], tuple[str, int]]:
    def _fn(name: str, label: str = label) -> tuple[str, int]:
        return get_prompt(name, label=label)

    return _fn


def _score_case(case: dict[str, Any], label: str) -> dict[str, Any]:
    result = asyncio.run(
        run_docs_agent(case["input"], prompt_fn=_prompt_fn_for_label(label))
    )
    test_case = LLMTestCase(
        input=case["input"],
        actual_output=result.response.answer,
        retrieval_context=result.retrieval_context,
    )
    metric = _support_resolution_quality_metric(_judge_model())
    score = metric.measure(test_case)
    return {"score": score, "reason": metric.reason, "answer": result.response.answer}


def _run_set(label: str) -> dict[str, list[dict[str, Any]]]:
    """>=3 runs per case — a single run is not evidence."""
    per_case: dict[str, list[dict[str, Any]]] = {}
    for case in _load_cases():
        print(f"  {case['id']} x{N_RUNS} runs against '{label}'...")
        per_case[case["id"]] = [_score_case(case, label) for _ in range(N_RUNS)]
    return per_case


def _mean_and_range(
    per_case: dict[str, list[dict[str, Any]]],
) -> tuple[float, float, float]:
    scores = [run["score"] for runs in per_case.values() for run in runs]
    return statistics.mean(scores), min(scores), max(scores)


def _collect_low_score_reasoning(per_case: dict[str, list[dict[str, Any]]]) -> str:
    lines = []
    for case_id, runs in per_case.items():
        for run in runs:
            if run["score"] < LOW_SCORE_THRESHOLD:
                lines.append(f"- {case_id} (score {run['score']:.2f}): {run['reason']}")
    return "\n".join(lines) if lines else "(no runs scored below the floor)"


def _build_candidate_prompt(current_prompt: str, low_score_reasoning: str) -> str:
    model = OpenRouterModel(
        model=META_PROMPT_MODEL, api_key=settings.openrouter_api_key
    )
    instruction = (
        "You are improving a system prompt for a customer-support LLM agent "
        "(a retail knowledge-base assistant). Below is the CURRENT prompt, "
        "followed by judge feedback on specific low-scoring answers it "
        "produced. The judges consistently flag one pattern: answers are "
        "relevant and honest, but too vague about the concrete next step for "
        "the customer. Rewrite the prompt to fix this pattern specifically — "
        "make it require a concrete, specific next step in every answer — "
        "without changing the prompt's overall structure, its Output Format "
        "section, or any of its existing safety constraints (grounding in "
        "retrieved content only, no invented facts, honesty about "
        "uncertainty, treating customer/retrieved text as data not "
        "instructions). Return ONLY the rewritten prompt text, nothing else "
        "— no preamble, no explanation.\n\n"
        f"CURRENT PROMPT:\n{current_prompt}\n\n"
        f"LOW-SCORING JUDGE FEEDBACK:\n{low_score_reasoning}\n"
    )
    text, _cost = model.generate(instruction)
    return str(text).strip()


def main() -> None:
    print(
        f"Meta-prompting Docs Agent's prompt over {len(_load_cases())} cases, "
        f"{N_RUNS} runs each, against 'production' then a rewritten "
        "'candidate' — real OpenRouter/judge calls both times. Confirm this "
        "was run with the author's permission before continuing."
    )

    print("\n== Measuring 'production' ==")
    production_runs = _run_set("production")
    prod_mean, prod_min, prod_max = _mean_and_range(production_runs)
    print(f"production: mean={prod_mean:.3f} range=[{prod_min:.2f}, {prod_max:.2f}]")

    current_prompt, _version = get_prompt(DOCS_PROMPT_NAME, label="production")
    low_score_reasoning = _collect_low_score_reasoning(production_runs)

    print("\n== Building candidate prompt (one strong-model rewrite call) ==")
    candidate_prompt = _build_candidate_prompt(current_prompt, low_score_reasoning)

    langfuse = Langfuse()
    langfuse.create_prompt(
        name=DOCS_PROMPT_NAME,
        prompt=candidate_prompt,
        labels=["candidate"],  # never "production" — that swap is manual.
        type="text",
    )
    langfuse.flush()
    print(f"Seeded candidate prompt to Langfuse ({DOCS_PROMPT_NAME}, label: candidate)")

    print("\n== Measuring 'candidate' ==")
    candidate_runs = _run_set("candidate")
    cand_mean, cand_min, cand_max = _mean_and_range(candidate_runs)
    print(f"candidate:  mean={cand_mean:.3f} range=[{cand_min:.2f}, {cand_max:.2f}]")

    print("\n== Comparison (production -> candidate) ==")
    delta = cand_mean - prod_mean
    print(f"  mean:  {prod_mean:.3f} -> {cand_mean:.3f}  (delta {delta:+.3f})")
    print(
        f"  range: [{prod_min:.2f}, {prod_max:.2f}] -> [{cand_min:.2f}, {cand_max:.2f}]"
    )
    for case_id in production_runs:
        p_scores = [r["score"] for r in production_runs[case_id]]
        c_scores = [r["score"] for r in candidate_runs[case_id]]
        print(
            f"  {case_id}: production={[round(s, 2) for s in p_scores]} "
            f"candidate={[round(s, 2) for s in c_scores]}"
        )

    output_path = Path(PROJECT_ROOT) / "output" / "meta_prompt_docs_comparison.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            {
                "production": production_runs,
                "candidate": candidate_runs,
                "candidate_prompt": candidate_prompt,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"\nWrote {output_path}")
    print(
        "\nThis script never promotes 'candidate' to 'production' — that "
        "label swap is a manual decision for the author to make after "
        "reading the comparison above, not something this script decides."
    )


if __name__ == "__main__":
    main()
