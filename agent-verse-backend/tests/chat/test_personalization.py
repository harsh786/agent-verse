"""Phase 11 — personalization: standing instructions + style injected per principal."""

from __future__ import annotations

import json
from typing import Any

from app.chat.personalization import (
    InMemoryPersonalizationStore,
    PersonalProfile,
    extract_standing_instruction,
    render_personalization_block,
)
from app.chat.service import ChatService


# ── extraction ───────────────────────────────────────────────────────────────

def test_extract_standing_instruction_detects_always_never() -> None:
    assert extract_standing_instruction("always book aisle seats") == "always book aisle seats"
    assert extract_standing_instruction("Please never call me after 9pm") == "never call me after 9pm"
    assert extract_standing_instruction("From now on, CC my partner") == "CC my partner"


def test_extract_standing_instruction_ignores_one_offs() -> None:
    assert extract_standing_instruction("book me a flight to NYC") is None
    assert extract_standing_instruction("what's the weather?") is None
    assert extract_standing_instruction("") is None


# ── rendering ────────────────────────────────────────────────────────────────

def test_render_block_empty_profile_is_blank() -> None:
    assert render_personalization_block(None) == ""
    assert render_personalization_block(PersonalProfile(principal_id="t1")) == ""


def test_render_block_includes_all_facets() -> None:
    prof = PersonalProfile(
        principal_id="t1",
        tone="concise",
        standing_instructions=["always book aisle seats"],
        preferences={"units": "metric"},
    )
    block = render_personalization_block(prof)
    assert "concise" in block
    assert "always book aisle seats" in block
    assert "units: metric" in block


# ── store ────────────────────────────────────────────────────────────────────

async def test_store_dedupes_standing_instructions() -> None:
    store = InMemoryPersonalizationStore()
    await store.add_standing_instruction("t1", "always book aisle seats")
    await store.add_standing_instruction("t1", "Always book aisle seats")  # dupe (case)
    prof = await store.get("t1")
    assert prof is not None
    assert prof.standing_instructions == ["Always book aisle seats"]


# ── end-to-end via run_qa ────────────────────────────────────────────────────

class _Provider:
    def __init__(self) -> None:
        self.requests: list[Any] = []

    async def complete(self, request: Any) -> Any:
        self.requests.append(request)

        class _R:
            content = "ok"

        return _R()


async def _collect(gen: Any) -> list[dict]:
    return [json.loads(f[len("data: "):].strip()) async for f in gen]


async def test_run_qa_learns_and_applies_standing_instruction() -> None:
    provider = _Provider()
    svc = ChatService(answer_generator=provider)
    session = svc.create_session("t1")

    # Turn 1: user states a durable preference.
    svc.save_message(session_id=session.id, tenant_id="t1", role="user",
                     content="always book aisle seats")
    await _collect(svc.run_qa(session_id=session.id, tenant_id="t1", message_id="m1",
                              user_message="always book aisle seats"))
    prof = await svc._personalization.get("t1")
    assert prof is not None and "always book aisle seats" in prof.standing_instructions

    # Turn 2: a later unrelated request carries the preference into the prompt.
    svc.save_message(session_id=session.id, tenant_id="t1", role="user",
                     content="book my flight to NYC")
    await _collect(svc.run_qa(session_id=session.id, tenant_id="t1", message_id="m2",
                              user_message="book my flight to NYC"))
    last_req = provider.requests[-1]
    joined = " ".join(str(m.content) for m in last_req.messages)
    assert "always book aisle seats" in joined
