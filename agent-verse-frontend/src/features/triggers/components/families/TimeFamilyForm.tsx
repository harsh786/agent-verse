import { useEffect } from 'react';
import { useAuthStore } from '@/stores/auth';
import type { TriggerType } from '../../types';
import { planMinIntervalSeconds, usePlanFloors } from '../../planFloors';
import { localInputToUtcIso, utcIsoToLocalInput } from '../../datetime';

interface FamilyFormProps {
  triggerType: TriggerType;
  value: Record<string, unknown>;
  onChange: (v: Record<string, unknown>) => void;
}

/**
 * B1-9: the defaults this form shows are also the values it submits. The
 * fields used to display 3600 while sending nothing, so an untouched interval
 * was refused (422 "interval_seconds > 0") and an untouched deadline warning
 * or relative offset ran with 0 instead of the hour on screen.
 */
const SHOWN_DEFAULTS: Partial<Record<TriggerType, Record<string, unknown>>> = {
  interval: { interval_seconds: 3600 },
  deadline: { deadline_warning_seconds: 3600 },
  relative_delay: { relative_offset_seconds: 3600 },
};

const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const DEFAULT_BUSINESS_DAYS = [0, 1, 2, 3, 4];

/** Types whose missed runs follow the catch-up policy (interval never replays). */
const CATCH_UP_TYPES: TriggerType[] = ['cron', 'business_calendar', 'once', 'relative_delay', 'deadline'];

