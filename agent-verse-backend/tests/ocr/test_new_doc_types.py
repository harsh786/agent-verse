"""Tests for new OCR document types: Voter ID, GSTIN, Cheque, Salary Slip, Address Proof."""
from app.ocr.classifier import DocumentClassifier
from app.ocr.extractors import get_extractor
from app.ocr.models import DocumentType

classifier = DocumentClassifier()

# ── Classifier tests ──────────────────────────────────────────────────────────


def test_classify_voter_id():
    text = "Election Commission of India\nEPIC No. ABC1234567\nElectors Photo"
    assert classifier.classify(text) == DocumentType.VOTER_ID


def test_classify_gstin_certificate():
    text = "Goods and Services Tax\nCertificate of Registration\nGSTIN: 29ABCDE1234F1Z5"
    assert classifier.classify(text) == DocumentType.GSTIN_CERTIFICATE


def test_classify_bank_cheque():
    text = "A/C Payee\nCheque No. 123456\nMICR: 600002157\nPay to Test Person"
    assert classifier.classify(text) == DocumentType.BANK_CHEQUE


def test_classify_salary_slip():
    text = "Salary Slip\nGross Salary: 50000\nNet Pay: 42000\nBasic Salary: 30000"
    assert classifier.classify(text) == DocumentType.SALARY_SLIP


def test_classify_address_proof():
    text = "Electricity Bill\nMunicipal Corporation\nName: Test User\nAddress: 123 Main St"
    assert classifier.classify(text) == DocumentType.ADDRESS_PROOF


# ── Extractor tests ───────────────────────────────────────────────────────────

VOTER_TEXT = "Election Commission of India\nEPIC No. ABC1234567\nName: TEST USER\nDOB: 01/01/1990"


def test_voter_id_extracts_epic_number():
    ext = get_extractor(DocumentType.VOTER_ID)
    fields = ext.extract(VOTER_TEXT)
    assert "epic_number" in fields
    assert "ABC1234567" in fields["epic_number"].value


def test_voter_id_extracts_name():
    ext = get_extractor(DocumentType.VOTER_ID)
    fields = ext.extract(VOTER_TEXT)
    assert "name" in fields


CHEQUE_TEXT = "A/C Payee\nCheque No. 123456\nPay: Test Person\nRs. 5000\n600002157"


def test_cheque_extracts_cheque_number():
    ext = get_extractor(DocumentType.BANK_CHEQUE)
    fields = ext.extract(CHEQUE_TEXT)
    assert "cheque_number" in fields
    assert "123456" in fields["cheque_number"].value


def test_cheque_extracts_amount():
    ext = get_extractor(DocumentType.BANK_CHEQUE)
    fields = ext.extract(CHEQUE_TEXT)
    assert "amount" in fields


GSTIN_TEXT = (
    "Goods and Services Tax\nCertificate of Registration\n"
    "GSTIN: 29ABCDE1234F1Z5\nLegal Name: Test Corp\nRegistration Date: 01/04/2018"
)


def test_gstin_extracts_gstin():
    ext = get_extractor(DocumentType.GSTIN_CERTIFICATE)
    fields = ext.extract(GSTIN_TEXT)
    assert "gstin" in fields


def test_gstin_extracts_business_name():
    ext = get_extractor(DocumentType.GSTIN_CERTIFICATE)
    fields = ext.extract(GSTIN_TEXT)
    assert "business_name" in fields
    assert "Test Corp" in fields["business_name"].value


SALARY_TEXT = (
    "Salary Slip - March 2026\nEmployee Name: Test User\n"
    "Employer: Test Corp\nGross Salary: 50,000\nNet Pay: 42,000"
)


def test_salary_extracts_gross():
    ext = get_extractor(DocumentType.SALARY_SLIP)
    fields = ext.extract(SALARY_TEXT)
    assert "gross_salary" in fields
    assert "50,000" in fields["gross_salary"].value


def test_salary_extracts_net_pay():
    ext = get_extractor(DocumentType.SALARY_SLIP)
    fields = ext.extract(SALARY_TEXT)
    assert "net_pay" in fields


def test_salary_extracts_pay_period():
    ext = get_extractor(DocumentType.SALARY_SLIP)
    fields = ext.extract(SALARY_TEXT)
    assert "pay_period" in fields


ADDRESS_TEXT = "Electricity Bill\nName: Test User\nAddress: 123 Main Street\nBill Date: 01/07/2026"


def test_address_proof_extracts_name():
    ext = get_extractor(DocumentType.ADDRESS_PROOF)
    fields = ext.extract(ADDRESS_TEXT)
    assert "name" in fields


def test_address_proof_extracts_address():
    ext = get_extractor(DocumentType.ADDRESS_PROOF)
    fields = ext.extract(ADDRESS_TEXT)
    assert "address" in fields
