# AgentVerse — World-Class JARVIS Visualization Specification
**Version:** 2.0 | **Date:** 2026-08-19 | **Status:** CANONICAL — DO NOT IMPLEMENT UNTIL APPROVED  
**Scope:** Every page, every SSE event, every agent interaction — full-platform visual overhaul

---

## 0. Reverification Audit (What Exists vs What Is Needed)

### Verified Existing Foundations
| Asset | File | State |
|---|---|---|
| JARVIS spring system | `components/ui/JARVISPageShell.tsx` | ✅ Built — 5 spring presets |
| d3-force simulation | `package.json` | ✅ Installed — `d3-force ^3.0.0` + `d3-selection ^3.0.0` |
| React Flow canvas | `package.json` | ✅ Installed — `@xyflow/react ^12.11.1` |
| Framer Motion | `package.json` | ✅ Installed — `framer-motion ^13.1.0` |
| Agent orbit (d3) | `dashboard/components/AgentOrbitView.tsx` | ✅ Built — SVG glow filter, force sim |
| Live cost ticker | `components/live/LiveCostTicker.tsx` | ✅ Built |
| Goal SSE stream | `lib/sse/useGoalStream.ts` | ✅ Built — 50+ event types, reconnect |
| Org SSE events | `features/org/OrgRealtimeManager.ts` | ✅ Built — 35 event types |
| Mission graph (DAG) | `features/org/MissionGraph.tsx` | ✅ Built — React Flow, 15 task states |
| Civilization map | `features/civilization/CivilizationMap.tsx` | ✅ Built — React Flow |
| JARVIS boot screen | `components/ui/JARVISBootScreen.tsx` | ✅ Built |
| Status orb | `components/ui/StatusOrb.tsx` | ✅ Built |
| KPI card | `components/ui/KpiCard.tsx` | ✅ Built |

### Critical Gaps (No Code Exists)
| What | Priority | Blocks |
|---|---|---|
| Canvas particle system (agent comms / tool calls / tokens) | P0 | Every real-time visualization |
| `useAgentNeuralGraph` — live graph state from SSE | P0 | Org Mission Control |
| `OrgMissionControlPage` — full-canvas live org view | P0 | The flagship screen |
| Goal execution NODE GRAPH (not text log) | P0 | GoalDetailPage visual overhaul |
| Tool call spark animation | P0 | Goal + Mission views |
| Token flow visualizer | P1 | Goal + Mission views |
| Guardrail shield materializer | P1 | Goal views |
| HITL gate node | P1 | Goal + Approval views |
| Agent-to-agent message beam | P1 | Org Mission Control |
| Obsidian vault 3D graph | P1 | ObsidianPage |
| Knowledge graph explorer visual overhaul | P1 | GraphExplorerPage |
| Design token file (`lib/design/tokens.ts`) | P0 | All pages |
| Motion preset file (`lib/design/motion.ts`) | P0 | All pages |

### Verified SSE Events (ALL available for visualization — no backend changes needed)
```
GOAL LIFECYCLE:  goal_started · goal_complete · goal_failed · goal_cancelled · goal_paused · goal_resumed
PLANNING:        plan_ready {steps[]} · workflow_planned
EXECUTION:       step_started · step_complete · steps_parallel_start · steps_parallel_complete
TOKENS:          token_chunk {step, cumulative}
TOOLS:           tool_call_complete · tool_call_failed · tool_call_pending_approval · tool_call_denied
VERIFICATION:    verification_done
PATTERNS:        debate_started · debate_proposals_ready · debate_complete
                 supervisor_decomposed · supervisor_task_started · supervisor_task_complete · supervisor_complete
                 synthesis_complete · pattern_assembled
AGENTS:          child_agent_spawned · approval_granted · approval_rejected · waiting_approval
GOVERNANCE:      hitl_approved · hitl_rejected · guardrail_profile_selected · guardrail_rejected · pii_redacted
KNOWLEDGE:       knowledge_retrieved · knowledge_retrieval_failed · cache_hit · artifact_captured
SIGNALS:         grounding_warning · stuck_loop_detected · replan
ORG (35 types):  org.mission.* · org.team.* · org.agent.* · org.approval.* · org.budget.* · org.policy.*
```

---

## 1. Design Language: JARVIS Neural Dark

### 1.1 Core Visual Identity
```
Background:  #020408 (deepest void)
Surface:     #0A0F1A → #0F1826 → #162035 (depth hierarchy)
Primary glow: #00D4FF (electric cyan — the JARVIS signature)
Secondary:   #6366F1 (indigo) · #00E676 (emerald) · #FF3366 (rose) · #FFB300 (amber)
Text:        #F0F6FF (primary) · #A0B4CC (secondary) · #5A7494 (muted)
```

### 1.2 The Glow System (SVG + CSS)
Every live/active element uses a layered glow:
```css
/* Tier 1 — subtle presence (idle agents, inactive nodes) */
box-shadow: 0 0 8px rgba(0,212,255,0.12);

/* Tier 2 — active glow (running, communicating agents) */
box-shadow: 0 0 16px rgba(0,212,255,0.30), 0 0 4px rgba(0,212,255,0.60);

/* Tier 3 — burst glow (tool call fired, message sent, approval) */
box-shadow: 0 0 32px rgba(0,212,255,0.50), 0 0 8px rgba(0,212,255,0.80);

/* SVG filter equivalent (for canvas elements) */
<filter id="glow-electric">
  <feGaussianBlur stdDeviation="4" result="blur"/>
  <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
</filter>
```

### 1.3 The Particle Language
Particles are the **synaptic language** of AgentVerse. Every data movement is a particle.

| Event | Particle color | Shape | Trail | Speed |
|---|---|---|---|---|
| `token_chunk` | `#00D4FF` dim | Round 2px | Yes, 8px | Fast |
| `tool_call_complete` | `#00E676` | Spark ✦ 4px | Yes, 12px | Burst |
| `tool_call_failed` | `#FF3366` | ✕ 4px | No | Burst |
| `tool_call_pending_approval` | `#FFB300` | ◆ 4px | Yes | Slow |
| `step_started` | `#6366F1` | ● 3px | Yes, 6px | Medium |
| `knowledge_retrieved` | `#34D399` | ◇ 3px | Yes | Medium |
| `guardrail_rejected` | `#FF3366` | ⬡ 5px | No | Instant |
| `hitl_approved` | `#00E676` | ✓ 5px | Yes | Slow |
| `child_agent_spawned` | `#A855F7` | ★ 4px | Yes | Arc |
| `debate_claim` | `#A855F7` | ● 2px | Yes | Medium |
| Agent → Agent message | Sender color | ● 3px | Yes, 16px | Medium |

