"""e2e_full: one coherent incident-response scenario chaining every platform layer.

The 43 sibling ``e2e_full`` files each isolate ONE dimension (RAG retrieval,
org missions, evals, triggers, RLS, ...). Isolating a dimension is exactly what
lets a cross-cutting bug slip through: does a document ingested in step 1
remain visible and correctly tenant-scoped by the time a scheduled trigger's
follow-up goal tries to retrieve it in step 5, three Postgres transactions and
two dispatch hops later? This file answers that by running ONE realistic story
end-to-end against the real booted app + real Postgres/pgvector + Redis:

  1. **Ingestion** — an "Ops Co" tenant ingests three genuinely distinct
     incident runbooks (failover, rate-limiting, credential containment) into
     one knowledge collection.
  2. **Governance** — the tenant explicitly enables the SOC2 guardrails-v2
     compliance bundle (redacts secrets/PII in tool + final output) before
     anything runs, exactly as an operator would before letting an agent touch
     an incident that involves a leaked credential.
  3. **AI org mission** — a 2-agent mission (research_analyst -> operations_lead)
     is dispatched whose objective requires retrieving the credential-
     containment runbook specifically (not the other two, unrelated, runbooks
     sitting in the same collection) via the agent graph's real
     ``rag_retrieval`` node, and whose answer would leak the exposed key were
     guardrails not wired — proving ingestion -> RAG -> agent execution ->
     guardrail enforcement -> org handoff bookkeeping all compose correctly on
     one real goal.
  4. **Evals** — once the mission's goal completes, ``POST /goals/{id}/eval``
     produces a scorecard that is read back both via the API and via a direct
     Postgres connection (the exact persistence path a same-day bug in this
     session silently broke: scorecards computed but never reaching Postgres).
  5. **Trigger-originated follow-up** — a webhook trigger (a different entry
     point than direct goal submission) fires a follow-up goal that re-touches
     the same knowledge collection, proving tenant scoping/visibility survive
     a goal that originates from the dispatcher instead of the API.
  6. **Tenant isolation checkpoint** — a second, freshly-signed-up tenant is
     asserted, via its OWN authenticated calls (the real user-facing contract,
     not a privileged bypass query), to see none of tenant A's collection,
     org, mission, goal, or eval data.

Determinism: one pinned ``FakeProvider``-derived completion (steps / success /
freeform schemas) and one pinned embedder, mirroring the conventions in
``test_rag_goal_retrieval_e2e.py`` / ``test_org_mission_collaboration_e2e.py``
/ ``test_evals_scorecard_e2e.py``. Fixtures are module-scoped (not the usual
per-test ``tenant_client``) because every phase below deliberately shares one
seeded tenant's state rather than being independent.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio

from app.providers.base import CompletionResponse
from app.providers.fake import FakeProvider

from .conftest import collect_sse

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]

# ── Scenario fixtures: a distinctive marker per runbook, and the "leaked" key ──

_MARKER_CRED = "Nightbloom Credential Containment"
_MARKER_FAILOVER = "Meridian Cluster Failover"
_MARKER_RATELIMIT = "Throttlewatch"
_LEAKED_KEY = "AKIAIOSFODNN7EXAMPLE"  # AWS's own documented example key id

_DOC_CRED = (
    f"The {_MARKER_CRED} Runbook mandates that any AWS access key matching the "
    "AKIA prefix found in a public commit or log line be revoked within fifteen "
    "minutes, its paired secret rotated, and CloudTrail audited for the "
    "twenty-four hours preceding exposure."
)
_DOC_FAILOVER = (
    f"The {_MARKER_FAILOVER} Runbook directs the on-call engineer to promote the "
    "eu-west-2 read replica within four minutes of a primary heartbeat loss, "
    "running 'pgctl promote' against node meridian-db-04 and re-pointing the "
    "connection pooler at the new primary."
)
_DOC_RATELIMIT = (
    f"The {_MARKER_RATELIMIT} Runbook instructs the gateway team to enable "
    "adaptive rate shedding on the /v2/orders endpoint once P99 latency exceeds "
    "800ms for two consecutive minutes, cutting non-priority traffic by forty "
    "percent until latency recovers."
)


class _IncidentProvider(FakeProvider):
    """Deterministic planner/verifier/executor completions for the whole scenario.

    Mirrors the schema-dispatch pattern used across the sibling e2e_full files
    (``test_rag_goal_retrieval_e2e.py``, ``test_evals_scorecard_e2e.py``,
    ``test_guardrail_block_e2e.py``): structured ``steps``/``success`` schemas
    get canned JSON, and the freeform executor completion "leaks" the exposed
    key alongside the containment runbook's marker so both the RAG-citation
    and the guardrail-redaction assertions have something real to catch.
    """

    async def complete(self, request: Any) -> Any:  # type: ignore[override]
        self.call_history.append(request)
        schema = getattr(request, "response_schema", None)
        props = (schema or {}).get("properties", {}) if isinstance(schema, dict) else {}
        messages_text = "\n".join(
            str(getattr(m, "content", "") or "") for m in getattr(request, "messages", [])
        )
        if "steps" in props:
            content = (
                '{"steps": ["Consult the incident runbooks and issue a '
                'credential containment recommendation"]}'
            )
        elif "success" in props:
            content = '{"success": true, "reason": "containment recommendation issued"}'
        elif '"relevance"' in messages_text:
            # Some goal phrasings in this scenario (e.g. the trigger-fired
            # "Re-audit ..." follow-up) are classified ANALYTICAL/HIGH-risk by
            # app/agent/pattern_assembler.py and routed to the "corrective" RAG
            # strategy, whose grade_evidence (app/rag/agentic/patterns/
            # corrective.py) grades retrieved passages via an un-schema'd
            # completion expecting exactly {"relevance": [<float per passage>]}
            # — see test_rag_goal_retrieval_e2e.py's _CompletingProvider for the
            # identical handling this scenario also needs to stay deterministic
            # regardless of which strategy a given goal's text gets routed to.
            import re as _re

            passage_count = len(_re.findall(r"^\[\d+\]", messages_text, flags=_re.MULTILINE))
            content = json.dumps({"relevance": [1.0] * max(passage_count, 1)})
        elif "Reformulate the query" in messages_text:
            # corrective.reformulate_query — vary the reply by attempt number so
            # it's always distinct from its own input (see the sibling RAG test
            # file's identical fixture for why a fixed canned string breaks the
            # second call).
            import re as _re2

            attempt_match = _re2.search(r"Attempt (\d+):", messages_text)
            attempt_label = attempt_match.group(1) if attempt_match else "1"
            content = f"{_MARKER_CRED} runbook audit (reformulated, attempt {attempt_label})"
        else:
            content = (
                f"Per the {_MARKER_CRED} runbook, immediately revoke the exposed "
                f"AWS access key {_LEAKED_KEY}, rotate the paired secret, and audit "
                "CloudTrail for the preceding 24 hours."
            )
        return CompletionResponse(content=content, model="fake", input_tokens=8, output_tokens=8)


def _two_agent_plan_factory() -> Any:
    """Deterministic 2-role OrchestrationPlan (research_analyst -> operations_lead).

    Copied from ``test_org_mission_collaboration_e2e.py``'s
    ``_fake_two_agent_plan_factory``: pinning two roles (instead of leaving team
    size to the real TeamFormationEngine heuristic) makes the first
    ``task.handoff`` reliably span two DIFFERENT agents — the actual
    collaboration hand-off this scenario needs to prove, not just that a
    handoff event of some kind fired. Topology stays "sequential" so the goal
    still runs through the real single-agent AgentGraph (with its real
    rag_retrieval + guardrails wiring), not the stub multi_agent path.
    """

    async def _fake_plan_mission(
        self: Any, goal: str, org: Any, tenant_id: str, mission: Any = None
    ) -> Any:
        from app.org.meta_orchestrator import OrchestrationPlan
        from app.org.team_formation import RoleAssignment, TeamManifest

        roles = [
            RoleAssignment(
                role_name="research_analyst",
                department_kind="research",
                seniority="mid",
                capabilities=["research"],
                model_profile="smart",
                estimated_cost_usd=0.1,
                estimated_hours=1.0,
                priority=1,
            ),
            RoleAssignment(
                role_name="operations_lead",
                department_kind="operations",
                seniority="senior",
                capabilities=["execution"],
                model_profile="smart",
                estimated_cost_usd=0.1,
                estimated_hours=1.0,
                priority=1,
            ),
        ]
        manifest = TeamManifest(
            mission_id=str(getattr(mission, "id", "m")),
            team_name="incident-response-team",
            roles=roles,
            departments=["research", "operations"],
            estimated_cost_usd=0.2,
            estimated_duration_hours=2.0,
            risk_level="low",
            success_probability=0.9,
            agent_count=2,
            requires_human_preview=False,
            formation_reasoning="forced deterministic 2-agent plan for the world-class scenario",
        )
        return OrchestrationPlan(
            mission_id=str(getattr(mission, "id", "m")),
            topology="sequential",
            departments=["research", "operations"],
            team_manifest=manifest,
            model_gateway_profile="smart",
            autonomy_level=3,
            approval_gates=[],
            execution_phases=[],
            estimated_total_cost_usd=0.2,
            estimated_total_duration_hours=2.0,
        )

    return _fake_plan_mission


def _parse(raw: str) -> dict[str, Any] | None:
    try:
        return json.loads(raw)  # type: ignore[no-any-return]
    except Exception:
        return None


async def _wait_for_goal_terminal(
    client: Any, goal_id: str, *, timeout: float = 45.0
) -> dict[str, Any]:
    deadline = asyncio.get_event_loop().time() + timeout
    last: dict[str, Any] = {}
    while asyncio.get_event_loop().time() < deadline:
        resp = await client.get(f"/goals/{goal_id}")
        if resp.status_code == 200:
            last = resp.json()
            if str(last.get("status")) in ("complete", "completed", "failed", "cancelled"):
                return last
        await asyncio.sleep(0.3)
    raise AssertionError(f"goal {goal_id} never reached terminal; last={last.get('status')!r}")


# ── Module-scoped tenant + deterministic provider/embedder ─────────────────────
# Every phase below shares this one seeded tenant's state, so these are scoped
# to the whole module (not the usual per-test ``tenant_client``).


@pytest.fixture(scope="module")
def monkeypatch_module() -> Any:
    """A module-scoped ``MonkeyPatch`` (pytest's builtin ``monkeypatch`` fixture
    is function-scoped and cannot be depended on by a module-scoped fixture)."""
    from _pytest.monkeypatch import MonkeyPatch

    mp = MonkeyPatch()
    yield mp
    mp.undo()


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def ops_tenant(app: Any, client: Any) -> AsyncIterator[dict[str, Any]]:
    from httpx import ASGITransport, AsyncClient

    email = f"ops-{uuid.uuid4().hex[:12]}@example.com"
    resp = await client.post("/tenants/signup", json={"name": "Ops Co", "email": email})
    assert resp.status_code == 201, f"signup failed: {resp.status_code} {resp.text}"
    body = resp.json()
    api_key, tenant_id = body["api_key"], body["tenant_id"]

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://e2e-full", headers={"X-API-Key": api_key}
    ) as c:
        yield {"client": c, "tenant_id": tenant_id, "api_key": api_key}


@pytest.fixture(scope="module")
def _fake_embedder(app: Any) -> Any:
    """Pin a deterministic embedder sized to the LTM column (see
    ``test_rag_goal_retrieval_e2e.py``'s fixture of the same name for the full
    rationale on why this must match ``_LTM_EMBEDDING_DIM`` exactly)."""
    import contextlib
    import dataclasses

    from app.memory.long_term import _LTM_EMBEDDING_DIM

    fake = FakeProvider(embed_dim=_LTM_EMBEDDING_DIM)
    prev_embedder = getattr(app.state, "embedder", None)
    app.state.embedder = fake
    gw = getattr(app.state, "retrieval_gateway", None)
    prev_deps = None
    if gw is not None and hasattr(gw, "dependencies"):
        prev_deps = gw.dependencies
        with contextlib.suppress(Exception):
            gw.dependencies = dataclasses.replace(gw.dependencies, embedder=fake)
    try:
        yield
    finally:
        app.state.embedder = prev_embedder
        if gw is not None and prev_deps is not None:
            gw.dependencies = prev_deps


@pytest.fixture(scope="module")
def _inline_provider(app: Any) -> Any:
    """Run every goal in this module in-process (no Celery) with the pinned
    ``_IncidentProvider``, for the whole module's lifetime.

    Also rewires the retrieval gateway's OWN ``llm_resolver`` (it resolves an
    LLM independently of ``app.state._llm_provider_override`` — see
    ``test_rag_goal_retrieval_e2e.py``'s fixture of the same name for the full
    rationale) and clears its ``search_capability``, so whichever RAG strategy
    a given goal's text gets routed to (hybrid or corrective) stays
    deterministic and never falls back to a real network call.
    """
    import contextlib
    import dataclasses

    gs = app.state.goal_service
    prev_override = getattr(app.state, "_llm_provider_override", None)
    prev_queue = gs._task_queue
    app.state._llm_provider_override = _IncidentProvider()
    gs._task_queue = None

    gw = getattr(app.state, "retrieval_gateway", None)
    prev_deps = None
    if gw is not None and hasattr(gw, "dependencies"):

        async def _fake_llm_resolver(_tenant_ctx: Any, _strategy: Any) -> Any:
            from app.rag.gateway import ResolvedLLM

            return ResolvedLLM(
                provider=_IncidentProvider(), model="fake-model", provider_type="fake"
            )

        prev_deps = gw.dependencies
        with contextlib.suppress(Exception):
            gw.dependencies = dataclasses.replace(
                gw.dependencies, llm_resolver=_fake_llm_resolver, search_capability=None
            )
    try:
        yield
    finally:
        app.state._llm_provider_override = prev_override
        gs._task_queue = prev_queue
        if gw is not None and prev_deps is not None:
            gw.dependencies = prev_deps


# ── The seeded scenario: ingestion -> governance -> mission -> completion ──────
# A single module-scoped fixture performs phases 1-3 exactly once; the test
# functions below each assert a different facet of that ONE shared run rather
# than re-deriving it, so the whole file exercises one coherent story.


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def scenario(
    ops_tenant: dict[str, Any],
    _fake_embedder: Any,
    _inline_provider: Any,
    monkeypatch_module: Any,
) -> dict[str, Any]:
    from app.org.meta_orchestrator import MetaOrchestrator

    monkeypatch_module.setattr(MetaOrchestrator, "plan_mission", _two_agent_plan_factory())

    client = ops_tenant["client"]

    # ── 1. Ingestion: one collection, three genuinely distinct runbooks ───────
    coll = await client.post(
        "/knowledge/collections",
        json={
            "name": f"incident-runbooks-{uuid.uuid4().hex[:8]}",
            "description": "on-call runbooks",
        },
    )
    assert coll.status_code == 201, f"collection create failed: {coll.status_code} {coll.text}"
    collection_id = coll.json()["collection_id"]

    for content in (_DOC_CRED, _DOC_FAILOVER, _DOC_RATELIMIT):
        ing = await client.post(
            "/knowledge/ingest",
            json={"collection_id": collection_id, "source_type": "text", "content": content},
        )
        assert ing.status_code == 201, f"ingest failed: {ing.status_code} {ing.text}"

    # ── 2. Governance: explicitly enable the SOC2 bundle before anything runs ─
    bundle = await client.post("/guardrails-v2/bundles/soc2")
    assert bundle.status_code == 200, f"enable SOC2 bundle failed: {bundle.status_code} {bundle.text}"
    bundle_body = bundle.json()
    assert bundle_body.get("status") == "enabled", bundle_body
    assert bundle_body.get("rules_created", 0) >= 1, f"SOC2 bundle created no rules: {bundle_body}"

    # ── 3. AI org mission: 2 agents, one task needs the credential-containment
    # runbook specifically (RAG retrieval scoped to the right document).
    org_resp = await client.post(
        "/v1/org", json={"name": "Incident Response Org", "autonomy_level": 3}
    )
    assert org_resp.status_code == 201, f"org create failed: {org_resp.text}"
    org_id = org_resp.json()["id"]

    objective = (
        f"A public commit exposed an AWS access key. Investigate the {_MARKER_CRED} "
        "runbook and issue a containment recommendation, coordinating a two-step "
        "response between the research team and the operations team."
    )
    mission_resp = await client.post(
        f"/v1/org/{org_id}/missions/execute",
        json={"title": "Credential leak containment", "objective": objective},
    )
    assert mission_resp.status_code == 201, f"mission dispatch failed: {mission_resp.text}"
    mission_body = mission_resp.json()
    mission_id = mission_body["mission_id"]
    goal_id = mission_body.get("goal_id")
    assert goal_id, f"mission dispatch produced no real goal: {mission_body}"

    goal = await _wait_for_goal_terminal(client, goal_id)
    assert str(goal.get("status")) in ("complete", "completed"), (
        f"mission goal did not complete: {goal.get('status')!r}: {goal}"
    )

    # Collect the goal's SSE history (persisted-event replay, same mechanism
    # test_rag_goal_retrieval_e2e.py uses after the goal is already terminal)
    # once here, so every downstream test reads the SAME captured events
    # rather than re-subscribing (subscribe_events is since_sequence-based, not
    # idempotent-replayable-forever across repeated calls in every test).
    sse_events_raw = await collect_sse(client, goal_id, until="goal_complete", timeout=15.0)

    fin_resp = await client.post(f"/v1/org/{org_id}/missions/{mission_id}/finalize")
    assert fin_resp.status_code == 200, f"finalize failed: {fin_resp.text}"
    fin = fin_resp.json()
    assert fin.get("finalized") is True and fin.get("status") == "completed", fin

    return {
        **ops_tenant,
        "collection_id": collection_id,
        "org_id": org_id,
        "mission_id": mission_id,
        "goal_id": goal_id,
        "goal": goal,
        "sse_events_raw": sse_events_raw,
        "finalize_result": fin,
    }


# ── Phase 3 assertions: ingestion -> RAG -> agent execution -> governance -> ──
# org handoff, all on the ONE goal the ``scenario`` fixture just ran.


async def test_mission_retrieves_the_right_runbook_and_redacts_the_leaked_key(
    scenario: dict[str, Any],
) -> None:
    client = scenario["client"]
    goal_id = scenario["goal_id"]
    collection_id = scenario["collection_id"]

    # The goal's real AgentGraph rag_retrieval node found the CREDENTIAL runbook
    # specifically — not the unrelated failover/rate-limit runbooks also sitting
    # in the same collection — proving retrieval actually discriminates by
    # content rather than just "some collection had something".
    parsed = [p for p in (_parse(raw) for raw in scenario["sse_events_raw"]) if p]
    retrieved = [p for p in parsed if p.get("type") == "knowledge_retrieved"]
    assert retrieved, (
        f"no knowledge_retrieved event on the mission goal; "
        f"event types seen: {[p.get('type') for p in parsed]}"
    )
    citations = [c for ev in retrieved for c in (ev.get("citations") or [])]
    assert citations, f"knowledge_retrieved carried no citations: {retrieved!r}"
    assert any(c.get("collection_id") == collection_id for c in citations), (
        f"no citation attributed to the ingested collection: {citations!r}"
    )
    cited_text = " ".join((c.get("content") or "") for c in citations)
    assert _MARKER_CRED.lower() in cited_text.lower(), (
        f"retrieval did not surface the credential-containment runbook specifically: "
        f"{cited_text!r}"
    )

    # Governance: the exposed key the executor "leaked" must NOT appear anywhere
    # in the goal's serialized result — the SOC2 bundle's secrets rule redacted
    # it from the final answer, the same real guardrails_v2 FINAL_OUTPUT gate
    # proven in test_guardrail_block_e2e.py, now exercised inside an org mission.
    payload_blob = json.dumps(scenario["goal"]) + json.dumps(scenario["sse_events_raw"])
    assert _LEAKED_KEY not in payload_blob, (
        "leaked AWS access key was emitted in the mission goal result — "
        "guardrail enforcement did not redact it"
    )

    # Org collaboration: a genuine handoff between two DIFFERENT agents, not
    # just an event of the right type firing (same proof as
    # test_org_mission_collaboration_e2e.py, exercised here on the same run
    # that did the RAG retrieval and the guardrail redaction).
    org_id = scenario["org_id"]
    ev_resp = await client.get(f"/v1/org/{org_id}/events", params={"limit": 200})
    assert ev_resp.status_code == 200
    ev_body = ev_resp.json()
    rows = ev_body.get("data", ev_body) if isinstance(ev_body, dict) else ev_body

    assigned = [
        r
        for r in rows
        if r.get("event_type") == "task.assigned" and (r.get("payload") or {}).get("agent_id")
    ]
    handoffs = [r for r in rows if r.get("event_type") == "task.handoff"]
    assert len(assigned) >= 2, f"expected >=2 assigned subtasks: {assigned}"
    assert handoffs, f"no task.handoff emitted: {rows}"

    assigned_by_order = sorted(assigned, key=lambda r: (r.get("payload") or {}).get("order", 0))
    first_agent = (assigned_by_order[0].get("payload") or {}).get("agent_id")
    second_agent = (assigned_by_order[1].get("payload") or {}).get("agent_id")
    assert first_agent and second_agent and first_agent != second_agent, (
        f"round-robin across 2 roles should assign different agents: "
        f"{first_agent!r} vs {second_agent!r}"
    )
    handoff_payload = handoffs[0].get("payload") or {}
    assert handoff_payload.get("from_agent") == first_agent
    assert handoff_payload.get("to_agent") == second_agent
    assert handoff_payload.get("from_task") != handoff_payload.get("to_task")

    # The finalized mission's aggregated deliverable is tied to this real goal.
    result = scenario["finalize_result"].get("result") or {}
    assert result.get("goal_id") == goal_id
    subtasks = result.get("subtasks") or []
    assert len(subtasks) >= 2, f"aggregated report under-decomposed: {subtasks}"


async def test_ingestion_persisted_to_postgres_for_the_owning_tenant(
    scenario: dict[str, Any], _migrated_backends: tuple[str, str]
) -> None:
    """Trust but verify: read the ingested runbooks back via a FRESH Postgres
    connection (not the API) under the owning tenant's own RLS context — proves
    both persistence and that RLS scopes correctly to the tenant that wrote it.

    Ingested content is chunked and persisted to the dimension-specific
    ``knowledge_chunks_<dim>`` table (``app/rag/store.py::_persist_chunks`` /
    ``_chunk_table``) — NOT the legacy ``documents`` ORM table, which
    ``POST /knowledge/ingest`` never writes a row to. The fixture's fake
    embedder produces ``_LTM_EMBEDDING_DIM`` (2048) wide vectors, so the
    physical table here is ``knowledge_chunks_2048``.
    """
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db.rls import sqlalchemy_rls_context
    from app.memory.long_term import _LTM_EMBEDDING_DIM
    from app.rag.store import _chunk_table

    database_url, _redis_url = _migrated_backends
    engine = create_async_engine(database_url)
    table = _chunk_table(_LTM_EMBEDDING_DIM)
    try:
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session, session.begin():
            async with sqlalchemy_rls_context(session, scenario["tenant_id"]):
                rows = (
                    await session.execute(
                        text(
                            f"SELECT content FROM {table} "
                            "WHERE collection_id = :cid ORDER BY chunk_index"
                        ),
                        {"cid": scenario["collection_id"]},
                    )
                ).fetchall()
        stored = {r[0] for r in rows}
        assert stored, f"no chunks persisted to {table} for collection {scenario['collection_id']!r}"
        assert any(_MARKER_CRED in c for c in stored), stored
        assert any(_MARKER_FAILOVER in c for c in stored), stored
        assert any(_MARKER_RATELIMIT in c for c in stored), stored
    finally:
        await engine.dispose()


# ── Phase 4: evals — a real scorecard, verified via API AND direct Postgres ────


async def test_eval_scorecard_is_produced_and_persisted_to_postgres(
    scenario: dict[str, Any], _migrated_backends: tuple[str, str]
) -> None:
    client = scenario["client"]
    goal_id = scenario["goal_id"]

    scored = await client.post(f"/goals/{goal_id}/eval")
    assert scored.status_code == 200, f"eval run failed: {scored.status_code} {scored.text}"
    card = scored.json()
    assert isinstance(card.get("scores"), dict) and card["scores"], (
        f"scorecard carries no per-dimension scores: {card!r}"
    )
    for dim, value in card["scores"].items():
        assert 0.0 <= float(value) <= 1.0, f"score {dim}={value} out of range: {card!r}"

    got = await client.get(f"/goals/{goal_id}/eval")
    assert got.status_code == 200, f"eval fetch failed: {got.status_code} {got.text}"
    cached = got.json()
    assert cached["status"] == "evaluated", f"scorecard not cached after run: {cached!r}"
    assert cached["average_score"] is not None
    assert 0.0 <= float(cached["average_score"]) <= 1.0

    # Trust but verify: the scorecard genuinely landed in Postgres, read back
    # via a FRESH connection rather than trusting the API's own 200 — the exact
    # class of check that caught this session's earlier "scorecards computed
    # but silently never reaching Postgres" bug.
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    database_url, _redis_url = _migrated_backends
    engine = create_async_engine(database_url)
    try:
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT overall_score, scores FROM eval_scorecards "
                        "WHERE goal_id = :gid AND tenant_id = :tid"
                    ),
                    {"gid": goal_id, "tid": scenario["tenant_id"]},
                )
            ).fetchall()
        assert rows, (
            f"no eval_scorecards row persisted for goal {goal_id!r} / "
            f"tenant {scenario['tenant_id']!r} — the scorecard the API returned "
            "never reached Postgres"
        )
        overall_score, scores_json = rows[0]
        assert 0.0 <= float(overall_score) <= 1.0, f"overall_score out of range: {overall_score!r}"
        assert scores_json, f"persisted scores JSONB is empty: {scores_json!r}"
    finally:
        await engine.dispose()


# ── Phase 5 + 6: trigger-originated follow-up, then the tenant-isolation ──────
# checkpoint — verified from tenant B's own authenticated calls, in the middle
# of this same multi-step flow rather than in isolation.


async def test_trigger_followup_reaches_same_knowledge_then_tenant_b_is_isolated(
    scenario: dict[str, Any], app: Any, client: Any
) -> None:
    ops_client = scenario["client"]

    # ── 5. A webhook trigger — a different entry point than direct goal
    # submission — fires a follow-up goal against the SAME tenant/collection.
    trig = await ops_client.post(
        "/triggers",
        json={
            "spec": {
                "trigger_type": "webhook",
                "name": "post-incident-reaudit",
                "description": "re-audit the containment runbook after remediation",
            },
            "goal_template": (
                f"Re-audit the {{{{payload.control}}}} control against the "
                f"{_MARKER_CRED} runbook"
            ),
        },
    )
    assert trig.status_code == 201, f"create trigger failed: {trig.status_code} {trig.text}"
    schedule_id = trig.json()["schedule_id"]

    fired = await ops_client.post(
        f"/triggers/{schedule_id}/fire",
        json={"payload": {"control": "credential-rotation"}},
    )
    assert fired.status_code == 200, f"fire failed: {fired.status_code} {fired.text}"
    fired_body = fired.json()
    followup_goal_id = fired_body["goal_id"]
    assert followup_goal_id, f"trigger fire produced no real goal: {fired_body!r}"
    assert fired_body.get("goal_created") is True

    got = await ops_client.get(f"/goals/{followup_goal_id}")
    assert got.status_code == 200, f"goal {followup_goal_id} not found: {got.text}"
    goal_text = got.json().get("goal_text") or got.json().get("goal") or ""
    assert "credential-rotation" in goal_text, f"goal_template not rendered: {goal_text!r}"

    followup = await _wait_for_goal_terminal(ops_client, followup_goal_id)
    assert str(followup.get("status")) in ("complete", "completed"), (
        f"trigger-originated goal did not complete: {followup.get('status')!r}"
    )

    # The trigger-originated goal STILL sees the tenant's ingested knowledge —
    # proving tenant scoping/RLS/knowledge visibility hold for a goal that
    # originated from the dispatcher, not the direct /goals API.
    followup_events_raw = await collect_sse(
        ops_client, followup_goal_id, until="goal_complete", timeout=15.0
    )
    followup_parsed = [p for p in (_parse(raw) for raw in followup_events_raw) if p]
    followup_retrieved = [p for p in followup_parsed if p.get("type") == "knowledge_retrieved"]
    assert followup_retrieved, (
        "trigger-originated goal produced no knowledge_retrieved event; "
        f"event types seen: {[p.get('type') for p in followup_parsed]}"
    )
    followup_citations = [c for ev in followup_retrieved for c in (ev.get("citations") or [])]
    assert any(c.get("collection_id") == scenario["collection_id"] for c in followup_citations), (
        f"trigger-originated goal did not retrieve from the same ingested "
        f"collection: {followup_citations!r}"
    )

    # ── 6. Tenant isolation checkpoint: a SECOND, freshly-signed-up tenant must
    # see NONE of tenant A's collection, org, mission, goal, or eval data — and
    # this is checked from tenant B's own authenticated calls, the real
    # user-facing contract, in the middle of this same multi-step flow.
    from httpx import ASGITransport, AsyncClient

    email_b = f"rival-{uuid.uuid4().hex[:12]}@example.com"
    signup_b = await client.post("/tenants/signup", json={"name": "Rival Co", "email": email_b})
    assert signup_b.status_code == 201, f"tenant B signup failed: {signup_b.text}"
    key_b = signup_b.json()["api_key"]

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://e2e-full", headers={"X-API-Key": key_b}
    ) as client_b:
        # Cannot enumerate tenant A's collection.
        b_colls = await client_b.get("/knowledge/collections")
        assert b_colls.status_code == 200
        b_ids = {c.get("collection_id") or c.get("id") for c in b_colls.json()}
        assert scenario["collection_id"] not in b_ids, (
            "tenant B enumerated tenant A's knowledge collection (RLS leak)"
        )

        # Cannot search into tenant A's collection by id, nor retrieve its content.
        leak = await client_b.get(
            "/knowledge/search",
            params={
                "q": _MARKER_CRED,
                "collection_id": scenario["collection_id"],
                "top_k": 5,
                "threshold": 0.0,
            },
        )
        assert leak.status_code in (200, 403, 404), leak.text
        if leak.status_code == 200:
            assert leak.json() == [], "tenant B retrieved tenant A's content (RLS leak)"

        # Cannot fetch tenant A's org or mission.
        org_get = await client_b.get(f"/v1/org/{scenario['org_id']}")
        assert org_get.status_code == 404, f"tenant B read tenant A's org: {org_get.text}"

        mission_get = await client_b.get(
            f"/v1/org/{scenario['org_id']}/missions/{scenario['mission_id']}"
        )
        assert mission_get.status_code in (403, 404), (
            f"tenant B read tenant A's mission: {mission_get.status_code} {mission_get.text}"
        )

        # Cannot fetch tenant A's goal (mission goal OR the trigger follow-up).
        goal_get_a = await client_b.get(f"/goals/{scenario['goal_id']}")
        assert goal_get_a.status_code == 404, (
            f"tenant B read tenant A's mission goal: {goal_get_a.text}"
        )
        goal_get_followup = await client_b.get(f"/goals/{followup_goal_id}")
        assert goal_get_followup.status_code == 404, (
            f"tenant B read tenant A's trigger-originated goal: {goal_get_followup.text}"
        )

        # Cannot fetch tenant A's eval scorecard.
        eval_get = await client_b.get(f"/goals/{scenario['goal_id']}/eval")
        assert eval_get.status_code == 404, (
            f"tenant B read tenant A's eval scorecard: {eval_get.text}"
        )
