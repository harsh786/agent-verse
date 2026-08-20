/**
 * AgentVerse Motion System — All animation variants + spring presets.
 * RULE: Never use duration/ease for UI motion. Always use springs.
 * Re-exports springs from JARVISPageShell for backward compatibility.
 */
import type { Variants } from 'framer-motion';

// ── Spring presets ─────────────────────────────────────────────────────────
export const springs = {
  /** Standard page transitions, panels, card enters */
  page:    { type: 'spring', stiffness: 280, damping: 26 } as const,
  /** Right panels, drawers */
  panel:   { type: 'spring', stiffness: 300, damping: 28 } as const,
  /** Button presses, toggles — snappy */
  fast:    { type: 'spring', stiffness: 600, damping: 35 } as const,
  /** Slow cinematic reveals, hero sections */
  slow:    { type: 'spring', stiffness: 200, damping: 25 } as const,
  /** Notifications, badges, completion events */
  bouncy:  { type: 'spring', stiffness: 450, damping: 18 } as const,
  /** Hover lifts, subtle accents */
  gentle:  { type: 'spring', stiffness: 180, damping: 24 } as const,
} as const;

// ── Page-level ─────────────────────────────────────────────────────────────
export const pageVariants: Variants = {
  hidden:  { opacity: 0, y: 16, filter: 'blur(4px)' },
  visible: { opacity: 1, y: 0,  filter: 'blur(0px)',
             transition: { ...springs.page, staggerChildren: 0.05 } },
  exit:    { opacity: 0, y: -8, filter: 'blur(2px)',
             transition: { duration: 0.15 } },
};

// ── Cards ──────────────────────────────────────────────────────────────────
export const cardContainer: Variants = {
  hidden:  { opacity: 0 },
  visible: { opacity: 1, transition: { staggerChildren: 0.05, delayChildren: 0.08 } },
};
export const cardItem: Variants = {
  hidden:  { opacity: 0, y: 18, scale: 0.97 },
  visible: { opacity: 1, y: 0,  scale: 1, transition: springs.page },
};

// ── Lists ──────────────────────────────────────────────────────────────────
export const listContainer: Variants = {
  hidden:  { opacity: 0 },
  visible: { opacity: 1, transition: { staggerChildren: 0.04, delayChildren: 0.05 } },
};
export const listItem: Variants = {
  hidden:  { opacity: 0, x: -14 },
  visible: { opacity: 1, x: 0, transition: springs.page },
  exit:    { opacity: 0, x: 14, transition: { duration: 0.12 } },
};

// ── Modals & panels ────────────────────────────────────────────────────────
export const backdrop: Variants = {
  hidden:  { opacity: 0 },
  visible: { opacity: 1, transition: { duration: 0.18 } },
  exit:    { opacity: 0, transition: { duration: 0.14 } },
};
export const modal: Variants = {
  hidden:  { opacity: 0, scale: 0.93, y: 14 },
  visible: { opacity: 1, scale: 1,    y: 0,  transition: springs.page },
  exit:    { opacity: 0, scale: 0.96, y: 8,  transition: { duration: 0.14 } },
};
export const slidePanel: Variants = {
  hidden:  { opacity: 0, x: 20 },
  visible: { opacity: 1, x: 0, transition: springs.panel },
  exit:    { opacity: 0, x: 20, transition: { duration: 0.14 } },
};
export const drawer: Variants = {
  hidden:  { y: '100%' },
  visible: { y: 0, transition: { ...springs.panel, delay: 0.05 } },
  exit:    { y: '100%', transition: { duration: 0.18 } },
};

// ── Counters & toasts ──────────────────────────────────────────────────────
export const counter: Variants = {
  initial: { y: 10,  opacity: 0 },
  animate: { y: 0,   opacity: 1, transition: springs.fast },
  exit:    { y: -10, opacity: 0, transition: { duration: 0.08 } },
};
export const toast: Variants = {
  hidden:  { opacity: 0, y: 36, scale: 0.92 },
  visible: { opacity: 1, y: 0,  scale: 1, transition: springs.bouncy },
  exit:    { opacity: 0, y: 16, scale: 0.96, transition: { duration: 0.16 } },
};

// ── Orb pulse ──────────────────────────────────────────────────────────────
export const orbPulse: Variants = {
  idle:    { scale: 1,    opacity: 1 },
  active:  { scale: [1, 1.6, 1], opacity: [1, 0.2, 1],
             transition: { duration: 1.8, repeat: Infinity, ease: 'easeInOut' } },
  offline: { scale: 1,    opacity: 0.3 },
};

// ── Neural graph springs (spec §1.4) ─────────────────────────────────────────
export const SPRING_NODE     = { type: 'spring', stiffness: 380, damping: 30 } as const;
export const SPRING_PARTICLE = { type: 'spring', stiffness: 800, damping: 40 } as const;

// Legacy flat aliases
export const SPRING_PAGE   = springs.page;
export const SPRING_PANEL  = springs.panel;
export const SPRING_FAST   = springs.fast;
export const SPRING_SLOW   = springs.slow;
export const SPRING_BOUNCY = springs.bouncy;

// Named variants (spec §2.2)
export const fadeUp: Variants = {
  hidden:  { opacity: 0, y: 12, filter: 'blur(4px)' },
  visible: { opacity: 1, y: 0,  filter: 'blur(0px)', transition: springs.page as Record<string, unknown> },
  exit:    { opacity: 0, y: -6, filter: 'blur(2px)', transition: springs.fast as Record<string, unknown> },
};

export const slideRight: Variants = {
  hidden:  { opacity: 0, x: -16 },
  visible: { opacity: 1, x: 0,  transition: springs.panel as Record<string, unknown> },
  exit:    { opacity: 0, x: -8, transition: springs.fast as Record<string, unknown> },
};

export const scaleIn: Variants = {
  hidden:  { opacity: 0, scale: 0.88 },
  visible: { opacity: 1, scale: 1,    transition: springs.bouncy as Record<string, unknown> },
  exit:    { opacity: 0, scale: 0.94, transition: springs.fast as Record<string, unknown> },
};

export const nodeAppear: Variants = {
  hidden:  { opacity: 0, scale: 0.4, filter: 'blur(8px)' },
  visible: { opacity: 1, scale: 1,   filter: 'blur(0px)', transition: SPRING_NODE as Record<string, unknown> },
  exit:    { opacity: 0, scale: 0.6, filter: 'blur(4px)', transition: springs.fast as Record<string, unknown> },
};
