# AI Org Team — Deep Gap Analysis & World-Class Implementation Plan

**Date:** 2026-08-20
**Owner:** Copilot (analysis mode)
**Sources analyzed:**
- Spec: `agent-verse-backend/docs/superpowers/specs/2026-08-17-ai-organization-os-design.md` (16,745 lines)
- Related specs reviewed: `2026-08-18-ui-ux-revamp`, `2026-08-19-jarvis-visualization-master`, `2026-08-18-world-class-engineering`
- Backend: `agent-verse-backend/app/org/` (38 Python files)
- Frontend: `agent-verse-frontend/src/features/org/` (24 component files)

**User's complaint, verbatim:**
> "AI org team itself not working with their core feature. No team has been created. No agents are created by planner. No results AI org team is doing. Templates, debates, auto discovery — nothing is there. Whole icons on main AI org team page inside not working."

---

## TL;DR — THE SINGLE ROOT CAUSE

The AI Org Team pipeline has **three independent break-points**, any one of which on its own is enough to make the feature feel "dead". **All three are present:**

```mermaid
flowchart LR
  A[POST /v1/org/:org/missions/execute] --> B[OrgService.create_mission_and_execute]
  B --> C[MetaOrchestrator.plan_mission → TeamManifest] %% ✅ WORKS — produces blueprint
  C --> D[TeamFormationEngine.form_team returns dataclass] %% ✅ WORKS — but no agent rows created
  D -. "❌ BREAK #1" .-> E[(No actual Agent rows spawned in AgentStore)]
  B --> F[GoalService.submit_goal with execution_context] %% ✅ WORKS — ctx carried
  F --> G[_run_agent_loop → AgentGraph.run] %% ✅ WORKS — but...
  G -. "❌ BREAK #2" .-> H[(AgentGraph ignores team_manifest entirely)]
  E -. feeds nothing .-> H
  H -. "❌ BREAK #3" .-> I[(No /teams or /agents GET endpoints on frontend)]
  I --> J[TeamPage renders Mock members<br/>AgentProfile shows empty shell<br/>MissionPage has 0 teams, 0 results]
```

**Net result on the screen:** Mission is created and dispatched (real), goal runs (real), but
**no agents are visible, no teams graph forms, no results attribution, no debate, no auto-discovery.**

---

## SECTION 1 — DEEP GAP ANALYSIS

### 1.1 The Spec's Core Promise (PART 1, Executive Summary)

> "**The gap is not capability. The gap is organization.**" (spec line ~120)
>
> User experience example: "Launch our new product in Germany" → System assembles Strategy +
> Market Research + Legal + Product + Engineering + Marketing + Finance automatically. Each agent
> uses the optimal LLM for its role. User watches a live org chart animate as the company goes
> to work.

That promise depends on **one architectural primitive**: `Goal → Capability → Role → Agent →
Model → Tool → Team → Workflow → Outcome` (the **9-link chain** on spec PART 1 line ~175).

Of these 9 links, 5 are implemented end-to-end (`Goal`, `Capability Registry`, `Role Taxonomy`,
`Tool`, `Workflow`). 4 are **structurally broken** in `app/org/`:

| Link in chain | Status | File | Why broken |
|---|---|---|---|
| Goal | ✅ | `app/agent/graph.py` | Plan→execute→verify works |
| Capability | ✅ | `app/org/capability_registry.py` (517 lines) | 80+ capabilities declared |
| Role | ✅ | `app/org/role_taxonomy.py` (399 lines) + `roles.py` | 22 depts, 456 roles declared |
| **Agent** | ❌ | none | TeamManifest lists roles, **no Agents are instantiated** |
| Model | ✅ | `app/org/model_gateway.py` | Per-role routing works in stub form |
| Tool | ✅ | `app/mcp/` | MCP works |
| **Team** | ❌ | `app/org/team_formation.py` | Returns manifest, **no OrgTeam row created from manifest** |
| **Workflow** | ⚠️ | `app/org/workflow_steps.py` (170 lines) | Steps defined, not bound to team agents |
| **Outcome** | ❌ | none | Tasks created with `actor_agent_id = None`; no result attribution |

