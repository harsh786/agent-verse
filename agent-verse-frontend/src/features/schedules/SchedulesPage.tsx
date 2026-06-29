import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Pause, Play, Trash2, Send } from 'lucide-react';
import { useAuthStore } from '@/stores/auth';

const API_BASE = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

// ── Types ─────────────────────────────────────────────────────────────────────

interface Schedule {
  schedule_id: string;
  agent_id?: string;
  goal_id?: string;
  goal_template?: string;
  trigger_type?: 'cron' | 'interval' | 'webhook' | string;
  cron_expr?: string;
  cron_expression?: string;
  interval_seconds?: number;
  status?: 'active' | 'paused' | string;
  paused?: boolean;
  created_at?: string;
  next_run_at?: string;
  last_fired_at?: string;
  fire_at_iso?: string;
  spec?: {
    trigger_type?: string;
    cron_expression?: string;
    interval_seconds?: number;
  };
}

interface Agent {
  agent_id: string;
  name: string;
  autonomy_mode?: string;
}

interface CreateScheduleForm {
  agent_id: string;
  goal_template: string;
  trigger_type: string;
  cron_expr: string;
  interval_seconds: string;
}

interface NLScheduleResult {
  schedule_id?: string;
  trigger_type?: string;
  cron_expr?: string;
  spec?: {
    trigger_type?: string;
    cron_expression?: string;
  };
  message?: string;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

const hdrs = (apiKey: string) => ({
  'X-API-Key': apiKey,
  'Content-Type': 'application/json',
});

async function apiFetch<T>(apiKey: string, path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { headers: hdrs(apiKey), ...init });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

const TRIGGER_TYPES = ['cron', 'interval', 'webhook'];
const INITIAL_FORM: CreateScheduleForm = {
  agent_id: '',
  goal_template: '',
  trigger_type: 'cron',
  cron_expr: '0 * * * *',
  interval_seconds: '3600',
};

function agentLabel(agent: Agent): string {
  return agent.autonomy_mode ? `${agent.name} (${agent.autonomy_mode})` : agent.name;
}

function agentDisplay(agentId: string | undefined, agents: Agent[]): string {
  if (!agentId) return '';
  const agent = agents.find((a) => a.agent_id === agentId);
  return agent ? agent.name : agentId;
}

function scheduleGoal(schedule: Schedule): string {
  return schedule.goal_template || schedule.goal_id || 'Untitled schedule';
}

function scheduleStatus(schedule: Schedule): string {
  return schedule.status || (schedule.paused ? 'paused' : 'active');
}

function scheduleTriggerType(schedule: Schedule): string {
  return schedule.trigger_type || schedule.spec?.trigger_type || 'unknown';
}

function scheduleCronExpression(schedule: Schedule): string {
  return schedule.cron_expr || schedule.spec?.cron_expression || '';
}

function scheduleIntervalSeconds(schedule: Schedule): number | undefined {
  return schedule.interval_seconds ?? schedule.spec?.interval_seconds;
}

function computeNextRun(schedule: Schedule): string {
  try {
    if (schedule.next_run_at) {
      return new Date(schedule.next_run_at).toLocaleString();
    }
    const triggerType = scheduleTriggerType(schedule);
    if (triggerType === 'cron') {
      const expr = scheduleCronExpression(schedule);
      return expr ? `Cron: ${expr}` : '—';
    }
    if (triggerType === 'interval') {
      const intervalSecs = scheduleIntervalSeconds(schedule);
      if (intervalSecs) {
        const lastFired = schedule.last_fired_at ? new Date(schedule.last_fired_at) : new Date();
        const nextRun = new Date(lastFired.getTime() + intervalSecs * 1000);
        return nextRun.toLocaleString();
      }
    }
    if (triggerType === 'once' && schedule.fire_at_iso) {
      return new Date(schedule.fire_at_iso).toLocaleString();
    }
    return '—';
  } catch {
    return '—';
  }
}

function AgentSelect({
  id,
  value,
  agents,
  onChange,
}: {
  id: string;
  value: string;
  agents: Agent[];
  onChange: (agentId: string) => void;
}) {
  return (
    <select
      id={id}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="w-full border border-input rounded-lg px-3 py-2 text-sm bg-background outline-none focus:ring-2 focus:ring-primary"
    >
      <option value="">No agent selected</option>
      {agents.map((agent) => (
        <option key={agent.agent_id} value={agent.agent_id}>
          {agentLabel(agent)}
        </option>
      ))}
    </select>
  );
}

// ── Schedules tab ─────────────────────────────────────────────────────────────

function SchedulesListTab({ apiKey, agents }: { apiKey: string; agents: Agent[] }) {
  const qc = useQueryClient();
  const [showCreate, setShowCreate] = useState(false);
  const [form, setForm] = useState<CreateScheduleForm>(INITIAL_FORM);
  const [webhookSecret, setWebhookSecret] = useState('');

  const { data: schedules = [], isLoading, error } = useQuery({
    queryKey: ['schedules'],
    queryFn: () => apiFetch<Schedule[]>(apiKey, '/schedules'),
    enabled: !!apiKey,
    refetchInterval: 15_000,
  });

  const createMutation = useMutation({
    mutationFn: () => {
      const body: Record<string, unknown> = {
        goal_template: form.goal_template,
        trigger_type: form.trigger_type,
      };
      if (form.agent_id.trim()) body.agent_id = form.agent_id.trim();
      if (form.trigger_type === 'cron') body.cron_expr = form.cron_expr;
      if (form.trigger_type === 'interval')
        body.interval_seconds = parseInt(form.interval_seconds, 10) || 3600;
      if (form.trigger_type === 'webhook' && webhookSecret.trim())
        body.source_config = { webhook_secret: webhookSecret.trim() };
      return apiFetch<Schedule>(apiKey, '/schedules', {
        method: 'POST',
        body: JSON.stringify(body),
      });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['schedules'] });
      setShowCreate(false);
      setForm(INITIAL_FORM);
      setWebhookSecret('');
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) =>
      apiFetch<void>(apiKey, `/schedules/${id}`, { method: 'DELETE' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['schedules'] }),
  });

  const pauseMutation = useMutation({
    mutationFn: (id: string) =>
      apiFetch<Schedule>(apiKey, `/schedules/${id}/pause`, { method: 'POST' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['schedules'] }),
  });

  const resumeMutation = useMutation({
    mutationFn: (id: string) =>
      apiFetch<Schedule>(apiKey, `/schedules/${id}/resume`, { method: 'POST' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['schedules'] }),
  });

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <button
          onClick={() => setShowCreate((v) => !v)}
          className="bg-primary text-primary-foreground px-4 py-2 rounded-lg text-sm hover:opacity-90"
        >
          {showCreate ? 'Cancel' : '+ New Schedule'}
        </button>
      </div>

      {showCreate && (
        <div className="bg-card border border-border rounded-xl p-5 space-y-4">
          <h3 className="font-medium text-sm">New Schedule</h3>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label htmlFor="schedule-goal-template" className="block text-xs font-medium mb-1">Goal Template</label>
              <input
                id="schedule-goal-template"
                value={form.goal_template}
                onChange={(e) => setForm((f) => ({ ...f, goal_template: e.target.value }))}
                placeholder="Check all open PRs and post a summary"
                className="w-full border border-input rounded-lg px-3 py-2 text-sm bg-background outline-none focus:ring-2 focus:ring-primary"
              />
            </div>
            <div>
              <label htmlFor="schedule-agent" className="block text-xs font-medium mb-1">Agent (optional)</label>
              <AgentSelect
                id="schedule-agent"
                value={form.agent_id}
                agents={agents}
                onChange={(agentId) => setForm((f) => ({ ...f, agent_id: agentId }))}
              />
            </div>
            <div>
              <label htmlFor="schedule-trigger-type" className="block text-xs font-medium mb-1">Trigger Type</label>
              <select
                id="schedule-trigger-type"
                value={form.trigger_type}
                onChange={(e) =>
                  setForm((f) => ({ ...f, trigger_type: e.target.value }))
                }
                className="w-full border border-input rounded-lg px-3 py-2 text-sm bg-background outline-none focus:ring-2 focus:ring-primary"
              >
                {TRIGGER_TYPES.map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </select>
            </div>
            {form.trigger_type === 'cron' && (
              <div>
                <label htmlFor="schedule-cron-expression" className="block text-xs font-medium mb-1">Cron Expression</label>
                <input
                  id="schedule-cron-expression"
                  value={form.cron_expr}
                  onChange={(e) => setForm((f) => ({ ...f, cron_expr: e.target.value }))}
                  placeholder="0 * * * *"
                  className="w-full border border-input rounded-lg px-3 py-2 text-sm font-mono bg-background outline-none focus:ring-2 focus:ring-primary"
                />
              </div>
            )}
            {form.trigger_type === 'interval' && (
              <div>
                <label htmlFor="schedule-interval-seconds" className="block text-xs font-medium mb-1">Interval (seconds)</label>
                <input
                  id="schedule-interval-seconds"
                  type="number"
                  min="60"
                  value={form.interval_seconds}
                  onChange={(e) =>
                    setForm((f) => ({ ...f, interval_seconds: e.target.value }))
                  }
                  className="w-full border border-input rounded-lg px-3 py-2 text-sm bg-background outline-none focus:ring-2 focus:ring-primary"
                />
              </div>
            )}
            {form.trigger_type === 'webhook' && (
              <div className="space-y-3 p-3 rounded-lg bg-muted/50 border">
                <div>
                  <p className="text-xs font-medium text-muted-foreground mb-1">Webhook endpoint URL</p>
                  <div className="flex items-center gap-2">
                    <code className="flex-1 text-xs bg-card border rounded px-2 py-1.5 text-foreground break-all">
                      {`${API_BASE}/webhooks/trigger`}
                    </code>
                    <button
                      type="button"
                      onClick={() => navigator.clipboard.writeText(`${API_BASE}/webhooks/trigger`)}
                      aria-label="Copy webhook URL"
                      className="px-2 py-1.5 text-xs border rounded hover:bg-muted"
                    >
                      Copy
                    </button>
                  </div>
                  <p className="mt-1 text-xs text-muted-foreground">
                    POST to this URL to fire the schedule. Pass <code>X-Webhook-Secret</code> header.
                  </p>
                </div>
                <label className="block text-sm font-medium">
                  Webhook secret (optional)
                  <input
                    aria-label="Webhook secret"
                    type="password"
                    className="mt-1 block w-full rounded border px-3 py-2 text-sm bg-background"
                    placeholder="Leave blank to auto-generate"
                    value={webhookSecret}
                    onChange={(e) => setWebhookSecret(e.target.value)}
                  />
                </label>
              </div>
            )}
          </div>
          {createMutation.isError && (
            <p className="text-xs text-red-600">{String(createMutation.error)}</p>
          )}
          <div className="flex justify-end">
            <button
              onClick={() => createMutation.mutate()}
              disabled={!form.goal_template.trim() || createMutation.isPending}
              className="bg-primary text-primary-foreground px-5 py-2 rounded-lg text-sm disabled:opacity-50"
            >
              {createMutation.isPending ? 'Creating…' : 'Create Schedule'}
            </button>
          </div>
        </div>
      )}

      <div className="bg-card border border-border rounded-xl overflow-hidden">
        {isLoading ? (
          <div className="py-10 text-center text-sm text-muted-foreground">Loading…</div>
        ) : error ? (
          <div className="py-10 text-center text-sm text-red-500">
            Failed to load schedules.
          </div>
        ) : schedules.length === 0 ? (
          <div className="py-10 text-center text-sm text-muted-foreground">
            No schedules yet. Create one to automate your agents.
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-muted/40">
                {['Goal', 'Trigger', 'Status', 'Next Run', 'Actions'].map((h) => (
                  <th
                    key={h}
                    className="text-left px-4 py-3 font-medium text-muted-foreground"
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {schedules.map((s) => (
                <tr key={s.schedule_id} className="hover:bg-accent/50 transition-colors">
                  <td className="px-4 py-3">
                    <p className="font-medium truncate max-w-xs">{scheduleGoal(s)}</p>
                    <p className="text-xs text-muted-foreground font-mono mt-0.5">
                      {s.schedule_id}
                    </p>
                    {s.agent_id && (
                      <p className="text-xs text-muted-foreground mt-0.5">
                        Agent: {agentDisplay(s.agent_id, agents)}
                      </p>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <span className="bg-muted text-muted-foreground px-2 py-0.5 rounded-full text-xs">
                      {scheduleTriggerType(s)}
                    </span>
                    {scheduleCronExpression(s) && (
                      <span className="text-xs text-muted-foreground font-mono ml-2">
                        {scheduleCronExpression(s)}
                      </span>
                    )}
                    {scheduleIntervalSeconds(s) != null && (
                      <span className="text-xs text-muted-foreground ml-2">
                        every {scheduleIntervalSeconds(s)}s
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <span
                      className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                        scheduleStatus(s) === 'active'
                          ? 'bg-green-100 text-green-800'
                          : 'bg-yellow-100 text-yellow-800'
                      }`}
                    >
                      {scheduleStatus(s)}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-xs text-muted-foreground">
                    {computeNextRun(s)}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      {scheduleStatus(s) === 'active' ? (
                        <button
                          onClick={() => pauseMutation.mutate(s.schedule_id)}
                          title="Pause"
                          className="p-1 hover:text-yellow-600 transition-colors"
                        >
                          <Pause className="h-4 w-4" />
                        </button>
                      ) : (
                        <button
                          onClick={() => resumeMutation.mutate(s.schedule_id)}
                          title="Resume"
                          className="p-1 hover:text-green-600 transition-colors"
                        >
                          <Play className="h-4 w-4" />
                        </button>
                      )}
                      <button
                        onClick={() => deleteMutation.mutate(s.schedule_id)}
                        disabled={deleteMutation.isPending}
                        title="Delete"
                        className="p-1 hover:text-destructive transition-colors disabled:opacity-40"
                      >
                        <Trash2 className="h-4 w-4" />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

// ── NL Scheduler tab ──────────────────────────────────────────────────────────

interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
}

function NLSchedulerTab({ apiKey, agents }: { apiKey: string; agents: Agent[] }) {
  const [command, setCommand] = useState('');
  const [agentId, setAgentId] = useState('');
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sending, setSending] = useState(false);

  const handleSubmit = async () => {
    if (!command.trim()) return;
    const userMsg = command.trim();
    setMessages((m) => [...m, { role: 'user', content: userMsg }]);
    setCommand('');
    setSending(true);
    try {
      const body: Record<string, string> = { command: userMsg };
      if (agentId.trim()) body.agent_id = agentId.trim();
      const data = await apiFetch<NLScheduleResult[] | NLScheduleResult>(apiKey, '/nl/schedule', {
        method: 'POST',
        body: JSON.stringify(body),
      });
      const records = Array.isArray(data) ? data : [data];
      const first = records[0];
      const triggerType = first?.trigger_type ?? first?.spec?.trigger_type ?? '';
      const cronExpr = first?.cron_expr ?? first?.spec?.cron_expression ?? '';
      const reply = first?.message
        ? first.message
        : `Created ${records.length} schedule${records.length === 1 ? '' : 's'} ${first?.schedule_id ?? ''} (${triggerType} ${cronExpr})`.trim();
      setMessages((m) => [...m, { role: 'assistant', content: reply }]);
    } catch (e) {
      setMessages((m) => [
        ...m,
        { role: 'assistant', content: `Error: ${String(e)}` },
      ]);
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="flex flex-col gap-4 max-w-2xl">
      {/* Optional agent ID */}
      <div className="bg-card border border-border rounded-xl p-4">
        <label htmlFor="nl-schedule-agent" className="block text-xs font-medium mb-1">Agent (optional)</label>
        <AgentSelect
          id="nl-schedule-agent"
          value={agentId}
          agents={agents}
          onChange={setAgentId}
        />
      </div>

      {/* Chat window */}
      <div className="bg-card border border-border rounded-xl overflow-hidden">
        <div className="px-4 py-3 border-b border-border">
          <h3 className="text-sm font-medium">NL Scheduler Chat</h3>
        </div>
        <div className="p-4 min-h-48 space-y-3">
          {messages.length === 0 && (
            <p className="text-sm text-muted-foreground text-center py-6">
              Describe a schedule in plain English, e.g. "Run a PR review every weekday at 9am"
            </p>
          )}
          {messages.map((msg, i) => (
            <div
              key={i}
              className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
            >
              <div
                className={`max-w-sm rounded-xl px-3 py-2 text-sm ${
                  msg.role === 'user'
                    ? 'bg-primary text-primary-foreground'
                    : 'bg-muted text-foreground'
                }`}
              >
                {msg.content}
              </div>
            </div>
          ))}
          {sending && (
            <div className="flex justify-start">
              <div className="bg-muted rounded-xl px-3 py-2 text-sm text-muted-foreground">
                Thinking…
              </div>
            </div>
          )}
        </div>
        <div className="border-t border-border p-3 flex gap-2">
          <input
            value={command}
            onChange={(e) => setCommand(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && !e.shiftKey && handleSubmit()}
            placeholder="Describe your schedule…"
            disabled={sending}
            className="flex-1 border border-input rounded-lg px-3 py-2 text-sm bg-background outline-none focus:ring-2 focus:ring-primary disabled:opacity-50"
          />
          <button
            onClick={handleSubmit}
            disabled={!command.trim() || sending}
            className="bg-primary text-primary-foreground p-2 rounded-lg hover:opacity-90 disabled:opacity-50"
          >
            <Send className="h-4 w-4" />
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

type Tab = 'schedules' | 'nl-scheduler';

export function SchedulesPage() {
  const apiKey = useAuthStore((s) => s.apiKey);
  const [tab, setTab] = useState<Tab>('schedules');

  const { data: agents = [] } = useQuery({
    queryKey: ['agents'],
    queryFn: () => apiFetch<Agent[]>(apiKey, '/agents'),
    enabled: !!apiKey,
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Schedules</h1>
        <p className="text-muted-foreground text-sm mt-1">
          Automate your agents with time-based and event-based triggers
        </p>
      </div>

      <div className="flex gap-4 border-b border-border">
        {(['schedules', 'nl-scheduler'] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`pb-2 px-1 font-medium text-sm transition-colors ${
              tab === t
                ? 'border-b-2 border-primary text-primary'
                : 'text-muted-foreground hover:text-foreground'
            }`}
          >
            {t === 'nl-scheduler' ? 'NL Scheduler' : 'Schedules'}
          </button>
        ))}
      </div>

      {tab === 'schedules' && <SchedulesListTab apiKey={apiKey} agents={agents} />}
      {tab === 'nl-scheduler' && <NLSchedulerTab apiKey={apiKey} agents={agents} />}
    </div>
  );
}
