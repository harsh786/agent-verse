# AgentVerse — Jarvis UI/UX Revamp Specification
**Version:** 1.0 | **Date:** 2026-08-18 | **Status:** READY FOR IMPLEMENTATION
**Scope:** All 87 frontend Page.tsx files + shared design system foundation

---

## Audit Baseline (Current State)

| Tier | Count | State |
|------|-------|-------|
| Score 4+ (fully Jarvis) | **0 / 87** | Not started |
| Score 1 (JARVISPageShell wrapper only) | **20 / 87** | Page entrance only, no inner motion |
| Score 2-3 (partial framer-motion) | **13 / 87** | Inconsistent, no design system |
| Score 0 (zero animation) | **54 / 87** | Plain React |

### What Already Exists

`components/ui/JARVISPageShell.tsx` — **built, working, correct spring physics:**
```typescript
export const SPRING_PAGE   = { type: 'spring', stiffness: 280, damping: 26 }
export const SPRING_PANEL  = { type: 'spring', stiffness: 300, damping: 28 }
export const SPRING_FAST   = { type: 'spring', stiffness: 600, damping: 35 }
export const SPRING_SLOW   = { type: 'spring', stiffness: 200, damping: 25 }
export const SPRING_BOUNCY = { type: 'spring', stiffness: 450, damping: 18 }
```
Also contains: `<JARVISStagger>`, `<JARVISStaggerItem>`, `<JARVISButton>` — **built but used by 0 pages**.

### What Is Missing (Must Create Before Implementing Pages)

| File | Status | Blocks |
|------|--------|--------|
| `src/lib/design/tokens.ts` | ❌ Missing | Color tokens, glass, shadows |
| `src/lib/design/motion.ts` | ❌ Missing | Named variants, spring re-exports |
| `src/components/ui/StatusOrb.tsx` | ❌ Missing | Pulsing status across all pages |
| `src/hooks/useMotionSafe.ts` | ❌ Missing | Standalone reduced-motion hook |
| `EmptyState.tsx` animation | ❌ None | 40+ pages show static empty states |

---

## Implementation Order

```
Phase 0: Foundation (prerequisite — unblocks everything)
  Step 1: Create lib/design/tokens.ts
  Step 2: Create lib/design/motion.ts
  Step 3: Create components/ui/StatusOrb.tsx
  Step 4: Upgrade EmptyState.tsx with animation

Phase 1: Quick Wins — 20 JARVISPageShell-only pages
  Apply JARVISStagger + JARVISStaggerItem to card grids and lists
  ~30 min per page, high visual impact

Phase 2: 54 Zero-Animation Pages
  Feature by feature, priority ordered

Phase 3: Polish Pass
  Micro-interactions, hover states, empty states, error states
```

---

## Phase 0 — Foundation Files

### Step 1: `src/lib/design/tokens.ts`

```typescript
// src/lib/design/tokens.ts
// Single source of truth for all Jarvis design values.
// Import from this file — never hardcode colors or shadows.

export const tokens = {
  color: {
    // Primary electric
    electric:       '#00D4FF',
    electricDim:    'rgba(0,212,255,0.15)',
    electricBright: 'rgba(0,212,255,0.60)',
    electricGlow:   'rgba(0,212,255,0.30)',

    // Accent palette
    indigo:     '#6366F1',  indigoDim:  'rgba(99,102,241,0.15)',
    emerald:    '#00E676',  emeraldDim: 'rgba(0,230,118,0.15)',
    amber:      '#FFB300',  amberDim:   'rgba(255,179,0,0.15)',
    rose:       '#FF3366',  roseDim:    'rgba(255,51,102,0.15)',
    violet:     '#A855F7',  violetDim:  'rgba(168,85,247,0.15)',

    // Surfaces (dark hierarchy)
    surface0: '#020408',   // deepest — page backdrop
    surface1: '#0A0F1A',   // page background
    surface2: '#0F1826',   // card background
    surface3: '#162035',   // elevated panel
    surface4: '#1E2C4A',   // hover / active
    surface5: '#253552',   // pressed

    // Text
    text1: '#F0F6FF',       // primary
    text2: '#A0B4CC',       // secondary
    text3: '#5A7494',       // muted
    textElectric: '#00D4FF',

    // Semantic
    success: '#00E676',
    warning: '#FFB300',
    error:   '#FF3366',
    info:    '#00D4FF',
  },

  glass: {
    subtle:    'rgba(255,255,255,0.03)',
    light:     'rgba(255,255,255,0.06)',
    medium:    'rgba(255,255,255,0.10)',
    strong:    'rgba(255,255,255,0.16)',
    blur:      'blur(20px)',
    blurHeavy: 'blur(40px)',
  },

  border: {
    subtle:  '1px solid rgba(255,255,255,0.04)',
    glass:   '1px solid rgba(255,255,255,0.08)',
    glow:    '1px solid rgba(0,212,255,0.25)',
    active:  '1px solid rgba(0,212,255,0.60)',
    error:   '1px solid rgba(255,51,102,0.50)',
    success: '1px solid rgba(0,230,118,0.40)',
  },

  shadow: {
    card:       '0 4px 24px rgba(0,0,0,0.40)',
    float:      '0 8px 40px rgba(0,0,0,0.60)',
    glow:       '0 0 20px rgba(0,212,255,0.15)',
    glowStrong: '0 0 40px rgba(0,212,255,0.35)',
    glowPulse:  '0 0 60px rgba(0,212,255,0.20)',
    error:      '0 0 20px rgba(255,51,102,0.20)',
    success:    '0 0 20px rgba(0,230,118,0.20)',
  },

  font: {
    sans: '"Inter", -apple-system, sans-serif',
    mono: '"JetBrains Mono", "Fira Code", monospace',
  },
} as const;

// Tailwind class helpers (use these instead of arbitrary values)
export const tw = {
  card:         'bg-[#0F1826] border border-white/[0.08] rounded-xl',
  cardHover:    'hover:border-[rgba(0,212,255,0.25)] hover:shadow-[0_0_40px_rgba(0,212,255,0.35)] hover:-translate-y-0.5',
  glass:        'bg-white/[0.06] backdrop-blur-xl border border-white/[0.08]',
  glassStrong:  'bg-white/[0.10] backdrop-blur-xl border border-white/[0.12]',
  electric:     'text-[#00D4FF]',
  electricBg:   'bg-[rgba(0,212,255,0.15)]',
  electricBorder: 'border-[rgba(0,212,255,0.60)]',
  surface1:     'bg-[#0A0F1A]',
  surface2:     'bg-[#0F1826]',
  surface3:     'bg-[#162035]',
  surface4:     'bg-[#1E2C4A]',
} as const;
```

### Step 2: `src/lib/design/motion.ts`

```typescript
// src/lib/design/motion.ts
// All animation variants + spring presets.
// Re-exports springs from JARVISPageShell for backward compatibility.

import type { Variants } from 'framer-motion';

// ── Spring presets ───────────────────────────────────────────────────────────
// Re-exported from JARVISPageShell (same values, single truth)
export const springs = {
  page:    { type: 'spring', stiffness: 280, damping: 26 } as const,  // page entry
  panel:   { type: 'spring', stiffness: 300, damping: 28 } as const,  // panels, drawers
  fast:    { type: 'spring', stiffness: 600, damping: 35 } as const,  // button presses
  slow:    { type: 'spring', stiffness: 200, damping: 25 } as const,  // slow reveals
  bouncy:  { type: 'spring', stiffness: 450, damping: 18 } as const,  // notifications, badges
  gentle:  { type: 'spring', stiffness: 180, damping: 24 } as const,  // hover lifts
} as const;

// ── Page-level ───────────────────────────────────────────────────────────────
// Wrap page root in <JARVISPageShell> instead — this variant is for nested pages
export const pageVariants: Variants = {
  hidden:  { opacity: 0, y: 16, filter: 'blur(4px)' },
  visible: { opacity: 1, y: 0,  filter: 'blur(0px)',
             transition: { ...springs.page, staggerChildren: 0.05 } },
  exit:    { opacity: 0, y: -8, filter: 'blur(2px)',
             transition: { duration: 0.15 } },
};

// ── Cards ────────────────────────────────────────────────────────────────────
export const cardVariants: Variants = {
  hidden:  { opacity: 0, y: 20, scale: 0.97 },
  visible: { opacity: 1, y: 0,  scale: 1, transition: springs.page },
  hover:   { y: -3, scale: 1.01, transition: springs.gentle },
  tap:     { scale: 0.98, transition: springs.fast },
};

// ── Lists ────────────────────────────────────────────────────────────────────
export const listContainerVariants: Variants = {
  hidden:  { opacity: 0 },
  visible: { opacity: 1,
             transition: { staggerChildren: 0.04, delayChildren: 0.05 } },
};
export const listItemVariants: Variants = {
  hidden:  { opacity: 0, x: -16 },
  visible: { opacity: 1, x: 0, transition: springs.page },
  exit:    { opacity: 0, x: 16, transition: { duration: 0.12 } },
};

// ── Status orb pulse ─────────────────────────────────────────────────────────
export const pulseVariants: Variants = {
  idle:    { scale: 1,    opacity: 1 },
  pulse:   { scale: [1, 1.5, 1], opacity: [1, 0.3, 1],
             transition: { duration: 1.8, repeat: Infinity, ease: 'easeInOut' } },
  offline: { scale: 1,    opacity: 0.3 },
};

// ── Panels & modals ──────────────────────────────────────────────────────────
export const panelVariants: Variants = {
  hidden:  { opacity: 0, x: 24 },
  visible: { opacity: 1, x: 0, transition: springs.panel },
  exit:    { opacity: 0, x: 24, transition: { duration: 0.15 } },
};
export const backdropVariants: Variants = {
  hidden:  { opacity: 0 },
  visible: { opacity: 1, transition: { duration: 0.2 } },
  exit:    { opacity: 0, transition: { duration: 0.15 } },
};
export const modalVariants: Variants = {
  hidden:  { opacity: 0, scale: 0.93, y: 16 },
  visible: { opacity: 1, scale: 1,    y: 0, transition: springs.page },
  exit:    { opacity: 0, scale: 0.95, y: 8, transition: { duration: 0.15 } },
};
export const drawerVariants: Variants = {
  hidden:  { y: '100%' },
  visible: { y: 0, transition: { ...springs.panel, delay: 0.05 } },
  exit:    { y: '100%', transition: { duration: 0.2 } },
};

// ── Counters & toasts ────────────────────────────────────────────────────────
export const counterVariants: Variants = {
  initial: { y: 12,  opacity: 0 },
  animate: { y: 0,   opacity: 1, transition: springs.fast },
  exit:    { y: -12, opacity: 0, transition: { duration: 0.1 } },
};
export const toastVariants: Variants = {
  hidden:  { opacity: 0, y: 40, scale: 0.90 },
  visible: { opacity: 1, y: 0,  scale: 1, transition: springs.bouncy },
  exit:    { opacity: 0, y: 20, scale: 0.95, transition: { duration: 0.18 } },
};

// ── Skeleton shimmer CSS class ────────────────────────────────────────────────
// Apply as className to skeleton placeholder elements
export const SKELETON_CLASS =
  'animate-pulse rounded bg-[#162035] relative overflow-hidden ' +
  'before:absolute before:inset-0 before:bg-gradient-to-r ' +
  'before:from-transparent before:via-white/[0.05] before:to-transparent ' +
  'before:animate-shimmer';
```

### Step 3: `src/components/ui/StatusOrb.tsx`

```typescript
// src/components/ui/StatusOrb.tsx
// Animated status indicator — used on ALL pages that show live state.
// Automatically pulses when status is 'running' or 'pending'.

import { motion } from 'framer-motion';
import { useReducedMotion } from 'framer-motion';

export type OrbStatus =
  | 'running' | 'executing'        // electric pulse
  | 'completed' | 'complete'        // emerald static
  | 'failed' | 'error'              // rose static
  | 'pending' | 'planning'          // amber pulse
  | 'idle' | 'waiting'              // text3 static
  | 'offline'                       // dark static
  | string;                         // fallback

const ORB_COLORS: Record<string, string> = {
  running:   '#00D4FF', executing:  '#00D4FF',
  completed: '#00E676', complete:   '#00E676',
  failed:    '#FF3366', error:      '#FF3366',
  pending:   '#FFB300', planning:   '#FFB300', waiting_human: '#FFB300',
  idle:      '#5A7494', waiting:    '#5A7494',
  offline:   '#2A3A52',
};

const ACTIVE_STATUSES = new Set([
  'running', 'executing', 'pending', 'planning', 'waiting_human',
]);

interface StatusOrbProps {
  status: OrbStatus;
  /** Diameter in px. Default: 8 */
  size?: number;
  className?: string;
}

export function StatusOrb({ status, size = 8, className = '' }: StatusOrbProps) {
  const reduce = useReducedMotion();
  const color  = ORB_COLORS[status] ?? '#5A7494';
  const active = ACTIVE_STATUSES.has(status);

  return (
    <span
      className={`relative inline-flex shrink-0 ${className}`}
      style={{ width: size, height: size }}
      aria-label={`Status: ${status}`}
      role="img"
    >
      {/* Pulse ring — only when active and motion is safe */}
      {active && !reduce && (
        <motion.span
          className="absolute inset-0 rounded-full"
          style={{ backgroundColor: color }}
          animate={{ scale: [1, 1.9, 1], opacity: [0.6, 0, 0.6] }}
          transition={{ duration: 1.8, repeat: Infinity, ease: 'easeInOut' }}
        />
      )}
      {/* Core dot */}
      <span
        className="relative rounded-full inline-block"
        style={{ width: size, height: size, backgroundColor: color }}
      />
    </span>
  );
}
```

