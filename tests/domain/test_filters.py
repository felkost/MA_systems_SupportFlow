"""Input filter tests. This is a
data-protection code path, so it carries a 100% coverage requirement,
not the general 80% target.

The fixture tables are the measured-recall artefact this test suite
commits to: `test_pii_fixture_recall` asserts every positive fixture is
caught, and reports the count so recall is a number with its n, not a
claim of "handles PII".
"""

from src.domain.filters import (
    contains_forbidden_data,
    detect_language,
    is_within_domain_bounds,
    mask_pii,
    run_input_filter,
)

# --- PII fixtures: (text, should_be_flagged) ---------------------------
# Positive cases cover named failure modes: spaced/dashed digits,
# spelled-out digits, Cyrillic input. Negative cases include order
# numbers — the false-positive the Luhn check exists to avoid.
_PII_FIXTURES: list[tuple[str, bool]] = [
    ("Зв'яжіться зі мною за номером 0671234567", True),
    ("+380 67 123 45 67 подзвоніть будь ласка", True),
    ("0 6 7 - 1 2 3 4 5 6 7", True),
    ("zero six seven one two three four five six seven", True),
    ("нуль шість сім один два три чотири п'ять шість сім", True),
    ("мій email test.user@example.com", True),
    ("картка 4111111111111111 прострочена", True),  # Luhn-valid test PAN
    ("У мене питання про молоко без лактози", False),
    ("номер замовлення 1234567890123457", False),  # 16 digits, Luhn-invalid
    ("Дякую за швидку відповідь!", False),
    ("", False),
]


def test_pii_fixture_recall() -> None:
    results = [
        (text, expected, contains_forbidden_data(text))
        for text, expected in _PII_FIXTURES
    ]
    positives = [r for r in results if r[1]]
    caught = [r for r in positives if r[2]]
    assert len(caught) == len(positives), (
        f"recall {len(caught)}/{len(positives)} on {len(positives)} positive fixtures: "
        f"{[t for t, _, got in positives if not got]}"
    )
    false_positives = [t for t, expected, got in results if not expected and got]
    assert not false_positives, f"false positives: {false_positives}"


def test_mask_pii_redacts_email_and_phone() -> None:
    masked = mask_pii("зателефонуйте на 0671234567 або пишіть на test@example.com")
    assert "0671234567" not in masked
    assert "test@example.com" not in masked
    assert "[REDACTED]" in masked


def test_mask_pii_does_not_touch_order_numbers() -> None:
    masked = mask_pii("номер замовлення 1234567890123457")
    assert "1234567890123457" in masked


def test_mask_pii_redacts_a_luhn_valid_card_number() -> None:
    masked = mask_pii("картка 4111111111111111 прострочена")
    assert "4111111111111111" not in masked
    assert "[REDACTED]" in masked


# --- language fixtures --------------------------------------------------

_LANGUAGE_FIXTURES: list[tuple[str, str]] = [
    ("Чи є у вас безлактозне молоко?", "uk"),
    ("Скажите пожалуйста, это молоко без лактозы?", "ru"),
    ("Is this milk lactose-free?", "en"),
    ("这个牛奶不含乳糖吗？", "unsupported"),
    ("12345 !!! ###", "unsupported"),
]


def test_language_detection_fixtures() -> None:
    for text, expected in _LANGUAGE_FIXTURES:
        assert detect_language(text) == expected, text


# --- domain-bounds / short-circuit fixtures -----------------------------


def test_domain_bounds_rejects_symbol_only_input() -> None:
    assert is_within_domain_bounds("!!! ### $$$") is False
    assert is_within_domain_bounds("🙂🙂🙂") is False


def test_domain_bounds_accepts_ordinary_text() -> None:
    assert is_within_domain_bounds("Чи є у вас акції на хліб?") is True


def test_run_input_filter_rejects_empty() -> None:
    assert run_input_filter("").error == "empty_input"
    assert run_input_filter("   \n\t  ").error == "empty_input"


def test_run_input_filter_rejects_over_length_cap() -> None:
    result = run_input_filter("а" * 5000)
    assert result.error == "input_too_long"


def test_run_input_filter_rejects_unsupported_language() -> None:
    assert run_input_filter("这个牛奶不含乳糖吗？").error == "unsupported_language"


def test_run_input_filter_rejects_symbol_only() -> None:
    assert run_input_filter("!!! ### $$$").error == "out_of_domain"


def test_run_input_filter_rejects_foreign_script_with_real_content() -> None:
    # domain-bounds passes (CJK characters are alphabetic), so this must
    # be the language check that rejects it — proves domain-bounds is not
    # dead code once it runs before the language check.
    assert run_input_filter("这个牛奶不含乳糖吗？").error == "unsupported_language"


def test_run_input_filter_passes_ordinary_product_query() -> None:
    result = run_input_filter("Чи є у вас безлактозне молоко?")
    assert result.error is None
    assert result.masked_text == "Чи є у вас безлактозне молоко?"


def test_run_input_filter_masks_pii_in_a_passing_message() -> None:
    result = run_input_filter("Мій номер 0671234567, є питання про доставку")
    assert result.error is None
    assert "0671234567" not in result.masked_text