export function TimeFamilyForm({ triggerType, value, onChange }: FamilyFormProps) {
  function set(key: string, val: unknown) {
    onChange({ ...value, [key]: val });
  }

  useEffect(() => {
    const defaults = SHOWN_DEFAULTS[triggerType];
    if (!defaults) return;
    const missing = Object.entries(defaults).filter(([k]) => value[k] === undefined);
    if (missing.length) onChange({ ...value, ...Object.fromEntries(missing) });
    // Only when the type changes: later edits are the user's.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [triggerType]);

  const plan = (useAuthStore((s) => s.plan) || 'free').toLowerCase();
  const floors = usePlanFloors();
  const minSeconds = planMinIntervalSeconds(plan, floors);
  const localZone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'local time';
  const planFloor = (triggerType === 'cron' || triggerType === 'interval' || triggerType === 'business_calendar') && (
    <p className="text-xs text-muted-foreground">
      Your {plan} plan runs a schedule at most every {minSeconds / 60} min; a shorter schedule is refused.
    </p>
  );
  // B1-9: <input type="datetime-local"> is the user's wall-clock time. It used to
  // be sent as-is ("2026-10-06T06:30", no zone), which the backend reads as UTC:
  // a time picked in India ran 5 h 30 min late.
  const instantField = (label: string) => (
    <Field label={label} hint={`Your local time (${localZone}); stored as UTC`}>
      <input
        type="datetime-local"
        required
        value={utcIsoToLocalInput(value.fire_at_iso as string | undefined)}
        onChange={(e) => set('fire_at_iso', localInputToUtcIso(e.target.value))}
        className={inputCls}
      />
    </Field>
  );
  const timezoneField = (
    <Field label="Timezone" hint="IANA timezone, e.g. America/New_York">
      <input
        type="text"
        value={(value.timezone as string) ?? 'UTC'}
        onChange={(e) => set('timezone', e.target.value)}
        className={inputCls}
      />
    </Field>
  );
  // Event-relative when a channel is set, or chosen (empty channel, no fixed time).
  const eventMode =
    triggerType === 'relative_delay' &&
    typeof value.event_channel === 'string' &&
    (value.event_channel !== '' || !value.fire_at_iso);
  const businessDays = Array.isArray(value.business_days) ? (value.business_days as number[]) : DEFAULT_BUSINESS_DAYS;
  const holidays = Array.isArray(value.holidays) ? (value.holidays as string[]) : [];

  return (
    <div className="space-y-4">
      {planFloor}
      {(triggerType === 'cron') && (
        <>
          <Field label="Cron Expression" hint="e.g. 0 9 * * 1-5 (weekdays at 9am)">
            <input
              type="text"
              value={(value.cron_expression as string) ?? ''}
              onChange={(e) => set('cron_expression', e.target.value)}
              placeholder="0 * * * *"
              className={inputCls}
            />
          </Field>
          {timezoneField}
        </>
      )}
      {triggerType === 'interval' && (
        <Field label="Interval (seconds)" hint="The first run is on the next minute; then every interval">
          <input
            type="number"
            min={60}
            value={(value.interval_seconds as number) ?? 3600}
            onChange={(e) => set('interval_seconds', Number(e.target.value))}
            className={inputCls}
          />
        </Field>
      )}
      {triggerType === 'once' && instantField('Run At')}
      {triggerType === 'deadline' && (
        <>
          {instantField('Deadline')}
          <Field label="Warn Before (seconds)" hint="Fires once, this long before the deadline (0 = at the deadline)">
            <input
              type="number"
              min={0}
              value={(value.deadline_warning_seconds as number) ?? 3600}
              onChange={(e) => set('deadline_warning_seconds', Number(e.target.value))}
              className={inputCls}
            />
          </Field>
        </>
      )}
      {triggerType === 'relative_delay' && (
        <>
          <Field label="Counted from">
            <select
              value={eventMode ? 'event' : 'time'}
              onChange={(e) => {
                const next = { ...value };
                if (e.target.value === 'event') {
                  delete next.fire_at_iso;
                  next.event_channel = '';
                } else {
                  delete next.event_channel;
                  delete next.relative_to_field;
                }
                onChange(next);
              }}
              className={inputCls}
            >
              <option value="time">A fixed time</option>
              <option value="event">Each event on a channel</option>
            </select>
          </Field>
          {eventMode ? (
            <>
              <Field
                label="Event channel"
                hint="Each event published to POST /triggers/events/{channel} arms one run"
              >
                <input
                  type="text"
                  required
                  value={(value.event_channel as string) ?? ''}
                  onChange={(e) => set('event_channel', e.target.value)}
                  placeholder="support.escalated"
                  className={inputCls}
                />
              </Field>
              <Field
                label="Count from payload field (optional)"
                hint="Dotted path to a timestamp in the event, e.g. order.delivered_at; empty = when the event arrives"
              >
                <input
                  type="text"
                  value={(value.relative_to_field as string) ?? ''}
                  onChange={(e) => set('relative_to_field', e.target.value)}
                  placeholder="order.delivered_at"
                  className={inputCls}
                />
              </Field>
            </>
          ) : (
            instantField('Base Time')
          )}
          <Field label="Offset (seconds)" hint="Fire this many seconds after the base (negative = before a fixed time)">
            <input
              type="number"
              value={(value.relative_offset_seconds as number) ?? 3600}
              onChange={(e) => set('relative_offset_seconds', Number(e.target.value))}
              className={inputCls}
            />
          </Field>
        </>
      )}
      {triggerType === 'business_calendar' && (
        <>
          <Field label="Cron Expression" hint="Slots outside business days / hours and on holidays are skipped">
            <input
              type="text"
              required
              value={(value.cron_expression as string) ?? ''}
              onChange={(e) => set('cron_expression', e.target.value)}
              placeholder="0 9 * * 1-5"
              className={`${inputCls} font-mono`}
            />
          </Field>
          {timezoneField}
          <Field label="Business days">
            <div className="flex flex-wrap gap-3">
              {WEEKDAYS.map((label, day) => (
                <label key={label} className="flex items-center gap-1 text-sm">
                  <input
                    type="checkbox"
                    checked={businessDays.includes(day)}
                    onChange={(e) =>
                      set(
                        'business_days',
                        e.target.checked
                          ? [...businessDays, day].sort((a, b) => a - b)
                          : businessDays.filter((d) => d !== day),
                      )
                    }
                  />
                  {label}
                </label>
              ))}
            </div>
          </Field>
          <div className="grid grid-cols-2 gap-4">
            <Field label="Business hours from">
              <input
                type="time"
                value={(value.business_hours_start as string) ?? '09:00'}
                onChange={(e) => set('business_hours_start', e.target.value)}
                className={inputCls}
              />
            </Field>
            <Field label="until (exclusive)">
              <input
                type="time"
                value={(value.business_hours_end as string) ?? '17:00'}
                onChange={(e) => set('business_hours_end', e.target.value)}
                className={inputCls}
              />
            </Field>
          </div>
          <Field label="Holidays" hint="One date per line (YYYY-MM-DD, in the timezone above); no run on these days">
            <textarea
              rows={3}
              value={holidays.join('\n')}
              onChange={(e) =>
                set(
                  'holidays',
                  e.target.value
                    .split(/[\n,]/)
                    .map((d) => d.trim())
                    .filter(Boolean),
                )
              }
              placeholder={'2026-11-09\n2026-12-25'}
              className={inputCls}
            />
          </Field>
        </>
      )}
      {CATCH_UP_TYPES.includes(triggerType) && (
        <Field label="Missed runs" hint="What happens to runs missed while the scheduler was down">
          <select
            value={(value.catch_up as string) ?? 'all'}
            onChange={(e) => set('catch_up', e.target.value)}
            className={inputCls}
          >
            <option value="all">Run every missed run (at most 60)</option>
            <option value="latest">Run only the most recent missed run</option>
            <option value="none">Skip missed runs (more than 90 s late)</option>
          </select>
        </Field>
      )}
      {/* Shared optional field */}
      <Field label="Max Firings (0 = unlimited)">
        <input
          type="number"
          min={0}
          value={(value.max_firings_per_hour as number) ?? 0}
          onChange={(e) => set('max_firings_per_hour', Number(e.target.value))}
          className={inputCls}
        />
      </Field>
    </div>
  );
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="block text-sm font-medium mb-1">{label}</label>
      {hint && <p className="text-xs text-muted-foreground mb-1.5">{hint}</p>}
      {children}
    </div>
  );
}

const inputCls = 'w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring font-mono';
