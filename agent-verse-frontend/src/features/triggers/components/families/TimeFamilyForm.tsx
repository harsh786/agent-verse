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
      {(triggerType === 'cron' || triggerType === 'rate_limited_schedule') && (
        <Field label="Cron Expression" hint="e.g. 0 9 * * 1-5 (weekdays at 9am)">
          <input
            type="text"
            value={(value.cron_expression as string) ?? ''}
            onChange={(e) => set('cron_expression', e.target.value)}
            placeholder="0 * * * *"
            className={inputCls}
          />
        </Field>
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
      {triggerType === 'one_shot' && (
        <Field label="Run At (ISO 8601)">
          <input
            type="datetime-local"
            value={(value.run_at as string) ?? ''}
            onChange={(e) => set('run_at', e.target.value)}
            className={inputCls}
          />
        </Field>
      )}
      {triggerType === 'deadline' && (
        <>
          <Field label="Deadline (ISO 8601)">
            <input
              type="datetime-local"
              value={(value.run_at as string) ?? ''}
              onChange={(e) => set('run_at', e.target.value)}
              className={inputCls}
            />
          </Field>
          <Field label="Warn Before (seconds)">
            <input
              type="number"
              min={0}
              value={(value.warn_before_seconds as number) ?? 3600}
              onChange={(e) => set('warn_before_seconds', Number(e.target.value))}
              className={inputCls}
            />
          </Field>
        </>
      )}
      {triggerType === 'business_hours' && (
        <Field label="Timezone" hint="IANA timezone, e.g. America/New_York">
          <input
            type="text"
            value={(value.timezone as string) ?? 'UTC'}
            onChange={(e) => set('timezone', e.target.value)}
            className={inputCls}
          />
        </Field>
      )}
      {/* Shared optional field */}
      <Field label="Max Firings (0 = unlimited)">
        <input
          type="number"
          min={0}
          value={(value.max_firings as number) ?? 0}
          onChange={(e) => set('max_firings', Number(e.target.value))}
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
