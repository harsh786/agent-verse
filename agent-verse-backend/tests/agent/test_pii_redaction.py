"""PII guardrail: Luhn-validated card detection + span-only redaction.

Regression for the false positive that redacted a valid web answer because a run
of years ("2015 2019 2021 2024") matched the generic 16-digit card pattern.
"""

from __future__ import annotations

from app.intelligence.guardrails import GuardrailChecker, _luhn_valid, redact_pii


def test_years_run_is_not_flagged_as_card():
    text = "Sundar Pichai (born 1972) has led Google since 2015 2019 2021 2024."
    redacted, issues = redact_pii(text)
    assert issues == []
    assert redacted == text  # untouched


def test_real_card_is_redacted_span_only():
    # 4111111111111111 is the canonical Visa test number (valid Luhn)
    text = "Pay with card 4111 1111 1111 1111 today please."
    redacted, issues = redact_pii(text)
    assert any("CARD" in i for i in issues)
    assert "[REDACTED:CARD]" in redacted
    # the rest of the sentence survives
    assert redacted.startswith("Pay with card ")
    assert redacted.endswith(" today please.")


def test_ssn_is_redacted():
    redacted, issues = redact_pii("My SSN is 123-45-6789 ok")
    assert any("SSN" in i for i in issues)
    assert "[REDACTED:SSN]" in redacted
    assert "ok" in redacted


def test_random_16_digit_id_without_luhn_not_flagged():
    # 16-digit id that fails Luhn -> not a card
    text = "Order id 1234 5678 9012 3456 shipped."
    redacted, issues = redact_pii(text)
    assert issues == []
    assert redacted == text


def test_multiple_pii_all_redacted_rest_preserved():
    text = "SSN 123-45-6789 and card 4111111111111111 for John."
    redacted, issues = redact_pii(text)
    assert "[REDACTED:SSN]" in redacted
    assert "[REDACTED:CARD]" in redacted
    assert "for John." in redacted


def test_luhn_helper():
    assert _luhn_valid("4111111111111111") is True
    assert _luhn_valid("2015201920212024") is False
    assert _luhn_valid("123") is False  # too short


def test_checker_redact_output_preserves_answer():
    chk = GuardrailChecker(known_tools=set())
    out, issues = chk.redact_output(
        output="The CEO is Sundar Pichai; Google was founded in 1998, IPO 2004."
    )
    assert issues == []
    assert "Sundar Pichai" in out


def test_checker_check_output_no_false_positive_on_years():
    chk = GuardrailChecker(known_tools=set())
    assert chk.check_output(output="Timeline: 2015 2019 2021 2024 milestones") == []