### 1.4 Spring Presets (All animations use springs — never duration/ease)
```typescript
// Exact values — do not change
SPRING_PAGE    = { type: 'spring', stiffness: 280, damping: 26 }  // page entry
SPRING_PANEL   = { type: 'spring', stiffness: 300, damping: 28 }  // panel slide
SPRING_FAST    = { type: 'spring', stiffness: 600, damping: 35 }  // instant feedback
SPRING_SLOW    = { type: 'spring', stiffness: 200, damping: 25 }  // heavy panels
SPRING_BOUNCY  = { type: 'spring', stiffness: 450, damping: 18 }  // delight
SPRING_NODE    = { type: 'spring', stiffness: 380, damping: 30 }  // graph nodes
SPRING_PARTICLE= { type: 'spring', stiffness: 800, damping: 40 }  // particles (snappy)
```

---

## 2. Foundation Files (Phase 0 — Build First)

### 2.1 `src/lib/design/tokens.ts`
```typescript
export const tokens = {
  color: {
    electric: '#00D4FF', electricDim: 'rgba(0,212,255,0.15)',
    electricGlow: 'rgba(0,212,255,0.30)', electricBright: 'rgba(0,212,255,0.60)',
    emerald: '#00E676', emeraldDim: 'rgba(0,230,118,0.15)',
    rose: '#FF3366', roseDim: 'rgba(255,51,102,0.15)',
    amber: '#FFB300', amberDim: 'rgba(255,179,0,0.15)',
    indigo: '#6366F1', indigoDim: 'rgba(99,102,241,0.15)',
    violet: '#A855F7', violetDim: 'rgba(168,85,247,0.15)',
    surface0: '#020408', surface1: '#0A0F1A', surface2: '#0F1826',
    surface3: '#162035', surface4: '#1E2C4A', surface5: '#253552',
    text1: '#F0F6FF', text2: '#A0B4CC', text3: '#5A7494', text4: '#334155',
    border1: 'rgba(255,255,255,0.06)',
    border2: 'rgba(255,255,255,0.10)',
    border3: 'rgba(255,255,255,0.16)',
  },
  glow: {
    tier1: '0 0 8px rgba(0,212,255,0.12)',
    tier2: '0 0 16px rgba(0,212,255,0.30), 0 0 4px rgba(0,212,255,0.60)',
    tier3: '0 0 32px rgba(0,212,255,0.50), 0 0 8px rgba(0,212,255,0.80)',
    emerald: '0 0 16px rgba(0,230,118,0.30)',
    rose: '0 0 16px rgba(255,51,102,0.30)',
    amber: '0 0 16px rgba(255,179,0,0.30)',
    indigo: '0 0 16px rgba(99,102,241,0.30)',
    violet: '0 0 16px rgba(168,85,247,0.30)',
  },
  glass: {
    panel: 'backdrop-blur-md bg-[#0A0F1A]/80 border border-white/6',
    card: 'backdrop-blur-sm bg-[#0F1826]/90 border border-white/8',
    elevated: 'backdrop-blur-md bg-[#162035]/90 border border-white/10',
    overlay: 'backdrop-blur-xl bg-[#020408]/60',
  },
} as const;
```

### 2.2 `src/lib/design/motion.ts`
```typescript
// motion.ts — re-exports springs + named animation variants

export { SPRING_PAGE, SPRING_PANEL, SPRING_FAST, SPRING_SLOW, SPRING_BOUNCY }
  from '@/components/ui/JARVISPageShell';

export const SPRING_NODE    = { type: 'spring', stiffness: 380, damping: 30 };
export const SPRING_PARTICLE= { type: 'spring', stiffness: 800, damping: 40 };

// Named animation variants (used with Framer Motion `variants` prop)
export const fadeUp = {
  hidden:  { opacity: 0, y: 12, filter: 'blur(4px)' },
  visible: { opacity: 1, y: 0,  filter: 'blur(0px)', transition: SPRING_PAGE },
  exit:    { opacity: 0, y: -6, filter: 'blur(2px)', transition: SPRING_FAST },
};

export const slideRight = {
  hidden:  { opacity: 0, x: -16 },
  visible: { opacity: 1, x: 0,  transition: SPRING_PANEL },
  exit:    { opacity: 0, x: -8, transition: SPRING_FAST },
};

export const scaleIn = {
  hidden:  { opacity: 0, scale: 0.88 },
  visible: { opacity: 1, scale: 1,    transition: SPRING_BOUNCY },
  exit:    { opacity: 0, scale: 0.94, transition: SPRING_FAST },
};

export const nodeAppear = {
  hidden:  { opacity: 0, scale: 0.4, filter: 'blur(8px)' },
  visible: { opacity: 1, scale: 1,   filter: 'blur(0px)', transition: SPRING_NODE },
  exit:    { opacity: 0, scale: 0.6, filter: 'blur(4px)', transition: SPRING_FAST },
};
```

### 2.3 `src/components/canvas/ParticleCanvas.tsx` (New Core Component)
```typescript
/**
 * ParticleCanvas — WebWorker-backed canvas for particle system.
 * Renders all inter-agent and intra-agent data movement particles.
 * Runs at 60fps without blocking the main thread.
 *
 * Architecture:
 *   Main thread: manages particle state (add/remove)
 *   OffscreenCanvas (Worker): renders every frame via requestAnimationFrame
 *   Fallback: If OffscreenCanvas not supported, renders on main thread canvas
 *
 * Usage:
 *   <ParticleCanvas ref={canvasRef} width={w} height={h} />
 *   // Then call: canvasRef.current.emitParticle(particle)
 *
 * Particle spec:
 *   {
 *     id: string
 *     from: {x, y}         // source position
 *     to:   {x, y}         // target position
 *     color: string        // hex
 *     size: number         // px radius
 *     trail: boolean
 *     trailLength: number  // px
 *     duration: number     // ms
 *     easing: 'linear' | 'easeIn' | 'easeOut' | 'elastic'
 *     shape: 'circle' | 'spark' | 'diamond' | 'star' | 'x'
 *     onComplete?: () => void
 *   }
 *
 * The component maintains a ring buffer of max 500 active particles.
 * When full, oldest particles are evicted first.
 */

export interface Particle {
  id:          string;
  from:        { x: number; y: number };
  to:          { x: number; y: number };
  color:       string;
  size:        number;
  trail:       boolean;
  trailLength: number;
  duration:    number;
  shape:       'circle' | 'spark' | 'diamond' | 'star' | 'x';
  onComplete?: () => void;
}

export interface ParticleCanvasRef {
  emitParticle: (p: Particle) => void;
  emitBurst:    (center: {x:number;y:number}, color: string, count?: number) => void;
  clearAll:     () => void;
}
```

