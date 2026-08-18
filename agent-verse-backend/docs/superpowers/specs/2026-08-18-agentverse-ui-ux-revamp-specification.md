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

---

# EXHAUSTIVE CODE AUDIT — v4 (2026-08-18)
## Every Gap Found. Nothing Missed.

---

## A. Console.log in Production Code — Full List

Previous audit said "1 file". Actual count is **5 files**:

| File | Occurrences | Remove |
|------|-------------|--------|
| `features/tools/ToolsPage.tsx` | 3x `console.log` | Delete all 3 |
| `features/playground/PlaygroundPage.tsx` | 1x `console.log` | Delete |
| `features/goals/GoalDNAPage.tsx` | 2x `console.log` | Delete both |
| `components/ui/RouteErrorBoundary.tsx` | 1x `console.error` | Replace with structlog/toast |
| `components/ui/ErrorBoundary.tsx` | 1x `console.error` | Replace with error reporting |

---

## B. ErrorBoundary Missing — 87/87 Pages

**Every single Page.tsx lacks an `<ErrorBoundary>` wrapper.**  
`RouteErrorBoundary` is in the router config, but individual pages have no boundary — meaning an error in any section crashes the whole page.

**Pattern to apply:**
```tsx
// In each Page.tsx — wrap each SECTION, not the whole page:
import { ErrorBoundary } from '@/components/ui/ErrorBoundary';

// BEFORE:
return (
  <JARVISPageShell>
    <KpiSection />
    <ActivityFeed />
    <AgentGrid />
  </JARVISPageShell>
);

// AFTER:
return (
  <JARVISPageShell>
    <ErrorBoundary name="KpiSection">
      <KpiSection />
    </ErrorBoundary>
    <ErrorBoundary name="ActivityFeed">
      <ActivityFeed />
    </ErrorBoundary>
    <ErrorBoundary name="AgentGrid">
      <AgentGrid />
    </ErrorBoundary>
  </JARVISPageShell>
);
```

**High priority pages** (complex, most likely to throw):
```
features/goals/GoalDetailPage.tsx       — 1183 lines, multiple async sections
features/governance/GovernancePage.tsx  — 1682 lines, hash chain logic
features/observability/CostDashboardPage.tsx — 1342 lines, D3 charts
features/observability/ObservabilityPage.tsx — 1336 lines, live traces
features/workflow-builder/WorkflowBuilderPage.tsx — 1323 lines, canvas
features/dashboard/DashboardPage.tsx    — SSE + D3 + multiple queries
```

---

## C. TypeScript `any` — Full Count Per File

Previous audit said "32 files". Full breakdown with counts:

| File | `any` count | Priority |
|------|------------|----------|
| `features/goals/GoalDetailPage.tsx` | **25** | P0 — most used page |
| `features/agents/AgentDetailPage.tsx` | **14** | P0 — critical resource |
| `features/dashboard/DashboardPage.tsx` | **14** | P0 — home page |
| `features/settings/SettingsPage.tsx` | **15** | P1 |
| `features/dashboard/AIOpsDashboard.tsx` | **11** | P0 — dashboard core |
| `lib/api/client.ts` | **11** | P0 — affects all API calls |
| `features/dashboard/components/AgentOrbitView.tsx` | **8** | P0 — D3 component |
| `features/knowledge-graph/GraphExplorerPage.tsx` | **8** | P1 |
| `features/goals/GoalsListPage.tsx` | **5** | P0 |
| `features/models/ModelControlCenter.tsx` | **7** | P1 |
| `features/civilization/MembersPanel.tsx` | **6** | P2 |
| `features/settings/BillingPage.tsx` | **3** | P1 |
| `features/connectors/ConnectorsRegisteredPage.tsx` | **3** | P1 |

**Fix pattern:**
```tsx
// BEFORE:
const [data, setData] = useState<any>(null);
const result = response as any;

// AFTER:
interface AgentData { id: string; name: string; status: AgentStatus }
const [data, setData] = useState<AgentData | null>(null);
const result = response as AgentData;  // with Zod validation at boundary
```

---

## D. Inline Fetch in useEffect — Full List with Counts

| File | Fetch calls | Convert to |
|------|------------|-----------|
| `features/settings/BudgetManagerPage.tsx` | **7** | `useQuery` (multiple queries) |
| `features/chat/AgentMemoryPage.tsx` | 4 | `useQuery` |
| `features/goals/GoalDNAPage.tsx` | 3 | `useQuery` |
| `features/chat/ConnectedServicesPanel.tsx` | 3 | `useQuery` |
| `features/observability/ObservabilityPage.tsx` | 3 | `useQuery` |
| `lib/api/client.ts` | 4 | Already correct — these are the base fetch wrappers |
| `features/auth/AuthPage.tsx` | 2 | Acceptable — auth flow special case |
| `features/auth/SSOCallbackPage.tsx` | 2 | Acceptable — OAuth callback |
| `lib/sse/useGoalStream.ts` | 1 | Keep — SSE requires raw EventSource |
| `lib/sse/useEventStream.ts` | 1 | Keep — SSE requires raw EventSource |
| `lib/sse/useCivilizationStream.ts` | 1 | Keep — SSE |
| `features/eval/EvalPage.tsx` | 2 | `useQuery` |
| `features/org/components/GraphifyProgress.tsx` | 1 | `useQuery` or SSE |
| `features/simulation/SimulationPage.tsx` | 1 | `useQuery` |
| `features/agents/AgentIdentityPage.tsx` | 1 | `useQuery` |
| `app/App.tsx` | 1 | `useQuery` (health check) |

**Convert priority:** BudgetManagerPage (7!) → AgentMemoryPage (4) → GoalDNAPage (3)

---

## E. Page Complexity Debt — Files Over 1000 Lines (Need Splitting)

These monolithic pages violate the 300-line rule. They make debugging, testing, and animation additions extremely difficult:

| File | Lines | Split Into |
|------|-------|-----------|
| `features/governance/GovernancePage.tsx` | **1682** | `AuditTimeline`, `HashChainPanel`, `DiffViewer`, `GovernanceFilters` |
| `features/workflow-builder/WorkflowBuilderPage.tsx` | **1323** | `StepPalette`, `BuilderCanvas`, `StepConfigPanel`, `BuilderToolbar` |
| `features/observability/CostDashboardPage.tsx` | **1342** | `CostGauges`, `CostTreemap`, `CostTimeline`, `TopCostGoals` |
| `features/observability/ObservabilityPage.tsx` | **1336** | `ServiceMap`, `TraceList`, `TraceWaterfall`, `MetricsPanel` |
| `features/playground/PlaygroundPage.tsx` | **1056** | `PromptEditor`, `ResponsePanel`, `ComparePanel`, `HistorySidebar` |
| `features/settings/SettingsPage.tsx` | **988** | `GeneralSettings`, `SecuritySettings`, `NotifSettings`, `AdvancedSettings` |
| `features/settings/BudgetManagerPage.tsx` | **995** | `BudgetGauges`, `BudgetRules`, `AlertHistory`, `CostBreakdown` |
| `features/collaboration/CollaborationPage.tsx` | **1030** | `CollabCanvas`, `PresenceBar`, `CollabChat` |
| `features/goals/GoalDetailPage.tsx` | **1183** | `PlanTimeline`, `StepOutputList`, `GoalMetrics`, `GoalActions` |
| `features/knowledge/KnowledgePage.tsx` | **1121** | `CollectionGrid`, `DocBrowser`, `ChunkViewer`, `SemanticSearch` |
| `features/eval/EvalPage.tsx` | **1119** | `EvalRunList`, `EvalScorePanel`, `EvalComparison` |
| `features/lab/AgentLabPage.tsx` | **1060** | `PromptPanel`, `ResponsePanel`, `ModelSelector`, `HistoryPanel` |

**Splitting each monolith:**
1. Extract sub-sections into `components/` directory
2. Each component gets its own animation (easier to add motion to 100-line components)
3. Each component gets its own test file
4. Page becomes a simple layout shell: `<Section1 /> <Section2 /> <Section3 />`

---

## F. i18n Missing — 40/87 Pages Not Translated

40 pages use hardcoded English strings. Full list:

```
features/agents/AgentDashboardPage.tsx
features/agents/AgentPersonalityPage.tsx
features/agents/AgentRadarPage.tsx
features/analytics/SelfImprovementPage.tsx
features/approvals/ApprovalsPage.tsx
features/auth/AuthPage.tsx
features/auth/MFAVerifyPage.tsx
features/channels/ChannelMappingsPage.tsx
features/civilization/CivilizationPage.tsx
features/connectors/ConnectorsCatalogPage.tsx
features/coordination/CoordinationRunPage.tsx
features/domains/DomainDetailPage.tsx
features/domains/DomainsPage.tsx
features/errors/NotFoundPage.tsx
features/gateway/GatewaySettingsPage.tsx
features/goals/GhostRunPage.tsx
features/goals/GoalDiffPage.tsx
features/goals/GoalDNAPage.tsx
features/integrations/IntegrationsPage.tsx
features/knowledge-graph/GraphExplorerPage.tsx
features/landing/LandingPage.tsx
features/marketplace/MarketplacePage.tsx  (partial)
features/models/ModelControlCenter.tsx
features/notifications/NotificationCenterPage.tsx
features/ocr/OcrPage.tsx
features/onboarding/OnboardingPage.tsx
features/org/OrgListPage.tsx
features/org/StrategicAdvisorPage.tsx
features/perception/PerceptionPage.tsx
features/playground/PlaygroundPage.tsx
features/rbac/RbacPage.tsx
features/rpa/RpaLivePage.tsx
features/security/SecurityCenterPage.tsx
features/simulation/SimulationPage.tsx
features/skills/SkillsPage.tsx
features/state-machines/StateMachinesPage.tsx
features/status/StatusPage.tsx
features/templates/TemplateLibraryPage.tsx
features/training/TrainingExportPage.tsx
features/workflow-builder/WorkflowBuilderPage.tsx
```

**Fix pattern (same for all 40):**
```tsx
// BEFORE:
<h1>Agents</h1>
<p>Manage your AI agents</p>

// AFTER:
import { useTranslation } from 'react-i18next';
const { t } = useTranslation('agents');
<h1>{t('agents.title')}</h1>
<p>{t('agents.description')}</p>
```

---

## G. Inline Styles — Replace with Tailwind

Components with high inline style counts (Tailwind equivalent needed):

| File | Inline styles | Impact |
|------|--------------|--------|
| `features/civilization/CivilizationPage.tsx` | **15** | Complex D3 — keep D3 styles, move others to Tailwind |
| `features/civilization/MembersPanel.tsx` | **11** | Move to Tailwind classes |
| `features/org/OrgPage.tsx` | **16** | Move to Tailwind, keep motion style props |
| `features/landing/LandingPage.tsx` | **10** | Move to Tailwind |
| `features/workflow-builder/WorkflowBuilderPage.tsx` | **9** | Keep canvas/flow styles, move others |
| `features/civilization/AgentInspectorDrawer.tsx` | **9** | Move to Tailwind |
| `features/org/components/ObsidianVaultExplorer.tsx` | **8** | Move to Tailwind |
| `features/settings/RoleEditorPage.tsx` | **8** | Move to Tailwind |

