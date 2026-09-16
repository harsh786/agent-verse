"""Coverage tests for app.chat.models — SQLAlchemy ORM models for the chat feature.

Instantiates each model at the Python level with required fields. Does NOT
require a live database — SQLAlchemy enforces NOT NULL / FK constraints only
at flush/commit time, not at __init__ time.
"""
from __future__ import annotations


def test_hex_id_generates_32_char_hex_string() -> None:
    from app.chat.models import _hex_id

    value = _hex_id()
    assert isinstance(value, str)
    assert len(value) == 32
    int(value, 16)  # must be valid hex


def test_hex_id_generates_unique_values() -> None:
    from app.chat.models import _hex_id

    assert _hex_id() != _hex_id()


def test_chat_session_folder_instantiation() -> None:
    from app.chat.models import ChatSessionFolder

    folder = ChatSessionFolder(
        id="folder-1",
        tenant_id="tenant-1",
        name="My Folder",
        color="#123456",
        position=2,
    )
    assert folder.id == "folder-1"
    assert folder.tenant_id == "tenant-1"
    assert folder.name == "My Folder"
    assert folder.color == "#123456"
    assert folder.position == 2
    assert folder.__tablename__ == "chat_session_folders"


def test_chat_session_instantiation_defaults() -> None:
    from app.chat.models import ChatSession

    session = ChatSession(id="s-1", tenant_id="tenant-1")
    assert session.id == "s-1"
    assert session.tenant_id == "tenant-1"
    assert session.__tablename__ == "chat_sessions"


def test_chat_session_instantiation_with_all_fields() -> None:
    from app.chat.models import ChatSession

    session = ChatSession(
        id="s-2",
        tenant_id="tenant-1",
        title="Custom Title",
        pinned=True,
        ttl_days=30,
        system_prompt="You are a helpful assistant",
        agent_id="agent-1",
        folder_id="folder-1",
        show_reasoning=True,
        proactive_suggestions=False,
        preferred_model="claude-sonnet",
    )
    assert session.title == "Custom Title"
    assert session.pinned is True
    assert session.ttl_days == 30
    assert session.system_prompt == "You are a helpful assistant"
    assert session.agent_id == "agent-1"
    assert session.folder_id == "folder-1"
    assert session.show_reasoning is True
    assert session.proactive_suggestions is False
    assert session.preferred_model == "claude-sonnet"


def test_chat_message_metadata_column_uses_safe_attribute_name() -> None:
    """Regression: ``metadata`` collides with SQLAlchemy's reserved Base.metadata
    attribute. The Python attribute must be ``metadata_`` while the underlying
    DB column stays named "metadata" for schema/migration compatibility."""
    from app.chat.models import ChatMessage

    col = ChatMessage.__table__.columns["metadata"]
    assert col is not None
    assert "metadata_" in ChatMessage.__mapper__.columns.keys() or hasattr(
        ChatMessage, "metadata_"
    )


def test_chat_message_instantiation() -> None:
    from app.chat.models import ChatMessage

    msg = ChatMessage(
        id="m-1",
        session_id="s-1",
        tenant_id="tenant-1",
        role="user",
        content="Hello there",
        metadata_={"key": "value"},
        branch_id="b-1",
        parent_message_id="m-0",
        goal_id="g-1",
        intent="chat",
    )
    assert msg.id == "m-1"
    assert msg.session_id == "s-1"
    assert msg.role == "user"
    assert msg.content == "Hello there"
    assert msg.metadata_ == {"key": "value"}
    assert msg.branch_id == "b-1"
    assert msg.parent_message_id == "m-0"
    assert msg.goal_id == "g-1"
    assert msg.intent == "chat"
    assert msg.__tablename__ == "chat_messages"


def test_chat_message_usage_instantiation() -> None:
    from app.chat.models import ChatMessageUsage

    usage = ChatMessageUsage(
        id="u-1",
        message_id="m-1",
        session_id="s-1",
        tenant_id="tenant-1",
        tokens_in=100,
        tokens_out=50,
        cost_usd=0.0123,
        model="claude-sonnet",
    )
    assert usage.message_id == "m-1"
    assert usage.tokens_in == 100
    assert usage.tokens_out == 50
    assert usage.cost_usd == 0.0123
    assert usage.model == "claude-sonnet"
    assert usage.__tablename__ == "chat_message_usage"


def test_chat_artifact_instantiation() -> None:
    from app.chat.models import ChatArtifact

    artifact = ChatArtifact(
        id="a-1",
        session_id="s-1",
        tenant_id="tenant-1",
        message_id="m-1",
        title="script.py",
        language="python",
        content="print('hi')",
    )
    assert artifact.id == "a-1"
    assert artifact.session_id == "s-1"
    assert artifact.message_id == "m-1"
    assert artifact.title == "script.py"
    assert artifact.language == "python"
    assert artifact.content == "print('hi')"
    assert artifact.__tablename__ == "chat_artifacts"


def test_chat_session_folder_relationship_to_sessions_defined() -> None:
    from app.chat.models import ChatSession, ChatSessionFolder

    assert "sessions" in ChatSessionFolder.__mapper__.relationships
    rel = ChatSessionFolder.__mapper__.relationships["sessions"]
    assert rel.mapper.class_ is ChatSession


def test_chat_session_relationships_defined() -> None:
    from app.chat.models import ChatSession

    rels = ChatSession.__mapper__.relationships
    assert "folder" in rels
    assert "messages" in rels
    assert "usage" in rels
    assert "artifacts" in rels


def test_chat_message_relationship_to_session_defined() -> None:
    from app.chat.models import ChatMessage, ChatSession

    assert "session" in ChatMessage.__mapper__.relationships
    rel = ChatMessage.__mapper__.relationships["session"]
    assert rel.mapper.class_ is ChatSession


def test_all_models_share_declarative_base() -> None:
    from app.chat.models import (
        ChatArtifact,
        ChatMessage,
        ChatMessageUsage,
        ChatSession,
        ChatSessionFolder,
    )
    from app.db.models import Base

    for model in (ChatSessionFolder, ChatSession, ChatMessage, ChatMessageUsage, ChatArtifact):
        assert issubclass(model, Base)
