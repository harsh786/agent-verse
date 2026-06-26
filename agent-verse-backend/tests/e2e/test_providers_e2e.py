"""E2E tests for all LLM provider interfaces and credential vault."""
from __future__ import annotations

import math

import pytest

from app.providers.base import Message, CompletionRequest, EmbedRequest, embed_texts
from app.providers.fake import FakeProvider
from app.providers.vault import CredentialVault, get_vault


# ── embed_texts helper ────────────────────────────────────────────────────────


async def test_embed_texts_with_fake_provider():
    """embed_texts uses the provided FakeProvider."""
    p = FakeProvider(responses=[], embed_dim=768)
    result = await embed_texts(["hello", "world"], provider=p)
    assert len(result) == 2
    assert all(len(e) > 0 for e in result)


async def test_embed_texts_fallback_no_provider():
    """embed_texts returns normalised random 768-dim vectors when no provider given."""
    result = await embed_texts(["hello"])
    assert len(result) == 1
    vec = result[0]
    assert len(vec) == 768
    norm = math.sqrt(sum(x * x for x in vec))
    assert abs(norm - 1.0) < 0.01


async def test_embed_texts_multiple_texts_no_provider():
    """embed_texts returns one vector per text when falling back."""
    texts = ["a", "b", "c", "d"]
    result = await embed_texts(texts)
    assert len(result) == len(texts)
    for vec in result:
        assert len(vec) == 768


async def test_embed_texts_provider_not_implemented_falls_back():
    """embed_texts falls back to random when provider raises NotImplementedError."""

    class NoEmbedProvider:
        async def embed(self, req: EmbedRequest) -> None:
            raise NotImplementedError

        def supports_vision(self) -> bool:
            return False

        def supports_tool_use(self) -> bool:
            return False

    result = await embed_texts(["hello"], provider=NoEmbedProvider())  # type: ignore[arg-type]
    assert len(result) == 1
    assert len(result[0]) == 768


# ── FakeProvider ──────────────────────────────────────────────────────────────


async def test_fake_provider_supports_tool_use():
    """FakeProvider reports supports_tool_use=True by default."""
    p = FakeProvider(responses=["response"])
    assert p.supports_tool_use() is True


async def test_fake_provider_vision_disabled_by_default():
    """FakeProvider reports supports_vision=False by default."""
    p = FakeProvider(responses=["response"])
    assert p.supports_vision() is False


async def test_fake_provider_vision_enabled():
    """FakeProvider supports_vision can be set to True."""
    p = FakeProvider(responses=["response"], vision=True)
    assert p.supports_vision() is True


async def test_fake_provider_cycles_responses_wrap():
    """FakeProvider wraps around after exhausting response list."""
    p = FakeProvider(responses=["a", "b"])
    req = CompletionRequest(
        messages=[Message(role="user", content="x")], model=""
    )
    results = [
        (await p.complete(req)).content for _ in range(5)
    ]
    assert results == ["a", "b", "a", "b", "a"]


async def test_fake_provider_records_all_calls():
    """FakeProvider call_history grows with each complete() call."""
    p = FakeProvider(responses=["ok"])
    req = CompletionRequest(
        messages=[Message(role="user", content="hi")], model="test"
    )
    for _ in range(3):
        await p.complete(req)
    assert len(p.call_history) == 3
    assert all(r.model == "test" for r in p.call_history)


async def test_fake_provider_embed_dim_configurable():
    """FakeProvider embed_dim controls vector length."""
    p = FakeProvider(responses=[], embed_dim=512)
    resp = await p.embed(EmbedRequest(texts=["hello"]))
    assert len(resp.embeddings[0]) == 512


async def test_fake_provider_embed_token_count():
    """FakeProvider embed total_tokens equals sum of word counts."""
    p = FakeProvider(responses=[], embed_dim=64)
    resp = await p.embed(EmbedRequest(texts=["hello world", "foo bar baz"]))
    # "hello world" = 2 words, "foo bar baz" = 3 words → 5 tokens
    assert resp.total_tokens == 5


