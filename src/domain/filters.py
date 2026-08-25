"""The input filter (task §7 step 1): language, domain bounds, and
personal/forbidden data — all three checks, before Router
(docs/decisions.md #10). Pure, deterministic, no I/O and no LLM call, so it
belongs in `domain`.

Two independent things happen here, and they are not the same gate:

- **Rejection** (`InputFilterResult.error`) — empty input, input over the
  length cap, or a language the project does not support. These short-
  circuit before any Router LLM call is made.
- **Masking** (`mask_pii`) — always applied to build the text that ever
  enters state or a trace (docs/decisions.md #14: masking is a precondition
  of entering the graph, never a node inside it). A message containing a
  phone number is not rejected outright — a customer giving a callback
  number is a normal support interaction — it is masked before it is
  stored or traced.
"""

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

from src.domain.state import ErrorType
from src.kernel.constants import MAX_INPUT_CHARS

SupportedLanguage = Literal["uk", "ru", "en", "unsupported"]

_UK_ONLY_LETTERS = set("іїєґІЇЄҐ")
_RU_ONLY_LETTERS = set("ыъэЫЪЭ")
_CYRILLIC_RE = re.compile(r"[Ѐ-ӿ]")
_LATIN_RE = re.compile(r"[A-Za-z]")
_ALPHABETIC_RE = re.compile(r"[^\W\d_]", re.UNICODE)

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# 9-13 digits, after normalisation, is treated as a phone number
# unconditionally — Ukrainian mobiles are 10 digits (0XXXXXXXXX) or 12 with
# the country code (380XXXXXXXXX). This range is disjoint from the
# card-candidate range below on purpose, so the two checks never compete
# over the same digit run.
_PHONE_RE = re.compile(r"(?<!\d)(\+?\d{9,13})(?!\d)")
# 14-19 digits is card-shaped but ambiguous with an order/reference number
# (docs/decisions.md #10, F9) — only a Luhn-valid run is treated as a card.
# A non-Luhn run in this range is assumed to be an order number and is
# deliberately NOT flagged; that is this filter's known ceiling.
_CARD_CANDIDATE_RE = re.compile(r"(?<!\d)(\d{14,19})(?!\d)")

_DIGIT_WORDS = {
    "zero": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "нуль": "0",
    "один": "1",
    "два": "2",
    "три": "3",
    "чотири": "4",
    "п'ять": "5",
    "п’ять": "5",
    "шість": "6",
    "сім": "7",
    "вісім": "8",
    "дев'ять": "9",
    "дев’ять": "9",
}
_DIGIT_WORD_RE = re.compile(
    "|".join(re.escape(w) for w in sorted(_DIGIT_WORDS, key=len, reverse=True)),
    re.IGNORECASE,
)
_DIGIT_SEPARATOR_RE = re.compile(r"(?<=\d)[\s\-.]+(?=\d)")


def _luhn_valid(digits: str) -> bool:
    """Standard Luhn check, used only to separate a card number from an
    order number of similar length (docs/decisions.md #10).
    """
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def _canonicalise_digits(text: str) -> str:
    """NFKC-normalise, expand spelled-out digit words, then collapse
    whitespace/dashes/dots between digits — so `"0 6 7 - one two three"`
    and similar obfuscation collapse to a single scannable digit run
    (docs/decisions.md #10, F8).
    """
    normalised = unicodedata.normalize("NFKC", text)
    expanded = _DIGIT_WORD_RE.sub(
        lambda m: _DIGIT_WORDS[m.group(0).lower()], normalised
    )
    return _DIGIT_SEPARATOR_RE.sub("", expanded)


def contains_forbidden_data(text: str) -> bool:
    """Whether `text` contains an email, a phone-shaped digit run, or a
    Luhn-valid card-shaped digit run.

    Parameters
    ----------
    text : str

    Returns
    -------
    bool
    """
    normalised = unicodedata.normalize("NFKC", text)
    if _EMAIL_RE.search(normalised):
        return True
    canonical = _canonicalise_digits(text)
    if _PHONE_RE.search(canonical):
        return True
    return any(_luhn_valid(m.group(1)) for m in _CARD_CANDIDATE_RE.finditer(canonical))


