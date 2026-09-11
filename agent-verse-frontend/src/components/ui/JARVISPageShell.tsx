/**
 * JARVISPageShell — world-class JARVIS page wrapper.
 *
 * Entrance animations are CSS-driven (see globals.css `.jarvis-*`) rather than
 * framer-motion springs. Under React 19 StrictMode the framer spring entrances
 * froze mid-flight and left whole pages stuck at opacity 0 (invisible content).
 * CSS keyframes always run to completion and rest at the visible end state no
 * matter how the component re-mounts, so page visibility never depends on a JS
 * animation loop. Hover/press micro-interactions stay as cheap CSS transforms.
 *
 * The spring presets remain exported for components that still tune framer
 * transitions locally.
 */
import type { ReactNode } from 'react';

// ── Spring presets (kept for local framer transitions elsewhere) ───────────
export const SPRING_PAGE   = { type: 'spring', stiffness: 280, damping: 26 } as const;
export const SPRING_PANEL  = { type: 'spring', stiffness: 300, damping: 28 } as const;
export const SPRING_FAST   = { type: 'spring', stiffness: 600, damping: 35 } as const;
export const SPRING_SLOW   = { type: 'spring', stiffness: 200, damping: 25 } as const;
export const SPRING_BOUNCY   = { type: 'spring', stiffness: 450, damping: 18 } as const;
export const SPRING_NODE     = { type: 'spring', stiffness: 380, damping: 30 } as const;
export const SPRING_PARTICLE = { type: 'spring', stiffness: 800, damping: 40 } as const;

interface JARVISPageShellProps {
  children:  ReactNode;
  className?: string;
  /** Delay page entry in seconds (use for route transitions) */
  delay?:    number;
}

export function JARVISPageShell({ children, className = '', delay = 0 }: JARVISPageShellProps) {
  return (
    <div
      className={`jarvis-page-in ${className}`}
      style={delay ? { animationDelay: `${delay}s` } : undefined}
    >
      {children}
    </div>
  );
}

/**
 * Stagger container — children animate in sequence via CSS nth-child delays
 * (see `.jarvis-stagger` in globals.css). `staggerMs` is accepted for API
 * compatibility; the cadence is defined in CSS.
 */
export function JARVISStagger({
  children,
  className = '',
  staggerMs: _staggerMs,
}: {
  children:  ReactNode;
  className?: string;
  staggerMs?: number;
}) {
  return <div className={`jarvis-stagger ${className}`}>{children}</div>;
}

/** Single stagger item — a CSS rise-in, plus optional hover/press transform. */
export function JARVISStaggerItem({
  children,
  className = '',
  interactive = false,
}: {
  children:     ReactNode;
  className?:   string;
  /** When true, adds a subtle hover-lift + press-scale (CSS transforms). */
  interactive?: boolean;
}) {
  const interactiveCls = interactive
    ? 'transition-transform duration-200 will-change-transform hover:-translate-y-0.5 active:scale-[0.98]'
    : '';
  return <div className={`jarvis-rise-in ${interactiveCls} ${className}`}>{children}</div>;
}

/** JARVIS button with CSS press state. */
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
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      aria-label={ariaLabel}
      style={{ touchAction: 'manipulation' }}
      className={`transition-transform duration-150 active:scale-[0.96] ${className}`}
    >
      {children}
    </button>
  );
}

export default JARVISPageShell;
