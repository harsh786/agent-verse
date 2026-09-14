import type { TriggerSpec } from '../../types';

interface AdvancedOptionsFormProps {
  value: Record<string, unknown>;
  onChange: (v: Record<string, unknown>) => void;
}

/**
 * Cross-cutting trigger options that apply to EVERY trigger type (they live on
 * the backend TriggerSpec regardless of family): a human label, rate cap, expiry,
 * priority, a CEL gate, failure notification, tags, and simulation mode. Rendered
 * as a collapsed section so simple triggers stay simple while production controls
 * are one click away.
 */
export function AdvancedOptionsForm({ value, onChange }: AdvancedOptionsFormProps) {
  function set(key: keyof TriggerSpec, val: unknown) {
    onChange({ ...value, [key]: val });
  }

  const tags = Array.isArray(value.tags) ? (value.tags as string[]) : [];

  return (
    <details className="mt-5 rounded-lg border border-border">
      <summary className="cursor-pointer select-none px-4 py-2.5 text-sm font-medium hover:bg-muted/40 rounded-lg">
        Advanced options
        <span className="ml-2 text-xs font-normal text-muted-foreground">
          rate limit · expiry · priority · condition · notifications
        </span>
      </summary>
      <div className="space-y-4 border-t border-border p-4">
        <Field label="Label" hint="A short human name shown in the triggers list">
          <input
            type="text"
            value={(value.description as string) ?? ''}
            onChange={(e) => set('description', e.target.value)}
            placeholder="e.g. Nightly invoice sync"
            className={inputCls}
          />
        </Field>

        <div className="grid grid-cols-2 gap-4">
          <Field label="Priority">
            <select
              value={(value.priority as string) ?? 'normal'}
              onChange={(e) => set('priority', e.target.value)}
              className={inputCls}
            >
              <option value="high">high</option>
              <option value="normal">normal</option>
              <option value="low">low</option>
            </select>
          </Field>
          <Field label="Max firings / hour" hint="0 = unlimited (rate cap)">
            <input
              type="number"
              min={0}
              value={(value.max_firings_per_hour as number) ?? 0}
              onChange={(e) => set('max_firings_per_hour', Number(e.target.value))}
              className={inputCls}
            />
          </Field>
        </div>

        <Field label="Expires at" hint="Auto-disable the trigger after this time (optional)">
          <input
            type="datetime-local"
            value={(value.expires_at_iso as string) ?? ''}
            onChange={(e) => set('expires_at_iso', e.target.value)}
            className={inputCls}
          />
        </Field>

        <Field label="Condition (CEL)" hint="Gate every fire, e.g. payload.env == 'prod'">
          <input
            type="text"
            value={(value.condition as string) ?? ''}
            onChange={(e) => set('condition', e.target.value)}
            placeholder="payload.amount > 1000"
            className={inputCls}
          />
        </Field>

        <Field label="Notify on failure" hint="Email or Slack channel for dispatch failures">
          <input
            type="text"
            value={(value.on_failure_notify as string) ?? ''}
            onChange={(e) => set('on_failure_notify', e.target.value)}
            placeholder="#ops-alerts or ops@acme.com"
            className={inputCls}
          />
        </Field>

        <Field label="Tags" hint="Comma-separated, for filtering & organisation">
          <input
            type="text"
            value={tags.join(', ')}
            onChange={(e) =>
              set(
                'tags',
                e.target.value
                  .split(',')
                  .map((t) => t.trim())
                  .filter(Boolean),
              )
            }
            placeholder="ops, billing"
            className={inputCls}
          />
        </Field>

        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={Boolean(value.simulation_mode)}
            onChange={(e) => set('simulation_mode', e.target.checked)}
            className="h-4 w-4 rounded border-border"
          />
          <span>
            Simulation mode
            <span className="ml-1 text-xs text-muted-foreground">
              — dispatch & evaluate but never create real goals (dry run)
            </span>
          </span>
        </label>
      </div>
    </details>
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

const inputCls =
  'w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring';
