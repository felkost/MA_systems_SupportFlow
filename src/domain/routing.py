"""Pure routing rules (task §7 steps 3-6). No I/O, no LLM call — this is
where route *correctness* is tested exhaustively, independent of the graph
that later dispatches on it (docs/decisions.md #16).
"""

from src.domain.schemas import ClassificationOutput
from src.domain.state import NextAction


def decide_route(classification: ClassificationOutput) -> NextAction:
    """Task §7 steps 3-5: where a classified request goes next.

    Parameters
    ----------
    classification : ClassificationOutput
        Router's validated output. A classification that failed to
        validate never reaches this function — that is
        `router_agent.py`'s fail-closed path (docs/decisions.md #12), not
        a routing decision.

    Returns
    -------
    {"escalate", "docs", "web_search"}
        `"escalate"` for a critical category or critical urgency (task §7
        step 3 — checked first and unconditionally, since a missed
        critical case is worse than an over-escalated one, per the
        Router's own seeded prompt). `"docs"` for `category="product"`
        (step 4). `"web_search"` for `category="general"` (step 5).

    Examples
    --------
    >>> args = dict(category="critical", urgency="low", language="uk")
    >>> decide_route(ClassificationOutput(**args))
    'escalate'
    >>> args = dict(category="product", urgency="critical", language="uk")
    >>> decide_route(ClassificationOutput(**args))
    'escalate'
    >>> args = dict(category="product", urgency="low", language="uk")
    >>> decide_route(ClassificationOutput(**args))
    'docs'
    >>> args = dict(category="general", urgency="medium", language="uk")
    >>> decide_route(ClassificationOutput(**args))
    'web_search'
    """
    if classification.category == "critical" or classification.urgency == "critical":
        return "escalate"
    if classification.category == "product":
        return "docs"
    return "web_search"


def confidence_below_threshold(confidence: float, threshold: float | None) -> bool:
    """Task §7 step 6, the confidence half: whether a downstream answer's
    confidence should trigger escalation.

    Parameters
    ----------
    confidence : float
        A `DocsResponse`/`WebSearchResponse.confidence` value, 0 to 1.
    threshold : float or None
        From `AgentModelConfig.confidence_threshold`. `None` means no gate
        is configured for this agent (Router, Escalation, Supervisor all
        have no confidence output at all — task §6).

    Returns
    -------
    bool
        `False` when `threshold` is `None` — nothing to compare against.

    Notes
    -----
    This gate rests on a self-reported number the answering LLM invents
    about itself; whether it correlates with actual correctness is an
    unproven assumption this function cannot resolve, only apply
    (docs/decisions.md #12's note on the confidence gate, F19 — validating
    it is Stage 4's job, by plotting reported confidence against judged
    correctness).
    """
    if threshold is None:
        return False
    return confidence < threshold
