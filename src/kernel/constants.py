"""Fixed values with no home in `config/models.yaml` — never a config knob
that would vary per agent or per environment.
"""

# End-to-end per-request latency budget. Router's own 10 s leg
# (config/models.yaml) is the only component named yet; later agents derive
# their leg deadline from what remains of this budget rather than reading
# their own timeout independently.
REQUEST_LATENCY_BUDGET_SECONDS: float = 30.0

# LangGraph's own default is 25, unnamed anywhere in this project until now
# — an unnamed default means a runaway graph surfaces as a framework
# traceback instead of an escalation.
GRAPH_RECURSION_LIMIT: int = 25

# The input filter's length cap. Roughly 1000 tokens of headroom for a
# support message — well above any legitimate customer question, and small
# enough that a request this long is itself a signal worth rejecting before
# it reaches an LLM call.
MAX_INPUT_CHARS: int = 4000

# A developer-picked safety cap, not a task threshold — bounds how many real
# Telegram sends one process lifetime can make, so a runaway loop or a large
# automated run cannot spam the test channel unboundedly.
#
# Raised 5 -> 100 once already (2026-08-29, docs/decisions.md #82): 5 was
# sized for a single manual smoke test, not a long-lived dev process, and
# silently stopped real sends mid-session with no visible reason. That fix
# also made the cap loud — `capped=True` now logs a WARNING and reaches
# `ChatResponse.escalation_capped` / the chat UI's badge instead of dying
# silently — which is precisely what the author then wanted to exercise.
# Set back to 5, deliberately, to test that visibility path itself: with
# the cap this tight it is reachable in a normal manual session instead of
# only after a long automated run. Raise back to 100 (or beyond) once that
# verification is done — this value is expected to move again, not a
# settled number either way.
MAX_ESCALATION_SENDS_PER_PROCESS: int = 5

# Per-session cap, smaller than the process-wide one above — a single
# customer session escalating repeatedly is itself a signal, not just
# aggregate process load.
MAX_ESCALATION_SENDS_PER_SESSION: int = 2

# Telegram's own documented per-message character limit (sendMessage) —
# text is truncated to this before a real send, never rejected outright.
TELEGRAM_MAX_MESSAGE_CHARS: int = 4096

# A developer-picked cap on session memory, not a task threshold — bounds
# how many prior turns' worth of tokens ride along on every Router/Docs/
# Web Search prompt in a long-running session (docs/decisions.md #77).
MAX_HISTORY_TURNS: int = 5