### Step 4: Upgrade `src/components/ui/EmptyState.tsx`

**Current state:** Plain div, no motion, no Jarvis tokens.  
**Target:** Animated icon float, stagger text + CTA, 3 visual variants.

```typescript
// src/components/ui/EmptyState.tsx — UPGRADED
// Replaces the static version with animated Jarvis-style empty states.

import type { ReactNode } from 'react';
import { motion, useReducedMotion } from 'framer-motion';
import { springs } from '@/lib/design/motion';

export type EmptyStateVariant = 'float' | 'pulse' | 'orbit';

interface EmptyStateProps {
  /** Icon or SVG element */
  icon?: ReactNode;
  title: string;
  description?: string;
  action?: ReactNode;
  /** Animation variant for the icon. Default: 'float' */
  variant?: EmptyStateVariant;
  className?: string;
}

const FLOAT_ANIM = {
  animate: { y: [0, -8, 0] },
  transition: { duration: 3, repeat: Infinity, ease: 'easeInOut' },
};
const PULSE_ANIM = {
  animate: { scale: [1, 1.08, 1] },
  transition: { duration: 2, repeat: Infinity, ease: 'easeInOut' },
};
const ORBIT_ANIM = {
  animate: { rotate: 360 },
  transition: { duration: 8, repeat: Infinity, ease: 'linear' },
};

export function EmptyState({
  icon,
  title,
  description,
  action,
  variant = 'float',
  className = '',
}: EmptyStateProps) {
  const reduce = useReducedMotion();

  const iconAnim =
    reduce ? {} :
    variant === 'float' ? FLOAT_ANIM :
    variant === 'pulse' ? PULSE_ANIM : ORBIT_ANIM;

  const containerVariants = {
    hidden:  { opacity: 0 },
    visible: {
      opacity: 1,
      transition: reduce ? {} : { staggerChildren: 0.1, delayChildren: 0.1 },
    },
  };
  const itemVariants = {
    hidden:  { opacity: 0, y: 8 },
    visible: { opacity: 1, y: 0, transition: springs.page },
  };

  return (
    <motion.div
      variants={containerVariants}
      initial="hidden"
      animate="visible"
      className={`flex flex-col items-center justify-center py-16 px-6 text-center gap-3 ${className}`}
    >
      {icon && (
        <motion.div
          {...iconAnim}
          className="text-[#00D4FF] opacity-40 mb-1"
        >
          {icon}
        </motion.div>
      )}
      <motion.p variants={itemVariants} className="text-sm font-medium text-[#F0F6FF]">
        {title}
      </motion.p>
      {description && (
        <motion.p variants={itemVariants} className="text-xs text-[#5A7494] max-w-xs">
          {description}
        </motion.p>
      )}
      {action && (
        <motion.div variants={itemVariants} className="mt-1">
          {action}
        </motion.div>
      )}
    </motion.div>
  );
}
```

---

## Phase 1 — Quick Wins: 20 JARVISPageShell-Only Pages

These pages already have the page-entrance animation from `<JARVISPageShell>`.  
The work is: wrap their **card grids** in `<JARVISStagger>` and **each card/row** in `<JARVISStaggerItem>`.

**Estimated effort:** ~30 min per page.  
**Visual impact:** Immediate — every list and grid will stagger in.

### Pages in This Phase

```
features/agents/AgentDashboardPage.tsx
features/agents/AgentDetailPage.tsx
features/agents/AgentsListPage.tsx
features/analytics/AnalyticsDashboardPage.tsx
features/approvals/ApprovalsPage.tsx
features/collaboration/CollaborationPage.tsx
features/dashboard/DashboardPage.tsx
features/eval/EvalPage.tsx
features/goals/GoalDetailPage.tsx
features/goals/GoalsListPage.tsx
features/knowledge/KnowledgePage.tsx
features/marketplace/MarketplacePage.tsx
features/memory/MemoryExplorerPage.tsx
features/notifications/NotificationCenterPage.tsx
features/observability/CostDashboardPage.tsx
features/observability/ObservabilityPage.tsx
features/schedules/SchedulesPage.tsx
features/settings/BillingPage.tsx
features/settings/SettingsPage.tsx
features/tools/ToolsPage.tsx
```

### Pattern to Apply (same for all 20)

```tsx
// BEFORE:
<div className="grid grid-cols-3 gap-4">
  {items.map(item => <ItemCard key={item.id} item={item} />)}
</div>

// AFTER:
import { JARVISStagger, JARVISStaggerItem } from '@/components/ui/JARVISPageShell';
import { StatusOrb } from '@/components/ui/StatusOrb';

<JARVISStagger className="grid grid-cols-3 gap-4">
  {items.map(item => (
    <JARVISStaggerItem key={item.id}>
      <ItemCard item={item} />
    </JARVISStaggerItem>
  ))}
</JARVISStagger>

// For status orbs in those cards (replace inline colored dots):
// BEFORE: <span className="w-2 h-2 rounded-full bg-green-400" />
// AFTER:  <StatusOrb status={item.status} size={8} />
```

### Page-Specific Notes

**DashboardPage:**
- Wrap KPI stat cards in `<JARVISStagger staggerMs={80}>`
- Wrap activity feed items in `<JARVISStagger staggerMs={40}>`
- Wrap agent grid in `<JARVISStagger staggerMs={60}>`

**GoalsListPage:**
- Wrap goal rows in `<JARVISStagger staggerMs={40}>`
- Replace inline status dots with `<StatusOrb status={goal.status} />`

**AgentsListPage:**
- Wrap agent cards in `<JARVISStagger staggerMs={60}>`
- Replace inline status indicators with `<StatusOrb status={agent.status} />`

**ApprovalsPage:**
- Wrap approval cards in `<JARVISStagger staggerMs={60}>`
- Urgent cards: add `className="ring-1 ring-[#FF3366]/30"` on stagger item

**MarketplacePage:**
- Wrap template cards in `<JARVISStagger staggerMs={50}>`

**NotificationCenterPage:**
- Wrap notification items in `<JARVISStagger staggerMs={35}>`

---

## Phase 2 — 54 Zero-Animation Pages

Priority order: highest user-traffic pages first.

### Priority Tier 1 — Core User Journey (implement first)

#### `features/auth/AuthPage.tsx`

```
What to add:
1. Wrap root in <JARVISPageShell> (page entrance blur+y)
2. Wrap form card in <motion.div variants={cardVariants}>
3. Plan badge reveal: AnimatePresence + y:-8→0 on API key validated
4. Error state: x shake animation [-8,8,-8,8,0] 300ms
5. Submit button: <JARVISButton type="submit">

Key motion elements:
- Logo: scale pulse 1→1.05→1, 2s loop (springs.gentle)
- Input focus: CSS transition border-color to #00D4FF (Tailwind focus:border-[#00D4FF])
- Plan badge: motion.div y:-8→0 springs.bouncy after API key blur
- Wrong key: form shake: animate={{ x: [0,-8,8,-8,8,0] }}
```

#### `features/auth/MFAVerifyPage.tsx`

```
What to add:
1. Wrap in <JARVISPageShell>
2. Shield icon: scale 0→1 springs.bouncy on mount
3. Digit inputs: auto-advance with electric focus ring (CSS)
4. Wrong code: all inputs shake + rose border
5. Success: ✅ scale 0→1.3→1 springs.bouncy + emerald glow
```

#### `features/goals/GoalsListPage.tsx` (already has JARVISPageShell)

```
Inner elements to animate:
1. Goal input bar focus: CSS border transition to electric
2. Status orbs: replace with <StatusOrb status={goal.status} />
3. Goal rows: already wrapped — ensure JARVISStaggerItem applied
4. Running goal progress bar: motion.div width spring animation
```

#### `features/goals/GoalDetailPage.tsx` (already has JARVISPageShell)

```
Inner elements to animate:
1. Plan step tracker: each step uses JARVISStaggerItem with 0.08s stagger
2. ✅ completed step: scale 0→1.2→1 springs.bouncy + emerald background flash
3. 🔄 active step: pulseVariants.pulse ring animation
4. Step accordion expand: AnimatePresence + height auto springs.page
5. LangSmith link hover: scale 1.05 springs.fast
```

### Priority Tier 1 — Remaining Core Pages

**Pattern for all:** Same 4-step approach:
1. Wrap root in `<JARVISPageShell>` (or confirm it already is)
2. Wrap all lists/grids in `<JARVISStagger>`
3. Wrap list items in `<JARVISStaggerItem>`
4. Replace status dots with `<StatusOrb>`

Apply to these in order:

```
features/agents/AgentCreatePage.tsx     — NL/manual tab AnimatePresence
features/agents/AgentDetailPage.tsx     — already has shell, add inner stagger
features/agents/AgentRadarPage.tsx      — D3 radar: axes stagger out springs.slow
features/agents/AgentIdentityPage.tsx   — avatar drop zone ring animation
features/agents/AgentPersonalityPage.tsx — slider spring thumb (whileDrag)
features/audit/AuditExplorerPage.tsx    — audit rows stagger, rose for errors
features/governance/GovernancePage.tsx  — timeline stagger, hash verify animation
features/ingestion/SourcesPage.tsx      — drop zone conic border, queue stagger
features/knowledge/KnowledgePage.tsx    — already has shell, add card stagger
features/connectors/ConnectorsCatalogPage.tsx — category filter AnimatePresence
features/connectors/ConnectorsRegisteredPage.tsx — health orb StatusOrb
features/connectors/ConnectorDetailPage.tsx — tool rows stagger
features/channels/ChannelMappingsPage.tsx — channel card stagger
features/compliance/CompliancePage.tsx  — score arc springs.slow
features/domains/DomainsPage.tsx        — tree expand spring height
features/domains/DomainDetailPage.tsx   — tab content y:8→0
features/enterprise/EnterprisePage.tsx  — feature card stagger
features/eval/EvalPage.tsx              — already has shell, add score bars
features/gateway/GatewaySettingsPage.tsx — already has motion, add shell
features/settings/BudgetManagerPage.tsx  — gauge fill spring
features/settings/GuardrailCenterPage.tsx — rule card stagger
features/settings/RoleEditorPage.tsx     — already has motion, add shell
features/settings/ScopeExplorerPage.tsx  — scope tree spring height
features/simulation/SimulationPage.tsx  — simulation banner animation
features/skills/SkillsPage.tsx          — skill card stagger
features/status/StatusPage.tsx          — uptime bars stagger fill
features/templates/TemplateLibraryPage.tsx — template card stagger
features/tools/ToolsPage.tsx            — already has shell, add stagger
features/training/TrainingExportPage.tsx — progress bar spring fill
features/triggers/TriggersPage.tsx      — trigger rows stagger
```

### Priority Tier 2 — Secondary Pages

```
features/a2a/A2APage.tsx
features/admin/AdminPage.tsx
features/analytics/SelfImprovementPage.tsx
features/artifacts/ArtifactsBrowserPage.tsx
features/auth/SSOCallbackPage.tsx
features/builder/BuilderPage.tsx
features/chat/ChatPage.tsx
features/chat/AgentMemoryPage.tsx
features/civilization/CivilizationPage.tsx
features/collaboration/CollaborationPage.tsx   — already has shell
features/connectors/OAuthCallbackPage.tsx
features/coordination/CoordinationRunPage.tsx
features/errors/NotFoundPage.tsx
features/goals/GhostRunPage.tsx
features/goals/GoalDiffPage.tsx
features/goals/GoalDNAPage.tsx
features/integrations/IntegrationsPage.tsx
features/knowledge-graph/GraphExplorerPage.tsx
features/lab/AgentLabPage.tsx
features/landing/LandingPage.tsx
features/marketplace/MarketplacePage.tsx    — already has shell
features/memory/MemoryExplorerPage.tsx      — already has shell
features/notifications/NotificationCenterPage.tsx — already has shell
features/observability/CostDashboardPage.tsx — already has shell
features/observability/ObservabilityPage.tsx — already has shell
features/ocr/OcrPage.tsx
features/onboarding/OnboardingPage.tsx
features/org/OrgListPage.tsx                — already has motion
features/org/OrgPage.tsx                    — already has motion
features/org/StrategicAdvisorPage.tsx       — already has motion
features/perception/PerceptionPage.tsx
features/playground/PlaygroundPage.tsx
features/rbac/RbacPage.tsx
features/rpa/RpaLivePage.tsx
features/schedules/SchedulesPage.tsx        — already has shell
features/security/SecurityCenterPage.tsx
features/settings/BillingPage.tsx          — already has shell
features/settings/SettingsPage.tsx         — already has shell
features/state-machines/StateMachinesPage.tsx
features/workflow-builder/WorkflowBuilderPage.tsx
```

