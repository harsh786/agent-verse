/**
 * GuardrailCenterPage — World-Class Guardrail Management
 *
 * 4 tabs:
 *   Dashboard   — live stats, severity distribution, top categories
 *   Rules       — CRUD with enable/disable toggle, edit, domain templates
 *   Violations  — real-time violation log with severity filter
 *   Test Playground — live text test with risk gauge
 */
import { useState } from "react";
import type { JSX, CSSProperties } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Shield, Plus, Trash2, Play, AlertCircle, AlertTriangle,
  BarChart2, CheckCircle, XCircle, Edit2, RefreshCw, Zap,
  ToggleLeft, ToggleRight, Filter, Activity,
} from "lucide-react";
import { guardrailsApi } from "@/lib/api/client";
import type {
  GuardrailConfig, CreateGuardrailRequest, GuardrailTestResult,
  GuardrailViolation, GuardrailStats,
} from "@/lib/api/client";
import { EmptyState } from "@/components/ui/EmptyState";
import { ConfirmModal } from "@/components/ui/ConfirmModal";
import { Skeleton } from "@/components/ui/Skeleton";
import { toast } from "@/stores/toast";

// ── Constants ─────────────────────────────────────────────────────────────────

const LAYERS = ["goal", "plan", "step", "tool_args", "tool_output", "final"] as const;

const SEVERITY_STYLES: Record<GuardrailConfig["severity"], string> = {
  critical: "bg-red-100 text-red-700 border-red-200",
  high:     "bg-orange-100 text-orange-700 border-orange-200",
  medium:   "bg-yellow-100 text-yellow-700 border-yellow-200",
  low:      "bg-blue-100 text-blue-700 border-blue-200",
};

const RULE_TYPES = [
  { value: "keyword_block",  label: "Keyword Block",   description: "Block specific keywords" },
  { value: "regex_match",    label: "Regex Match",     description: "Block matching patterns" },
  { value: "pii_detection",  label: "PII Detection",   description: "Detect/redact PII data" },
  { value: "length_limit",   label: "Length Limit",    description: "Enforce max length" },
  { value: "toxicity",       label: "Toxicity Filter", description: "Block harmful content" },
  { value: "tool_allowlist", label: "Tool Allowlist",  description: "Restrict tool access" },
] as const;

const DOMAIN_TEMPLATES: Array<{ name: string; color: string; rules: CreateGuardrailRequest[] }> = [
  {
    name: "HIPAA", color: "bg-blue-50 text-blue-700 border-blue-200",
    rules: [
      { name: "HIPAA PII Guard", rule_type: "pii_detection", severity: "critical", layers: ["goal", "tool_args", "tool_output", "final"], config: { phi_mode: true } },
      { name: "HIPAA Keyword Block", rule_type: "keyword_block", severity: "high", layers: ["goal"], config: { keywords: ["SSN", "social security", "medical record", "PHI"] } },
    ],
  },
  {
    name: "GDPR", color: "bg-green-50 text-green-700 border-green-200",
    rules: [
      { name: "GDPR Personal Data", rule_type: "pii_detection", severity: "high", layers: ["goal", "final"], config: { gdpr_mode: true } },
      { name: "GDPR Consent Check", rule_type: "keyword_block", severity: "medium", layers: ["goal"], config: { keywords: ["personal data", "GDPR"] } },
    ],
  },
  {
    name: "SOC2", color: "bg-purple-50 text-purple-700 border-purple-200",
    rules: [
      { name: "SOC2 Credential Guard", rule_type: "regex_match", severity: "critical", layers: ["tool_args", "tool_output"], config: { pattern: "(password|secret|key|token)\\s*[:=]\\s*[^\\s]{8,}" } },
      { name: "SOC2 Length Limit", rule_type: "length_limit", severity: "low", layers: ["goal"], config: { max_length: 10000 } },
    ],
  },
];

// ── Risk Gauge ────────────────────────────────────────────────────────────────

