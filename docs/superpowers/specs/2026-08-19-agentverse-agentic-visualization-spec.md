# AgentVerse World-Class Agentic Visualization Specification

> **Vision:** A living, breathing JARVIS-style command center where you can *see* agents think, talk, and collaborate in real-time — particles of intelligence flowing between nodes, debates materializing as structured dialogues, swarm intelligence converging before your eyes.

---

## 1. Current State Analysis

### What Exists (Strong Foundation)
| Component | Location | Capability |
|-----------|----------|------------|
| **CivilizationMap** | `features/civilization/CivilizationMap.tsx` | React Flow canvas with radial layout, animated edges, minimap |
| **AgentNode** | `features/civilization/AgentNode.tsx` | Glassmorphic cards with reputation rings, status gradients, role badges |
| **BlackboardFeed** | `features/civilization/BlackboardFeed.tsx` | Real-time findings feed with confidence bars |
| **DebateViewer** | `features/civilization/DebateViewer.tsx` | Two-column claim face-off with consensus verdict |
| **AgentInspectorDrawer** | `features/civilization/AgentInspectorDrawer.tsx` | Tabbed slide-over (Overview/Messages/Config) |
| **CoordinationRunPage** | `features/coordination/CoordinationRunPage.tsx` | Multi-pattern evidence view (MoA, CAMEL, Generative, Swarm, Auction) |
| **SharedTranscript** | `features/coordination/SharedTranscript.tsx` | Causal message log with sender→recipient |
| **Real-time SSE** | `useCoordinationStream.ts` + `useCivilizationStream.ts` | Live event streams with auto-reconnect |

### Backend Coordination Patterns (Ready to Visualize)
- **Swarm** — Gossip-based convergence, claim propagation
- **Auction** — Sealed bids, fairness scoring, allocation
- **Generative Agents** — Persona-driven simulation with observation/reflection
- **CAMEL** — Role-play dialogues with inception/termination
- **Magentic** — Human-in-the-loop with replan/stall detection
- **MoA** — Layer-wise proposal/critique aggregation
- **Group Chat** — Speaker policy, compaction, topic tracking
- **Supervisor** — Plan → delegate → synthesize
- **Debate** — Structured argument exchange with consensus
- **Goal Tree** — Recursive decomposition
- **Handoffs** — Membership changes, context transfer

---

## 2. Target Experience: "Agentic Observatory"

### Core Metaphor: **Neural Constellation**
Agents are not static nodes — they're *living neurons* in a thinking brain. Communication = synaptic firing. Debates = cortical columns resolving conflict. Swarms = ensemble synchronization.

### Three Synchronized Views (JARVIS Triptych)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  AGENTIC OBSERVATORY                                                         │
├──────────────┬──────────────────────────────┬──────────────────────────────┤
│  NEURAL GRAPH│       MESSAGE STREAM          │     INSPECTOR DRAWER         │
│  (Left 50%)  │       (Right 30%)             │     (Right 20% slide-over)   │
│              │                                │                              │
│  • Live node │  • Particle messages          │  • Agent deep-dive           │
│    animations │    flowing between nodes      │  • Message history           │
│  • Thought   │  • Expandable bubbles         │  • Config & connectors       │
│    bubbles   │  • Protocol badges            │  • Budget & reputation       │
│  • Attention │  • Timeline scrubber          │  • Subagent tree             │
│    beams     │  • Pattern visualization      │                              │
└──────────────┴────────────────────────────────┴──────────────────────────────┘
```

---

## 3. Specification: Neural Graph (Primary Canvas)

### 3.1 Node States (Animated)
```typescript
type AgentNeuralState = 
  | 'idle'           // Breathing glow, slow pulse
  | 'thinking'       // Internal shimmer, particle generation
  | 'communicating'  // Emitting/receiving message particles
  | 'debating'       // Dual-color oscillation (claim A vs B)
  | 'executing'      // Tool call sparks, progress ring
  | 'waiting'        // Subtle anticipation pulse
  | 'error'          // Red distress signal
  | 'completed';     // Success burst, then settle
