/**
 * SchedulesPage — World-Class AI Schedule Management
 *
 * 4 tabs:
 *   Schedules  — enhanced CRUD table with bulk ops, run-now, templates
 *   Analytics  — 7-day firing chart, trigger distribution, summary stats
 *   AI Advisor — LLM-powered schedule suggestions from goal description
 *   NL Scheduler — natural language chat schedule creation
 */
import { useCallback, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  BarChart2, Bell, Bot, Calendar, CheckCircle, ChevronRight,
  Clock, Copy, Loader2, Pause, Play, Plus, RefreshCw,
  Sparkles, Trash2, Zap, XCircle, Activity,
} from 'lucide-react';
import { useAuthStore } from '@/stores/auth';
import { toast } from '@/stores/toast';

// ── Types ──────────────────────────────────────────────────────────────────────

interface Schedule {
  schedule_id: string;
  goal_template?: string;
  trigger_type?: string;
  cron_expr?: string;
  cron_expression?: string;
  interval_seconds?: number;
  paused?: boolean;
  status?: string;
  next_run_at?: string;
  last_fired_at?: string;
  agent_id?: string;
  spec?: { trigger_type?: string; cron_expression?: string; interval_seconds?: number };
}
interface Agent { agent_id: string; name: string; }
interface ScheduleAnalytics {
  total: number;
  active: number;
  paused: number;
  by_trigger_type: Record<string, number>;
  fired_last_7_days: Record<string, number>;
  schedules_summary: Schedule[];
}
interface AISuggestion {
  rank: number;
  title: string;
  trigger_type: string;
  cron_expr: string | null;
  interval_seconds: number | null;
  rationale: string;
  use_case: string;
}
type Tab = 'schedules' | 'analytics' | 'advisor' | 'nl';

// ── Helpers ───────────────────────────────────────────────────────────────────

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000';

function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const apiKey = useAuthStore.getState().apiKey;
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (apiKey) headers['X-API-Key'] = apiKey;
  return fetch(`${API_BASE}${path}`, { ...init, headers: { ...headers, ...(init?.headers ?? {}) } })
    .then((r) => { if (!r.ok) throw new Error(r.statusText); return r.json() as Promise<T>; });
}

function humanCron(cron: string | undefined): string {
  if (!cron) return '';
  const parts = cron.trim().split(/\s+/);
  if (parts.length < 5) return cron;
  const [min, hour, dom, , dow] = parts;
  if (min === '0' && hour !== '*' && dom === '*' && dow === '*')
    return `Daily at ${hour.padStart(2, '0')}:00`;
  if (min === '0' && hour === '*') return 'Every hour';
  if (min !== '*' && hour === '*') return `Every hour at :${min.padStart(2, '0')}`;
  if (min === '0' && hour !== '*' && dom === '*' && dow === '1')
    return `Mondays at ${hour.padStart(2, '0')}:00`;
  if (min === '0' && hour !== '*' && dom === '1' && dow === '*')
    return `1st of month at ${hour.padStart(2, '0')}:00`;
  return cron;
}

function scheduleGoal(s: Schedule): string {
  return s.goal_template || 'Untitled';
}
function scheduleTrigger(s: Schedule): string {
  return s.trigger_type || s.spec?.trigger_type || 'unknown';
}
function scheduleCron(s: Schedule): string {
  return s.cron_expr || s.cron_expression || s.spec?.cron_expression || '';
}
function scheduleInterval(s: Schedule): number {
  return s.interval_seconds ?? s.spec?.interval_seconds ?? 0;
}
function scheduleStatus(s: Schedule): 'active' | 'paused' {
  return s.paused || s.status === 'paused' ? 'paused' : 'active';
}
function fmtInterval(sec: number): string {
  if (sec < 60) return `${sec}s`;
  if (sec < 3600) return `${Math.round(sec / 60)}m`;
  if (sec < 86400) return `${Math.round(sec / 3600)}h`;
  return `${Math.round(sec / 86400)}d`;
}

