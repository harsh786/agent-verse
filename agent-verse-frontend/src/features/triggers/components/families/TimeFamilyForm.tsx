import type { TriggerType } from '../../types';

interface FamilyFormProps {
  triggerType: TriggerType;
  value: Record<string, unknown>;
  onChange: (v: Record<string, unknown>) => void;
}

export function TimeFamilyForm({ triggerType, value, onChange }: FamilyFormProps) {
  function set(key: string, val: unknown) {
    onChange({ ...value, [key]: val });
  }

  return (
    <div className="space-y-4">
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
          <Field label="Timezone" hint="IANA timezone, e.g. America/New_York">
            <input
              type="text"
              value={(value.timezone as string) ?? 'UTC'}
              onChange={(e) => set('timezone', e.target.value)}
              className={inputCls}
            />
          </Field>
        </>
      )}
      {triggerType === 'interval' && (
        <Field label="Interval (seconds)">
          <input
            type="number"
            min={60}
            value={(value.interval_seconds as number) ?? 3600}
            onChange={(e) => set('interval_seconds', Number(e.target.value))}
            className={inputCls}
          />
        </Field>
      )}
      {triggerType === 'once' && (
        <Field label="Run At (ISO 8601)">
          <input
            type="datetime-local"
            value={(value.fire_at_iso as string) ?? ''}
            onChange={(e) => set('fire_at_iso', e.target.value)}
            className={inputCls}
          />
        </Field>
      )}
      {triggerType === 'deadline' && (
        <>
          {/* TRG-08: the beat fires from fire_at_iso only; payload deadlines
              (deadline_field) are not supported yet, so the field is not offered. */}
          <Field label="Deadline (ISO 8601)" hint="Times without a zone are read as UTC">
            <input
              type="datetime-local"
              required
              value={(value.fire_at_iso as string) ?? ''}
              onChange={(e) => set('fire_at_iso', e.target.value)}
              className={inputCls}
            />
          </Field>
          <Field label="Warn Before (seconds)">
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
          {/* TRG-08: the delay is added to fire_at_iso; payload-relative
              delays (relative_to_field) are not supported yet. */}
          <Field label="Base Time (ISO 8601)" hint="The offset is added to this time (UTC if no zone)">
            <input
              type="datetime-local"
              required
              value={(value.fire_at_iso as string) ?? ''}
              onChange={(e) => set('fire_at_iso', e.target.value)}
              className={inputCls}
            />
          </Field>
          <Field label="Offset (seconds)" hint="Fire this many seconds after the base time (negative = before)">
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
          {/* TRG-08: fires on this cron, only during business hours (Mon-Fri
              09:00-17:00 in the timezone). Named calendars are not supported yet. */}
          <Field label="Cron Expression" hint="Slots outside business hours (Mon-Fri 9-17) are skipped">
            <input
              type="text"
              required
              value={(value.cron_expression as string) ?? ''}
              onChange={(e) => set('cron_expression', e.target.value)}
              placeholder="0 9 * * 1-5"
              className={`${inputCls} font-mono`}
            />
          </Field>
          <Field label="Timezone" hint="IANA timezone, e.g. America/New_York">
            <input
              type="text"
              value={(value.timezone as string) ?? 'UTC'}
              onChange={(e) => set('timezone', e.target.value)}
              className={inputCls}
            />
          </Field>
        </>
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
