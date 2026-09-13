import type { TriggerType } from '../../types';

interface FamilyFormProps {
  triggerType: TriggerType;
  value: Record<string, unknown>;
  onChange: (v: Record<string, unknown>) => void;
}

/**
 * Generic form for families that don't have specialised forms yet.
 * Renders a CEL condition field + a raw JSON field for additional spec overrides.
 */
export function GenericFamilyForm({ triggerType, value, onChange }: FamilyFormProps) {
  function set(key: string, val: unknown) {
    onChange({ ...value, [key]: val });
  }

  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-amber-200 bg-amber-50 dark:bg-amber-900/20 dark:border-amber-800 px-3 py-2 text-xs text-amber-700 dark:text-amber-300">
        <strong>{triggerType}</strong> — configure with the CEL condition and JSON fields below.
      </div>
      <Field label="Condition CEL (optional)" hint="e.g. payload.severity == 'critical'">
        <input
          type="text"
          value={(value.condition as string) ?? ''}
          onChange={(e) => set('condition', e.target.value)}
          placeholder="payload.value > 100"
          className={inputCls}
        />
      </Field>
      <Field
        label="Extra configuration (JSON)"
        hint="Advanced: raw spec fields as JSON object"
      >
        <textarea
          rows={4}
          value={(value._raw_extra as string) ?? ''}
          onChange={(e) => {
            const raw = e.target.value;
            set('_raw_extra', raw);
            try {
              const parsed = JSON.parse(raw);
              onChange({ ...value, ...parsed, _raw_extra: raw });
            } catch {
              /* ignore parse errors while typing */
            }
          }}
          placeholder='{"mqtt_topic": "sensors/+/temp"}'
          className={`${inputCls} resize-y`}
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