```

### 3.2 Message Particles (The "Synapses")
```typescript
interface MessageParticle {
  id: string;
  from: string;           // source agentId
  to: string;             // target agentId (or 'broadcast')
  type: MessageType;
  payload: unknown;
  timestamp: number;
  // Visual
  color: string;          // by type
  size: number;           // by payload size
  trail: boolean;         // leave glowing path
  speed: number;          // by urgency
}

type MessageType = 
  | 'task_request'      // Blue: "Do this"
  | 'task_response'     // Green: "Done, here's result"
  | 'knowledge_share'   // Emerald: "Found this"
  | 'debate_claim'      // Purple: "I claim X"
  | 'debate_rebuttal'   // Orange: "But Y"
  | 'consensus'         // Gold: "We agree"
  | 'spawn_request'     // Amber: "Need agent"
  | 'spawn_response'    // Cyan: "Agent ready"
  | 'budget_alert'      // Red: "Running low"
  | 'heartbeat';        // Dim: "I'm alive"
```

### 3.3 Animation Physics (Framer Motion + Canvas)
- **Particles**: Canvas-rendered (performance) with requestAnimationFrame
- **Node reactions**: Framer Motion spring (stiffness: 400, damping: 30)
- **Edge pulses**: Animated strokeDashOffset on SVG edges
- **Attention beams**: Three.js/WebGL for multi-target attention visualization

### 3.4 Layout Algorithm (Force-Directed + Hierarchical)
```typescript
// Hybrid: Force-directed for clusters, hierarchical for spawn lineage
// - Spawn tree: fixed parent→child vertical layout
// - Communication clusters: force-directed within depth bands
// - Debate pairs: spring-together with repulsion from others
// - Min 120px node separation, max 300px
```

---

## 4. Specification: Message Stream (Communication Log)

### 4.1 Live Stream Component
```tsx
// features/coordination/NeuralMessageStream.tsx
interface NeuralMessageStreamProps {
  sessionId: string;
  selectedAgentId?: string;    // Filter to/from this agent
  patternFilter?: PatternType; // Show only pattern messages
  maxMessages: 100;            // Virtualized
}
```

### 4.2 Message Bubble Design
```
┌─────────────────────────────────────────────────────────────┐
│ 🤖 researcher_001  →  🤖 analyst_003    [knowledge_share]   │
│ ████████████░░░░░░░░  87% confidence      14:32:15          │
├─────────────────────────────────────────────────────────────┤
│  "Found 3 relevant papers on transformer efficiency.        │
│   Key insight: sparse attention reduces compute 40%..."     │
│  [Expand ▼]                                                 │
├─────────────────────────────────────────────────────────────┤
│  📎 Citations: [paper_1, paper_2]    🔗 Trace: step_3       │
└─────────────────────────────────────────────────────────────┘
```

### 4.3 Expandable Details (Progressive Disclosure)
- Click → full payload JSON with syntax highlighting
- "View in Graph" → pans Neural Graph to sender/receiver
- "Open Inspector" → opens AgentInspectorDrawer for sender

### 4.4 Timeline Scrubber
- Horizontal scrollbar with time markers
- Hover → tooltip with message count
- Drag → replay mode (pauses live stream, shows historical state)

---

## 5. Specification: Pattern Visualization Overlay

### 5.1 Protocol HUD (Top of Neural Graph)
```
┌────────────────────────────────────────────────────────────────┐
│  PATTERN: MAGENTIC  │  ROUND 3/5  │  STALL: 0  │  🟢 HEALTHY   │
│  ████████████░░░░░░░░░░  60% consensus on step 4               │
│  Supervisor: coordinator_001  │  Subagents: 4 active            │
└────────────────────────────────────────────────────────────────┘
```

### 5.2 Pattern-Specific Visualizations

| Pattern | Visual Metaphor | Key Elements |
|---------|-----------------|--------------|
| **Supervisor** | Tree with pulse | Plan steps → delegate arrows → synthesis converge |
| **Debate** | Two-column arena | Claim cards sliding, confidence bars, verdict burst |
| **Swarm** | Constellation | Gossip ripples, claim propagation waves |
| **Auction** | Sealed envelopes | Bid particles → reveal animation → allocation lines |
| **MoA** | Layer cake | Layer 1→2→3 proposal flow, critique back-flow |
| **CAMEL** | Dialogue bubbles | Role badges, turn-taking, inception/termination |
| **Magentic** | Kanban + chat | Progress columns, human review gates, replan triggers |
| **Goal Tree** | Recursive fractal | Decomposition branches, leaf execution, roll-up |

---

## 6. Specification: Inspector Drawer (Deep Dive)

### 6.1 Enhanced Tabs
```typescript
type InspectorTab = 
  | 'overview'      // Existing: reputation, budget, status
  | 'messages'      // Existing: bus messages
  | 'thoughts'      // NEW: Internal reasoning trace (private_reasoning)
  | 'tools'         // NEW: Tool call timeline with latency/success
  | 'subagents'     // NEW: Spawned agent tree with hierarchy
  | 'memory'        // NEW: Read/write operations on shared memory
  | 'budget'        // NEW: Cost breakdown by step/model
  | 'eval';         // NEW: Evaluation scores per dimension