const TEMPLATES = [
  { label: 'Daily Report', cron_expr: '0 9 * * *', trigger_type: 'cron', goal_template: 'Generate and send daily report' },
  { label: 'Hourly Check', cron_expr: '0 * * * *', trigger_type: 'cron', goal_template: 'Check system health and alert if needed' },
  { label: 'Weekly Summary', cron_expr: '0 8 * * 1', trigger_type: 'cron', goal_template: 'Create weekly team summary' },
  { label: 'On Webhook', cron_expr: '', trigger_type: 'webhook', goal_template: 'Process incoming event' },
];

// ── Schedules Tab ─────────────────────────────────────────────────────────────

function TriggerBadge({ type }: { type: string }) {
  const colors: Record<string, string> = {
    cron: 'bg-violet-100 text-violet-700',
    interval: 'bg-blue-100 text-blue-700',
    webhook: 'bg-orange-100 text-orange-700',
    once: 'bg-gray-100 text-gray-600',
    rest: 'bg-green-100 text-green-700',
  };
  return (
    <span className={`px-2 py-0.5 rounded text-xs font-medium ${colors[type] ?? 'bg-gray-100 text-gray-600'}`}>
      {type}
    </span>
  );
}

function StatusDot({ status }: { status: 'active' | 'paused' }) {
  return (
    <span className="flex items-center gap-1.5 text-xs">
      <span className={`w-2 h-2 rounded-full ${status === 'active' ? 'bg-green-500 animate-pulse' : 'bg-amber-400'}`} />
      {status}
    </span>
  );
}

