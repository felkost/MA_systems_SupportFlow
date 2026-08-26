"""FastAPI API for the React chat frontend (task §8) — the only public HTTP
entry point in this project. CORS is restricted to `settings.frontend_origin`
(task §8: "CORS open only to the local frontend").

Every `/chat` call runs `handle_request` inside `_bypass_hitl()`: Escalation's
confirmation step (`escalation_agent.py::_default_confirm`) blocks on
`input()`, which is only safe when a human is at this process's own console
— never true for a uvicorn worker thread serving a browser request. This is
the same "automated run" case `tests/evaluation/harness.py`'s own
`_bypass_hitl()` and `scripts/golden_dataset_smoke.py` already handle
(CLAUDE.md's "explicit bypass flag in automated runs") — scoped with
try/finally, never a permanent module-level mutation, since `settings` is a
process-wide singleton other code (and, in the test process, other test
modules) also reads. `ALLOW_REAL_SEND` is untouched, so this alone never
triggers a real Telegram send.
"""

import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import src.application.supervisor as supervisor
from src.domain.schemas import Source
from src.domain.state import ErrorType
from src.infrastructure.observability import new_trace_id
from src.kernel.constants import MAX_INPUT_CHARS
from src.kernel.settings import settings


@contextmanager
def _bypass_hitl() -> Iterator[None]:
    original = settings.bypass_hitl
    settings.bypass_hitl = True
    try:
        yield
    finally:
        settings.bypass_hitl = original


_REJECTION_MESSAGES: dict[ErrorType, str] = {
    "empty_input": "Будь ласка, напишіть ваше запитання.",
    "input_too_long": "Повідомлення занадто довге — скоротіть його, будь ласка.",
    "unsupported_language": (
        "Наразі ми відповідаємо українською, російською та англійською мовами."
    ),
    "out_of_domain": "Це питання виходить за межі того, з чим ми можемо допомогти.",
    "forbidden_content": (
        "Не вдалося обробити це повідомлення — спробуйте, будь ласка, переформулювати."
    ),
}
_DEFAULT_REJECTION_MESSAGE = _REJECTION_MESSAGES["forbidden_content"]

app = FastAPI(title="SupportFlow API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    message: str = Field(max_length=MAX_INPUT_CHARS)
    session_id: str | None = None


class ChatResponse(BaseModel):
    answer: str
    sources: list[Source] = Field(default_factory=list)
    confidence: float | None = None
    category: str | None = None
    escalated: bool
    report_written: bool = False
    telegram_sent: bool = False
    elapsed_ms: int
    session_id: str


class RealSendToggleRequest(BaseModel):
    enabled: bool


@app.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True, "allow_real_send": settings.allow_real_send}


@app.post("/admin/real-send")
def set_real_send(payload: RealSendToggleRequest) -> dict[str, bool]:
    """Task §8's own optional "Settings page" extension: a runtime toggle
    for `ALLOW_REAL_SEND`, so a demo can turn a real Telegram send on and
    off from the UI without restarting the process. Mutates the shared
    `settings` singleton directly (unlike `_bypass_hitl`'s per-request
    scoping) — the whole point here is that the state persists until
    toggled again, matching the author's own request ("turn it on, it
    works with Telegram; turn it off, it doesn't").

    Carries the same risk `/chat`'s own no-auth design already accepts
    (decision 5): this endpoint has no auth either, only CORS restricting
    it to the local frontend origin — acceptable for a local demo, not a
    hosted deployment.
    """
    settings.allow_real_send = payload.enabled
    return {"allow_real_send": settings.allow_real_send}


@app.post("/chat")
def chat(payload: ChatRequest) -> ChatResponse:
    request_id = uuid.uuid4().hex
    session_id = payload.session_id or uuid.uuid4().hex
    trace_id = new_trace_id()

    started = time.monotonic()
    with _bypass_hitl():
        state = supervisor.handle_request(
            payload.message, request_id, session_id, trace_id
        )
    elapsed_ms = round((time.monotonic() - started) * 1000)

    if state["next_action"] == "reject":
        error = state["errors"][0] if state["errors"] else "forbidden_content"
        answer = _REJECTION_MESSAGES.get(error, _DEFAULT_REJECTION_MESSAGE)
    else:
        answer = state["answer"] or _DEFAULT_REJECTION_MESSAGE

    response_source = state["docs_response"] or state["web_search_response"]
    classification = state["classification"]

    return ChatResponse(
        answer=answer,
        sources=response_source.sources if response_source is not None else [],
        confidence=state["confidence"],
        category=classification.category if classification is not None else None,
        escalated=state["next_action"] == "escalate",
        report_written=state["report_written"],
        telegram_sent=state["telegram_sent"],
        elapsed_ms=elapsed_ms,
        session_id=session_id,
    )