### 2.4 `src/hooks/useSSEToParticles.ts` (New Core Hook)
Maps every SSE event from `useGoalStream` to a particle emission. This is the bridge between the backend data and the visual layer.

```typescript
/**
 * useSSEToParticles — maps SSE events to ParticleCanvas emissions.
 *
 * Input:  GoalEvent[] from useGoalStream
 * Output: emits particles on ParticleCanvasRef whenever new events arrive
 *
 * Event → Particle mapping:
 *   token_chunk              → fast dim-cyan particles along LLM→output edge
 *   tool_call_complete       → green spark burst at tool node
 *   tool_call_failed         → red X burst at tool node
 *   tool_call_pending_approval → amber orbit at tool node
 *   step_started             → indigo particle wave along step edge
 *   step_complete            → emerald arc from step to output
 *   knowledge_retrieved      → teal diamond along knowledge edge
 *   guardrail_rejected       → red hexagon burst at guardrail node
 *   guardrail_profile_selected → indigo glow pulse at guardrail node
 *   child_agent_spawned      → violet star arc from parent to child
 *   plan_ready               → blue cascade down plan nodes
 *   pii_redacted             → amber pulse at output node
 *   cache_hit                → dim cyan flash at knowledge node
 */
export function useSSEToParticles(
  events: GoalEvent[],
  nodePositions: Map<string, {x: number, y: number}>,
  canvasRef: React.RefObject<ParticleCanvasRef>
): void
```

---

## 3. The Flagship Screen: Org Mission Control

### 3.1 Overview

This is the most important screen in AgentVerse. When a user opens any org, they see a **living neural constellation** — their AI team at work.

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  MISSION CONTROL CENTER — Acme AI        🔴 3 Active  ⏳ 2 Pending  💰 $12.40  │
├─────────────────────────────┬───────────────────────────┬───────────────────────┤
│    AGENT CONSTELLATION       │   MISSION COMMAND LOG     │   INSPECTOR          │
│    (Left 55%)               │   (Middle 25%)            │   (Right 20%)        │
│                             │                           │                      │
│   Glowing agent nodes        │  Live SSE event stream    │  Selected agent or   │
│   with d3-force layout      │  Tool calls spark         │  mission deep-dive   │
│   Message beams between     │  Token flow counter       │  Thought stream      │
│   agents                    │  Guardrail shield         │  Tool timeline       │
│   Mission status rings      │  HITL gates               │  Budget burn         │
│   Team formation arcs        │  Mission progress         │  Subagent tree       │
│                             │                           │                      │
└─────────────────────────────┴───────────────────────────┴───────────────────────┘
```

### 3.2 File: `src/features/org/OrgMissionControlPage.tsx` (New — replaces current OrgPage split)

This component is the main visual upgrade of OrgPage. It receives org + mission data from existing hooks and renders it as the constellation.

**Layout architecture:**
```tsx
<OrgMissionControlPage orgId={orgId}>
  <div className="flex h-full overflow-hidden">
    {/* Left 55% — Neural Constellation */}
    <AgentConstellation
      orgId={orgId}
      missions={activeMissions}
      onAgentSelect={setSelectedAgent}
      onMissionSelect={setSelectedMission}
      particleCanvas={canvasRef}
    />

    {/* Middle 25% — Live Command Log */}
    <MissionCommandLog
      orgId={orgId}
      selectedMissionId={selectedMission}
      canvasRef={canvasRef}
    />

    {/* Right 20% — Deep Inspector Drawer */}
    <AnimatePresence>
      {(selectedAgent || selectedMission) && (
        <MissionInspectorPanel
          agentId={selectedAgent}
          missionId={selectedMission}
          onClose={() => { setSelectedAgent(null); setSelectedMission(null); }}
        />
      )}
    </AnimatePresence>
  </div>
</OrgMissionControlPage>
```

### 3.3 `AgentConstellation` Component (d3-force + Canvas + React)

**Architecture:** d3-force computes positions, React Flow renders declarative nodes, Canvas renders particles.

```typescript
// Node types in the constellation
type ConstellationNodeType =
  | 'ceo_agent'        // central hub — large, electric cyan glow
  | 'dept_agent'       // department head — medium, department color
  | 'task_agent'       // executing a task — smaller, shows tool name
  | 'team_node'        // team cluster — dashed border, contains agents
  | 'mission_node'     // active mission — pulsing ring, progress bar
  | 'tool_node'        // tool being called — spark when active
  | 'knowledge_node'   // knowledge retrieval — teal glow when hit

// Edge types
type ConstellationEdgeType =
  | 'delegation'       // CEO → dept agent (solid, electric)
  | 'collaboration'    // agent ↔ agent (dashed, bidirectional)
  | 'tool_call'        // agent → tool (animated dash, orange)
  | 'knowledge'        // agent → knowledge (animated dash, teal)
  | 'supervision'      // supervisor → task agent (hierarchy line)
  | 'debate'           // agent ↔ agent in debate (purple, animated)
```

**Node visual states (animated with Framer Motion):**
```
IDLE:           Dim glow, slow 4s breathing pulse (scale 0.97 → 1.03)
ACTIVE:         Tier-2 electric glow, moderate pulse
THINKING:       Internal shimmer animation (gradient sweep)
EXECUTING:      Tier-2 glow + rotating progress ring + tool sparks
COMMUNICATING:  Bright burst + beam animation along edge
WAITING_HITL:   Amber glow, slow pulse with pause icon overlay
GUARDRAIL:      Red glow, red shield SVG materializes
ERROR:          Rose glow, distress jitter animation
COMPLETED:      Green burst → settle to dim success state
SPAWNING:       Violet star animation → new child node arc
```

**d3-force layout configuration:**
```typescript
const simulation = d3force.forceSimulation(nodes)
  .force('link',     d3force.forceLink(edges).id(d => d.id).distance(150).strength(0.6))
  .force('charge',   d3force.forceManyBody().strength(-800))
  .force('center',   d3force.forceCenter(width/2, height/2))
  .force('collision',d3force.forceCollide().radius(d => d.radius + 20))
  .force('x',        d3force.forceX(width/2).strength(0.05))
  .force('y',        d3force.forceY(height/2).strength(0.05));