### 1.2 The Three Break-Points — with file:line evidence

#### ❌ BREAK #1 — TeamManifest never materializes into Agent rows
**Location:** `app/org/team_formation.py:301-385` and `app/org/service.py` (no agent spawning)

```python
# app/org/team_formation.py (simplified)
class TeamFormationEngine:
    async def form_team_for_goal(self, mission, org) -> TeamManifest:
        # ...returns a TeamManifest dataclass with roles=[...]
        # No call to AgentStore, no INSERT into agents table anywhere
```

**Evidence:**
- `grep -nE "create_agent|spawn_agent|AgentRecord|AgentStore" app/org/service.py` → **0 hits**
- `grep -nE "create_agent|spawn_agent" app/org/team_formation.py` → **0 hits**
- `app/org/models.py` `OrgTeam` table has `member_agent_ids = Column(ARRAY(String))` but nobody writes to it from a mission dispatch path.

**Result:** The MetaOrchestrator produces a beautiful blueprint (`topology`, `departments`,
`agent_count`, `estimated_cost_usd`). The blueprint is captured into `execution_context["orchestration_plan"]`
and shipped to AgentGraph — but **it is never consumed downstream**:

```
grep -rnE "orchestration_plan|team_manifest" app/ --include='*.py' | grep -v app/org/ → 0 hits
```

#### ❌ BREAK #2 — AgentGraph ignores the team blueprint
**Location:** `app/agent/graph.py` and `app/agent/loop.py`

The org dispatch path passes `execution_context={"org_id":..., "mission_id":..., "orchestration_plan": plan_summary}`
in `execution_ctx` — verified at `app/org/service.py:1208-1214` and preserved into `GoalRecord.execution_context`
at `app/services/goal_service.py:2319`. The AgentGraph does **NOT** read `orchestration_plan` to:
- instantiate one LangGraph agent per role in the manifest
- route the mission to multiple agents concurrently
- attribute sub-results to specific roles/agents
- produce team-relevant events for the frontend

**Result:** Even when a mission "runs", it runs as a single-agent LangGraph loop — exactly the
legacy goal-execute flow with `org_id` decorated on metadata. No team forms. No debate. No
multi-agent reasoning. From the user's perspective: nothing happening.

#### ❌ BREAK #3 — No `/teams` or `/agents` GET endpoints exposed to the frontend
**Location:** `agent-verse-frontend/src/features/org/api.ts` — `orgApi` ends at `listEvents`.

**Frontend `orgApi` object literally has only these methods** (verified):
- `create`, `list`, `get`, `update`, `delete` (Orgs)
- `health`
- `createDepartment`, `listDepartments`
- `createMission` (→ POST `/missions/execute`), `listMissions`, `getMission`, `updateMissionStatus`
- `listTasks`, `updateTaskStatus`
- `listEvents`

**MISSING from frontend `orgApi`:**
- `listTeams`, `getTeam`, `createTeam`, `updateTeamLifecycle`
- `listAgents`, `getAgent`, `getAgentReputation`, `getAgentMemory`, `getAgentTools`
- `getDigest`, `getModelGateway`, `getMemory`, `listDecisions`, `getDecisionLog`
- `getOrgIntelligence`, `getDigitalTwin`, `getOrgChart` (graph data)

**Evidence on the frontend:**
- `TeamPage.tsx:97` literally says `// Mock members until team endpoint is available`
- `AgentProfile.tsx`: `grep -nE "useAgent|useQuery|orgApi\.|fetchAgent|getAgent"` → **0 hits** — pure shell component, no hooks at all
- `dashboards/` has 7 files (CEO/CTO/CMO/CFO/HRO/Sales/DevOps) but they're scaffolded shells with placeholder data — verified by `grep -nE "mock|placeholder" src/features/org/dashboards/*`

