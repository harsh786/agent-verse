"""Tool-call-budget convergence: keep delivery/action tools, drop read/search.

Regression for the agent never delivering its final answer: once the per-goal
tool-call budget was spent, the executor dropped ALL tools, so a goal whose last
step is a delivery action (e.g. telegram_send_message) could never complete —
the model could only describe the call in text, which was not dispatched.
"""

from __future__ import annotations

from app.agent.nodes._helpers import (
    select_action_tools_for_convergence,
    surface_delivered_content,
)
from app.providers.base import ToolDefinition


def _td(name: str) -> ToolDefinition:
    return ToolDefinition(name=name, description="", input_schema={})


def test_keeps_delivery_tool_drops_search_tools() -> None:
    tools = [
        _td("tavily_search"),
        _td("knowledge_search"),
        _td("tavily_extract"),
        _td("telegram_send_message"),
    ]
    kept = {t.name for t in select_action_tools_for_convergence(tools)}
    # Delivery ("send") survives; read/search tools are dropped.
    assert "telegram_send_message" in kept
    assert "tavily_search" not in kept
    assert "knowledge_search" not in kept


def test_all_read_tools_yields_empty() -> None:
    tools = [_td("web_search"), _td("get_page"), _td("list_files")]
    assert select_action_tools_for_convergence(tools) == []


def test_keeps_multiple_action_tools() -> None:
    tools = [_td("tavily_search"), _td("telegram_send_message"), _td("email_send")]
    kept = {t.name for t in select_action_tools_for_convergence(tools)}
    # Both "send" deliveries survive; the search tool is dropped.
    assert kept == {"telegram_send_message", "email_send"}


def test_surface_delivered_content_includes_sent_text() -> None:
    """The verifier must see WHAT was delivered, not just the receipt."""
    out = surface_delivered_content(
        raw_output="{'ok': True, 'message_id': 127}",
        tool="telegram_send_message",
        arguments={"text": "- Bullet one\n- Bullet two"},
        success=True,
    )
    assert "- Bullet one" in out
    assert "message_id" in out  # receipt still present


def test_surface_delivered_content_noop_on_failure_or_no_content() -> None:
    # Failed call → unchanged.
    assert (
        surface_delivered_content("err", "telegram_send_message", {"text": "hi"}, False) == "err"
    )
    # No text-bearing argument → unchanged.
    assert (
        surface_delivered_content("{'ok': True}", "some_tool", {"id": 5}, True) == "{'ok': True}"
    )
    # No arguments → unchanged.
    assert surface_delivered_content("{'ok': True}", "t", None, True) == "{'ok': True}"