// CEO agent has fixed position at center with strong Y attraction
```

### 3.4 Live Message Beams

When one agent sends a message to another (SSE events with `sender_agent_id` + `recipient_agent_id`), a **beam** animates along the edge:

```typescript
// BeamAnimation — SVG path animation along existing edge
// Uses stroke-dashoffset animation (CSS, not Framer Motion — for performance)
function AgentBeam({ from, to, color, duration }: BeamProps) {
  const path = cubicBezierPath(from, to);  // smooth curve between nodes
  return (
    <path
      d={path}
      stroke={color}
      strokeWidth={2}
      fill="none"
      className="agent-beam"
      style={{
        strokeDasharray: '100%',
        strokeDashoffset: '100%',
        animation: `beam-travel ${duration}ms linear forwards`,
        filter: `drop-shadow(0 0 4px ${color})`,
      }}
    />
  );
}
```

### 3.5 Tool Call Spark Visualization

When `tool_call_complete` or `tool_call_failed` fires, a spark materializes at the tool node:

```typescript
// Tool nodes appear as small satellite nodes orbiting their parent agent
// When tool is called:
//   1. Tool node brightens (tier-3 glow)
//   2. 8 spark particles emit radially outward
//   3. On success: green burst + fade to tier-1
//   4. On failure: red burst + node turns red briefly
//   5. Particle travels from tool node back to agent (result return)
```

### 3.6 Token Flow Visualizer

When `token_chunk` events fire, tiny particles flow continuously from the LLM model node to the step output area:

```typescript
// TokenFlowVisualizer — overlaid on the step node
function TokenFlowVisualizer({
  isActive: boolean,       // true when step is running
  tokensPerSecond: number, // computed from token_chunk frequency
  model: string,           // model name badge
  inputTokens: number,
  outputTokens: number,
  costUsd: number,
}) {
  // Renders a continuous stream of 2px cyan dots
  // Speed proportional to tokensPerSecond
  // Shows model badge with provider color
  // Shows live cost increment: "+$0.0003" fading up
}
```

### 3.7 Guardrail Shield Materializer

When `guardrail_rejected` or `tool_call_denied` fires:

```typescript
// GuardrailShield — animated SVG shield that materializes at the blocking point
// 1. Shield SVG scales in from 0 → 1 with SPRING_BOUNCY
// 2. Red particle burst radiates outward
// 3. Rule text appears below: "Blocked: PII detection"
// 4. The blocked tool/step node pulses red 3x
// 5. After 3s, shield fades but leaves red outline on node
function GuardrailShield({
  position: {x, y},
  ruleName: string,
  visible: boolean,
}) {}
```

### 3.8 HITL Gate Node

When `tool_call_pending_approval` or `waiting_approval` fires:

```typescript
// HITLGate — amber glowing gate that blocks progress
// Shows: amber ◆ diamond spinning · "Awaiting approval" · requester name
// Countdown timer if approval_timeout is set
// When approved: gate opens with green burst
// When rejected: gate slams red with X
function HITLGateNode({
  position: {x, y},
  requestId: string,
  approver: string,
  timeoutMs?: number,
  status: 'waiting' | 'approved' | 'rejected',
}) {}
```

---

## 4. Goal Execution Neural Theater (GoalDetailPage Upgrade)

### 4.1 Current State (text log → Neural Theater)

GoalDetailPage currently renders SSE events as a text log. The upgrade replaces the "execution" tab's content with a **node graph theater** while keeping the existing tabs (results, evidence, eval, explain).

### 4.2 Goal Execution Graph Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  EXECUTION THEATER                                          │
│                                                             │
│   [START] ──► [PLAN] ──► [STEP 1] ──► [TOOL A] ──► ...    │
│                │               │         └── [TOOL B]       │
│                └──► [STEP 2] ──┘                           │
│                       │                                     │
│                 [VERIFY] ──► [COMPLETE] or [REPLAN]        │
│                                                             │
│   Token stream: flowing particles along active edge         │
│   Guardrail:    red shield at blocked edge                 │
│   HITL:         amber gate at approval edge                │
│   Tool call:    spark at tool node                         │
└─────────────────────────────────────────────────────────────┘
```

### 4.3 Node Types in Goal Graph

```typescript
type GoalNodeType =
  | 'start'        // Goal input — neon electric ring
  | 'plan'         // plan_ready event — shows step count badge
  | 'step'         // step_started/complete — progress ring, step description
  | 'tool'         // tool_call_* — tool name, success/failure state
  | 'verify'       // verification_done — check/x icon
  | 'replan'       // replan event — warning amber, shows reason
  | 'hitl'         // waiting_approval — amber gate
  | 'guardrail'    // guardrail_rejected — red shield
  | 'knowledge'    // knowledge_retrieved — teal diamond
  | 'complete'     // goal_complete — emerald burst
  | 'failed'       // goal_failed — rose pulse
```

### 4.4 Node State Animations

```typescript
const NODE_ANIMATIONS: Record<GoalNodeType, {
  baseStyle: string,
  activeAnimation: string,   // CSS class or Framer Motion variant name
  glowColor: string,
}> = {
  start:     { glowColor: '#00D4FF', /* breathing electric pulse */  },
  plan:      { glowColor: '#6366F1', /* cascade fill as steps plan */ },
  step: {
    pending: { glowColor: '#334155' /* dim */                        },
    running: { glowColor: '#00D4FF', /* spinning progress ring */    },
    done:    { glowColor: '#00E676', /* success flash → settle */    },
    failed:  { glowColor: '#FF3366', /* red pulse × 3 → settle */   },
  },
  tool:      { glowColor: '#FFB300', /* spark on call, green/red on result */ },
  guardrail: { glowColor: '#FF3366', /* shield materialize animation */       },
  hitl:      { glowColor: '#FFB300', /* amber orbit ring */                   },
  knowledge: { glowColor: '#34D399', /* teal diamond pulse */                 },
  replan:    { glowColor: '#FFB300', /* warning bounce + edge redirect */      },
  complete:  { glowColor: '#00E676', /* final emerald starburst */            },
  failed:    { glowColor: '#FF3366', /* rose distress pulse */                },
};
```