function RiskGauge({ score }: { score: number }): JSX.Element {
  const r = 45;
  const circ = 2 * Math.PI * r;
  const dash = circ - (score / 100) * circ;
  const color = score > 70 ? "#ef4444" : score > 40 ? "#f59e0b" : "#22c55e";
  return (
    <div className="flex flex-col items-center gap-2">
      <svg width="120" height="120" viewBox="0 0 100 100" aria-label={`Risk score: ${score}`}>
        <circle cx="50" cy="50" r={r} fill="none" stroke="#e5e7eb" strokeWidth="8" />
        <circle cx="50" cy="50" r={r} fill="none" stroke={color} strokeWidth="8"
          strokeLinecap="round" strokeDasharray={`${circ - dash} ${dash}`}
          style={{ transition: "stroke-dasharray 0.6s ease" } as CSSProperties}
          transform="rotate(-90 50 50)" />
        <text x="50" y="50" textAnchor="middle" dy="0.35em" fontSize="18" fontWeight="bold" fill={color}>{score}</text>
        <text x="50" y="64" textAnchor="middle" fontSize="9" fill="#9ca3af">/ 100</text>
      </svg>
      <p className={`text-sm font-semibold ${score > 70 ? "text-red-600" : score > 40 ? "text-yellow-600" : "text-green-600"}`}>
        {score > 70 ? "High Risk" : score > 40 ? "Medium Risk" : "Low Risk"}
      </p>
    </div>
  );
}

// ── Bar Chart ─────────────────────────────────────────────────────────────────

function MiniBar({ label, value, max, color }: { label: string; value: number; max: number; color: string }) {
  const pct = max > 0 ? (value / max) * 100 : 0;
  return (
    <div className="flex items-center gap-2 text-xs">
      <span className="w-24 text-muted-foreground shrink-0 capitalize">{label}</span>
      <div className="flex-1 bg-muted rounded h-3 overflow-hidden">
        <div className={`h-full rounded transition-all ${color}`} style={{ width: `${Math.max(2, pct)}%` }} />
      </div>
      <span className="w-8 text-right font-mono tabular-nums">{value}</span>
    </div>
  );
}

// ── Create/Edit Modal ─────────────────────────────────────────────────────────

