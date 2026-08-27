"""The four mandatory Pydantic models (task §6) plus `Source`.

`Source` is not one of the four mandatory models — it is the element type
of `DocsResponse.sources` / `WebSearchResponse.sources`, structured instead
of `list[str]`: task §6 asks only for "sources", but a bare string can
neither carry a retrieval timestamp (staleness) nor populate DeepEval's
`retrieval_context` for `FaithfulnessMetric`.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Category = Literal["product", "general", "critical"]
Urgency = Literal["low", "medium", "critical"]


class Source(BaseModel):
    """One piece of evidence behind an agent's answer.

    Parameters
    ----------
    ref : str
        A URL, document id, or MCP tool-call identifier — whatever
        identifies where this came from.
    retrieved_at : datetime
        When this value was fetched. A quoted price or availability fact
        is wrong the moment it goes stale; carrying this lets an agent's
        prompt say "станом на …" instead of stating a fact as if it were
        current forever.
    version : str, default=""
        A rule version or document version, when the source has one (task
        §5: "every knowledge-base document has a source, retrieval date,
        rule version"). Empty for sources with no versioning concept (a
        web search result).
    """

    ref: str
    retrieved_at: datetime
    version: str = ""


class ClassificationOutput(BaseModel):
    """Router Agent's structured output (task §6). Router has no tools and
    no confidence field — task §6 defines confidence only on
    `DocsResponse`/`WebSearchResponse`.

    Parameters
    ----------
    category : {"product", "general", "critical"}
    urgency : {"low", "medium", "critical"}
    language : str
        ISO 639-1 code (the seeded `supportflow/router` prompt instructs
        this format explicitly). Self-reported by the same LLM that
        classifies the message — an accepted trade-off, not a
        proven-accurate signal.
    """

    category: Category
    urgency: Urgency
    language: str


class DocsResponse(BaseModel):
    """Docs Agent's structured output (task §6).

    Parameters
    ----------
    answer : str
    sources : list[Source], default=[]
    confidence : float
        0 to 1 (task §6). Self-reported by the answering LLM — an
        unvalidated gate until measured against judged correctness.
    """

    answer: str
    sources: list[Source] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class WebSearchResponse(BaseModel):
    """Web Search Agent's structured output (task §6). Same shape as
    `DocsResponse` — both are "grounded answer + its evidence + how sure".
    """

    answer: str
    sources: list[Source] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class EscalationOutput(BaseModel):
    """Escalation Agent's structured output (task §6).

    Parameters
    ----------
    summary : str
        Short description of the case, for an operator's first glance.
    category : {"product", "general", "critical"}
    customer_message : str
        What the customer is told is happening next. The seeded
        `supportflow/escalation` prompt forbids full address/phone/email/
        payment data here — a prompt instruction, not a control; the
        deterministic filter runs over this field before it is written or
        sent.
    attempted_resolution : str
        What was already tried, so an operator never re-derives it.
    """

    summary: str
    category: Category
    customer_message: str
    attempted_resolution: str
