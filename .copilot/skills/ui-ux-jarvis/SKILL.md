# UI/UX JARVIS Style Skill — AgentVerse

## When to Invoke
Invoke for ANY frontend work: new pages, components, animations, redesigns.

## Global Skill Reference
This skill extends: `~/.agents/skills/ui-ux-pro-max/SKILL.md`
Read that file first for the full 161-palette, 57-font-pairing, 99-UX-guideline system.
Then apply AgentVerse JARVIS-specific styles below.

---

## JARVIS Design Identity

```
Core aesthetic: JARVIS-inspired dark intelligence
- Background: Deep space black (#0F1117)
- Surfaces:   Layered dark (#1A1F2E → #252B3B → #2D3748)
- Accent:     Electric blue (#3B82F6), Violet (#8B5CF6), Cyan (#06B6D4)
- Text:       Ice white (#F1F5F9), muted (#94A3B8)
- Status:     Emerald (#10B981), Amber (#F59E0B), Rose (#EF4444)
- Font:       Inter (UI) + JetBrains Mono (code/data)
```

---

## Design Token System

```css
/* src/styles/tokens.css — THE single source of truth */
:root {
  /* ─── Backgrounds (light to elevated) ─────────────── */
  --bg-base:       #0A0D14;   /* page background */
  --bg-primary:    #0F1117;   /* default surface */
  --bg-secondary:  #1A1F2E;   /* cards, panels */
  --bg-elevated:   #252B3B;   /* hover, selected */
  --bg-overlay:    #2D3748;   /* modals, dropdowns */
  --bg-glass:      rgba(15, 17, 23, 0.85); /* glassmorphism */

  /* ─── Accents ─────────────────────────────────────── */
  --accent-blue:   #3B82F6;
  --accent-violet: #8B5CF6;
  --accent-cyan:   #06B6D4;
  --accent-emerald:#10B981;
  --accent-amber:  #F59E0B;
  --accent-rose:   #EF4444;

  /* ─── Text ──────────────────────────────────────────── */
  --text-primary:  #F1F5F9;
  --text-secondary:#94A3B8;
  --text-muted:    #475569;
  --text-inverted: #0F1117;

  /* ─── Borders ──────────────────────────────────────── */
  --border:        #1E2535;
  --border-subtle: #2D3748;
  --border-accent: rgba(59, 130, 246, 0.3);

  /* ─── Spacing (4px base grid) ─────────────────────── */
  --s-1: 0.25rem; --s-2: 0.5rem;  --s-3: 0.75rem;
  --s-4: 1rem;    --s-6: 1.5rem;  --s-8: 2rem;
  --s-12: 3rem;   --s-16: 4rem;   --s-24: 6rem;

  /* ─── Radius ─────────────────────────────────────── */
  --r-sm: 4px;  --r-md: 8px;  --r-lg: 12px;
  --r-xl: 16px; --r-full: 9999px;

  /* ─── Shadows (JARVIS glow effect) ───────────────── */
  --shadow-blue:   0 0 20px rgba(59, 130, 246, 0.15);
  --shadow-cyan:   0 0 15px rgba(6, 182, 212, 0.2);
  --shadow-card:   0 4px 24px rgba(0, 0, 0, 0.4);
  --shadow-modal:  0 20px 60px rgba(0, 0, 0, 0.6);
}
```

---

## Component Patterns

### Glassmorphism Card

```tsx
function GlassCard({ children, glowColor = 'blue' }: GlassCardProps) {
  return (
    <div
      className={cn(
        // Glassmorphism base
        "backdrop-blur-xl bg-bg-glass",
        "border border-border-accent",
        "rounded-xl",
        // Conditional glow
        glowColor === 'blue'  && "shadow-[0_0_20px_rgba(59,130,246,0.15)]",
        glowColor === 'cyan'  && "shadow-[0_0_20px_rgba(6,182,212,0.2)]",
        glowColor === 'violet'&& "shadow-[0_0_20px_rgba(139,92,246,0.2)]",
      )}
    >
      {children}
    </div>
  );
}
```

### JARVIS Status Badge with Pulse

```tsx
function StatusBadge({ status }: { status: MissionStatus }) {
  const config = {
    active:    { color: 'emerald', label: 'Active',    pulse: true },
    running:   { color: 'blue',    label: 'Running',   pulse: true },
    pending:   { color: 'amber',   label: 'Pending',   pulse: false },
    completed: { color: 'emerald', label: 'Completed', pulse: false },
    failed:    { color: 'rose',    label: 'Failed',    pulse: false },
  }[status];

  return (
    <span className={cn(
      "inline-flex items-center gap-1.5 px-2 py-0.5",
      "text-xs font-medium rounded-full",
      config.color === 'emerald' && "bg-emerald-500/10 text-emerald-400",
      config.color === 'blue'    && "bg-blue-500/10 text-blue-400",
      config.color === 'amber'   && "bg-amber-500/10 text-amber-400",
      config.color === 'rose'    && "bg-rose-500/10 text-rose-400",
    )}>
      {config.pulse && (
        <span className={cn(
          "relative w-1.5 h-1.5 rounded-full",
          config.color === 'emerald' && "bg-emerald-400",
          config.color === 'blue'    && "bg-blue-400",
          // Pulse ring
          "before:absolute before:inset-0 before:rounded-full",
          "before:animate-ping before:opacity-75",
          config.color === 'emerald' && "before:bg-emerald-400",
          config.color === 'blue'    && "before:bg-blue-400",
        )} />
      )}
      {config.label}
    </span>
  );
}
```