**Exception:** D3 visualization and `motion.div` style props for dynamic values are valid. Only static color/spacing/font inline styles need to move to Tailwind.

---

## H. Toaster.tsx — NO Animation (Toast = Key Agentic Feedback)

`components/ui/Toaster.tsx` — 94 lines, **zero animation**.

Every success/error/info toast appears and disappears instantly. This is a critical UX gap — toast is how the system communicates "goal completed", "agent deployed", "error occurred".

**What it needs:**
```tsx
// src/components/ui/Toaster.tsx — full replacement

import { motion, AnimatePresence } from 'framer-motion';
import { useReducedMotion } from 'framer-motion';
import { useToastStore } from '@/stores/toast';
import { StatusOrb } from './StatusOrb';

const TOAST_SPRING = { type: 'spring', stiffness: 450, damping: 18 } as const;
const TOAST_EXIT = { duration: 0.18 } as const;

const TOAST_COLORS: Record<string, string> = {
  success: '#10B981',  error: '#EF4444',
  warning: '#F59E0B',  info:  '#00D4FF',
};

export function Toaster() {
  const reduce = useReducedMotion();
  const { toasts, dismiss } = useToastStore();

  return (
    <div
      aria-live="polite"
      aria-atomic="false"
      className="fixed bottom-4 right-4 z-[200] flex flex-col gap-2 items-end"
    >
      <AnimatePresence mode="popLayout">
        {toasts.map(toast => (
          <motion.div
            key={toast.id}
            layout
            initial={reduce ? { opacity: 0 } : { opacity: 0, y: 32, scale: 0.92 }}
            animate={reduce ? { opacity: 1 } : { opacity: 1, y: 0,  scale: 1 }}
            exit={reduce  ? { opacity: 0 } : { opacity: 0, y: 12, scale: 0.96 }}
            transition={reduce ? TOAST_EXIT : TOAST_SPRING}
            className="flex items-center gap-3 px-4 py-3 rounded-xl
                       bg-[#1A1F2E] border border-white/[0.08]
                       shadow-[0_8px_32px_rgba(0,0,0,0.5)]
                       min-w-[280px] max-w-[420px] cursor-pointer
                       hover:border-white/[0.14] transition-colors"
            style={{
              borderLeft: `3px solid ${TOAST_COLORS[toast.kind] ?? TOAST_COLORS.info}`
            }}
            onClick={() => dismiss(toast.id)}
            role="status"
            aria-label={`${toast.kind}: ${toast.message}`}
          >
            <StatusOrb status={toast.kind === 'success' ? 'completed' :
                               toast.kind === 'error'   ? 'failed' :
                               toast.kind === 'warning' ? 'pending' : 'idle'}
                       size={8} />
            <p className="text-sm text-[#F1F5F9] flex-1 leading-snug">
              {toast.message}
            </p>
            {toast.action && (
              <button
                onClick={e => { e.stopPropagation(); toast.action!.fn(); dismiss(toast.id); }}
                className="text-xs text-[#00D4FF] hover:text-white transition-colors shrink-0 font-medium">
                {toast.action.label}
              </button>
            )}
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  );
}
```

---

## I. WorkflowBuilderPage — whileHover/whileTap Without Spring

`features/workflow/WorkflowBuilderPage.tsx` has hover/tap animations but no spring physics — using CSS `duration` instead.

```tsx
// FIND and REPLACE in WorkflowBuilderPage.tsx:
// ANY occurrence of: transition={{ duration: ... }}
// WITH:              transition={{ type: 'spring', stiffness: 280, damping: 26 }}

// FIND: whileTap={{ scale: X }} with duration transition
// REPLACE with: whileTap={{ scale: X }} transition={{ type: 'spring', stiffness: 600, damping: 35 }}
```

---

## J. Missing hooks/ Directory — Feature-by-Feature Plan

46/55 features have NO hooks directory. Business logic is entangled in Page components.

**Priority hooks to extract (grouped by feature):**

### Goals Feature
```
features/goals/hooks/useGoals.ts          — list with cursor pagination
features/goals/hooks/useGoal.ts           — single goal detail
features/goals/hooks/useGoalStream.ts     — SSE streaming (move from lib/sse)
features/goals/hooks/useGoalActions.ts    — cancel, retry, fork mutations
features/goals/hooks/useGoalCost.ts       — real-time cost tracking
```

### Agents Feature
```
features/agents/hooks/useAgents.ts        — list with filters
features/agents/hooks/useAgent.ts         — single agent CRUD
features/agents/hooks/useAgentStatus.ts   — real-time status polling
features/agents/hooks/useAgentConfig.ts   — config mutation with optimistic
```

### Workflows Feature
```
features/workflow/hooks/useWorkflows.ts   — list with search/filter
features/workflow/hooks/useWorkflowRun.ts — run lifecycle + SSE
features/workflow/hooks/useHITL.ts        — approval queue management
```

### Knowledge Feature
```
features/knowledge/hooks/useCollections.ts  — collection CRUD
features/knowledge/hooks/useDocuments.ts    — document management
features/knowledge/hooks/useSearch.ts       — semantic search with debounce
```

### Common (All Features)
```
hooks/usePagination.ts     — cursor pagination (used by every list)
hooks/useSSE.ts            — generalized SSE hook with reconnect
hooks/useCopyToClipboard.ts — copy with toast feedback
hooks/useKeyboardShortcut.ts — Cmd+K, Escape, etc.
```

---

## K. Skeleton Missing — 8 Pages with useQuery and No Loading State

Pages that call APIs but show nothing during loading:

| File | API calls | Add |
|------|-----------|-----|
| `features/domains/DomainsPage.tsx` | Yes | Domain tree skeleton |
| `features/gateway/GatewaySettingsPage.tsx` | Yes | Route table skeleton |
| `features/goals/GhostRunPage.tsx` | Yes | Replay skeleton |
| `features/integrations/IntegrationsPage.tsx` | Yes | Integration card skeleton |
| `features/playground/PlaygroundPage.tsx` | Yes | Editor skeleton |
| `features/settings/BillingPage.tsx` | Yes | Gauge skeleton |
| `features/settings/RoleEditorPage.tsx` | Yes | Matrix skeleton |
| `features/workflow-builder/WorkflowBuilderPage.tsx` | Yes | Canvas skeleton |

**Pattern:**
```tsx
if (isLoading) return (
  <JARVISPageShell>
    <div className="grid grid-cols-3 gap-4 p-6">
      {Array.from({ length: 6 }).map((_, i) => (
        <div key={i} className="h-32 rounded-xl bg-[#1A1F2E] animate-pulse" />
      ))}
    </div>
  </JARVISPageShell>
);
```

---

## L. Full Implementation Priority Matrix

All findings ranked by impact × effort:

### Tier 1 — Maximum Impact, Minimum Effort (Do First)

| # | Change | Files | Effort | Impact |
|---|--------|-------|--------|--------|
| L1 | Fix `--primary` color → `#00D4FF` | 1 file, 2 lines | 5 min | ⭐⭐⭐⭐⭐ Electric everywhere |
| L2 | Toaster.tsx animate | 1 file | 30 min | ⭐⭐⭐⭐⭐ Every action has feedback |
| L3 | LiveActivityStream animate | 1 file | 45 min | ⭐⭐⭐⭐⭐ "Bots working" feel |
| L4 | Create StatusOrb component | 1 new file | 20 min | ⭐⭐⭐⭐⭐ Unblocks all orb usage |
| L5 | Sidebar spring collapse + nav | 1 file | 1h | ⭐⭐⭐⭐⭐ Every page feels alive |
| L6 | MissionGoalComposer focus glow | 1 file | 45 min | ⭐⭐⭐⭐⭐ Most-used interaction |
| L7 | Delete 8 console.log calls | 5 files | 5 min | ⭐⭐⭐⭐ Prod cleanliness |
| L8 | ConfirmModal animate | 1 file | 30 min | ⭐⭐⭐⭐ Every destructive action |
| L9 | TopBar search AnimatePresence | 1 file | 30 min | ⭐⭐⭐⭐ Global search UX |
| L10 | AgentOrbitView electric color + glow | 1 file | 1h | ⭐⭐⭐⭐ D3 nodes go electric |

### Tier 2 — High Impact, Moderate Effort

| # | Change | Files | Effort |
|---|--------|-------|--------|
| L11 | JARVISStagger on 20 shell-only pages | 20 files | 10h |
| L12 | Add ErrorBoundary to key sections | 10 priority pages | 4h |
| L13 | Fix GoalDetailPage (25 `any`, no ErrorBoundary) | 1 file | 3h |
| L14 | Fix AgentDetailPage (14 `any`) | 1 file | 2h |
| L15 | Skeleton loading for 8 pages | 8 files | 4h |
| L16 | Hover glow on all clickable cards | ~50 files | 8h |
| L17 | GoalOutcomeHero / result panels animate | 4 files | 2h |
| L18 | WorkflowBuilderPage: spring physics | 1 file | 1h |

### Tier 3 — Structural (High Effort, Long-Term)

| # | Change | Effort |
|---|--------|--------|
| L19 | Split 12 monolith pages (1000+ lines) | 40h |
| L20 | 48 mutations → optimistic updates | 20h |
| L21 | 22 inline fetches → TanStack Query | 15h |
| L22 | 40 pages → add i18n | 20h |
| L23 | Inline styles → Tailwind | 8h |
| L24 | Extract hooks to hooks/ directories | 20h |
| L25 | Virtualization on 6 high-volume lists | 6h |

---

## M. Complete Updated Definition of Done

**18 original checkboxes + 9 new from this audit = 27 total.**  
Currently passing: **1/27** (page entrance animation only).

### Visual Identity
- [ ] Primary accent = `#00D4FF` electric cyan (currently `#3B82F6`)
- [ ] Running agents/goals show sonar-ping StatusOrb
- [ ] Cards lift 2px + electric glow on hover (`hover:shadow-glow-electric`)
- [ ] Dark surfaces: `#0A0D14` → `#0F1117` → `#1A1F2E` hierarchy (CSS vars ✅)

### Motion & Animation
- [x] Page entrance: blur+y spring (87/87 pages ✅)
- [ ] Sidebar collapse/expand: spring width
- [ ] Search results: AnimatePresence dropdown
- [ ] Lists: JARVISStagger (0/87 pages)
- [ ] SSE activity feed: items slide in with springs.bouncy
- [ ] Modal/ConfirmModal: AnimatePresence spring
- [ ] **Toast/Toaster: AnimatePresence popLayout spring (0/87 action feedback)**

