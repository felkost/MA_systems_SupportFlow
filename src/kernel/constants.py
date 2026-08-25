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
