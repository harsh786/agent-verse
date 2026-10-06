/**
 * B1-9: datetime-local inputs hold the user's wall-clock time, but the backend
 * reads a timestamp without a zone as UTC. Convert at the boundary: send an
 * explicit UTC ISO string, and show a stored value back in local time.
 */

const pad = (n: number) => String(n).padStart(2, '0');

/** "2026-10-06T06:30" (local wall-clock) -> "2026-10-06T01:00:00.000Z" in India. */
export function localInputToUtcIso(local: string): string {
  if (!local) return '';
  const d = new Date(local); // a zone-less date-time string is parsed as local time
  return Number.isNaN(d.getTime()) ? '' : d.toISOString();
}

/** A stored ISO value (a zone-less one is UTC, as the backend reads it) as a
 * datetime-local value in the browser's timezone. */
export function utcIsoToLocalInput(iso: string | undefined | null): string {
  if (!iso) return '';
  const d = parseInstant(iso);
  if (Number.isNaN(d.getTime())) return '';
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** A stored ISO value as a Date; a zone-less one is UTC (as the backend reads it). */
export function parseInstant(iso: string): Date {
  const hasZone = /(?:[zZ]|[+-]\d{2}:?\d{2})$/.test(iso);
  return new Date(hasZone ? iso : `${iso}Z`);
}