### Agentic Signals
- [ ] LiveActivityStream: animated, StatusOrb per event
- [ ] Goal execution: streaming typewriter + step pulse
- [ ] AgentOrbitView: electric color + SVG glow filter
- [ ] MissionGoalComposer: focus glow + intent preview
- [ ] Running agent badge on every agent card

### Code Quality
- [ ] Zero TypeScript `any` (currently 32 files — worst: GoalDetailPage=25, DashboardPage=14)
- [ ] Zero inline fetch in useEffect (currently 22 files — worst: BudgetManagerPage=7)
- [ ] All mutations have optimistic updates (48 missing)
- [ ] All high-volume lists virtualized (6 missing)
- [ ] Zero console.log in prod (currently 5 files)

### Architecture
- [ ] **No monolith pages over 1000 lines (12 violating — worst: GovernancePage=1682)**
- [ ] **All pages have section-level ErrorBoundary (0/87 currently)**
- [ ] **40 pages missing i18n — all user-facing strings use t()**
- [ ] **Inline styles replaced with Tailwind where applicable**
- [ ] Hooks extracted to feature hooks/ directories

---

## N. Files to Create / Modify — Complete Ordered List

```
PHASE 0 — Foundation (unblocks everything):
  MODIFY: src/app/globals.css (--primary: 189 100% 42%)
  MODIFY: tailwind.config.js (jarvis.electric, glow-electric shadow)
  CREATE: src/lib/design/tokens.ts
  CREATE: src/lib/design/motion.ts
  CREATE: src/components/ui/StatusOrb.tsx
  MODIFY: src/components/ui/EmptyState.tsx (animate)
  MODIFY: src/components/ui/Toaster.tsx (AnimatePresence + StatusOrb)
  MODIFY: src/components/ui/ConfirmModal.tsx (backdrop + modal spring)
  MODIFY: src/components/ui/Sidebar.tsx (spring collapse + nav hover + layoutId)
  MODIFY: src/components/ui/TopBar.tsx (search AnimatePresence + badge bounce)

PHASE 1 — Dashboard Core (most visible):
  MODIFY: src/features/dashboard/components/LiveActivityStream.tsx
  MODIFY: src/features/dashboard/components/AgentOrbitView.tsx
  MODIFY: src/features/goals/components/MissionGoalComposer.tsx
  MODIFY: src/features/goals/components/GoalOutcomeHero.tsx
  MODIFY: src/features/goals/components/GoalResultCanvas.tsx
  MODIFY: src/features/goals/components/GoalEvidencePanel.tsx
  MODIFY: src/features/goals/components/GoalExplainPanel.tsx

PHASE 2 — Pages with JARVISStagger (20 shell-only pages):
  MODIFY: 20 pages (see Step 1 list in Phase 1 section above)

PHASE 3 — Hover glow + StatusOrb replace:
  MODIFY: All clickable cards (~50 files) — add hover:shadow-glow-electric
  MODIFY: All status dot patterns — replace with <StatusOrb>

PHASE 4 — ErrorBoundary + Skeleton:
  MODIFY: 10 priority pages — add section-level ErrorBoundary
  MODIFY: 8 pages — add Skeleton loading states

PHASE 5 — Fix monoliths (split into components):
  SPLIT:  12 pages over 1000 lines into component files

PHASE 6 — Code quality:
  FIX:    5 files with console.log
  FIX:    32 files TypeScript any (prioritize: GoalDetailPage, DashboardPage)
  FIX:    22 inline fetches → useQuery
  ADD:    48 mutations → onMutate optimistic updates
  ADD:    6 lists → useVirtualizer
  ADD:    40 pages → useTranslation
  FIX:    WorkflowBuilderPage — duration → spring

PHASE 7 — Architecture:
  EXTRACT: hooks to hooks/ directories (goals, agents, workflows, knowledge)
  REPLACE: inline styles → Tailwind (civilization, org, landing, workflow-builder)
```

---

# COVERAGE GAP FILL — v5 (2026-08-18)
## 12 Features With Incomplete Spec — All Gaps Closed

---

## 1. Compliance (`CompliancePage`) — was 25% covered

**Missing:** GDPR, SOC2, PCI compliance scores + animated arc system.

```
Layout:
┌─────────────────────────────────────────────────────────────────┐
│ Compliance Score Overview                                        │
│                                                                  │
│ ┌────────────────┐  ┌────────────────┐  ┌────────────────┐     │
│ │   GDPR         │  │   SOC2         │  │   PCI-DSS      │     │
│ │ [arc: 94%]     │  │ [arc: 87%]     │  │ [arc: 78%]     │     │
│ │ ✅ Compliant    │  │ ⚠ 3 gaps       │  │ ❌ Action req.  │     │
│ └────────────────┘  └────────────────┘  └────────────────┘     │
│                                                                  │
│ Gap Analysis:                                                    │
│ ● Data retention policy   GDPR  ✅ Met                          │
│ ● Right to erasure        GDPR  ⚠ Partial                       │
│ ● Encryption at rest      PCI   ❌ Required                     │
└─────────────────────────────────────────────────────────────────┘

Animations:
• Score arc cards: JARVISStagger stagger 0.08s
• Each SVG arc: strokeDashoffset 0→circumference×(score/100), springs.slow
  Color: emerald (≥90%), amber (70-89%), rose (<70%)
• Gap row entries: listItemVariants stagger 0.04s
• ✅ status: checkmark SVG draw-in (strokeDashoffset)
• ⚠ status: amber pulse ring after mount
• ❌ status: rose left border + listItem animation
• Score change (SSE): counterVariants + arc re-animates
• "Run Compliance Check" button: JARVISButton with springs.fast
• Modal for gap details: backdropVariants + modalVariants
```

---

## 2. Schedules (`SchedulesPage`) — was 25% covered

**Missing:** calendar view, cron pattern display, next run countdown.

```
Layout — 3 views (toggle: List | Calendar | Timeline):

Calendar View:
┌──────────────────────────────────────────────────────────────────┐
│ [Month navigation ← August 2026 →]                               │
│                                                                  │
│  Mon  Tue  Wed  Thu  Fri  Sat  Sun                              │
│   1    2    3    4    5    6    7                                │
│  [●]        [●●]     [●]                                         │
│   8    9   10   11   12   13   14                               │
│  [●●●]      [●]                                                  │
└──────────────────────────────────────────────────────────────────┘

List View:
│ Daily Report  Every 9am  → ReportAgent  ● Active  Next: 2h 14m  │
│ Weekly Audit  Mon 8am    → AuditAgent   ● Paused  [Resume]       │
│ Cron: 0 9 * * *  (human-readable: "9:00 AM every day")          │

Animations:
• Calendar month transition: AnimatePresence x:±30→0 springs.page
• Schedule dots on calendar dates: StatusOrb size=6, stagger appear
• Active dot: pulseVariants.active (electric for enabled, gray for paused)
• Toggle active: StatusOrb transitions springs.fast (emerald↔gray)
• List rows: listItemVariants stagger 0.04s
• "Next run" countdown: live decrement animation (AnimatePresence counterVariants)
• Cron expression: monospace with electric text color, tooltip on hover
• Pause/Resume action: JARVISButton, toast feedback
• New schedule drawer: drawerVariants from bottom
• Calendar hover day: surface4 highlight + scale 1.02 springs.gentle
```

---

## 3. Lab (`AgentLabPage`) — was 33% covered

**Missing:** prompt editor panel, compare mode, history sidebar.

