/**
 * Shared status colours for the Model Registry (Fast Refresh: no components here).
 *
 * The app is dark by default (`:root` carries the dark theme tokens) and light
 * only under `html.light` (see app/globals.css) — Tailwind's `dark:` variant is
 * not reliable here because the `dark` class is not always set. So every tone
 * is written dark-first, with a `[.light_&]:` override for the light theme.
 * Neutral surfaces use the theme tokens (card / border / muted / foreground).
 *
 * One style per meaning so a status always looks the same: success = can serve
 * / primary, warning = skipped or needs attention, danger = refused / failed,
 * info = fallback, neutral = a fact, primary = a setting.
 */
export type BadgeTone = 'success' | 'warning' | 'danger' | 'info' | 'neutral' | 'primary' | 'solid-success';

export const TONE_CLASSES: Record<BadgeTone, string> = {
  // White on emerald-700 is 5.5:1 in both themes.
  'solid-success': 'bg-emerald-700 text-white',
  success: 'bg-emerald-500/15 text-emerald-300 [.light_&]:bg-emerald-100 [.light_&]:text-emerald-900',
  warning: 'bg-amber-500/15 text-amber-300 [.light_&]:bg-amber-100 [.light_&]:text-amber-900',
  danger: 'bg-red-500/15 text-red-300 [.light_&]:bg-red-100 [.light_&]:text-red-900',
  info: 'bg-sky-500/15 text-sky-300 [.light_&]:bg-sky-100 [.light_&]:text-sky-900',
  primary: 'bg-primary/15 text-foreground ring-1 ring-inset ring-primary/40',
  neutral: 'border border-border text-muted-foreground',
};

/** Panel (callout) colours per tone, for result cards and notices. */
export const PANEL_CLASSES: Record<'success' | 'warning' | 'danger' | 'neutral', string> = {
  success: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-100 [.light_&]:border-emerald-300 [.light_&]:bg-emerald-50 [.light_&]:text-emerald-900',
  warning: 'border-amber-500/40 bg-amber-500/10 text-amber-100 [.light_&]:border-amber-300 [.light_&]:bg-amber-50 [.light_&]:text-amber-900',
  danger: 'border-red-500/40 bg-red-500/10 text-red-100 [.light_&]:border-red-300 [.light_&]:bg-red-50 [.light_&]:text-red-900',
  neutral: 'border-border bg-muted/40 text-foreground',
};

/** Inline text colour for warnings / successes inside neutral surfaces. */
export const TEXT_TONE = {
  warning: 'text-amber-300 [.light_&]:text-amber-800',
  success: 'text-emerald-300 [.light_&]:text-emerald-800',
  danger: 'text-destructive',
} as const;

/** Row / chip states used by the capability sections and the coverage strip. */
export const STATE_CLASSES = {
  primaryRow: 'border-emerald-500/60 bg-emerald-500/10 [.light_&]:border-emerald-500 [.light_&]:bg-emerald-50',
  dirtySection: 'border-amber-500/70 [.light_&]:border-amber-400',
  chipReady: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-200 [.light_&]:border-emerald-300 [.light_&]:bg-emerald-50 [.light_&]:text-emerald-900',
  chipNotReady: 'border-amber-500/40 bg-amber-500/10 text-amber-200 [.light_&]:border-amber-300 [.light_&]:bg-amber-50 [.light_&]:text-amber-900',
} as const;