```

### 6.2 Thought Stream (NEW)
Real-time LLM reasoning tokens streaming in:
```
▶ Planner: "The goal requires web research..."
  ▸ Step 1: Search for "transformer efficiency" [executing...]
  ▸ Step 2: Analyze papers [pending]
  ▸ Step 3: Synthesize findings [pending]
  
  💭 Private: "User likely wants actionable optimization..."
```

---

## 7. Technical Architecture

### 7.1 Data Flow
```
Backend (Coordination Runtime)
    │
    ├── SSE /events          → Real-time particles + node state updates
    ├── REST /sessions/:id   → Initial graph + full history
    ├── REST /swarm          → Topology (nodes/edges)
    ├── REST /generative     → Agent personas, observations
    ├── REST /moa            → Layer proposals/critiques
    ├── REST /camel          → Dialogue turns
    └── REST /auction        → Bids, allocations
    │
Frontend (React + Zustand + Framer Motion)
    │
    ├── NeuralGraph (Canvas + React Flow hybrid)
    ├── MessageStream (Virtualized list + Canvas particles)
    ├── InspectorDrawer (React + Framer Motion)
    └── PatternHUD (React + CSS animations)
```

### 7.2 State Management (Zustand Store)
```typescript
// stores/agenticObservatory.ts
interface AgenticObservatoryState {
  // Session
  sessionId: string | null;
  pattern: PatternType | null;
  
  // Graph
  nodes: Map<string, NeuralNode>;
  edges: Map<string, NeuralEdge>;
  particles: MessageParticle[];
  
  // Stream
  messageHistory: CoordinationMessage[];
  liveCursor: number;
  isReplaying: boolean;
  replaySpeed: 1 | 2 | 5 | 10;
  
  // UI
  selectedAgentId: string | null;
  hoveredAgentId: string | null;
  inspectorOpen: boolean;
  inspectorTab: InspectorTab;
  patternFilter: PatternType | 'all';
  