### Priority Tier 3 — Workflow Sub-Pages (partial motion exists, standardize)

```
features/workflow/ApprovalInboxPage.tsx     — already has motion, add shell
features/workflow/WorkflowAnalyticsPage.tsx — already has motion, add shell
features/workflow/WorkflowBuilderPage.tsx   — already has motion, add shell
features/workflow/WorkflowListPage.tsx      — already has motion, add shell
features/workflow/WorkflowMarketplacePage.tsx — already has motion, add shell
features/workflow/WorkflowRunDetailPage.tsx — already has motion, add shell
features/workflow/WorkflowRunsPage.tsx      — already has motion, add shell
features/workflow/WorkflowSettingsPage.tsx  — already has motion, add shell
```

For all Tier 3 pages: wrap in `<JARVISPageShell>` and replace `duration`-based transitions with spring equivalents.

---

## Phase 3 — Polish Pass

After Phase 2, apply these cross-cutting polish items:

### 3.1 Hover States on All Interactive Cards

Every card that is clickable/navigable needs:
```tsx
// Add to the card's motion.div or JARVISStaggerItem wrapper:
whileHover={{ y: -3, transition: springs.gentle }}
whileTap={{ scale: 0.98, transition: springs.fast }}
// And these Tailwind classes:
className="... transition-shadow hover:shadow-[0_0_40px_rgba(0,212,255,0.35)]
           hover:border-[rgba(0,212,255,0.25)] cursor-pointer"
```

### 3.2 Empty States — Replace Static with Animated

Every page that currently shows `<EmptyState>` benefits from:
```tsx
// BEFORE (static):
<EmptyState title="No goals yet" />

// AFTER (animated, with icon):
import { Target } from 'lucide-react';
<EmptyState
  icon={<Target size={40} />}
  title="No goals yet"
  description="Define a goal and deploy your agents"
  variant="float"
  action={<button>Create First Goal</button>}
/>
```

### 3.3 Replace All Inline Status Dots

Search pattern: `className="w-2 h-2 rounded-full bg-green` (and similar)  
Replace with: `<StatusOrb status={...} size={8} />`

### 3.4 Fix Duration-Based Transitions to Springs

Search: `transition={{ duration:` in any feature page  
Replace: use spring equivalents from `springs` — never `duration`/`ease` for UI motion.

```tsx
// WRONG:
transition={{ duration: 0.3, ease: 'easeOut' }}

// RIGHT:
transition={springs.page}       // for enters/exits
transition={springs.fast}       // for button presses
transition={springs.bouncy}     // for notifications/badges
transition={springs.gentle}     // for hover lifts
```

### 3.5 Loading Skeleton Upgrade

Every page that shows loading state should use animated skeletons:
```tsx
// BEFORE:
<div className="h-8 bg-gray-200 rounded animate-pulse" />

// AFTER:
import { Skeleton } from '@/components/ui/Skeleton';
<Skeleton className="h-8 w-full" />
// (Skeleton.tsx already exists — ensure it uses SKELETON_CLASS from motion.ts)
```

---

## Accessibility Requirements (All Phases)

Per WCAG 2.2 AA — apply alongside every animation:

1. **`useReducedMotion` compliance:** All motion already handled by `JARVISPageShell` and the `springs` approach. `JARVISStagger` already checks `useReducedMotion()`.

2. **`aria-label` on icon-only buttons:**
   ```tsx
   // Every icon-only button needs aria-label:
   <JARVISButton aria-label="Cancel goal">
     <X size={16} />
   </JARVISButton>
   ```

3. **`aria-live` for dynamic content:**
   ```tsx
   // SSE-driven content needs aria-live:
   <div aria-live="polite" aria-atomic="true">
     {streamingOutput}
   </div>
   ```

4. **Focus management on modals:** Use `autoFocus` on first interactive element inside `motion.div` modal wrappers.

5. **Touch targets:** Minimum 44×44px on mobile for all interactive elements.

---

## TypeScript Requirements

All new files must:
- Use strict TypeScript (`"strict": true` already in tsconfig)
- No `any` types — use `unknown` and narrow
- Export named types alongside components
- Use `as const` for config objects (enables literal type inference)

---

## Testing Requirements

Per project TDD mandate — write tests BEFORE implementation:

```
For each Phase 0 file:
  - tokens.ts: verify all values are strings (snapshot test)
  - motion.ts: verify spring values match expected (snapshot)
  - StatusOrb.tsx: renders for all 8+ status values, pulse when active
  - EmptyState.tsx: renders, icon floats when motion safe, static when reduced

For each Phase 1 page:
  - Renders with stagger (smoke test)
  - Stagger items visible after animation
  - StatusOrb present for status fields

For each Phase 2/3 page:
  - Renders without crashing (smoke)
  - JARVISPageShell wrapping present
  - No duration-based transitions remain
```

---

## Completion Checklist

### Phase 0 — Foundation ❌ (not started)
- [ ] `src/lib/design/tokens.ts` created
- [ ] `src/lib/design/motion.ts` created
- [ ] `src/components/ui/StatusOrb.tsx` created
- [ ] `src/components/ui/EmptyState.tsx` upgraded with animation
- [ ] All 4 files have tests
- [ ] TypeScript: 0 new errors

### Phase 1 — 20 Shell-Only Pages ❌ (not started)
- [ ] DashboardPage: KPI stagger + activity stagger + agent grid stagger
- [ ] GoalsListPage: row stagger + StatusOrb
- [ ] AgentsListPage: card stagger + StatusOrb
- [ ] ApprovalsPage: card stagger + urgent rose ring
- [ ] CollaborationPage: stagger
- [ ] EvalPage: stagger
- [ ] KnowledgePage: collection card stagger
- [ ] MarketplacePage: template card stagger
- [ ] MemoryExplorerPage: memory card stagger
- [ ] NotificationCenterPage: notification stagger
- [ ] CostDashboardPage: stagger
- [ ] ObservabilityPage: trace row stagger
- [ ] SchedulesPage: schedule row stagger
- [ ] BillingPage: invoice row stagger
- [ ] SettingsPage: settings section stagger
- [ ] ToolsPage: tool row stagger
- [ ] AgentDashboardPage: chart stagger
- [ ] AgentDetailPage: tab content stagger
- [ ] AnalyticsDashboardPage: chart stagger
- [ ] GoalDetailPage: step stagger + accordion

### Phase 2 — 54 Zero-Animation Pages ❌ (not started)
- [ ] Tier 1: ~30 priority pages (auth, agents sub-pages, audit, governance, ingestion, connectors, channels, compliance, domains, enterprise, settings sub-pages, simulation, skills, status, templates, training, triggers)
- [ ] Tier 2: ~20 secondary pages
- [ ] Tier 3: 8 workflow sub-pages (standardize existing motion)

### Phase 3 — Polish ❌ (not started)
- [ ] Hover states on all clickable cards
- [ ] All empty states upgraded
- [ ] All inline status dots → StatusOrb
- [ ] All duration-based transitions → springs
- [ ] Loading skeletons animated

### Final Verification
- [ ] 0 pages with `score < 2` in Jarvis audit script
- [ ] TypeScript: 0 errors
- [ ] Lighthouse Accessibility ≥ 95 on core pages
- [ ] `prefers-reduced-motion` tested manually
- [ ] `npm run test` passes

---

## Page-by-Page Animation Detail

### dashboard — `DashboardPage.tsx`
**Current:** JARVISPageShell only  
**Target animation:**
- KPI cards: `JARVISStagger staggerMs={80}` + `JARVISStaggerItem`
- Activity feed (SSE): new item enters top (y:-16→0, springs.bouncy)
- Agent grid: `JARVISStagger staggerMs={60}`, orbs → `<StatusOrb>`
- Quick goal bar focus: CSS `focus:border-[#00D4FF] focus:shadow-[0_0_20px_rgba(0,212,255,0.15)]`

### goals/GoalsListPage — `GoalsListPage.tsx`
**Current:** JARVISPageShell only  
**Target:**
- Goal rows: `JARVISStagger staggerMs={40}` + `JARVISStaggerItem`
- Status column: `<StatusOrb status={goal.status} />`
- Running goal: progress bar `motion.div` width from `0%` to `${pct}%` with springs.page

### goals/GoalDetailPage — `GoalDetailPage.tsx`
**Current:** JARVISPageShell only  
**Target:**
- Plan steps: `JARVISStagger staggerMs={80}` on step list
- Completed step: `motion.div` scale 0→1.2→1 springs.bouncy + emerald flash (background-color animation)
- Active step: `<StatusOrb status="running" size={10} />` + pulsing ring
- Step accordion: `AnimatePresence` + `motion.div` height `0→auto`

### agents/AgentsListPage — `AgentsListPage.tsx`
**Current:** JARVISPageShell only  
**Target:**
- Card grid: `JARVISStagger staggerMs={60}` + `JARVISStaggerItem`
- Status orb: `<StatusOrb status={agent.status} size={10} />`
- Card hover: `whileHover={{ y: -3 }}` + CSS shadow transition

### agents/AgentDetailPage — `AgentDetailPage.tsx`
**Current:** JARVISPageShell only  
**Target:**
- Tab content: `AnimatePresence mode="wait"` + `motion.div` y:8→0 per tab
- Readiness score: `motion.div` arc (SVG strokeDashoffset) from 0 to score
- Tool list: `JARVISStagger staggerMs={30}` + `JARVISStaggerItem`

### agents/AgentDashboardPage — `AgentDashboardPage.tsx`
**Current:** JARVISPageShell only  
**Target:**
- KPI strip: `JARVISStagger staggerMs={80}`
- Charts: D3 path draw-in via CSS stroke-dashoffset animation

### agents/AgentRadarPage — `AgentRadarPage.tsx`
**Current:** Zero animation  
**Target:**
- Wrap in `<JARVISPageShell>`
- D3 `ThemedRadarChart`: axes animate outward (JS-driven stagger in D3)
- Score bars below: `JARVISStagger` + fill animation via `motion.div` width

### agents/AgentCreatePage — `AgentCreatePage.tsx`
**Current:** Zero animation  
**Target:**
- Wrap in `<JARVISPageShell>`
- Tab switch: `AnimatePresence` x:±30→0 springs.page
- Submit button: `<JARVISButton type="submit">`
- Success navigate: brief emerald flash before navigate

### agents/AgentIdentityPage — `AgentIdentityPage.tsx`
**Current:** Zero animation  
**Target:**
- Wrap in `<JARVISPageShell>`
- Avatar drop zone: `whileHover={{ scale: 1.02 }}` + CSS conic border via Tailwind
- Drag active: `animate={{ scale: 1.04, borderColor: '#00D4FF' }}`
- Save button: `<JARVISButton>`

### agents/AgentPersonalityPage — `AgentPersonalityPage.tsx`
**Current:** Zero animation  
**Target:**
- Wrap in `<JARVISPageShell>`
- Sliders: `whileDrag` spring physics thumb (custom slider or range input CSS)
- Pill selection: selected pill `animate={{ scale: 1.05 }}` springs.fast
- Save button: `<JARVISButton>`

### analytics/AnalyticsDashboardPage — `AnalyticsDashboardPage.tsx`
**Current:** JARVISPageShell only  
**Target:**
- KPI row: `JARVISStagger staggerMs={80}`
- Chart cards: `JARVISStagger staggerMs={100}` + `JARVISStaggerItem`

### analytics/SelfImprovementPage — `SelfImprovementPage.tsx`
**Current:** Zero animation  
**Target:**
- Wrap in `<JARVISPageShell>`
- Prompt version nodes: `JARVISStagger` horizontal
- A/B winner badge: scale 0→1.2→1 springs.bouncy + emerald flash

### approvals/ApprovalsPage — `ApprovalsPage.tsx`
**Current:** JARVISPageShell only  
**Target:**
- Pending cards: `JARVISStagger staggerMs={60}`
- Urgent card: `className="ring-1 ring-[#FF3366]/30"` + amber pulse badge
- Approve action: `AnimatePresence` exit x:40 emerald flash
- Reject action: exit x:-40 rose flash