### 4.5 `useGoalExecutionGraph` Hook

This hook consumes `useGoalStream` events and builds the node graph state:

```typescript
interface GoalExecutionGraph {
  nodes: GoalGraphNode[];
  edges: GoalGraphEdge[];
  activeNodeId: string | null;
  tokenStats: { input: number; output: number; costUsd: number; tps: number };
  guardrails: { fired: number; blocked: number; lastRule?: string };
  hitlGates: { pending: number; approved: number; rejected: number };
}

function useGoalExecutionGraph(goalId: string): GoalExecutionGraph {
  // Subscribes to useGoalStream
  // On plan_ready: creates step nodes + edges
  // On step_started: activates step node
  // On tool_call_*: creates/updates tool node
  // On token_chunk: increments token counters
  // On guardrail_*: creates guardrail node + updates stats
  // On hitl_*: creates/updates HITL gate node
  // On knowledge_retrieved: creates knowledge node
  // On replan: creates replan node + redirects edges
  // On verification_done: activates verify node
  // On goal_complete/failed: activates terminal node
}
```

### 4.6 Live Token Waterfall

Inside the active step node, a **token waterfall** shows the streaming LLM output character by character with a glowing cursor:

```typescript
function TokenWaterfall({ stepName: string, tokens: string, isActive: boolean }) {
  // Renders token text with:
  //   - Monospace font, electric cyan color
  //   - Blinking cursor at end (CSS animation)
  //   - Character reveal animation (fast, no delay)
  //   - Overflow: scrolls to latest token
  //   - Shows TPS badge: "42 tok/s"
  //   - Shows model badge: "claude-3.5-sonnet" with Anthropic color
}
```

---

## 5. Dashboard: AgentVerse Command Center

### 5.1 Dashboard Layout

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  AGENTVERSE COMMAND CENTER                                                   │
├────────────────────────────────┬─────────────────────────────────────────────┤
│   AGENT ORBIT (d3-force)       │   KPI STRIP (existing KpiCard × 4)         │
│   Left 60% top                 │   Right 40% top                             │
│   (AgentOrbitView — upgrade)   │   Active goals · Cost today · Agents · Avg  │
├────────────────────────────────┴─────────────────────────────────────────────┤
│   LIVE ACTIVITY STREAM                          │  MINI ORG HEALTH           │
│   (LiveActivityStream — upgrade)                │  (OrgHealthWidget exists)  │
│   Left 65% bottom                               │  Right 35% bottom          │
└─────────────────────────────────────────────────┴────────────────────────────┘
```

### 5.2 Agent Orbit Upgrade

The existing `AgentOrbitView.tsx` uses d3-force with SVG glow. The upgrade adds:

1. **Orbit trails** — each active agent leaves a faint arc trail as it moves
2. **Goal-count rings** — concentric rings around node = number of active goals
3. **Real-time status color** — pulled from live org SSE events
4. **Click-to-navigate** — click node → navigates to org page for that agent
5. **Message beam overlay** — when two agents interact (org SSE events), a brief beam connects them
6. **Size breathing** — active agents pulse in size (scale 0.95 → 1.05, 2s loop)

### 5.3 Live Activity Stream Upgrade

Existing `LiveActivityStream.tsx` shows goal status. Upgrade adds:

1. **Event type icons** — each event has the correct icon (tool ⚡, plan 📋, etc.)
2. **Status color bars** — left border colored by event type
3. **Cost inline** — "+$0.003" shown inline for tool calls
4. **Particle fly-in** — new events fly in from right with a particle trail
5. **Expand on click** — expands to show full event payload inline

---

## 6. Chat Page: Agentic Chat Theater

### 6.1 Current State

ChatPage shows streaming text messages. The upgrade adds a **side panel** that visualizes the agent's execution while the chat is active:

```
┌─────────────────────────┬────────────────────────────────────┐
│   CHAT THREAD           │   AGENT EXECUTION PANEL            │
│   (existing)            │   (new — slides in from right)     │
│                         │                                     │
│   TypingIndicator +     │   Token flow counter               │
│   streaming tokens      │   Active tool cards                │
│   MessageBubbles        │   Step progress nodes              │
│                         │   Knowledge retrieval hits         │
│                         │   Guardrail indicators             │
│                         │   Cost accumulator                 │
└─────────────────────────┴────────────────────────────────────┘
```

### 6.2 Agentic Execution Panel Components

```typescript
// src/features/chat/components/AgenticExecutionPanel.tsx
// Shows real-time execution while chatting

// Tool Call Card — appears when tool_call_complete fires
function ToolCallCard({ toolName, inputs, output, durationMs, success }) {
  // Card with tool name, collapsible inputs/outputs
  // Success: green left border + ⚡ icon
  // Failed: red left border + ✗ icon
  // Duration badge: "342ms"
  // Slides in with SPRING_PANEL, exits after 10s
}

// Step Progress Mini-Node — for each step_started
function StepProgressNode({ step, status, output }) {
  // Small rounded node with:
  //   - Step name (truncated to 50 chars)
  //   - Status dot (running = spin, done = check, failed = x)
  //   - Output preview on hover
}

// Knowledge Hit Badge — for knowledge_retrieved
function KnowledgeHitBadge({ source, chunks, confidence }) {
  // Small teal badge: "📚 3 chunks from docs"
}