  // Actions
  setSession: (id: string) => void;
  applyEvent: (event: CoordinationEvent) => void;
  addParticle: (particle: MessageParticle) => void;
  selectAgent: (id: string | null) => void;
  toggleInspector: (tab?: InspectorTab) => void;
  scrubTo: (sequence: number) => void;
}
```

### 7.3 Performance Targets
| Metric | Target |
|--------|--------|
| 60fps with 50 agents + 200 particles | ✅ Canvas particles |
| <100ms event-to-visual latency | ✅ Optimistic UI + SSE |
| <50MB memory for 1hr session | ✅ Ring buffers, virtualization |
| Mobile responsive | ✅ Collapsible panels |

---

## 8. Implementation Phases

### Phase 1: Neural Graph Core (Week 1-2)
- [ ] Canvas-based particle system (MessageParticleRenderer)
- [ ] Neural node states with Framer Motion
- [ ] Hybrid layout (force + hierarchical)
- [ ] Integration with existing CivilizationMap types
- [ ] JARVISPageShell + design tokens

### Phase 2: Message Stream & Sync (Week 2-3)
- [ ] NeuralMessageStream with virtualization
- [ ] Expandable message bubbles
- [ ] Timeline scrubber with replay
- [ ] Graph ↔ Stream ↔ Inspector synchronization
- [ ] Pattern filter chips

### Phase 3: Pattern Visualizations (Week 3-4)
- [ ] PatternHUD component
- [ ] Supervisor tree animation
- [ ] Debate arena visualization
- [ ] Swarm convergence waves
- [ ] Auction reveal animation
- [ ] MoA layer flow
- [ ] CAMEL dialogue view

### Phase 4: Inspector Enhancements (Week 4)
- [ ] Thought stream tab (private_reasoning)
- [ ] Tool call timeline
- [ ] Subagent hierarchy tree
- [ ] Memory operations log
- [ ] Budget/cost breakdown

### Phase 5: Polish & E2E (Week 5)
- [ ] End-to-end test: Goal → Coordination → Visualization
- [ ] Reduced motion compliance
- [ ] Mobile responsive (collapsible panels)
- [ ] Accessibility (ARIA live regions, keyboard nav)
- [ ] Performance profiling
- [ ] Documentation + Storybook stories

---

## 9. New Files to Create

```
agent-verse-frontend/
├── src/
│   ├── components/
│   │   ├── agentic/
│   │   │   ├── NeuralGraph.tsx              # Main canvas + React Flow hybrid
│   │   │   ├── NeuralNode.tsx               # Animated agent node (extends AgentNode)
│   │   │   ├── NeuralEdge.tsx               # Animated edges with particle flow
│   │   │   ├── MessageParticleRenderer.tsx  # Canvas particle system
│   │   │   ├── MessageParticle.ts           # Particle physics/types
│   │   │   ├── NeuralMessageStream.tsx      # Virtualized message list
│   │   │   ├── MessageBubble.tsx            # Expandable message component
│   │   │   ├── TimelineScrubber.tsx         # Replay control
│   │   │   ├── PatternHUD.tsx               # Protocol overlay
│   │   │   ├── patterns/
│   │   │   │   ├── SupervisorViz.tsx
│   │   │   │   ├── DebateArena.tsx
│   │   │   │   ├── SwarmConvergence.tsx
│   │   │   │   ├── AuctionReveal.tsx
│   │   │   │   ├── MoALayerFlow.tsx
│   │   │   │   ├── CAMELDialogue.tsx
│   │   │   │   ├── MagenticKanban.tsx
│   │   │   │   └── GoalTreeFractal.tsx
│   │   │   └── AgenticInspectorDrawer.tsx   # Enhanced inspector
│   │   └── ui/
│   │       └── JARVISPageShell.tsx          # (exists)
│   ├── features/
│   │   └── agentic-observatory/
│   │       ├── AgenticObservatoryPage.tsx   # Main page entry
│   │       ├── AgenticObservatoryPage.test.tsx
│   │       ├── hooks/
│   │       │   ├── useNeuralGraph.ts
│   │       │   ├── useMessageParticles.ts
│   │       │   ├── usePatternViz.ts
│   │       │   └── useReplay.ts
│   │       └── stores/
│   │           └── agenticObservatory.ts
│   ├── lib/
│   │   └── design/
│   │       └── motion.ts                    # (exists - extend)
│   └── app/
│       └── App.tsx                          # Add routes
```

### Backend Enhancements (if needed)
```
agent-verse-backend/
├── app/
│   ├── api/
│   │   └── coordination/
│   │       └── observatory.py               # Unified observability endpoint
│   └── coordination/
│       └── observability.py                 # Event enrichment for UI
```

---

## 10. API Contracts (Frontend ↔ Backend)

### 10.1 Enhanced SSE Event Types
```python
# Backend: app/coordination/observability.py
class NeuralEventType(str, Enum):
    NODE_STATE_CHANGED = "node_state_changed"
    MESSAGE_SENT = "message_sent"
    MESSAGE_RECEIVED = "message_received"
    PARTICLE_EMITTED = "particle_emitted"
    PATTERN_ROUND_START = "pattern_round_start"
    PATTERN_ROUND_END = "pattern_round_end"
    CONSENSUS_REACHED = "consensus_reached"
    DEBATE_CLAIM = "debate_claim"
    DEBATE_REBUTTAL = "debate_rebuttal"
    SPAWN_REQUEST = "spawn_request"
    SPAWN_COMPLETE = "spawn_complete"
    TOOL_CALL_START = "tool_call_start"
    TOOL_CALL_END = "tool_call_end"
    THOUGHT_TOKEN = "thought_token"      # Private reasoning stream
    BUDGET_UPDATE = "budget_update"
    REPUTATION_CHANGE = "reputation_change"

