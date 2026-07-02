/**
 * GovernancePage — World-Class Governance Center
 *
 * Four fully-featured tabs:
 *   Policies  — CRUD with time windows, simulate, version history, rollback
 *   Approvals — HITL queue with SLA stats, batch approve/reject, live SSE
 *   Audit     — Tamper-evident log with chain verification, filters, export
 *   Budget    — Gauge + thresholds + cost anomalies + real-time spend
 *
 * Plus: Emergency Stop kill-switch (always visible above tabs)
 */
import { useCallback, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Activity,
  AlertTriangle,
  BadgeCheck,
  BarChart2,
  CheckCircle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  ClipboardCopy,
  Clock,
  DollarSign,
  Download,
  FileJson,
  Filter,
  History,
  Info,
  Loader2,
  Plus,
  RefreshCw,
  RotateCcw,
  Search,
  Shield,
  ShieldAlert,
  ShieldCheck,
  ShieldOff,
  Sparkles,
  Trash2,
  XCircle,
  Zap,
} from 'lucide-react';
import { Link } from 'react-router-dom';
import {
  governanceApi,
  auditApi,
  costsApi,
  type GovernancePolicy,
  type CreateGovernancePolicyRequest,
  type SlaStats,
  type AuditEvent,
  type AuditQuery,
  type GovBudget,
} from '@/lib/api/client';
import { useAuthStore } from '@/stores/auth';
import { useEmergencyStore } from '@/stores/emergency';
import { toast } from '@/stores/toast';
import { useEventStream } from '@/lib/sse/useEventStream';

// ── Types ──────────────────────────────────────────────────────────────────────

type GovTab = 'policies' | 'approvals' | 'audit' | 'budget';

// ── Utilities ─────────────────────────────────────────────────────────────────

function fmtUsd(n: number) {
  return `$${n.toFixed(2)}`;
}

function fmtDuration(sec: number) {
  if (sec < 60) return `${Math.round(sec)}s`;
  if (sec < 3600) return `${Math.round(sec / 60)}m`;
  return `${(sec / 3600).toFixed(1)}h`;
}

function actionColor(action: string) {
  if (action === 'deny') return 'bg-red-100 text-red-700 border-red-200';
  if (action === 'require_approval') return 'bg-orange-100 text-orange-700 border-orange-200';
  return 'bg-gray-100 text-gray-600 border-gray-200';
}

function riskColor(risk?: string) {
  switch (risk) {
    case 'critical': return 'bg-red-100 text-red-700';
    case 'high': return 'bg-orange-100 text-orange-700';
    case 'medium': return 'bg-yellow-100 text-yellow-700';
    case 'low': return 'bg-green-100 text-green-700';
    default: return 'bg-gray-100 text-gray-600';
  }
}

function outcomeBadge(outcome: string) {
  const o = (outcome || '').toLowerCase();
  if (o.includes('allow') || o === 'success') return 'bg-green-100 text-green-700';
  if (o.includes('deny') || o.includes('block')) return 'bg-red-100 text-red-700';
  if (o.includes('approv')) return 'bg-orange-100 text-orange-700';
  return 'bg-gray-100 text-gray-600';
}

function actionLevelBadge(level: string) {
  switch ((level || '').toLowerCase()) {
    case 'allow': return 'bg-green-100 text-green-700';
    case 'allow_log': return 'bg-blue-100 text-blue-700';
    case 'approval': return 'bg-orange-100 text-orange-700';
    case 'deny': return 'bg-red-100 text-red-700';
    default: return 'bg-gray-100 text-gray-600';
  }
}

// ── Budget Gauge (SVG) ────────────────────────────────────────────────────────

