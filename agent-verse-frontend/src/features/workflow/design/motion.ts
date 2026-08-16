/**
 * Motion System — Framer Motion spring presets + animation catalogue.
 *
 * Usage:
 *   import { springs, animations } from '../design/motion';
 *   <motion.div {...animations.nodeEnter} />
 */
import type { Transition, Variants } from 'framer-motion';

// ── Spring presets ────────────────────────────────────────────────────────────

export const springs = {
  /** Fast, crisp. Buttons, badges, toggles. */
  snappy: { type: 'spring', stiffness: 500, damping: 30 } satisfies Transition,

  /** Playful bounce. Node drop, panel open, toast. */
  bouncy: { type: 'spring', stiffness: 300, damping: 18 } satisfies Transition,

  /** Smooth glide. Panels sliding in, drawer open. */
  gentle: { type: 'spring', stiffness: 200, damping: 24 } satisfies Transition,

  /** Very slow, intentional. Full-page transitions. */
  smooth: { type: 'spring', stiffness: 120, damping: 20 } satisfies Transition,
} as const;

// ── Reduced motion: fall back to immediate transitions ────────────────────────

export const reducedMotion = {
  type: 'tween',
  duration: 0,
} satisfies Transition;

// ── Named animation variants ──────────────────────────────────────────────────

/** Node bounces in when dropped onto canvas. */
export const nodeBounce: Variants = {
  initial: { scale: 0.6, opacity: 0 },
  animate: { scale: 1, opacity: 1, transition: springs.bouncy },
  exit:    { scale: 0.8, opacity: 0, transition: { duration: 0.12 } },
};

/** Panel slides in from the right side. */
export const panelSlide: Variants = {
  initial: { x: '100%', opacity: 0 },
  animate: { x: 0, opacity: 1, transition: springs.gentle },
  exit:    { x: '100%', opacity: 0, transition: { duration: 0.18 } },
};

/** Palette slides in from the left. */
export const paletteSlide: Variants = {
  initial: { x: '-100%', opacity: 0 },
  animate: { x: 0, opacity: 1, transition: springs.gentle },
  exit:    { x: '-100%', opacity: 0, transition: { duration: 0.15 } },
};

/** Toast stacks: bounce in from bottom-right. */
export const toastEnter: Variants = {
  initial: { y: 80, opacity: 0, scale: 0.9 },
  animate: { y: 0, opacity: 1, scale: 1, transition: springs.bouncy },
  exit:    { y: -20, opacity: 0, scale: 0.95, transition: { duration: 0.2 } },
};

/** Node execution pulse — rings ripple outward when a node is running. */
export const runningPulse: Variants = {
  initial: { scale: 1, opacity: 0.6 },
  animate: {
    scale: [1, 1.08, 1],
    opacity: [0.6, 0.9, 0.6],
    transition: { repeat: Infinity, duration: 1.4, ease: 'easeInOut' },
  },
};

/** HITL SLA countdown pulse — subtle urgency on near-deadline items. */
export const slaPulse: Variants = {
  initial: { opacity: 1 },
  animate: {
    opacity: [1, 0.5, 1],
    transition: { repeat: Infinity, duration: 1.8, ease: 'easeInOut' },
  },
};

/** Step completion tick — scale bounce on ✅. */
export const completionBounce: Variants = {
  initial: { scale: 0, opacity: 0 },
  animate: { scale: 1, opacity: 1, transition: springs.bouncy },
};

/** Edge animated flow dots. Applied via CSS custom properties. */
export const edgeFlow = {
  animation: 'flow 1.2s linear infinite',
  '@keyframes flow': {
    '0%':   { strokeDashoffset: 24 },
    '100%': { strokeDashoffset: 0 },
  },
};

/** Approval card mobile swipe tint — approval green, reject red. */
export const swipeTint = {
  approve: { backgroundColor: 'rgba(16,185,129,0.15)', transition: { duration: 0.1 } },
  reject:  { backgroundColor: 'rgba(239,68,68,0.15)',  transition: { duration: 0.1 } },
};

/** Toolbar button hover/active states. */
export const toolbarButton: Variants = {
  rest:    { scale: 1 },
  hover:   { scale: 1.05, transition: springs.snappy },
  pressed: { scale: 0.95, transition: { duration: 0.06 } },
};

/** Empty state illustration fade+float in. */
export const emptyStateFade: Variants = {
  initial: { y: 16, opacity: 0 },
  animate: { y: 0, opacity: 1, transition: { ...springs.gentle, delay: 0.15 } },
};

/** Modal: backdrop + content coordinated enter. */
export const modalBackdrop: Variants = {
  initial: { opacity: 0 },
  animate: { opacity: 1, transition: { duration: 0.15 } },
  exit:    { opacity: 0, transition: { duration: 0.1 } },
};

export const modalContent: Variants = {
  initial: { scale: 0.94, opacity: 0, y: 12 },
  animate: { scale: 1, opacity: 1, y: 0, transition: springs.bouncy },
  exit:    { scale: 0.96, opacity: 0, y: 8, transition: { duration: 0.12 } },
};

/** Skeleton shimmer: handled purely with CSS (better perf than JS animation). */
export const SKELETON_SHIMMER_CLASS = 'animate-pulse bg-white/5';

// ── Convenience: prefers-reduced-motion aware wrapper ─────────────────────────

export function useMotionSafe<T extends Variants>(variants: T): T | typeof reducedMotion {
  if (typeof window !== 'undefined'
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    // Return a no-op variant set
    const noOp = {} as T;
    for (const key of Object.keys(variants)) {
      (noOp as Record<string, unknown>)[key] = { transition: reducedMotion };
    }
    return noOp;
  }
  return variants;
}
