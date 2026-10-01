"""OAUTH-DECRYPT-FAILOPEN: an OAuth token that cannot be decrypted is never used.

``OAuthFlowManager._decrypt_token`` swallowed the vault error and returned the
stored value — the *ciphertext* — as the token, so a rotated/lost vault key or a
tampered row sent the ciphertext as a Bearer token to the provider. It now
raises :class:`OAuthReauthorizationRequiredError`, and a stored token that cannot be
opened marks the connection as needing re-authorization (``oauth_tokens
.needs_reauth``) until a new token is obtained.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.mcp.oauth import OAuthFlowManager, OAuthReauthorizationRequiredError, OAuthToken
from app.providers.vault import CredentialVault

VAULT = CredentialVault(master_key="oauth-failopen-current-key")
OTHER = CredentialVault(master_key="oauth-failopen-some-other-key")


@pytest.fixture(autouse=True)
def _no_tenant_envelope_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _no_key(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr("app.providers.tenant_vault.ensure_tenant_vault", _no_key)


class _Result:
    def __init__(self, row: Any) -> None:
        self._row = row

    def first(self) -> Any:
        return self._row


class _Tx:
    async def __aenter__(self) -> _Tx:
        return self

    async def __aexit__(self, *a: Any) -> bool:
        return False


class _Db:
    """One oauth_tokens row behind fake sessions; records every statement."""

    def __init__(self, row: dict[str, Any] | None) -> None:
        self.row = row
        self.statements: list[tuple[str, dict[str, Any]]] = []

    def __call__(self) -> _Session:
        return _Session(self)


class _Session:
    def __init__(self, db: _Db) -> None:
        self.db = db

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *a: Any) -> bool:
        return False

    def begin(self) -> _Tx:
        return _Tx()

    async def execute(self, stmt: Any, params: Any = None) -> _Result:
        sql = " ".join(str(stmt).split())
        p = dict(params or {})
        self.db.statements.append((sql, p))
        row = self.db.row
        if sql.startswith("SELECT access_token") and row is not None:
            return _Result(
                (row["access_token"], row["refresh_token"], None, "Bearer", "",
                 row.get("needs_reauth", False))
            )
        if sql.startswith("UPDATE oauth_tokens SET needs_reauth = true") and row is not None:
            row["needs_reauth"] = True
        if sql.startswith("INSERT INTO oauth_tokens") and row is not None:
            row.update(access_token=p["at"], refresh_token=p["rt"], needs_reauth=False)
        return _Result(None)


def _manager(db: _Db) -> OAuthFlowManager:
    mgr = OAuthFlowManager(vault=VAULT)
    mgr._db_session_factory = db
    return mgr


def test_undecryptable_token_raises_instead_of_returning_the_ciphertext() -> None:
    mgr = OAuthFlowManager(vault=VAULT)
    foreign = OTHER.encrypt("ya29.real-token")
    with pytest.raises(OAuthReauthorizationRequiredError):
        mgr._decrypt_token(foreign)
    with pytest.raises(OAuthReauthorizationRequiredError):
        mgr._decrypt_token("not-a-fernet-token-at-all")
    assert mgr._decrypt_token(VAULT.encrypt("ok")) == "ok"


def test_without_a_vault_dev_plaintext_is_unchanged() -> None:
    assert OAuthFlowManager(vault=None)._decrypt_token("plain") == "plain"


async def test_stored_token_that_cannot_be_opened_marks_the_connection() -> None:
    db = _Db({"access_token": OTHER.encrypt("a"), "refresh_token": OTHER.encrypt("r")})
    mgr = _manager(db)
    mgr._cache_token(("t1", "srv"), OAuthToken(access_token="stale", expires_in=0))

    with pytest.raises(OAuthReauthorizationRequiredError) as info:
        await mgr.aget_token("t1", "srv")
    assert info.value.server_id == "srv"
    assert db.row is not None and db.row["needs_reauth"] is True
    mark = [p for s, p in db.statements if s.startswith("UPDATE oauth_tokens SET needs_reauth")]
    assert mark and mark[0]["tid"] == "t1" and mark[0]["sid"] == "srv"
    assert mgr.get_token("t1", "srv") is None  # the stale in-process copy is dropped


async def test_marked_connection_is_refused_without_decrypting() -> None:
    db = _Db({"access_token": VAULT.encrypt("a"), "refresh_token": "", "needs_reauth": True})
    with pytest.raises(OAuthReauthorizationRequiredError):
        await _manager(db).aget_token("t1", "srv")


async def test_a_new_token_clears_the_mark() -> None:
    db = _Db({"access_token": OTHER.encrypt("a"), "refresh_token": "", "needs_reauth": True})
    mgr = _manager(db)
    await mgr._persist_token_to_db("t1", "srv", OAuthToken(access_token="fresh"))
    insert = [s for s, _ in db.statements if s.startswith("INSERT INTO oauth_tokens")]
    assert insert and "needs_reauth=false" in insert[0].replace(" ", "")
    mgr._drop_cached(("t1", "srv"))
    token = await mgr.aget_token("t1", "srv")
    assert token is not None and token.access_token == "fresh"


async def test_startup_warmup_skips_undecryptable_rows() -> None:
    mgr = OAuthFlowManager(vault=VAULT)
    mgr._db_session_factory = object()

    async def _rows() -> list[tuple[Any, ...]]:
        return [
            ("t1", "bad", OTHER.encrypt("a"), "", None, "Bearer", ""),
            ("t1", "good", VAULT.encrypt("b"), "", None, "Bearer", ""),
        ]

    mgr._fetch_all_token_rows = _rows  # type: ignore[method-assign]
    assert await mgr.load_tokens_from_db() == 1
    assert mgr.get_token("t1", "bad") is None
    good = mgr.get_token("t1", "good")
    assert good is not None and good.access_token == "b"


async def test_mcp_client_never_sends_the_ciphertext_as_bearer() -> None:
    from app.mcp.client import MCPClient

    db = _Db({"access_token": OTHER.encrypt("a"), "refresh_token": ""})
    client = MCPClient.__new__(MCPClient)
    client._oauth_manager = _manager(db)

    class _Cfg:
        auth_config: dict[str, Any] = {}

    class _Ctx:
        tenant_id = "t1"

    token = await client._oauth_access_token(_Cfg(), tenant_ctx=_Ctx(), server_id="srv")  # type: ignore[arg-type]
    assert token is None