### artifacts/ArtifactsBrowserPage — `ArtifactsBrowserPage.tsx`
**Current:** Zero animation  
**Target:**
- Wrap in `<JARVISPageShell>`
- Grid: `JARVISStagger staggerMs={40}`
- Thumbnail: `initial={{ filter: 'blur(8px)' }} animate={{ filter: 'blur(0px)' }}`

### audit/AuditExplorerPage — `AuditExplorerPage.tsx`
**Current:** Zero animation  
**Target:**
- Wrap in `<JARVISPageShell>`
- Timeline rows: `JARVISStagger staggerMs={35}`
- Security event rows: `className="border-l-2 border-[#FF3366]/50"`
- New SSE event: `JARVISStaggerItem` entry from top

### auth/AuthPage — (see Phase 2 section above)
### auth/MFAVerifyPage — (see Phase 2 section above)
### auth/SSOCallbackPage
**Target:** Wrap in `<JARVISPageShell>`. Electric spinner. Status typewriter text.

### builder/BuilderPage — `BuilderPage.tsx`
**Current:** Zero animation  
**Target:**
- Wrap in `<JARVISPageShell>`
- Node spring physics on drag release (handled by @xyflow/react built-in)
- Selected node: `animate={{ boxShadow: '0 0 0 2px #00D4FF' }}`

### channels/ChannelMappingsPage — `ChannelMappingsPage.tsx`
**Current:** Zero animation  
**Target:**
- Wrap in `<JARVISPageShell>`
- Channel cards: `JARVISStagger staggerMs={60}` + `JARVISStaggerItem`
- Connected status: `<StatusOrb status="running" />` (using running for connected)

### chat/ChatPage — `ChatPage.tsx`
**Current:** Zero animation  
**Target:**
- Wrap in `<JARVISPageShell>`
- Messages: `JARVISStaggerItem` per message
- User msg: `initial={{ x: 16 }}` animate to 0 springs.page
- AI msg: `initial={{ x: -16 }}` animate to 0 springs.page
- Typing indicator: 3-dot stagger scale 0.5→1→0.5

### chat/AgentMemoryPage — `AgentMemoryPage.tsx`
**Current:** Zero animation  
**Target:**
- Wrap in `<JARVISPageShell>`
- Memory cards: `JARVISStagger staggerMs={40}` + virtualized
- Forget action: scale 0 + opacity 0 exit springs.fast + rose flash

### civilization/CivilizationPage — `CivilizationPage.tsx`
**Current:** Zero animation  
**Target:**
- Wrap in `<JARVISPageShell>`
- D3 globe: continuous rotation animation (JS-driven, slow 2rpm)
- Stats row: `JARVISStagger staggerMs={80}`

### collaboration/CollaborationPage — `CollaborationPage.tsx`
**Current:** JARVISPageShell only  
**Target:**
- Presence avatars: `JARVISStagger staggerMs={50}` + bouncy enter
- Remote cursor: `motion.div` spring-follow (update position with springs.gentle)

### compliance/CompliancePage — `CompliancePage.tsx`
**Current:** Zero animation  
**Target:**
- Wrap in `<JARVISPageShell>`
- Score arcs: SVG arc draw-in animation (strokeDashoffset 0→circumference×score)
- Score cards: `JARVISStagger staggerMs={80}`
- Gap rows: `JARVISStagger staggerMs={40}`

### connectors/ (all 4 pages)
**Target pattern:** `<JARVISPageShell>` + `JARVISStagger` on card/row grids + `<StatusOrb>` for health

### coordination/CoordinationRunPage — `CoordinationRunPage.tsx`
**Current:** Zero animation  
**Target:**
- Wrap in `<JARVISPageShell>`
- Agent graph: D3 force spring settle (JS-driven)
- Messages: `JARVISStaggerItem` per message, direction based on sender

### domains/ (2 pages)
**Target:** `<JARVISPageShell>` + tree spring height expand + new domain springs.bouncy

### enterprise/EnterprisePage — `EnterprisePage.tsx`
**Current:** Zero animation  
**Target:**
- Wrap in `<JARVISPageShell>`
- Feature cards: `JARVISStagger staggerMs={70}` + `JARVISStaggerItem`

### errors/NotFoundPage — `NotFoundPage.tsx`
**Current:** Zero animation  
**Target:**
- Wrap in `<JARVISPageShell>`
- 404 text: CSS glitch keyframe animation (translateX ±3px + opacity flicker)
- "Signal lost" text: typewriter 40ms/char after 300ms delay
- CTA buttons: `JARVISButton` with springs.bouncy enter (delay 0.4s)

### eval/EvalPage — `EvalPage.tsx`
**Current:** JARVISPageShell only  
**Target:**
- Run rows: `JARVISStagger staggerMs={40}`
- Score bars: `motion.div` width 0→score% springs.page
- Delta badges: `motion.span` counterVariants color (emerald/rose/amber)

### gateway/GatewaySettingsPage — `GatewaySettingsPage.tsx`
**Current:** motion.div present (score 3) — add JARVISPageShell  
**Target:** Wrap in `<JARVISPageShell>`. Route health: `<StatusOrb>`.

### goals/ sub-pages (GhostRunPage, GoalDiffPage, GoalDNAPage)
**Target:**
- All: wrap in `<JARVISPageShell>`
- GoalDiffPage: panes slide from sides simultaneously springs.page
- GoalDNAPage: D3 force knowledge graph spring settle
- GhostRunPage: scrubber thumb `whileDrag` spring physics

### governance/GovernancePage — `GovernancePage.tsx`
**Current:** Zero animation  
**Target:**
- Wrap in `<JARVISPageShell>`
- Timeline rows: `JARVISStagger staggerMs={35}`
- Hash verify: sequential chain verification animation (JS-driven)

### ingestion/SourcesPage — `SourcesPage.tsx`
**Current:** Zero animation  
**Target:**
- Wrap in `<JARVISPageShell>`
- Drop zone: `whileHover={{ scale: 1.01 }}` + CSS conic gradient border
- Queue items: `JARVISStagger staggerMs={50}`
- Progress bars: `motion.div` width spring animation

### integrations/IntegrationsPage — `IntegrationsPage.tsx`
**Current:** Zero animation  
**Target:** Same as ConnectorsCatalogPage

### knowledge-graph/GraphExplorerPage — `GraphExplorerPage.tsx`
**Current:** Zero animation  
**Target:**
- Wrap in `<JARVISPageShell>`
- D3/xyflow nodes: spring physics settle on mount (JS-driven)
- Selected node: `animate={{ scale: 1.2, filter: 'drop-shadow(0 0 8px #00D4FF)' }}`

### lab/AgentLabPage — `AgentLabPage.tsx`
**Current:** Zero animation  
**Target:**
- Wrap in `<JARVISPageShell>`
- Run button: `<JARVISButton>` + springs.fast press
- Compare pane: `AnimatePresence` + panelVariants from right

### landing/LandingPage — `LandingPage.tsx`
**Current:** Zero animation  
**Target:**
- Wrap in `<JARVISPageShell delay={0}>`
- Hero text: pageVariants with blur
- CTA buttons: `JARVISButton` springs.bouncy enter
- Feature sections: `IntersectionObserver` → trigger `JARVISStagger`

### marketplace/MarketplacePage — `MarketplacePage.tsx`
**Current:** JARVISPageShell only  
**Target:**
- Template cards: `JARVISStagger staggerMs={50}` + `JARVISStaggerItem`
- Category tab switch: `AnimatePresence` content fade

### memory/MemoryExplorerPage — `MemoryExplorerPage.tsx`
**Current:** JARVISPageShell only  
**Target:**
- Memory cards: `JARVISStagger staggerMs={40}` + virtualized list
- Tab switch: `AnimatePresence` + y:8→0
- D3 cluster: force spring settle

### notifications/NotificationCenterPage — `NotificationCenterPage.tsx`
**Current:** JARVISPageShell only  
**Target:**
- Notification items: `JARVISStagger staggerMs={35}`
- New SSE item: `initial={{ y: -16 }}` + springs.bouncy entry
- Mark read: opacity 1→0.6 transition

### observability/ (both pages)
**Current:** JARVISPageShell only  
**Target:**
- Trace rows: `JARVISStagger staggerMs={30}` + virtualized
- Duration bars: `motion.div` width 0→px springs.page
- CostDashboard gauges: fill springs.page

### ocr/OcrPage — `OcrPage.tsx`
**Current:** Zero animation  
**Target:**
- Wrap in `<JARVISPageShell>`
- Bounding boxes: scale 0.95→1 stagger springs.page
- Confidence bars: `motion.div` width spring animation

### onboarding/OnboardingPage — `OnboardingPage.tsx`
**Current:** Zero animation  
**Target:**
- Wrap in `<JARVISPageShell>`
- Step transition: `AnimatePresence` x:±40→0 springs.page
- Final step: confetti + `animate={{ scale: [1, 1.3, 1] }}` springs.bouncy

### org/ (3 pages)
**Current:** OrgPage + OrgListPage + StrategicAdvisorPage have motion (score 2-3)  
**Target:** Add `<JARVISPageShell>` to OrgListPage and StrategicAdvisorPage. Ensure no duration-based transitions.

### perception/PerceptionPage — `PerceptionPage.tsx`
**Current:** Zero animation  
**Target:**
- Wrap in `<JARVISPageShell>`
- Bounding boxes: stagger appearance springs.page
- Confidence bars: spring fill animation

### playground/PlaygroundPage — `PlaygroundPage.tsx`
**Current:** Zero animation  
**Target:** Same as AgentLabPage pattern

### rbac/RbacPage — `RbacPage.tsx`
**Current:** Zero animation  
**Target:**
- Wrap in `<JARVISPageShell>`
- Permission matrix: `JARVISStagger` rows + `JARVISStaggerItem` per row
- Toggle: scale flip springs.fast

### rpa/RpaLivePage — `RpaLivePage.tsx`
**Current:** Zero animation  
**Target:**
- Wrap in `<JARVISPageShell>`
- Step list: `JARVISStagger staggerMs={50}`
- Active step: `animate={{ borderLeft: '2px solid #00D4FF' }}`

### schedules/SchedulesPage — `SchedulesPage.tsx`
**Current:** JARVISPageShell only  
**Target:**
- Schedule rows: `JARVISStagger staggerMs={45}`
- Active toggle: `<StatusOrb>` transitions color springs.fast

### security/SecurityCenterPage — `SecurityCenterPage.tsx`
**Current:** Zero animation  
**Target:**
- Wrap in `<JARVISPageShell>`
- OWASP grid: `JARVISStagger staggerMs={50}` + `JARVISStaggerItem`
- Score arc: SVG draw-in animation
- ⚠ items: amber pulse ring after mount

### settings/ sub-pages
- **BillingPage** (has shell): Add gauge fill `motion.div` width springs.page
- **BudgetManagerPage**: Wrap + gauge animation + slider spring
- **GuardrailCenterPage**: Wrap + rule card stagger + toggle springs.fast
- **RoleEditorPage** (has motion): Add shell, ensure spring physics
- **ScopeExplorerPage**: Wrap + tree height springs.page

### simulation/SimulationPage — `SimulationPage.tsx`
**Current:** Zero animation  
**Target:**
- Wrap in `<JARVISPageShell>`
- SIMULATION banner: amber/violet gradient border + bouncy entry
- Simulated badge: violet springs.bouncy first appear

### skills/SkillsPage — `SkillsPage.tsx`
**Current:** Zero animation  
**Target:**
- Wrap in `<JARVISPageShell>`
- Skill cards: `JARVISStagger staggerMs={60}` + `JARVISStaggerItem`
- Enable orb: `<StatusOrb status="running" />` when enabled

### state-machines/StateMachinesPage — `StateMachinesPage.tsx`
**Current:** Zero animation  
**Target:**
- Wrap in `<JARVISPageShell>`
- @xyflow/react canvas: spring physics on node drag (built-in)
- State hover: `whileHover={{ scale: 1.05 }}`

### status/StatusPage — `StatusPage.tsx`
**Current:** Zero animation  
**Target:**
- Wrap in `<JARVISPageShell>`
- Component rows: `JARVISStagger staggerMs={40}`
- Status orbs: `<StatusOrb status={component.status} />`
- Uptime bars: `JARVISStagger staggerMs={8}` (one per day = fine-grained)

### templates/TemplateLibraryPage — `TemplateLibraryPage.tsx`
**Current:** Zero animation  
**Target:**
- Wrap in `<JARVISPageShell>`
- Template cards: `JARVISStagger staggerMs={50}` + `JARVISStaggerItem`

### tools/ToolsPage — `ToolsPage.tsx`
**Current:** JARVISPageShell only  
**Target:**
- Tool rows: `JARVISStagger staggerMs={30}` (virtualized)
- Risk badge: color springs.fast on hover

