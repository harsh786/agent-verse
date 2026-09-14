# Autonomous Org Brain — World-Class UI/UX Improvement Plan

**Status:** Implemented (v1) — verified e2e 2026-09-14. Shipped: typed collaboration +
persistence, guardrail trace, agent audit, mission timeline; Team Channel v2, Brain Feed v2,
live beams, agent audit drawer, mission Gantt, budget gauges, narration ticker, hero Pause.

**Goal:** make the autonomy *visible and legible* — you should be able to watch a robotic
AI organization think, talk, act, and spend, in real time, and trust it because every
decision, message, and action is traceable with timing.

The guiding metaphor: a **Mission Control / War Room** for a living org. Not dashboards of
numbers — a stage where agents are characters, messages are visible signals, and the brain's
reasoning is narrated.

---

## A. The three "wow" surfaces (highest impact first)

### 1. Live Agent Network → "The Org, Alive" (upgrade the existing AgentConstellation)
Today there's a constellation of agent nodes. Make it a **living neural map**:
- **Agent nodes as robots/avatars** with a state ring: idle (dim pulse), thinking (spinning
  arc), acting (solid glow), blocked (amber), escalated (red). Department = color cluster.
- **Message beams**: when agent A sends data to agent B, animate a packet traveling the edge,
  colored by message type (question / handoff / result / escalation). This is the "teams are
  talking" moment — literally see the traffic.
- **Hover a beam** → a tooltip of *what* was sent (the payload summary: the ask, the artifact
  name, token/really-cost of that exchange). **Click** → opens the message in the Team Channel
  with full payload + audit.
- **The Brain hub** at the center pulses on each tick; a ripple animation fires when it
  SENSE→DECIDE→ACTs, so you feel the heartbeat of the loop.

### 2. Team Channel → "Situation Room Chat" (upgrade the current TeamChannel)
Today it renders `org.collaboration.message` as chat lines. Make it a **first-class comms feed**:
- **Threaded by mission/topic**, with agent avatars, department badges, and role
  (planner/executor/verifier) chips.
- **Typed messages**: proposal 💡, question ❓, handoff 🤝, result ✅, risk ⚠️, block ⛔ — each
  visually distinct so the *nature* of the conversation reads at a glance.
- **Payload inspector**: expand any message to see the actual data exchanged (the goal text,
  the artifact, the tool call + args, the sub-result) — with a "copy / open artifact" action.
- **Per-message meta**: sender → receiver, timestamp, latency, token + $ cost of that turn.
- **Typing/working indicator**: "Finance-Analyst is drafting…" so it feels live.

### 3. Brain Feed → "Decision Timeline with Reasons" (upgrade the current BrainFeed)
Today it lists decisions and marks held-back ones. Make it the **trust surface**:
- A vertical timeline, newest first, grouped by tick. Each entry: the **action** (executed /
  proposed / blocked), a one-line **reason/rationale**, the **est. cost**, and **created_at**.
- **Held-back rows are the star**: show *which guardrail* stopped it (budget? concurrency?
  cooldown? risk? dedup?) with the specific number ("daily budget $4.80/$5.00 → blocked").
  Seeing what the brain *chose not to do, and why* is what makes people trust letting it run.
- **Expand a decision** → the full SENSE→DECIDE→GUARD→ACT trace: the snapshot it saw, the
  goal it picked, each of the 8 guardrail checks with pass/fail, and the mission it created
  (deep-link to the mission).

---

## B. Time & telemetry made visible (the "how long / how much" layer)

Everything the user asked to see about *timing* and *data flow*:

- **Per-operation timing**: every mission and step shows elapsed time live (a running clock
  while active) and final duration when done. A subtle **sparkline of tick cadence** shows the
  5-min heartbeat.
- **Mission timeline (Gantt-style ribbon)**: for each mission, a horizontal track — planned →
  team formed → executing → verifying → done — with real durations per phase and the agent
  responsible for each. Hover a segment → who, what, how long, cost.
