"""Comprehensive tests for app/governance/siem_adapters.py — targeting 90%+ coverage."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.governance.siem_adapters import (
    CEFAdapter,
    DatadogAdapter,
    ElasticsearchAdapter,
    LEEFAdapter,
    NullSIEMAdapter,
    SIEM_ADAPTER_MAP,
    SIEMAdapter,
    SIEMConfig,
    SIEMType,
    SplunkHECAdapter,
    WebhookAdapter,
    build_siem_adapter,
)


def _event(
    event_type: str = "goal.created",
    tenant_id: str = "t1",
    created_at: str = "2026-06-28T12:00:00+00:00",
    **kwargs: object,
) -> dict:
    return {
        "id": "evt-1",
        "event_type": event_type,
        "tenant_id": tenant_id,
        "action": "create",
        "status": "success",
        "created_at": created_at,
        "resource_type": "goal",
        "request_id": "req-1",
        **kwargs,
    }


# ── SIEMType ──────────────────────────────────────────────────────────────────

class TestSIEMType:
    def test_all_types_defined(self) -> None:
        types = {t.value for t in SIEMType}
        assert "splunk" in types
        assert "elasticsearch" in types
        assert "datadog" in types
        assert "cef" in types
        assert "leef" in types
        assert "webhook" in types
        assert "null" in types


# ── SIEMConfig ────────────────────────────────────────────────────────────────

class TestSIEMConfig:
    def test_defaults(self) -> None:
        cfg = SIEMConfig()
        assert cfg.siem_type == SIEMType.WEBHOOK
        assert cfg.index == "agentverse"
        assert cfg.source_type == "agentverse:audit"
        assert cfg.port == 514
        assert cfg.protocol == "udp"

    def test_custom_values(self) -> None:
        cfg = SIEMConfig(
            siem_type=SIEMType.SPLUNK,
            endpoint="https://splunk.example.com",
            api_key="mytoken",
        )
        assert cfg.siem_type == SIEMType.SPLUNK
        assert cfg.endpoint == "https://splunk.example.com"


# ── NullSIEMAdapter ───────────────────────────────────────────────────────────

class TestNullSIEMAdapter:
    async def test_send_returns_true(self) -> None:
        adapter = NullSIEMAdapter()
        cfg = SIEMConfig(siem_type=SIEMType.NULL)
        result = await adapter.send([_event()], cfg)
        assert result is True

    async def test_send_empty_returns_true(self) -> None:
        adapter = NullSIEMAdapter()
        cfg = SIEMConfig(siem_type=SIEMType.NULL)
        result = await adapter.send([], cfg)
        assert result is True


# ── SplunkHECAdapter ──────────────────────────────────────────────────────────

class TestSplunkHECAdapter:
    async def test_send_success(self) -> None:
        adapter = SplunkHECAdapter()
        cfg = SIEMConfig(
            siem_type=SIEMType.SPLUNK,
            endpoint="https://splunk.example.com",
            api_key="token123",
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await adapter.send([_event()], cfg)

        assert result is True

    async def test_send_non_200_returns_false(self) -> None:
        adapter = SplunkHECAdapter()
        cfg = SIEMConfig(endpoint="https://splunk.example.com", api_key="tok")
        mock_resp = MagicMock()
        mock_resp.status_code = 400

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await adapter.send([_event()], cfg)

        assert result is False

    async def test_send_network_error_returns_false(self) -> None:
        adapter = SplunkHECAdapter()
        cfg = SIEMConfig(endpoint="https://splunk.example.com", api_key="tok")

        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__ = AsyncMock(
                side_effect=ConnectionError("Network error")
            )
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await adapter.send([_event()], cfg)

        assert result is False

    async def test_send_invalid_timestamp_handled(self) -> None:
        adapter = SplunkHECAdapter()
        cfg = SIEMConfig(endpoint="https://splunk.example.com", api_key="tok")
        mock_resp = MagicMock(status_code=200)
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await adapter.send([{"id": "e1", "created_at": "invalid-ts"}], cfg)

        assert result is True  # ts falls back to 0


# ── ElasticsearchAdapter ──────────────────────────────────────────────────────

class TestElasticsearchAdapter:
    async def test_send_success(self) -> None:
        adapter = ElasticsearchAdapter()
        cfg = SIEMConfig(endpoint="https://es.example.com", api_key="eskey")
        mock_resp = MagicMock(status_code=200)
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await adapter.send([_event()], cfg)

        assert result is True

    async def test_send_201_success(self) -> None:
        adapter = ElasticsearchAdapter()
        cfg = SIEMConfig(endpoint="https://es.example.com", api_key="eskey")
        mock_resp = MagicMock(status_code=201)
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await adapter.send([_event()], cfg)

        assert result is True

    async def test_send_failure_returns_false(self) -> None:
        adapter = ElasticsearchAdapter()
        cfg = SIEMConfig(endpoint="https://es.example.com", api_key="eskey")

        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__ = AsyncMock(
                side_effect=Exception("Connection refused")
            )
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await adapter.send([_event()], cfg)

        assert result is False


# ── DatadogAdapter ────────────────────────────────────────────────────────────

class TestDatadogAdapter:
    async def test_send_success_202(self) -> None:
        adapter = DatadogAdapter()
        cfg = SIEMConfig(siem_type=SIEMType.DATADOG, api_key="dd-key", service="test")
        mock_resp = MagicMock(status_code=202)
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await adapter.send([_event()], cfg)

        assert result is True

    async def test_send_non_202_returns_false(self) -> None:
        adapter = DatadogAdapter()
        cfg = SIEMConfig(api_key="dd-key")
        mock_resp = MagicMock(status_code=400)
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await adapter.send([_event()], cfg)

        assert result is False

    async def test_send_error_returns_false(self) -> None:
        adapter = DatadogAdapter()
        cfg = SIEMConfig(api_key="dd-key")

        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__ = AsyncMock(side_effect=Exception("err"))
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await adapter.send([_event()], cfg)

        assert result is False


# ── CEFAdapter ────────────────────────────────────────────────────────────────

class TestCEFAdapter:
    async def test_send_udp_success(self) -> None:
        adapter = CEFAdapter()
        cfg = SIEMConfig(
            siem_type=SIEMType.CEF, host="127.0.0.1", port=514, protocol="udp"
        )

        mock_sock = MagicMock()
        mock_sock.sendto = MagicMock()
        mock_sock.__enter__ = MagicMock(return_value=mock_sock)
        mock_sock.__exit__ = MagicMock(return_value=False)

        with patch("socket.socket", return_value=mock_sock):
            result = await adapter.send([_event(metadata={"severity": "high"})], cfg)

        assert result is True
        mock_sock.sendto.assert_called()

    async def test_send_tcp_success(self) -> None:
        adapter = CEFAdapter()
        cfg = SIEMConfig(
            siem_type=SIEMType.CEF, host="127.0.0.1", port=514, protocol="tcp"
        )
        mock_sock = MagicMock()
        mock_sock.sendall = MagicMock()
        mock_sock.__enter__ = MagicMock(return_value=mock_sock)
        mock_sock.__exit__ = MagicMock(return_value=False)

        with patch("socket.socket", return_value=mock_sock):
            result = await adapter.send([_event()], cfg)

        assert result is True

    async def test_send_error_returns_false(self) -> None:
        adapter = CEFAdapter()
        cfg = SIEMConfig(host="127.0.0.1", port=514)

        with patch("socket.socket", side_effect=OSError("socket error")):
            result = await adapter.send([_event()], cfg)

        assert result is False

    async def test_severity_mapping(self) -> None:
        adapter = CEFAdapter()
        assert adapter._SEVERITY["low"] == "3"
        assert adapter._SEVERITY["medium"] == "5"
        assert adapter._SEVERITY["high"] == "8"
        assert adapter._SEVERITY["critical"] == "10"

    async def test_send_unknown_severity_defaults(self) -> None:
        adapter = CEFAdapter()
        cfg = SIEMConfig(host="127.0.0.1", port=514, protocol="udp")
        mock_sock = MagicMock()
        mock_sock.__enter__ = MagicMock(return_value=mock_sock)
        mock_sock.__exit__ = MagicMock(return_value=False)

        with patch("socket.socket", return_value=mock_sock):
            result = await adapter.send(
                [_event(metadata={"severity": "extreme"})], cfg
            )

        assert result is True


# ── LEEFAdapter ───────────────────────────────────────────────────────────────

class TestLEEFAdapter:
    async def test_send_success(self) -> None:
        adapter = LEEFAdapter()
        cfg = SIEMConfig(endpoint="https://qradar.example.com/leef")
        mock_resp = MagicMock(status_code=200)
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await adapter.send([_event()], cfg)

        assert result is True

    async def test_send_invalid_timestamp_handled(self) -> None:
        adapter = LEEFAdapter()
        cfg = SIEMConfig(endpoint="https://qradar.example.com/leef")
        mock_resp = MagicMock(status_code=200)
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await adapter.send([{"id": "x", "created_at": "bad-ts"}], cfg)

        assert result is True  # falls back gracefully

    async def test_send_error_returns_false(self) -> None:
        adapter = LEEFAdapter()
        cfg = SIEMConfig(endpoint="https://qradar.example.com/leef")

        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__ = AsyncMock(side_effect=Exception("err"))
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await adapter.send([_event()], cfg)

        assert result is False

    async def test_leef_header_format(self) -> None:
        adapter = LEEFAdapter()
        assert adapter.LEEF_VERSION == "LEEF:2.0"
        assert adapter.VENDOR == "AgentVerse"

    async def test_send_202_201_accepted(self) -> None:
        adapter = LEEFAdapter()
        cfg = SIEMConfig(endpoint="https://qradar.example.com/leef")
        for status_code in [201, 202]:
            mock_resp = MagicMock(status_code=status_code)
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            with patch("httpx.AsyncClient") as mock_httpx:
                mock_httpx.return_value.__aenter__ = AsyncMock(return_value=mock_client)
                mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)
                result = await adapter.send([_event()], cfg)
            assert result is True


# ── WebhookAdapter ────────────────────────────────────────────────────────────

class TestWebhookAdapter:
    async def test_send_success_200(self) -> None:
        adapter = WebhookAdapter()
        cfg = SIEMConfig(
            endpoint="https://webhook.example.com/events", api_key="secret"
        )
        mock_resp = MagicMock(status_code=200)
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await adapter.send([_event()], cfg)

        assert result is True

    async def test_send_204_success(self) -> None:
        adapter = WebhookAdapter()
        cfg = SIEMConfig(endpoint="https://webhook.example.com/events", api_key="s")
        mock_resp = MagicMock(status_code=204)
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await adapter.send([_event()], cfg)

        assert result is True

    async def test_send_error_returns_false(self) -> None:
        adapter = WebhookAdapter()
        cfg = SIEMConfig(endpoint="https://webhook.example.com/events")

        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__ = AsyncMock(side_effect=Exception("err"))
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await adapter.send([_event()], cfg)

        assert result is False

    async def test_send_non_2xx_returns_false(self) -> None:
        adapter = WebhookAdapter()
        cfg = SIEMConfig(endpoint="https://webhook.example.com/events")
        mock_resp = MagicMock(status_code=500)
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await adapter.send([_event()], cfg)

        assert result is False


# ── build_siem_adapter ────────────────────────────────────────────────────────

class TestBuildSIEMAdapter:
    def test_build_all_types(self) -> None:
        type_to_cls = {
            "null": NullSIEMAdapter,
            "splunk": SplunkHECAdapter,
            "elasticsearch": ElasticsearchAdapter,
            "datadog": DatadogAdapter,
            "cef": CEFAdapter,
            "leef": LEEFAdapter,
            "webhook": WebhookAdapter,
        }
        for siem_str, expected_cls in type_to_cls.items():
            adapter = build_siem_adapter(siem_str)
            assert isinstance(adapter, expected_cls), f"{siem_str!r} → {type(adapter)}"

    def test_build_with_enum_value(self) -> None:
        adapter = build_siem_adapter(SIEMType.SPLUNK)
        assert isinstance(adapter, SplunkHECAdapter)

    def test_build_unknown_string_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown SIEM type"):
            build_siem_adapter("unsupported")

    def test_adapter_map_has_all_types(self) -> None:
        for siem_type in SIEMType:
            assert siem_type in SIEM_ADAPTER_MAP, f"{siem_type} missing from SIEM_ADAPTER_MAP"

    def test_abstract_base_cannot_be_instantiated(self) -> None:
        with pytest.raises(TypeError):
            SIEMAdapter()  # type: ignore[abstract]