---

## Framer Motion Patterns

### Page Entry Animation (Every Route)

```tsx
const pageVariants = {
  hidden:  { opacity: 0, y: 8,  filter: 'blur(4px)' },
  visible: { opacity: 1, y: 0,  filter: 'blur(0px)' },
  exit:    { opacity: 0, y: -8, filter: 'blur(4px)' },
};

function AnimatedPage({ children }: { children: React.ReactNode }) {
  return (
    <motion.div
      variants={pageVariants}
      initial="hidden"
      animate="visible"
      exit="exit"
      transition={{ duration: 0.2, ease: [0.4, 0, 0.2, 1] }}  // cubic-bezier
    >
      {children}
    </motion.div>
  );
}
```

### Staggered List Items

```tsx
const container = {
  hidden: {},
  visible: { transition: { staggerChildren: 0.06 } },
};
const item = {
  hidden:  { opacity: 0, x: -12 },
  visible: { opacity: 1, x: 0, transition: { duration: 0.25 } },
};

function MissionList({ missions }: { missions: Mission[] }) {
  return (
    <motion.ul variants={container} initial="hidden" animate="visible">
      {missions.map((m) => (
        <motion.li key={m.id} variants={item}>
          <MissionCard mission={m} />
        </motion.li>
      ))}
    </motion.ul>
  );
}
```

### Data Connection Line (JARVIS Graph)

```tsx
// Animated connection line that draws itself
function ConnectionLine({ from, to, active }: ConnectionLineProps) {
  const pathLength = useMotionValue(0);
  const opacity = useTransform(pathLength, [0, 1], [0, 0.6]);

  return (
    <motion.line
      x1={from.x} y1={from.y} x2={to.x} y2={to.y}
      stroke="var(--accent-cyan)"
      strokeWidth={active ? 2 : 1}
      style={{ pathLength, opacity }}
      initial={{ pathLength: 0 }}
      animate={{ pathLength: 1 }}
      transition={{ duration: 0.8, ease: "easeInOut" }}
    />
  );
}
```

### Command Bar Animation

```tsx
function CommandBar({ isOpen }: { isOpen: boolean }) {
  return (
    <AnimatePresence>
      {isOpen && (
        <motion.div
          initial={{ opacity: 0, scale: 0.96, y: -8 }}
          animate={{ opacity: 1, scale: 1,    y: 0  }}
          exit=  {{ opacity: 0, scale: 0.96, y: -8  }}
          transition={{ duration: 0.15, ease: [0.4, 0, 0.2, 1] }}
          className="fixed top-[15%] left-1/2 -translate-x-1/2 w-[640px]
                     bg-bg-overlay/90 backdrop-blur-2xl
                     border border-border-accent rounded-xl
                     shadow-modal z-[100]"
          role="dialog"
          aria-modal="true"
          aria-label="Command bar"
        >
          {/* ... */}
        </motion.div>
      )}
    </AnimatePresence>
  );
}
```

---

## Accessibility + Animation (WCAG 2.2 + Motion)

```tsx
// ALWAYS respect prefers-reduced-motion
import { useReducedMotion } from 'framer-motion';

function AnimatedComponent() {
  const shouldReduce = useReducedMotion();

  return (
    <motion.div
      animate={{ opacity: 1, y: 0 }}
      transition={shouldReduce
        ? { duration: 0 }     // instant — no animation
        : { duration: 0.2 }   // normal animation
      }
    />
  );
}

// In Tailwind: use the `motion-safe:` and `motion-reduce:` variants
<div className="
  motion-reduce:transition-none
  motion-safe:transition-all motion-safe:duration-200
" />
```

---

## UI Quality Checklist

```
□ Dark theme: all surfaces use design tokens (never hardcoded hex)
□ Glassmorphism cards on key content panels
□ Pulse animation on active/running status indicators
□ Page transitions: fade + slight Y translate (200ms cubic-bezier)
□ List items: staggered entrance (60ms between items)
□ Interactive elements: scale(0.98) on press + glow on focus
□ Loading states: skeleton with shimmer (not spinning wheel)
□ Empty states: illustration + CTA (not just grey text)
□ Command bar: keyboard accessible (⌘K trigger, Esc to close)
□ Color contrast: 4.5:1 minimum (verified with axe-core in CI)
□ Reduced motion: no animations if OS prefers-reduced-motion
□ Touch targets: min 44×44px on all interactive elements
□ Typography: clear hierarchy (Inter 400/500/600/700)
```