function BudgetGauge({
  used,
  total,
  label,
  size = 120,
}: {
  used: number;
  total: number;
  label: string;
  size?: number;
}) {
  const pct = total > 0 ? Math.min(used / total, 1) : 0;
  const r = (size / 2) * 0.7;
  const circumference = 2 * Math.PI * r;
  const strokeDash = circumference * pct;
  const color = pct < 0.5 ? '#22c55e' : pct < 0.8 ? '#f59e0b' : '#ef4444';
  return (
    <div className="flex flex-col items-center gap-1">
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="#e5e7eb" strokeWidth={10} />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke={color}
          strokeWidth={10}
          strokeDasharray={`${strokeDash} ${circumference}`}
          strokeLinecap="round"
          transform={`rotate(-90 ${size / 2} ${size / 2})`}
          style={{ transition: 'stroke-dasharray 0.6s ease' }}
        />
        <text x={size / 2} y={size / 2 + 5} textAnchor="middle" fontSize={size * 0.18} fontWeight="bold" fill={color}>
          {Math.round(pct * 100)}%
        </text>
      </svg>
      <span className="text-xs text-muted-foreground text-center">{label}</span>
      <span className="text-xs font-mono">{fmtUsd(used)} / {fmtUsd(total)}</span>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════════
// EMERGENCY STOP BANNER
// ═══════════════════════════════════════════════════════════════════════════════

function EmergencyStopBanner() {
  const { isActive, activatedAt, cancelledGoals, rejectedApprovals, setActive, clear } = useEmergencyStore();
  const [confirming, setConfirming] = useState(false);

  const stopMutation = useMutation({
    mutationFn: () => governanceApi.emergencyStop(),
    onSuccess: (d) => {
      setActive({ cancelledGoals: d.cancelled_goals, rejectedApprovals: d.rejected_approvals });
      setConfirming(false);
      toast({ kind: 'error', message: 'Emergency stop activated.' });
    },
  });

  const clearMutation = useMutation({
    mutationFn: () => governanceApi.clearEmergencyStop(),
    onSuccess: () => {
      clear();
      toast({ kind: 'success', message: 'Emergency stop cleared.' });
    },
  });

  if (isActive) {
    return (
      <div
        data-testid="emergency-banner"
        className="bg-red-600 text-white rounded-xl px-4 py-3 flex items-center justify-between gap-4 flex-wrap"
      >
        <div className="flex items-center gap-3">
          <ShieldAlert className="h-5 w-5 shrink-0 animate-pulse" />
          <div>
            <p className="font-semibold">⚠ Emergency Stop Active — All goal execution halted</p>
            <p className="text-xs text-red-200 mt-0.5">
              {cancelledGoals} goals cancelled · {rejectedApprovals} approvals rejected
              {activatedAt && ` · activated ${new Date(activatedAt).toLocaleTimeString()}`}
            </p>
          </div>
        </div>
        <button
          onClick={() => clearMutation.mutate()}
          disabled={clearMutation.isPending}
          className="px-3 py-1.5 bg-white text-red-700 rounded-md text-sm font-medium hover:bg-red-50"
        >
          {clearMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : 'Clear Emergency Stop'}
        </button>
      </div>
    );
  }

  return (
    <div className="flex items-center justify-between bg-card border border-border rounded-xl px-4 py-2.5">
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <ShieldCheck className="h-4 w-4 text-green-500" />
        <span>System operational — all agents running normally</span>
      </div>
      {confirming ? (
        <div className="flex items-center gap-2">
          <span className="text-sm text-red-600 font-medium">Halt all agent execution?</span>
          <button
            onClick={() => stopMutation.mutate()}
            disabled={stopMutation.isPending}
            className="px-3 py-1.5 bg-red-600 text-white rounded-md text-xs font-medium hover:bg-red-700 flex items-center gap-1"
          >
            {stopMutation.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
            Confirm Stop
          </button>
          <button
            onClick={() => setConfirming(false)}
            className="px-3 py-1.5 border border-border rounded-md text-xs"
          >
            Cancel
          </button>
        </div>
      ) : (
        <button
          data-testid="emergency-stop-btn"
          onClick={() => setConfirming(true)}
          className="flex items-center gap-1.5 px-3 py-1.5 bg-red-50 text-red-700 border border-red-200 rounded-md text-sm hover:bg-red-100"
        >
          <Zap className="h-3.5 w-3.5" />
          Emergency Stop
        </button>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════════
// POLICIES TAB
// ═══════════════════════════════════════════════════════════════════════════════

const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const PATTERN_EXAMPLES = [
  'shell:*', 'github:delete*', 'jira:create*', 'slack:*', '*:delete*', 'deploy:*',
];

function SimulateModal({
  onClose,
  tenantId,
}: {
  onClose: () => void;
  tenantId: string;
}) {
  const [toolsText, setToolsText] = useState('shell:execute\ngithub:delete_repo\njira:create_issue');
  const simulateMutation = useMutation({
    mutationFn: () =>
      governanceApi.simulatePolicies(
        toolsText.split('\n').map((t) => t.trim()).filter(Boolean)
      ),
  });

  const results = simulateMutation.data?.simulation_results ?? {};

  return (
    <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4">
      <div className="bg-card border border-border rounded-xl w-full max-w-lg shadow-xl">
        <div className="flex items-center justify-between px-5 py-4 border-b border-border">
          <h3 className="font-semibold flex items-center gap-2">
            <Sparkles className="h-4 w-4 text-violet-500" /> Policy Simulator
          </h3>
          <button onClick={onClose} className="text-muted-foreground hover:text-foreground">
            <XCircle className="h-5 w-5" />
          </button>
        </div>
        <div className="p-5 space-y-4">
          <div>
            <label className="block text-sm font-medium mb-1">
              Tool calls to simulate <span className="text-muted-foreground font-normal">(one per line)</span>
            </label>
            <textarea
              value={toolsText}
              onChange={(e) => setToolsText(e.target.value)}
              rows={5}
              className="w-full px-3 py-2 border border-border rounded-md text-sm font-mono bg-background resize-none"
            />
          </div>
          <button
            onClick={() => simulateMutation.mutate()}
            disabled={simulateMutation.isPending}
            className="w-full flex items-center justify-center gap-2 py-2 bg-primary text-primary-foreground rounded-md text-sm"
          >
            {simulateMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
            Run Simulation
          </button>
          {Object.keys(results).length > 0 && (
            <div className="space-y-1.5">
              <p className="text-sm font-medium">Results for tenant <code className="text-xs">{tenantId}</code>:</p>
              {Object.entries(results).map(([tool, result]) => (
                <div key={tool} className="flex items-center justify-between text-sm p-2 bg-muted/40 rounded-md">
                  <span className="font-mono text-xs">{tool}</span>
                  <span className={`px-2 py-0.5 rounded text-xs font-medium border ${actionColor(result.toLowerCase().includes('deny') ? 'deny' : result.toLowerCase().includes('approv') ? 'require_approval' : 'allow')}`}>
                    {result}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function VersionHistoryModal({
  policyId,
  policyName,
  onClose,
}: {
  policyId: string;
  policyName: string;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const { data: versions = [], isLoading } = useQuery({
    queryKey: ['policy-versions', policyId],
    queryFn: () => governanceApi.getPolicyVersions(policyId),
  });

  const rollbackMutation = useMutation({
    mutationFn: ({ v, reason }: { v: number; reason: string }) =>
      governanceApi.rollbackPolicy(policyId, v, reason),
    onSuccess: () => {
      toast({ kind: 'success', message: 'Policy rolled back.' });
      void qc.invalidateQueries({ queryKey: ['governance-policies'] });
      onClose();
    },
    onError: () => toast({ kind: 'error', message: 'Rollback failed.' }),
  });

  return (
    <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4">
      <div className="bg-card border border-border rounded-xl w-full max-w-lg shadow-xl max-h-[80vh] overflow-hidden flex flex-col">
        <div className="flex items-center justify-between px-5 py-4 border-b border-border">
          <h3 className="font-semibold flex items-center gap-2">
            <History className="h-4 w-4" /> Version History — {policyName}
          </h3>
          <button onClick={onClose} className="text-muted-foreground hover:text-foreground">
            <XCircle className="h-5 w-5" />
          </button>
        </div>
        <div className="overflow-y-auto p-5 space-y-3">
          {isLoading && <div className="flex justify-center py-8"><Loader2 className="h-5 w-5 animate-spin" /></div>}
          {!isLoading && versions.length === 0 && (
            <p className="text-sm text-muted-foreground text-center py-6">No version history available (requires database).</p>
          )}
          {versions.map((v) => (
            <div key={v.id} className={`border rounded-lg p-3 ${v.is_active ? 'border-primary bg-primary/5' : 'border-border'}`}>
              <div className="flex items-center justify-between mb-1">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium">v{v.version_number}</span>
                  {v.is_active && <span className="px-1.5 py-0.5 bg-primary/20 text-primary text-xs rounded">Active</span>}
                </div>
                <span className="text-xs text-muted-foreground">
                  {new Date(v.changed_at).toLocaleString()}
                </span>
              </div>
              {v.change_summary && <p className="text-xs text-muted-foreground">{v.change_summary}</p>}
              {v.changed_by && <p className="text-xs text-muted-foreground">by {v.changed_by}</p>}
              {!v.is_active && (
                <button
                  onClick={() =>
                    rollbackMutation.mutate({
                      v: v.version_number,
                      reason: `UI rollback to v${v.version_number}`,
                    })
                  }
                  disabled={rollbackMutation.isPending}
                  className="mt-2 flex items-center gap-1 text-xs text-primary hover:underline"
                >
                  <RotateCcw className="h-3 w-3" /> Rollback to this version
                </button>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function PoliciesTab({ tenantId }: { tenantId: string }) {
  const qc = useQueryClient();
  const [search, setSearch] = useState('');
  const [showForm, setShowForm] = useState(false);
  const [showSimulate, setShowSimulate] = useState(false);
  const [versionPolicyId, setVersionPolicyId] = useState<string | null>(null);
  const [versionPolicyName, setVersionPolicyName] = useState('');
  const [selected, setSelected] = useState<Set<string>>(new Set());

  // Form state
  const [form, setForm] = useState<Omit<CreateGovernancePolicyRequest, 'name'> & { name: string }>({
    name: '',
    description: '',
    tools_pattern: '',
    action: 'deny',
    priority: 0,
    allowed_hours_utc: undefined,
    allowed_weekdays: undefined,
  });
  const [useTimeWindow, setUseTimeWindow] = useState(false);
  const [selectedHours, setSelectedHours] = useState<number[]>([]);
  const [selectedDays, setSelectedDays] = useState<number[]>([]);

  const { data: policies = [], isLoading, isFetching } = useQuery({
    queryKey: ['governance-policies'],
    queryFn: () => governanceApi.listGovernancePolicies(),
    refetchInterval: 30_000,
  });

  // SSE live updates for policies
  useEventStream(governanceApi.policiesStreamPath(), {
    onEvent: () => void qc.invalidateQueries({ queryKey: ['governance-policies'] }),
  });

  const createMutation = useMutation({
    mutationFn: (data: CreateGovernancePolicyRequest) =>
      governanceApi.createGovernancePolicy(data),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['governance-policies'] });
      setShowForm(false);
      setForm({ name: '', description: '', tools_pattern: '', action: 'deny', priority: 0 });
      setUseTimeWindow(false);
      setSelectedHours([]);
      setSelectedDays([]);
      toast({ kind: 'success', message: 'Policy created.' });
    },
    onError: () => toast({ kind: 'error', message: 'Failed to create policy.' }),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => governanceApi.deletePolicy(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['governance-policies'] });
      toast({ kind: 'success', message: 'Policy deleted.' });
    },
  });

  const bulkDeleteMutation = useMutation({
    mutationFn: async () => {
      for (const id of selected) await governanceApi.deletePolicy(id);
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['governance-policies'] });
      setSelected(new Set());
      toast({ kind: 'success', message: `${selected.size} policies deleted.` });
    },
  });

  const filtered = policies.filter(
    (p) =>
      !search ||
      p.name.toLowerCase().includes(search.toLowerCase()) ||
      p.tools_pattern.toLowerCase().includes(search.toLowerCase())
  );

  const handleCreate = () => {
    if (!form.name || !form.tools_pattern) {
      toast({ kind: 'error', message: 'Name and Tools Pattern are required.' });
      return;
    }
    createMutation.mutate({
      ...form,
      allowed_hours_utc: useTimeWindow && selectedHours.length ? selectedHours : undefined,
      allowed_weekdays: useTimeWindow && selectedDays.length ? selectedDays : undefined,
    });
  };

  const toggleSelect = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  return (
    <div className="space-y-4">
      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex-1 relative min-w-[160px]">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search policies…"
            className="w-full pl-9 pr-3 py-2 border border-border rounded-md text-sm bg-background"
          />
        </div>
        <div className="flex gap-2">
          {selected.size > 0 && (
            <button
              onClick={() => bulkDeleteMutation.mutate()}
              disabled={bulkDeleteMutation.isPending}
              className="flex items-center gap-1.5 px-3 py-2 bg-red-50 text-red-700 border border-red-200 rounded-md text-sm"
            >
              <Trash2 className="h-4 w-4" /> Delete {selected.size}
            </button>
          )}
          <button
            onClick={() => setShowSimulate(true)}
            className="flex items-center gap-1.5 px-3 py-2 border border-border rounded-md text-sm hover:bg-muted"
          >
            <Sparkles className="h-4 w-4 text-violet-500" /> Simulate
          </button>
          <button
            onClick={() => setShowForm((v) => !v)}
            className="flex items-center gap-1.5 px-3 py-2 bg-primary text-primary-foreground rounded-md text-sm"
          >
            <Plus className="h-4 w-4" /> New Policy
          </button>
        </div>
        {isFetching && !isLoading && (
          <RefreshCw className="h-4 w-4 animate-spin text-muted-foreground" />
        )}
      </div>

      {/* Create form */}
      {showForm && (
        <div
          data-testid="policy-form"
          className="bg-card border border-border rounded-xl p-4 space-y-4"
        >
          <h3 className="font-medium text-sm flex items-center gap-2">
            <Shield className="h-4 w-4" /> Create Policy
          </h3>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-muted-foreground mb-1">Policy name *</label>
              <input
                id="policy-name"
                data-testid="policy-name-input"
                value={form.name}
                onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                placeholder="block-shell-commands"
                className="w-full px-3 py-2 border border-border rounded-md text-sm bg-background"
              />
            </div>
            <div>
              <label className="block text-xs text-muted-foreground mb-1">Tools pattern *</label>
              <div className="flex gap-2">
                <input
                  id="tools-pattern"
                  data-testid="policy-pattern-input"
                  value={form.tools_pattern}
                  onChange={(e) => setForm((f) => ({ ...f, tools_pattern: e.target.value }))}
                  placeholder="shell:*"
                  className="flex-1 px-3 py-2 border border-border rounded-md text-sm bg-background"
                />
                <select
                  aria-label="Pattern examples"
                  onChange={(e) => e.target.value && setForm((f) => ({ ...f, tools_pattern: e.target.value }))}
                  className="px-2 py-2 border border-border rounded-md text-xs bg-background"
                  value=""
                >
                  <option value="">Examples</option>
                  {PATTERN_EXAMPLES.map((p) => (
                    <option key={p} value={p}>{p}</option>
                  ))}
                </select>
              </div>
            </div>
            <div>
              <label className="block text-xs text-muted-foreground mb-1">Action</label>
              <select
                id="policy-action"
                data-testid="policy-action-select"
                value={form.action}
                onChange={(e) => setForm((f) => ({ ...f, action: e.target.value as 'deny' | 'require_approval' }))}
                className="w-full px-3 py-2 border border-border rounded-md text-sm bg-background"
              >
                <option value="deny">Deny (block immediately)</option>
                <option value="require_approval">Require Approval (HITL)</option>
              </select>
            </div>
            <div>
              <label className="block text-xs text-muted-foreground mb-1">Priority (higher = enforced first)</label>
              <input
                type="number"
                min={0}
                max={100}
                value={form.priority}
                onChange={(e) => setForm((f) => ({ ...f, priority: Number(e.target.value) }))}
                className="w-full px-3 py-2 border border-border rounded-md text-sm bg-background"
              />
            </div>
          </div>
          <div>
            <label className="block text-xs text-muted-foreground mb-1">Description (optional)</label>
            <textarea
              value={form.description}
              onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
              rows={2}
              placeholder="Blocks all shell execution for security"
              className="w-full px-3 py-2 border border-border rounded-md text-sm bg-background resize-none"
            />
          </div>

          {/* Time window */}
          <div className="border border-border rounded-lg p-3 space-y-3">
            <label className="flex items-center gap-2 text-sm cursor-pointer">
              <input
                type="checkbox"
                checked={useTimeWindow}
                onChange={(e) => setUseTimeWindow(e.target.checked)}
                className="rounded"
              />
              <Clock className="h-4 w-4" />
              <span>Restrict to time window</span>
            </label>
            {useTimeWindow && (
              <div className="pl-6 space-y-3">
                <div>
                  <p className="text-xs text-muted-foreground mb-1.5">Allowed hours (UTC)</p>
                  <div className="flex flex-wrap gap-1">
                    {Array.from({ length: 24 }, (_, h) => (
                      <button
                        key={h}
                        type="button"
                        onClick={() =>
                          setSelectedHours((prev) =>
                            prev.includes(h) ? prev.filter((x) => x !== h) : [...prev, h]
                          )
                        }
                        className={`w-8 h-7 text-xs rounded ${selectedHours.includes(h) ? 'bg-primary text-primary-foreground' : 'bg-muted text-muted-foreground hover:bg-muted/80'}`}
                      >
                        {h}
                      </button>
                    ))}
                  </div>
                </div>
                <div>
                  <p className="text-xs text-muted-foreground mb-1.5">Allowed days</p>
                  <div className="flex flex-wrap gap-1">
                    {WEEKDAYS.map((d, i) => (
                      <button
                        key={d}
                        type="button"
                        onClick={() =>
                          setSelectedDays((prev) =>
                            prev.includes(i) ? prev.filter((x) => x !== i) : [...prev, i]
                          )
                        }
                        className={`px-2.5 py-1 text-xs rounded ${selectedDays.includes(i) ? 'bg-primary text-primary-foreground' : 'bg-muted text-muted-foreground hover:bg-muted/80'}`}
                      >
                        {d}
                      </button>
                    ))}
                  </div>
                </div>
              </div>
            )}
          </div>

          <div className="flex gap-2">
            <button
              data-testid="save-policy-btn"
              onClick={handleCreate}
              disabled={createMutation.isPending}
              className="flex items-center gap-1.5 px-4 py-2 bg-primary text-primary-foreground rounded-md text-sm disabled:opacity-50"
            >
              {createMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
              Save Policy
            </button>
            <button
              onClick={() => setShowForm(false)}
              className="px-4 py-2 border border-border rounded-md text-sm"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Policies table */}
      {isLoading ? (
        <div className="flex justify-center py-12">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      ) : filtered.length === 0 ? (
        <div
          data-testid="policies-empty"
          className="flex flex-col items-center justify-center py-14 text-muted-foreground gap-2"
        >
          <ShieldOff className="h-10 w-10 opacity-30" />
          <p className="text-sm">No policies configured</p>
          <p className="text-xs">Create a policy to control which tools agents can execute.</p>
        </div>
      ) : (
        <div className="border border-border rounded-xl overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-muted/40 border-b border-border">
              <tr>
                <th className="px-3 py-2.5 text-left w-8">
                  <input
                    type="checkbox"
                    onChange={(e) =>
                      setSelected(e.target.checked ? new Set(filtered.map((p) => p.policy_id)) : new Set())
                    }
                    checked={selected.size === filtered.length && filtered.length > 0}
                    className="rounded"
                  />
                </th>
                <th className="px-3 py-2.5 text-left text-xs font-medium text-muted-foreground">Priority</th>
                <th className="px-3 py-2.5 text-left text-xs font-medium text-muted-foreground">Name</th>
                <th className="px-3 py-2.5 text-left text-xs font-medium text-muted-foreground">Pattern</th>
                <th className="px-3 py-2.5 text-left text-xs font-medium text-muted-foreground">Action</th>
                <th className="px-3 py-2.5 text-left text-xs font-medium text-muted-foreground">Window</th>
                <th className="px-3 py-2.5 text-right text-xs font-medium text-muted-foreground">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {filtered.map((p) => (
                <tr key={p.policy_id} className="hover:bg-muted/20">
                  <td className="px-3 py-2.5">
                    <input
                      type="checkbox"
                      checked={selected.has(p.policy_id)}
                      onChange={() => toggleSelect(p.policy_id)}
                      className="rounded"
                    />
                  </td>
                  <td className="px-3 py-2.5">
                    <span className="text-xs font-mono bg-muted px-1.5 py-0.5 rounded">{p.priority}</span>
                  </td>
                  <td className="px-3 py-2.5">
                    <p className="font-medium">{p.name}</p>
                    {p.description && (
                      <p className="text-xs text-muted-foreground truncate max-w-[200px]">{p.description}</p>
                    )}
                  </td>
                  <td className="px-3 py-2.5">
                    <code className="text-xs bg-muted px-1.5 py-0.5 rounded font-mono">{p.tools_pattern}</code>
                  </td>
                  <td className="px-3 py-2.5">
                    <span className={`px-2 py-0.5 rounded text-xs font-medium border ${actionColor(p.action)}`}>
                      {p.action}
                    </span>
                  </td>
                  <td className="px-3 py-2.5">
                    {p.allowed_hours_utc?.length || p.allowed_weekdays?.length ? (
                      <div className="flex items-center gap-1 text-xs text-amber-600">
                        <Clock className="h-3 w-3" /> Restricted
                      </div>
                    ) : (
                      <span className="text-xs text-muted-foreground">Always</span>
                    )}
                  </td>
                  <td className="px-3 py-2.5">
                    <div className="flex items-center justify-end gap-1">
                      <button
                        onClick={() => {
                          setVersionPolicyId(p.policy_id);
                          setVersionPolicyName(p.name);
                        }}
                        className="p-1.5 text-muted-foreground hover:text-foreground rounded"
                        title="Version history"
                      >
                        <History className="h-3.5 w-3.5" />
                      </button>
                      <button
                        data-testid={`delete-policy-${p.policy_id}`}
                        onClick={() => deleteMutation.mutate(p.policy_id)}
                        disabled={deleteMutation.isPending}
                        className="p-1.5 text-muted-foreground hover:text-red-500 rounded"
                        title="Delete"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {showSimulate && <SimulateModal onClose={() => setShowSimulate(false)} tenantId={tenantId} />}
      {versionPolicyId && (
        <VersionHistoryModal
          policyId={versionPolicyId}
          policyName={versionPolicyName}
          onClose={() => setVersionPolicyId(null)}
        />
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════════
// APPROVALS TAB
// ═══════════════════════════════════════════════════════════════════════════════

function SlaStatsRow({ stats }: { stats: SlaStats }) {
  const withinPct = stats.pending + stats.approved + stats.denied > 0
    ? Math.round((stats.within_sla / (stats.approved + stats.denied || 1)) * 100)
    : 0;

  return (
    <div
      data-testid="sla-stats"
      className="grid grid-cols-2 sm:grid-cols-4 gap-3"
    >
      {[
        { label: 'Pending', value: stats.pending, icon: <Clock className="h-4 w-4 text-amber-500" /> },
        { label: 'Approved', value: stats.approved, icon: <CheckCircle className="h-4 w-4 text-green-500" /> },
        { label: 'Denied', value: stats.denied, icon: <XCircle className="h-4 w-4 text-red-500" /> },
        { label: 'Within SLA', value: `${withinPct}%`, icon: <BadgeCheck className="h-4 w-4 text-blue-500" /> },
      ].map((s) => (
        <div key={s.label} className="bg-card border border-border rounded-lg p-3">
          <div className="flex items-center gap-1.5 text-xs text-muted-foreground mb-1">
            {s.icon} {s.label}
          </div>
          <p className="text-xl font-bold">{s.value}</p>
          {s.label === 'Pending' && stats.avg_resolution_seconds > 0 && (
            <p className="text-xs text-muted-foreground mt-0.5">
              avg {fmtDuration(stats.avg_resolution_seconds)}
            </p>
          )}
        </div>
      ))}
    </div>
  );
}

function ApprovalsTab({ tenantId }: { tenantId: string }) {
  const qc = useQueryClient();
  const approver = tenantId ? `user:${tenantId.slice(0, 12)}` : 'ui-user';
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const { data: approvals = [], isLoading } = useQuery({
    queryKey: ['governance-approvals'],
    queryFn: () => governanceApi.listApprovals(),
    refetchInterval: 15_000,
  });

  const { data: slaStats } = useQuery({
    queryKey: ['governance-sla-stats'],
    queryFn: () => governanceApi.getSlaStats(),
    staleTime: 60_000,
  });

  // SSE live updates
  useEventStream(governanceApi.approvalsStreamPath(), {
    onEvent: () => void qc.invalidateQueries({ queryKey: ['governance-approvals'] }),
  });

  const approveMutation = useMutation({
    mutationFn: ({ id }: { id: string }) =>
      governanceApi.approve(id, approver, notes[id] ?? ''),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['governance-approvals'] });
      toast({ kind: 'success', message: 'Approval granted.' });
    },
  });

  const rejectMutation = useMutation({
    mutationFn: ({ id }: { id: string }) =>
      governanceApi.reject(id, approver, notes[id] ?? ''),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['governance-approvals'] });
      toast({ kind: 'info', message: 'Rejected.' });
    },
  });

  const batchMutation = useMutation({
    mutationFn: ({ action }: { action: 'approve' | 'reject' }) =>
      governanceApi.batchApprove([...selected], action, approver, ''),
    onSuccess: (d, { action }) => {
      void qc.invalidateQueries({ queryKey: ['governance-approvals'] });
      setSelected(new Set());
      toast({
        kind: 'success',
        message: action === 'approve'
          ? `${d.approved} approved, ${d.not_found} not found.`
          : `${d.rejected} rejected.`,
      });
    },
  });

  const pending = approvals.filter((a) => a.status === 'pending');
  const resolved = approvals.filter((a) => a.status !== 'pending').slice(0, 10);

  const toggleSelect = (id: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  return (
    <div className="space-y-4">
      {/* SLA stats */}
      {slaStats && <SlaStatsRow stats={slaStats} />}

      {/* Batch toolbar */}
      {selected.size > 0 && (
        <div
          data-testid="batch-toolbar"
          className="flex items-center gap-3 bg-primary/5 border border-primary/20 rounded-lg px-4 py-2.5"
        >
          <span className="text-sm font-medium">{selected.size} selected</span>
          <button
            onClick={() => batchMutation.mutate({ action: 'approve' })}
            disabled={batchMutation.isPending}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-green-600 text-white rounded-md text-xs"
          >
            <CheckCircle2 className="h-3.5 w-3.5" /> Batch Approve
          </button>
          <button
            onClick={() => batchMutation.mutate({ action: 'reject' })}
            disabled={batchMutation.isPending}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-red-600 text-white rounded-md text-xs"
          >
            <XCircle className="h-3.5 w-3.5" /> Batch Reject
          </button>
          <button onClick={() => setSelected(new Set())} className="text-xs text-muted-foreground ml-auto">
            Clear
          </button>
        </div>
      )}

      {/* Header row */}
      <div className="flex items-center justify-between">
        <h3 className="font-semibold text-sm">
          Pending Approvals
          {pending.length > 0 && (
            <span className="ml-2 bg-orange-100 text-orange-700 text-xs px-1.5 py-0.5 rounded-full">
              {pending.length}
            </span>
          )}
        </h3>
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <span className="h-2 w-2 rounded-full bg-green-500 animate-pulse" />
          Live updates
        </div>
      </div>

      {isLoading ? (
        <div data-testid="loading" className="flex justify-center py-12">
          <Loader2 className="h-6 w-6 animate-spin" />
        </div>
      ) : pending.length === 0 ? (
        <div
          data-testid="approvals-empty"
          className="flex flex-col items-center justify-center py-14 text-muted-foreground gap-2"
        >
          <CheckCircle className="h-10 w-10 opacity-30 text-green-500" />
          <p className="text-sm font-medium">All clear — no pending approvals</p>
          <p className="text-xs">Your agents are running autonomously.</p>
        </div>
      ) : (
        <div className="space-y-3">
          {pending.map((req) => (
            <div
              key={req.request_id}
              data-testid="approval-card"
              className="bg-card border border-border rounded-xl p-4 space-y-3"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="flex items-start gap-3">
                  <input
                    type="checkbox"
                    checked={selected.has(req.request_id)}
                    onChange={() => toggleSelect(req.request_id)}
                    className="mt-1 rounded"
                  />
                  <div>
                    <div className="flex items-center gap-2 flex-wrap">
                      <code className="text-xs font-mono text-muted-foreground">{req.request_id.slice(0, 16)}…</code>
                      {req.risk_level && (
                        <span
                          data-testid="risk-badge"
                          className={`px-2 py-0.5 rounded text-xs font-medium ${riskColor(req.risk_level)}`}
                        >
                          {req.risk_level}
                        </span>
                      )}
                      <span className="px-2 py-0.5 bg-orange-100 text-orange-700 text-xs rounded">pending</span>
                    </div>
                    {req.action && (
                      <p className="text-sm font-medium mt-1">{req.action}</p>
                    )}
                    <Link
                      to={`/goals/${req.goal_id}`}
                      className="text-xs text-blue-500 hover:underline font-mono mt-0.5 block"
                    >
                      Goal: {req.goal_id}
                    </Link>
                  </div>
                </div>
              </div>
              <textarea
                value={notes[req.request_id] ?? ''}
                onChange={(e) => setNotes((n) => ({ ...n, [req.request_id]: e.target.value }))}
                placeholder="Add a note (optional)"
                rows={2}
                className="w-full px-3 py-2 border border-border rounded-md text-sm bg-background resize-none"
              />
              <div className="flex gap-2">
                <button
                  data-testid={`approve-btn-${req.request_id}`}
                  onClick={() => approveMutation.mutate({ id: req.request_id })}
                  disabled={approveMutation.isPending}
                  className="flex items-center gap-1.5 px-3 py-1.5 bg-green-600 text-white rounded-md text-sm"
                >
                  <CheckCircle className="h-4 w-4" /> Approve
                </button>
                <button
                  data-testid={`reject-btn-${req.request_id}`}
                  onClick={() => rejectMutation.mutate({ id: req.request_id })}
                  disabled={rejectMutation.isPending}
                  className="flex items-center gap-1.5 px-3 py-1.5 bg-red-600 text-white rounded-md text-sm"
                >
                  <XCircle className="h-4 w-4" /> Reject
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Recently resolved */}
      {resolved.length > 0 && (
        <div className="mt-6 space-y-2">
          <h3 className="text-sm font-semibold text-muted-foreground">Recently Resolved</h3>
          {resolved.map((req) => (
            <div
              key={req.request_id}
              className="flex items-center justify-between bg-card border border-border rounded-lg px-4 py-2.5 text-sm"
            >
              <div className="flex items-center gap-3 min-w-0">
                {req.status === 'approved' ? (
                  <CheckCircle className="h-4 w-4 text-green-500 shrink-0" />
                ) : (
                  <XCircle className="h-4 w-4 text-red-500 shrink-0" />
                )}
                <code className="text-xs font-mono text-muted-foreground truncate">{req.request_id.slice(0, 16)}…</code>
                {req.action && <span className="text-xs truncate hidden sm:block">{req.action}</span>}
              </div>
              <span className={`px-2 py-0.5 rounded text-xs font-medium ml-3 shrink-0 ${req.status === 'approved' ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'}`}>
                {req.status}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════════
// AUDIT TAB
// ═══════════════════════════════════════════════════════════════════════════════

function AuditTab() {
  const [query, setQuery] = useState<AuditQuery>({ limit: 100 });
  const [draft, setDraft] = useState<AuditQuery>({ limit: 100 });
  const [selectedEvent, setSelectedEvent] = useState<AuditEvent | null>(null);
  const [chainResult, setChainResult] = useState<{ verified: boolean; verified_events: number; chain_tip_hash?: string } | null>(null);

  const { data: events = [], isLoading, isFetching, refetch } = useQuery({
    queryKey: ['governance-audit', query],
    queryFn: () => auditApi.query(query),
    staleTime: 30_000,
  });

  const verifyMutation = useMutation({
    mutationFn: () => governanceApi.verifyAuditChain(),
    onSuccess: (r) => setChainResult(r),
    onError: () => toast({ kind: 'error', message: 'Chain verification failed (requires database).' }),
  });

  const applyFilters = () => setQuery({ ...draft });
  const resetFilters = () => {
    setDraft({ limit: 100 });
    setQuery({ limit: 100 });
  };

  const exportJSON = () => {
    const blob = new Blob([JSON.stringify(events, null, 2)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `audit-events-${Date.now()}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  const exportCSV = () => {
    const headers = ['event_id', 'goal_id', 'tool_name', 'action_level', 'outcome', 'approver', 'note'];
    const rows = events.map((e) =>
      headers.map((h) => JSON.stringify((e as Record<string, unknown>)[h] ?? '')).join(',')
    );
    const blob = new Blob([[headers.join(','), ...rows].join('\n')], { type: 'text/csv' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `audit-events-${Date.now()}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  // Stats
  const stats = {
    total: events.length,
    allowed: events.filter((e) => (e.action_level || '').toLowerCase().startsWith('allow')).length,
    denied: events.filter((e) => (e.action_level || '').toLowerCase() === 'deny').length,
    approval: events.filter((e) => (e.action_level || '').toLowerCase() === 'approval').length,
  };

  return (
    <div className="space-y-4">
      {/* Filters */}
      <div
        data-testid="audit-filters"
        className="bg-card border border-border rounded-xl p-4 space-y-3"
      >
        <div className="flex items-center gap-2 text-sm font-medium">
          <Filter className="h-4 w-4" /> Filters
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
          <div>
            <label htmlFor="audit-goal-id" className="block text-xs text-muted-foreground mb-1">Goal ID</label>
            <input
              id="audit-goal-id"
              value={draft.goal_id ?? ''}
              onChange={(e) => setDraft((d) => ({ ...d, goal_id: e.target.value || undefined }))}
              placeholder="goal-abc123"
              className="w-full px-3 py-2 border border-border rounded-md text-sm bg-background"
            />
          </div>
          <div>
            <label htmlFor="audit-tool-name" className="block text-xs text-muted-foreground mb-1">Tool name</label>
            <input
              id="audit-tool-name"
              value={draft.tool_name ?? ''}
              onChange={(e) => setDraft((d) => ({ ...d, tool_name: e.target.value || undefined }))}
              placeholder="shell:execute"
              className="w-full px-3 py-2 border border-border rounded-md text-sm bg-background"
            />
          </div>
          <div>
            <label className="block text-xs text-muted-foreground mb-1">Start time</label>
            <input
              type="datetime-local"
              value={draft.start_time ?? ''}
              onChange={(e) => setDraft((d) => ({ ...d, start_time: e.target.value || undefined }))}
              className="w-full px-3 py-2 border border-border rounded-md text-sm bg-background"
            />
          </div>
          <div>
            <label className="block text-xs text-muted-foreground mb-1">End time</label>
            <input
              type="datetime-local"
              value={draft.end_time ?? ''}
              onChange={(e) => setDraft((d) => ({ ...d, end_time: e.target.value || undefined }))}
              className="w-full px-3 py-2 border border-border rounded-md text-sm bg-background"
            />
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex items-center gap-2">
            <label className="text-xs text-muted-foreground">Limit:</label>
            <select
              value={draft.limit ?? 100}
              onChange={(e) => setDraft((d) => ({ ...d, limit: Number(e.target.value) }))}
              className="px-2 py-1.5 border border-border rounded-md text-xs bg-background"
            >
              {[50, 100, 200, 500].map((v) => (
                <option key={v} value={v}>{v}</option>
              ))}
            </select>
          </div>
          <button
            data-testid="apply-audit-filters"
            onClick={applyFilters}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-primary text-primary-foreground rounded-md text-xs"
          >
            <Search className="h-3.5 w-3.5" /> Apply
          </button>
          <button
            onClick={resetFilters}
            className="px-3 py-1.5 border border-border rounded-md text-xs"
          >
            Reset
          </button>
        </div>
      </div>

      {/* Stats + export bar */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap gap-3 text-sm">
          {[
            { label: 'Total', value: stats.total, color: 'text-foreground' },
            { label: 'Allowed', value: stats.allowed, color: 'text-green-600' },
            { label: 'Denied', value: stats.denied, color: 'text-red-600' },
            { label: 'Approval', value: stats.approval, color: 'text-orange-600' },
          ].map((s) => (
            <div key={s.label} className="flex items-center gap-1.5">
              <span className="text-muted-foreground text-xs">{s.label}:</span>
              <span className={`font-semibold ${s.color}`}>{s.value}</span>
            </div>
          ))}
          {isFetching && <RefreshCw className="h-4 w-4 animate-spin text-muted-foreground" />}
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => verifyMutation.mutate()}
            disabled={verifyMutation.isPending}
            className="flex items-center gap-1.5 px-3 py-1.5 border border-border rounded-md text-xs hover:bg-muted"
            title="Verify hash-chain integrity"
          >
            {verifyMutation.isPending ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : chainResult?.verified ? (
              <ShieldCheck className="h-3.5 w-3.5 text-green-500" />
            ) : chainResult ? (
              <ShieldAlert className="h-3.5 w-3.5 text-red-500" />
            ) : (
              <ShieldCheck className="h-3.5 w-3.5" />
            )}
            {chainResult ? (chainResult.verified ? 'Chain verified' : 'Chain broken!') : 'Verify chain'}
          </button>
          <button
            data-testid="export-json-btn"
            onClick={exportJSON}
            className="flex items-center gap-1.5 px-3 py-1.5 border border-border rounded-md text-xs hover:bg-muted"
          >
            <FileJson className="h-3.5 w-3.5" /> JSON
          </button>
          <button
            data-testid="export-csv-btn"
            onClick={exportCSV}
            className="flex items-center gap-1.5 px-3 py-1.5 border border-border rounded-md text-xs hover:bg-muted"
          >
            <Download className="h-3.5 w-3.5" /> CSV
          </button>
        </div>
      </div>

      {/* Chain verification result */}
      {chainResult && (
        <div className={`flex items-center gap-2 text-sm p-2.5 rounded-lg border ${chainResult.verified ? 'bg-green-50 border-green-200 text-green-700' : 'bg-red-50 border-red-200 text-red-700'}`}>
          {chainResult.verified ? (
            <><ShieldCheck className="h-4 w-4" /> {chainResult.verified_events} events verified — chain intact
              {chainResult.chain_tip_hash && (
                <code className="text-xs font-mono ml-2 opacity-70">tip: {chainResult.chain_tip_hash.slice(0, 16)}…</code>
              )}
            </>
          ) : (
            <><ShieldAlert className="h-4 w-4" /> Hash chain broken at event {(chainResult as { broken_chain_at?: string }).broken_chain_at}</>
          )}
        </div>
      )}

      {/* Events table */}
      {isLoading ? (
        <div className="flex justify-center py-12">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      ) : events.length === 0 ? (
        <div
          data-testid="audit-empty"
          className="flex flex-col items-center justify-center py-14 text-muted-foreground gap-2"
        >
          <Activity className="h-10 w-10 opacity-30" />
          <p className="text-sm">No audit events found</p>
          <p className="text-xs">Try adjusting filters or broadening the time range.</p>
        </div>
      ) : (
        <div className="border border-border rounded-xl overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-muted/40 border-b border-border">
              <tr>
                <th className="px-3 py-2.5 text-left text-xs font-medium text-muted-foreground">Time</th>
                <th className="px-3 py-2.5 text-left text-xs font-medium text-muted-foreground">Tool</th>
                <th className="px-3 py-2.5 text-left text-xs font-medium text-muted-foreground">Level</th>
                <th className="px-3 py-2.5 text-left text-xs font-medium text-muted-foreground">Outcome</th>
                <th className="px-3 py-2.5 text-left text-xs font-medium text-muted-foreground">Goal</th>
                <th className="px-3 py-2.5 text-left text-xs font-medium text-muted-foreground">Approver</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {events.map((e) => (
                <tr
                  key={e.event_id}
                  data-testid="audit-row"
                  onClick={() => setSelectedEvent(e)}
                  className="hover:bg-muted/20 cursor-pointer"
                >
                  <td className="px-3 py-2.5 text-xs text-muted-foreground whitespace-nowrap">
                    {e.event_id?.slice(0, 8) ?? '—'}
                  </td>
                  <td className="px-3 py-2.5">
                    <code className="text-xs font-mono">{e.tool_name}</code>
                  </td>
                  <td className="px-3 py-2.5">
                    <span className={`px-2 py-0.5 rounded text-xs font-medium ${actionLevelBadge(e.action_level)}`}>
                      {e.action_level}
                    </span>
                  </td>
                  <td className="px-3 py-2.5">
                    <span className={`px-2 py-0.5 rounded text-xs font-medium ${outcomeBadge(e.outcome)}`}>
                      {e.outcome}
                    </span>
                  </td>
                  <td className="px-3 py-2.5">
                    <Link
                      to={`/goals/${e.goal_id}`}
                      onClick={(ev) => ev.stopPropagation()}
                      className="text-xs font-mono text-blue-500 hover:underline"
                    >
                      {e.goal_id?.slice(0, 12)}…
                    </Link>
                  </td>
                  <td className="px-3 py-2.5 text-xs text-muted-foreground">{e.approver ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Event detail drawer */}
      {selectedEvent && (
        <div className="fixed inset-0 bg-black/40 z-40 flex justify-end" onClick={() => setSelectedEvent(null)}>
          <div
            className="bg-card border-l border-border w-full max-w-md h-full overflow-y-auto p-5 space-y-3"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between">
              <h3 className="font-semibold">Event Details</h3>
              <button onClick={() => setSelectedEvent(null)}>
                <XCircle className="h-5 w-5 text-muted-foreground" />
              </button>
            </div>
            {Object.entries(selectedEvent).map(([k, v]) =>
              v != null ? (
                <div key={k} className="space-y-0.5">
                  <p className="text-xs text-muted-foreground uppercase tracking-wide">{k}</p>
                  <p className="text-sm font-mono break-all">{String(v)}</p>
                </div>
              ) : null
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════════
// BUDGET TAB
// ═══════════════════════════════════════════════════════════════════════════════

function BudgetTab() {
  const qc = useQueryClient();
  const [draft, setDraft] = useState<GovBudget | null>(null);

  const { data: budget, isLoading: budgetLoading } = useQuery({
    queryKey: ['governance-budget'],
    queryFn: () => governanceApi.getBudget(),
    staleTime: 30_000,
  });

  const { data: costSummary } = useQuery({
    queryKey: ['costs-summary'],
    queryFn: () => costsApi.getSummary(),
    staleTime: 30_000,
    retry: false,
  });

  const { data: anomalies = [] } = useQuery({
    queryKey: ['costs-anomalies'],
    queryFn: () => costsApi.getAnomalies(),
    staleTime: 60_000,
    retry: false,
  });

  const saveMutation = useMutation({
    mutationFn: (d: GovBudget) =>
      governanceApi.setBudget({ per_goal_usd: d.per_goal_usd, per_tenant_daily_usd: d.per_tenant_daily_usd }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['governance-budget'] });
      setDraft(null);
      toast({ kind: 'success', message: 'Budget limits saved.' });
    },
    onError: () => toast({ kind: 'error', message: 'Failed to save budget.' }),
  });

  const current = draft ?? budget;
  const isDirty = draft != null && (draft.per_goal_usd !== budget?.per_goal_usd || draft.per_tenant_daily_usd !== budget?.per_tenant_daily_usd);
  const dailySpent = costSummary?.total_cost_usd ?? 0;
  const dailyBudget = budget?.per_tenant_daily_usd ?? 500;

  return (
    <div className="space-y-5">
      {/* Live spend status */}
      {costSummary && (
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          {[
            {
              label: 'Daily Spend',
              value: fmtUsd(dailySpent),
              sub: `of ${fmtUsd(dailyBudget)} budget`,
              icon: <DollarSign className="h-4 w-4 text-blue-500" />,
            },
            {
              label: 'Budget Utilization',
              value: `${Math.round((dailySpent / dailyBudget) * 100)}%`,
              sub: dailySpent / dailyBudget > 0.8 ? '⚠ Near limit' : 'Healthy',
              icon: <BarChart2 className="h-4 w-4 text-violet-500" />,
            },
            {
              label: 'Daily Remaining',
              value: fmtUsd(Math.max(dailyBudget - dailySpent, 0)),
              sub: 'available today',
              icon: <Sparkles className="h-4 w-4 text-green-500" />,
            },
          ].map((s) => (
            <div key={s.label} className="bg-card border border-border rounded-lg p-3">
              <div className="flex items-center gap-1.5 text-xs text-muted-foreground mb-1">
                {s.icon} {s.label}
              </div>
              <p className="text-xl font-bold">{s.value}</p>
              <p className="text-xs text-muted-foreground mt-0.5">{s.sub}</p>
            </div>
          ))}
        </div>
      )}

      {/* Gauges + config */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        {/* Left: Gauges */}
        <div className="bg-card border border-border rounded-xl p-4">
          <h3 className="font-medium text-sm mb-4 flex items-center gap-2">
            <BarChart2 className="h-4 w-4" /> Budget Utilization
          </h3>
          {budgetLoading ? (
            <div className="flex justify-center py-8"><Loader2 className="h-5 w-5 animate-spin" /></div>
          ) : (
            <div className="flex flex-wrap gap-6 justify-center">
              <BudgetGauge
                used={dailySpent}
                total={budget?.per_tenant_daily_usd ?? 500}
                label="Daily budget"
              />
              <BudgetGauge
                used={dailySpent / Math.max(costSummary?.total_cost_usd ?? 1, 1) * (budget?.per_goal_usd ?? 10)}
                total={budget?.per_goal_usd ?? 10}
                label="Per-goal avg"
              />
            </div>
          )}
        </div>

        {/* Right: Config form */}
        <div className="bg-card border border-border rounded-xl p-4 space-y-4">
          <h3 className="font-medium text-sm flex items-center gap-2">
            <DollarSign className="h-4 w-4" /> Budget Limits
          </h3>
          {budgetLoading ? (
            <div className="flex justify-center py-8"><Loader2 className="h-5 w-5 animate-spin" /></div>
          ) : (
            <>
              <div>
                <label htmlFor="per-goal-usd" className="block text-sm font-medium mb-1">
                  Per-goal limit (USD)
                </label>
                <input
                  id="per-goal-usd"
                  type="number"
                  min={0.01}
                  step={0.5}
                  value={current?.per_goal_usd ?? 10}
                  onChange={(e) =>
                    setDraft((prev) => ({
                      ...(prev ?? budget ?? { tenant_id: '', per_goal_usd: 10, per_tenant_daily_usd: 500 }),
                      per_goal_usd: Number(e.target.value),
                    }))
                  }
                  className="w-full px-3 py-2 border border-border rounded-md text-sm bg-background"
                />
                <p className="text-xs text-muted-foreground mt-1">
                  Maximum cost allowed for a single goal execution
                </p>
              </div>
              <div>
                <label htmlFor="per-tenant-daily-usd" className="block text-sm font-medium mb-1">
                  Daily tenant limit (USD)
                </label>
                <input
                  id="per-tenant-daily-usd"
                  type="number"
                  min={1}
                  step={10}
                  value={current?.per_tenant_daily_usd ?? 500}
                  onChange={(e) =>
                    setDraft((prev) => ({
                      ...(prev ?? budget ?? { tenant_id: '', per_goal_usd: 10, per_tenant_daily_usd: 500 }),
                      per_tenant_daily_usd: Number(e.target.value),
                    }))
                  }
                  className="w-full px-3 py-2 border border-border rounded-md text-sm bg-background"
                />
                <p className="text-xs text-muted-foreground mt-1">
                  Aggregate daily spend cap for all agents in this tenant
                </p>
              </div>
              {isDirty && (
                <button
                  data-testid="save-budget-btn"
                  onClick={() => current && saveMutation.mutate(current)}
                  disabled={saveMutation.isPending}
                  className="flex items-center gap-1.5 px-4 py-2 bg-primary text-primary-foreground rounded-md text-sm disabled:opacity-50"
                >
                  {saveMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
                  Save Budget Limits
                </button>
              )}
            </>
          )}
        </div>
      </div>

      {/* Cost Anomalies */}
      {anomalies.length > 0 && (
        <div className="bg-card border border-border rounded-xl overflow-hidden">
          <div className="px-4 py-3 border-b border-border bg-muted/40 flex items-center gap-2">
            <AlertTriangle className="h-4 w-4 text-amber-500" />
            <span className="font-medium text-sm">Cost Anomalies</span>
            <span className="text-xs text-muted-foreground">({anomalies.length})</span>
          </div>
          <div className="divide-y divide-border">
            {anomalies.map((a) => (
              <div key={a.id} className="px-4 py-3 flex items-center gap-3 text-sm">
                <span className={`px-2 py-0.5 rounded text-xs font-medium ${a.severity === 'high' ? 'bg-red-100 text-red-700' : a.severity === 'medium' ? 'bg-amber-100 text-amber-700' : 'bg-gray-100 text-gray-600'}`}>
                  {a.severity}
                </span>
                <div className="flex-1">
                  <p className="font-medium">{a.type}</p>
                  <p className="text-xs text-muted-foreground">{a.message}</p>
                </div>
                <span className="text-sm font-mono text-red-600">+{fmtUsd(a.cost_delta_usd)}</span>
                <span className="text-xs text-muted-foreground whitespace-nowrap">
                  {new Date(a.detected_at).toLocaleTimeString()}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Quick links */}
      <div className="flex flex-wrap gap-3 text-sm">
        <Link
          to="/observability/cost"
          className="flex items-center gap-1.5 text-blue-500 hover:underline"
        >
          <ChevronRight className="h-3.5 w-3.5" /> Full Cost Dashboard
        </Link>
        <Link
          to="/settings/budgets"
          className="flex items-center gap-1.5 text-blue-500 hover:underline"
        >
          <ChevronRight className="h-3.5 w-3.5" /> Advanced Budget Manager
        </Link>
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════════
// MAIN PAGE
// ═══════════════════════════════════════════════════════════════════════════════

export function GovernancePage() {
  const [activeTab, setActiveTab] = useState<GovTab>('policies');
  const { tenantId } = useAuthStore();

  const { data: approvals = [] } = useQuery({
    queryKey: ['governance-approvals'],
    queryFn: () => governanceApi.listApprovals(),
    refetchInterval: 15_000,
  });
  const pendingCount = approvals.filter((a) => a.status === 'pending').length;

  const tabs: Array<{ id: GovTab; label: string; icon: React.ReactNode; badge?: number }> = [
    { id: 'policies', label: 'Policies', icon: <Shield className="h-4 w-4" /> },
    { id: 'approvals', label: 'Approvals', icon: <CheckCircle className="h-4 w-4" />, badge: pendingCount || undefined },
    { id: 'audit', label: 'Audit', icon: <Activity className="h-4 w-4" /> },
    { id: 'budget', label: 'Budget', icon: <DollarSign className="h-4 w-4" /> },
  ];

  return (
    <div className="space-y-5">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold flex items-center gap-2">
          <Shield className="h-6 w-6 text-blue-500" /> Governance
        </h1>
        <p className="text-muted-foreground text-sm mt-1">
          Policies · HITL approvals · Immutable audit · Budget enforcement
        </p>
      </div>

      {/* Emergency stop */}
      <EmergencyStopBanner />

      {/* Tabs */}
      <div className="bg-card border border-border rounded-xl overflow-hidden">
        <div className="flex border-b border-border overflow-x-auto">
          {tabs.map((t) => (
            <button
              key={t.id}
              data-testid={`tab-${t.id}`}
              onClick={() => setActiveTab(t.id)}
              className={`flex items-center gap-2 px-5 py-3 text-sm font-medium border-b-2 transition-colors whitespace-nowrap ${
                activeTab === t.id
                  ? 'border-primary text-primary'
                  : 'border-transparent text-muted-foreground hover:text-foreground'
              }`}
            >
              {t.icon}
              {t.label}
              {t.badge !== undefined && t.badge > 0 && (
                <span className="bg-orange-100 text-orange-700 text-xs px-1.5 py-0.5 rounded-full min-w-[20px] text-center">
                  {t.badge}
                </span>
              )}
            </button>
          ))}
        </div>
        <div className="p-5">
          {activeTab === 'policies' && <PoliciesTab tenantId={tenantId ?? ''} />}
          {activeTab === 'approvals' && <ApprovalsTab tenantId={tenantId ?? ''} />}
          {activeTab === 'audit' && <AuditTab />}
          {activeTab === 'budget' && <BudgetTab />}
        </div>
      </div>
    </div>
  );
}