**Note about the user's "icons not working" complaint:** The header icons in `OrgPage.tsx`
lines 142-296 DO toggle panels open (Voice, Graphify, Connectors, Twin, Commands, History,
Obsidian, Approvals). The perception that they're "not working" comes from the panels being
empty shells — they open but show no data because:
- `TeamPage` shows mock members (no endpoint)
- `AgentProfile` shows nothing (no hook)
- `MorningBrief` calls `orgApi.getDigest` which **doesn't exist** on `orgApi`
- `CommandBar` has no way to call the universal command endpoint
- Digested org intelligence, memory, model gateway, decision logs — none are in `orgApi`

### 1.3 Spec PART 4 — Templates, Departments (22), Roles (456)

**Status:** Data exists, but not surfaced as `Org` "templates" the user can browse/instantiate.

| Spec section | Required | Code status | File |
|---|---|---|---|
| 22 Departments defined | ✅ Yes | Data structure declared | `app/org/role_taxonomy.py:71-90` and `app/org/roles.py:43` |
| 456 Roles (full taxonomy) | ✅ Yes | All 456 role definitions indexed | `app/org/roles.py` |
| Org templates you can apply | ❌ Missing | No `templates.py` file | — |
| Template instantiation ("create org from template 'Marketing Agency'") | ❌ Missing | No `/v1/org/templates` endpoint | router.py |
| Browseable template gallery | ❌ Missing | No frontend `/org/templates` route | — |

**Note:** The role *taxonomy* exists, but the user-facing concept of "org templates" (a
preassembled org shape: CEO + 3 departments + default agents) does **not**.
That is what the user means by "templates missing".

### 1.4 Spec — Auto Discovery
**Status:** ❌ **Completely missing.**
```
grep -rnE "auto_discover|AutoDiscovery" app/ → 0 hits
```
Nothing inspects tenant goal history + capability registry to suggest agent creation,
department formation, or capability gaps. The spec (PART 24, 26) describes an
`AutoDiscoveryEngine` — still unimplemented.

### 1.5 Spec — Org-level Debate
**Status:** ❌ **Not wired to org missions.**

A `DurableDebateAdapter` exists in `app/coordination/patterns/debate_adapter.py` (cross-cutting).
But **no org-level integration**:
```
grep -rnE "class.*Debate|def debate" app/org/ → 0 hits
```
`MetaOrchestrator.plan_mission` never selects debate topology for high-stakes/strategic missions,
even though the spec's PART 12 mandates it as one of the orchestration primitives.

### 1.6 Backend `app/org/` — File-by-File Status

Using `wc -l` + stub-mark scan:

| File | Lines | Status | Notes |
|---|---|---|---|
| `role_taxonomy.py` | 399 | ✅ Complete | 22 departments + subset of 456 roles |
| `roles.py` | (large) | ✅ Complete | All 456 roles |
| `capability_registry.py` | 517 | ✅ Complete | Capability model defined |
| `team_formation.py` | 480 | ⚠️ Partial | Returns TeamManifest; **does NOT instantiate agents** |
| `meta_orchestrator.py` | (large) | ⚠️ Partial | Calls team_formation, falls back to `sequential` if LLM unavailable |
| `model_gateway.py` | 233 | ✅ Complete | Stub provider — routing logic only |
| `service.py` | (large) | ⚠️ Partial | `create_mission_and_execute` works but no agent-spawn path |
| `router.py` | 57 routes | ⚠️ Partial | Has teams/agents CRUD but not a `GET` agent-list-by-org / `GET /teams/{id}` fetching real members |
| `models.py` | (large) | ✅ Complete | OrgTeam, OrgAgent, OrgMission, OrgTask, OrgEvent tables exist |
| `approval_chain.py` | 440 | ✅ Complete | ApprovalChain implemented; pending approvals visible via `/v1/org/:id/approvals` |
| `intelligence.py` | 335 | ⚠️ Partial | Bottleneck detection logic exists but no endpoint surface for UI |
| `digest.py` | 514 | ⚠️ Partial | MorningBrief computes summary but: (a) no hook on frontend, (b) "while you were away" data is mostly empty |
| `digital_twin.py` | 156 | ⚠️ Partial | Simulation shell only |
| `analytics.py` | 316 | ⚠️ Partial | Metrics computed but not exposed via `orgApi` |
| `self_improvement.py` | 392 | ✅ Complete | Self-improvement loop defined |
| `auto_discovery.py` | **MISSING** | ❌ | No file exists |
| `templates.py` | **MISSING** | ❌ | No org templates module |
| `debate.py` (org-level) | **MISSING** | ❌ | `coordination/` has debate but not exposed to org |

