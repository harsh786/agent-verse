"""OAuth tokens are encrypted: the app's manager has a vault, and failures raise.

Regressions: ``create_app`` built ``OAuthFlowManager()`` with no vault, so access
and refresh tokens were kept and persisted in plaintext; and ``_encrypt_token``
swallowed vault errors and returned the plaintext token.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.mcp.oauth import OAuthFlowManager


def test_app_oauth_manager_encrypts_tokens() -> None:
    from app.main import create_app

    app = create_app(manage_pools=False)
    mgr = app.state.oauth_manager
    assert mgr._vault is not None
    enc = mgr._encrypt_token("ya29.secret-access-token")
    assert enc != "ya29.secret-access-token"
    assert mgr._decrypt_token(enc) == "ya29.secret-access-token"


def test_encrypt_failure_raises_instead_of_returning_plaintext() -> None:
    vault = MagicMock()
    vault.encrypt.side_effect = RuntimeError("kms down")
    mgr = OAuthFlowManager(vault=vault)
    with pytest.raises(RuntimeError, match="kms down"):
        mgr._encrypt_token("refresh-token")
