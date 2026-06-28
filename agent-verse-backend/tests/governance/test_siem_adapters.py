"""Tests for SIEM adapters — ensures no direct base class instantiation and NullAdapter works."""
from __future__ import annotations

import abc
import asyncio

import pytest

from app.governance.siem_adapters import (
    DatadogAdapter,
    ElasticsearchAdapter,
    NullSIEMAdapter,
    SIEMAdapter,
    SIEMConfig,
    SIEMType,
    SplunkHECAdapter,
    WebhookAdapter,
    build_siem_adapter,
)


def test_siem_adapter_base_cannot_be_instantiated() -> None:
    """SIEMAdapter must be abstract — direct instantiation raises TypeError."""
    with pytest.raises(TypeError, match="abstract"):
        SIEMAdapter()  # type: ignore[abstract]


def test_null_adapter_always_returns_true() -> None:
    """NullSIEMAdapter.send() always returns True without side-effects."""
    adapter = NullSIEMAdapter()
    config = SIEMConfig(siem_type=SIEMType.NULL)
    result = asyncio.run(adapter.send([{"event": "test"}], config))
    assert result is True


def test_null_adapter_returns_true_on_empty_events() -> None:
    """NullSIEMAdapter handles empty event list gracefully."""
    adapter = NullSIEMAdapter()
    config = SIEMConfig(siem_type=SIEMType.NULL)
    result = asyncio.run(adapter.send([], config))
    assert result is True


def test_build_siem_adapter_null() -> None:
    """build_siem_adapter('null') returns a NullSIEMAdapter."""
    adapter = build_siem_adapter("null")
    assert isinstance(adapter, NullSIEMAdapter)


def test_build_siem_adapter_null_enum() -> None:
    """build_siem_adapter(SIEMType.NULL) returns a NullSIEMAdapter."""
    adapter = build_siem_adapter(SIEMType.NULL)
    assert isinstance(adapter, NullSIEMAdapter)


def test_build_siem_adapter_unknown_raises() -> None:
    """build_siem_adapter with an unrecognised type raises ValueError."""
    with pytest.raises(ValueError, match="Unknown SIEM type"):
        build_siem_adapter("unsupported_siem")


def test_concrete_adapters_are_not_abstract() -> None:
    """All concrete adapters can be instantiated and are SIEMAdapter subclasses."""
    from app.governance.siem_adapters import CEFAdapter, LEEFAdapter

    for cls in [
        SplunkHECAdapter,
        ElasticsearchAdapter,
        DatadogAdapter,
        CEFAdapter,
        LEEFAdapter,
        WebhookAdapter,
        NullSIEMAdapter,
    ]:
        instance = cls()
        assert isinstance(instance, SIEMAdapter), f"{cls.__name__} must be a SIEMAdapter"


def test_siem_type_null_in_enum() -> None:
    """SIEMType.NULL is a valid enum member with value 'null'."""
    assert SIEMType.NULL == "null"
    assert SIEMType("null") is SIEMType.NULL


def test_siem_adapter_is_abc() -> None:
    """SIEMAdapter must be an ABC (not just a regular class)."""
    assert issubclass(SIEMAdapter, abc.ABC)


def test_siem_adapter_send_is_abstract() -> None:
    """SIEMAdapter.send must be an abstract method."""
    # abstractmethods set must include 'send'
    assert "send" in SIEMAdapter.__abstractmethods__
