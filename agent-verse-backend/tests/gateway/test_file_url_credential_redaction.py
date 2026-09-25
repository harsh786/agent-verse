"""A chat-delivered file's download URL must never be persisted with its credential.

Telegram hands out file URLs of the form::

    https://api.telegram.org/file/bot<BOT_TOKEN>/documents/file_42.pdf

``TelegramChannelAdapter._get_file_url`` builds exactly that, and the gateway
passed it straight into ``RawDocument.source_url``. ``source_url`` is persisted
on ``knowledge_documents`` and copied into every chunk's metadata, where it is
read back out as the ``source_url`` of a RAG citation — so the bot token was
written to Postgres in plaintext and surfaced to the tenant (and to any agent
that retrieved the document) on every citation of anything a user ever sent the
bot. Rotating the token does not un-write it.

The same shape appears on other channels: pre-signed object-store links carry
``?X-Amz-Signature=``/``?token=``, and a plain ``https://user:pass@host/…`` puts
the credential in userinfo.
"""

from __future__ import annotations

import pytest

from app.gateway.router import redact_url_credentials


@pytest.mark.parametrize(
    ("raw", "must_not_contain"),
    [
        (
            "https://api.telegram.org/file/bot123456:AAH-SECRET-TOKEN/docs/f.pdf",
            "AAH-SECRET-TOKEN",
        ),
        ("https://host/f.pdf?token=s3cr3t-value", "s3cr3t-value"),
        ("https://host/f.pdf?access_token=s3cr3t-value&x=1", "s3cr3t-value"),
        ("https://host/f.pdf?X-Amz-Signature=deadbeefcafe", "deadbeefcafe"),
        ("https://user:hunter2@host/f.pdf", "hunter2"),
        ("https://host/f.pdf?sig=abc123&api_key=zzz999", "zzz999"),
    ],
)
def test_credentials_are_stripped(raw: str, must_not_contain: str) -> None:
    redacted = redact_url_credentials(raw)
    assert must_not_contain not in redacted, f"credential survived redaction: {redacted}"


def test_the_useful_parts_of_the_url_survive() -> None:
    """Redaction must keep the URL recognisable — it is a provenance record."""
    redacted = redact_url_credentials(
        "https://api.telegram.org/file/bot999:SECRET/documents/quarterly.pdf"
    )
    assert "api.telegram.org" in redacted
    assert "quarterly.pdf" in redacted
    assert "SECRET" not in redacted


def test_non_urls_and_empties_are_passed_through_unharmed() -> None:
    assert redact_url_credentials("") == ""
    assert redact_url_credentials("not a url") == "not a url"


def test_telegram_file_url_is_redacted_before_it_reaches_a_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end on the shape the Telegram adapter actually produces."""
    from app.gateway.channels.telegram import TelegramChannelAdapter

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:AAH-REAL-BOT-TOKEN")
    adapter = TelegramChannelAdapter()
    built = f"{adapter._api_base}/file/documents/report.pdf"
    assert "AAH-REAL-BOT-TOKEN" in built, "fixture no longer matches the real URL shape"
    assert "AAH-REAL-BOT-TOKEN" not in redact_url_credentials(built)