### 1.7 Frontend `src/features/org/` — Component Status

| Component | Status | Evidence |
|---|---|---|
| `OrgPage.tsx` (main AI Org Team page) | ✅ Layout working, ⚠️ data empty | Real missions render; team/agent panels empty |
| `CommandCenter.tsx` | ✅ Works | Real missions list — but no team/agent attribution |
| `OrgChart.tsx` | ⚠️ Partial | Real department data, **no team→agent links**: agents don't exist |
| `MissionsList.tsx` | ✅ Works | Real missions from `useMissions` |
| `MissionPage.tsx` | ✅ Layout, ⚠️ data thin | Timeline+kanban work; team graph empty, agent rosters empty |
| `MissionGraph.tsx` | ✅ Works | Tasks render; tasks have `actor_agent_id = None` because no agents were spawned |
| `DepartmentPage.tsx` | ✅ Works for departments | Real depts render |
| `TeamPage.tsx` | ❌ Stub | `// Mock members until team endpoint is available` (line 97) |
| `AgentProfile.tsx` | ❌ Stub | No hooks; data-binding `grep` returns 0 hits |
| `ApprovalCenter.tsx` | ✅ Works | Calls `/v1/org/:id/approvals` directly — bypasses `orgApi` |
| `KanbanBoard.tsx` | ✅ Works | Tasks; cards show `actor_agent_id` placeholder |
| `ArtifactGallery.tsx` | ⚠️ Partial | UI works; artifacts not attributed to agents |
| `OrgIntelligence.tsx`, `DigestPanel.tsx`, `ModelGatewayUI.tsx`, `MemoryBrowser.tsx`, `DigitalTwinPanel.tsx` | 🟡 Shell | Component exists; `orgApi` has no method to feed them |
| `dashboards/{CEO,CTO,...}.tsx` (7 files) | 🟡 Scaffold | Files exist but with placeholder data |
| `CommandBar.tsx` | ✅ File exists | No Cmd+K hotkey listener on OrgPage; no `/universal-command` call |

### 1.8 DB Tables Status (spec vs reality)

| Spec Required Table | Migration Exists? | ORM Model Exists? | Has Data via Service? |
|---|---|---|---|
| `organizations` | ✅ Yes | ✅ `app/org/models.py` | ✅ |
| `org_departments` | ✅ Yes | ✅ | ✅ |
| `org_teams` | ✅ Yes | ✅ `OrgTeam` (line 100) | ⚠️ Row create works, but `member_agent_ids` empty |
| `org_agents` | ✅ Yes | ✅ `OrgAgent` | ❌ **No service method writes rows from a TeamManifest** |
| `org_missions` | ✅ Yes | ✅ `OrgMission` | ✅ |
| `org_tasks` | ✅ Yes | ✅ `OrgTask` | ✅ (but `actor_agent_id = None`) |
| `org_events` | ✅ Yes | ✅ | ✅ via `OrgEventPublisher` |
| `org_decisions` | (need verify) | ✅ | ⚠️ Decisions created, not surfaced to UI |
| `org_approval_chains` | ✅ Yes | ✅ `ApprovalChain` | ✅ via `approval_chain.py` |
| `org_templates` | ❌ No migration | ❌ No model | ❌ Missing entirely |

---

## SECTION 2 — WORLD-CLASS IMPLEMENTATION PLAN

A **15-phase execution plan** that closes every gap. Each phase is independent enough to land
in isolation; together they deliver the spec's "watch your AI company go to work" promise.

### Phase 0 — One-test verification of the bug (½ day)