function SchedulesTab() {
  const qc = useQueryClient();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [showCreate, setShowCreate] = useState(false);
  const [prefill, setPrefill] = useState<Partial<typeof TEMPLATES[0]>>({});
  const [form, setForm] = useState({ goal_template: '', trigger_type: 'cron', cron_expr: '0 * * * *', interval_seconds: '3600', agent_id: '' });

  const { data: schedules = [], isLoading } = useQuery<Schedule[]>({
    queryKey: ['schedules'],
    queryFn: () => apiFetch('/schedules'),
    refetchInterval: 15_000,
  });
  const { data: agents = [] } = useQuery<Agent[]>({
    queryKey: ['agents'],
    queryFn: () => apiFetch('/agents'),
  });

  const createMutation = useMutation({
    mutationFn: (f: typeof form) => apiFetch('/schedules', {
      method: 'POST',
      body: JSON.stringify({
        goal_template: f.goal_template,
        trigger_type: f.trigger_type,
        cron_expr: f.trigger_type === 'cron' ? f.cron_expr : '',
        interval_seconds: f.trigger_type === 'interval' ? Number(f.interval_seconds) : 0,
        agent_id: f.agent_id || '',
      }),
    }),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ['schedules'] }); setShowCreate(false); toast({ kind: 'success', message: 'Schedule created.' }); },
    onError: (e) => toast({ kind: 'error', message: String(e) }),
  });
  const deleteMutation = useMutation({
    mutationFn: (id: string) => apiFetch(`/schedules/${id}`, { method: 'DELETE' }),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ['schedules'] }); toast({ kind: 'success', message: 'Deleted.' }); },
  });
  const pauseMutation = useMutation({
    mutationFn: (id: string) => apiFetch(`/schedules/${id}/pause`, { method: 'POST' }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['schedules'] }),
  });
  const resumeMutation = useMutation({
    mutationFn: (id: string) => apiFetch(`/schedules/${id}/resume`, { method: 'POST' }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['schedules'] }),
  });
  const fireMutation = useMutation({
    mutationFn: (id: string) => apiFetch(`/schedules/${id}/fire`, { method: 'POST' }),
    onSuccess: () => toast({ kind: 'success', message: 'Fired!' }),
    onError: () => toast({ kind: 'error', message: 'Cannot fire this schedule type manually.' }),
  });

  const bulkPauseMutation = useMutation({
    mutationFn: async () => { for (const id of selected) await apiFetch(`/schedules/${id}/pause`, { method: 'POST' }); },
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ['schedules'] }); setSelected(new Set()); },
  });
  const bulkResumeMutation = useMutation({
    mutationFn: async () => { for (const id of selected) await apiFetch(`/schedules/${id}/resume`, { method: 'POST' }); },
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ['schedules'] }); setSelected(new Set()); },
  });
  const bulkDeleteMutation = useMutation({
    mutationFn: async () => { for (const id of selected) await apiFetch(`/schedules/${id}`, { method: 'DELETE' }); },
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ['schedules'] }); setSelected(new Set()); },
  });

  const openCreate = useCallback((template?: typeof TEMPLATES[0]) => {
    if (template) {
      setForm((f) => ({ ...f, goal_template: template.goal_template, trigger_type: template.trigger_type, cron_expr: template.cron_expr || '0 * * * *' }));
      setPrefill(template);
    }
    setShowCreate(true);
  }, []);

  return (
    <div className="space-y-4">
      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-3">
        {selected.size > 0 && (
          <div className="flex items-center gap-2 bg-primary/5 border border-primary/20 rounded-lg px-3 py-1.5">
            <span className="text-xs font-medium text-primary">{selected.size} selected</span>
            <button onClick={() => bulkPauseMutation.mutate()} className="text-xs px-2 py-1 bg-amber-600 text-white rounded">Pause</button>
            <button onClick={() => bulkResumeMutation.mutate()} className="text-xs px-2 py-1 bg-green-600 text-white rounded">Resume</button>
            <button onClick={() => bulkDeleteMutation.mutate()} className="text-xs px-2 py-1 bg-red-600 text-white rounded">Delete</button>
            <button onClick={() => setSelected(new Set())} className="text-xs text-muted-foreground">Clear</button>
          </div>
        )}
        <div className="ml-auto flex gap-2">
          <button onClick={() => openCreate()} data-testid="create-schedule-btn"
            className="flex items-center gap-1.5 px-3 py-2 bg-primary text-primary-foreground rounded-md text-sm">
            <Plus className="h-4 w-4" /> New Schedule
          </button>
        </div>
      </div>

      {/* Templates row */}
      <div className="flex flex-wrap gap-2">
        <span className="text-xs text-muted-foreground self-center">Quick start:</span>
        {TEMPLATES.map((t) => (
          <button key={t.label} onClick={() => openCreate(t)}
            className="flex items-center gap-1.5 px-2.5 py-1 border border-border rounded text-xs hover:bg-muted">
            <Zap className="h-3 w-3 text-amber-500" /> {t.label}
          </button>
        ))}
      </div>

      {/* Create form */}
      {showCreate && (
        <div data-testid="create-schedule-form" className="bg-card border border-border rounded-xl p-4 space-y-4">
          <h3 className="font-medium text-sm">New Schedule</h3>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-muted-foreground mb-1">Goal template *</label>
              <input value={form.goal_template} onChange={(e) => setForm((f) => ({ ...f, goal_template: e.target.value }))}
                placeholder="Describe what to run…" className="w-full px-3 py-2 border border-border rounded-md text-sm bg-background" />
            </div>
            <div>
              <label className="block text-xs text-muted-foreground mb-1">Agent (optional)</label>
              <select value={form.agent_id} onChange={(e) => setForm((f) => ({ ...f, agent_id: e.target.value }))}
                className="w-full px-3 py-2 border border-border rounded-md text-sm bg-background">
                <option value="">Any agent</option>
                {agents.map((a) => <option key={a.agent_id} value={a.agent_id}>{a.name}</option>)}
              </select>
            </div>
          </div>
          <div className="flex gap-2">
            {['cron', 'interval', 'webhook'].map((t) => (
              <button key={t} onClick={() => setForm((f) => ({ ...f, trigger_type: t }))}
                className={`px-3 py-1.5 rounded border text-xs capitalize ${form.trigger_type === t ? 'border-primary bg-primary/10 text-primary' : 'border-border hover:bg-muted'}`}>
                {t}
              </button>
            ))}
          </div>
          {form.trigger_type === 'cron' && (
            <div>
              <label className="block text-xs text-muted-foreground mb-1">Cron expression</label>
              <input value={form.cron_expr} onChange={(e) => setForm((f) => ({ ...f, cron_expr: e.target.value }))}
                placeholder="0 9 * * *" className="w-full px-3 py-2 border border-border rounded-md text-sm bg-background font-mono" />
              {form.cron_expr && <p className="text-xs text-muted-foreground mt-1">{humanCron(form.cron_expr)}</p>}
            </div>
          )}
          {form.trigger_type === 'interval' && (
            <div>
              <label className="block text-xs text-muted-foreground mb-1">Interval (seconds)</label>
              <input type="number" min="60" value={form.interval_seconds}
                onChange={(e) => setForm((f) => ({ ...f, interval_seconds: e.target.value }))}
                className="w-full px-3 py-2 border border-border rounded-md text-sm bg-background" />
              <p className="text-xs text-muted-foreground mt-1">= {fmtInterval(Number(form.interval_seconds))}</p>
            </div>
          )}
          {form.trigger_type === 'webhook' && (
            <div className="text-xs text-muted-foreground bg-muted/50 p-2 rounded">
              A webhook URL will be generated after creation. POST to it to trigger this schedule.
            </div>
          )}
          <div className="flex gap-2">
            <button onClick={() => createMutation.mutate(form)} disabled={!form.goal_template || createMutation.isPending}
              className="px-4 py-2 bg-primary text-primary-foreground rounded-md text-sm disabled:opacity-50">
              {createMutation.isPending ? 'Creating…' : 'Create Schedule'}
            </button>
            <button onClick={() => setShowCreate(false)} className="px-4 py-2 border border-border rounded-md text-sm">Cancel</button>
          </div>
        </div>
      )}

      {/* Schedules table */}
      {isLoading ? (
        <div className="flex justify-center py-12"><Loader2 className="h-6 w-6 animate-spin text-muted-foreground" /></div>
      ) : schedules.length === 0 ? (
        <div className="flex flex-col items-center py-14 text-muted-foreground gap-2">
          <Calendar className="h-10 w-10 opacity-30" />
          <p className="text-sm">No schedules yet — create one above or use a quick-start template.</p>
        </div>
      ) : (
        <div data-testid="schedules-table" className="border border-border rounded-xl overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-muted/40 border-b border-border">
              <tr>
                <th className="px-3 py-2.5 w-8"><input type="checkbox" onChange={(e) => setSelected(e.target.checked ? new Set(schedules.map((s) => s.schedule_id)) : new Set())} className="rounded" /></th>
                <th className="px-3 py-2.5 text-left text-xs font-medium text-muted-foreground">Goal</th>
                <th className="px-3 py-2.5 text-left text-xs font-medium text-muted-foreground">Trigger</th>
                <th className="px-3 py-2.5 text-left text-xs font-medium text-muted-foreground">Status</th>
                <th className="px-3 py-2.5 text-left text-xs font-medium text-muted-foreground">Schedule</th>
                <th className="px-3 py-2.5 text-right text-xs font-medium text-muted-foreground">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {schedules.map((s) => {
                const st = scheduleStatus(s);
                const ttype = scheduleTrigger(s);
                const cron = scheduleCron(s);
                const interval = scheduleInterval(s);
                return (
                  <tr key={s.schedule_id} data-testid={`schedule-row-${s.schedule_id}`} className="hover:bg-muted/20">
                    <td className="px-3 py-2.5"><input type="checkbox" checked={selected.has(s.schedule_id)} onChange={() => setSelected((prev) => { const n = new Set(prev); n.has(s.schedule_id) ? n.delete(s.schedule_id) : n.add(s.schedule_id); return n; })} className="rounded" /></td>
                    <td className="px-3 py-2.5">
                      <p className="font-medium truncate max-w-[200px]">{scheduleGoal(s)}</p>
                      {s.agent_id && <p className="text-xs text-muted-foreground">Agent: {s.agent_id.slice(0, 12)}…</p>}
                    </td>
                    <td className="px-3 py-2.5"><TriggerBadge type={ttype} /></td>
                    <td className="px-3 py-2.5"><StatusDot status={st} /></td>
                    <td className="px-3 py-2.5 text-xs">
                      {ttype === 'cron' && cron && <span className="font-mono">{humanCron(cron) || cron}</span>}
                      {ttype === 'interval' && interval > 0 && <span>Every {fmtInterval(interval)}</span>}
                      {ttype === 'webhook' && <span className="text-muted-foreground">On webhook call</span>}
                      {s.next_run_at && <p className="text-muted-foreground mt-0.5">Next: {new Date(s.next_run_at).toLocaleString()}</p>}
                    </td>
                    <td className="px-3 py-2.5">
                      <div className="flex items-center justify-end gap-1">
                        {(ttype === 'rest' || ttype === 'webhook') && (
                          <button data-testid={`run-now-btn-${s.schedule_id}`} onClick={() => fireMutation.mutate(s.schedule_id)}
                            className="p-1.5 text-muted-foreground hover:text-green-600 rounded" title="Fire now">
                            <Play className="h-3.5 w-3.5" />
                          </button>
                        )}
                        {st === 'active' ? (
                          <button data-testid={`pause-btn-${s.schedule_id}`} onClick={() => pauseMutation.mutate(s.schedule_id)}
                            className="p-1.5 text-muted-foreground hover:text-amber-500 rounded" title="Pause">
                            <Pause className="h-3.5 w-3.5" />
                          </button>
                        ) : (
                          <button data-testid={`resume-btn-${s.schedule_id}`} onClick={() => resumeMutation.mutate(s.schedule_id)}
                            className="p-1.5 text-muted-foreground hover:text-green-500 rounded" title="Resume">
                            <Play className="h-3.5 w-3.5" />
                          </button>
                        )}
                        <button data-testid={`delete-btn-${s.schedule_id}`} onClick={() => deleteMutation.mutate(s.schedule_id)}
                          className="p-1.5 text-muted-foreground hover:text-red-500 rounded" title="Delete">
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ── Analytics Tab ─────────────────────────────────────────────────────────────

function AnalyticsTab() {
  const { data: analytics, isLoading } = useQuery<ScheduleAnalytics>({
    queryKey: ['schedules-analytics'],
    queryFn: () => apiFetch('/schedules/analytics'),
    staleTime: 30_000,
  });

  if (isLoading) return <div className="flex justify-center py-12"><Loader2 className="h-6 w-6 animate-spin text-muted-foreground" /></div>;
  if (!analytics) return <div className="text-muted-foreground text-center py-8">No analytics data.</div>;

  const dayEntries = Object.entries(analytics.fired_last_7_days).sort(([a], [b]) => a.localeCompare(b));
  const maxFires = Math.max(...dayEntries.map(([, v]) => v), 1);
  const typeEntries = Object.entries(analytics.by_trigger_type);
  const totalTypes = typeEntries.reduce((a, [, v]) => a + v, 0);
  const typeColors: Record<string, string> = { cron: '#8b5cf6', interval: '#3b82f6', webhook: '#f59e0b', once: '#6b7280' };

  return (
    <div className="space-y-5">
      {/* KPI row */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {[
          { label: 'Total', value: analytics.total, icon: <Calendar className="h-4 w-4 text-violet-500" /> },
          { label: 'Active', value: analytics.active, icon: <Activity className="h-4 w-4 text-green-500" /> },
          { label: 'Paused', value: analytics.paused, icon: <Pause className="h-4 w-4 text-amber-500" /> },
          { label: 'Fired Today', value: dayEntries.at(-1)?.[1] ?? 0, icon: <Zap className="h-4 w-4 text-orange-500" /> },
        ].map((s) => (
          <div key={s.label} className="bg-card border border-border rounded-xl p-3">
            <div className="flex items-center gap-1.5 text-xs text-muted-foreground mb-1">{s.icon} {s.label}</div>
            <p className="text-2xl font-bold">{s.value}</p>
          </div>
        ))}
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
        {/* 7-day bar chart */}
        <div className="bg-card border border-border rounded-xl p-4">
          <h3 className="font-medium text-sm flex items-center gap-2 mb-4"><BarChart2 className="h-4 w-4" /> Firing Activity (7 days)</h3>
          <div className="flex items-end gap-1.5 h-28">
            {dayEntries.map(([day, count]) => (
              <div key={day} className="flex-1 flex flex-col items-center gap-1">
                <div className="w-full bg-violet-500 rounded-t transition-all"
                  style={{ height: `${Math.max(4, (count / maxFires) * 96)}px` }} title={`${count} fires`} />
                <span className="text-[9px] text-muted-foreground rotate-0">{day.slice(5)}</span>
              </div>
            ))}
          </div>
        </div>

        {/* Trigger type distribution */}
        <div className="bg-card border border-border rounded-xl p-4">
          <h3 className="font-medium text-sm flex items-center gap-2 mb-4"><Bell className="h-4 w-4" /> Trigger Types</h3>
          <div className="space-y-2">
            {typeEntries.map(([type, count]) => (
              <div key={type} className="flex items-center gap-2 text-sm">
                <span className="w-16 text-muted-foreground capitalize">{type}</span>
                <div className="flex-1 bg-muted rounded h-4 overflow-hidden">
                  <div className="h-full rounded" style={{ width: `${(count / totalTypes) * 100}%`, backgroundColor: typeColors[type] ?? '#94a3b8' }} />
                </div>
                <span className="w-6 text-right text-xs font-mono">{count}</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Recent schedules */}
      {analytics.schedules_summary.length > 0 && (
        <div className="bg-card border border-border rounded-xl p-4">
          <h3 className="font-medium text-sm mb-3">Schedule Overview</h3>
          <div className="space-y-2">
            {analytics.schedules_summary.slice(0, 8).map((s) => (
              <div key={s.schedule_id} className="flex items-center gap-3 text-sm">
                <StatusDot status={scheduleStatus(s)} />
                <span className="flex-1 truncate">{scheduleGoal(s)}</span>
                <TriggerBadge type={scheduleTrigger(s)} />
                {s.last_fired_at && <span className="text-xs text-muted-foreground">{new Date(s.last_fired_at).toLocaleDateString()}</span>}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ── AI Advisor Tab ────────────────────────────────────────────────────────────

function AIAdvisorTab({ onUseTemplate }: { onUseTemplate: (s: AISuggestion) => void }) {
  const [goalDesc, setGoalDesc] = useState('');
  const [suggestions, setSuggestions] = useState<AISuggestion[]>([]);
  const [llmPowered, setLlmPowered] = useState(false);

  const suggestMutation = useMutation({
    mutationFn: () => apiFetch<{ suggestions: AISuggestion[]; llm_powered: boolean }>('/schedules/suggest', {
      method: 'POST',
      body: JSON.stringify({ goal_description: goalDesc }),
    }),
    onSuccess: (r) => { setSuggestions(r.suggestions); setLlmPowered(r.llm_powered); },
    onError: () => toast({ kind: 'error', message: 'Suggestion failed.' }),
  });

  const examples = [
    'Send daily engineering metrics report to team',
    'Check production database for anomalies every hour',
    'Generate weekly customer success summary on Mondays',
    'Trigger deployment pipeline when code is pushed',
  ];

  return (
    <div className="space-y-5">
      <div className="bg-card border border-border rounded-xl p-4 space-y-3">
        <div className="flex items-center gap-2 mb-1">
          <Sparkles className="h-4 w-4 text-violet-500" />
          <h3 className="font-medium text-sm">AI Schedule Advisor</h3>
          <span className="ml-auto text-xs text-muted-foreground">Describe your goal → get optimal schedule suggestions</span>
        </div>
        <textarea value={goalDesc} onChange={(e) => setGoalDesc(e.target.value)} rows={3}
          placeholder="Describe what your schedule should do and how often…"
          className="w-full px-3 py-2 border border-border rounded-md text-sm bg-background resize-none" />
        <div className="flex flex-wrap gap-1.5">
          <span className="text-xs text-muted-foreground self-center">Examples:</span>
          {examples.map((e) => (
            <button key={e} onClick={() => setGoalDesc(e)}
              className="text-xs px-2 py-1 bg-muted rounded hover:bg-muted/80 truncate max-w-[200px]">{e}</button>
          ))}
        </div>
        <button data-testid="suggest-btn" onClick={() => suggestMutation.mutate()} disabled={!goalDesc.trim() || suggestMutation.isPending}
          className="flex items-center gap-2 px-4 py-2 bg-violet-600 text-white rounded-md text-sm disabled:opacity-50">
          {suggestMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
          {suggestMutation.isPending ? 'Analyzing…' : 'Get AI Suggestions'}
        </button>
      </div>

      {suggestions.length > 0 && (
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="font-medium text-sm">Suggestions for: <span className="text-muted-foreground">{goalDesc}</span></h3>
            <span className={`text-xs px-2 py-0.5 rounded ${llmPowered ? 'bg-violet-100 text-violet-700' : 'bg-gray-100 text-gray-600'}`}>
              {llmPowered ? '✦ AI-powered' : 'Template'}
            </span>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            {suggestions.map((s) => (
              <div key={s.rank} className="bg-card border border-border rounded-xl p-4 space-y-3">
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <p className="font-semibold text-sm">{s.title}</p>
                    <TriggerBadge type={s.trigger_type} />
                  </div>
                  <span className="text-xs bg-primary/10 text-primary px-1.5 py-0.5 rounded-full">#{s.rank}</span>
                </div>
                <div className="text-xs font-mono bg-muted p-2 rounded">
                  {s.cron_expr ? humanCron(s.cron_expr) || s.cron_expr : s.interval_seconds ? fmtInterval(s.interval_seconds) : 'webhook'}
                </div>
                <p className="text-xs text-muted-foreground">{s.rationale}</p>
                <div className="flex items-center gap-1 text-xs text-muted-foreground">
                  <Clock className="h-3 w-3" /> {s.use_case}
                </div>
                <button onClick={() => onUseTemplate(s)}
                  className="w-full flex items-center justify-center gap-1.5 px-3 py-1.5 bg-primary text-primary-foreground rounded-md text-xs">
                  <Plus className="h-3.5 w-3.5" /> Use this schedule
                </button>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ── NL Scheduler Tab ──────────────────────────────────────────────────────────

function NLSchedulerTab() {
  const qc = useQueryClient();
  const [messages, setMessages] = useState<Array<{ role: 'user' | 'assistant'; content: string }>>([]);
  const [input, setInput] = useState('');

  const nlMutation = useMutation({
    mutationFn: (command: string) =>
      apiFetch<Array<{ schedule_id: string; trigger_type?: string; spec?: unknown }>>('/nl/schedule', {
        method: 'POST',
        body: JSON.stringify({ command }),
      }),
    onSuccess: (res, command) => {
      const reply = Array.isArray(res) && res.length > 0
        ? `Created ${res.length} schedule(s). First: ${JSON.stringify(res[0], null, 2).slice(0, 200)}`
        : 'No schedules created.';
      setMessages((m) => [...m, { role: 'user', content: command }, { role: 'assistant', content: reply }]);
      setInput('');
      void qc.invalidateQueries({ queryKey: ['schedules'] });
    },
    onError: (e) => {
      setMessages((m) => [...m, { role: 'assistant', content: `Error: ${String(e)}` }]);
    },
  });

  const examples = [
    'Run a PR review every weekday at 9 AM',
    'Check server health every 30 minutes',
    'Send weekly summary every Monday at 8 AM',
    'Generate monthly report on the 1st at midnight',
  ];

  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-2 min-h-[200px] bg-muted/20 rounded-xl p-3">
        {messages.length === 0 && (
          <div className="flex flex-col items-center justify-center flex-1 py-8 text-muted-foreground gap-2">
            <Bot className="h-8 w-8 opacity-30" />
            <p className="text-sm">Type a natural language schedule description</p>
            <div className="flex flex-wrap justify-center gap-2 mt-2">
              {examples.map((e) => (
                <button key={e} onClick={() => setInput(e)}
                  className="text-xs px-3 py-1 bg-card border border-border rounded hover:bg-muted">{e}</button>
              ))}
            </div>
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`p-3 rounded-xl text-sm max-w-[90%] ${m.role === 'user' ? 'ml-auto bg-primary text-primary-foreground' : 'mr-auto bg-card border border-border'}`}>
            {m.role === 'assistant' ? <pre className="whitespace-pre-wrap font-mono text-xs">{m.content}</pre> : m.content}
          </div>
        ))}
        {nlMutation.isPending && (
          <div className="mr-auto flex items-center gap-2 p-3 bg-card border border-border rounded-xl text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" /> Parsing schedule…
          </div>
        )}
      </div>
      <div className="flex gap-2">
        <input value={input} onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey && input.trim()) { e.preventDefault(); nlMutation.mutate(input); } }}
          placeholder="Describe your schedule in plain English…"
          className="flex-1 px-3 py-2 border border-border rounded-md text-sm bg-background" />
        <button onClick={() => nlMutation.mutate(input)} disabled={!input.trim() || nlMutation.isPending}
          className="px-4 py-2 bg-primary text-primary-foreground rounded-md text-sm disabled:opacity-50">
          Create
        </button>
      </div>
    </div>
  );
}

// ── Main Page ─────────────────────────────────────────────────────────────────

export function SchedulesPage() {
  const [activeTab, setActiveTab] = useState<Tab>('schedules');
  const [advisorPrefill, setAdvisorPrefill] = useState<AISuggestion | null>(null);

  const handleUseAdvisorTemplate = (s: AISuggestion) => {
    setAdvisorPrefill(s);
    setActiveTab('schedules');
    toast({ kind: 'info', message: `Pre-filling create form with "${s.title}"` });
  };

  const tabs: Array<{ id: Tab; label: string; icon: React.ReactNode }> = [
    { id: 'schedules', label: 'Schedules', icon: <Calendar className="h-4 w-4" /> },
    { id: 'analytics', label: 'Analytics', icon: <BarChart2 className="h-4 w-4" /> },
    { id: 'advisor', label: 'AI Advisor', icon: <Sparkles className="h-4 w-4 text-violet-500" /> },
    { id: 'nl', label: 'NL Scheduler', icon: <Bot className="h-4 w-4" /> },
  ];

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold flex items-center gap-2">
          <Calendar className="h-6 w-6 text-violet-500" /> Schedules
        </h1>
        <p className="text-muted-foreground text-sm mt-1">Automate agent goals with cron, interval, webhook, and AI-suggested triggers</p>
      </div>

      <div className="bg-card border border-border rounded-xl overflow-hidden">
        <div className="flex border-b border-border overflow-x-auto">
          {tabs.map((t) => (
            <button key={t.id} data-testid={`tab-${t.id}`} onClick={() => setActiveTab(t.id)}
              className={`flex items-center gap-2 px-5 py-3 text-sm font-medium border-b-2 whitespace-nowrap transition-colors ${
                activeTab === t.id ? 'border-primary text-primary' : 'border-transparent text-muted-foreground hover:text-foreground'
              }`}>
              {t.icon} {t.label}
            </button>
          ))}
        </div>
        <div className="p-5">
          {activeTab === 'schedules'  && <SchedulesTab />}
          {activeTab === 'analytics' && <AnalyticsTab />}
          {activeTab === 'advisor'   && <AIAdvisorTab onUseTemplate={handleUseAdvisorTemplate} />}
          {activeTab === 'nl'        && <NLSchedulerTab />}
        </div>
      </div>
    </div>
  );
}

export default SchedulesPage;
