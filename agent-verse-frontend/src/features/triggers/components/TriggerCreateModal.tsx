import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { X, ChevronRight } from 'lucide-react';
import type { TriggerFamily, TriggerType, CreateTriggerRequest } from '../types';
import { TRIGGER_FAMILY_LABELS, TRIGGER_TYPE_FAMILY, SUPPORTED_TRIGGER_TYPES } from '../types';
import { useCreateTrigger } from '../hooks';
import { agentsApi, goalsApi } from '@/lib/api/client';
import { TimeFamilyForm } from './families/TimeFamilyForm';
import { GoalChainFamilyForm } from './families/GoalChainFamilyForm';
import { WebhookFamilyForm } from './families/WebhookFamilyForm';
import { ConversationalFamilyForm } from './families/ConversationalFamilyForm';
import { ConditionFamilyForm } from './families/ConditionFamilyForm';
import { DataFamilyForm } from './families/DataFamilyForm';
import { MonitoringFamilyForm } from './families/MonitoringFamilyForm';
import { IoTFamilyForm } from './families/IoTFamilyForm';
import { PollingFamilyForm } from './families/PollingFamilyForm';
import { GenericFamilyForm } from './families/GenericFamilyForm';
import { AdvancedOptionsForm } from './families/AdvancedOptionsForm';

type Step = 'family' | 'type' | 'config' | 'confirm';

interface TriggerCreateModalProps {
  onClose: () => void;
}

// Derived from the canonical maps so it never drifts: only backend
// dispatch-supported types are offered, grouped by their declared family.
const FAMILY_TYPES: Record<TriggerFamily, TriggerType[]> = (() => {
  const out = Object.fromEntries(
    (Object.keys(TRIGGER_FAMILY_LABELS) as TriggerFamily[]).map((f) => [f, [] as TriggerType[]]),
  ) as Record<TriggerFamily, TriggerType[]>;
  for (const [type, family] of Object.entries(TRIGGER_TYPE_FAMILY) as [TriggerType, TriggerFamily][]) {
    if (SUPPORTED_TRIGGER_TYPES.has(type)) out[family].push(type);
  }
  return out;
})();

// Families that currently have at least one creatable type (hide empty ones).
const CREATABLE_FAMILIES = (Object.keys(TRIGGER_FAMILY_LABELS) as TriggerFamily[]).filter(
  (f) => FAMILY_TYPES[f].length > 0,
);

const FAMILY_DESCRIPTIONS: Record<TriggerFamily, string> = {
  time: 'Schedule goals at fixed times, intervals, or calendar events',
  goal_chain: 'React to goal lifecycle events and chain automations',
  conversational: 'Respond to chat commands, emails, forms, and voice',
  webhook: 'Fire on GitHub, Stripe, Jira, PagerDuty, and custom webhooks',
  data: 'Watch for database changes, file events, and API responses',
  monitoring: 'Trigger from metrics, log patterns, and alerting systems',
  state_condition: 'Evaluate conditions, feature flags, and state transitions',
  ml_signal: 'React to model drift, anomalies, and ML predictions',
  iot: 'Handle MQTT messages, geofence events, and sensor readings',
};

