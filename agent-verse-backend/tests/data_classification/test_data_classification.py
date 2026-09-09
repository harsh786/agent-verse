"""DataClassifier must classify text before it enters prompts or tools."""
from __future__ import annotations

import pytest

from app.data_classification.classifier import DataClassifier
from app.data_classification.redaction import Redactor
from app.data_classification.schema import DataClass


@pytest.fixture
def clf() -> DataClassifier:
    return DataClassifier()


@pytest.fixture
def redactor() -> Redactor:
    return Redactor()


def test_public_text(clf: DataClassifier) -> None:
    result = clf.classify("The capital of France is Paris.")
    assert DataClass.PUBLIC in result.classes


def test_email_is_pii(clf: DataClassifier) -> None:
    result = clf.classify("Contact john.doe@example.com for support.")
    assert DataClass.PII in result.classes


def test_credit_card_is_pci(clf: DataClassifier) -> None:
    result = clf.classify("Card: 4111 1111 1111 1111")
    assert DataClass.PCI in result.classes


def test_api_key_is_secret(clf: DataClassifier) -> None:
    result = clf.classify("My API key is sk-proj-abc123DEFxyz456")
    assert DataClass.SECRET in result.classes


def test_ssn_is_phi_or_pii(clf: DataClassifier) -> None:
    result = clf.classify("Patient SSN: 123-45-6789")
    assert DataClass.PII in result.classes or DataClass.PHI in result.classes


def test_code_detected(clf: DataClassifier) -> None:
    result = clf.classify("def calculate(x, y): return x * y")
    assert DataClass.SOURCE_CODE in result.classes


def test_secret_not_safe_for_prompt(clf: DataClassifier) -> None:
    result = clf.classify("The secret key is AKIA1234567890ABCDEF")
    assert not result.safe_for_prompt


def test_public_safe_for_prompt(clf: DataClassifier) -> None:
    result = clf.classify("The sky is blue.")
    assert result.safe_for_prompt is True


def test_redact_email(redactor: Redactor) -> None:
    text = "Contact alice@company.com for help."
    redacted = redactor.redact(text, classes=[DataClass.PII])
    assert "alice@company.com" not in redacted
    assert "[REDACTED-PII]" in redacted


def test_redact_credit_card(redactor: Redactor) -> None:
    text = "Card: 4111-1111-1111-1111"
    redacted = redactor.redact(text, classes=[DataClass.PCI])
    assert "4111" not in redacted


def test_classify_fallback_never_raises(clf: DataClassifier) -> None:
    result = clf.classify_or_safe_fallback("")
    assert result is not None
    assert len(result.classes) > 0
