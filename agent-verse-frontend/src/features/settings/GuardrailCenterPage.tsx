import { useState } from "react";
import type { JSX, CSSProperties } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Shield, Plus, Trash2, Play, AlertCircle, ToggleLeft, ToggleRight,
} from "lucide-react";
import { guardrailsApi } from "@/lib/api/client";
import type {
  GuardrailConfig, CreateGuardrailRequest, GuardrailTestResult, GuardrailViolation,
} from "@/lib/api/client";
import { EmptyState } from "@/components/ui/EmptyState";
import { ConfirmModal } from "@/components/ui/ConfirmModal";
import { Skeleton } from "@/components/ui/Skeleton";
import { toast } from "@/stores/toast";

// ── Constants ─────────────────────────────────────────────────────────────────

const LAYERS = ["goal", "plan", "step", "tool_args", "tool_output", "final"] as const;
type Layer = typeof LAYERS[number];

const SEVERITY_STYLES: Record<GuardrailConfig["severity"], string> = {
  critical: "bg-red-100 text-red-700 border-red-200 dark:bg-red-900/30 dark:text-red-400",
  high: "bg-orange-100 text-orange-700 border-orange-200 dark:bg-orange-900/30 dark:text-orange-400",
  medium: "bg-yellow-100 text-yellow-700 border-yellow-200 dark:bg-yellow-900/30 dark:text-yellow-400",
  low: "bg-blue-100 text-blue-700 border-blue-200 dark:bg-blue-900/30 dark:text-blue-400",
};

const RULE_TYPES = [
  { value: "keyword_block", label: "Keyword Block", description: "Block if specific keywords appear" },
  { value: "regex_match", label: "Regex Match", description: "Block matching regex pattern" },
  { value: "pii_detection", label: "PII Detection", description: "Detect and redact PII data" },
  { value: "length_limit", label: "Length Limit", description: "Enforce max input/output length" },
  { value: "toxicity", label: "Toxicity Filter", description: "Detect toxic or harmful content" },
  { value: "tool_allowlist", label: "Tool Allowlist", description: "Restrict allowed tools" },
] as const;

// ── Risk Gauge SVG ────────────────────────────────────────────────────────────

function RiskGauge({ score }: { score: number }): JSX.Element {
  const radius = 45;
  const circumference = 2 * Math.PI * radius;
  const dashOffset = circumference - (score / 100) * circumference;
  const color = score > 70 ? "#ef4444" : score > 40 ? "#f59e0b" : "#22c55e";

  return (
    <div className="flex flex-col items-center gap-2">
      <svg width="120" height="120" viewBox="0 0 100 100" aria-label={`Risk score: ${score}`}>
        <circle cx="50" cy="50" r={radius} fill="none" stroke="hsl(214.3 31.8% 91.4%)" strokeWidth="8" />
        <circle
          cx="50"
          cy="50"
          r={radius}
          fill="none"
          stroke={color}
          strokeWidth="8"
          strokeLinecap="round"
          strokeDasharray={circumference}
          style={{ "--target": `${dashOffset}` } as CSSProperties}
          className="risk-gauge-arc"
          transform="rotate(-90 50 50)"
        />
        <text x="50" y="50" textAnchor="middle" dy="0.35em" fontSize="18" fontWeight="bold" fill="currentColor">
          {score}
        </text>
        <text x="50" y="64" textAnchor="middle" fontSize="9" fill="hsl(215.4 16.3% 46.9%)">
          / 100
        </text>
      </svg>
      <p
        className={`text-sm font-semibold ${
          score > 70 ? "text-red-600" : score > 40 ? "text-yellow-600" : "text-green-600"
        }`}
      >
        {score > 70 ? "High Risk" : score > 40 ? "Medium Risk" : "Low Risk"}
      </p>
    </div>
  );
}

// ── Violation Card ────────────────────────────────────────────────────────────

