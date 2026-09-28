/**
 * Formatting helpers for metrics the backend may legitimately not have.
 *
 * Benchmark endpoints return `null` for any figure they could not compute
 * (no goals yet, or too few tenants for a k-anonymous platform average).
 * Rendering that as `0` / `0.0%` would present an invented number, and calling
 * `.toFixed` on it crashes the page — so every formatter here maps a missing
 * value to an em dash instead.
 */

/** Placeholder rendered in place of a metric the backend could not compute. */
export const NO_VALUE = '—';

/** Message shown where a whole metric block has no data behind it. */
export const NOT_ENOUGH_DATA = 'Not enough data yet';

/** True for a real, finite number (not null / undefined / NaN / Infinity). */
export function isMetric(v: unknown): v is number {
  return typeof v === 'number' && Number.isFinite(v);
}

/** 0..1 ratio → "83.0%". */
export function fmtRatioPct(v: number | null | undefined, digits = 1): string {
  return isMetric(v) ? `${(v * 100).toFixed(digits)}%` : NO_VALUE;
}

/** Plain number with fixed digits. */
export function fmtFixed(v: number | null | undefined, digits = 2): string {
  return isMetric(v) ? v.toFixed(digits) : NO_VALUE;
}

/** USD amount; sub-dollar amounts keep 4 decimals so tiny per-goal costs stay visible. */
export function fmtUsd(v: number | null | undefined): string {
  if (!isMetric(v)) return NO_VALUE;
  return v >= 1 ? `$${v.toFixed(2)}` : `$${v.toFixed(4)}`;
}