// Guardrail Alert — for guardrail_rejected
function GuardrailAlert({ rule }) {
  // Red banner: "🛡️ Guardrail: {rule} — blocked"
  // Auto-dismisses after 5s
}
```

### 6.3 ChatTokenCostBadge Upgrade (existing file)

The existing `ChatTokenCostBadge.tsx` shows token counts. Upgrade it to:
- Show a live cost counter that ticks up as tokens stream
- Color shifts: green → amber → red as cost increases
- Tooltip: breakdown by prompt vs completion tokens
- Provider logo badge next to model name

---

## 7. Agent Detail Page: Agent Profile Neural Card

### 7.1 Current State

AgentDetailPage shows a form-like agent profile. Upgrade to:

```
┌──────────────────────────────────────────────────────────────┐
│  AGENT PROFILE                                               │
│                                                              │
│   [Neural Ring Identity]    │  [Live Stats KPIs]            │
│   Agent avatar with         │  Tasks today / Avg quality    │
│   reputation glow           │  Tokens used / Cost           │
│   Role badge                │  Success rate / Speed         │
│                             │                               │
│   [Active Mission Card]     │  [Execution History Graph]    │
│   Current task with         │  Recharts: success/fail over  │
│   live progress bar         │  time                         │
│   Tool call indicator       │                               │
│                             │                               │
│   [Tool Proficiency Radar]  │  [Memory / Knowledge]         │
│   Recharts RadarChart       │  Recent retrievals            │
│   12 dimensions             │  Stored memories              │
└──────────────────────────────────────────────────────────────┘
```

### 7.2 Neural Ring Identity Card

```typescript
function AgentNeuralRing({ agent, isActive, reputationScore }) {
  // Outer ring: reputation score (0-100) as arc, colored by score
  //   0-50: rose · 50-70: amber · 70-90: electric · 90+: emerald
  // Inner glow: tier-1 (idle) or tier-2 (active)
  // Center: agent type icon (Bot, Cpu, Zap based on role)
  // Status dot: bottom-right, colored by current status
  // Breathing animation when active (4s loop)
  // Spring entry on mount with SPRING_NODE
}
```

---

## 8. Approvals Page: HITL Command Center

### 8.1 Current State

ApprovalsPage shows a list. Upgrade to:

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  HUMAN-IN-THE-LOOP COMMAND CENTER                                            │
│  "The decisions that matter"                                                 │
├────────────────────────────────┬─────────────────────────────────────────────┤
│  PENDING APPROVALS             │   APPROVAL DETAIL                           │
│  (Left 40%)                    │   (Right 60% — slides in)                  │
│                                │                                             │
│  Amber glowing queue           │   Full context: goal, step, tool call      │
│  Priority ordering             │   Risk level badge (from guardrail data)   │
│  Time-remaining countdown      │   Evidence from agent reasoning             │
│  Agent requesting + reason     │   Approve / Reject / Modify buttons        │
│                                │   Impact preview                            │
│  Empty: emerald "All clear" ✓  │   Approval history                         │
└────────────────────────────────┴─────────────────────────────────────────────┘
```

### 8.2 Approval Card Design

```typescript
function ApprovalCard({ approval, onSelect, isSelected }) {
  // Layout:
  //   Left border: amber (pending) → emerald (approved) → rose (rejected)
  //   Amber glow if pending + urgency is high
  //   Agent avatar + name
  //   Tool name being requested: "📞 send_email to 450 recipients"
  //   Risk badge: LOW / MEDIUM / HIGH / CRITICAL (colored)
  //   Time remaining: countdown if timeout set
  //   Approve/Reject quick actions directly on card (no drawer needed for low-risk)
}
```

---

## 9. Civilization / Coordination Pages: Neural Observatory

### 9.1 CivilizationPage Upgrade

The existing `CivilizationMap.tsx` (React Flow) is upgraded to:

1. **Particle system overlay** — Canvas layer above React Flow that renders particles for all inter-agent messages (using the `ParticleCanvas` component)
2. **Agent state animations** — existing `AgentNode.tsx` receives `neuralState` prop that triggers the animated states defined in section 3.2
3. **Message bubble overlay** — when agents exchange messages, a chat-bubble style tooltip briefly appears at the source node
4. **Debate arena mode** — when debate pattern fires, two agent nodes spring together with a colored field between them

### 9.2 CoordinationRunPage Upgrade

Each pattern view gets a dedicated visual:

| Pattern | Visual upgrade |
|---|---|
| Swarm | Convergence animation: nodes orbit toward consensus, particles flow along gossip graph |
| Debate | Two nodes face each other, alternating claim/rebuttal particles, consensus burst |
| Auction | Sealed envelopes fly toward auctioneer, then allocation lines radiate out |
| MoA | Layered stack, proposals flow up, critiques flow down |
| CAMEL | Two nodes in dialogue with turn-indicator |
| Magentic | Progress column with HITL gates shown as physical barriers |
| Supervisor | Tree with delegation beams cascading down |

---

## 10. Knowledge Graph / Graphify / Obsidian Pages

### 10.1 GraphExplorerPage (Knowledge Graph)

The existing page needs a full overhaul. Target experience:

```typescript
// Knowledge nodes as glowing spheres
// Relationship edges as dim lines with particle flow when traversed
// Search: nodes highlight + fade others when searching
// Cluster view: related nodes form glowing constellations
// Hover: node card with summary, related count, last updated
// Click: full detail drawer slides in from right
// 3D depth: z-axis offset gives parallax depth on mouse move
```

### 10.2 GraphifyPage

Graphify builds knowledge graphs from documents. Visual:

```typescript
// During processing: nodes "materialize" one by one with SPRING_NODE
// Edge connections draw in with beam animations
// Cluster badges appear when community detection fires
// Final graph: force-directed with glowing cluster halos
// Export: nodes snap to grid animation before export
```

### 10.3 ObsidianPage  

Obsidian vault visualized as floating notes in space:

```typescript
// Notes as frosted-glass cards floating in dark space
// Links as dim lines between cards
// On hover: card brightens, linked cards also brighten
// Active note: fully bright with electric border
// Search: non-matching cards fade to near-invisible
// Backlinks panel: slides in from right as glass panel
```

---

## 11. Global Navigation Upgrade

### 11.1 Sidebar Enhancement

The existing sidebar (`components/ui/Sidebar.tsx`) needs:

1. **Active route indicator** — electric cyan left border + glow on active item
2. **Activity badges** — live counts from org SSE events:
   - Pending approvals: amber badge
   - Running goals: teal counter
   - Budget alerts: red badge
3. **Collapse animation** — icons scale in when sidebar collapses, spring physics
4. **Section headers** — subtle uppercase labels with cyan opacity

### 11.2 TopBar Enhancement

```typescript
// TopBar additions:
// 1. System health indicator (green/amber/red dot with tooltip)
// 2. Live goal counter: "3 running" with spinning indicator
// 3. Cost today badge: "$2.40 today" with live tick-up
// 4. Notification bell with ambient glow when new approvals
// 5. Voice command button (mic icon — wires to VoiceModal)
```

### 11.3 JARVIS Boot Screen Enhancement