def mask_pii(text: str) -> str:
    """Replace detected email/phone/card substrings with `[REDACTED]`.

    This is what builds `SupportFlowState.original_request_masked`
    (docs/decisions.md #14) — the only version of the customer's message
    that ever enters the graph or a Langfuse trace.

    Parameters
    ----------
    text : str

    Returns
    -------
    str
    """
    normalised = unicodedata.normalize("NFKC", text)
    masked = _EMAIL_RE.sub("[REDACTED]", normalised)
    canonical = _canonicalise_digits(masked)
    for match in _CARD_CANDIDATE_RE.finditer(canonical):
        if _luhn_valid(match.group(1)):
            canonical = canonical.replace(match.group(1), "[REDACTED]")
    canonical = _PHONE_RE.sub("[REDACTED]", canonical)
    return canonical


def detect_language(text: str) -> SupportedLanguage:
    """A stdlib Unicode-script heuristic — a gate ("supported / not"), not
    a classifier (docs/decisions.md #10). Router's own
    `ClassificationOutput.language` remains the fine-grained signal
    downstream; this function only decides whether the input filter lets
    a message through to Router at all.

    Parameters
    ----------
    text : str

    Returns
    -------
    {"uk", "ru", "en", "unsupported"}
        Cyrillic with a Ukrainian-only letter (і/ї/є/ґ) is `"uk"`; Cyrillic
        with a Russian-only letter (ы/ъ/э) is `"ru"`; Cyrillic with
        neither defaults to `"uk"` (the project's dominant expected
        language, docs/decisions.md #6); Latin-only is `"en"`; no
        recognisable letters at all is `"unsupported"`.
    """
    if any(ch in _UK_ONLY_LETTERS for ch in text):
        return "uk"
    if any(ch in _RU_ONLY_LETTERS for ch in text):
        return "ru"
    if _CYRILLIC_RE.search(text):
        return "uk"
    if _LATIN_RE.search(text):
        return "en"
    return "unsupported"


def is_within_domain_bounds(text: str) -> bool:
    """A coarse, deliberately weak gate: does this message contain any
    alphabetic content at all?

    Not a domain classifier — Router's own classification already
    re-covers "general" vs. genuinely out-of-scope more precisely, and
    duplicating that here would spend a check on every request for no
    added precision (docs/decisions.md #10). This only catches
    symbol-only or emoji-only spam that would otherwise reach the Router
    LLM for nothing.

    Parameters
    ----------
    text : str

    Returns
    -------
    bool
    """
    return bool(_ALPHABETIC_RE.search(text))


@dataclass(frozen=True)
class InputFilterResult:
    """Parameters
    ----------
    masked_text : str
        Always PII-masked, regardless of `error` — this is what
        `SupportFlowState.original_request_masked` is set to.
    error : ErrorType or None
        Set when the request should short-circuit before Router.
    """

    masked_text: str
    error: ErrorType | None


def run_input_filter(text: str) -> InputFilterResult:
    """Task §7 step 1, run as one entry point: empty/whitespace, length
    cap, domain bounds, then language.

    Domain bounds runs before language deliberately — both catch "no real
    content" from opposite ends (`is_within_domain_bounds` requires at
    least one alphabetic character of any script; `detect_language`
    requires a *recognised-script* one), so symbol-only or emoji-only spam
    is caught by the cheaper, more general check first, and a message with
    real but unsupported-script content (e.g. Chinese) still reaches the
    language check and is rejected there. Reversing this order would make
    `is_within_domain_bounds` unreachable: anything that has no letters at
    all already fails `detect_language` first.

    Parameters
    ----------
    text : str
        The raw customer message.

    Returns
    -------
    InputFilterResult
    """
    stripped = text.strip()
    if not stripped:
        return InputFilterResult(masked_text="", error="empty_input")
    if len(text) > MAX_INPUT_CHARS:
        return InputFilterResult(
            masked_text=mask_pii(text[:MAX_INPUT_CHARS]), error="input_too_long"
        )
    if not is_within_domain_bounds(stripped):
        return InputFilterResult(masked_text=mask_pii(text), error="out_of_domain")
    if detect_language(stripped) == "unsupported":
        return InputFilterResult(
            masked_text=mask_pii(text), error="unsupported_language"
        )
    return InputFilterResult(masked_text=mask_pii(text), error=None)