class NeuralEvent(BaseModel):
    sequence: int
    event_type: NeuralEventType
    timestamp: datetime
    agent_id: str | None
    target_agent_id: str | None
    pattern: PatternType | None
    round: int | None
    payload: dict
    # Visual hints
    visual: NeuralVisualHints

class NeuralVisualHints(BaseModel):
    particle_color: str | None
    particle_size: float | None
    node_glow: str | None
    edge_pulse: bool
    attention_beam: list[str] | None  # target agent IDs
```

### 10.2 REST Endpoints
```
GET  /api/v1/coordination/sessions/{id}/neural-graph
    → { nodes: NeuralNode[], edges: NeuralEdge[], pattern: PatternType }

GET  /api/v1/coordination/sessions/{id}/thoughts/{agent_id}
    → { tokens: ThoughtToken[], reasoning_trace: string }

GET  /api/v1/coordination/sessions/{id}/tool-timeline
    → { calls: ToolCall[] }

GET  /api/v1/coordination/sessions/{id}/subagents
    → { tree: SubagentNode[] }
```

---

## 11. Design Tokens Extension

```typescript
// lib/design/tokens.ts - ADDITIONS
export const neural = {
  // Particle colors by message type
  particle: {
    task_request:     '#3b82f6',  // Blue
    task_response:    '#22c55e',  // Green
    knowledge_share:  '#10b981',  // Emerald
    debate_claim:     '#a855f7',  // Purple
    debate_rebuttal:  '#f97316',  // Orange
    consensus:        '#f59e0b',  // Amber
    spawn_request:    '#f59e0b',  // Amber
    spawn_response:   '#06b6d4',  // Cyan
    budget_alert:     '#ef4444',  // Red
    heartbeat:        '#64748b',  // Slate
  },
  
  // Node state glows
  nodeGlow: {
    idle:           'rgba(100,116,139,0.3)',
    thinking:       'rgba(59,130,246,0.4)',
    communicating:  'rgba(16,185,129,0.5)',
    debating:       'rgba(168,85,247,0.5)',
    executing:      'rgba(249,115,22,0.5)',
    waiting:        'rgba(245,158,11,0.4)',
    error:          'rgba(239,68,68,0.5)',
    completed:      'rgba(34,197,94,0.4)',
  },
  
  // Spring presets for neural animations
  springs: {
    particle:   { type: 'spring', stiffness: 800, damping: 20 },
    nodeReact:  { type: 'spring', stiffness: 500, damping: 35 },
    edgePulse:  { type: 'spring', stiffness: 300, damping: 25 },
    beam:       { type: 'spring', stiffness: 200, damping: 15 },
  }
} as const;
```

---

## 12. Acceptance Criteria (Definition of Done)

### Functional
- [ ] Submit a goal → see agents spawn, communicate, converge in real-time
- [ ] Click any message in stream → highlights sender/receiver in graph
- [ ] Click agent node → opens inspector with full context
- [ ] Switch pattern filter → graph + stream update instantly
- [ ] Drag timeline scrubber → replays historical state perfectly
- [ ] All 10 coordination patterns have distinct visualizations

### Visual
- [ ] 60fps on 50-agent swarm with 200 concurrent particles
- [ ] JARVIS design tokens used everywhere (no hardcoded colors)
- [ ] Spring animations throughout (no duration/ease)
- [ ] Reduced motion: particles disabled, instant state changes
- [ ] Dark mode only (JARVIS mandate)

### Technical
- [ ] TypeScript strict mode passes
- [ ] Unit tests for particle physics, layout, replay logic
- [ ] Integration test: full goal → visualization pipeline
- [ ] Bundle size < 200KB gzipped for observatory chunk
- [ ] Memory stable over 1hr session

### Accessibility
- [ ] ARIA live regions for particle announcements
- [ ] Keyboard navigation: Tab through agents, Enter to inspect
- [ ] Screen reader: "Agent researcher_001 sent knowledge_share to analyst_003"
- [ ] High contrast mode support

---

## 13. Route Structure

```tsx
// App.tsx additions
<Route path="observatory" element={lazy_rb("Agentic Observatory", <AgenticObservatoryPage />)} />
<Route path="observatory/:sessionId" element={lazy_rb("Agentic Observatory", <AgenticObservatoryPage />)} />
<Route path="civilization/:civId/neural" element={lazy_rb("Neural View", <NeuralCivilizationPage />)} />
```

---

## 14. Dependencies to Add

```json
// package.json additions
{
  "dependencies": {
    "@xyflow/react": "^12.x",           // Already present
    "three": "^0.160.x",                // Attention beams, 3D effects
    "@react-three/fiber": "^8.x",       // React Three Fiber
    "@react-three/drei": "^9.x",        // Helpers
    "d3-force": "^3.x",                 // Force-directed layout
    "d3-scale": "^4.x",                 // Color/size scales
    "d3-interpolate": "^3.x",           // Color interpolation
    "canvas-confetti": "^1.x"           // Consensus celebration
  },
  "devDependencies": {
    "@types/three": "^0.160.x",
    "@types/d3-force": "^3.x",
    "@types/canvas-confetti": "^1.x"
  }
}
```

---

## 15. Risk Mitigation

| Risk | Mitigation |
|------|------------|
| Particle performance at scale | Canvas rendering, object pooling, LOD (reduce particles when zoomed out) |
| SSE reconnection state sync | Server-side cursor, client replays missed events on reconnect |
| Pattern visualization complexity | Modular pattern components, shared base class, feature flags |
| Mobile layout | Collapsible panels, swipe gestures, responsive breakpoints |
| Backward compatibility | New routes, existing CivilizationPage unchanged |

---

## 16. Future Extensions (Post-MVP)

1. **3D Neural Constellation** — Three.js/WebGPU immersive view
2. **Audio Sonification** — Pitch/timbre by message type (optional)
3. **Multi-tenant Observatory** — Platform-wide agent topology
4. **AI-Generated Narratives** — LLM summarizes "what happened"
5. **Export/Share** — Recorded session as interactive replay link
6. **Plugin API** — Custom pattern visualizations

---

*Spec Version: 1.0*  
*Author: AgentVerse Design System*  
*Date: 2026-08-19*  
*Status: Ready for Implementation Planning*