The existing `JARVISBootScreen.tsx` plays on first load. Enhancements:
1. After authentication, show **live dashboard stats** in the boot animation
2. Voice greeting (if VOICE_ENABLED): OmniVoice speaks "Good morning, {name}. Your org has {n} active missions..."
3. Neural constellation preview during boot: agents spinning up

---

## 12. Page-by-Page Motion Upgrade (All 87 Pages)

### 12.1 Priority Order (implement in this order)

**P0 — Flagship (build first):**
- `OrgMissionControlPage` (new) — the showpiece
- `GoalDetailPage` — execution theater upgrade
- `DashboardPage` — command center upgrade

**P1 — High traffic:**
- `AgentDetailPage`, `AgentsListPage`
- `CivilizationPage`, `CoordinationRunPage`
- `ApprovalsPage`
- `ChatPage` (execution panel)

**P2 — Feature pages:**
- All workflow pages (`WorkflowListPage`, `WorkflowRunDetailPage` etc.)
- `TemplateLibraryPage`
- `ConnectorsCatalogPage`
- `KnowledgePage`, `GraphExplorerPage`
- `AnalyticsDashboardPage`, `AIOpsDashboard`

**P3 — Settings & admin:**
- All settings pages
- `AdminPage`, `SecurityCenterPage`, `CompliancePage`

### 12.2 Universal Motion Pattern (Every Page)

Every page that doesn't yet have animation gets this exact pattern:

```tsx
// 1. Wrap with JARVISPageShell (page blur-in entry)
<JARVISPageShell>

  {/* 2. Header section: fadeUp variant */}
  <motion.div variants={fadeUp} initial="hidden" animate="visible">
    <h1>Page Title</h1>
  </motion.div>

  {/* 3. KPI strip: JARVISStagger with 60ms delay */}
  <JARVISStagger staggerMs={60}>
    {kpis.map(kpi => <JARVISStaggerItem key={kpi.id}><KpiCard {...kpi}/></JARVISStaggerItem>)}
  </JARVISStagger>

  {/* 4. Main content: slideRight or fadeUp */}
  <JARVISStagger staggerMs={40}>
    {items.map(item => <JARVISStaggerItem key={item.id}>...</JARVISStaggerItem>)}
  </JARVISStagger>

  {/* 5. Empty states: animated (not static) */}
  <AnimatePresence>
    {isEmpty && <EmptyState variants={scaleIn} message="..." action="..." />}
  </AnimatePresence>

</JARVISPageShell>
```

### 12.3 Card Hover/Active States (Universal)

Every card across the platform:
```tsx
<motion.div
  whileHover={{ scale: 1.02, boxShadow: tokens.glow.tier2, transition: SPRING_FAST }}
  whileTap={{  scale: 0.98, transition: SPRING_PARTICLE }}
  layout  // smooth layout animations when list reorders
>
```

### 12.4 List Update Animations

When lists update from SSE/TanStack Query:
```tsx
<AnimatePresence mode="popLayout">  // use popLayout not sync for lists
  {items.map(item => (
    <motion.div
      key={item.id}
      layout
      variants={fadeUp}
      initial="hidden"
      animate="visible"
      exit="exit"
    >
      <ItemCard {...item} />
    </motion.div>
  ))}
</AnimatePresence>
```

---

## 13. Complete File Creation Map

### 13.1 New Files to Create

```
src/
├── lib/design/
│   ├── tokens.ts                                    # NEW — design tokens
│   └── motion.ts                                    # NEW — motion presets
│
├── components/canvas/
│   ├── ParticleCanvas.tsx                           # NEW — canvas particle system
│   ├── ParticleCanvas.worker.ts                     # NEW — offscreen canvas worker
│   └── AgentBeam.tsx                                # NEW — SVG beam animation
│
├── components/neural/
│   ├── AgentNeuralNode.tsx                          # NEW — universal agent node
│   ├── NeuralEdge.tsx                               # NEW — animated edge
│   ├── GuardrailShield.tsx                          # NEW — red shield materializer
│   ├── HITLGateNode.tsx                             # NEW — amber approval gate
│   ├── TokenWaterfall.tsx                           # NEW — streaming tokens display
│   └── ToolSparkNode.tsx                            # NEW — tool call spark
│
├── hooks/
│   ├── useSSEToParticles.ts                         # NEW — SSE → particle bridge
│   ├── useAgentNeuralGraph.ts                       # NEW — org SSE → graph state
│   ├── useGoalExecutionGraph.ts                     # NEW — goal SSE → graph state
│   └── useConstellationLayout.ts                    # NEW — d3-force layout manager
│
└── features/
    ├── org/
    │   ├── OrgMissionControlPage.tsx                # NEW — flagship mission control
    │   ├── components/
    │   │   ├── AgentConstellation.tsx               # NEW — d3+React Flow constellation
    │   │   ├── MissionCommandLog.tsx                # NEW — live event log panel
    │   │   ├── MissionInspectorPanel.tsx            # NEW — deep-dive panel
    │   │   ├── AgentNeuralRing.tsx                  # NEW — reputation ring identity
    │   │   └── TokenFlowOverlay.tsx                 # NEW — token particles overlay
    │   └── hooks/
    │       └── useOrgNeuralState.ts                 # NEW — org realtime → visual state
    │
    ├── goals/
    │   └── components/
    │       ├── GoalExecutionGraph.tsx               # NEW — node graph execution theater
    │       └── GoalNeuralStats.tsx                  # NEW — token/guardrail/cost HUD
    │
    └── chat/
        └── components/
            └── AgenticExecutionPanel.tsx            # NEW — right-panel execution viz
```

### 13.2 Files to Modify

```
src/features/org/OrgPage.tsx                  — Add OrgMissionControlPage import/route
src/features/goals/GoalDetailPage.tsx         — Replace execution tab with GoalExecutionGraph
src/features/dashboard/components/AgentOrbitView.tsx — Add trails, goal rings, beams
src/features/dashboard/components/LiveActivityStream.tsx — Add icons, particles, expand
src/features/civilization/CivilizationMap.tsx — Add ParticleCanvas overlay layer
src/features/civilization/AgentNode.tsx       — Add neuralState prop + all 9 animations
src/features/coordination/CoordinationRunPage.tsx — Pattern-specific visual upgrades
src/features/approvals/ApprovalsPage.tsx      — HITL command center layout
src/features/agents/AgentDetailPage.tsx       — Neural ring identity card
src/features/chat/ChatPage.tsx                — Add AgenticExecutionPanel side panel
src/features/chat/ChatTokenCostBadge.tsx      — Live cost counter + model badge
src/components/ui/Sidebar.tsx                 — Activity badges, active glow, collapse anim
src/components/ui/TopBar.tsx                  — Health dot, cost today, notification glow
src/components/ui/JARVISPageShell.tsx         — Add SPRING_NODE + SPRING_PARTICLE exports
```