async def test_fake_provider_completion_token_count():
    """FakeProvider completion output_tokens equals word count of response."""
    p = FakeProvider(responses=["one two three"])
    req = CompletionRequest(
        messages=[Message(role="user", content="count")], model=""
    )
    resp = await p.complete(req)
    assert resp.output_tokens == 3
    assert resp.input_tokens == 10


# ── Message model ─────────────────────────────────────────────────────────────


async def test_message_with_image_data():
    """Message accepts image_data for vision models."""
    msg = Message(role="user", content="describe this", image_data="base64data==")
    assert msg.image_data == "base64data=="
    assert msg.content == "describe this"


async def test_message_image_data_defaults_none():
    """Message.image_data defaults to None when not provided."""
    msg = Message(role="user", content="no image here")
    assert msg.image_data is None


async def test_completion_request_with_system():
    """CompletionRequest stores system prompt and messages correctly."""
    req = CompletionRequest(
        messages=[
            Message(role="system", content="You are helpful"),
            Message(role="user", content="Hello"),
        ],
        model="claude-opus-4-8",
        system="Override system",
    )
    assert req.system == "Override system"
    assert len(req.messages) == 2


async def test_completion_response_total_tokens():
    """CompletionResponse.total_tokens sums input and output tokens."""
    from app.providers.base import CompletionResponse

    resp = CompletionResponse(
        content="hello",
        model="test",
        input_tokens=10,
        output_tokens=5,
    )
    assert resp.total_tokens == 15


# ── CredentialVault ───────────────────────────────────────────────────────────


def test_vault_encrypt_decrypt_roundtrip():
    """Vault encrypt → decrypt round-trips correctly."""
    vault = CredentialVault(master_key="test-master-key-for-testing")
    plaintext = "super-secret-api-key-12345"
    encrypted = vault.encrypt(plaintext)
    assert encrypted != plaintext
    assert "super-secret" not in encrypted
    decrypted = vault.decrypt(encrypted)
    assert decrypted == plaintext


def test_vault_repr_hides_key():
    """CredentialVault repr never exposes the key."""
    vault = CredentialVault(master_key="secret-key-abc")
    r = repr(vault)
    assert "CredentialVault" in r
    assert "secret-key-abc" not in r


def test_vault_str_hides_key():
    """CredentialVault str representation never exposes the key."""
    vault = CredentialVault(master_key="another-secret")
    s = str(vault)
    assert "another-secret" not in s


def test_vault_tamper_detection():
    """Vault raises InvalidToken when a character in the ciphertext is altered."""
    vault = CredentialVault(master_key="tamper-test-key")
    encrypted = vault.encrypt("my-secret")
    # Replace a character near the middle — reliable way to corrupt the MAC
    mid = len(encrypted) // 2
    replacement = "Z" if encrypted[mid] != "Z" else "A"
    tampered = encrypted[:mid] + replacement + encrypted[mid + 1:]
    with pytest.raises(Exception):
        vault.decrypt(tampered)


def test_vault_different_keys_produce_different_ciphertexts():
    """Two vaults with different keys produce different ciphertexts."""
    v1 = CredentialVault(master_key="key-one")
    v2 = CredentialVault(master_key="key-two")
    plaintext = "same-secret"
    c1 = v1.encrypt(plaintext)
    c2 = v2.encrypt(plaintext)
    assert c1 != c2


def test_vault_cross_key_decryption_fails():
    """A ciphertext encrypted by one vault cannot be decrypted by another vault."""
    v1 = CredentialVault(master_key="key-alpha")
    v2 = CredentialVault(master_key="key-beta")
    encrypted = v1.encrypt("my-data")
    with pytest.raises(Exception):
        v2.decrypt(encrypted)


def test_get_vault_returns_credential_vault():
    """get_vault() returns a CredentialVault instance."""
    vault = get_vault()
    assert isinstance(vault, CredentialVault)


def test_get_vault_can_encrypt_decrypt():
    """get_vault() returns a working vault."""
    vault = get_vault()
    msg = "test-encrypt-decrypt"
    enc = vault.encrypt(msg)
    assert vault.decrypt(enc) == msg
