import type { GovernancePolicy } from '@/lib/api/client';

/** ISO weekday index (0 = Monday), as the policy engine stores them. */
export const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

const hh = (h: number) => `${String(h).padStart(2, '0')}:00`;

/** Active hours as ranges, e.g. [9, 10, 11, 14] → "09:00–12:00, 14:00–15:00". */
export function formatActiveHours(hours: number[]): string {
  const sorted = [...new Set(hours)].sort((a, b) => a - b);
  const ranges: string[] = [];
  let start = sorted[0];
  for (let i = 0; i < sorted.length; i++) {
    if (sorted[i + 1] !== sorted[i] + 1) {
      ranges.push(`${hh(start)}–${hh(sorted[i] + 1)}`);
      start = sorted[i + 1];
    }
  }
  return ranges.join(', ');
}

/** Does the policy apply only inside a time window (hours and/or weekdays)? */
export function hasTimeWindow(p: GovernancePolicy): boolean {
  return Boolean(p.allowed_hours_utc?.length || p.allowed_weekdays?.length);
}

/** "09:00–17:00 Europe/Berlin" (hours are read in the policy's own timezone). */
export function formatWindowHours(p: GovernancePolicy): string {
  return p.allowed_hours_utc?.length
    ? `${formatActiveHours(p.allowed_hours_utc)} ${p.timezone ?? 'UTC'}`
    : 'all day';
}

export function formatWindowDays(p: GovernancePolicy): string {
  return p.allowed_weekdays?.length
    ? p.allowed_weekdays.map((d) => WEEKDAYS[d] ?? d).join(', ')
    : 'every day';
}
