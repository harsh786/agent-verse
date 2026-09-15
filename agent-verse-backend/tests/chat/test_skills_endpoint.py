"""Phase 5 — ChatService.list_skills discovery."""

from __future__ import annotations

from app.chat.service import ChatService
from app.chat.skills.registry import ChatSkill, SkillRegistry


def _reg() -> SkillRegistry:
    reg = SkillRegistry()

    async def _h(**_kw: object) -> None:
        return None

    reg.register(ChatSkill(name="submit_goal", description="run a goal", handler=_h,
                           args={"goal": "what"}, scope="goals:write"))
    reg.register(ChatSkill(name="pub", description="public", handler=_h))
    return reg


def test_list_skills_returns_registered_skills() -> None:
    svc = ChatService(skill_registry=_reg())
    skills = svc.list_skills()
    names = {s["name"] for s in skills}
    assert names == {"submit_goal", "pub"}
    goal = next(s for s in skills if s["name"] == "submit_goal")
    assert goal["scope"] == "goals:write" and goal["args"] == {"goal": "what"}


def test_list_skills_scope_filtered() -> None:
    svc = ChatService(skill_registry=_reg())
    limited = svc.list_skills(scopes=frozenset({"other"}))
    assert {s["name"] for s in limited} == {"pub"}  # scoped skill hidden


def test_list_skills_empty_without_registry() -> None:
    assert ChatService().list_skills() == []