Write `tests/org/test_mission_to_team_e2e.py`:
```python
async def test_mission_creates_team_and_spawns_agents(test_app):
    org_id, mission_id = await create_test_org_and_mission()
    agents = await org_service.list_agents(org_id)
    assert len(agents) >= 1, "Mission did not spawn any agents"   # will FAIL
    teams = await org_service.list_teams(org_id)
    assert any(t.mission_id == mission_id for t in teams), "No team linked to mission"
```
This single failing test is the contract for everything else. Run it once to confirm red.

### Phase 1 — Agent Factory (unbreaks BREAK #1) [2 days]
Create `app/org/agent_factory.py`:
```python
class OrgAgentFactory:
    async def materialize_manifest(self, org_id, mission_id, manifest: TeamManifest) -> list[OrgAgent]:
        agents = []
        for role in manifest.roles:
            agent = await self._create_agent_for_role(org_id, mission_id, role)
            agents.append(agent)
        await self._bind_team_roles(org_id, manifest, agents)
        return agents

    async def _create_agent_for_role(self, org_id, mission_id, role) -> OrgAgent:
        # 1. Resolve role template from role_taxonomy
        # 2. Optionally reuse existing agent with matching role_id
        # 3. INSERT into agents (AgentStore) — reuses existing agent_create flow
        # 4. INSERT into org_agents linking org_id → agent_id with role_id
        # 5. Emit org.event "agent.materialized"
        ...
```
Wire it into `OrgService.create_mission_and_execute` between Step 3 and Step 4:
```python
if orch_plan and orch_plan.team_manifest:
    agents = await OrgAgentFactory(...).materialize_manifest(org_id, str(mission.id), orch_plan.team_manifest)
    # update OrgTeam row created by orch_plan with member_agent_ids=[a.id for a in agents]
    await self._bind_team_from_manifest(org_id, orch_plan, agents)
```

### Phase 2 — Multi-agent AgentGraph dispatch from manifest (unbreaks BREAK #2) [3 days]

Modify `app/agent/graph.py` (or `app/agent_runtime/`) to:
- Inspect `execution_context["orchestration_plan"]` at the initialize step
- If `topology in {parallel, hierarchical, swarm, moa, debate}`:
  - Load each `agent_id` from `execution_context["team_agent_ids"]`
  - Spawn one LangGraph subgraph per agent (using `app/agent/supervisor.py` or `app/coordination/`)
  - Route sub-tasks via the topology
  - Persist per-agent results into `org_tasks.actor_agent_id`
  - Emit `org_event` per agent (started/delivered)
- For `debate` topology specifically: use `DurableDebateAdapter` from `app/coordination/patterns/debate_adapter.py`

Add a feature flag (already have `app/org/feature_flags.py`) so legacy single-agent missions continue working until validated.

### Phase 3 — Org Templates module (Closes "templates missing") [1 day]

Create `app/org/templates.py`:
```python
@dataclass
class OrgTemplate:
    template_id: str
    name: str                   # "Marketing Agency", "Startup R&D Team"
    description: str
    departments: list[DeptSpec] # pre-baked departments
    default_roles: list[RoleSpec]
    default_teams: list[TeamSpec]
    autonomy_level_default: int
    model_profile_default: str

ORG_TEMPLATES: list[OrgTemplate] = [
    OrgTemplate(
        template_id="marketing_agency",
        name="Marketing Agency",
        description="3 creative strategists + 2 designers + 1 analyst, ...",
        departments=[...]   # draws on the 22 department taxonomy
    ),
    OrgTemplate(template_id="startup_rd_team", ...),
    OrgTemplate(template_id="enterprise_strategy_offic", ...),
    OrgTemplate(template_id="devops_sre_pod", ...),
    OrgTemplate(template_id="research_policy_unit", ...),
]   # 6-8 templates covering spec PART 4's most useful org shapes
```
Surface via 2 new endpoints in `app/org/router.py`:
- `GET /v1/org/templates` → list
- `POST /v1/org/{org_id}/apply-template` → instantiates departments + roles

### Phase 4 — Auto-Discovery Engine (Closes "auto discovery missing") [2 days]

