import { X, Zap, RefreshCw } from 'lucide-react';
import type { Trigger } from '../types';
import { TRIGGER_FAMILY_LABELS, TRIGGER_TYPE_FAMILY } from '../types';
import { useSimulateTrigger, useFireTriggerNow } from '../hooks';
import { TriggerStatusBadge } from './TriggerStatusBadge';
import { TriggerHistoryPanel } from './TriggerHistoryPanel';

interface TriggerDetailDrawerProps {
  trigger: Trigger;
  onClose: () => void;
}

export function TriggerDetailDrawer({ trigger, onClose }: TriggerDetailDrawerProps) {
  const family = TRIGGER_TYPE_FAMILY[trigger.spec.trigger_type];
  const simulate = useSimulateTrigger();
  const fireNow = useFireTriggerNow();

  return (
    <div
      className="fixed inset-0 z-50 flex"
      role="dialog"
      aria-modal="true"
      aria-label={`Trigger detail: ${trigger.spec.name ?? trigger.spec.trigger_type}`}
    >
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/40" onClick={onClose} />

      {/* Drawer */}
      <div className="relative ml-auto flex h-full w-full max-w-lg flex-col bg-background shadow-xl overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-border px-5 py-4">
          <div>
            <div className="flex items-center gap-2">
              <span className="rounded bg-muted px-2 py-0.5 text-xs font-mono">
                {trigger.spec.trigger_type}
              </span>
              <span className="text-xs text-muted-foreground">
                {TRIGGER_FAMILY_LABELS[family]}
              </span>
            </div>
            <h2 className="mt-1 text-base font-semibold">
              {trigger.spec.name ?? 'Trigger Detail'}
            </h2>
            <div className="mt-1">
              <TriggerStatusBadge paused={trigger.paused} />
            </div>
          </div>
          <button
            onClick={onClose}
            aria-label="Close trigger detail"
            className="rounded-md p-2 text-muted-foreground hover:bg-muted transition-colors"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-5 space-y-5">
          {/* Goal template */}
          <Section title="Goal Template">
            <code className="block rounded-lg bg-muted p-3 text-sm whitespace-pre-wrap font-mono">
              {trigger.goal_template}
            </code>
          </Section>

          {/* Spec fields */}
          <Section title="Configuration">
            <SpecFields spec={trigger.spec} />
          </Section>

          {/* Timing */}
          <Section title="Timing">
            <dl className="grid grid-cols-2 gap-2 text-sm">
              {trigger.next_fire_at && (
                <>
                  <dt className="text-muted-foreground">Next fire</dt>
                  <dd>{new Date(trigger.next_fire_at).toLocaleString()}</dd>
                </>
              )}
              {trigger.last_fired_at && (
                <>
                  <dt className="text-muted-foreground">Last fired</dt>
                  <dd>{new Date(trigger.last_fired_at).toLocaleString()}</dd>
                </>
              )}
              <dt className="text-muted-foreground">Total fires</dt>
              <dd>{trigger.fire_count ?? 0}</dd>
              <dt className="text-muted-foreground">Status</dt>
              <dd className={trigger.paused ? 'text-amber-600' : 'text-emerald-600'}>
                {trigger.paused ? 'Paused' : 'Active'}
              </dd>
            </dl>
          </Section>

          {/* Simulate */}
          <Section title="Simulate">
            <button
              onClick={() => simulate.mutate({ scheduleId: trigger.schedule_id })}
              disabled={simulate.isPending}
              className="inline-flex items-center gap-2 rounded-lg border border-border px-3 py-2 text-sm hover:bg-muted transition-colors disabled:opacity-50"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${simulate.isPending ? 'animate-spin' : ''}`} />
              Run Simulation
            </button>
            {simulate.data && (
              <div className="mt-3 rounded-lg bg-muted p-3 text-xs font-mono whitespace-pre-wrap">
                {JSON.stringify(simulate.data, null, 2)}
              </div>
            )}
          </Section>

          {/* Events */}
          <Section title="Recent Events">
            <TriggerHistoryPanel scheduleId={trigger.schedule_id} />
          </Section>
        </div>

        {/* Footer actions */}
        <div className="border-t border-border px-5 py-4 flex gap-2 justify-end">
          <button
            onClick={() => fireNow.mutate({ scheduleId: trigger.schedule_id })}
            disabled={fireNow.isPending || trigger.paused}
            className="inline-flex items-center gap-2 rounded-lg bg-primary text-primary-foreground px-4 py-2 text-sm font-medium hover:bg-primary/90 transition-colors disabled:opacity-50"
          >
            <Zap className="h-4 w-4" />
            {fireNow.isPending ? 'Firing…' : 'Fire Now'}
          </button>
        </div>
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-2">{title}</h3>
      {children}
    </div>
  );
}

function SpecFields({ spec }: { spec: Trigger['spec'] }) {
  const exclude = new Set(['trigger_id', 'trigger_type', 'name', 'description', 'webhook_secret']);
  const fields = Object.entries(spec).filter(
    ([k, v]) => !exclude.has(k) && v !== undefined && v !== '' && v !== null
  );
  if (!fields.length) return <p className="text-sm text-muted-foreground">No extra configuration.</p>;
  return (
    <dl className="grid grid-cols-2 gap-2 text-sm">
      {fields.map(([k, v]) => (
        <>
          <dt key={`dt-${k}`} className="text-muted-foreground font-mono text-xs">{k}</dt>
          <dd key={`dd-${k}`} className="font-mono text-xs break-all">{JSON.stringify(v as unknown)}</dd>
        </>
      ))}
    </dl>
  );
}
