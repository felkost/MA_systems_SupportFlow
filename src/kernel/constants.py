"""Fixed values with no home in `config/models.yaml` — never a config knob
that would vary per agent or per environment (see docs/decisions.md #11, #12).
"""

# docs/decisions.md #11: the end-to-end per-request latency budget. Router's
# own 10 s leg (config/models.yaml) is the only component named yet; later
# stages derive their leg deadline from what remains of this budget rather
# than reading their own timeout independently.
REQUEST_LATENCY_BUDGET_SECONDS: float = 30.0

# docs/decisions.md #12: LangGraph's own default is 25, unnamed anywhere in
# this project until now — an unnamed default means a runaway graph surfaces
# as a framework traceback instead of an escalation.
GRAPH_RECURSION_LIMIT: int = 25

# docs/decisions.md #10: the input filter's length cap. Roughly 1000 tokens
# of headroom for a support message — well above any legitimate customer
# question, and small enough that a request this long is itself a signal
# worth rejecting before it reaches an LLM call.
MAX_INPUT_CHARS: int = 4000

# docs/decisions.md #19 (F17): a developer-picked safety cap, not a task
# threshold — bounds how many real Telegram sends one process lifetime can
# make, so a runaway loop or a large automated run cannot spam the test
# channel unboundedly.
MAX_ESCALATION_SENDS_PER_PROCESS: int = 5

# docs/decisions.md #19 (F2): per-session cap, smaller than the process-wide
# one above — a single customer session escalating repeatedly is itself a
# signal, not just aggregate process load.
MAX_ESCALATION_SENDS_PER_SESSION: int = 2

# Telegram's own documented per-message character limit (sendMessage) —
# text is truncated to this before a real send, never rejected outright.
TELEGRAM_MAX_MESSAGE_CHARS: int = 4096
