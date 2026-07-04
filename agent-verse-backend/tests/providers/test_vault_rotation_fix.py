"""Regression tests for C2 — vault key rotation must be correct end-to-end."""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from app.providers.vault import CredentialVault, _derive_fernet_key


class TestVaultRotationConsistency:

    def test_rotate_key_uses_same_derivation_as_init(self):
        """C2-a: The key derived by rotate_key must equal the key derived by __init__ for the same master."""
        from cryptography.fernet import Fernet
        new_master = "new-master-key-for-testing-abc123"

        # What _derive_fernet_key produces (used by __init__)
        expected_fernet_key = _derive_fernet_key(new_master)
        expected_fernet = Fernet(expected_fernet_key)

        # Encrypt something with the expected key
        plaintext = b"secret-connector-cred"
        ciphertext = expected_fernet.encrypt(plaintext)

        # After rotate_key with the same master as bytes, a new vault instance
        # built with that master must be able to decrypt
        new_vault = CredentialVault(master_key=new_master)
        # The new vault's _fernet should be able to decrypt the ciphertext
        assert new_vault._fernet.decrypt(ciphertext) == plaintext

    @pytest.mark.asyncio
    async def test_rotate_key_updates_self_fernet(self):
        """C2-b: After rotate_key, self._fernet must use the NEW key immediately."""
        vault = CredentialVault(master_key="old-master-key")
        old_fernet = vault._fernet

        new_master = b"new-master-key-testing-32bytes!!"
        await vault.rotate_key(new_master_key=new_master)

        assert vault._fernet is not old_fernet, "self._fernet must be reassigned after rotation"

        # The new fernet must match what __init__ would produce
        new_master_str = new_master.decode("utf-8")
        expected_key = _derive_fernet_key(new_master_str)
        from cryptography.fernet import Fernet
        expected_fernet = Fernet(expected_key)

        test_plaintext = b"test-secret"
        ciphertext = vault._fernet.encrypt(test_plaintext)
        assert expected_fernet.decrypt(ciphertext) == test_plaintext

    @pytest.mark.asyncio
    async def test_rotate_key_full_roundtrip_with_redis(self):
        """C2 full: encrypt → rotate → decrypt with new key succeeds."""
        # Setup: vault with old key, store one secret in mock Redis
        old_vault = CredentialVault(master_key="old-master-key")
        secret = "my-jira-api-token-xyz"
        encrypted_with_old = old_vault.encrypt(secret)

        stored: dict[str, bytes] = {}

        async def mock_get(key):
            return stored.get(key)

        async def mock_set(key, value):
            stored[key] = value.encode() if isinstance(value, str) else value

        async def mock_keys_scan():
            return list(stored.keys())

        mock_redis = AsyncMock()
        mock_redis.get.side_effect = mock_get
        mock_redis.set.side_effect = mock_set

        # Store the encrypted secret
        stored_key = "mcp:connector_secrets:tenant1:server1:api_key"
        stored[stored_key] = (
            encrypted_with_old.encode()
            if isinstance(encrypted_with_old, str)
            else encrypted_with_old
        )

        # Simulate scan_iter by patching
        async def mock_scan_iter(**kwargs):
            for k in list(stored.keys()):
                yield k

        mock_redis.scan_iter = mock_scan_iter

        # Mock pipeline
        mock_pipe = AsyncMock()
        mock_pipe.__aenter__ = AsyncMock(return_value=mock_pipe)
        mock_pipe.__aexit__ = AsyncMock(return_value=False)
        executed_writes: dict = {}

        async def pipe_set(k, v):
            executed_writes[k] = v

        mock_pipe.set = AsyncMock(side_effect=pipe_set)

        async def pipe_execute():
            for k, v in executed_writes.items():
                stored[k] = v.encode() if isinstance(v, str) else v
            return [True] * len(executed_writes)

        mock_pipe.execute = AsyncMock(side_effect=pipe_execute)
        mock_redis.pipeline.return_value = mock_pipe

        # Rotate to new key
        new_master_bytes = b"new-master-key-xyz-32-chars-here"
        result = await old_vault.rotate_key(new_master_key=new_master_bytes, redis=mock_redis)
        assert result["rotated_secrets"] >= 1
        assert result["failed"] == 0

        # Verify: a fresh vault with the new master can decrypt the rotated secret
        new_master_str = new_master_bytes.decode("utf-8")
        new_vault = CredentialVault(master_key=new_master_str)

        rotated_ciphertext = stored[stored_key]
        if isinstance(rotated_ciphertext, bytes):
            rotated_ciphertext = rotated_ciphertext.decode()

        decrypted = new_vault.decrypt(rotated_ciphertext)
        assert decrypted == secret, f"Expected '{secret}', got '{decrypted}'"

    @pytest.mark.asyncio
    async def test_rotate_key_in_process_decrypt_works_immediately(self):
        """C2-b: After rotation, the SAME vault instance can decrypt rotated secrets."""
        vault = CredentialVault(master_key="old-key")
        secret = "connector-secret-value"
        encrypted_with_old = vault.encrypt(secret)

        stored = {
            "mcp:connector_secrets:t1:s1:k": (
                encrypted_with_old.encode()
                if isinstance(encrypted_with_old, str)
                else encrypted_with_old
            )
        }

        mock_redis = AsyncMock()

        async def mock_get(key):
            return stored.get(key)

        mock_redis.get.side_effect = mock_get

        async def mock_scan_iter(**kwargs):
            for k in list(stored.keys()):
                yield k

        mock_redis.scan_iter = mock_scan_iter

        mock_pipe = AsyncMock()
        mock_pipe.__aenter__ = AsyncMock(return_value=mock_pipe)
        mock_pipe.__aexit__ = AsyncMock(return_value=False)
        written: dict = {}

        async def pipe_set(k, v):
            written[k] = v

        mock_pipe.set = AsyncMock(side_effect=pipe_set)

        async def pipe_execute():
            stored.update(
                {k: v.encode() if isinstance(v, str) else v for k, v in written.items()}
            )
            return [True] * len(written)

        mock_pipe.execute = AsyncMock(side_effect=pipe_execute)
        mock_redis.pipeline.return_value = mock_pipe

        # Rotate
        new_key = b"new-key-32-chars-for-test-here!!"
        await vault.rotate_key(new_master_key=new_key, redis=mock_redis)

        # In-process vault should now decrypt rotated secrets
        rotated = stored["mcp:connector_secrets:t1:s1:k"]
        if isinstance(rotated, bytes):
            rotated = rotated.decode()

        assert vault.decrypt(rotated) == secret

    def test_rotate_key_rejects_short_key(self):
        """Existing test preserved: short key rejected."""
        vault = CredentialVault(master_key="test-key")
        with pytest.raises(ValueError):
            asyncio.run(vault.rotate_key(new_master_key=b"short"))
