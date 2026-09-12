"""Per-connector autonomous-execution opt-in.

A connector marked ``auto_approve`` runs its high-risk (write_high) tools without
a human approver in autonomous goals; everything else stays default-secure.
"""

from __future__ import annotations

import pytest

from app.agent.nodes._helpers import resolve_effective_tool_risk


# --- the pure risk-downgrade decision ---------------------------------------


@pytest.mark.parametrize("mode", ["bounded-autonomous", "fully-autonomous", "supervised"])
def test_connector_opt_in_downgrades_write_high(mode: str) -> None:
    assert (
        resolve_effective_tool_risk(
            "write_high",
            autonomy_mode=mode,
            connector_auto_approve=True,
            allow_fa_write_high=False,
        )
        == "write_low"
    )


def test_no_opt_in_keeps_write_high_gated() -> None:
    assert (
        resolve_effective_tool_risk(
            "write_high",
            autonomy_mode="bounded-autonomous",
            connector_auto_approve=False,
            allow_fa_write_high=False,
        )
        == "write_high"
    )


def test_fully_autonomous_flag_still_works() -> None:
    assert (
        resolve_effective_tool_risk(
            "write_high",
            autonomy_mode="fully-autonomous",
            connector_auto_approve=False,
            allow_fa_write_high=True,
        )
        == "write_low"
    )
    # flag only applies to fully-autonomous, not bounded
    assert (
        resolve_effective_tool_risk(
            "write_high",
            autonomy_mode="bounded-autonomous",
            connector_auto_approve=False,
            allow_fa_write_high=True,
        )
        == "write_high"
    )


@pytest.mark.parametrize("risk", ["read", "write_low", "destructive", "unknown"])
def test_non_write_high_risk_is_never_changed(risk: str) -> None:
    # destructive stays destructive even with opt-in — opt-in only relaxes write_high
    assert (
        resolve_effective_tool_risk(
            risk,
            autonomy_mode="bounded-autonomous",
            connector_auto_approve=True,
            allow_fa_write_high=True,
        )
        == risk
    )


# --- the flag propagates config -> tool -------------------------------------


def test_mcp_server_config_roundtrips_auto_approve() -> None:
    from app.mcp.registry import MCPServerConfig

    cfg = MCPServerConfig(name="Telegram", auto_approve=True)
    assert cfg.auto_approve is True
    restored = MCPServerConfig.model_validate_json(cfg.model_dump_json())
    assert restored.auto_approve is True
    # default-secure OFF
    assert MCPServerConfig(name="x").auto_approve is False


def test_tool_ref_defaults_auto_approve_false() -> None:
    from app.agent.tool_context import ToolRef

    t = ToolRef(server_id="s", server_name="Telegram", name="telegram_send_message",
                description="", input_schema={})
    assert t.auto_approve is False
    assert ToolRef(server_id="s", server_name="n", name="t", description="",
                   input_schema={}, auto_approve=True).auto_approve is True