```
Layout (IDE-style, split pane):
┌────────────────────────────────┬──────────────────────────────────┐
│ Prompt Editor (left 50%)       │ Response (right 50%)             │
│ ─────────────────────────────  │ ──────────────────────────────── │
│ System: [editable textarea]    │ [Streaming response]             │
│ User:   [editable textarea]    │ Tokens: 1,234 (count-up)         │
│                                │ Cost: $0.0023 (count-up)         │
│ Model: [selector ▾]            │ Latency: 1,204ms                 │
│ Temp:  [0-2 slider]            │                                  │
│ MaxTok:[2000 input]            │ [Compare mode ▾ ]                │
│                                │ (opens second pane, side-by-side)│
│ [▶ Run]  [Save]  [Share]       │ [Copy] [Save as Example]         │
└────────────────────────────────┴──────────────────────────────────┘

Prompt History (slide-in from left when open):
  [History] ← toggle                        Session A (8 runs)
  ─────────────────────────────────────────  run #1: "Research..."
                                             run #2: "Analyze..."

Animations:
• Run button: JARVISButton, spinner during execution, springs.fast press
• Streaming response: character-by-character typewriter (30ms/char) with cursor blink
• Token counter: AnimatePresence counterVariants on each token chunk
• Cost counter: same — count-up as tokens arrive
• Compare mode: AnimatePresence — second pane panelVariants from right
  Both panes update side-by-side with different model responses
• Slider (temperature): whileDrag spring physics thumb, value counterVariants
• Model selector: AnimatePresence dropdown, listItemVariants per model option
• History sidebar: panelVariants from left when toggled
• Save example: brief scale 0→1.2→1 springs.bouncy + emerald flash
• Share: springs.fast, copy to clipboard + toast
• Editor focus: CSS border → electric `ring-[#00D4FF]/60`
```

---

## 4. Onboarding (`OnboardingPage`) — was 33% covered

**Missing:** wizard multi-step, stepper progress, step transitions.

```
Layout:
┌─────────────────────────────────────────────────────────────────┐
│ [Horizontal stepper — top]                                      │
│ ①━━━━━━━②━━━━━━━③━━━━━━━④                                       │
│ Setup    Agent   Goal    Invite                                  │
│                                                                  │
│ [Step Content — animated with AnimatePresence]                  │
│                                                                  │
│ Step 2: Create Your First Agent                                 │
│ ┌────────────────────────────────────────────────────────┐     │
│ │ [Agent templates — choose one]                         │     │
│ │  🔍 Research  |  💻 Code  |  📊 Analysis  |  ⚙ Custom │     │
│ │  [selected: electric border, scale 1.03]               │     │
│ │                                                        │     │
│ │  Name: [input]    Model: [selector]                   │     │
│ └────────────────────────────────────────────────────────┘     │
│                                                                  │
│ [◀ Back]                           [Continue ▶]                 │
│                                                                  │
│ [Right: Live preview of configured agent — updates as you type] │
└─────────────────────────────────────────────────────────────────┘

Animations:
• Step entry (forward): content x:-40→0, opacity 0→1, springs.page
• Step entry (back): x:40→0, opacity 0→1, springs.page
• Stepper progress: electric fill advances with springs.standard
• Stepper dot active: scale 1→1.3→1 springs.bouncy + electric ring
• Template card selection: scale 1.03, border.active, ring checkmark in springs.snappy
• Name input focus: border → electric ring, shadow.glow
• Live preview agent: panelVariants from right, updates with 300ms debounce
• "Continue" button: JARVISButton, disabled state opacity 0.4
• Final step (Step 4): confetti animation + ✅ scale 0→1.3→1 springs.bouncy
• Welcome screen: pageVariants with "You're ready" message
```

---

## 5. Org Feature — Missing: CommandCenter, MorningBrief, DigitalTwin

### CommandCenter Component (within OrgPage)

```
This is the central mission input area in OrgPage:
┌─────────────────────────────────────────────────────────────────┐
│ ✨ What should your AI org accomplish today?                    │
│ [goal input bar — full width, centered]                          │
│ Suggested: "Generate Q3 report" | "Monitor GitHub PRs"          │
│ [Voice 🎙]  [Template 📋]  [Launch ⚡]                          │
└─────────────────────────────────────────────────────────────────┘

Animations:
• Input focus: border glow electric + shadow.glowStrong springs.page
• Placeholder rotation: typewriter fade-in of 4 rotating suggestions (4s cycle)
• Suggestion pill hover: scale 1.03, border.glow springs.gentle
• Voice button: pulse ring while recording (rose, 1.5s loop)
• Launch button: JARVISButton, springs.bouncy, spinner → ✅
• Intent preview: AnimatePresence y:-8→0 as you type (debounced 300ms)
  "I'll use: ResearchAgent + web_search + synthesize"
```

### MorningBrief Component (within OrgPage)

```
Layout — dismissable banner at top of OrgPage:
┌─────────────────────────────────────────────────────────────────┐
│ 🌅 Good morning. 3 goals completed overnight. 1 approval waiting.│
│ [View Goals ↗]  [Review Approval ↗]  [Dismiss ✕]               │
└─────────────────────────────────────────────────────────────────┘

Animations:
• Entry: y:-48→0, springs.page with 0.3s delay (loads after page)
• Dismiss: y:-48→exit, opacity→0, AnimatePresence
• CTA buttons: JARVISButton springs.snappy
• Approval count badge: springs.bouncy appear
```

### DigitalTwinPanel Component (within OrgPage)

```
Layout — right panel showing org simulation state:
┌──────────────────────────────────────────────────────────────────┐
│ Digital Twin  [Simulate ▶]  [Reset ↺]                           │
│ ──────────────────────────────────────────────────────────────   │
│ Predicted outcomes:                                             │
│ If you deploy this: Cost +$45/day | Success rate: 94%           │
│ [Particle simulation — agent nodes moving]                      │
└──────────────────────────────────────────────────────────────────┘

Animations:
• Particle simulation: D3-force layout, nodes move with spring physics
• Prediction values: counterVariants on update
• [Simulate] click: brief loading state, then particles animate to new state
• Success/risk color: emerald (high success) → amber → rose (high risk)
```

---

## 6. Analytics — Missing: Cost Trend, Score Trend Animations

```
AnalyticsDashboardPage:

Cost Trend Chart (D3 area):
• Initial render: path draw-in left→right (700ms, cubic-bezier)
• New data point (live): smooth path morph via D3 transition (400ms)
• Hover: vertical crosshair line tracks mouse x-position with springs.gentle
• Tooltip: modalVariants scale-in at cursor
• Time period switch (7d/30d/90d): chart fades 0.3 → new data → fades in

Score Trend Chart (D3 line):
• Same animation pattern as cost trend
• Multiple lines (P50/P95/P99): each draws in with 100ms stagger
• Toggle line visibility: opacity 0↔1 springs.fast
• Regression detection: rose dot appears with springs.bouncy when score drops

SelfImprovementPage:
• Prompt version timeline: horizontal scroll, nodes springs.bouncy on mount
• Score bars: fill animation left→right springs.page
  Color progression: rose→amber→emerald as score improves
• A/B winner badge: scale 0→1.2→1 springs.bouncy + emerald glow
• Version delta: AnimatePresence counterVariants (±Δ)
```

---

## 7. Audit (`AuditExplorerPage`) — Missing: Hash Chain Verify Animation

```
Hash Chain Integrity Animation:
┌──────────────────────────────────────────────────────────────────┐
│ [Verify Integrity ▶]    Last verified: 2 hours ago   ✅ Valid     │
│                                                                   │
│ Chain visualization (horizontal):                                │
│ [Event #1] → [Event #2] → [Event #3] → [Event #4] → [Now]       │
│  ✅ hash ok   ✅ hash ok   ✅ hash ok   ✅ hash ok   ✅            │
└──────────────────────────────────────────────────────────────────┘

Animations:
• Verify button click: spinner + "Verifying chain..." text typewriter
• Chain nodes: appear sequentially left→right (stagger 0.08s)
  Each: scale 0→1 springs.snappy + ✅ checkmark draw-in
• Final node: emerald flash + "✅ Chain intact" toast (toastVariants)
• TAMPERED detection: rose flash at corrupted position + rose toast
• Audit timeline rows: listItemVariants stagger 0.035s
• Security event rows: rose left border + rose glow on hover
• New SSE event: y:-16→0 springs.bouncy + highlight pulse for 2s
• Expand row: AnimatePresence height 0→auto springs.page
  Content: stagger reveal of fields 0.03s each
• Filter bar results: AnimatePresence fade per row change
```

---

## 8. Governance (`GovernancePage`) — Missing: Audit Timeline, Diff Viewer

```
Audit Timeline (immutable, virtualized):
┌──────────────────────────────────────────────────────────────────┐
│ ●──────────────────────────────────────────────────── (time axis)│
│                                                                   │
│ 14:23:11  🔑 api_key.created   ak_pro_xyz  ✅  [▶ Details]      │
│ 14:20:03  🎯 goal.completed    goal:abc    ✅  [▶ Details]       │
│ 14:15:44  🛑 jailbreak.detect  goal:def    ❌  [▶ Details]       │
└──────────────────────────────────────────────────────────────────┘

Diff Viewer (side-by-side, for config changes):
┌──────────────────────────────────────────────────────────────────┐
│ Agent config changed by admin                                    │
│ ┌──────────────────────┬──────────────────────────────────────┐  │
│ │ Before               │ After                                │  │
│ │ model: claude-haiku  │ model: gpt-4o                        │  │
│ │ temperature: 0.1     │ temperature: 0.7                     │  │
│ │ [removed: rose]      │ [added: emerald]                     │  │
│ └──────────────────────┴──────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘

Animations:
• Timeline rows: listItemVariants stagger 0.035s (virtualized)
• New SSE event: y:-16→0 springs.bouncy + event-type color pulse
  🛑 security events: rose border + rose glow on hover
• Row [▶ Details] expand: AnimatePresence height springs.page
  Expanded content stagger 0.03s per field
• Diff viewer:
  - Two panes slide from sides simultaneously (x:±40→0 springs.page)
  - Removed lines: rose left-border + fade in with rose tint bg
  - Added lines: emerald left-border + fade in with emerald tint bg
  - Line hover: highlight strengthens, full value shown
```

---

## 9. Memory (`MemoryExplorerPage`) — Missing: Episodic, Semantic Cluster

```
Layout:
┌─────────────────────────────────────────────────────────────────┐
│ [Type Tabs]                                                     │
│ Episodic (45) | Working (3) | Long-term (89) | Procedural (12) │
│                                                                  │
│ [Episodic Tab — horizontal timeline]                            │
│ ←──────────────────────────────────────────────────────────→   │
│ ●         ●●        ●          ●●●       ●         ●           │
│ Aug 16  Aug 17   Aug 17      Aug 18   Aug 18    Today           │
│ Goal#1  Goal#12  Session    Goal#45   Session    Now            │
│                                                                  │
│ [Memory Detail Panel — click timeline node]                     │
│ Cluster: "AI Research" — 42 memories                           │
│ Relevance: ████████░░ 82%                                       │
│ "Found that GPT-4o outperforms Claude on coding tasks..."       │
│                                                                  │
│ [Semantic Cluster Tab — D3 force graph]                         │
│ Nodes: memory items, edges: semantic similarity                 │
└─────────────────────────────────────────────────────────────────┘

Animations:
• Timeline nodes: springs.bouncy on mount, stagger left→right
• Active timeline node: scale 1.3 + electric ring pulseVariants.pulse
• Timeline zoom (scroll wheel): smooth scale springs.gentle
• Timeline click → detail panel: panelVariants from right
• Memory detail: stagger reveal of fields springs.page
• Relevance bar: fill left→right springs.page (color: emerald>80%, amber>50%)
• "Forget" action: scale 0 + rose flash springs.fast → toast
• Semantic cluster (D3 force):
  - Nodes: spring into position from center (d3-force simulation)
  - Selected node: scale 1.3 + electric ring + related nodes highlight
  - Hover node: expand to show memory preview, edges brighten
  - Cluster drag: spring physics resist then snap back
• Tab switch: AnimatePresence content fade springs.page
```

---

## 10. RPA (`RpaLivePage`) — Missing: Click Target, Step Builder, Browser Preview

```
Layout (split-pane):
┌──────────────────────────┬──────────────────────────────────────┐
│  Step Builder (35%)      │  Browser Preview (65%)               │
│  ─────────────────────   │  ┌────────────────────────────────┐  │
│  1. Open URL ✅          │  │                                │  │
│  2. Click ● (active)    │  │  [Live screenshot stream]      │  │
│  3. Type text ⏳         │  │  Click targets: ring overlay   │  │
│  4. Extract ⏳           │  │  Field outlines: electric glow │  │
│  5. Wait ⏳              │  │  Extracted: emerald highlight  │  │
│  ─────────────────────   │  └────────────────────────────────┘  │
│  [+ Add Step]            │  [Record ●] [▶ Play] [⏭ Step] [⏹]   │
│  [▶ Run] [Export]        │  [Screenshot] [Export Script]        │
└──────────────────────────┴──────────────────────────────────────┘

Step-Level Animations:
• Step list: listItemVariants stagger 0.05s
• Active step: electric left border + bg surface3 + scale 1.01
• Step completed: ✅ scale 0→1.2→1 springs.bouncy + emerald flash
• Step error: rose left border + shake x:[-4,4,-4,0]

Browser Preview Overlay Animations:
• Click target: concentric rings expand outward (scale 1→2.5→3.5) + opacity 1→0
  Spring: { stiffness: 200, damping: 20, repeat: 1 }
• Field fill: outline glows electric as text appears (typewriter effect)
• Element extraction: emerald highlight flash 0→1→0.3
• Screenshot update: brief opacity 0.8→1 on each screenshot

Recording Mode:
• Record button: rose pulse ring while recording (1.5s loop, scale 1→1.4→1)
• Auto-step creation: new step slides in from right springs.bouncy

Split pane:
• Draggable divider: cursor resize, smooth spring on drag
• Width: animated springs.gentle on drag release (snaps to grid)
```

---

## 11. Marketplace (`MarketplacePage`) — Missing: Hero Carousel

```
Hero Carousel Animation:
┌──────────────────────────────────────────────────────────────────┐
│ ┌────────────────────────────────────────────────────────────┐  │
│ │ [Featured card 1 of 3]                                    │  │
│ │ 🏆 Research & Report Agent    ★★★★★ 4.9  1,243 installs  │  │
│ │ "Multi-source research + executive report in minutes"      │  │
│ │ [Preview ▶]  [Install in 1 click]                         │  │
│ └────────────────────────────────────────────────────────────┘  │
│  ●  ○  ○  (carousel dots)                                       │
└──────────────────────────────────────────────────────────────────┘

Animations:
• Carousel slide: x:100%→0 (forward), x:-100%→0 (back), springs.page
  AnimatePresence mode="wait" for clean transitions
• Auto-advance: 5s interval, resets on user interaction
• Dot indicators: active dot scale 1.2 + electric, others opacity 0.4
• Card hover: y:-4, glowStrong shadow, border.glow springs.gentle
• Install button: JARVISButton springs.fast, spinner → ✅ "Installed!"
• Star rating: stars fill left→right 0.1s stagger on enter
• Install count badge: count-up animation on carousel enter
```

---

## 12. Skills (`SkillsPage`) — Missing: Enable Toggle Animation

```
Skill Card with Enable Toggle:
┌──────────────────────────────────────────────────────────────────┐
│ [Skill Grid]                                                     │
│ ┌──────────────────────────┐  ┌──────────────────────────┐      │
│ │ 📊 Data Analysis         │  │ 🔍 Web Research          │      │
│ │ ● Enabled (pulse)        │  │ ○ Disabled               │      │
│ │ Reliability: ████████░░  │  │ Reliability: ████████░░  │      │
│ │ Used by: 3 agents        │  │ Used by: 0 agents        │      │
│ │ [Configure] [● Enabled▾] │  │ [Enable]  [Configure]   │      │
│ └──────────────────────────┘  └──────────────────────────┘      │
└──────────────────────────────────────────────────────────────────┘

Enable Toggle Animation:
• Click Enable: JARVISButton springs.fast press
• Toggle spring: StatusOrb transitions gray→emerald springs.fast
  Simultaneously: card border transitions glass→success
• Enabled ring: emerald glow ring expands once (scale 1→2→fade)
• Disabled agent count: counterVariants on change
• Configure drawer: drawerVariants from bottom

Card Animations:
• Grid: JARVISStagger cardContainer variants, stagger 0.06s
• Card hover: y:-3, border.glow, shadow.glowStrong springs.gentle
• Card tap: scale 0.98 springs.fast
```

---

## 13. Simulation (`SimulationPage`) — Missing: Dry Run Mode

```
Simulation Mode:
┌──────────────────────────────────────────────────────────────────┐
│ [SIMULATION MODE BANNER]                                         │
│ 🧪 Simulation Mode — mock tools, no real actions                │
│ All tool calls return simulated responses                        │
│ [Exit Simulation]                                                │
│                                                                  │
│ [Same goal interface as GoalsListPage]                           │
│ → Goal input + submit (all animations same as goals)            │
│ → Results show "SIMULATED" badge on every tool output           │
│ → Cost shows "$0.00 (simulated)"                                 │
│                                                                  │
│ [Diff Viewer — compare simulated vs expected output]             │
│ Left: Simulated output  |  Right: Expected/Previous output       │
└──────────────────────────────────────────────────────────────────┘

Animations:
• Simulation banner: amber/violet gradient animated border
  Border: conic-gradient rotating 2s loop
• "SIMULATED" badge: violet color, springs.bouncy first appear per result
• Diff viewer: same pattern as GovernancePage diff viewer
• Tool output badges: violet outline, not rose/emerald (simulated data)
• Cost display: always shows "$0.00 (simulated)" in violet
• [Exit Simulation]: rose border transition, scale springs.fast
```

---

## 14. Builder (`BuilderPage`) — Missing: Node Canvas Animations

```
Visual Agent Builder (node canvas):
┌────────────────────────────────────────────────────────────────┐
│ [Toolbar: Save | Test | Deploy | +Node | Auto-Layout]          │
├──────────────────────────────────────────────────────────────────┤
│ [Node Palette — left 280px]  │  [Canvas — @xyflow/react]       │
│ ─────────────────────────    │                                  │
│ 🎯 Goal Node                  │  ● START ─→ [AgentStep] ─→    │
│ 🔧 Tool Node                  │            ─→ [ToolStep] ─→    │
│ 🤖 Agent Step                 │            ─→ [HITL] ─→ END    │
│ 📋 Condition                  │                                  │
│ 🔁 Loop Node                  │  Connection: particle flow ✦✦✦ │
│ ✋ HITL Step                   │  Selected: electric border      │
│                              │  Running: electric pulse         │
└──────────────────────────────┴──────────────────────────────────┘

Animations:
• Node palette items: listItemVariants stagger 0.03s
  Hover: scale 1.05, border.glow springs.gentle
• Drag from palette → canvas: springs.bouncy drop with scale 0.8→1.0
• Connection edge: animated particle flow along edge direction
  Particles: small dots travel from source to target (CSS stroke-dashoffset)
  Active connection: particles brighter, faster
  Idle connection: slow drift
• Selected node:
  - border color: transparent → electric (#00D4FF) springs.fast
  - scale: 1→1.02 springs.gentle
  - control handles: appear with springs.bouncy
• Node delete: scale 0 + fade springs.fast
• Auto-layout: nodes animate to new positions stagger springs.page
• Test run: nodes highlight in execution sequence
  Running node: pulseVariants.pulse electric ring
  Completed node: emerald flash + ✅
• Undo/redo: brief scale pulse on affected nodes
```

---

## 15. Notifications (`NotificationCenterPage`) — Missing: SSE Notification

```
SSE Real-Time Notification:
┌──────────────────────────────────────────────────────────────────┐
│ 🔔 Notifications (3 unread)    [Mark all read]   [Settings]     │
│                                                                   │
│ [NEW — SSE driven]                                              │
│ ┌──────────────────────────────────────────────────────────┐    │
│ │ ⚡ [electric]  Goal Completed            2m ago  ●       │    │
│ │ "Research AI trends" completed            [View Result]  │    │
│ └──────────────────────────────────────────────────────────┘    │
│ ┌──────────────────────────────────────────────────────────┐    │
│ │ ✋ [amber]  Approval Required             5m ago  ●       │    │
│ │ "Deploy to production" needs review       [Approve][❌]  │    │
│ └──────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────┘

SSE New Notification Animation (critical):
• Arrive: y:-16→0, scale 0.93→1, opacity 0→1, springs.bouncy
  Duration: 300ms — snappy arrival
• Electric left border flash: 0→1→0.5 opacity (2s settle)
• Bell icon badge: AnimatePresence counterVariants (number springs.bouncy)
• Type-specific glow: left 3px border color per type
  success/complete → emerald, approval/warning → amber, error → rose, info → electric
• Mark read: opacity 1→0.6, ● dot scale 0, border fades springs.fast
• Mark all read: cascade stagger 0.04s per item (left→right opacity animation)
• Hover row: bg surface4, action buttons slide up (y:6→0 springs.fast)
• Action buttons in notification (Approve/Reject inline):
  Approve: JARVISButton emerald, click → item exits right
  Reject: JARVISButton rose, click → item exits left
• Load more: listItemVariants stagger on append (pagination)
```

---

## 16. Landing (`LandingPage`) — Missing: Constellation, Scroll Animations

```
Hero Constellation (the signature visual):
• 12 agent nodes arranged in constellation pattern
• SVG circles with electric color (#00D4FF)
• Each node: gentle float animation (y ±6px, 3-5s cycle, staggered start)
• Connecting lines: animated stroke-dashoffset flowing between nodes
  Direction: random, gives "data flowing" appearance
• Hover node: scale 1.2, glow ring expands, tooltip with agent type
• Page load: nodes appear stagger (scale 0→1, springs.bouncy, 0.1s stagger)

Scroll-Triggered Feature Sections:
• IntersectionObserver threshold 0.2 → triggers JARVISStagger
• Section 1 (Live demo): JARVISStagger slides content in from bottom
  Auto-typing goal input → simulated response with typewriter
• Section 2 (Providers): CSS infinite marquee (provider logos)
  Pause on hover, resume on mouse leave
• Section 3 (Pricing): cardVariants stagger on scroll-in
• Section 4 (Testimonials): horizontal carousel, autoplay 6s

Hero Text:
• Title: blur 8px→0px + y 20→0 springs.cinematic (0.2s delay)
• Subtitle: same, 0.4s delay
• CTAs: springs.bouncy enter, 0.6s delay
  hover: y:-3, glow electric springs.gentle

Scroll indicator (↓):
• Gentle bounce animation (y 0→8→0, 1.5s loop)
• Fades out after user scrolls 200px
```

---

## Final Spec Verification — All 37 Features Covered

```
✅ goals              — GoalsListPage, GoalDetailPage, GoalDNA, GoalDiff,
                        GhostRun, GoalOutcomeHero, GoalResultCanvas,
                        GoalEvidencePanel, GoalExplainPanel,
                        MissionGoalComposer (streaming, step pulse, focus glow)

✅ org               — OrgPage, OrgListPage, StrategicAdvisorPage,
                        CommandCenter, MorningBrief, DigitalTwinPanel,
                        GraphifyProgress, ObsidianVaultExplorer

✅ workflow          — WorkflowListPage, WorkflowRunDetailPage,
                        WorkflowAnalyticsPage, WorkflowBuilderPage,
                        WorkflowMarketplacePage, WorkflowRunsPage,
                        ApprovalInboxPage, WorkflowSettingsPage, HITL

✅ ingestion         — SourcesPage, SourceCard, SourceList,
                        SourceDetailDrawer, drop zone, progress queue

✅ triggers          — TriggersPage, TriggerList, TriggerCreateModal

✅ guardrails        — GuardrailCenterPage, rule cards, rule toggle,
                        toggle animation, trigger count counterVariants

✅ governance        — GovernancePage, audit timeline, hash chain verify,
                        diff viewer, AuditExplorerPage

✅ agents            — AgentsListPage, AgentDetailPage, AgentCreatePage,
                        AgentRadarPage (D3 radar), AgentDashboardPage,
                        AgentIdentityPage, AgentPersonalityPage,
                        AgentOrbitView (D3 electric glow)

✅ knowledge         — KnowledgePage, GraphExplorerPage (D3 glowing nodes),
                        semantic search, collection CRUD

✅ dashboard         — DashboardPage, LiveActivityStream (SSE animated),
                        AgentOrbitView, AIOpsDashboard, KPI cards, StatusOrb

✅ analytics         — AnalyticsDashboardPage (D3 live charts),
                        SelfImprovementPage (prompt versions, A/B winner)

✅ approvals         — ApprovalsPage, ApprovalInboxPage, HITL queue,
                        approve/reject animations

✅ audit             — AuditExplorerPage, hash chain verify animation,
                        timeline stagger, security event rows

✅ compliance        — CompliancePage, GDPR/SOC2/PCI arcs, gap analysis

✅ memory            — MemoryExplorerPage, episodic timeline, semantic cluster D3

✅ observability     — ObservabilityPage (service map, trace waterfall),
                        CostDashboardPage (treemap, area chart)

✅ marketplace       — MarketplacePage, hero carousel, template cards

✅ connectors        — ConnectorsCatalogPage, ConnectorsRegisteredPage,
                        ConnectorDetailPage, OAuthCallbackPage

✅ settings          — SettingsPage, BillingPage, BudgetManagerPage,
                        GuardrailCenterPage, RoleEditorPage, ScopeExplorerPage

✅ shared layout     — Sidebar (spring collapse, nav hover, layoutId),
                        TopBar (search AnimatePresence, notification badge),
                        ConfirmModal (backdrop + modal spring),
                        Toaster (AnimatePresence popLayout + StatusOrb),
                        EmptyState (float/pulse/orbit), StatusOrb

✅ auth              — AuthPage (plan badge, error shake),
                        MFAVerifyPage (6-digit, success bounce),
                        SSOCallbackPage (spinner)

✅ chat              — ChatPage (typewriter, SSE),
                        AgentMemoryPage (memory cards, Forget animation),
                        ChatStepCard, ChatHITLCard

✅ rpa               — RpaLivePage, click target rings, step builder,
                        browser preview overlay, record mode

✅ ocr               — OcrPage, bounding boxes, confidence bars

✅ civilization      — CivilizationPage, D3 globe rotation, arc flows

✅ schedules         — SchedulesPage, calendar month slide, cron display,
                        next-run countdown, status orb toggle

✅ templates         — TemplateLibraryPage, TemplatePickerModal,
                        TemplateInstantiator

✅ tools             — ToolsPage, tool rows stagger, risk badge

✅ status            — StatusPage, uptime bars stagger, component health orbs

✅ lab               — AgentLabPage, prompt editor, streaming typewriter,
                        compare mode panelVariants, history sidebar

✅ landing           — LandingPage, hero constellation, scroll triggers

✅ skills            — SkillsPage, enable toggle animation, orb transition

✅ simulation        — SimulationPage, SIMULATION banner, dry run badge

✅ builder           — BuilderPage, node canvas, particle edge flow,
                        drag-drop spring physics

✅ rbac              — RbacPage, permission matrix, role selector

✅ notifications     — NotificationCenterPage, SSE notification spring entry,
                        bell badge counterVariants, mark-read cascade

✅ onboarding        — OnboardingPage, wizard stepper, step transitions,
                        confetti on completion

✅ errors            — NotFoundPage, 404 glitch CSS, typewriter text
```

**37/37 features: Layout ✅ | Motion ✅ | SSE ✅ (where applicable) | Agentic ✅**

---

# COMPONENT-LEVEL SPEC — v6 (2026-08-18)
## 119 Non-Page Components: Every Gap Closed

**Audit method:** Scanned all 251 TSX files. 119 components (non-Page files) were not in spec.  
**Animation state:** 80+ with zero motion, 20+ partial (some in org/workflow), rest untouched.

---

## TIER 1: Global Shared Components (Used on Every Page)

### `components/command-palette/CommandPalette.tsx` (109 lines, ZERO animation)
**Critical — Cmd+K is the primary navigation tool.**

```
Layout (full-screen overlay):
┌────────────────────────────────────────────────────────────────────┐
│ ████████████████████████████ (dim backdrop, blur(40px))           │
│                                                                    │
│              ┌───────────────────────────────────────┐            │
│              │ ⌘  Search commands, pages, goals...   │            │
│              │ ──────────────────────────────────    │            │
│              │ RECENT                                │            │
│              │  🎯 Goals → "Research AI trends"      │            │
│              │  🤖 Agents → ResearchAgent            │            │
│              │ ──────────────────────────────────    │            │
│              │ ACTIONS                               │            │
│              │  ⚡ Create new goal                    │            │
│              │  ⚡ Deploy workflow                    │            │
│              └───────────────────────────────────────┘            │
└────────────────────────────────────────────────────────────────────┘

Animations:
• Open: backdropVariants (opacity 0→1, 180ms) + modalVariants
  (scale 0.93→1, y:16→0, springs.page)
• Close: exit variants reverse
• Search results: AnimatePresence, items listItemVariants stagger 0.03s
• Selected item: bg surface3, left border electric, spring slide
• Keyboard arrow navigation: selection moves spring position
• Group header (RECENT/ACTIONS): fade in 100ms after items
• Result icons: scale 0.8→1 springs.snappy on appear
• Empty state: ChatEmptyState pattern, float animation
• Trigger key "⌘K" hint: glows electric on press
```

### `components/execution/ExecutionTimeline.tsx` (242 lines, ZERO animation)
**Critical — shows the live agent execution step-by-step.**

```
Layout:
┌─────────────────────────────────────────────────────────────┐
│ [Step 1 — Initialize] ✅                     0.3s  $0.001  │
│ [Step 2 — RAG Retrieval] ✅                  1.2s  $0.003  │
│ [Step 3 — Plan] ✅                           2.1s  $0.012  │
│ [Step 4 — Execute: web_search] 🔄 ACTIVE     ...           │
│   └── Tool: web_search  args: {query: "..."}               │
│   └── Waiting for response...                              │
│ [Step 5 — Verify] ⏳                                        │
└─────────────────────────────────────────────────────────────┘

Animations:
• Timeline container: JARVISStagger staggerMs=80
• Each step: listItemVariants
• ✅ completed: scale 0→1.2→1 springs.bouncy + emerald left-border flash
• 🔄 active: StatusOrb size=10, pulseVariants.pulse electric ring
  Active step bg: bg-[#00D4FF]/[0.06] border border-[#00D4FF]/20
• ⏳ pending: opacity 0.4, gray left border
• Step expand (tool detail): AnimatePresence height springs.page
  Tool call args: monospace with electric text-[#00D4FF]
  Tool output: fade in after latency counter
• Latency/cost: count-up counterVariants when step completes
• New step arriving (SSE): y:-12→0 springs.bouncy
• Error step: rose border, rose glow, shake x:[-4,4,-4,0]
```

### `components/execution/ToolCallInspector.tsx` (227 lines, ZERO animation)
**Shows tool call inputs, outputs, timing for any step.**

```
Layout (expandable panel):
┌─────────────────────────────────────────────────────────────┐
│ 🔧 web_search  [HIGH risk] [34ms] [▶ Expand]               │
│  ▼ Expanded:                                                 │
│  Input args: { query: "AI market 2026", max_results: 10 }   │
│  Output:  [15 results fetched] [See full ▶]                 │
│  ────────────────────────────────────────────────────────   │
│  Evidence: chunk_id_abc123 (relevance: 0.92)               │
└─────────────────────────────────────────────────────────────┘

Animations:
• Accordion: AnimatePresence height 0→auto springs.page
• Risk badge: rose=HIGH, amber=MEDIUM, emerald=LOW — springs.snappy appear
• Input JSON: syntax-highlighted, fade in when expanded
• Evidence citations: listItemVariants stagger 0.04s
• "See full" expand: nested AnimatePresence
• Latency: monospace count-up on appear
• Electric text for tool name
```

### `components/live/LiveCostTicker.tsx` (85 lines, ZERO animation)
**Live cost counter on dashboard and goal execution.**

```
Current: Static rendering of cost value.

What it needs:
• Cost value: AnimatePresence counterVariants on each update
  New value slides in from bottom, old exits top
• Electric color: text-[#00D4FF] for cost amount
• Micro-animation: very subtle scale 1→1.05→1 springs.gentle on each update
• Threshold alert: when approaching limit, amber color + amber pulse ring
• Zero state: text-[#475569] "—" when no cost yet
```

### `components/ui/StatusBadge.tsx` (72 lines, ZERO animation)
**Used everywhere to show status pills (complete, running, failed).**

```
Current: Static className mapping with CSS classes.

What it needs:
• Enter animation: scale 0.8→1 springs.snappy on mount
• Status change: AnimatePresence for smooth text/color transition
• Running badge: text with animated ellipsis (...)
  or StatusOrb size=6 alongside the text
• Electric color for "running"/"executing" status
• Hover: subtle scale 1.02 springs.gentle

Note: Distinguish from StatusOrb (round dot) — this is the full pill badge.
They work together: StatusOrb (orb) + StatusBadge (text pill)
```

### `components/voice/VoiceGoalInput.tsx` (116 lines, ZERO animation)
**Voice input for goal submission — used in MissionGoalComposer.**

```
Current: Static recording UI with basic state.

What it needs:
• Mic button idle: subtle scale 1→1.03→1 pulse (2s loop)
• Recording active: rose pulse ring (1.5s loop, scale 1→1.5→1, opacity 1→0)
  Mic icon changes from static to animated recording indicator
• Sound wave visualization: 5 bars, heights animated with D3/CSS
  Random heights between 20%-100%, updates every 100ms
• Stop recording: rose→electric transition springs.fast
• Processing: spinner + "Processing..." text fade in
• Error state: shake x:[-4,4,-4,0] + toast
• Modal wrapper (if used in modal): drawerVariants from bottom
```

### `components/ui/PendingApprovalsBadge.tsx` (35 lines, ZERO animation)
**Approval count badge in the sidebar/nav.**

```
What it needs:
• Count change: AnimatePresence counterVariants (number springs.bouncy)
• Non-zero: amber color + amber pulse ring
• Zero: fades out (scale 0 springs.snappy) — no badge shown
• New approval arrived: brief scale 1→1.4→1 springs.bouncy + amber flash
```

### `components/ui/MissionControlLayout.tsx` (153 lines, ZERO animation)
**Layout wrapper used by some pages.**

```
What it needs:
• OperationalStatusBar: clock ticks with counterVariants (1s interval)
• Health status orb: StatusOrb for system health (healthy=emerald, degraded=amber)
• Running goals count: counterVariants on change
• Right panel (inspector drawer): panelVariants from right when toggled
• Pending approvals banner: y:-40→0 springs.page, amber left border
```

---

## TIER 2: Analytics & Visualization Components

### `components/charts/ThemedBarChart.tsx` (79 lines, ZERO animation)
### `components/charts/ThemedLineChart.tsx` (57 lines, ZERO animation)
**Core chart components used in analytics, agent radar, eval.**

```
ThemedBarChart:
• Bars: scaleY 0→1 stagger 0.03s springs.cinematic (from bottom)
• Colors: electric (#00D4FF) for primary, variants for secondary
• Hover bar: opacity 1 (others 0.4), tooltip animates in modalVariants
• Y-axis labels: fade in after bars settle (0.3s delay)
• Value labels atop bars: counterVariants on settle
• Electric gridlines: opacity 0.08 on dark surface

ThemedLineChart:
• Line: SVG strokeDashoffset 0→length, 700ms cubic-bezier
• Data points: scale 0→1 springs.bouncy stagger after line draws
• Hover crosshair: vertical line tracks mouse with springs.gentle
• Tooltip: modalVariants scale-in at cursor position
• Area fill: opacity 0→0.15 after line draws (+200ms delay)
• Multiple lines: each draws in with 150ms stagger
```

### `components/graph/FlowCanvas.tsx` (217 lines, ZERO animation)
**@xyflow/react canvas used in workflow builder.**

```
What it needs (leverages @xyflow built-in + adds):
• Edge connection: particle flow animation (stroke-dashoffset CSS)
  Active edges: brighter particles, faster flow (600ms cycle)
  Idle edges: dim particles, slow drift (2s cycle)
• Node selection: electric border springs.fast + scale 1.02
• Node drag: spring physics on release (snap to grid)
• Canvas zoom/pan: smooth spring ease (xyflow built-in, verify)
• Node add: springs.bouncy drop from palette position
• Node delete: scale 0 + opacity 0 springs.fast
• Error edge (invalid connection): rose flash + shake
```

---

## TIER 3: Chat Sub-Components (Agentic Conversation UI)

### `ChatThread.tsx` (94 lines), `ChatMessage.tsx` (95 lines), `ChatInput.tsx` (111 lines) — ALL ZERO animation

**These are the core chat rendering components. Chat = primary interface for goal execution.**

```
ChatThread:
• Messages container: virtualized scroll (already uses useVirtualizer ✅)
• New message arrival: listItemVariants from appropriate direction
  User msg: x:16→0 springs.page (from right)
  AI msg: x:-16→0 springs.page (from left)
• Scroll to bottom: smooth spring scroll animation
• Date separator: fade in (opacity 0→1 springs.page)

ChatMessage:
• Enter: listItemVariants based on sender direction
• Streaming: character-by-character typewriter (already partially handled)
• Code blocks: fade in after typing completes (+200ms)
• Reaction hover: scale 1.1 springs.snappy, emoji appears
• Long message truncation → expand: AnimatePresence height springs.page
• Tool call result: springs.bouncy card appear
• Electric border for AI messages with citations

ChatInput:
• Focus: border electric springs.fast + shadow.glow
• Submit button: JARVISButton springs.fast, spinner during processing
• Attachment chip: springs.bouncy appear, scale 0→1
• Clear attachment: scale 0 springs.fast
• Voice button: same as VoiceGoalInput (rose pulse when recording)
• Cmd+Enter shortcut: brief electric flash on submit
• Paste detection: brief border flash springs.fast
```

### `TypingIndicator.tsx` (21 lines, ZERO animation)
**The 3-dot "AI is thinking" indicator. Currently 21 lines with probably static dots.**

```
What it needs:
• 3 dots with staggered scale animation:
  dot 1: scale 0.5→1→0.5 at 0ms
  dot 2: scale 0.5→1→0.5 at 150ms
  dot 3: scale 0.5→1→0.5 at 300ms
  Cycle: 1.4s total, springs.bouncy for each peak
• Electric color dots (text-[#00D4FF])
• Appear: fade in with springs.page (0.3s after last message)
• Disappear: AnimatePresence fade out when response starts
• Container: same width as AI message bubble (consistency)
```

### `ChatHITLCard.tsx` (58 lines) & `ChatStepCard.tsx` (67 lines) — ZERO animation

```
ChatHITLCard (approval request in chat):
• Entry: listItemVariants with amber left border
• Amber pulse: StatusOrb status="pending" size=10
• Approve button: JARVISButton emerald, click → card exits right + toast
• Reject button: JARVISButton rose, click → card exits left + toast
• Waiting timer: live countdown counterVariants

ChatStepCard (step execution card in chat):
• Entry: listItemVariants springs.page
• StatusOrb: matches step status (running=electric, done=emerald, failed=rose)
• Running: pulsing electric ring on the step card header
• Completed: brief emerald flash on the card + checkmark draws in
• Tool output: expand AnimatePresence on click
• Cost/latency: count-up counterVariants on complete
```

### Other Chat Components

```
ChatSidebar (179 lines, ZERO animation):
• Session list: listItemVariants stagger 0.04s
• Active session: electric left border + surface3 bg
• New session: springs.bouncy appear from top
• Session hover: surface4 bg transition

ChatEmptyState (59 lines, ZERO animation):
• Same as upgraded EmptyState — icon float + stagger text
• Chat-specific: animated conversation bubble SVG
• "Start a conversation" CTA: JARVISButton springs.bouncy

ChatChart (52 lines), ChatDiff (37 lines), ChatGoalSummary (37 lines):
• All need JARVISPageShell-level entrance animation
• ChatChart: same as ThemedBarChart/ThemedLineChart
• ChatDiff: rose/emerald line highlighting (same as GovernancePage diff)
• ChatGoalSummary: listItemVariants for summary points
```

---

## TIER 4: Civilization Components (Multi-Agent Society)

### All 8 Civilization components — ALL ZERO animation

```
AgentNode.tsx (237 lines) — D3 node for each agent:
• Node shape: electric circle glow when active (SVG filter)
• Pulse ring: SVG circle scale animation (same as StatusOrb concept)
• Status color: #00D4FF=active, #475569=idle, #EF4444=error
• Hover: scale 1.15 springs.gentle, tooltip appear
• Selected: scale 1.3, electric border ring

BlackboardFeed.tsx (121 lines) — shared knowledge updates:
• Each entry: listItemVariants from right (SSE-driven)
• Electric accent for latest update
• Fade older entries: opacity reduces by age
• Category icons: colored per knowledge type

CivilizationMap.tsx (252 lines) — D3 geographic/force visualization:
• Nodes (agents): spring force settle on mount
• Active connections: animated stroke-dashoffset flowing
• Node pulse: SVG animation for active agents (electric)
• Zoom/pan: smooth spring ease

DebateViewer.tsx (234 lines) — agent debate visualization:
• Argument cards: alternating left/right slide (x:±16→0 springs.page)
• Consensus meter: fill animation springs.page
• Voting: scale springs.bouncy on each vote
• Winner declaration: emerald flash + scale 0→1.3→1 springs.bouncy

CivilizationMetrics.tsx (206 lines) — metrics panel:
• JARVISStagger on metric rows
• Value changes: counterVariants springs.fast
• Trend arrows: color-coded with springs.snappy

SpawnLineageTimeline.tsx (149 lines) — agent genealogy:
• Timeline: horizontal spring settle (same as MemoryExplorerPage)
• Spawn event: springs.bouncy node appear
• Lineage lines: SVG stroke-dashoffset animation

ConstitutionEditor.tsx (402 lines) — rule editing:
• Rule rows: listItemVariants stagger
• Edit mode: border electric transition
• Save: springs.bouncy ✅ flash

LearningLedger.tsx (150 lines) — learning records:
• Record rows: listItemVariants stagger 0.04s
• New learning: springs.bouncy enter from top
• Score change: counterVariants
```

---

## TIER 5: Security Panels (SecurityCenterPage sub-panels)

### All 6 Security panels — ALL ZERO animation

```
AgentIdentityPanel.tsx (211 lines):
• Identity card: cardVariants on mount
• Credential items: listItemVariants stagger
• Copy credentials: brief emerald flash springs.fast
• Revoke action: rose flash + ConfirmModal animation

GovernancePanel.tsx (135 lines):
• Policy rows: listItemVariants stagger
• Active policy: electric left border
• Violation count: counterVariants amber

GuardrailsPanel.tsx (74 lines):
• Rule cards: listItemVariants stagger
• Toggle: StatusOrb transitions emerald↔gray springs.fast

LimitsPanel.tsx (106 lines):
• Rate limit gauges: fill animation springs.page
• Near-limit threshold: amber pulse when >80%

ScopesPanel.tsx (99 lines):
• Scope tree: height spring expand/collapse
• Admin scope: rose badge springs.bouncy

AuditPanel.tsx (128 lines):
• Audit events: listItemVariants stagger
• Security events: rose left border
• New event (SSE): springs.bouncy from top
```

---

## TIER 6: Observability Sub-Components

### `TraceExplorer.tsx` (90 lines, ZERO animation)
**Drill-down into individual traces.**

```
• Span waterfall: each span bar grows left→right (scaleX 0→1, stagger 0.02s)
• Span hover: expand to show tags, springs.page
• Error spans: rose color + rose glow
• Parent/child indentation: spring reveal on expand
• Latency axis: draw-in animation
```

### `RuntimeDecisionPanel.tsx` (151 lines, ZERO animation)
**Shows LLM decision points during execution.**

```
• Decision cards: listItemVariants stagger 0.05s
• LLM "thinking" state: 3-dot TypingIndicator
• Decision reveal: typewriter effect
• Confidence score: fill bar springs.page
• Alternative options: scale 0→1 stagger springs.snappy
```

---

## TIER 7: Settings Sub-Components

### `MFASettings.tsx` (415 lines, ZERO animation)
**MFA enrollment, TOTP setup, recovery codes.**

```
• QR code reveal: scale 0.8→1 springs.page on setup initiation
• TOTP verification: 6-digit input same as MFAVerifyPage (spring focus ring)
• Success state: ✅ scale 0→1.3→1 springs.bouncy + emerald glow
• Recovery codes list: listItemVariants stagger 0.04s
• Code copy: brief emerald flash + "Copied!" toast
• Disable MFA: rose ConfirmModal with animation
• Recovery count low: amber warning badge counterVariants

PrivacySettings.tsx (372 lines — already has motion ✅)
```

---

## TIER 8: Goals Sub-Components

### `CostEstimateWidget.tsx` (93 lines, ZERO animation)
**Shows estimated cost before submitting a goal — shown in MissionGoalComposer.**

```
• Appear: AnimatePresence y:-8→0 springs.page when estimate loads
• Cost value: count-up counterVariants
• Model comparison rows: listItemVariants stagger
• Budget warning: amber pulse when estimate approaches limit
• "Free tier" indicator: electric badge springs.bouncy
• Loading state: shimmer skeleton
```

### `GoalFeedback.tsx` (106 lines, ZERO animation)
**User rating/feedback on completed goals.**

```
• Star rating: stars fill left→right stagger 0.08s springs.snappy
  Hover: stars highlight as cursor moves (spring-smooth highlight)
• Selected rating: scale 1.2→1 springs.bouncy
• Text feedback: focus border electric
• Submit: JARVISButton springs.fast → ✅ "Thank you!" emerald flash
• Already rated: stars show filled with disabled state
```

### `CitationList.tsx` (45 lines, ZERO animation)
**Citation footnotes on RAG responses.**

```
• Citation items: listItemVariants stagger 0.04s
• Hover: border.glow electric, excerpt expands AnimatePresence
• Click: highlight jumps to source document (scroll + flash)
• Relevance badge: fill bar springs.page
• Citation number: springs.bouncy appear
```

### `AdaptiveResultPanel.tsx` (228 lines, ZERO animation)
**Adaptive display of goal results (text/code/table/chart).**

```
• Panel mount: pageVariants (blur→0, y→0)
• Content type switch: AnimatePresence mode="wait" fade
• Table rows: listItemVariants stagger 0.02s
• Code block: fade in springs.page
• Chart: same as ThemedBarChart/LineChart with draw-in
• "Export" button: JARVISButton, springs.fast
```

### `GoalResultActions.tsx` (129 lines, ZERO animation)
**Action buttons on goal results (copy, share, fork, template).**

```
• Button group: JARVISStagger stagger 0.05s
• Each button: JARVISButton springs.fast
• Copy: brief electric flash + "Copied!" toast toastVariants
• Fork as template: brief scale 0→1 springs.bouncy → navigate
• Share: popover AnimatePresence
```

---

## TIER 9: Org Sub-Components (Most Already Animated ✅)

### `OrgHealthWidget.tsx` (88 lines, ZERO animation)
**Org-level health summary used in OrgPage.**

```
• Health score arc: SVG strokeDashoffset springs.slow
• Health change: counterVariants springs.fast
• Degraded state: amber pulse + "Action required" badge
• Hover: expand to show breakdown AnimatePresence
• Metric rows: listItemVariants stagger
```

---

## TIER 10: Triggers Sub-Components

### `TriggerCard.tsx` (155 lines, ZERO animation)
### `TriggerDetailDrawer.tsx` (159 lines, ZERO animation)

```
TriggerCard:
• Card: cardVariants on mount
• Active toggle: StatusOrb transitions springs.fast
• Last triggered: relative time (live updating)
• Hover: y:-2 + border.glow springs.gentle
• "Fire" action: brief flash animation + toast

TriggerDetailDrawer:
• Drawer: drawerVariants from bottom
• History rows: listItemVariants stagger
• Stats: count-up counterVariants
• Edit form: fields animate to editable (border.active spring)
• Save: springs.fast → ✅ toast

TriggerDLQPanel.tsx (71 lines):
• DLQ items: listItemVariants stagger, rose left border
• Retry action: JARVISButton springs.fast, spinner
• Clear DLQ: rose ConfirmModal with animation

TriggerHistoryPanel.tsx (96 lines):
• Event rows: listItemVariants stagger 0.03s
• Success: emerald dot, failure: rose dot (StatusOrb size=6)
• Last 24h sparkline: SVG path draw-in
```

---

## TIER 11: Workflow Builder Sub-Components

### `WorkflowExecutionOverlay.tsx` — Not in spec
**Overlay shown during workflow test execution.**

```
• Fade in over canvas: backdropVariants
• Current node highlight: electric ring springs.fast
• Execution log: listItemVariants from bottom (SSE)
• Completed: exit springs.page + emerald success banner
• Error: rose banner with shake animation
```

### `WorkflowStepConfig.tsx` — Panel for step configuration
**Right panel for configuring selected workflow steps.**

```
• Slide in: panelVariants from right on node select
• Tab switch: AnimatePresence y:8→0 springs.page
• Field focus: border electric springs.fast
• Save changes: springs.fast → ✅ in panel header
• Close: panelVariants exit right
```

### `RunTimeline.tsx` (workflow run-viewer, 246 lines — has motion ✅)
### `StepOutputInspector.tsx` (158 lines — has motion ✅)
### `HITLContextRenderer.tsx` (196 lines — has motion ✅)

---

## TIER 12: Ingestion Source Family Forms

### All 7 source family forms — ALL ZERO animation
`CodeRepoForm`, `CommunicationForm`, `DatabaseForm`, `GenericSourceForm`, `ObjectStorageForm`, `StreamingForm`, `WebForm`

```
Pattern for all 7 forms:
• Form mount: JARVISPageShell entrance (blur+y springs.page)
• Field focus: border electric springs.fast
• Validation error: rose border + shake x:[-4,4,-4,0]
• Test connection button: JARVISButton springs.fast, spinner → ✅/❌
• Advanced settings accordion: AnimatePresence height springs.page
• Connection success: emerald flash + "N resources found" counterVariants

SourceCreateWizard.tsx (220 lines — has motion ✅):
• Already has AnimatePresence — verify step transitions use springs.page
```

---

## TIER 13: Coordination Views

### Coordination views are nearly empty stubs (3-25 lines)

```
AuctionBidView.tsx (4 lines):   Needs full implementation
CodeExecutionView.tsx (3 lines): Needs full implementation  
SwarmTopologyView.tsx (3 lines): Needs full implementation
ParentChildTopology.tsx (17 lines): Needs basic topology viz
ReflexionEvidenceView.tsx (3 lines): Needs full implementation

These are placeholders. When implemented:
• AuctionBidView: listItemVariants for bids, winning bid springs.bouncy
• CodeExecutionView: same as terminal-style streaming output
• SwarmTopologyView: D3 force graph same as AgentOrbitView
• ParentChildTopology: tree layout with spring settle

RunTimeline.tsx (21 lines) — coordination specific:
• Execution steps: same as ExecutionTimeline.tsx
• Agent assignments: StatusOrb per agent
• Handoff arrows: animated SVG between agent cards
```

---

## TIER 14: Template Components

### `TemplateCard.tsx` (102 lines, ZERO animation)

```
• Card: cardVariants on mount (same as MarketplacePage card)
• Official badge: electric glow springs.snappy appear
• Rating stars: fill stagger 0.08s
• Install count: counterVariants
• Hover: y:-3 + border.glow + glowStrong shadow springs.gentle
• "Use" click: card lifts (scale 1.03) + brief exit → navigate
• Preview: hover overlay fades in with "Preview ▶" CTA
```

---

## TIER 15: CRDT Collaboration Component

### `components/collab/CRDTEditor.tsx` (195 lines, ZERO animation)
**YJS-powered collaborative editor used in ColaborationPage.**

```
• Editor mount: JARVISPageShell entrance
• Remote cursor: motion.div follows spring to cursor position
  Name tooltip: springs.gentle appear
  Color: unique per collaborator (CSS variable)
• Remote selection: colored highlight, spring fade in/out
• User join: presence avatar springs.bouncy appear
• User leave: presence avatar scale 0 springs.fast exit
• CRDT merge: brief amber flash on conflict resolution
• Sync status indicator: StatusOrb (connected=emerald, syncing=amber)
```

---

## Complete Component Coverage Matrix — v6 Final

```
CRITICAL (must animate before launch):
  ✅ CommandPalette       — Cmd+K global, backdropVariants + listItemVariants stagger
  ✅ ExecutionTimeline    — Agent steps, StatusOrb, ✅ bounce, error shake
  ✅ ToolCallInspector    — Accordion expand, risk badge, latency count-up
  ✅ ChatThread           — Virtualized, listItemVariants per sender
  ✅ ChatMessage          — Typewriter streaming, direction-aware slide
  ✅ ChatInput            — Focus glow, JARVISButton, voice pulse
  ✅ TypingIndicator      — 3-dot stagger springs.bouncy
  ✅ StatusBadge          — Scale enter, AnimatePresence status change
  ✅ LiveCostTicker       — counterVariants, electric color

HIGH (animate in Phase 1):
  ✅ ThemedBarChart       — scaleY stagger springs.cinematic
  ✅ ThemedLineChart      — strokeDashoffset draw-in
  ✅ VoiceGoalInput       — rose pulse ring, sound wave bars
  ✅ FlowCanvas           — particle edge flow, node select electric
  ✅ CostEstimateWidget   — AnimatePresence appear, count-up
  ✅ GoalFeedback         — star fill stagger, JARVISButton
  ✅ TriggerCard          — cardVariants, StatusOrb toggle
  ✅ TriggerDetailDrawer  — drawerVariants from bottom
  ✅ MissionControlLayout — clock tick, health StatusOrb, panelVariants
  ✅ OrgHealthWidget      — score arc springs.slow
  ✅ ChatHITLCard         — amber pulse, approve/reject direction exit
  ✅ ChatStepCard         — StatusOrb, running ring, expand output
  ✅ TemplateCard         — cardVariants, star stagger, use-animation

MEDIUM (animate in Phase 2):
  ✅ All 8 Civilization components — force graph, debate alternating, metrics stagger
  ✅ All 6 Security panels — listItemVariants, StatusOrb toggles
  ✅ TraceExplorer        — waterfall scaleX stagger
  ✅ RuntimeDecisionPanel — decision reveal typewriter
  ✅ MFASettings          — 6-digit input, QR reveal, recovery list stagger
  ✅ AdaptiveResultPanel  — pageVariants, type switch AnimatePresence
  ✅ GoalResultActions    — JARVISStagger button group
  ✅ CitationList         — listItemVariants, hover expand
  ✅ CRDTEditor           — remote cursor spring-follow, presence springs.bouncy
  ✅ All 7 ingestion forms — field focus electric, test connection, validation shake

STUB (implement then animate):
  ✅ AuctionBidView, CodeExecutionView, SwarmTopologyView,
     ReflexionEvidenceView — build first, then apply listItemVariants/D3 patterns
```

---

## Updated Total Counts

| Category | Total Files | Spec Coverage (before v6) | Spec Coverage (after v6) |
|----------|------------|---------------------------|--------------------------|
| Pages (87) | 87 | 87 (100%) | 87 (100%) |
| Shared components | 28 | 10 | 28 (100%) |
| Feature components | 196 | 42 | 164 (84%) |
| **TOTAL** | **251** | **132 (53%)** | **251 (100%)** |

**Remaining ~32 feature components not explicitly spec'd = ingestion family forms (7) + trigger family forms (10) + coordination stubs (5) + small utility components — all covered by the "pattern for all N" instructions above.**

---

## Final: Definition of Done — Complete (27+)

```
Every component in the app:
□ Uses spring physics (not duration/ease) for all animations
□ Respects useReducedMotion()
□ Has StatusOrb instead of inline colored dots
□ Has JARVISButton for all interactive buttons
□ Has AnimatePresence for conditional renders
□ Has listItemVariants/cardVariants for lists/grids
□ Uses electric #00D4FF (resolved from --primary after Step 0-A)
□ Has aria-label on all icon-only buttons
□ Has ErrorBoundary on all major sections
□ Uses t() for all user-facing strings
□ Has skeleton loading state when useQuery is pending
□ Has animated EmptyState when data is empty
```
