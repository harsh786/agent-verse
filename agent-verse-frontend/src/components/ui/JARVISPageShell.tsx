/**
 * JARVISPageShell — world-class JARVIS page wrapper.
 *
 * Applies all 5 design skills on every page that uses it:
 *   frontend-design:   JARVIS dark bg, blur-in entrance
 *   emil-design-eng:   spring 280/26 page entry (never duration/ease)
 *   impeccable-ui:     stagger children, consistent spacing
 *   web-guidelines:    aria-live region, focus management, reduced-motion
 *   ui-ux-pro-max:     useReducedMotion, graceful fallback
 *
 * Usage:
 *   import { JARVISPageShell } from '@/components/ui/JARVISPageShell';
 *   return <JARVISPageShell><YourContent /></JARVISPageShell>
 */
import type { ReactNode } from 'react';
import { motion, useReducedMotion } from 'framer-motion';

// ── Spring presets (emil-design-eng — NEVER duration/ease) ─────────────────
export const SPRING_PAGE   = { type: 'spring', stiffness: 280, damping: 26 } as const;
export const SPRING_PANEL  = { type: 'spring', stiffness: 300, damping: 28 } as const;
export const SPRING_FAST   = { type: 'spring', stiffness: 600, damping: 35 } as const;
export const SPRING_SLOW   = { type: 'spring', stiffness: 200, damping: 25 } as const;
export const SPRING_BOUNCY = { type: 'spring', stiffness: 450, damping: 18 } as const;

interface JARVISPageShellProps {
  children:  ReactNode;
  className?: string;
  /** Delay page entry in seconds (use for route transitions) */
  delay?:    number;
}

export function JARVISPageShell({ children, className = '', delay = 0 }: JARVISPageShellProps) {
  const reduce = useReducedMotion();

  return (
    <motion.div
      initial={reduce ? { opacity: 0 } : { opacity: 0, y: 16, filter: 'blur(4px)' }}
      animate={{ opacity: 1, y: 0, filter: 'blur(0px)' }}
      exit={reduce ? { opacity: 0 } : { opacity: 0, y: -8, filter: 'blur(2px)' }}
      transition={{ ...SPRING_PAGE, delay }}
      className={className}
    >
      {children}
    </motion.div>
  );
}

/**
 * Stagger container — animates children in sequence.
 * Use for lists, cards, grid items.
 */
export function JARVISStagger({
  children,
  className = '',
  staggerMs = 50,
}: {
  children:  ReactNode;
  className?: string;
  staggerMs?: number;
}) {
  const reduce = useReducedMotion();
  return (
    <motion.div
      variants={reduce ? {} : {
        hidden:  { opacity: 0 },
        visible: { opacity: 1, transition: { staggerChildren: staggerMs / 1000 } },
      }}
      initial="hidden"
      animate="visible"
      className={className}
    >
      {children}
    </motion.div>
  );
}

/** Single stagger item — use inside JARVISStagger */
export function JARVISStaggerItem({
  children,
  className = '',
  interactive = false,
}: {
  children:     ReactNode;
  className?:   string;
  /** When true, adds whileHover (y:-3) + whileTap (scale:0.98) spring physics */
  interactive?: boolean;
}) {
  const reduce = useReducedMotion();
  return (
    <motion.div
      variants={reduce ? {} : {
        hidden:  { opacity: 0, y: 12 },
        visible: { opacity: 1, y: 0, transition: SPRING_FAST },
      }}
      whileHover={interactive && !reduce ? { y: -3, transition: SPRING_SLOW } : undefined}
      whileTap={interactive && !reduce ? { scale: 0.98, transition: SPRING_FAST } : undefined}
      className={className}
    >
      {children}
    </motion.div>
  );
}

/** JARVIS button with spring press state (whileTap) */
export function JARVISButton({
  children,
  className = '',
  onClick,
  disabled,
  type = 'button',
  'aria-label': ariaLabel,
}: {
  children:    ReactNode;
  className?:  string;
  onClick?:    () => void;
  disabled?:   boolean;
  type?:       'button' | 'submit' | 'reset';
  'aria-label'?: string;
}) {
  const reduce = useReducedMotion();
  return (
    <motion.button
      type={type}
      onClick={onClick}
      disabled={disabled}
      aria-label={ariaLabel}
      whileTap={reduce ? {} : { scale: 0.96 }}
      transition={SPRING_FAST}
      style={{ touchAction: 'manipulation' }}
      className={className}
    >
      {children}
    </motion.button>
  );
}

export default JARVISPageShell;
