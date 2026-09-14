import type { TriggerType } from '../../types';

interface FamilyFormProps {
  triggerType: TriggerType;
  value: Record<string, unknown>;
  onChange: (v: Record<string, unknown>) => void;
}

export function ConditionFamilyForm({ triggerType, value, onChange }: FamilyFormProps) {
  function set(key: string, val: unknown) {
    onChange({ ...value, [key]: val });
  }

  const compoundTriggerIds = Array.isArray(value.compound_trigger_ids)
    ? (value.compound_trigger_ids as string[])
    : [];

  return (
    <div className="space-y-4">
      {triggerType === 'condition' && (
        <>
          <Field
            label="CEL Condition Expression"
            hint="Evaluated against trigger payload. e.g. payload.amount > 1000"
          >
            <textarea
              value={(value.condition_expression as string) ?? ''}
              onChange={(e) => set('condition_expression', e.target.value)}
              placeholder='payload.amount > 1000 && payload.currency == "USD"'
              rows={4}
              className={`${inputCls} font-mono resize-y`}
            />
          </Field>
          <div className="rounded-lg bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 px-3 py-2 text-xs text-blue-700 dark:text-blue-300">
            CEL expressions have access to <code className="font-mono">payload.*</code> fields.
            Expressions timeout after 500ms.
          </div>
        </>
      )}
      {triggerType === 'state_transition' && (
        <>
          <Field label="State Machine ID">
            <input
              type="text"
              value={(value.state_machine_id as string) ?? ''}
              onChange={(e) => set('state_machine_id', e.target.value)}
              placeholder="machine-uuid"
              className={`${inputCls} font-mono`}
            />
          </Field>
          <Field label="From State (optional)" hint="Leave blank to match any source state">
            <input
              type="text"
              value={(value.from_state as string) ?? ''}
              onChange={(e) => set('from_state', e.target.value)}
              placeholder="pending"
              className={inputCls}
            />
          </Field>
          <Field label="To State" hint="The target state that triggers this automation">
            <input
              type="text"
              value={(value.to_state as string) ?? ''}
              onChange={(e) => set('to_state', e.target.value)}
              placeholder="completed"
              className={inputCls}
            />
          </Field>
        </>
      )}
      {triggerType === 'counter_threshold' && (
        <>
          <Field label="Counter Key" hint="Unique identifier for the counter (e.g. api_calls_per_user)">
            <input
              type="text"
              value={(value.counter_key as string) ?? ''}
              onChange={(e) => set('counter_key', e.target.value)}
              placeholder="api_calls_per_user"
              className={`${inputCls} font-mono`}
            />
          </Field>
          <Field label="Threshold">
            <input
              type="number"
              min={1}
              value={(value.counter_threshold as number) ?? 100}
              onChange={(e) => set('counter_threshold', Number(e.target.value))}
              className={inputCls}
            />
          </Field>
          <Field label="Window (seconds)">
            <input
              type="number"
              min={60}
              value={(value.counter_window_secs as number) ?? 3600}
              onChange={(e) => set('counter_window_secs', Number(e.target.value))}
              className={inputCls}
            />
          </Field>
        </>
      )}
      {triggerType === 'window_aggregate' && (
        <>
          <Field label="Window Field" hint="JSONPath of the numeric field to aggregate, e.g. payload.amount">
            <input
              type="text"
              value={(value.window_field as string) ?? ''}
              onChange={(e) => set('window_field', e.target.value)}
              placeholder="payload.amount"
              className={`${inputCls} font-mono`}
            />
          </Field>
          <Field label="Aggregation">
            <select
              value={(value.window_aggregation as string) ?? 'sum'}
              onChange={(e) => set('window_aggregation', e.target.value)}
              className={inputCls}
            >
              <option value="sum">sum</option>
              <option value="avg">avg</option>
              <option value="max">max</option>
              <option value="min">min</option>
              <option value="count">count</option>
            </select>
          </Field>
          <Field label="Window (seconds)" hint="Rolling window over which values are aggregated">
            <input
              type="number"
              min={1}
              value={(value.window_seconds as number) ?? 3600}
              onChange={(e) => set('window_seconds', Number(e.target.value))}
              className={inputCls}
            />
          </Field>
          <Field label="Threshold">
            <input
              type="number"
              min={0}
              step={0.01}
              value={(value.window_threshold as number) ?? 100}
              onChange={(e) => set('window_threshold', Number(e.target.value))}
              className={inputCls}
            />
          </Field>
        </>
      )}
      {triggerType === 'compound' && (
        <>
          <Field label="Combine Logic" hint="How the child triggers combine to fire">
            <select
              value={(value.compound_logic as string) ?? 'AND'}
              onChange={(e) => set('compound_logic', e.target.value)}
              className={inputCls}
            >
              <option value="AND">AND (all must fire)</option>
              <option value="OR">OR (any may fire)</option>
            </select>
          </Field>
          <Field label="Child Trigger IDs" hint="Comma-separated trigger IDs to combine">
            <input
              type="text"
              value={compoundTriggerIds.join(', ')}
              onChange={(e) =>
                set(
                  'compound_trigger_ids',
                  e.target.value
                    .split(',')
                    .map((id) => id.trim())
                    .filter(Boolean),
                )
              }
              placeholder="trig-abc, trig-def"
              className={`${inputCls} font-mono`}
            />
          </Field>
        </>
      )}
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

const inputCls = 'w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring';
