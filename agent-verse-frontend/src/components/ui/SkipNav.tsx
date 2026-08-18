/**
 * SkipNav — XA2/Y9/W (Accessibility): keyboard skip-navigation links.
 *
 * Renders visually hidden links that become visible on focus.
 * Placed at very top of AppLayout so keyboard users can jump to:
 *   • Main content (#main-content)
 *   • Navigation (#sidebar-nav)
 *   • Search (#global-search)
 *
 * Skills:
 *   web-guidelines: WCAG 2.4.1 "Bypass Blocks" — required for WCAG AA
 *   ui-ux-pro-max:  Visible on :focus, high contrast, 44px targets
 */
import { motion, useReducedMotion } from 'framer-motion';

const SKIP_LINKS = [
  { href: '#main-content', label: 'Skip to main content' },
  { href: '#sidebar-nav',  label: 'Skip to navigation' },
] as const;

const SPRING = { type: 'spring', stiffness: 600, damping: 35 } as const;

export function SkipNav() {
  const reduce = useReducedMotion();

  return (
    <div className="sr-only focus-within:not-sr-only" aria-label="Skip navigation">
      {SKIP_LINKS.map(({ href, label }) => (
        <motion.a
          key={href}
          href={href}
          whileFocus={reduce ? {} : { scale: 1.02 }}
          transition={SPRING}
          className={[
            'fixed top-2 left-2 z-[9999]',
            'px-4 py-2 rounded-lg text-sm font-semibold',
            'bg-blue-600 text-white shadow-lg',
            'focus:outline-none focus:ring-4 focus:ring-blue-400/60',
            // Shown only when focused (via parent sr-only override)
            'opacity-0 focus:opacity-100 pointer-events-none focus:pointer-events-auto',
            'transition-opacity',
          ].join(' ')}
          style={{ touchAction: 'manipulation' }}
        >
          {label}
        </motion.a>
      ))}
    </div>
  );
}

export default SkipNav;