### training/TrainingExportPage — `TrainingExportPage.tsx`
**Current:** Zero animation  
**Target:**
- Wrap in `<JARVISPageShell>`
- Job cards: `JARVISStagger staggerMs={60}`
- Progress bar: `motion.div` width spring animation from 0 to `${pct}%`

### triggers/TriggersPage — `TriggersPage.tsx`
**Current:** Zero animation  
**Target:**
- Wrap in `<JARVISPageShell>`
- Trigger rows: `JARVISStagger staggerMs={45}`
- Active orb: `<StatusOrb status="running" />` when enabled

### workflow-builder/WorkflowBuilderPage — `WorkflowBuilderPage.tsx`
**Current:** Zero animation  
**Target:**
- Wrap in `<JARVISPageShell>`
- @xyflow/react canvas: spring node physics built-in
- Step palette items: `JARVISStaggerItem` each

### workflow/ sub-pages (8 pages — standardize)
All 8 need `<JARVISPageShell>` added. Replace any `duration`-based transitions with springs.
Existing `motion.div` elements are valid — just add the shell wrapper.

---

## Success Criteria

When this revamp is complete:

```
Jarvis audit script output:
  Score 0 (no Jarvis):        0 / 87  ← was 54
  Score 1 (shell only):       0 / 87  ← was 20
  Score 2-3 (partial):        0 / 87  ← was 13
  Score 4+ (good Jarvis):    87 / 87  ← was 0

Design system files:
  ✅ src/lib/design/tokens.ts
  ✅ src/lib/design/motion.ts
  ✅ src/components/ui/StatusOrb.tsx
  ✅ src/components/ui/EmptyState.tsx (animated)

No duration-based transitions in any Page.tsx
All StatusOrb replaces all inline colored status dots
All empty states show animated icons
All interactive cards have hover lift + glow
prefers-reduced-motion: all animations respect it
TypeScript: 0 new errors
```

---

# LIVE CODE AUDIT — v2 (2026-08-18)
## Post-Commit Findings + Correct Implementation Sequence

**Trigger:** Deep audit of all 87 pages after the "100% spring coverage" commit.  
**Finding:** That commit added page-entrance animation only. The agentic feeling is not yet present.

---

## What the "100% Coverage" Commit Actually Did

```
Before commit: 54 pages had zero animation
After commit:  87 pages have <JARVISPageShell> wrapper

What was added:  opacity 0→1, y 16→0, filter blur(4px)→blur(0px), spring 280/26
What was NOT added:
  - JARVISStagger on any card grid or list (0/87)
  - StatusOrb on any page (0/87)
  - Glow effects on cards/buttons (85/87 still missing)
  - Electric blue accent color (#00D4FF not in codebase)
  - Staggered list item entrances
  - Card hover lift+glow
```

**Result:** Every page now has a smooth entrance. The "agentic" signals are still missing.

---

## Current State — Exact Numbers (Audit 2026-08-18)

| Signal | Pages | Status |
|--------|-------|--------|
| Page entrance (blur+y spring) | 87/87 | ✅ Done |
| `#00D4FF` electric cyan anywhere | **0/87** | ❌ Not started |
| `JARVISStagger` staggered grids | **0/87** | ❌ Not started |
| `StatusOrb` pulsing agent orb | **0/87** (component missing) | ❌ Not started |
| Glow shadows on cards | **2/87** | ❌ OrgPage + Landing only |
| Score 4+ (truly agentic) | **2/87** | ❌ OrgPage + Landing only |
| Dark surface classes | 23/87 | ⚠️ Most use `bg-card` CSS var |

---

## Critical Finding: Primary Color Is Wrong

**The spec calls for `#00D4FF` electric cyan. The codebase uses `#3B82F6` standard blue.**

Current `globals.css`:
```css
--primary:       217 91% 60%;   /* = #3B82F6 — standard blue */
--accent-cyan:   #06B6D4;       /* exists but not used as primary */
--shadow-blue:   0 0 20px rgba(59, 130, 246, 0.15);  /* blue glow, not cyan */
```

Tailwind color aliases that exist but few pages use:
```
jarvis.base:       #0A0D14   (page background — correct)
jarvis.secondary:  #1A1F2E   (cards — correct)
telemetry-cyan:    #06B6D4   (near-electric but not #00D4FF)
```

**Fix required:** Change `--primary` in `globals.css` to the electric cyan:
```css
/* BEFORE */
--primary: 217 91% 60%;   /* #3B82F6 */

/* AFTER */
--primary: 189 100% 42%;  /* #00D4FF — electric cyan */
--shadow-blue: 0 0 20px rgba(0, 212, 255, 0.15);   /* electric glow */
```
This single change propagates electric blue to every `bg-primary`, `border-primary`, `ring-primary`, `text-primary` in the codebase.

---

## Feature Coverage: Graphify & Obsidian

Both features exist and are well-built. Their location and animation state:

### Graphify (`GraphifyProgress.tsx`)
- **Location:** `src/features/org/components/GraphifyProgress.tsx`
- **Access:** OrgPage (`/org`) → sidebar "Graph" button → toggles panel
- **Animation:** ✅ Full spring + SSE-driven node animation, `useReducedMotion`, phase transitions
- **Status:** Complete for OrgPage panel. NOT accessible as a standalone page/route.

### Obsidian Vault Explorer (`ObsidianVaultExplorer.tsx`)
- **Location:** `src/features/org/components/ObsidianVaultExplorer.tsx`
- **Access:** OrgPage (`/org`) → sidebar vault icon → toggles panel
- **Animation:** ✅ `AnimatePresence`, spring tab transitions (400/30), stagger 40ms
- **Status:** Complete for OrgPage panel. NOT in main navigation.

### Knowledge Graph Page (`GraphExplorerPage.tsx`)
- **Location:** `src/features/knowledge-graph/GraphExplorerPage.tsx`
- **Route:** `/knowledge-graph`
- **Animation:** Score 1 (JARVISPageShell only, no inner motion)
- **Gap:** D3 force nodes have no spring settle, no selected-node glow, no cluster animation

---

## Corrected Implementation Sequence

The previous spec had the right phases but needed correction based on audit. Here is the definitive order:

---

### STEP 0-A (BLOCKING): Fix the Primary Color

**This must happen before any other work.** Every page uses `--primary` for focus rings, borders, and buttons. Currently it's generic blue. Changing it to electric cyan instantly transforms the visual identity.

**File:** `src/app/globals.css`
```css
/* In :root, .dark block — change these two lines: */
--primary:      189 100% 42%;  /* #00D4FF — electric cyan */
--ring:         189 100% 42%;  /* matches primary */

/* Update the glow shadows to use electric cyan: */
--shadow-blue:  0 0 20px rgba(0, 212, 255, 0.15);
--shadow-card:  0 4px 24px rgba(0, 0, 0, 0.4), 0 0 1px rgba(0, 212, 255, 0.05);
```

**File:** `tailwind.config.js` — add electric cyan to the named palette:
```js
jarvis: {
  base:     "#0A0D14",
  primary:  "#0F1117",
  secondary:"#1A1F2E",
  elevated: "#252B3B",
  overlay:  "#2D3748",
  electric: "#00D4FF",        // ← ADD THIS
  glow:     "rgba(0,212,255,0.15)",  // ← ADD THIS
},
// Also add named box shadow:
boxShadow: {
  "glow-electric": "0 0 20px rgba(0,212,255,0.15)",
  "glow-electric-strong": "0 0 40px rgba(0,212,255,0.35)",
  // keep existing:
  "glow-blue":   "0 0 20px rgba(59,130,246,0.15)",
  "glow-cyan":   "0 0 15px rgba(6,182,212,0.2)",
  "card":        "0 4px 24px rgba(0,0,0,0.4)",
  "modal":       "0 20px 60px rgba(0,0,0,0.6)",
},
```

**Impact:** This single change makes every existing page feel electric immediately — focus rings glow cyan, buttons pulse cyan, active states glow.

---

### STEP 0-B: Create Foundation Files

In this order (each is a dependency of later steps):

#### 1. `src/lib/design/tokens.ts`

```typescript
// src/lib/design/tokens.ts
// Typed design tokens for programmatic use in components.
// For Tailwind classes, use the tw.* helpers below.

export const colors = {
  electric:    '#00D4FF',
  electricDim: 'rgba(0,212,255,0.15)',
  electricGlow:'rgba(0,212,255,0.30)',
  emerald:     '#10B981',
  amber:       '#F59E0B',
  rose:        '#EF4444',
  violet:      '#8B5CF6',
  // Surfaces
  base:        '#0A0D14',
  surface1:    '#0F1117',
  surface2:    '#1A1F2E',
  surface3:    '#252B3B',
} as const;

export const shadows = {
  electricSm:  '0 0 20px rgba(0,212,255,0.15)',
  electricMd:  '0 0 40px rgba(0,212,255,0.35)',
  card:        '0 4px 24px rgba(0,0,0,0.4)',
} as const;

// Tailwind class helpers — use in className props
export const tw = {
  cardBase:    'bg-[#1A1F2E] border border-white/[0.07] rounded-xl',
  cardHover:   'hover:border-[#00D4FF]/25 hover:shadow-glow-electric hover:-translate-y-0.5',
  glassPanel:  'bg-white/[0.05] backdrop-blur-xl border border-white/[0.08] rounded-xl',
  electricText:'text-[#00D4FF]',
  electricBg:  'bg-[rgba(0,212,255,0.12)]',
  electricRing:'ring-[#00D4FF]/60',
} as const;
```

#### 2. `src/lib/design/motion.ts`

```typescript
// src/lib/design/motion.ts
// Re-exports springs from JARVISPageShell + adds named animation variants.
// Import springs from here — single source of truth.

import type { Variants } from 'framer-motion';
export { SPRING_PAGE as pageSpring, SPRING_FAST as fastSpring,
         SPRING_BOUNCY as bouncySpring, SPRING_SLOW as slowSpring,
         SPRING_PANEL as panelSpring }
  from '@/components/ui/JARVISPageShell';

// Additional named springs not in JARVISPageShell
export const springs = {
  page:    { type: 'spring', stiffness: 280, damping: 26 } as const,
  fast:    { type: 'spring', stiffness: 600, damping: 35 } as const,
  bouncy:  { type: 'spring', stiffness: 450, damping: 18 } as const,
  slow:    { type: 'spring', stiffness: 200, damping: 25 } as const,
  panel:   { type: 'spring', stiffness: 300, damping: 28 } as const,
  gentle:  { type: 'spring', stiffness: 180, damping: 24 } as const,
} as const;

// List stagger
export const listContainer: Variants = {
  hidden:  { opacity: 0 },
  visible: { opacity: 1, transition: { staggerChildren: 0.04, delayChildren: 0.05 } },
};
export const listItem: Variants = {
  hidden:  { opacity: 0, x: -14 },
  visible: { opacity: 1, x: 0, transition: springs.page },
  exit:    { opacity: 0, x: 14, transition: { duration: 0.12 } },
};

// Card stagger
export const cardContainer: Variants = {
  hidden:  { opacity: 0 },
  visible: { opacity: 1, transition: { staggerChildren: 0.05, delayChildren: 0.08 } },
};
export const cardItem: Variants = {
  hidden:  { opacity: 0, y: 18, scale: 0.97 },
  visible: { opacity: 1, y: 0,  scale: 1, transition: springs.page },
};

// Modals / panels
export const backdrop: Variants = {
  hidden:  { opacity: 0 },
  visible: { opacity: 1, transition: { duration: 0.18 } },
  exit:    { opacity: 0, transition: { duration: 0.14 } },
};
export const modal: Variants = {
  hidden:  { opacity: 0, scale: 0.94, y: 14 },
  visible: { opacity: 1, scale: 1,    y: 0, transition: springs.page },
  exit:    { opacity: 0, scale: 0.96, y: 8, transition: { duration: 0.14 } },
};
export const drawer: Variants = {
  hidden:  { y: '100%' },
  visible: { y: 0, transition: { ...springs.panel, delay: 0.05 } },
  exit:    { y: '100%', transition: { duration: 0.18 } },
};
export const slidePanel: Variants = {
  hidden:  { opacity: 0, x: 20 },
  visible: { opacity: 1, x: 0, transition: springs.panel },
  exit:    { opacity: 0, x: 20, transition: { duration: 0.14 } },
};

// Counter (number change animation)
export const counter: Variants = {
  initial: { y: 10,  opacity: 0 },
  animate: { y: 0,   opacity: 1, transition: springs.fast },
  exit:    { y: -10, opacity: 0, transition: { duration: 0.08 } },
};

// Toast
export const toast: Variants = {
  hidden:  { opacity: 0, y: 36, scale: 0.92 },
  visible: { opacity: 1, y: 0,  scale: 1, transition: springs.bouncy },
  exit:    { opacity: 0, y: 16, scale: 0.96, transition: { duration: 0.16 } },
};

// Status orb pulse
export const orbPulse: Variants = {
  idle:    { scale: 1, opacity: 1 },
  active:  { scale: [1, 1.6, 1], opacity: [1, 0.2, 1],
             transition: { duration: 1.8, repeat: Infinity, ease: 'easeInOut' } },
  offline: { scale: 1, opacity: 0.3 },
};
```