export function TriggerCreateModal({ onClose }: TriggerCreateModalProps) {
  const [step, setStep] = useState<Step>('family');
  const [selectedFamily, setSelectedFamily] = useState<TriggerFamily | null>(null);
  const [selectedType, setSelectedType] = useState<TriggerType | null>(null);
  const [specFields, setSpecFields] = useState<Record<string, unknown>>({});
  const [goalTemplate, setGoalTemplate] = useState('');
  const [agentId, setAgentId] = useState('');
  const [goalId, setGoalId] = useState('');

  const create = useCreateTrigger();
  // Existing agents to reference — a trigger can just run an agent's own goal.
  const { data: agents = [] } = useQuery({ queryKey: ['agents'], queryFn: agentsApi.list });
  // Existing goals to bind to — binding a concrete goal_id avoids the noise of a
  // free-text template matching many goals (each fire re-runs THIS goal).
  const { data: goalsResp } = useQuery({
    queryKey: ['goals', 'trigger-picker'],
    queryFn: () => goalsApi.list({ page_size: 50 }),
  });
  const goals = goalsResp?.goals ?? [];

  function handleSubmit() {
    if (!selectedType) return;
    const req: CreateTriggerRequest = {
      spec: {
        trigger_type: selectedType,
        ...specFields,
      },
      goal_id: goalId,
      agent_id: agentId || undefined,
      goal_template: goalTemplate,
    };
    create.mutate(req, {
      onSuccess: () => onClose(),
    });
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" role="dialog" aria-modal="true" aria-label="Create trigger">
      <div className="absolute inset-0 bg-black/40" onClick={onClose} />
      <div className="relative w-full max-w-2xl rounded-xl bg-background shadow-xl flex flex-col max-h-[90vh] overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-border px-5 py-4">
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            {(['family', 'type', 'config', 'confirm'] as Step[]).map((s, i) => (
              <span key={s} className={`flex items-center gap-1 ${step === s ? 'text-foreground font-medium' : ''}`}>
                {i > 0 && <ChevronRight className="h-3 w-3" />}
                {s.charAt(0).toUpperCase() + s.slice(1)}
              </span>
            ))}
          </div>
          <button onClick={onClose} aria-label="Close" className="rounded-md p-2 hover:bg-muted transition-colors">
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-5">
          {/* Step 1: Select family */}
          {step === 'family' && (
            <div>
              <h2 className="text-base font-semibold mb-4">Choose a trigger family</h2>
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                {CREATABLE_FAMILIES.map((family) => (
                  <button
                    key={family}
                    onClick={() => { setSelectedFamily(family); setStep('type'); }}
                    className="rounded-xl border border-border p-4 text-left hover:border-primary hover:bg-primary/5 transition-all group"
                  >
                    <div className="font-medium text-sm group-hover:text-primary">{TRIGGER_FAMILY_LABELS[family]}</div>
                    <div className="text-xs text-muted-foreground mt-1">{FAMILY_DESCRIPTIONS[family]}</div>
                    <div className="text-xs text-muted-foreground mt-2">{FAMILY_TYPES[family].length} types</div>
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Step 2: Select type */}
          {step === 'type' && selectedFamily && (
            <div>
              <button onClick={() => setStep('family')} className="text-sm text-muted-foreground hover:text-foreground mb-4 flex items-center gap-1">
                ← Back
              </button>
              <h2 className="text-base font-semibold mb-4">{TRIGGER_FAMILY_LABELS[selectedFamily]}</h2>
              <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
                {FAMILY_TYPES[selectedFamily].map((type) => (
                  <button
                    key={type}
                    onClick={() => { setSelectedType(type); setStep('config'); }}
                    className="rounded-lg border border-border px-3 py-2 text-left text-sm font-mono hover:border-primary hover:bg-primary/5 transition-all"
                  >
                    {type}
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Step 3: Configure */}
          {step === 'config' && selectedType && selectedFamily && (
            <div>
              <button onClick={() => setStep('type')} className="text-sm text-muted-foreground hover:text-foreground mb-4 flex items-center gap-1">
                ← Back
              </button>
              <h2 className="text-base font-semibold mb-4">Configure <code className="font-mono bg-muted rounded px-1.5 py-0.5">{selectedType}</code></h2>

              {/* Family-specific form */}
              {selectedFamily === 'time' && (
                <TimeFamilyForm triggerType={selectedType} value={specFields} onChange={setSpecFields} />
              )}
              {selectedFamily === 'goal_chain' && (
                <GoalChainFamilyForm triggerType={selectedType} value={specFields} onChange={setSpecFields} />
              )}
              {selectedFamily === 'webhook' && (
                <WebhookFamilyForm triggerType={selectedType} value={specFields} onChange={setSpecFields} />
              )}
              {selectedFamily === 'conversational' && (
                <ConversationalFamilyForm triggerType={selectedType} value={specFields} onChange={setSpecFields} />
              )}
              {selectedFamily === 'state_condition' && (
                <ConditionFamilyForm triggerType={selectedType} value={specFields} onChange={setSpecFields} />
              )}
              {selectedFamily === 'data' && (
                <DataFamilyForm triggerType={selectedType} value={specFields} onChange={setSpecFields} />
              )}
              {selectedFamily === 'monitoring' && (
                <MonitoringFamilyForm triggerType={selectedType} value={specFields} onChange={setSpecFields} />
              )}
              {selectedFamily === 'iot' && (
                <IoTFamilyForm triggerType={selectedType} value={specFields} onChange={setSpecFields} />
              )}
              {selectedFamily === 'ml_signal' && (
                <PollingFamilyForm triggerType={selectedType} value={specFields} onChange={setSpecFields} />
              )}
              {!['time', 'goal_chain', 'webhook', 'conversational', 'state_condition', 'data', 'monitoring', 'iot', 'ml_signal'].includes(selectedFamily) && (
                <GenericFamilyForm triggerType={selectedType} value={specFields} onChange={setSpecFields} />
              )}

              {/* Cross-cutting production controls — apply to every trigger type */}
              <AdvancedOptionsForm value={specFields} onChange={setSpecFields} />

              {/* Common fields — reference an existing agent and/or a goal.
                  A trigger needs at least one: pick an agent to run its own
                  configured goal, or write a goal template (or both). */}
              <div className="mt-5 space-y-4">
                <div>
                  <label className="text-sm font-medium" htmlFor="goal-id">Bind to an existing goal</label>
                  <select
                    id="goal-id"
                    value={goalId}
                    onChange={(e) => setGoalId(e.target.value)}
                    className="mt-1.5 w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
                  >
                    <option value="">None — use a template or agent below</option>
                    {goals.map((g) => {
                      const id = g.goal_id ?? g.id;
                      const label = g.goal.length > 60 ? `${g.goal.slice(0, 60)}…` : g.goal;
                      return (
                        <option key={id} value={id}>
                          {label} · {g.status} · {id.slice(0, 8)}
                        </option>
                      );
                    })}
                  </select>
                  <p className="mt-1 text-xs text-muted-foreground">
                    Re-runs this exact goal on every fire — the precise choice when
                    several goals share similar text (no template ambiguity).
                  </p>
                </div>
                <div>
                  <label className="text-sm font-medium" htmlFor="agent-id">Run as agent</label>
                  <select
                    id="agent-id"
                    value={agentId}
                    onChange={(e) => setAgentId(e.target.value)}
                    className="mt-1.5 w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
                  >
                    <option value="">Auto-route (no specific agent)</option>
                    {agents.map((a) => (
                      <option key={a.agent_id} value={a.agent_id}>{a.name}</option>
                    ))}
                  </select>
                  <p className="mt-1 text-xs text-muted-foreground">
                    Reference an agent you already created — the trigger runs that agent
                    (and its own goal) on each fire.
                  </p>
                </div>
                <div>
                  <label className="text-sm font-medium" htmlFor="goal-template">
                    Goal Template <span className="text-muted-foreground font-normal">(optional)</span>
                  </label>
                  <textarea
                    id="goal-template"
                    value={goalTemplate}
                    onChange={(e) => setGoalTemplate(e.target.value)}
                    placeholder={agentId
                      ? "Leave blank to run the selected agent's own goal, or override it here…"
                      : 'Describe the goal to create when this trigger fires…'}
                    rows={3}
                    className="mt-1.5 w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring resize-none"
                  />
                  <p className="mt-1 text-xs text-muted-foreground">
                    {agentId
                      ? "Optional override — blank uses the agent's goal."
                      : 'Required unless you selected an agent above.'}
                  </p>
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Footer */}
        {step === 'config' && (
          <div className="border-t border-border px-5 py-4 flex gap-2 justify-end">
            <button onClick={onClose} className="rounded-lg border border-border px-4 py-2 text-sm hover:bg-muted transition-colors">
              Cancel
            </button>
            <button
              onClick={handleSubmit}
              disabled={create.isPending || (!goalId && !goalTemplate.trim() && !agentId)}
              className="rounded-lg bg-primary text-primary-foreground px-4 py-2 text-sm font-medium hover:bg-primary/90 transition-colors disabled:opacity-50"
            >
              {create.isPending ? 'Creating…' : 'Create Trigger'}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
