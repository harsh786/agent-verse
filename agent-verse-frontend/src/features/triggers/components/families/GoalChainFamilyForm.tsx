import { useEffect } from 'react';
import type { TriggerType } from '../../types';

// Defaults the form shows; sent as well, so the saved trigger matches the
// screen (B7-5: a goal_score_below saved without a threshold could never fire).
const SHOWN_DEFAULTS: Partial<Record<TriggerType, Record<string, unknown>>> = {
  goal_score_below: { score_threshold: 0.7 },
};

interface FamilyFormProps {
  triggerType: TriggerType;
  value: Record<string, unknown>;
  onChange: (v: Record<string, unknown>) => void;
}

export function GoalChainFamilyForm({ triggerType, value, onChange }: FamilyFormProps) {
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
      <Field
        label="Watch goals from agent (optional)"
        hint="Only fire for goals run by this agent — blank matches every agent. The agent this trigger runs is chosen separately under “Run as agent”."
      >
        <input
          type="text"
          value={(value.watch_agent_id as string) ?? ''}
          onChange={(e) => set('watch_agent_id', e.target.value)}
          placeholder="agent-uuid"
          className={inputCls}
        />
      </Field>
      {(triggerType === 'goal_score_below') && (
        <>
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
          <Field label="Score Dimension (optional)" hint="Which eval dimension to compare, e.g. accuracy (blank = overall)">
            <input
              type="text"
              value={(value.score_dimension as string) ?? ''}
              onChange={(e) => set('score_dimension', e.target.value)}
              placeholder="accuracy"
              className={inputCls}
            />
          </Field>
        </>
      )}
      {(triggerType === 'hitl_approved' || triggerType === 'hitl_rejected') && (
        <Field
          label="HITL Queue (optional)"
          hint="Only approvals for one agent (agent:<agent_id>) or one risk tier (risk:high, risk:critical, risk:write_high, …)"
        >
          <input
            type="text"
            value={(value.hitl_queue_id as string) ?? ''}
            onChange={(e) => set('hitl_queue_id', e.target.value)}
            placeholder="agent:<agent_id> or risk:high"
            className={inputCls}
          />
        </Field>
      )}
      <label className="flex items-start gap-2 text-sm">
        <input
          type="checkbox"
          className="mt-0.5"
          checked={value.allow_self_trigger === true}
          onChange={(e) => set('allow_self_trigger', e.target.checked)}
        />
        <span>
          Also fire on events from goals it started
          <span className="block text-xs text-muted-foreground">
            Off by default so a trigger cannot loop on its own output. Chains stop after 10 levels either way.
          </span>
        </span>
      </label>
      {triggerType === 'memory_created' && (
        <Field label="Memory Type (optional)" hint="Filter by memory type, e.g. learning, fact">
          <input
            type="text"
            value={(value.memory_type as string) ?? ''}
            onChange={(e) => set('memory_type', e.target.value)}
            placeholder="learning"
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