#### 3. `src/components/ui/StatusOrb.tsx`

```typescript
// src/components/ui/StatusOrb.tsx
// The key "agent is alive" visual signal. Pulsing ring expands outward
// like a sonar ping. Used everywhere agents/goals show status.

import { motion, useReducedMotion } from 'framer-motion';

// Map every status string → hex color
const ORB: Record<string, string> = {
  running:       '#00D4FF',  executing:    '#00D4FF',
  completed:     '#10B981',  complete:     '#10B981',
  failed:        '#EF4444',  error:        '#EF4444',
  pending:       '#F59E0B',  planning:     '#F59E0B',
  waiting_human: '#F59E0B',  waiting:      '#F59E0B',
  idle:          '#475569',  paused:       '#475569',
  offline:       '#1E2535',  unknown:      '#1E2535',
  draft:         '#8B5CF6',  archived:     '#475569',
  published:     '#10B981',
};

const PULSE_STATUSES = new Set([
  'running', 'executing', 'pending', 'planning', 'waiting_human',
]);

interface StatusOrbProps {
  status: string;
  size?: number;   // diameter in px — default 8
  className?: string;
}

export function StatusOrb({ status, size = 8, className = '' }: StatusOrbProps) {
  const reduce = useReducedMotion();
  const color  = ORB[status] ?? ORB.unknown;
  const active = PULSE_STATUSES.has(status) && !reduce;

  return (
    <span
      className={`relative inline-flex shrink-0 items-center justify-center ${className}`}
      style={{ width: size, height: size }}
      role="img"
      aria-label={`Status: ${status}`}
    >
      {/* Expanding ring — sonar ping effect */}
      {active && (
        <motion.span
          className="absolute rounded-full"
          style={{ width: size, height: size, backgroundColor: color }}
          animate={{ scale: [1, 2.2, 1], opacity: [0.7, 0, 0.7] }}
          transition={{ duration: 1.8, repeat: Infinity, ease: 'easeInOut' }}
        />
      )}
      {/* Core dot */}
      <span
        className="relative rounded-full"
        style={{ width: size, height: size, backgroundColor: color }}
      />
    </span>
  );
}
```

#### 4. Upgrade `src/components/ui/EmptyState.tsx`

```typescript
// Upgraded EmptyState with 3 animation variants
// Replaces the static version (plain div, no motion)

import type { ReactNode } from 'react';
import { motion, useReducedMotion } from 'framer-motion';

interface EmptyStateProps {
  icon?:        ReactNode;
  title:        string;
  description?: string;
  action?:      ReactNode;
  variant?:     'float' | 'pulse' | 'static';
  className?:   string;
}

export function EmptyState({
  icon, title, description, action,
  variant = 'float', className = '',
}: EmptyStateProps) {
  const reduce = useReducedMotion();

  const iconMotion =
    reduce || variant === 'static' ? {} :
    variant === 'float' ? {
      animate: { y: [0, -8, 0] },
      transition: { duration: 3, repeat: Infinity, ease: 'easeInOut' },
    } : {
      animate: { scale: [1, 1.08, 1] },
      transition: { duration: 2, repeat: Infinity, ease: 'easeInOut' },
    };

  return (
    <motion.div
      initial={reduce ? {} : { opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ type: 'spring', stiffness: 280, damping: 26, delay: 0.1 }}
      className={`flex flex-col items-center justify-center py-14 px-6 text-center gap-3 ${className}`}
    >
      {icon && (
        <motion.div {...iconMotion} className="text-[#00D4FF] opacity-35 mb-1">
          {icon}
        </motion.div>
      )}
      <p className="text-sm font-semibold text-[#F1F5F9]">{title}</p>
      {description && (
        <p className="text-xs text-[#475569] max-w-xs leading-relaxed">{description}</p>
      )}
      {action && <div className="mt-2">{action}</div>}
    </motion.div>
  );
}
```

---

### STEP 1: Apply JARVISStagger to 20 Shell-Only Pages

After Step 0, the stagger animations will use electric cyan (`--primary` now = `#00D4FF`).

**Pattern for every page in this step:**

```tsx
// 1. Add import at top:
import { JARVISStagger, JARVISStaggerItem } from '@/components/ui/JARVISPageShell';
import { StatusOrb } from '@/components/ui/StatusOrb';

// 2. Wrap every card grid/list:
// BEFORE:
<div className="grid grid-cols-3 gap-4">
  {items.map(item => <Card key={item.id} item={item} />)}
</div>

// AFTER:
<JARVISStagger className="grid grid-cols-3 gap-4">
  {items.map(item => (
    <JARVISStaggerItem key={item.id}>
      <Card item={item} />
    </JARVISStaggerItem>
  ))}
</JARVISStagger>

// 3. Replace inline status dots:
// BEFORE: <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
// AFTER:  <StatusOrb status={item.status} size={8} />
```

**Page-specific priorities in Step 1:**

| Page | Grid/List to Wrap | StatusOrb Replacements |
|------|------------------|----------------------|
| `DashboardPage` | KPI cards (staggerMs=80), activity feed (staggerMs=40), agent grid (staggerMs=60) | Agent status dots |
| `GoalsListPage` | Goal rows (staggerMs=40) | `goal.status` dots |
| `AgentsListPage` | Agent cards (staggerMs=60) | `agent.status` dots |
| `ApprovalsPage` | Approval cards (staggerMs=60) | Approval status |
| `MarketplacePage` | Template cards (staggerMs=50) | Template status |
| `NotificationCenterPage` | Notification rows (staggerMs=35) | Notification type orbs |
| `EvalPage` | Eval run rows (staggerMs=45) | Run status |
| `KnowledgePage` | Collection cards (staggerMs=60) | - |
| `MemoryExplorerPage` | Memory cards (staggerMs=40) | - |
| `ToolsPage` | Tool rows (staggerMs=30) | Tool connector status |
| `ObservabilityPage` | Trace rows (staggerMs=30) | Service health |
| `CostDashboardPage` | Goal rows (staggerMs=40) | - |
| `SchedulesPage` | Schedule rows (staggerMs=45) | Schedule active status |
| `BillingPage` | Invoice rows (staggerMs=50) | - |
| `SettingsPage` | Settings sections (staggerMs=60) | - |
| `AnalyticsDashboardPage` | Chart cards (staggerMs=100) | - |
| `AgentsListPage` | Agent cards (staggerMs=60) | Agent status |
| `AgentDetailPage` | Tool list per tab (staggerMs=30) | Agent status |
| `AgentDashboardPage` | KPI strip (staggerMs=80) | - |
| `GoalDetailPage` | Step list (staggerMs=80) | Step status |

---

### STEP 2: Add Card Hover Glow to All Interactive Cards

This is the single most impactful "agentic" change. Every card that is clickable must glow on hover.

**Add these Tailwind classes to every clickable card or list row:**
```
className="... transition-[box-shadow,border-color,transform]
           hover:border-[#00D4FF]/25
           hover:shadow-glow-electric
           hover:-translate-y-0.5
           cursor-pointer"
```

Using the `tw` helpers from `tokens.ts`:
```tsx
import { tw } from '@/lib/design/tokens';
<div className={`${tw.cardBase} ${tw.cardHover} p-4`}>
  ...
</div>
```

Apply to:
- All agent cards
- All goal rows/cards
- All workflow cards
- All template cards
- All connector cards
- All marketplace items
- All tool rows
- All notification items (hover only, not hover-lift)

---

### STEP 3: Running Agent Pulse — The Agentic Signal

This is the "bots are working" visual. Replace all static status indicators with `StatusOrb`.

**Search pattern to find all replacements:**
```bash
grep -rn "animate-pulse\|rounded-full.*bg-emerald\|rounded-full.*bg-green\|rounded-full.*bg-blue" src/features/ --include="*.tsx"
```

**Every occurrence of these patterns becomes a `StatusOrb`:**
```tsx
// All of these become <StatusOrb status="running" size={8} />:
<span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
<span className="w-2 h-2 rounded-full bg-green-400" />
<div className="h-2 w-2 rounded-full bg-blue-400 animate-pulse" />
<span className="w-2 h-2 bg-emerald-400 rounded-full animate-pulse-glow" />
```

---

### STEP 4: Fix Knowledge Graph Page (GraphExplorerPage)

Currently score 1. This is where "glowing nodes" literally lives.

```tsx
// src/features/knowledge-graph/GraphExplorerPage.tsx

// CURRENT STATE: JARVISPageShell wrapper only, no inner animation

// TARGET:
// 1. D3/xyflow nodes need: selected node glows electric
//    node.style.boxShadow = '0 0 20px rgba(0,212,255,0.4)'
// 2. Selected node: scale 1.2 with spring
// 3. On mount: nodes animate from center outward (spring settle)
// 4. Hover node: electric ring expands
// 5. Edge flow: animated dashes (stroke-dashoffset) flowing along edges

// Minimum viable improvement (no D3 rewrite needed):
import { motion } from 'framer-motion';
// Wrap each node in the node render function with motion.div
// <motion.div whileHover={{ scale: 1.08 }} whileTap={{ scale: 0.96 }}>
```

---

### STEP 5: Graphify + Obsidian — Promote to Navigation

Both features exist and are fully animated. They should be more discoverable.

**Current state:** Only accessible via OrgPage sidebar → icon button  
**Target:** Accessible from main navigation sidebar

```
Sidebar navigation items to add/ensure:
  /knowledge-graph  → Knowledge Graph (icon: Network)
  /org → "Command Center" which contains Graphify + Obsidian panels

Main nav structure should show:
  Dashboard
  Goals
  Agents
  Knowledge
  Knowledge Graph  ← promote (has D3 force viz)
  Command Center   ← OrgPage (has Graphify + Obsidian)
  ...
```

---

### STEP 6: Agentic Dark Surface on 64 Pages Without Dark Classes

64/87 pages use `bg-card` (CSS var) but most do this in components already using the dark theme through CSS variables. The dark `--background` and `--card` CSS vars ARE dark (`#0A0D14`, `#111827`). This step verifies the inheritance is correct.

**Verify:** In `globals.css`, `--background: 220 20% 5%` = `#0A0D14` ✅  
**Verify:** `--card: 222 18% 10%` = dark ✅  
**Action:** No mass change needed here — CSS vars are already dark. The issue was wrong primary color (Step 0-A fixes this).

---

### STEP 7: Live Agent Activity — "Bots Working" on Dashboard

The dashboard needs real-time visual feedback that agents are executing:

```tsx
// src/features/dashboard/components/LiveActivityStream.tsx
// Current: renders items without animation
// Target: each new SSE event slides in from top with springs.bouncy

import { motion, AnimatePresence } from 'framer-motion';
import { springs } from '@/lib/design/motion';
import { StatusOrb } from '@/components/ui/StatusOrb';

// Wrap the item list:
<AnimatePresence mode="popLayout">
  {events.map(event => (
    <motion.div
      key={event.id}
      layout
      initial={{ opacity: 0, y: -12, scale: 0.97 }}
      animate={{ opacity: 1, y: 0,   scale: 1 }}
      exit={{    opacity: 0, x: 20,  scale: 0.95 }}
      transition={springs.bouncy}
      className="flex items-center gap-3 p-2 rounded-lg hover:bg-white/[0.04]"
    >
      <StatusOrb status={event.type} size={8} />
      <span className="text-sm text-[#94A3B8]">{event.message}</span>
      <span className="ml-auto text-xs text-[#475569]">{event.time}</span>
    </motion.div>
  ))}
</AnimatePresence>
```

---

### STEP 8: Goal Execution — Streaming "Machine Working" Animation

When a goal is running, the GoalDetailPage should feel like a machine executing:

```tsx
// src/features/goals/GoalDetailPage.tsx

// Current: step list is static, status badges are color classes
// Target agentic feeling:

// 1. Running step: pulsing ring (StatusOrb size=12, status="running")
// 2. Completed step: brief scale 0→1.2→1 + emerald flash
// 3. Streaming output: typewriter animation (character by character)
// 4. Cost counter: AnimatePresence counterVariants on value update
// 5. Progress: motion.div width animation 0→(step/total * 100)%

// Step status transition (key micro-interaction):
<motion.div
  key={step.id}
  layout
  className={`flex items-center gap-3 p-3 rounded-lg ${
    step.status === 'active' ? 'bg-[#00D4FF]/[0.06] border border-[#00D4FF]/20' :
    step.status === 'complete' ? 'bg-emerald-500/[0.06]' : ''
  }`}
>
  <StatusOrb status={step.status} size={10} />
  ...
</motion.div>
```

---

## Verification Script (Run After Each Step)

