"""Tests for DocumentClassifier."""
import pytest
from app.ocr.classifier import DocumentClassifier
from app.ocr.models import DocumentType


@pytest.fixture
def classifier() -> DocumentClassifier:
    return DocumentClassifier()


def test_classify_pan_card(classifier):
    text = "PERMANENT ACCOUNT NUMBER ABCDE1234F\nIncome Tax Department\nName: Test User"
    assert classifier.classify(text) == DocumentType.PAN_CARD


def test_classify_pan_card_regex_boost(classifier):
    # Regex match alone gives score 2 (meets threshold)
    text = "ABCDE1234F"
    assert classifier.classify(text) == DocumentType.PAN_CARD


def test_classify_aadhaar(classifier):
    text = "UIDAI\nUnique Identification Authority\n1234 5678 9012\nTest Name"
    assert classifier.classify(text) == DocumentType.AADHAAR


def test_classify_aadhaar_regex_boost(classifier):
    text = "1234 5678 9012 aadhaar"
    assert classifier.classify(text) == DocumentType.AADHAAR


def test_classify_passport(classifier):
    text = "Republic of India\nPassport\nNationality: Indian\nPlace of Birth: Delhi"
    assert classifier.classify(text) == DocumentType.PASSPORT


def test_classify_invoice(classifier):
    text = "Invoice No: INV-001\nBill To: Test Company\nGST: 29ABCDE1234F1Z5\nTotal Amount: 1500"
    assert classifier.classify(text) == DocumentType.INVOICE


def test_classify_bank_statement(classifier):
    text = "Account Statement\nOpening Balance: 10000\nIFSC: SBIN0001234\nClosing Balance: 12000"
    assert classifier.classify(text) == DocumentType.BANK_STATEMENT


def test_classify_receipt(classifier):
    text = "Receipt No: REC-001\nAmount Paid: Rs. 500\nPayment Received"
    assert classifier.classify(text) == DocumentType.RECEIPT


def test_classify_general_no_match(classifier):
    text = "Hello world, this is a random text with no document keywords."
    assert classifier.classify(text) == DocumentType.GENERAL


def test_classify_general_low_score(classifier):
    # "invoice" alone scores 1, below threshold of 2
    text = "This mentions invoice once."
    assert classifier.classify(text) == DocumentType.GENERAL


def test_classify_case_insensitive(classifier):
    text = "INVOICE\nBILL TO: CUSTOMER\nGST NUMBER: 29ABCDE1234F1Z5\nTOTAL AMOUNT: 500"
    assert classifier.classify(text) == DocumentType.INVOICE