Create `app/org/auto_discovery.py`:
```python
class AutoDiscoveryEngine:
    """Spec PART 24/26: observes tenant goal history + capability gaps and proposes
    new departments/agents/roles."""
    async def discover_for_org(self, org_id) -> list[DiscoveryRecommendation]:
        # 1. Pull last 100 completed missions
        # 2. Identify capabilities invoked in tasks
        # 3. Cross-reference with CapabilityRegistry
        # 4. Detect gaps: capability invoked but no agent owns it
        # 5. LLM-summarize as recommendations: "Add a Data Pipeline Agent to Engineering"
        # 6. Emit org_event "discovery.recommendation"
```
Endpoints:
- `GET /v1/org/{org_id}/discovery` → list recommendations
- `POST /v1/org/{org_id}/discovery/{rec_id}/accept` → instantiate recommended role/agent
- Fires automatically via Celery beat daily run for active orgs

### Phase 5 — Org-level Debate integration [2 days]

Promote `DurableDebateAdapter` to org-level usage:
- `MetaOrchestrator.plan_mission` selects `topology="debate"` for high-stakes/strategic missions
- Create `app/org/debate.py` wrapper that:
  - Picks 3-5 agents from the manifest (diverse depts)
  - Runs DurableDebateRuntime → produces "Decision"
  - Persists into `org_decisions` (existing)
  - Emits `org_event debate.round_finished`

### Phase 6 — REST endpoints the frontend is missing (unbreaks BREAK #3) [1 day]

In `app/org/router.py`, add/at-least-confirm-these-exist:
```
GET    /v1/org/{org_id}/teams                    # list teams (real data)
GET    /v1/org/{org_id}/teams/{team_id}          # full team with member_agent_ids expanded
GET    /v1/org/{org_id}/agents                   # list agents (with role_id, dept)
GET    /v1/org/{org_id}/agents/{agent_id}        # full agent profile
GET    /v1/org/{org_id}/agents/{agent_id}/memory # scope-aware memory
GET    /v1/org/{org_id}/agents/{agent_id}/reputation
GET    /v1/org/{org_id}/digest                   # morning brief
GET    /v1/org/{org_id}/model-gateway            # routing profile
GET    /v1/org/{org_id}/memory                   # org memory
GET    /v1/org/{org_id}/decisions                # decision log
GET    /v1/org/{org_id}/intelligence             # bottleneck analysis
GET    /v1/org/{org_id}/digital-twin/simulate    # POST actually
POST   /v1/org/{org_id}/universal-command        # NL → mission create (already partially exists)
```
Many of these endpoints may already exist in `router.py` 57 routes — the gap is they're not
registered in `orgApi` and not consumed by frontend hooks. **Action: alignment inventory
then expose all of them in `orgApi`.**

### Phase 7 — Frontend `orgApi` expansion + hooks [2 days]

Extend `agent-verse-frontend/src/features/org/api.ts`:
```typescript
orgApi = { ...existing,
  listTeams, getTeam, createTeam, updateTeamLifecycle,
  listAgents, getAgent, getAgentMemory, getAgentReputation, getAgentTools,
  getDigest, getModelGateway, getOrgMemory, listDecisions,
  getOrgIntelligence, simulateDigitalTwin, universalCommand,
  listTemplates, applyTemplate, listDiscoveries, acceptDiscovery,
}
```
Create one TanStack Query hook per method in `src/features/org/hooks/`:
- `useTeams`, `useTeam`, `useAgents`, `useAgent`, `useAgentMemory`, `useDigest`, etc.

### Phase 8 — TeamPage, AgentProfile, MissionPage real wiring [3 days]
- `TeamPage.tsx`: replace mock at line 97 with `useTeam(orgId, teamId)` + `useMissions({team_id})`
- `AgentProfile.tsx`: bind to `useAgent`, `useAgentMemory`, `useAgentReputation`; slide-in panel shows real reputation, capability icons, live status (current task via `/tasks?actor_agent_id=...`)
- `MissionPage.tsx`: team graph uses `useTeam` to show real roster + per-agent progress from `useOrgTasks({mission_id, group_by='agent'})`