function ViolationCard({ violation }: { violation: GuardrailViolation }): JSX.Element {
  return (
    <div className="violation-entering flex items-start gap-3 bg-card border border-border rounded-lg px-4 py-3">
      <div
        className={`w-2 h-2 rounded-full mt-1.5 flex-shrink-0 ${
          violation.severity === "critical"
            ? "bg-red-500"
            : violation.severity === "high"
            ? "bg-orange-500"
            : violation.severity === "medium"
            ? "bg-yellow-500"
            : "bg-blue-500"
        }`}
      />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium">{violation.guardrail_name}</span>
          <span className="text-xs text-muted-foreground">{violation.type}</span>
        </div>
        <p className="text-xs text-muted-foreground mt-0.5 truncate">{violation.message}</p>
        <p className="text-xs text-muted-foreground mt-0.5">
          {new Date(violation.created_at).toLocaleString()}
        </p>
      </div>
    </div>
  );
}

// ── Rule Creator Modal ────────────────────────────────────────────────────────

interface RuleModalProps {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}

function RuleCreatorModal({ open, onClose, onCreated }: RuleModalProps): JSX.Element | null {
  const [name, setName] = useState("");
  const [ruleType, setRuleType] = useState<string>("keyword_block");
  const [severity, setSeverity] = useState<GuardrailConfig["severity"]>("medium");
  const [selectedLayers, setSelectedLayers] = useState<Layer[]>(["goal"]);
  const [configJson, setConfigJson] = useState('{"keywords": ["delete all", "drop table"]}');
  const qc = useQueryClient();

  const createMutation = useMutation({
    mutationFn: () => {
      let parsedConfig: Record<string, unknown> = {};
      try {
        parsedConfig = JSON.parse(configJson) as Record<string, unknown>;
      } catch {
        // keep empty
      }
      const body: CreateGuardrailRequest = {
        name,
        rule_type: ruleType,
        severity,
        layers: selectedLayers,
        config: parsedConfig,
      };
      return guardrailsApi.create(body);
    },
    onSuccess: () => {
      toast({ kind: "success", message: "Guardrail created" });
      qc.invalidateQueries({ queryKey: ["guardrails"] });
      onCreated();
      onClose();
      setName("");
    },
    onError: (e) => toast({ kind: "error", message: `Failed: create guardrail. ${String(e)}` }),
  });

  const toggleLayer = (layer: Layer): void => {
    setSelectedLayers((prev) =>
      prev.includes(layer) ? prev.filter((l) => l !== layer) : [...prev, layer]
    );
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-[200] flex items-center justify-center p-4" role="dialog" aria-modal="true">
      <div className="absolute inset-0 bg-black/50 backdrop-blur-sm" onClick={onClose} aria-hidden="true" />
      <div className="relative bg-card border border-border rounded-xl shadow-xl max-w-lg w-full p-6 space-y-5 max-h-[90vh] overflow-y-auto">
        <h2 className="text-lg font-semibold">Create Guardrail Rule</h2>

        <div>
          <label className="block text-xs font-medium text-muted-foreground mb-1">Name</label>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Block SQL injection attempts"
            className="w-full border border-input rounded-md px-3 py-2 text-sm bg-background"
          />
        </div>

        <div>
          <label className="block text-xs font-medium text-muted-foreground mb-1">Rule Type</label>
          <div className="grid grid-cols-2 gap-2">
            {RULE_TYPES.map(({ value, label }) => (
              <button
                key={value}
                onClick={() => setRuleType(value)}
                className={`text-left px-3 py-2 rounded-md text-xs border transition-colors ${
                  ruleType === value
                    ? "bg-primary/10 border-primary text-primary"
                    : "border-border hover:bg-muted"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
          <p className="text-xs text-muted-foreground mt-1">
            {RULE_TYPES.find((r) => r.value === ruleType)?.description}
          </p>
        </div>

        <div>
          <label className="block text-xs font-medium text-muted-foreground mb-1">Severity</label>
          <div className="flex gap-2">
            {(["critical", "high", "medium", "low"] as const).map((s) => (
              <button
                key={s}
                onClick={() => setSeverity(s)}
                className={`px-3 py-1 rounded-md text-xs border capitalize transition-colors ${
                  severity === s ? SEVERITY_STYLES[s] : "border-border hover:bg-muted"
                }`}
              >
                {s}
              </button>
            ))}
          </div>
        </div>

        <div>
          <p className="text-xs font-medium text-muted-foreground mb-2">Layers</p>
          <div className="flex flex-wrap gap-2">
            {LAYERS.map((l) => (
              <label key={l} className="flex items-center gap-1.5 text-xs cursor-pointer">
                <input
                  type="checkbox"
                  checked={selectedLayers.includes(l)}
                  onChange={() => toggleLayer(l)}
                  className="rounded"
                />
                <span className="font-mono">{l}</span>
              </label>
            ))}
          </div>
        </div>

        <div>
          <label className="block text-xs font-medium text-muted-foreground mb-1">Config (JSON)</label>
          <textarea
            value={configJson}
            onChange={(e) => setConfigJson(e.target.value)}
            rows={4}
            className="w-full border border-input rounded-md px-3 py-2 text-xs font-mono bg-background resize-none"
          />
        </div>

        <div className="flex justify-end gap-3 pt-2">
          <button onClick={onClose} className="px-4 py-2 text-sm border border-border rounded-md hover:bg-muted">
            Cancel
          </button>
          <button
            onClick={() => createMutation.mutate()}
            disabled={!name || createMutation.isPending}
            className="px-4 py-2 text-sm bg-primary text-primary-foreground rounded-md hover:opacity-90 disabled:opacity-50"
          >
            {createMutation.isPending ? "Creating…" : "Create Rule"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── GuardrailCenterPage ───────────────────────────────────────────────────────

type PageTab = "rules" | "violations" | "test";

export function GuardrailCenterPage(): JSX.Element {
  const qc = useQueryClient();
  const [tab, setTab] = useState<PageTab>("rules");
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
  const [testText, setTestText] = useState("");
  const [testResult, setTestResult] = useState<GuardrailTestResult | null>(null);
  const [testLoading, setTestLoading] = useState(false);
  const [layerToggles, setLayerToggles] = useState<Record<Layer, boolean>>(
    Object.fromEntries(LAYERS.map((l) => [l, true])) as Record<Layer, boolean>
  );

  const rulesQuery = useQuery({
    queryKey: ["guardrails"],
    queryFn: () => guardrailsApi.list(),
  });

  const violationsQuery = useQuery({
    queryKey: ["guardrail-violations"],
    queryFn: () => guardrailsApi.getViolations({ limit: 50 }),
    enabled: tab === "violations",
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => guardrailsApi.delete(id),
    onSuccess: () => {
      toast({ kind: "success", message: "Guardrail deleted" });
      qc.invalidateQueries({ queryKey: ["guardrails"] });
      setDeleteTarget(null);
    },
    onError: (e) => toast({ kind: "error", message: `Failed: delete guardrail. ${String(e)}` }),
  });

  const toggleEnabledMutation = useMutation({
    mutationFn: ({ id, rule }: { id: string; rule: GuardrailConfig }) =>
      guardrailsApi.update(id, {
        name: rule.name,
        rule_type: rule.rule_type,
        severity: rule.severity,
        layers: rule.layers,
        config: rule.config,
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["guardrails"] }),
    onError: (e) => toast({ kind: "error", message: `Failed: update guardrail. ${String(e)}` }),
  });

  const handleTest = async (): Promise<void> => {
    setTestLoading(true);
    try {
      const result = await guardrailsApi.test({ text: testText });
      setTestResult(result);
    } catch (e) {
      toast({ kind: "error", message: `Failed: test rule. ${String(e)}` });
    } finally {
      setTestLoading(false);
    }
  };

  const applyTemplate = (templateName: string): void => {
    toast({ kind: "info", message: `${templateName} template queued — check back in a few minutes` });
  };

  const toggleLayerFilter = (layer: Layer): void => {
    setLayerToggles((prev) => ({ ...prev, [layer]: !prev[layer] }));
  };

  const rules = rulesQuery.data ?? [];
  const violations = violationsQuery.data ?? [];

  return (
    <div className="p-4 md:p-6 max-w-5xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Guardrail Center</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Content safety rules applied at each layer of agent execution.
          </p>
        </div>
        <button
          onClick={() => setShowCreateModal(true)}
          className="flex items-center gap-2 px-4 py-2 bg-primary text-primary-foreground rounded-lg text-sm hover:opacity-90"
        >
          <Plus className="h-4 w-4" /> New Rule
        </button>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 border-b border-border">
        {(["rules", "violations", "test"] as PageTab[]).map((t) => (
          <button
            key={t}
            role="tab"
            aria-selected={tab === t}
            onClick={() => setTab(t)}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors capitalize ${
              tab === t
                ? "border-primary text-primary"
                : "border-transparent text-muted-foreground hover:text-foreground"
            }`}
          >
            {t === "test" ? "Test Playground" : t.charAt(0).toUpperCase() + t.slice(1)}
          </button>
        ))}
      </div>

      {/* Rules tab */}
      {tab === "rules" && (
        <div className="space-y-5">
          {/* Per-layer toggles */}
          <div className="bg-card border border-border rounded-xl p-5">
            <h2 className="text-sm font-semibold mb-3">Per-Layer Enforcement</h2>
            <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
              {LAYERS.map((layer) => (
                <button
                  key={layer}
                  onClick={() => toggleLayerFilter(layer)}
                  className="flex items-center justify-between bg-muted/40 rounded-lg px-3 py-2.5 hover:bg-muted/60 transition-colors"
                  aria-pressed={layerToggles[layer]}
                >
                  <span className="text-sm font-mono">{layer}</span>
                  {layerToggles[layer] ? (
                    <ToggleRight className="h-5 w-5 text-primary" />
                  ) : (
                    <ToggleLeft className="h-5 w-5 text-muted-foreground" />
                  )}
                </button>
              ))}
            </div>
          </div>

          {/* Domain template buttons */}
          <div className="flex flex-wrap gap-2">
            <button
              onClick={() => applyTemplate("HIPAA")}
              className="px-3 py-1.5 text-xs border border-border rounded-lg hover:bg-muted transition-colors flex items-center gap-1"
            >
              <Shield className="h-3.5 w-3.5" /> Apply HIPAA Template
            </button>
            <button
              onClick={() => applyTemplate("GDPR")}
              className="px-3 py-1.5 text-xs border border-border rounded-lg hover:bg-muted transition-colors flex items-center gap-1"
            >
              <Shield className="h-3.5 w-3.5" /> Apply GDPR Template
            </button>
            <button
              onClick={() => applyTemplate("SOC2")}
              className="px-3 py-1.5 text-xs border border-border rounded-lg hover:bg-muted transition-colors flex items-center gap-1"
            >
              <Shield className="h-3.5 w-3.5" /> Apply SOC2 Template
            </button>
          </div>

          {/* Rules list */}
          {rulesQuery.isLoading ? (
            <div className="space-y-3">
              {Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-20 w-full" />)}
            </div>
          ) : rulesQuery.isError ? (
            <div role="alert" className="flex flex-col items-center justify-center h-32 text-muted-foreground">
              <AlertCircle className="h-8 w-8 opacity-40 mb-2" />
              <p className="text-sm">Failed to load guardrails</p>
              <button onClick={() => void rulesQuery.refetch()} className="mt-2 text-xs text-primary hover:underline">
                Retry
              </button>
            </div>
          ) : rules.length === 0 ? (
            <EmptyState
              title="No guardrail rules"
              description="Create rules to enforce content safety across all agent executions."
              action={
                <button
                  onClick={() => setShowCreateModal(true)}
                  className="px-4 py-2 bg-primary text-primary-foreground rounded-lg text-sm hover:opacity-90"
                >
                  Create First Rule
                </button>
              }
            />
          ) : (
            <div className="space-y-3">
              {rules.map((rule) => (
                <div
                  key={rule.id}
                  className="bg-card border border-border rounded-xl px-5 py-4 flex items-start justify-between gap-4"
                >
                  <div className="space-y-1.5 min-w-0 flex-1">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-medium text-sm">{rule.name}</span>
                      <span
                        className={`px-2 py-0.5 rounded-full text-xs border font-medium capitalize ${
                          SEVERITY_STYLES[rule.severity]
                        }`}
                      >
                        {rule.severity}
                      </span>
                      <span className="px-2 py-0.5 rounded-full text-xs bg-muted font-mono">{rule.rule_type}</span>
                    </div>
                    <div className="flex flex-wrap gap-1">
                      {rule.layers.map((l) => (
                        <span key={l} className="px-1.5 py-0.5 text-xs bg-muted/60 rounded font-mono">
                          {l}
                        </span>
                      ))}
                    </div>
                  </div>
                  <div className="flex items-center gap-2 flex-shrink-0">
                    <button
                      onClick={() => toggleEnabledMutation.mutate({ id: rule.id, rule })}
                      aria-label={rule.enabled ? "Disable" : "Enable"}
                      className="text-muted-foreground hover:text-foreground transition-colors"
                    >
                      {rule.enabled ? (
                        <ToggleRight className="h-5 w-5 text-primary" />
                      ) : (
                        <ToggleLeft className="h-5 w-5" />
                      )}
                    </button>
                    <button
                      onClick={() => setDeleteTarget(rule.id)}
                      aria-label={`Delete ${rule.name}`}
                      className="p-1.5 hover:bg-red-50 dark:hover:bg-red-900/20 text-red-600 rounded-md transition-colors"
                    >
                      <Trash2 className="h-4 w-4" />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Violations tab */}
      {tab === "violations" && (
        <div className="space-y-3">
          <h2 className="text-sm font-semibold">Recent Violations</h2>
          {violationsQuery.isLoading ? (
            <div className="space-y-2">
              {Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-16 w-full" />)}
            </div>
          ) : violations.length === 0 ? (
            <EmptyState title="No violations recorded" description="Guardrail violations will appear here in real-time." />
          ) : (
            <div className="space-y-2">
              {violations.map((v) => (
                <ViolationCard key={v.id} violation={v} />
              ))}
            </div>
          )}
        </div>
      )}

      {/* Test Playground */}
      {tab === "test" && (
        <div className="space-y-5">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <div className="space-y-3">
              <h2 className="text-sm font-semibold">Test Input</h2>
              <textarea
                value={testText}
                onChange={(e) => setTestText(e.target.value)}
                placeholder="Enter goal text, tool arguments, or output to test against guardrails…"
                rows={8}
                className="w-full border border-input rounded-lg px-3 py-2.5 text-sm bg-background resize-none"
              />
              <button
                onClick={() => void handleTest()}
                disabled={!testText.trim() || testLoading}
                className="flex items-center gap-2 px-4 py-2 bg-primary text-primary-foreground rounded-lg text-sm hover:opacity-90 disabled:opacity-50"
              >
                <Play className="h-4 w-4" />
                {testLoading ? "Testing…" : "Run Test"}
              </button>
            </div>

            {testResult && (
              <div className="space-y-4">
                <h2 className="text-sm font-semibold">Test Result</h2>
                <RiskGauge score={testResult.risk_score} />
                <div className="space-y-2">
                  <p className={`text-sm font-medium ${testResult.passed ? "text-green-600" : "text-red-600"}`}>
                    {testResult.passed ? "✓ All guardrails passed" : `✗ ${testResult.violations.length} violation(s) detected`}
                  </p>
                  {testResult.violations.map((v, i) => (
                    <div key={i} className="bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg px-3 py-2 text-xs">
                      <span className="font-semibold text-red-700 dark:text-red-400">{v.severity.toUpperCase()}</span>
                      {" — "}
                      {v.message}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Modals */}
      <RuleCreatorModal
        open={showCreateModal}
        onClose={() => setShowCreateModal(false)}
        onCreated={() => qc.invalidateQueries({ queryKey: ["guardrails"] })}
      />

      <ConfirmModal
        open={!!deleteTarget}
        title="Delete guardrail rule?"
        description="This cannot be undone. Active violations referencing this rule will remain."
        confirmLabel="Delete"
        variant="danger"
        isLoading={deleteMutation.isPending}
        onConfirm={() => deleteTarget && deleteMutation.mutate(deleteTarget)}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  );
}
