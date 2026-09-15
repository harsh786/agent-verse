"""Phase 5 — chat skill registry (command-surface foundation)."""

from __future__ import annotations

import pytest

from app.chat.skills.registry import ChatSkill, SkillRegistry


def _skill(name: str, scope: str | None = None):  # type: ignore[no-untyped-def]
    async def handler(**kwargs):  # type: ignore[no-untyped-def]
        return {"called": name, "kwargs": kwargs}

    return ChatSkill(name=name, description=f"do {name}", handler=handler,
                     args={"x": "an input"}, scope=scope)


async def test_register_get_list_and_dispatch() -> None:
    reg = SkillRegistry()
    reg.register(_skill("list_agents"))
    reg.register(_skill("run_workflow"))
    assert [s.name for s in reg.list()] == ["list_agents", "run_workflow"]
    assert reg.get("list_agents") is not None
    result = await reg.dispatch("run_workflow", x=1)
    assert result == {"called": "run_workflow", "kwargs": {"x": 1}}


async def test_unknown_skill_raises() -> None:
    reg = SkillRegistry()
    with pytest.raises(KeyError, match="unknown skill"):
        await reg.dispatch("nope")


def test_duplicate_registration_rejected() -> None:
    reg = SkillRegistry()
    reg.register(_skill("dup"))
    with pytest.raises(ValueError, match="duplicate skill"):
        reg.register(_skill("dup"))


def test_describe_for_prompt_is_scope_filtered() -> None:
    reg = SkillRegistry()
    reg.register(_skill("public_skill"))                         # no scope -> always shown
    reg.register(_skill("admin_skill", scope="governance:admin"))
    full = reg.describe_for_prompt()
    assert "public_skill" in full and "admin_skill" in full
    limited = reg.describe_for_prompt(scopes=frozenset({"other"}))
    assert "public_skill" in limited and "admin_skill" not in limited
    assert "x: an input" in full  # arg schema rendered