- **Data-flow ledger per exchange**: for each agent→agent message, record & show bytes/tokens
  moved, latency, and $ — so "what are they sending each other and how expensive is it" is a
  first-class, sortable view, not buried.
- **Budget burn gauges**: a live radial for daily budget, per-mission ceiling, and the
  separate collaboration budget — filling as the org spends, turning amber near the cap. Ties
  the abstract caps to something you can watch approach the line.
- **Cost/latency heat on the network**: thicken/redden edges that are expensive or slow, so
  bottlenecks are obvious spatially.

## C. Per-agent audit trail ("the black box for each robot")

- **Agent profile drawer**: click any agent → a drawer with its live status, current task,
  and a **chronological audit log**: every action it took, every tool it called (with args +
  result), every message sent/received, every decision it influenced — each with timestamp,
  duration, and cost. This is the per-agent accountability the user asked for.
- **"Explain this action"**: on any audit entry, a one-click trace back to the brain decision
  and the guardrail verdict that authorized it — end-to-end provenance.
- **Filterable / exportable**: filter an agent's trail by mission, time window, or action type;
  export to CSV/JSON for compliance.

## D. Making it feel *robotic & agentic* (the craft layer)

- **Motion language**: JARVIS-style — spring transitions, scanline sweeps on new data, a soft
  "thinking" shimmer on active agents, a decisive "commit" flash when a mission launches.
  Respect `prefers-reduced-motion` (already the codebase convention).
- **Sound (opt-in)**: subtle blips for message-sent, a chime for mission-complete, a low tone
  for a block — the platform already has a voice/alert layer to build on.
- **Ambient "org is alive" state**: even when idle, gentle breathing pulses and the occasional
  collaboration beam, so the space never feels dead.
- **Narration ticker**: a one-line, human-readable running commentary — "Brain proposed
  'refresh competitor pricing'; held back a 2nd mission (cooldown 4m left)." Turns raw events
  into a story.

## E. Control & trust (operator confidence)

- **The Pause is a hero control**: a big, always-visible kill/pause with a clear state
  ("AUTONOMOUS · L4" vs "PAUSED"), and an org-wide **emergency stop** with confirmation.
- **Autonomy level as a visible dial** with plain-language consequences per level (the panel
  exists; make it the centerpiece with a "what changes if I go L4?" preview).
- **Approval inbox**: proposals surface as cards with the reason, cost estimate, and risk —
  approve/reject inline, with the decision written back to the audit trail.
- **"What would it do?" dry-run**: a button to run one tick in shadow and preview the decisions
  without executing — de-risks turning the level up.

---

## F. Suggested build order (incremental, each shippable)

1. **Team Channel v2** (typed messages + payload inspector + per-message cost/latency) — fastest
   path to "I can see them talking and what they send."
2. **Brain Feed v2** (guardrail-attributed held-back reasons + expandable SENSE→ACT trace).
3. **Agent profile drawer** (per-agent audit trail + explain-this-action).
4. **Live network beams** (animated message packets + hover payload + click-through).
5. **Timing layer** (mission Gantt ribbon + live clocks + budget burn gauges).
6. **Craft pass** (motion, narration ticker, opt-in sound, hero Pause).

Each step is additive over components that already exist (AgentConstellation, TeamChannel,
BrainFeed, AutonomyControl, MissionOrbit), and each is independently valuable — so you can
demo progress every few days.

## G. Data you already emit that powers all of this

- `org.collaboration.message` (lead + message) → Team Channel + network beams.
- `org_brain_decisions` rows (kind, action, guardrail_verdict, reason, rationale, est_cost_usd,
  mission_id, created_at) → Brain Feed + decision traces + timing.
- Org event stream (mission/agent/approval/budget events) → network states, Gantt, gauges.
The main additions needed are richer message payloads (typed + data summary + latency/cost per
exchange) and per-agent action logging surfaced through the profile drawer.