---

## 14. Performance Contracts

| Metric | Target | Implementation |
|---|---|---|
| Particle system FPS | 60 fps with 200 active particles | OffscreenCanvas worker + ring buffer |
| Node graph updates | <16ms per SSE event | React Flow batched updates |
| Spring animations | All springs, no duration/ease | Framer Motion configuration |
| SSE → visual latency | <100ms event to visual | Optimistic UI + direct state update |
| Page entry animation | <300ms blur-in | SPRING_PAGE = stiffness 280, damping 26 |
| Memory (1hr session) | <80MB | Ring buffers, particle eviction |
| Reduced motion | All animations disabled | `useReducedMotion()` on every component |
| Mobile (768px) | Constellation collapses to list | CSS breakpoint + conditional render |

---

## 15. Accessibility Contracts

All visual additions MUST:
- Have `aria-hidden="true"` on decorative canvas/SVG elements
- Provide `aria-live="polite"` on SSE-driven status updates
- Maintain `prefers-reduced-motion` — all animations gate on `!reduce`
- Keep keyboard navigation intact after visual overlays
- Never place interactive elements inside canvas (use DOM overlays)
- Every status change announced via existing `aria-live` regions

---

## 16. E2E Flow: Goal Submission to Visual Completion

```
1. User submits goal on GoalDetailPage
   → Button press: SPRING_PARTICLE scale feedback (0→1)
   → Status changes to "planning"

2. plan_ready SSE fires
   → Step nodes materialize in DAG with SPRING_NODE stagger
   → Indigo cascade particles flow from PLAN node down to step nodes
   → Step count badge pulses on PLAN node

3. step_started fires (for each step)
   → Step node activates: electric glow tier-2
   → Token waterfall begins: cyan particles flow from LLM area
   → Step node progress ring begins spinning

4. token_chunk fires (continuously while LLM generates)
   → TokenWaterfall shows character-by-character output
   → Particle density on LLM→step edge varies with TPS
   → Cost counter increments: "+$0.0001" fades up

5. tool_call_pending_approval fires
   → Tool node gets amber orbit ring
   → HITLGateNode materializes on the edge to next step
   → ApprovalsPage badge increments (red ambient glow on sidebar)

6. tool_call_complete fires
   → Tool node: green spark burst (8 particles radiate)
   → Tool card slides in to AgenticExecutionPanel (chat) or log panel
   → Green particle travels from tool node → step node (result return)
   → Duration badge: "342ms" fades in below tool node

7. guardrail_rejected fires
   → GuardrailShield materializes at blocked edge
   → Red particle burst at tool/step node
   → Node pulses red ×3 → settles to red outline
   → Sidebar guardrail count increments

8. verification_done fires
   → VERIFY node activates with check or X icon
   → If success: emerald arc to COMPLETE node
   → If failure: amber arc to REPLAN node

9. goal_complete fires
   → COMPLETE node: emerald starburst (16 particles radiate)
   → All nodes settle to dim success state
   → Goal card in MissionsListPage: springs to "completed" state
   → Activity feed: emerald event slides in from right

Full loop: ~0ms display lag (optimistic → SSE confirms)
```

---

## 17. Implementation Phases

### Phase 0 — Foundation (1 sprint, no visible UI change)
```
- [ ] src/lib/design/tokens.ts
- [ ] src/lib/design/motion.ts  
- [ ] Extend JARVISPageShell with SPRING_NODE + SPRING_PARTICLE
- [ ] src/components/canvas/ParticleCanvas.tsx (ring buffer, OffscreenCanvas)
- [ ] src/components/canvas/ParticleCanvas.worker.ts
- [ ] src/components/canvas/AgentBeam.tsx (SVG beam animation)
- [ ] src/components/neural/* (5 components)
- [ ] src/hooks/useSSEToParticles.ts
- [ ] src/hooks/useGoalExecutionGraph.ts
- [ ] src/hooks/useAgentNeuralGraph.ts
```

### Phase 1 — Flagship Screens (2 sprints, maximum visual impact)
```
- [ ] src/features/org/OrgMissionControlPage.tsx
- [ ] src/features/org/components/AgentConstellation.tsx (d3-force + React Flow)
- [ ] src/features/org/components/MissionCommandLog.tsx
- [ ] src/features/org/components/MissionInspectorPanel.tsx
- [ ] src/features/goals/GoalDetailPage.tsx — GoalExecutionGraph integration
- [ ] src/features/goals/components/GoalExecutionGraph.tsx
- [ ] src/features/dashboard/components/AgentOrbitView.tsx upgrade
```

### Phase 2 — High Traffic Pages (1 sprint)
```
- [ ] GoalDetailPage AgenticExecutionPanel sidebar
- [ ] ChatPage execution panel + ChatTokenCostBadge upgrade
- [ ] ApprovalsPage HITL command center
- [ ] AgentDetailPage neural ring card
- [ ] CivilizationPage ParticleCanvas overlay
- [ ] Sidebar + TopBar enhancements
```

### Phase 3 — Universal Motion Pass (1 sprint)
```
- [ ] All 54 zero-animation pages: JARVISPageShell + JARVISStagger
- [ ] All card hover/active states: whileHover + whileTap
- [ ] All lists: AnimatePresence popLayout
- [ ] Empty states: animated
- [ ] Error states: animated  
- [ ] Loading skeletons: shimmer upgrade
```

### Phase 4 — Advanced Visualizations (1 sprint)
```
- [ ] CoordinationRunPage pattern visualizations
- [ ] GraphExplorerPage knowledge graph overhaul
- [ ] GraphifyPage node materialization
- [ ] ObsidianPage floating notes
- [ ] OrgPage intelligence dashboard overlays
```

### Phase 5 — Polish + E2E (0.5 sprint)
```
- [ ] Reduced motion compliance audit (all 87 pages)
- [ ] Mobile responsive audit (constellation → list)
- [ ] Keyboard navigation audit
- [ ] E2E test: Goal submission → visual completion loop
- [ ] Performance profiling (60fps guarantee)
- [ ] Storybook stories for all neural components
```
