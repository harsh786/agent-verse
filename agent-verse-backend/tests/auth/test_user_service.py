"""Tests for app.auth.user_service.upsert_google_user."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from app.auth.user_service import upsert_google_user
from app.db.models.user import User


def _make_session(user_result: object, membership_result: object) -> tuple[AsyncMock, MagicMock]:
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.execute = AsyncMock(side_effect=[user_result, membership_result])
    mock_session.flush = AsyncMock()
    mock_session.add = MagicMock()

    mock_begin = AsyncMock()
    mock_begin.__aenter__ = AsyncMock(return_value=mock_begin)
    mock_begin.__aexit__ = AsyncMock(return_value=False)
    mock_session.begin = MagicMock(return_value=mock_begin)

    db_factory = MagicMock(return_value=mock_session)
    return db_factory, mock_session


def _result(scalar: object) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = scalar
    return result


async def test_upsert_creates_new_user_and_personal_tenant() -> None:
    db_factory, session = _make_session(_result(None), _result(None))

    user_id, tenant_id = await upsert_google_user(
        db_factory=db_factory,
        email="new@example.com",
        google_sub="google-sub-1",
        name="New User",
        picture_url="http://pic",
    )

    assert user_id
    assert tenant_id == f"personal_{user_id[:16]}"
    assert session.add.call_count == 2  # new User + new TenantMembership
    session.flush.assert_awaited_once()

    added_user = session.add.call_args_list[0].args[0]
    assert isinstance(added_user, User)
    assert added_user.email == "new@example.com"
    assert added_user.google_sub == "google-sub-1"
    assert added_user.name == "New User"

    added_membership = session.add.call_args_list[1].args[0]
    assert added_membership.user_id == user_id
    assert added_membership.tenant_id == tenant_id
    assert added_membership.role == "owner"
    assert added_membership.status == "active"


async def test_upsert_existing_user_by_google_sub_no_flush() -> None:
    existing_user = User(id="existing-id", email="e@example.com", google_sub="sub-1", name="Old Name")
    db_factory, session = _make_session(_result(existing_user), _result(None))

    user_id, tenant_id = await upsert_google_user(
        db_factory=db_factory, email="e@example.com", google_sub="sub-1"
    )

    assert user_id == "existing-id"
    assert tenant_id == "personal_existing-id"
    session.flush.assert_not_awaited()
    # Only the new membership gets added; the user already existed.
    assert session.add.call_count == 1
    added_membership = session.add.call_args_list[0].args[0]
    assert added_membership.tenant_id == tenant_id


async def test_upsert_existing_user_fills_missing_google_sub() -> None:
    existing_user = User(id="existing-id-2", email="e2@example.com", google_sub=None, name="Has Name")
    db_factory, session = _make_session(_result(existing_user), _result(None))

    await upsert_google_user(
        db_factory=db_factory, email="e2@example.com", google_sub="new-sub", name="Ignored"
    )

    assert existing_user.google_sub == "new-sub"
    # Name is not overwritten because the existing user already has one.
    assert existing_user.name == "Has Name"


async def test_upsert_existing_user_fills_missing_name() -> None:
    existing_user = User(id="existing-id-3", email="e3@example.com", google_sub="sub-3", name=None)
    db_factory, session = _make_session(_result(existing_user), _result(None))

    await upsert_google_user(
        db_factory=db_factory, email="e3@example.com", google_sub="sub-3", name="Filled In"
    )

    assert existing_user.name == "Filled In"


async def test_upsert_existing_membership_not_duplicated() -> None:
    existing_user = User(id="existing-id-4", email="e4@example.com", google_sub="sub-4")
    existing_membership = MagicMock()
    db_factory, session = _make_session(_result(existing_user), _result(existing_membership))

    await upsert_google_user(db_factory=db_factory, email="e4@example.com", google_sub="sub-4")

    # No new objects added when both user and membership already exist.
    session.add.assert_not_called()


async def test_upsert_returns_tuple_of_user_id_and_tenant_id() -> None:
    db_factory, _ = _make_session(_result(None), _result(None))

    result = await upsert_google_user(
        db_factory=db_factory, email="tup@example.com", google_sub="sub-tup"
    )

    assert isinstance(result, tuple)
    assert len(result) == 2