### Phase 9 — Live org chart with team→agent links [2 days]
- `OrgChart.tsx`: extend to show `agents` as leaf nodes under each `department`, with the agents belonging to active teams highlighted in real time via the SSE `OrgRealtimeManager` (already exists)
- Sub-node: when a mission is active, draw colored edges from involved agents to the mission node

### Phase 10 — Digest ("While You Were Away") [1 day]
- Backend `digest.py` already computes summary → expose `GET /v1/org/{org_id}/digest`
- Frontend `DigestPanel.tsx`: wire `useDigest`; render grouped events (decisions made, approvals waiting, missions completed, failures, deliverables)

### Phase 11 — Command Bar (Cmd+K universal command) [1 day]
- Add Cmd+K hotkey listener on `OrgPage.tsx`
- Modal: NL textarea → `POST /v1/org/{id}/universal-command`
- Backend route returns either a mission_id or a debate_id — frontend opens the relevant page

### Phase 12 — Org Templates gallery [1 day]
- New `src/features/org/TemplatesGallery.tsx` (modal on OrgListPage)
- Calls `GET /v1/org/templates`, `POST /apply-template`
- Each template card shows departments, default agents, estimated cost, autonomy level

### Phase 13 — Auto-Discovery banner [1 day]
- On OrgPage header, add a `<Sparkles />` "Discoveries" badge showing count of `DiscoveryRecommendation`
- Click → drawer listing recommendations, each with "Create agent" button

### Phase 14 — Debate timeline visualization [1 day]
- `MissionPage.tsx` detects `topology==="debate"` → renders `DebateTimeline.tsx` (new): rounds, positions, votes, final decision
- Backend emits `org_event debate.*` (Phase 5) consumed via `OrgRealtimeManager`

### Phase 15 — Polishing + tests + frontendРеactive mock removal [2 days]
- Remove all `// Mock members` and placeholder dashboards
- E2E Playwright test: create org → apply template → create mission → see agents materialize in chart → see mission finish → see decision logged
- Backend: `tests/org/test_mission_to_team_e2e.py` goes green (closed Phase 0 loop)

---

## SECTION 3 — PRIORITY TRIAGE (what to do first to unblock the user)

Do these first because they are the *visible* failures:

1. **Phase 1 + 6 + 7 + 8** (unbreak "no team/agent results on screen") — combined ~8 days
2. **Phase 0** (red test) first — ½ day — proves the bug exists
3. **Phase 3** (templates) — 1 day — most perception-altering UI improvement
4. **Phase 4** (auto-discovery) — 2 days — closes one of the user's three explicit complaints
5. **Phase 5 + 14** (debate) — 3 days — closes another explicit complaint
6. The rest in any order

**Total estimated effort:** ~23 engineering days for a single senior engineer.
Parallelizable across frontend + backend to ~15 working days.

---

## SECTION 4 — WHAT THE USER WILL SEE WHEN FIXED

After Phases 1-8 ship:
1. User creates a mission → agent factory materializes 4 agents (Strategy, Legal, Research, Engineering)
2. OrgChart animates: agents appear under their departments with `running` status
3. MissionPage shows team graph + member roster with live task assignment
4. KanbanBoard task cards have real `actor_agent_id` and progress bars
5. AgentProfile slide-in shows real reputation, memory, current task
6. "While You Were Away" shows real morning brief with completed missions and pending approvals

After Phases 3-5, 12-14 ship:
7. TemplatesGallery lets user pick a starter template (e.g. "Marketing Agency")
8. Auto-discovery badge shows "3 new agents recommended" with explanation
9. Strategic missions trigger debate → DebateTimeline renders real rounds

That is the spec's promise, fully realized.

---

## SECTION 5 — RECOMMENDED NEXT STEP

**Approve Phase 0 (½ day red test) + Phase 1 (agent factory) + Phase 6 (endpoints) + Phase 7
(orgApi/hooks) + Phase 8 (frontend wiring) as a sprint.** I can execute each phase in this
session via the standard pattern: write the failing test → implement to green → lint →
typecheck → commit.

Reply with a number or list of which phases you want me to start now, and I'll
proceed in plan-execute-test-review cycles.