```python
# Save as scripts/audit_jarvis.py
# Run: python3 scripts/audit_jarvis.py

import os, sys

ROOT = 'agent-verse-frontend/src/features'
results = []

for root, _, files in os.walk(ROOT):
    for fname in files:
        if not fname.endswith('Page.tsx'): continue
        path = os.path.join(root, fname)
        with open(path) as f: content = f.read()
        
        score = sum([
            'JARVISStagger'    in content,   # stagger lists
            '<motion.'         in content,   # motion elements
            '#00D4FF'          in content or 'text-[#00D4' in content,  # electric
            'glow-electric'    in content or 'glow_electric' in content, # glow
            'StatusOrb'        in content,   # status orbs
            'JARVISStaggerItem'in content,   # stagger items
        ])
        results.append((score, path))

results.sort()
s0 = sum(1 for s,_ in results if s==0)
s1 = sum(1 for s,_ in results if s==1)
s2 = sum(1 for s,_ in results if s==2)
s3 = sum(1 for s,_ in results if s==3)
s4p= sum(1 for s,_ in results if s>=4)
total = len(results)

print(f"Total: {total}")
print(f"Score 0 (not started): {s0}")
print(f"Score 1 (minimal):     {s1}")
print(f"Score 2-3 (partial):   {s2+s3}")
print(f"Score 4+ (agentic):    {s4p}  ← target: {total}")
print(f"\nNot-agentic pages ({s0+s1}):")
for s,p in results:
    if s < 2: print(f"  {p}")
```

---

## Complete Sequence Summary

```
Step 0-A: globals.css — --primary: 189 100% 42% (#00D4FF) [1 file, 2 lines]
Step 0-B1: Create src/lib/design/tokens.ts
Step 0-B2: Create src/lib/design/motion.ts
Step 0-B3: Create src/components/ui/StatusOrb.tsx
Step 0-B4: Upgrade src/components/ui/EmptyState.tsx

Step 1: Apply JARVISStagger + StatusOrb to 20 shell-only pages
        → Instant stagger on cards/lists, electric rings on status

Step 2: Add hover glow (hover:shadow-glow-electric hover:border-[#00D4FF]/25)
        to all clickable cards — 50+ pages

Step 3: Replace all inline status dots → <StatusOrb> — 50+ pages
        (search: animate-pulse + rounded-full)

Step 4: Upgrade GraphExplorerPage — glowing nodes, selected node electric ring

Step 5: Add Knowledge Graph + Command Center to main sidebar navigation

Step 6: Verify dark surfaces propagate correctly via CSS vars (no mass change)

Step 7: Upgrade LiveActivityStream — SSE items slide in with springs.bouncy

Step 8: Upgrade GoalDetailPage — streaming typewriter, step pulse, progress bar

Phase 3 Polish (after all steps):
  - All empty states get icon + float animation via upgraded EmptyState
  - All duration-based transitions → spring equivalents
  - All modals use backdrop + modal variants from motion.ts
```

---

## Definition of "World-Class Agentic Dashboard"

The experience the user described ("glowing nodes, autonomous bots working, Jarvis feeling") requires:

1. **Electric cyan everywhere:** Primary color glows on focus, borders, rings
2. **Status orbs with sonar rings:** Every running agent/goal pulses — sonar expand-fade
3. **Staggered content:** Cards/rows appear in sequence on navigation
4. **Live activity stream:** SSE events slide in and stack with spring physics
5. **Goal execution streaming:** Text appears character by character while running
6. **Dark surfaces:** Near-black backgrounds with translucent glass panels
7. **Hover lift+glow:** Cards lift 2px + electric glow edge on hover
8. **Knowledge graph glow:** D3 nodes pulse and glow when selected

**When all 8 are present: the UI feels like a living machine, not a static dashboard.**

Currently present: 7 (dark surfaces) ✅, partial 8 (Graphify only)
Missing: 1,2,3,4,5,6 across most pages

---

# DEEP CODE ANALYSIS — v3 (2026-08-18)
## Component-Level Audit: Everything That Can Be Improved

---

## Executive Summary

| Category | Issue | Count | Impact |
|----------|-------|-------|--------|
| Feature dirs with zero animation | ❌ | **46 / 55** | Every feature looks static |
| Shared layout components without animation | Sidebar, TopBar, ConfirmModal | **3** | Affects every page |
| Interactive components without hover/tap | No whileHover/whileTap | **67** | No tactile feedback |
| Mutations without optimistic updates | No onMutate | **48** | UI feels laggy on actions |
| Inline fetch in useEffect | Should be TanStack Query | **22** | Duplicate loading states |
| TypeScript `any` | Type safety gaps | **32 files** | Runtime errors in prod |
| Pages with useQuery but no Skeleton | Missing loading state | **8** | White flash on load |
| Console.log in prod | ToolsPage.tsx | **1** | Dev noise in prod |

---

## Critical: Shared Layout Components (Affects EVERY Page)

### Sidebar.tsx — 410 lines, ZERO animation

The sidebar is visible on every page. Currently:
- Collapse/expand: instant width change, no spring
- Nav item hover: CSS `hover:bg-accent` only
- Active item: static color change, no spring
- Mobile overlay: no AnimatePresence

**What it needs:**
```tsx
// 1. Collapse/expand with spring width:
<motion.div
  animate={{ width: collapsed ? 64 : 220 }}
  transition={{ type: 'spring', stiffness: 280, damping: 26 }}
>

// 2. Nav item hover lift:
<motion.div whileHover={{ x: 2 }} whileTap={{ scale: 0.98 }}
  transition={{ type: 'spring', stiffness: 600, damping: 35 }}>

// 3. Active indicator spring:
<motion.div layoutId="sidebar-active-indicator"
  className="absolute left-0 inset-y-0 w-0.5 bg-[#00D4FF] rounded-r-full" />

// 4. Mobile overlay AnimatePresence:
<AnimatePresence>
  {sidebarOpen && (
    <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
      className="fixed inset-0 bg-black/40 z-20 md:hidden" />
  )}
</AnimatePresence>

// 5. Logo pulse: the Zap icon should pulse electric on mount
<motion.div animate={{ scale: [1, 1.1, 1] }} transition={{ duration: 2, repeat: Infinity }}>
  <Zap className="h-5 w-5 text-[#00D4FF]" />
</motion.div>
```

### TopBar.tsx — 193 lines, ZERO animation

The top bar search and notifications are visible everywhere. Currently:
- Search: no AnimatePresence on results dropdown
- Plan badge: static colored span
- Theme toggle: instant swap, no spring

**What it needs:**
```tsx
// 1. Search results dropdown AnimatePresence:
<AnimatePresence>
  {results.length > 0 && (
    <motion.div
      initial={{ opacity: 0, y: -8, scale: 0.97 }}
      animate={{ opacity: 1, y: 0,  scale: 1 }}
      exit={{    opacity: 0, y: -4, scale: 0.98 }}
      transition={{ type: 'spring', stiffness: 300, damping: 28 }}
      className="absolute top-full mt-1 left-0 w-80 bg-[#1A1F2E] ..."
    >
      {results.map((r, i) => (
        <motion.div key={r.id}
          initial={{ opacity: 0, x: -8 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ delay: i * 0.03, type: 'spring', stiffness: 280, damping: 26 }}>
          ...
        </motion.div>
      ))}
    </motion.div>
  )}
</AnimatePresence>

// 2. Notification badge: bounce when count increases
<motion.span key={notifCount}
  initial={{ scale: 0.5 }} animate={{ scale: 1 }}
  transition={{ type: 'spring', stiffness: 450, damping: 18 }}
  className="absolute -top-1 -right-1 h-4 w-4 rounded-full bg-[#00D4FF] text-[9px] font-bold">
  {notifCount}
</motion.span>

// 3. Cmd+K hint: glow on focus
// 4. Theme toggle: spring rotate on icon swap
```

### ConfirmModal.tsx — 146 lines, ZERO animation

All destructive confirmations (delete agent, archive workflow, etc.) use this modal. Currently instant appear/disappear.

**What it needs:**
```tsx
// Backdrop + modal with AnimatePresence:
<AnimatePresence>
  {open && (
    <>
      <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
        className="fixed inset-0 bg-black/60 z-40" />
      <motion.div
        initial={{ opacity: 0, scale: 0.94, y: 12 }}
        animate={{ opacity: 1, scale: 1,    y: 0 }}
        exit={{    opacity: 0, scale: 0.96, y: 8 }}
        transition={{ type: 'spring', stiffness: 280, damping: 26 }}
        className="fixed z-50 ...">
        {/* Destructive: red-tinted confirm button pulses attention */}
        <motion.button
          whileTap={{ scale: 0.97 }}
          transition={{ type: 'spring', stiffness: 600, damping: 35 }}
          className="bg-rose-600 hover:bg-rose-700 ...">
          {confirmLabel}
        </motion.button>
      </motion.div>
    </>
  )}
</AnimatePresence>
```

---

## Critical: Dashboard Components

### LiveActivityStream.tsx — THE "BOTS WORKING" FEED — ZERO animation

This is the single most important agentic component on the dashboard. It shows running goals in real-time. Currently renders a static list.

**Current:** `const displayed = goals.slice(0, maxItems)` → plain `<div>` per item.

**What it needs:**
```tsx
import { motion, AnimatePresence } from 'framer-motion';
import { StatusOrb } from '@/components/ui/StatusOrb';

// REPLACE the return with:
return (
  <div className="space-y-1">
    <AnimatePresence mode="popLayout">
      {displayed.map((goal, i) => (
        <motion.div
          key={goal.id}
          layout
          initial={{ opacity: 0, y: -10, scale: 0.97 }}
          animate={{ opacity: 1, y: 0,   scale: 1 }}
          exit={{    opacity: 0, x: 20,  scale: 0.95 }}
          transition={{ type: 'spring', stiffness: 300, damping: 26, delay: i * 0.03 }}
          onClick={() => navigate(`/goals/${goal.id}`)}
          className="flex items-center gap-3 p-2.5 rounded-lg cursor-pointer
                     hover:bg-white/[0.04] hover:shadow-[0_0_12px_rgba(0,212,255,0.08)]
                     transition-shadow group"
        >
          <StatusOrb status={goal.status} size={8} />
          <span className="flex-1 text-sm text-[#94A3B8] truncate group-hover:text-[#F1F5F9] transition-colors">
            {goal.goal}
          </span>
          <span className="text-xs text-[#475569] tabular-nums shrink-0">
            {timeAgo(goal.created_at)}
          </span>
          {goal.cost_usd != null && (
            <span className="text-xs text-[#00D4FF] tabular-nums shrink-0">
              ${goal.cost_usd.toFixed(3)}
            </span>
          )}
        </motion.div>
      ))}
    </AnimatePresence>
  </div>
);
```

### AgentOrbitView.tsx — D3 Force Graph — ZERO spring physics

D3 is used for layout only. Status colors are static hex strings. No spring on node entrance.

**Current gaps:**
- Status colors: `active: "#22c55e"` → generic green, not electric `#00D4FF`
- No AnimatePresence on node enter/exit
- No glowing ring on active nodes
- Static SVG rendering, no CSS transitions

**What it needs:**
```tsx
// 1. Electric color for active agents:
const STATUS_COLORS = {
  active: "#00D4FF",  // ← was #22c55e
  idle:   "#475569",
  error:  "#EF4444",
};

// 2. SVG circles: add CSS filter for glow on active nodes
// In the D3 circle rendering:
circle
  .attr('filter', d => d.status === 'active' ? 'url(#glow-filter)' : 'none')

// 3. Add SVG defs for glow filter:
svg.append('defs').html(`
  <filter id="glow-filter" x="-50%" y="-50%" width="200%" height="200%">
    <feGaussianBlur stdDeviation="3" result="coloredBlur"/>
    <feMerge>
      <feMergeNode in="coloredBlur"/>
      <feMergeNode in="SourceGraphic"/>
    </feMerge>
  </filter>
`);

// 4. Pulse ring on active nodes (CSS animation):
circle.attr('class', d => d.status === 'active' ? 'agent-active-pulse' : '')
// CSS: .agent-active-pulse { animation: pulse-ring 1.8s ease-in-out infinite; }
```

### AIOpsDashboard.tsx — 7 `any` types, no animation

The secondary dashboard mode with AI ops panel. Has lists but no stagger.

**Fixes needed:**
- Replace `any` with proper interfaces
- Add stagger on metrics list
- Electric color on active service indicators

---

## Critical: Goals Components (The Heart of the Product)

### MissionGoalComposer.tsx — NO animation

This is the primary goal submission input — the most used interaction in the entire app. It has no animation whatsoever.

**Current:** CSS classes only, `transition-colors` on some elements.

