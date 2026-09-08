# Community 346

> 19 nodes · cohesion 0.15

## Key Concepts

- **email_tool.py** (7 connections) — `agent-verse-backend/app/tools/email_tool.py`
- **EmailTool** (7 connections) — `agent-verse-backend/app/tools/email_tool.py`
- **email_send()** (5 connections) — `agent-verse-backend/app/tools/email_tool.py`
- **.from_vault_config()** (5 connections) — `agent-verse-backend/app/tools/email_tool.py`
- **IMAPConfig** (5 connections) — `agent-verse-backend/app/tools/email_tool.py`
- **SMTPConfig** (5 connections) — `agent-verse-backend/app/tools/email_tool.py`
- **.send()** (4 connections) — `agent-verse-backend/app/tools/email_tool.py`
- **Any** (4 connections)
- **.__init__()** (3 connections) — `agent-verse-backend/app/tools/email_tool.py`
- **.read_inbox()** (3 connections) — `agent-verse-backend/app/tools/email_tool.py`
- **_validate_email()** (2 connections) — `agent-verse-backend/app/tools/email_tool.py`
- **Email sending and reading tool. Sending: aiosmtplib (async SMTP) Reading:…** (1 connections) — `agent-verse-backend/app/tools/email_tool.py`
- **Read emails from IMAP inbox. Returns list of message dicts with from, subject,…** (1 connections) — `agent-verse-backend/app/tools/email_tool.py`
- **Create EmailTool from a vault/secrets config dict. Expected keys: smtp_host,…** (1 connections) — `agent-verse-backend/app/tools/email_tool.py`
- **Send an email via aiosmtplib using environment-variable SMTP config. For local…** (1 connections) — `agent-verse-backend/app/tools/email_tool.py`
- **SMTP connection configuration.** (1 connections) — `agent-verse-backend/app/tools/email_tool.py`
- **IMAP connection configuration.** (1 connections) — `agent-verse-backend/app/tools/email_tool.py`
- **Async email tool for sending and reading emails.** (1 connections) — `agent-verse-backend/app/tools/email_tool.py`
- **Send an email via SMTP. Returns {"status": "sent", "message_id": ...} on…** (1 connections) — `agent-verse-backend/app/tools/email_tool.py`

## Relationships

- [Community 305](Community_305.md) (4 shared connections)
- [Community 281](Community_281.md) (2 shared connections)

## Source Files

- `agent-verse-backend/app/tools/email_tool.py`

## Audit Trail

- EXTRACTED: 32 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*