function RuleModal({
  open, onClose, onSaved, initial,
}: {
  open: boolean;
  onClose: () => void;
  onSaved: () => void;
  initial?: GuardrailConfig;
}): JSX.Element | null {
  const qc = useQueryClient();
  const [name, setName] = useState(initial?.name ?? "");
  const [ruleType, setRuleType] = useState(initial?.rule_type ?? "pii_detection");
  const [severity, setSeverity] = useState<GuardrailConfig["severity"]>(initial?.severity ?? "high");
  const [layers, setLayers] = useState<string[]>(initial?.layers ?? ["goal"]);
  const [configText, setConfigText] = useState(initial ? JSON.stringify(initial.config, null, 2) : "{}");
  const [configError, setConfigError] = useState("");

  const saveMutation = useMutation({
    mutationFn: () => {
      let config: Record<string, unknown> = {};
      try { config = JSON.parse(configText); } catch { throw new Error("Config must be valid JSON"); }
      const body: CreateGuardrailRequest = { name, rule_type: ruleType, severity, layers, config };
      return initial ? guardrailsApi.update(initial.id, body) : guardrailsApi.create(body);
    },
    onSuccess: () => {
      toast({ kind: "success", message: initial ? "Guardrail updated." : "Guardrail created." });
      void qc.invalidateQueries({ queryKey: ["guardrails"] });
      onSaved();
      onClose();
    },
    onError: (e) => toast({ kind: "error", message: String(e) }),
  });

  const toggleLayer = (l: string) =>
    setLayers((prev) => prev.includes(l) ? prev.filter((x) => x !== l) : [...prev, l]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/50" onClick={onClose} aria-hidden="true" />
      <div className="relative bg-card border border-border rounded-xl shadow-2xl w-full max-w-lg p-6 space-y-4 max-h-[90vh] overflow-y-auto">
        <h2 className="font-semibold text-lg">{initial ? "Edit Guardrail" : "New Guardrail"}</h2>
        <div>
          <label className="block text-xs text-muted-foreground mb-1">Name *</label>
          <input value={name} onChange={(e) => setName(e.target.value)}
            placeholder="Block PII in outputs" className="w-full px-3 py-2 border border-border rounded-md text-sm bg-background" />
        </div>
        <div>
          <p className="text-xs text-muted-foreground mb-2">Rule Type</p>
          <div className="grid grid-cols-2 gap-2">
            {RULE_TYPES.map((rt) => (
              <button key={rt.value} onClick={() => setRuleType(rt.value)}
                className={`px-3 py-2 rounded-lg border text-xs text-left transition-colors ${ruleType === rt.value ? "border-primary bg-primary/10 text-primary" : "border-border hover:bg-muted"}`}>
                <div className="font-medium">{rt.label}</div>
                <div className="text-muted-foreground mt-0.5">{rt.description}</div>
              </button>
            ))}
          </div>
        </div>
        <div>
          <p className="text-xs text-muted-foreground mb-2">Severity</p>
          <div className="flex gap-2">
            {(["critical", "high", "medium", "low"] as const).map((s) => (
              <button key={s} onClick={() => setSeverity(s)}
                className={`px-3 py-1.5 rounded-md border text-xs capitalize transition-colors ${severity === s ? SEVERITY_STYLES[s] : "border-border hover:bg-muted"}`}>
                {s}
              </button>
            ))}
          </div>
        </div>
        <div>
          <p className="text-xs text-muted-foreground mb-2">Layers</p>
          <div className="flex flex-wrap gap-2">
            {LAYERS.map((l) => (
              <label key={l} className="flex items-center gap-1.5 text-xs cursor-pointer">
                <input type="checkbox" checked={layers.includes(l)} onChange={() => toggleLayer(l)} className="rounded" />
                <span className="font-mono">{l}</span>
              </label>
            ))}
          </div>
        </div>
        <div>
          <label className="block text-xs text-muted-foreground mb-1">Config (JSON)</label>
          <textarea value={configText} onChange={(e) => { setConfigText(e.target.value); setConfigError(""); }}
            rows={4} className="w-full px-3 py-2 border border-border rounded-md text-xs font-mono bg-background resize-none" />
          {configError && <p className="text-destructive text-xs mt-1">{configError}</p>}
        </div>
        <div className="flex justify-end gap-3">
          <button onClick={onClose} className="px-4 py-2 border border-border rounded-md text-sm">Cancel</button>
          <button onClick={() => saveMutation.mutate()} disabled={!name || saveMutation.isPending}
            className="px-4 py-2 bg-primary text-primary-foreground rounded-md text-sm disabled:opacity-50">
            {saveMutation.isPending ? "Saving…" : initial ? "Save Changes" : "Create Rule"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Dashboard Tab ─────────────────────────────────────────────────────────────

function DashboardTab(): JSX.Element {
  const { data: stats, isLoading } = useQuery<GuardrailStats>({
    queryKey: ["guardrail-stats"],
    queryFn: () => guardrailsApi.getStats(),
    staleTime: 30_000,
  });

  if (isLoading) return <div className="space-y-3">{Array.from({length: 4}).map((_, i) => <Skeleton key={i} className="h-20" />)}</div>;
  if (!stats) return <div className="text-muted-foreground text-sm text-center py-8">No stats available.</div>;

  const sevMax = Math.max(...Object.values(stats.by_severity), 1);
  const layMax = Math.max(...Object.values(stats.by_layer), 1);
  const sevColors: Record<string, string> = { critical: "bg-red-500", high: "bg-orange-500", medium: "bg-yellow-500", low: "bg-blue-500" };

  return (
    <div data-testid="stats-cards" className="space-y-5">
      {/* KPI row */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {[
          { label: "Last 24h", value: stats.total_24h, color: "text-amber-600" },
          { label: "All Time", value: stats.total_all, color: "text-foreground" },
          { label: "Risk P95", value: `${Math.round(stats.risk_score_p95 * 100)}%`, color: stats.risk_score_p95 > 0.7 ? "text-red-600" : "text-green-600" },
          { label: "Top Category", value: stats.top_categories[0]?.category ?? "—", color: "text-violet-600" },
        ].map((s) => (
          <div key={s.label} className="bg-card border border-border rounded-xl p-3">
            <p className="text-xs text-muted-foreground">{s.label}</p>
            <p className={`text-xl font-bold mt-0.5 truncate ${s.color}`}>{s.value}</p>
          </div>
        ))}
      </div>
      {/* Charts */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
        <div className="bg-card border border-border rounded-xl p-4 space-y-3">
          <h3 className="text-sm font-medium flex items-center gap-2"><BarChart2 className="h-4 w-4" /> By Severity</h3>
          {Object.entries(stats.by_severity).map(([k, v]) => (
            <MiniBar key={k} label={k} value={v} max={sevMax} color={sevColors[k] ?? "bg-gray-400"} />
          ))}
        </div>
        <div className="bg-card border border-border rounded-xl p-4 space-y-3">
          <h3 className="text-sm font-medium flex items-center gap-2"><Activity className="h-4 w-4" /> By Layer</h3>
          {Object.entries(stats.by_layer).map(([k, v]) => (
            <MiniBar key={k} label={k} value={v} max={layMax} color="bg-primary/60" />
          ))}
        </div>
      </div>
      {/* Top categories */}
      {stats.top_categories.length > 0 && (
        <div className="bg-card border border-border rounded-xl p-4">
          <h3 className="text-sm font-medium mb-3">Top Violation Categories</h3>
          <div className="space-y-2">
            {stats.top_categories.slice(0, 5).map((c, i) => (
              <div key={c.category} className="flex items-center justify-between text-sm">
                <span className="flex items-center gap-2">
                  <span className="text-xs text-muted-foreground font-mono w-4">#{i+1}</span>
                  <span className="capitalize">{c.category.replace(/_/g, " ")}</span>
                </span>
                <span className="font-mono text-xs bg-muted px-2 py-0.5 rounded">{c.count}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Rules Tab ─────────────────────────────────────────────────────────────────

function RulesTab(): JSX.Element {
  const qc = useQueryClient();
  const [showCreate, setShowCreate] = useState(false);
  const [editRule, setEditRule] = useState<GuardrailConfig | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
  const [severityFilter, setSeverityFilter] = useState("all");

  const { data: rules = [], isLoading, error } = useQuery<GuardrailConfig[]>({
    queryKey: ["guardrails"],
    queryFn: () => guardrailsApi.list(),
    staleTime: 30_000,
  });

  const toggleMutation = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      guardrailsApi.update(id, { enabled } as any),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["guardrails"] }),
    onError: (e) => toast({ kind: "error", message: String(e) }),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => guardrailsApi.delete(id),
    onSuccess: () => {
      toast({ kind: "success", message: "Guardrail deleted." });
      void qc.invalidateQueries({ queryKey: ["guardrails"] });
      setDeleteTarget(null);
    },
  });

  const applyTemplateMutation = useMutation({
    mutationFn: async (rules: CreateGuardrailRequest[]) => {
      for (const r of rules) await guardrailsApi.create(r);
    },
    onSuccess: (_, rules) => {
      toast({ kind: "success", message: `${(rules as CreateGuardrailRequest[]).length} rules created.` });
      void qc.invalidateQueries({ queryKey: ["guardrails"] });
      void qc.invalidateQueries({ queryKey: ["guardrail-stats"] });
    },
    onError: (e) => toast({ kind: "error", message: String(e) }),
  });

  const filtered = rules.filter((r) => severityFilter === "all" || r.severity === severityFilter);

  if (isLoading) return <div className="space-y-2">{Array.from({length: 4}).map((_, i) => <Skeleton key={i} className="h-14" />)}</div>;

  return (
    <div className="space-y-4">
      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex gap-1">
          {["all", "critical", "high", "medium", "low"].map((s) => (
            <button key={s} onClick={() => setSeverityFilter(s)}
              className={`px-2.5 py-1 text-xs rounded-full capitalize transition-colors ${severityFilter === s ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground hover:bg-muted/80"}`}>
              {s}
            </button>
          ))}
        </div>
        <div className="ml-auto flex gap-2">
          <button data-testid="new-rule-btn" onClick={() => setShowCreate(true)}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-primary text-primary-foreground rounded-md text-sm">
            <Plus className="h-4 w-4" /> New Rule
          </button>
        </div>
      </div>

      {/* Domain templates (empty state enhancement) */}
      {rules.length === 0 && (
        <div className="bg-muted/30 border border-border rounded-xl p-4">
          <p className="text-sm font-medium mb-3">Quick-start with a compliance template:</p>
          <div className="flex flex-wrap gap-2">
            {DOMAIN_TEMPLATES.map((t) => (
              <button key={t.name} onClick={() => applyTemplateMutation.mutate(t.rules)}
                disabled={applyTemplateMutation.isPending}
                className={`px-3 py-1.5 rounded-md border text-xs font-medium transition-colors ${t.color}`}>
                {t.name} ({t.rules.length} rules)
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Rules list */}
      {filtered.length === 0 && rules.length > 0 ? (
        <EmptyState title="No rules match filter" description="Change the severity filter." />
      ) : filtered.length === 0 ? (
        <EmptyState title="No guardrails configured" description="Create a rule to start protecting your agents." />
      ) : (
        <div className="border border-border rounded-xl overflow-hidden">
          {filtered.map((r, i) => (
            <div key={r.id} data-testid={`guardrail-item-${r.id}`}
              className={`flex items-center gap-3 px-4 py-3 text-sm ${i > 0 ? "border-t border-border" : ""} hover:bg-muted/20`}>
              <button data-testid={`toggle-${r.id}`}
                onClick={() => toggleMutation.mutate({ id: r.id, enabled: !r.enabled })}
                className={`shrink-0 ${r.enabled ? "text-green-500" : "text-muted-foreground"}`}
                title={r.enabled ? "Enabled — click to disable" : "Disabled — click to enable"}>
                {r.enabled ? <ToggleRight className="h-5 w-5" /> : <ToggleLeft className="h-5 w-5" />}
              </button>
              <div className="flex-1 min-w-0">
                <p className="font-medium truncate">{r.name}</p>
                <div className="flex flex-wrap gap-1.5 mt-0.5">
                  <span className="text-xs font-mono text-muted-foreground">{r.rule_type}</span>
                  {r.layers.slice(0, 3).map((l) => (
                    <span key={l} className="px-1.5 py-0.5 bg-muted rounded text-xs font-mono">{l}</span>
                  ))}
                  {r.layers.length > 3 && <span className="text-xs text-muted-foreground">+{r.layers.length - 3}</span>}
                </div>
              </div>
              <span className={`px-2 py-0.5 rounded border text-xs font-medium shrink-0 ${SEVERITY_STYLES[r.severity]}`}>
                {r.severity}
              </span>
              <div className="flex gap-1 shrink-0">
                <button onClick={() => setEditRule(r)} className="p-1.5 text-muted-foreground hover:text-foreground rounded" title="Edit">
                  <Edit2 className="h-3.5 w-3.5" />
                </button>
                <button data-testid={`delete-${r.id}`} onClick={() => setDeleteTarget(r.id)}
                  className="p-1.5 text-muted-foreground hover:text-red-500 rounded" title="Delete">
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      <RuleModal open={showCreate || editRule !== null} onClose={() => { setShowCreate(false); setEditRule(null); }}
        onSaved={() => { setShowCreate(false); setEditRule(null); }} initial={editRule ?? undefined} />

      <ConfirmModal open={!!deleteTarget} title="Delete guardrail?" description="This rule will no longer protect your agents."
        confirmLabel="Delete" variant="danger" isLoading={deleteMutation.isPending}
        onConfirm={() => deleteTarget && deleteMutation.mutate(deleteTarget)}
        onCancel={() => setDeleteTarget(null)} />
    </div>
  );
}

// ── Violations Tab ────────────────────────────────────────────────────────────

function ViolationsTab(): JSX.Element {
  const [sevFilter, setSevFilter] = useState("all");
  const { data: violations = [], isLoading, isFetching, refetch } = useQuery<GuardrailViolation[]>({
    queryKey: ["guardrail-violations", sevFilter],
    queryFn: () => guardrailsApi.getViolations({
      limit: 100,
      severity: sevFilter !== "all" ? sevFilter : undefined,
    }),
    refetchInterval: 30_000,
    staleTime: 15_000,
  });

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3 flex-wrap">
        <div className="flex gap-1">
          {["all", "critical", "high", "medium", "low"].map((s) => (
            <button key={s} onClick={() => setSevFilter(s)}
              className={`px-2.5 py-1 text-xs rounded-full capitalize ${sevFilter === s ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground hover:bg-muted/80"}`}>
              {s}
            </button>
          ))}
        </div>
        <button onClick={() => void refetch()} className="ml-auto text-xs text-muted-foreground flex items-center gap-1 hover:text-foreground">
          <RefreshCw className={`h-3.5 w-3.5 ${isFetching ? "animate-spin" : ""}`} /> Refresh
        </button>
      </div>

      {isLoading ? (
        <div className="space-y-2">{Array.from({length: 5}).map((_, i) => <Skeleton key={i} className="h-14" />)}</div>
      ) : violations.length === 0 ? (
        <EmptyState title="No violations" description="Guardrails are running cleanly." />
      ) : (
        <div data-testid="violations-table" className="border border-border rounded-xl overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-muted/40 border-b border-border">
              <tr>
                <th className="px-3 py-2.5 text-left text-xs font-medium text-muted-foreground">Time</th>
                <th className="px-3 py-2.5 text-left text-xs font-medium text-muted-foreground">Guardrail</th>
                <th className="px-3 py-2.5 text-left text-xs font-medium text-muted-foreground">Type</th>
                <th className="px-3 py-2.5 text-left text-xs font-medium text-muted-foreground">Severity</th>
                <th className="px-3 py-2.5 text-left text-xs font-medium text-muted-foreground">Message</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {violations.map((v) => (
                <tr key={v.id} className="hover:bg-muted/20">
                  <td className="px-3 py-2.5 text-xs font-mono text-muted-foreground whitespace-nowrap">{v.id.slice(0, 8)}</td>
                  <td className="px-3 py-2.5 font-medium text-xs">{v.guardrail_name}</td>
                  <td className="px-3 py-2.5 text-xs font-mono">{v.type}</td>
                  <td className="px-3 py-2.5">
                    <span className={`px-1.5 py-0.5 rounded text-xs font-medium border ${SEVERITY_STYLES[v.severity as GuardrailConfig["severity"]] ?? "bg-gray-100 text-gray-600"}`}>
                      {v.severity}
                    </span>
                  </td>
                  <td className="px-3 py-2.5 text-xs text-muted-foreground max-w-xs truncate">{v.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ── Test Playground Tab ───────────────────────────────────────────────────────

function TestPlaygroundTab(): JSX.Element {
  const [text, setText] = useState("");
  const [layer, setLayer] = useState("goal");
  const [result, setResult] = useState<GuardrailTestResult | null>(null);

  const testMutation = useMutation({
    mutationFn: () => guardrailsApi.test({ text, layer }),
    onSuccess: (r) => setResult(r),
    onError: (e) => toast({ kind: "error", message: String(e) }),
  });

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        {/* Input */}
        <div className="space-y-3">
          <div>
            <label className="block text-sm font-medium mb-1">Layer</label>
            <select value={layer} onChange={(e) => setLayer(e.target.value)}
              className="w-full px-3 py-2 border border-border rounded-md text-sm bg-background">
              {LAYERS.map((l) => <option key={l} value={l}>{l}</option>)}
            </select>
          </div>
          <div>
            <label className="block text-sm font-medium mb-1">Test Input</label>
            <textarea data-testid="test-input" value={text} onChange={(e) => setText(e.target.value)}
              rows={8} placeholder="Enter text to test against guardrails…"
              className="w-full px-3 py-2 border border-border rounded-md text-sm bg-background resize-none font-mono" />
          </div>
          <button data-testid="run-test-btn" onClick={() => testMutation.mutate()}
            disabled={!text.trim() || testMutation.isPending}
            className="w-full flex items-center justify-center gap-2 py-2 bg-primary text-primary-foreground rounded-md text-sm disabled:opacity-50">
            {testMutation.isPending ? <RefreshCw className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
            {testMutation.isPending ? "Testing…" : "Run Test"}
          </button>
        </div>

        {/* Results */}
        <div className="space-y-4">
          {result ? (
            <>
              <div className="flex items-center gap-4">
                <RiskGauge score={Math.round(result.risk_score * 100)} />
                <div>
                  <div className={`flex items-center gap-2 text-lg font-bold ${result.passed ? "text-green-600" : "text-red-600"}`}>
                    {result.passed ? <CheckCircle className="h-5 w-5" /> : <XCircle className="h-5 w-5" />}
                    {result.passed ? "Passed" : "Blocked"}
                  </div>
                  <p className="text-sm text-muted-foreground mt-1">
                    {result.violations.length} violation{result.violations.length !== 1 ? "s" : ""} detected
                  </p>
                </div>
              </div>
              {result.violations.length > 0 && (
                <div className="space-y-2">
                  <h3 className="text-sm font-medium">Violations</h3>
                  {result.violations.map((v, i) => (
                    <div key={i} className={`border rounded-lg p-3 ${SEVERITY_STYLES[v.severity as GuardrailConfig["severity"]] ?? "border-border"}`}>
                      <div className="flex items-center gap-2 text-xs font-medium">
                        <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
                        <span className="capitalize">{v.type.replace(/_/g, " ")}</span>
                        <span className="ml-auto font-mono">{v.severity}</span>
                      </div>
                      <p className="text-xs mt-1 text-muted-foreground">{v.message}</p>
                    </div>
                  ))}
                </div>
              )}
            </>
          ) : (
            <div className="flex flex-col items-center justify-center h-48 text-muted-foreground gap-2 border-2 border-dashed border-border rounded-xl">
              <Shield className="h-8 w-8 opacity-30" />
              <p className="text-sm">Test results will appear here</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Main Page ─────────────────────────────────────────────────────────────────

type PageTab = "dashboard" | "rules" | "violations" | "test";

export function GuardrailCenterPage(): JSX.Element {
  const [activeTab, setActiveTab] = useState<PageTab>("rules");

  const tabs: Array<{ id: PageTab; label: string; icon: JSX.Element }> = [
    { id: "dashboard", label: "Dashboard", icon: <BarChart2 className="h-4 w-4" /> },
    { id: "rules",     label: "Rules",     icon: <Shield className="h-4 w-4" /> },
    { id: "violations",label: "Violations",icon: <AlertCircle className="h-4 w-4" /> },
    { id: "test",      label: "Test Playground", icon: <Play className="h-4 w-4" /> },
  ];

  return (
    <div className="p-4 md:p-6 max-w-5xl mx-auto space-y-5">
      <div>
        <h1 className="text-2xl font-bold flex items-center gap-2">
          <Shield className="h-6 w-6 text-blue-500" /> Guardrail Center
        </h1>
        <p className="text-muted-foreground text-sm mt-1">
          Define content safety rules, monitor violations, and test guardrails in real time.
        </p>
      </div>

      <div className="bg-card border border-border rounded-xl overflow-hidden">
        <div className="flex border-b border-border overflow-x-auto">
          {tabs.map((t) => (
            <button key={t.id} data-testid={`tab-${t.id}`} onClick={() => setActiveTab(t.id)}
              className={`flex items-center gap-2 px-5 py-3 text-sm font-medium border-b-2 whitespace-nowrap transition-colors ${
                activeTab === t.id ? "border-primary text-primary" : "border-transparent text-muted-foreground hover:text-foreground"
              }`}>
              {t.icon} {t.label}
            </button>
          ))}
        </div>
        <div className="p-5">
          {activeTab === "dashboard"  && <DashboardTab />}
          {activeTab === "rules"      && <RulesTab />}
          {activeTab === "violations" && <ViolationsTab />}
          {activeTab === "test"       && <TestPlaygroundTab />}
        </div>
      </div>
    </div>
  );
}

export default GuardrailCenterPage;
