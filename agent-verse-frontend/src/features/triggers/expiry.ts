import type { TriggerSpec } from './types';

const HAS_ZONE = /([zZ]|[+-]\d{2}:?\d{2})$/;

/**
 * True when the trigger's `expires_at_iso` is at or before `now` (TRG-09).
 * Mirrors the backend's `is_trigger_expired`: a timestamp without a zone is
 * read as UTC, and an unparseable one counts as expired (the backend refuses
 * to fire it).
 */
export function isTriggerExpired(spec: Pick<TriggerSpec, 'expires_at_iso'>, now = Date.now()): boolean {
  const raw = spec.expires_at_iso?.trim();
  if (!raw) return false;
  const ms = new Date(HAS_ZONE.test(raw) ? raw : `${raw}Z`).getTime();
  if (Number.isNaN(ms)) return true;
  return ms <= now;
}
