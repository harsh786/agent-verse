import type { TriggerType } from '../../types';

interface FamilyFormProps {
  triggerType: TriggerType;
  value: Record<string, unknown>;
  onChange: (v: Record<string, unknown>) => void;
}

export function GoalChainFamilyForm({ triggerType, value, onChange }: FamilyFormProps) {
  function set(key: string, val: unknown) {
    onChange({ ...value, [key]: val });
  }

  return (
    <div className="space-y-4">
      <Field label="Watch Goal ID (optional)" hint="Leave blank to match all goals">
        <input
          type="text"
          value={(value.watch_goal_id as string) ?? ''}
          onChange={(e) => set('watch_goal_id', e.target.value)}
          placeholder="goal-uuid"
          className={inputCls}
        />
      </Field>
      <Field label="Watch Agent ID (optional)">
        <input
          type="text"
          value={(value.watch_agent_id as string) ?? ''}
          onChange={(e) => set('watch_agent_id', e.target.value)}
          placeholder="agent-uuid"
          className={inputCls}
        />
      </Field>
      {(triggerType === 'goal_score_below' || triggerType === 'goal_score_above') && (
        <Field label="Score Threshold (0.0–1.0)">
          <input
            type="number"
            min={0}
            max={1}
            step={0.05}
            value={(value.score_threshold as number) ?? 0.7}
            onChange={(e) => set('score_threshold', Number(e.target.value))}
            className={inputCls}
          />
        </Field>
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

const inputCls = 'w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring font-mono';