**What it needs:**
```tsx
// 1. Input focus glow (the signature interaction):
<motion.div
  animate={isFocused ? {
    boxShadow: '0 0 0 2px rgba(0,212,255,0.4), 0 0 20px rgba(0,212,255,0.15)'
  } : {
    boxShadow: '0 0 0 1px rgba(255,255,255,0.08)'
  }}
  transition={{ type: 'spring', stiffness: 280, damping: 26 }}
  className="rounded-xl">
  <textarea ... onFocus={() => setFocused(true)} onBlur={() => setFocused(false)} />
</motion.div>

// 2. Workflow mode pills: selected pill scale
<motion.button
  key={mode.id}
  whileTap={{ scale: 0.96 }}
  animate={selectedMode === mode.id ? { scale: 1.02 } : { scale: 1 }}
  transition={{ type: 'spring', stiffness: 600, damping: 35 }}>

// 3. Submit button: JARVISButton with spring press:
<JARVISButton type="submit" className="...">
  {submitting ? <Loader2 className="animate-spin" /> : <Zap />}
  {submitting ? 'Launching...' : 'Launch Goal'}
</JARVISButton>

// 4. Intent preview: slide down AnimatePresence when input has content:
<AnimatePresence>
  {goalText.length > 10 && (
    <motion.div
      initial={{ opacity: 0, y: -6, height: 0 }}
      animate={{ opacity: 1, y: 0,  height: 'auto' }}
      exit={{    opacity: 0, y: -4, height: 0 }}
      transition={{ type: 'spring', stiffness: 280, damping: 26 }}>
      <p className="text-xs text-[#475569]">Will use: {suggestedAgent}</p>
    </motion.div>
  )}
</AnimatePresence>
```

### GoalOutcomeHero.tsx — NO animation

The success/failure state shown after a goal completes. Currently static.

**What it needs:**
```tsx
// Success state: emerald scale-in + confetti
<motion.div
  initial={{ scale: 0.8, opacity: 0 }}
  animate={{ scale: 1, opacity: 1 }}
  transition={{ type: 'spring', stiffness: 300, damping: 20 }}>
  <CheckCircle2 className="h-16 w-16 text-emerald-400" />
</motion.div>

// Failure state: rose shake on mount
<motion.div
  initial={{ x: 0 }}
  animate={{ x: [0, -8, 8, -6, 6, 0] }}
  transition={{ duration: 0.4, delay: 0.2 }}>
```

### GoalResultCanvas.tsx, GoalEvidencePanel.tsx, GoalExplainPanel.tsx — NO animation

All goal result views are static. They show rich data (evidence, explanation, results) but with zero entrance animation.

**Pattern for all three:**
```tsx
// Wrap in JARVISPageShell equivalent (or add motion.div directly):
<motion.div
  initial={{ opacity: 0, y: 12 }}
  animate={{ opacity: 1, y: 0 }}
  transition={{ type: 'spring', stiffness: 280, damping: 26 }}>

// For lists within (evidence items, explain steps):
// Use JARVISStagger + JARVISStaggerItem
```

---

## Missing Optimistic Updates (48 Mutations)

Optimistic updates make the UI feel instant. Without them, every user action has a visible delay.

**Priority mutations to fix (highest user impact):**

| File | Mutation | Fix |
|------|----------|-----|
| `AgentsListPage` | Delete agent | Optimistic remove from list |
| `GoalsListPage` | Cancel goal | Optimistic status → 'cancelling' |
| `AgentDetailPage` | Update agent config | Optimistic field update |
| `ApprovalsPage` | Approve/Reject | Optimistic remove from pending |
| `KnowledgePage` | Delete collection | Optimistic remove |
| `ToolsPage` | Toggle tool | Optimistic status flip |
| `SchedulesPage` | Toggle schedule | Optimistic active flip |
| `ConnectorsRegisteredPage` | Disconnect | Optimistic status → 'offline' |

**Pattern:**
```tsx
const deleteMutation = useMutation({
  mutationFn: (id: string) => agentsApi.delete(id),
  onMutate: async (id) => {
    await qc.cancelQueries({ queryKey: ['agents'] });
    const prev = qc.getQueryData(['agents']);
    qc.setQueryData(['agents'], (old: Agent[]) =>
      old?.filter(a => a.id !== id) ?? []);
    return { prev };
  },
  onError: (_, __, ctx) => qc.setQueryData(['agents'], ctx?.prev),
  onSettled: () => qc.invalidateQueries({ queryKey: ['agents'] }),
});
```

---

## Inline Fetch → TanStack Query (22 files)

Using `useEffect + fetch` causes: no caching, duplicate requests, manual loading state, no retry.

**Files to convert (priority):**
```
features/agents/AgentIdentityPage.tsx         — useEffect fetch for identity data
features/artifacts/ArtifactsBrowserPage.tsx   — useEffect fetch for artifacts
features/auth/AuthPage.tsx                    — useEffect fetch for SSO config
features/chat/AgentMemoryPage.tsx             — useEffect fetch for memories
features/eval/EvalPage.tsx                    — useEffect fetch for eval runs
features/goals/GoalDNAPage.tsx                — useEffect fetch for DNA data
```

**Pattern:**
```tsx
// BEFORE:
useEffect(() => {
  fetch('/api/v1/agents').then(r => r.json()).then(setAgents);
}, []);

// AFTER:
const { data: agents } = useQuery({
  queryKey: ['agents', orgId],
  queryFn: () => agentsApi.list(orgId),
  staleTime: 30_000,
});
```

---

## TypeScript `any` Fixes (32 files)

**Highest impact files:**

| File | `any` count | Fix |
|------|------------|-----|
| `AgentOrbitView.tsx` | 3 | Define `SimulationNode` interface |
| `AIOpsDashboard.tsx` | 7 | Define `AIMetric`, `ServiceStatus` interfaces |
| `GoalDetailPage.tsx` | Multiple | Use existing `Goal` type from api/client |
| `GoalsListPage.tsx` | Multiple | Narrow with `Goal` type |
| `KnowledgeGraphExplorerPage.tsx` | Multiple | Define `GraphNode`, `GraphEdge` |
| `ModelControlCenter.tsx` | 4 | Define `Model`, `ModelConfig` interfaces |

---

## Missing Virtualization (High-Volume Lists)

Only 4 files use `useVirtualizer`. These lists can grow to 100s/1000s of items:

| Feature | List | Current | Should Use |
|---------|------|---------|-----------|
| `GoalsListPage` | Goals list | `.map()` | `useVirtualizer` |
| `AgentsListPage` | Agent cards | `.map()` | `useVirtualizer` |
| `AuditExplorerPage` | Audit events | `.map()` | `useVirtualizer` |
| `ToolsPage` | Tools list | `.map()` | `useVirtualizer` |
| `MemoryExplorerPage` | Memory cards | `.map()` | `useVirtualizer` |
| `NotificationCenterPage` | Notifications | `.map()` | `useVirtualizer` |

---

## Missing hooks/ Directory (Most Features)

46 of 55 features have NO dedicated hooks directory. Without hooks:
- Business logic mixed into Page components
- No reusability across pages
- No isolated testing

**Features that need hooks extraction:**
```
Most impactful:
  features/goals/hooks/useGoals.ts          — list query + pagination
  features/goals/hooks/useGoalStream.ts     — SSE streaming
  features/agents/hooks/useAgents.ts        — agent queries
  features/agents/hooks/useAgentStatus.ts   — real-time status
  features/knowledge/hooks/useKnowledge.ts  — collection queries
  features/workflow/hooks/useWorkflow.ts    — workflow CRUD
```

---

## Ingestion Feature — Components Exist but No Animation

`features/ingestion/components/` has: `SourceCard.tsx`, `SourceDetailDrawer.tsx`, `SourceList.tsx`

None have animation. These are the components shown during file upload (drag-drop, progress, queue).

**SourceCard.tsx needs:**
```tsx
// Card hover: lift + electric border
// Processing state: animated progress fill
// Complete state: emerald flash
```

**SourceList.tsx needs:**
```tsx
// JARVISStagger on list
// New item enters from top with springs.bouncy
// Item remove: scale out exit
```

---

## Full Improvement Sequence (Updated with Component-Level Findings)

```
Step 0-A: Fix --primary color in globals.css (#3B82F6 → #00D4FF)
Step 0-B: Create lib/design/tokens.ts, motion.ts, StatusOrb.tsx, EmptyState.tsx

Step 1-SHARED: Animate shared layout (affects every page)
  1a. Sidebar.tsx — spring collapse, nav item hover, layoutId active indicator
  1b. TopBar.tsx — AnimatePresence search results, notification badge bounce
  1c. ConfirmModal.tsx — AnimatePresence backdrop + modal spring

Step 1-DASHBOARD: Animate core dashboard components
  1d. LiveActivityStream.tsx — AnimatePresence popLayout, StatusOrb, hover glow
  1e. AgentOrbitView.tsx — electric active color, SVG glow filter, pulse ring
  1f. AIOpsDashboard.tsx — stagger metrics, fix 7 `any` types

Step 1-GOALS: Animate goal submission and results
  1g. MissionGoalComposer.tsx — focus glow, pill spring, JARVISButton, intent preview
  1h. GoalOutcomeHero.tsx — scale-in success, shake failure
  1i. GoalResultCanvas.tsx, GoalEvidencePanel.tsx, GoalExplainPanel.tsx — stagger

Step 2: Apply JARVISStagger + StatusOrb to 20 shell-only pages
Step 3: Add hover:shadow-glow-electric to all clickable cards
Step 4: Replace all inline status dots with <StatusOrb>
Step 5: Add AnimatePresence to all existing dropdowns/modals
Step 6: GraphExplorerPage — glowing nodes
Step 7: 48 mutations → add optimistic updates (priority: agents, goals, approvals)
Step 8: 22 inline fetches → convert to TanStack Query
Step 9: Add virtualization to 6 high-volume lists
Step 10: Fix 32 TypeScript `any` types
Step 11: Extract hooks to hooks/ directory for 10 priority features
Step 12: Ingestion components animation (SourceCard, SourceList, SourceDetailDrawer)
Step 13: Chat components (ChatStepCard, ChatHITLCard, ChatArtifactPanel)
Step 14: Triggers components (TriggerList, TriggerCreateModal)
Step 15: Templates components (TemplatePickerModal, TemplateInstantiator)
```

---

## Feature-by-Feature Animation Status

```
❌ 46/55 features: zero motion in any file
✅  3/55 features: good (gateway, org, settings)
⚠️  6/55 features: partial (dashboard, eval, goals, landing, workflow, models)

Top 10 highest-impact features to fix first:
  1. dashboard — LiveActivityStream, AgentOrbitView (most visible)
  2. goals     — MissionGoalComposer, GoalDetailPage (most used)
  3. agents    — AgentsListPage, AgentDetailPage (primary resource)
  4. knowledge — KnowledgePage, GraphExplorerPage (D3 glowing nodes)
  5. workflows — all 8 sub-pages (complex feature, partial motion)
  6. governance — AuditExplorerPage (hash chain animation)
  7. ingestion  — drop zone, progress queue
  8. chat       — ChatStepCard, ChatHITLCard (agentic execution cards)
  9. approvals  — HITL queue cards
 10. notifications — SSE-driven live feed
```

---

## Updated Definition of Done

The dashboard feels "world-class agentic" when ALL of these are true:

### Visual Identity ✓/✗
- [ ] Primary accent = `#00D4FF` electric cyan (currently `#3B82F6`)
- [ ] Running agents/goals show sonar-ping orbs (StatusOrb component)
- [ ] Cards lift 2px + electric glow edge on hover
- [ ] Dark surfaces: `#0A0D14` → `#0F1117` → `#1A1F2E` hierarchy

### Motion & Animation ✓/✗
- [ ] Page entrance: blur+y spring (✅ done for all 87 pages)
- [ ] Sidebar collapse/expand: spring width (❌ missing)
- [ ] Search results: AnimatePresence dropdown (❌ missing)
- [ ] Lists: JARVISStagger enter sequence (❌ 0/87 pages)
- [ ] SSE activity feed: items slide in from top with springs.bouncy (❌ missing)
- [ ] Modal open/close: AnimatePresence spring (❌ ConfirmModal missing)

### Agentic Signals ✓/✗
- [ ] LiveActivityStream: animated, StatusOrb per event (❌ zero animation)
- [ ] Goal execution: streaming typewriter + step pulse (❌ missing)
- [ ] AgentOrbitView: electric color, SVG glow filter (❌ wrong color, no glow)
- [ ] MissionGoalComposer: focus glow, intent preview (❌ no animation)
- [ ] Running agent badge on every agent card (❌ missing)

### Code Quality ✓/✗
- [ ] Zero TypeScript `any` (❌ 32 files)
- [ ] Zero inline fetch in useEffect (❌ 22 files)
- [ ] All mutations have optimistic updates (❌ 48 missing)
- [ ] All high-volume lists virtualized (❌ 6 lists)
- [ ] Zero console.log in prod (❌ 1 file)
