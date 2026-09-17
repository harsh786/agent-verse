"""Tests for short-lived SSE stream tokens (app/auth/stream_tokens.py)."""
from __future__ import annotations

import time

from app.auth import stream_tokens
from app.auth.stream_tokens import mint_stream_token, verify_stream_token


def test_mint_verify_roundtrip():
    token = mint_stream_token(tenant_id="tenant-1", key_id="key-1")
    claims = verify_stream_token(token)
    assert claims is not None
    assert claims["tenant_id"] == "tenant-1"
    assert claims["key_id"] == "key-1"
    assert claims["typ"] == "stream"


def test_verify_rejects_garbage():
    assert verify_stream_token("not-a-token") is None
    assert verify_stream_token("a.b") is None
    assert verify_stream_token("") is None


def test_verify_rejects_tampered_signature():
    token = mint_stream_token(tenant_id="t", key_id="k")
    # Flip the FIRST character of the signature, not the last: a base64-encoded
    # 32-byte digest has 43 chars with no padding, so its last character only
    # carries 4 significant bits — the other 2 are discard bits ignored by the
    # decoder. 'A' (000000) and 'B' (000001) differ only in a discard bit, so
    # swapping between them there is sometimes a no-op after decoding (~1/32 of
    # random signatures, whenever the real last char happens to be 'A' or 'B'),
    # making the assertion flaky. The first character's bits are always fully
    # significant, so tampering there reliably changes the decoded bytes.
    header, body, sig = token.split(".")
    tampered_sig = ("A" if sig[0] != "A" else "B") + sig[1:]
    tampered = f"{header}.{body}.{tampered_sig}"
    assert verify_stream_token(tampered) is None


def test_verify_rejects_tampered_payload():
    token = mint_stream_token(tenant_id="t", key_id="k")
    header, body, sig = token.split(".")
    # Swap in a different body (different tenant) while keeping the old signature.
    other = mint_stream_token(tenant_id="attacker", key_id="k").split(".")[1]
    forged = f"{header}.{other}.{sig}"
    assert verify_stream_token(forged) is None


def test_verify_rejects_expired_token():
    token = mint_stream_token(tenant_id="t", key_id="k", ttl=-1)
    assert verify_stream_token(token) is None


def test_verify_rejects_wrong_type():
    # A token whose typ is not "stream" (e.g. re-using the goal-token secret) must
    # not be accepted as a stream token.
    import base64
    import hashlib
    import hmac
    import json

    now = int(time.time())
    payload = {"typ": "goal", "tenant_id": "t", "exp": now + 100}
    b = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    h = base64.urlsafe_b64encode(b'{"alg":"HS256"}').rstrip(b"=").decode()
    signing_input = f"{h}.{b}"
    sig = hmac.new(
        stream_tokens._SIGNING_SECRET.encode(), signing_input.encode(), hashlib.sha256
    ).digest()
    token = f"{signing_input}.{base64.urlsafe_b64encode(sig).rstrip(b'=').decode()}"
    assert verify_stream_token(token) is None


def test_verify_rejects_missing_tenant():
    token = mint_stream_token(tenant_id="", key_id="k")
    assert verify_stream_token(token) is None
