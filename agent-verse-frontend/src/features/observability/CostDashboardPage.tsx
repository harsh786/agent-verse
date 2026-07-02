/**
 * CostDashboardPage — World-class financial intelligence dashboard for AgentVerse.
 *
 * Sections:
 *  1. Command Bar  — period selector, agent filter, export CSV, set budget, live ticker
 *  2. KPI Row      — 6 metric cards with animated counters
 *  3. Intelligence Row — cost trajectory chart + model/operation breakdown
 *  4. Anomaly Panel — EWMA anomalies with sigma badges + investigate links
 *  5. Agent Table  — sortable per-agent cost table with sparklines
 *  6. Cost Predictor — debounced prediction with breakdown
 *  7. Budget Modal — per-goal / daily / per-agent limits
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { JSX } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import {
  AlertCircle,
  ArrowDown,
  ArrowUp,
  ChevronDown,
  ChevronRight,
  ChevronUp,
  Download,
  DollarSign,
  Filter,
  RefreshCw,
  Settings,
  Target,
  TrendingUp,
  Zap,
  X,
  Minus,
} from "lucide-react";
import {
  Cell,
  Line,
  LineChart,
  Pie,
  PieChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  CartesianGrid,
} from "recharts";

import { useAuthStore } from "@/stores/auth";
import { analyticsApi, costsApi } from "@/lib/api/client";
import type { CostAnomaly, AgentCost } from "@/lib/api/client";
import { Skeleton } from "@/components/ui/Skeleton";
import { toast } from "@/stores/toast";

// ── Constants & helpers ───────────────────────────────────────────────────────

const PERIODS = [
  { label: "Today", days: 1 },
  { label: "7d", days: 7 },
  { label: "30d", days: 30 },
  { label: "90d", days: 90 },
] as const;

type PeriodDays = 1 | 7 | 30 | 90;

const PIE_COLORS = [
  "#6366f1", "#06b6d4", "#10b981", "#f59e0b",
  "#ef4444", "#8b5cf6", "#ec4899", "#14b8a6",
];

const formatCost = (v: number): string =>
  v >= 100 ? `$${v.toFixed(0)}` : v >= 1 ? `$${v.toFixed(2)}` : v >= 0.01 ? `$${v.toFixed(4)}` : `$${v.toFixed(6)}`;

const formatCompact = (v: number): string =>
  v >= 1000 ? `$${(v / 1000).toFixed(1)}k` : formatCost(v);

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const m = Math.floor(diff / 60_000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

function useDebounce<T>(value: T, delay: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}

// ── AnimatedNumber component ─────────────────────────────────────────────────

function AnimatedNumber({
  value,
  format,
}: {
  value: number;
  format: (v: number) => string;
}): JSX.Element {
  const [display, setDisplay] = useState(value);
  const prevRef = useRef(value);

  useEffect(() => {
    const from = prevRef.current;
    const to = value;
    prevRef.current = to;
    if (from === to) return;

    const duration = 600;
    const start = performance.now();
    const step = (now: number) => {
      const t = Math.min((now - start) / duration, 1);
      const eased = t < 0.5 ? 2 * t * t : -1 + (4 - 2 * t) * t;
      setDisplay(from + (to - from) * eased);
      if (t < 1) requestAnimationFrame(step);
      else setDisplay(to);
    };
    requestAnimationFrame(step);
  }, [value]);

  return <>{format(display)}</>;
}

// ── Sparkline component ──────────────────────────────────────────────────────

function Sparkline({ data }: { data: number[] }): JSX.Element {
  if (data.length < 2) return <span className="text-xs text-muted-foreground">—</span>;
  const max = Math.max(...data, 0.001);
  const w = 48;
  const h = 20;
  const points = data.map((v, i) => [
    (i / (data.length - 1)) * w,
    h - (v / max) * h,
  ]);
  const d = points.map(([x, y], i) => `${i === 0 ? "M" : "L"} ${x} ${y}`).join(" ");
  const trend = data[data.length - 1] - data[0];
  const color = trend > 0 ? "#ef4444" : "#10b981";
  return (
    <svg width={w} height={h} className="inline-block">
      <path d={d} fill="none" stroke={color} strokeWidth={1.5} />
    </svg>
  );
}

// ── Budget Modal ─────────────────────────────────────────────────────────────

interface BudgetModalProps {
  onClose: () => void;
}

function BudgetModal({ onClose }: BudgetModalProps): JSX.Element {
  const qc = useQueryClient();
  const { data: budget } = useQuery({
    queryKey: ["cost-budgets"],
    queryFn: () => costsApi.getBudgets(),
    staleTime: 30_000,
  });

  const [perGoal, setPerGoal] = useState<string>(
    String(budget?.per_goal_usd ?? "10.00")
  );
  const [perDay, setPerDay] = useState<string>(
    String(budget?.per_tenant_daily_usd ?? "500.00")
  );
  const [alert80, setAlert80] = useState(true);
  const [alert95, setAlert95] = useState(true);

  const mutation = useMutation({
    mutationFn: () =>
      costsApi.updateBudgets({
        per_goal_usd: parseFloat(perGoal) || 10,
        per_tenant_daily_usd: parseFloat(perDay) || 500,
        alert_pct_thresholds: [
          ...(alert80 ? [80] : []),
          ...(alert95 ? [95] : []),
        ],
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["cost-budgets"] });
      toast({ kind: "success", message: "Budget saved successfully" });
      onClose();
    },
    onError: () => toast({ kind: "error", message: "Failed to save budget" }),
  });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
      <div
        className="bg-card border border-border rounded-2xl p-6 w-full max-w-md shadow-2xl"
        role="dialog"
        aria-label="Budget Manager"
      >
        <div className="flex items-center justify-between mb-5">
          <h2 className="text-lg font-bold flex items-center gap-2">
            <Settings className="h-5 w-5 text-primary" />
            Budget Manager
          </h2>
          <button
            onClick={onClose}
            aria-label="Close budget modal"
            className="text-muted-foreground hover:text-foreground transition-colors"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium mb-1.5">Per-goal limit (USD)</label>
            <div className="relative">
              <DollarSign className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
              <input
                type="number"
                min="0"
                step="0.01"
                value={perGoal}
                onChange={(e) => setPerGoal(e.target.value)}
                className="w-full border border-input rounded-lg pl-9 pr-3 py-2 text-sm bg-background"
                aria-label="Per-goal budget limit"
              />
            </div>
          </div>

          <div>
            <label className="block text-sm font-medium mb-1.5">Daily tenant limit (USD)</label>
            <div className="relative">
              <DollarSign className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
              <input
                type="number"
                min="0"
                step="1"
                value={perDay}
                onChange={(e) => setPerDay(e.target.value)}
                className="w-full border border-input rounded-lg pl-9 pr-3 py-2 text-sm bg-background"
                aria-label="Daily budget limit"
              />
            </div>
          </div>

          <div>
            <p className="text-sm font-medium mb-2">Alert thresholds</p>
            <div className="space-y-2">
              <label className="flex items-center gap-2 text-sm cursor-pointer">
                <input
                  type="checkbox"
                  checked={alert80}
                  onChange={(e) => setAlert80(e.target.checked)}
                  className="rounded"
                />
                Alert at 80% of daily limit
              </label>
              <label className="flex items-center gap-2 text-sm cursor-pointer">
                <input
                  type="checkbox"
                  checked={alert95}
                  onChange={(e) => setAlert95(e.target.checked)}
                  className="rounded"
                />
                Alert at 95% of daily limit
              </label>
            </div>
          </div>
        </div>

        <div className="flex gap-3 mt-6">
          <button
            onClick={onClose}
            className="flex-1 border border-border rounded-lg py-2 text-sm hover:bg-muted transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={() => mutation.mutate()}
            disabled={mutation.isPending}
            className="flex-1 bg-primary text-primary-foreground rounded-lg py-2 text-sm font-medium hover:opacity-90 transition-opacity disabled:opacity-60"
          >
            {mutation.isPending ? "Saving…" : "Save Budget"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── KPI Card ─────────────────────────────────────────────────────────────────

interface KpiCardProps {
  label: string;
  value: number;
  format: (v: number) => string;
  sub?: string;
  icon?: JSX.Element;
  trend?: number | null;  // positive = up, negative = down
  ringPct?: number;       // 0–100 for progress ring
  ringColor?: string;
  loading?: boolean;
}

function KpiCard({
  label,
  value,
  format,
  sub,
  icon,
  trend,
  ringPct,
  ringColor = "#6366f1",
  loading = false,
}: KpiCardProps): JSX.Element {
  if (loading) return <Skeleton className="h-28 w-full rounded-xl" />;

  const trendIsUp = trend != null && trend > 0;
  const trendIsDown = trend != null && trend < 0;
  const TrendIcon = trendIsUp ? ArrowUp : trendIsDown ? ArrowDown : Minus;

  return (
    <div className="bg-card border border-border rounded-xl p-5 flex flex-col gap-1.5 hover:shadow-md transition-shadow">
      <div className="flex items-center justify-between">
        <span className="text-xs text-muted-foreground font-medium uppercase tracking-wide">
          {label}
        </span>
        {icon && (
          <span className="text-muted-foreground">{icon}</span>
        )}
      </div>

      <div className="flex items-end gap-3">
        <span className="text-2xl font-bold tabular-nums">
          <AnimatedNumber value={value} format={format} />
        </span>

        {ringPct != null && (
          <div
            className="relative flex-shrink-0 h-10 w-10"
            aria-label={`${ringPct.toFixed(0)}% used`}
          >
            <svg viewBox="0 0 36 36" className="rotate-[-90deg]">
              <circle cx="18" cy="18" r="14" fill="none" stroke="#e5e7eb" strokeWidth="4" />
              <circle
                cx="18"
                cy="18"
                r="14"
                fill="none"
                stroke={ringColor}
                strokeWidth="4"
                strokeDasharray={`${Math.min(ringPct, 100) * 0.879} 100`}
                strokeLinecap="round"
              />
            </svg>
            <span className="absolute inset-0 flex items-center justify-center text-[9px] font-bold" style={{ transform: "rotate(90deg)" }}>
              {ringPct.toFixed(0)}%
            </span>
          </div>
        )}

        {trend != null && (
          <span
            className={`flex items-center gap-0.5 text-xs font-medium mb-0.5 ${
              trendIsUp ? "text-red-500" : trendIsDown ? "text-green-500" : "text-muted-foreground"
            }`}
          >
            <TrendIcon className="h-3 w-3" />
            {Math.abs(trend).toFixed(1)}%
          </span>
        )}
      </div>

      {sub && <p className="text-xs text-muted-foreground">{sub}</p>}
    </div>
  );
}

// ── Anomaly Panel ─────────────────────────────────────────────────────────────

function AnomalyPanel(): JSX.Element {
  const navigate = useNavigate();
  const { data: anomalies = [], isLoading } = useQuery({
    queryKey: ["cost-anomalies"],
    queryFn: () => costsApi.getAnomalies(),
    refetchInterval: 60_000,
  });

  const [thresholdOpen, setThresholdOpen] = useState(false);

  if (isLoading) return <Skeleton className="h-24 w-full rounded-xl" />;
  if (anomalies.length === 0) return <></>;

  const highCount = anomalies.filter((a) => a.severity === "high").length;
  const medCount = anomalies.filter((a) => a.severity === "medium").length;

  return (
    <div className="bg-card border border-amber-200/60 dark:border-amber-700/40 rounded-xl overflow-hidden">
      <div className="flex items-center justify-between px-5 py-3 bg-amber-50/50 dark:bg-amber-900/10 border-b border-amber-200/60 dark:border-amber-700/40">
        <div className="flex items-center gap-2.5">
          <AlertCircle className="h-4 w-4 text-amber-600 dark:text-amber-400" />
          <h2 className="font-semibold text-sm">
            {anomalies.length} anomal{anomalies.length === 1 ? "y" : "ies"} detected
          </h2>
          <div className="flex gap-1.5">
            {highCount > 0 && (
              <span className="text-xs px-1.5 py-0.5 rounded-full bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400 font-medium">
                {highCount} high
              </span>
            )}
            {medCount > 0 && (
              <span className="text-xs px-1.5 py-0.5 rounded-full bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400 font-medium">
                {medCount} medium
              </span>
            )}
          </div>
        </div>
        <button
          onClick={() => setThresholdOpen(true)}
          className="text-xs text-muted-foreground hover:text-foreground flex items-center gap-1 transition-colors"
          aria-label="Configure anomaly thresholds"
        >
          <Settings className="h-3.5 w-3.5" />
          Configure Thresholds
        </button>
      </div>

      <div className="p-3 space-y-2">
        {anomalies.map((anomaly: CostAnomaly) => (
          <div
            key={anomaly.id}
            className={`flex items-start gap-3 rounded-xl border px-4 py-3 text-sm ${
              anomaly.severity === "high"
                ? "border-red-300/60 bg-red-50/40 dark:bg-red-900/10"
                : anomaly.severity === "medium"
                ? "border-yellow-300/60 bg-yellow-50/40 dark:bg-yellow-900/10"
                : "border-border bg-muted/20"
            }`}
          >
            <AlertCircle
              className={`h-4 w-4 mt-0.5 flex-shrink-0 ${
                anomaly.severity === "high"
                  ? "text-red-500 dark:text-red-400"
                  : anomaly.severity === "medium"
                  ? "text-yellow-500 dark:text-yellow-400"
                  : "text-muted-foreground"
              }`}
            />
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <p className="font-medium capitalize">
                  {(anomaly.type ?? anomaly.anomaly_type ?? "anomaly").replace(/_/g, " ")}
                </p>
                <span className="text-xs text-muted-foreground">
                  {anomaly.agent_id ? `agent: ${anomaly.agent_id.slice(0, 8)}…` : "tenant-wide"}
                </span>
              </div>
              <p className="text-muted-foreground text-xs mt-0.5">{anomaly.message ?? ""}</p>
              {anomaly.sigma_deviation != null && (
                <p className="text-xs mt-0.5 font-mono">
                  <span className="font-semibold">{anomaly.sigma_deviation.toFixed(1)}σ</span>
                  {" "}above baseline
                  {anomaly.detected_at && (
                    <span className="text-muted-foreground ml-2">{timeAgo(anomaly.detected_at)}</span>
                  )}
                </p>
              )}
            </div>
            <div className="flex flex-col items-end gap-2 flex-shrink-0">
              <span className="text-sm font-mono font-semibold text-red-600 dark:text-red-400">
                +{formatCost(Math.max(anomaly.cost_delta_usd ?? 0, 0))}
              </span>
              {anomaly.agent_id && (
                <button
                  onClick={() => navigate(`/agents/${anomaly.agent_id}`)}
                  className="text-xs text-primary hover:underline"
                  aria-label={`Investigate agent ${anomaly.agent_id}`}
                >
                  Investigate →
                </button>
              )}
            </div>
          </div>
        ))}
      </div>

      {thresholdOpen && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm"
          role="dialog"
          aria-label="Configure anomaly thresholds"
        >
          <div className="bg-card border border-border rounded-2xl p-6 w-full max-w-sm shadow-2xl">
            <div className="flex items-center justify-between mb-4">
              <h3 className="font-semibold">Anomaly Thresholds</h3>
              <button onClick={() => setThresholdOpen(false)} aria-label="Close threshold modal">
                <X className="h-5 w-5 text-muted-foreground" />
              </button>
            </div>
            <p className="text-sm text-muted-foreground mb-4">
              Anomalies are flagged using EWMA z-score detection. The default threshold is 3σ.
              Adjust via the backend <code className="text-xs">CostTracker._SIGMA_THRESHOLD</code>.
            </p>
            <button
              onClick={() => setThresholdOpen(false)}
              className="w-full bg-primary text-primary-foreground rounded-lg py-2 text-sm font-medium"
            >
              Close
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Cost Predictor ────────────────────────────────────────────────────────────

function CostPredictor({
  agents,
  dailyRemaining,
}: {
  agents: AgentCost[];
  dailyRemaining: number;
}): JSX.Element {
  const [open, setOpen] = useState(false);
  const [goalText, setGoalText] = useState("");
  const [selectedAgent, setSelectedAgent] = useState<string>("");
  const debouncedGoal = useDebounce(goalText, 800);

  const { data: prediction, isFetching } = useQuery({
    queryKey: ["cost-predict", debouncedGoal, selectedAgent],
    queryFn: () => costsApi.predict(debouncedGoal, selectedAgent || undefined),
    enabled: debouncedGoal.trim().length > 20,
    staleTime: 60_000,
  });

  return (
    <div className="bg-card border border-border rounded-xl overflow-hidden">
      <button
        className="w-full flex items-center justify-between px-5 py-3.5 text-sm font-semibold hover:bg-muted/30 transition-colors"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-controls="cost-predictor-body"
      >
        <span className="flex items-center gap-2">
          <Zap className="h-4 w-4 text-primary" />
          Cost Predictor
        </span>
        {open ? (
          <ChevronDown className="h-4 w-4 text-muted-foreground" />
        ) : (
          <ChevronRight className="h-4 w-4 text-muted-foreground" />
        )}
      </button>

      {open && (
        <div id="cost-predictor-body" className="px-5 pb-5 space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            <div className="md:col-span-2">
              <label className="text-xs text-muted-foreground mb-1 block">
                Goal description ({goalText.length} chars)
              </label>
              <textarea
                rows={3}
                value={goalText}
                onChange={(e) => setGoalText(e.target.value)}
                placeholder="Describe the goal to predict its cost…  (type at least 20 characters)"
                className="w-full border border-input rounded-lg px-3 py-2 text-sm bg-background resize-none"
                aria-label="Goal text for cost prediction"
              />
            </div>
            <div>
              <label className="text-xs text-muted-foreground mb-1 block">Agent (optional)</label>
              <select
                value={selectedAgent}
                onChange={(e) => setSelectedAgent(e.target.value)}
                className="w-full border border-input rounded-lg px-3 py-2 text-sm bg-background"
                aria-label="Select agent for prediction"
              >
                <option value="">Any agent</option>
                {agents.map((a) => (
                  <option key={a.agent_id} value={a.agent_id}>
                    {a.agent_id}
                  </option>
                ))}
              </select>
            </div>
          </div>

          {isFetching && (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <RefreshCw className="h-3.5 w-3.5 animate-spin" />
              Estimating…
            </div>
          )}

          {prediction && !isFetching && (
            <div className="bg-muted/30 rounded-xl p-4 space-y-3">
              <div className="flex items-center justify-between">
                <div>
                  <div className="flex items-baseline gap-2">
                    <span className="text-2xl font-bold tabular-nums">
                      {formatCost(prediction.predicted_cost_usd)}
                    </span>
                    <span className="text-sm text-muted-foreground">estimated (p50)</span>
                  </div>
                  <div className="flex items-center gap-3 mt-1 text-xs text-muted-foreground">
                    <span>p95: <span className="font-medium text-foreground">{formatCost(prediction.p95_cost_usd)}</span></span>
                    <span
                      className={`px-2 py-0.5 rounded-full font-medium ${
                        prediction.confidence === "high"
                          ? "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400"
                          : "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400"
                      }`}
                    >
                      {prediction.confidence} confidence
                    </span>
                  </div>
                </div>
                <div className="text-right text-xs">
                  <p className="text-muted-foreground">Budget remaining after this goal</p>
                  <p
                    className={`font-semibold text-sm mt-0.5 ${
                      dailyRemaining - prediction.predicted_cost_usd < 0
                        ? "text-red-500"
                        : "text-green-600 dark:text-green-400"
                    }`}
                  >
                    {formatCost(Math.max(dailyRemaining - prediction.predicted_cost_usd, 0))}
                  </p>
                </div>
              </div>

              <div className="grid grid-cols-3 gap-2 pt-2 border-t border-border">
                {(
                  [
                    ["Planning", prediction.breakdown.planning_usd],
                    ["Execution", prediction.breakdown.execution_usd],
                    ["Verification", prediction.breakdown.verification_usd],
                  ] as [string, number][]
                ).map(([label, cost]) => (
                  <div key={label} className="text-center">
                    <p className="text-xs text-muted-foreground">{label}</p>
                    <p className="font-mono text-sm font-medium">{formatCost(cost)}</p>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Agent Cost Table ─────────────────────────────────────────────────────────

type SortColumn = keyof AgentCost | "efficiency";
type SortDir = "asc" | "desc";

function AgentCostTable({ agents, isLoading }: { agents: AgentCost[]; isLoading: boolean }): JSX.Element {
  const navigate = useNavigate();
  const [sortCol, setSortCol] = useState<SortColumn>("total_cost_usd");
  const [sortDir, setSortDir] = useState<SortDir>("desc");

  const handleSort = useCallback(
    (col: SortColumn) => {
      if (col === sortCol) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
      else { setSortCol(col); setSortDir("desc"); }
    },
    [sortCol]
  );

  const sorted = useMemo(() => {
    const rows = [...agents];
    rows.sort((a, b) => {
      let va: number, vb: number;
      if (sortCol === "efficiency") {
        const totalA = a.total_prompt_tokens + a.total_completion_tokens;
        const totalB = b.total_prompt_tokens + b.total_completion_tokens;
        va = totalA > 0 ? a.total_cost_usd / (totalA / 1_000_000) : 0;
        vb = totalB > 0 ? b.total_cost_usd / (totalB / 1_000_000) : 0;
      } else {
        va = (a[sortCol as keyof AgentCost] as number) ?? 0;
        vb = (b[sortCol as keyof AgentCost] as number) ?? 0;
      }
      return sortDir === "asc" ? va - vb : vb - va;
    });
    return rows;
  }, [agents, sortCol, sortDir]);

  function SortIcon({ col }: { col: SortColumn }) {
    if (sortCol !== col) return <ChevronUp className="h-3 w-3 text-muted-foreground/40" />;
    return sortDir === "asc"
      ? <ChevronUp className="h-3 w-3 text-primary" />
      : <ChevronDown className="h-3 w-3 text-primary" />;
  }

  function Th({ col, label, align = "right" }: { col: SortColumn; label: string; align?: string }) {
    return (
      <th
        className={`px-4 py-3 text-${align} text-xs font-medium text-muted-foreground cursor-pointer hover:text-foreground transition-colors select-none`}
        onClick={() => handleSort(col)}
        aria-sort={sortCol === col ? (sortDir === "asc" ? "ascending" : "descending") : "none"}
      >
        <span className="inline-flex items-center gap-1">
          {label}
          <SortIcon col={col} />
        </span>
      </th>
    );
  }

  if (isLoading) return <Skeleton className="h-32 w-full" />;
  if (agents.length === 0) {
    return (
      <p className="px-5 py-6 text-sm text-muted-foreground text-center">
        No per-agent cost data yet. Goals need to be run with an agent assigned.
      </p>
    );
  }

  const maxCost = Math.max(...agents.map((a) => a.total_cost_usd), 0.001);

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border bg-muted/30">
            <Th col="agent_id" label="Agent" align="left" />
            <Th col="total_cost_usd" label="Total Cost" />
            <Th col="goal_count" label="Goals" />
            <Th col="avg_cost_per_goal" label="Avg / Goal" />
            <Th col="efficiency" label="Token Efficiency" />
            <th className="px-4 py-3 text-right text-xs font-medium text-muted-foreground">Trend</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {sorted.map((ac) => {
            const totalTokens = ac.total_prompt_tokens + ac.total_completion_tokens;
            const costPerMToken = totalTokens > 0 ? (ac.total_cost_usd / (totalTokens / 1_000_000)) : 0;
            const efficiencyPct = maxCost > 0 ? (ac.total_cost_usd / maxCost) * 100 : 0;
            const efficiencyColor =
              efficiencyPct > 70
                ? "text-red-600 dark:text-red-400"
                : efficiencyPct > 40
                ? "text-yellow-600 dark:text-yellow-400"
                : "text-green-600 dark:text-green-400";

            return (
              <tr
                key={ac.agent_id}
                className="hover:bg-muted/20 transition-colors cursor-pointer"
                onClick={() => navigate(`/agents/${ac.agent_id}`)}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => e.key === "Enter" && navigate(`/agents/${ac.agent_id}`)}
              >
                <td className="px-4 py-3">
                  <span className="font-medium font-mono text-xs bg-muted px-2 py-0.5 rounded">
                    {ac.agent_id ? ac.agent_id.slice(0, 12) : "—"}
                  </span>
                </td>
                <td className="px-4 py-3 text-right font-mono font-semibold">
                  {formatCost(ac.total_cost_usd)}
                </td>
                <td className="px-4 py-3 text-right tabular-nums">{ac.goal_count}</td>
                <td className="px-4 py-3 text-right font-mono">
                  {formatCost(ac.avg_cost_per_goal)}
                </td>
                <td className={`px-4 py-3 text-right font-mono ${efficiencyColor}`}>
                  {costPerMToken > 0 ? `$${costPerMToken.toFixed(2)}/1M tok` : "—"}
                </td>
                <td className="px-4 py-3 text-right">
                  <Sparkline data={[ac.goal_count, ac.total_cost_usd, ac.avg_cost_per_goal]} />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ── Live cost ticker ─────────────────────────────────────────────────────────

function LiveCostTicker({ days }: { days: PeriodDays }): JSX.Element {
  const [delta, setDelta] = useState<number | null>(null);
  const [prevTotal, setPrevTotal] = useState<number | null>(null);
  const [flash, setFlash] = useState(false);

  const { data } = useQuery({
    queryKey: ["cost-summary-ticker", days],
    queryFn: () => costsApi.getSummary(days),
    refetchInterval: 30_000,
  });

  useEffect(() => {
    if (!data) return;
    const current = data.total_cost_usd ?? 0;
    if (prevTotal !== null && current !== prevTotal) {
      setDelta(current - prevTotal);
      setFlash(true);
      const t = setTimeout(() => setFlash(false), 1200);
      return () => clearTimeout(t);
    }
    setPrevTotal(current);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data]);

  if (!delta && delta !== 0) return <></>;

  return (
    <span
      className={`text-xs font-mono px-2 py-1 rounded-full transition-all duration-300 ${
        flash
          ? delta > 0
            ? "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400"
            : "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400"
          : "bg-muted text-muted-foreground"
      }`}
      aria-live="polite"
      aria-label={`Cost change since last refresh: ${delta > 0 ? "+" : ""}${formatCost(delta)}`}
    >
      {delta > 0 ? "+" : ""}{formatCost(delta)} since last refresh
    </span>
  );
}

// ── Main CostDashboardPage ────────────────────────────────────────────────────

export function CostDashboardPage(): JSX.Element {
  const apiKey = useAuthStore((s) => s.apiKey);

  const [days, setDays] = useState<PeriodDays>(30);
  const [agentFilter, setAgentFilter] = useState<string>("");
  const [budgetModalOpen, setBudgetModalOpen] = useState(false);
  const [breakdownView, setBreakdownView] = useState<"model" | "operation">("model");

  const enabled = !!apiKey;

  // ── API queries ────────────────────────────────────────────────────────────

  const { data: kpiData, isLoading: kpiLoading } = useQuery({
    queryKey: ["cost-metrics-kpi"],
    queryFn: async () => {
      const apiUrl = import.meta.env.VITE_API_URL ?? "http://localhost:8000";
      const res = await fetch(`${apiUrl}/goals/cost-metrics`, {
        headers: { "X-API-Key": apiKey },
      });
      if (!res.ok) throw new Error(String(res.status));
      return res.json() as Promise<{
        cost_today_usd: number;
        daily_budget_usd: number;
        budget_utilization: number;
        active_goals: number;
        total_goals: number;
        goals_today: number;
        per_goal_budget_usd: number;
      }>;
    },
    refetchInterval: 30_000,
    enabled,
  });

  const { data: analytics } = useQuery({
    queryKey: ["analytics-costs", days],
    queryFn: () => analyticsApi.getCostMetrics(days),
    enabled,
  });

  const { data: perAgent = [], isLoading: agentCostLoading } = useQuery({
    queryKey: ["cost-per-agent", days],
    queryFn: () => costsApi.getPerAgent(days),
    enabled,
  });

  const { data: modelBreakdown = [] } = useQuery({
    queryKey: ["cost-by-model", days],
    queryFn: () => costsApi.getCostByModel(days),
    enabled,
  });

  const { data: trends = [] } = useQuery({
    queryKey: ["cost-trends", days],
    queryFn: () => costsApi.getCostTrends(days),
    enabled,
  });

  const { data: projection } = useQuery({
    queryKey: ["cost-projection"],
    queryFn: () => costsApi.getProjection(),
    enabled,
    staleTime: 300_000,
  });

  const { data: budgets } = useQuery({
    queryKey: ["cost-budgets"],
    queryFn: () => costsApi.getBudgets(),
    enabled,
    staleTime: 60_000,
  });

  // ── Derived values ─────────────────────────────────────────────────────────

  const utilization = (kpiData?.budget_utilization ?? 0) * 100;
  const dailySpent = kpiData?.cost_today_usd ?? 0;
  const dailyLimit = budgets?.per_tenant_daily_usd ?? kpiData?.daily_budget_usd ?? 500;
  const dailyRemaining = Math.max(0, dailyLimit - dailySpent);

  const ringColor =
    utilization > 80 ? "#ef4444" : utilization > 50 ? "#f59e0b" : "#10b981";

  const avgCostPerGoal =
    kpiData?.goals_today && kpiData.goals_today > 0
      ? dailySpent / kpiData.goals_today
      : 0;

  const totalPeriodSpend = analytics?.total_cost_usd ?? 0;

  // Model breakdown for pie chart (prefer /costs/by-model, fall back to analytics)
  const modelPieData = useMemo(() => {
    if (modelBreakdown.length > 0) {
      return modelBreakdown
        .slice(0, 8)
        .map((m) => ({ name: m.model.split("/").pop()?.slice(0, 16) ?? m.model, value: m.total_cost_usd }));
    }
    if (analytics?.cost_by_model) {
      return Object.entries(analytics.cost_by_model)
        .map(([m, v]) => ({ name: m.split("/").pop()?.slice(0, 16) ?? m, value: v as number }))
        .sort((a, b) => b.value - a.value)
        .slice(0, 8);
    }
    return [];
  }, [modelBreakdown, analytics]);

  const operationPieData = useMemo(() => {
    if (!perAgent.length) return [];
    const totalCost = perAgent.reduce((s, a) => s + a.total_cost_usd, 0);
    return [
      { name: "Planning", value: totalCost * 0.1 },
      { name: "Execution", value: totalCost * 0.8 },
      { name: "Verification", value: totalCost * 0.1 },
    ].filter((d) => d.value > 0);
  }, [perAgent]);

  const activePieData = breakdownView === "model" ? modelPieData : operationPieData;

  // Trajectory chart — merge daily trends with analytics daily data
  const trajectoryData = useMemo(() => {
    if (trends.length > 0) return trends;
    return (analytics?.cost_by_day ?? []).map((d) => ({
      date: d.date,
      cost_usd: d.cost_usd,
      moving_avg_7d: 0,
      is_anomaly: false,
    }));
  }, [trends, analytics]);

  const anomalyDates = new Set(
    trajectoryData.filter((d) => d.is_anomaly).map((d) => d.date)
  );

  // Filtered agents
  const filteredAgents = useMemo(
    () =>
      agentFilter
        ? perAgent.filter((a) => (a.agent_id ?? "").includes(agentFilter))
        : perAgent,
    [perAgent, agentFilter]
  );

  // CSV export
  const handleExportCsv = useCallback(async () => {
    try {
      const blob = await costsApi.getSummaryCsv(days);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `cost_summary_${days}d.csv`;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      toast({ kind: "error", message: "Failed to export CSV" });
    }
  }, [days]);

  return (
    <div className="space-y-6">
      {/* ── Command Bar ──────────────────────────────────────────────── */}
      <div className="sticky top-0 z-10 bg-background/80 backdrop-blur-md border-b border-border/60 -mx-6 px-6 py-3 flex flex-wrap items-center gap-3">
        <div>
          <h1 className="text-xl font-bold leading-tight">Cost Dashboard</h1>
          <p className="text-xs text-muted-foreground">LLM spend · budget tracking · anomaly detection</p>
        </div>

        <div className="flex gap-1 border border-border rounded-lg overflow-hidden ml-auto">
          {PERIODS.map((p) => (
            <button
              key={p.days}
              onClick={() => setDays(p.days as PeriodDays)}
              aria-pressed={days === p.days}
              className={`px-3 py-1.5 text-xs font-medium transition-colors ${
                days === p.days ? "bg-primary text-primary-foreground" : "hover:bg-muted"
              }`}
            >
              {p.label}
            </button>
          ))}
        </div>

        {/* Agent filter */}
        <div className="relative">
          <Filter className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
          <select
            value={agentFilter}
            onChange={(e) => setAgentFilter(e.target.value)}
            className="border border-border rounded-lg pl-8 pr-3 py-1.5 text-xs bg-background"
            aria-label="Filter by agent"
          >
            <option value="">All Agents</option>
            {perAgent.map((a) => (
              <option key={a.agent_id} value={a.agent_id ?? ""}>
                {a.agent_id ?? "unknown"}
              </option>
            ))}
          </select>
        </div>

        <LiveCostTicker days={days} />

        <button
          onClick={() => setBudgetModalOpen(true)}
          className="flex items-center gap-1.5 border border-border rounded-lg px-3 py-1.5 text-xs hover:bg-muted transition-colors"
          aria-label="Set budget"
        >
          <Settings className="h-3.5 w-3.5" />
          Set Budget
        </button>

        <button
          onClick={handleExportCsv}
          className="flex items-center gap-1.5 border border-border rounded-lg px-3 py-1.5 text-xs hover:bg-muted transition-colors"
          aria-label="Export CSV"
        >
          <Download className="h-3.5 w-3.5" />
          Export CSV
        </button>
      </div>

      {/* ── Anomaly Panel ─────────────────────────────────────────────── */}
      <AnomalyPanel />

      {/* ── KPI Row ───────────────────────────────────────────────────── */}
      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-4">
        <KpiCard
          label="Cost Today"
          value={dailySpent}
          format={formatCost}
          sub={`of ${formatCost(dailyLimit)} daily budget`}
          icon={<DollarSign className="h-4 w-4" />}
          ringPct={utilization}
          ringColor={ringColor}
          loading={kpiLoading}
        />
        <KpiCard
          label="Budget Remaining"
          value={dailyRemaining}
          format={formatCost}
          sub={`${(100 - utilization).toFixed(0)}% remaining`}
          icon={<Target className="h-4 w-4" />}
          loading={kpiLoading}
        />
        <KpiCard
          label="Avg Cost / Goal"
          value={avgCostPerGoal}
          format={formatCost}
          sub={`${kpiData?.goals_today ?? 0} goals today`}
          icon={<TrendingUp className="h-4 w-4" />}
          loading={kpiLoading}
        />
        <KpiCard
          label={`Total (${days}d)`}
          value={totalPeriodSpend}
          format={formatCompact}
          sub={`${days}-day period`}
          icon={<DollarSign className="h-4 w-4" />}
          loading={kpiLoading}
        />
        <KpiCard
          label="Goals Run"
          value={kpiData?.total_goals ?? 0}
          format={(v) => v.toFixed(0)}
          sub={`${kpiData?.active_goals ?? 0} active now`}
          icon={<Target className="h-4 w-4" />}
          loading={kpiLoading}
        />
        <KpiCard
          label="Projected Month"
          value={projection?.projected_monthly_usd ?? 0}
          format={formatCompact}
          sub={`${projection?.confidence ?? "low"} confidence · ${projection?.days_of_data ?? 0} days data`}
          icon={<TrendingUp className="h-4 w-4" />}
          loading={!projection && kpiLoading}
        />
      </div>

      {/* ── Budget progress bar ─────────────────────────────────────── */}
      <div className="bg-card border border-border rounded-xl p-5">
        <div className="flex items-center justify-between mb-3">
          <h2 className="font-semibold text-sm">Daily Budget Consumption</h2>
          <div className="flex items-center gap-2">
            {utilization > 95 && (
              <span className="flex items-center gap-1 text-red-600 dark:text-red-400 text-xs font-medium">
                <AlertCircle className="h-3.5 w-3.5" />
                Critical — budget almost exhausted
              </span>
            )}
            {utilization > 80 && utilization <= 95 && (
              <span className="flex items-center gap-1 text-yellow-600 dark:text-yellow-400 text-xs font-medium">
                <AlertCircle className="h-3.5 w-3.5" />
                Budget 80% consumed
              </span>
            )}
            <button
              onClick={() => setBudgetModalOpen(true)}
              className="text-xs text-muted-foreground hover:text-foreground transition-colors"
            >
              Edit
            </button>
          </div>
        </div>
        <div className="w-full bg-muted rounded-full h-3 overflow-hidden">
          <div
            className={`h-3 rounded-full transition-all duration-700 ${
              utilization > 80 ? "bg-red-500" : utilization > 50 ? "bg-yellow-500" : "bg-green-500"
            }`}
            style={{ width: `${Math.min(utilization, 100)}%` }}
            role="progressbar"
            aria-valuenow={utilization}
            aria-valuemin={0}
            aria-valuemax={100}
          />
        </div>
        <div className="flex justify-between text-xs text-muted-foreground mt-2">
          <span>{formatCost(dailySpent)} used</span>
          <span>{formatCost(dailyLimit)} limit</span>
        </div>
      </div>

      {/* ── Intelligence Row ────────────────────────────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Cost Trajectory */}
        <div className="bg-card border border-border rounded-xl p-5">
          <h2 className="font-semibold text-sm mb-4">Cost Trajectory ({days}d)</h2>
          {trajectoryData.length === 0 ? (
            <div className="h-52 flex items-center justify-center text-sm text-muted-foreground">
              No daily cost data yet
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={210}>
              <LineChart data={trajectoryData} margin={{ top: 4, right: 8, bottom: 0, left: 8 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" opacity={0.5} />
                <XAxis
                  dataKey="date"
                  tick={{ fill: "var(--muted-foreground)", fontSize: 10 }}
                  tickLine={false}
                  axisLine={false}
                  tickFormatter={(v: string) => v.slice(5)}
                />
                <YAxis
                  tick={{ fill: "var(--muted-foreground)", fontSize: 10 }}
                  tickLine={false}
                  axisLine={false}
                  tickFormatter={formatCost}
                />
                <Tooltip
                  contentStyle={{
                    background: "var(--card)",
                    border: "1px solid var(--border)",
                    borderRadius: "8px",
                    fontSize: 12,
                  }}
                  formatter={(v: number, name: string) => [formatCost(v), name]}
                />
                {dailyLimit > 0 && (
                  <ReferenceLine
                    y={dailyLimit}
                    stroke="#ef4444"
                    strokeDasharray="4 4"
                    label={{ value: "Budget", fill: "#ef4444", fontSize: 10 }}
                  />
                )}
                <Line
                  type="monotone"
                  dataKey="cost_usd"
                  name="Daily Cost"
                  stroke="#6366f1"
                  strokeWidth={2}
                  dot={(props) => {
                    const { cx, cy, payload } = props as { cx: number; cy: number; payload: { is_anomaly?: boolean; date: string } };
                    if (payload.is_anomaly) {
                      return (
                        <circle key={payload.date} cx={cx} cy={cy} r={5} fill="#ef4444" stroke="#fff" strokeWidth={1.5} />
                      );
                    }
                    return <g key={payload.date} />;
                  }}
                  activeDot={{ r: 4 }}
                />
                {trajectoryData.some((d) => d.moving_avg_7d > 0) && (
                  <Line
                    type="monotone"
                    dataKey="moving_avg_7d"
                    name="7-day avg"
                    stroke="#06b6d4"
                    strokeWidth={1.5}
                    strokeDasharray="5 3"
                    dot={false}
                  />
                )}
              </LineChart>
            </ResponsiveContainer>
          )}
          {anomalyDates.size > 0 && (
            <p className="text-xs text-red-500 mt-2 flex items-center gap-1">
              <span className="inline-block h-2 w-2 rounded-full bg-red-500" />
              Red dots = anomaly days
            </p>
          )}
        </div>

        {/* Cost Breakdown Pie */}
        <div className="bg-card border border-border rounded-xl p-5">
          <div className="flex items-center justify-between mb-4">
            <h2 className="font-semibold text-sm">Cost Breakdown</h2>
            <div className="flex gap-1 border border-border rounded-lg overflow-hidden text-xs">
              <button
                onClick={() => setBreakdownView("model")}
                aria-pressed={breakdownView === "model"}
                className={`px-2.5 py-1 transition-colors ${
                  breakdownView === "model" ? "bg-primary text-primary-foreground" : "hover:bg-muted"
                }`}
              >
                By Model
              </button>
              <button
                onClick={() => setBreakdownView("operation")}
                aria-pressed={breakdownView === "operation"}
                className={`px-2.5 py-1 transition-colors ${
                  breakdownView === "operation" ? "bg-primary text-primary-foreground" : "hover:bg-muted"
                }`}
              >
                By Operation
              </button>
            </div>
          </div>
          {activePieData.length === 0 ? (
            <div className="h-52 flex items-center justify-center text-sm text-muted-foreground">
              No {breakdownView} data yet
            </div>
          ) : (
            <div className="flex items-center gap-4">
              <ResponsiveContainer width="55%" height={200}>
                <PieChart>
                  <Pie
                    data={activePieData}
                    cx="50%"
                    cy="50%"
                    innerRadius={50}
                    outerRadius={80}
                    paddingAngle={2}
                    dataKey="value"
                  >
                    {activePieData.map((_, idx) => (
                      <Cell key={`cell-${idx}`} fill={PIE_COLORS[idx % PIE_COLORS.length]} />
                    ))}
                  </Pie>
                  <Tooltip
                    contentStyle={{
                      background: "var(--card)",
                      border: "1px solid var(--border)",
                      borderRadius: "8px",
                      fontSize: 12,
                    }}
                    formatter={(v: number) => [formatCost(v)]}
                  />
                </PieChart>
              </ResponsiveContainer>
              <div className="flex-1 space-y-2">
                {activePieData.map((entry, idx) => {
                  const total = activePieData.reduce((s, d) => s + d.value, 0);
                  const pct = total > 0 ? ((entry.value / total) * 100).toFixed(0) : "0";
                  return (
                    <div key={entry.name} className="flex items-center gap-2">
                      <span
                        className="h-2.5 w-2.5 rounded-full flex-shrink-0"
                        style={{ background: PIE_COLORS[idx % PIE_COLORS.length] }}
                      />
                      <span className="text-xs text-muted-foreground flex-1 truncate">{entry.name}</span>
                      <span className="text-xs font-mono font-medium">{formatCost(entry.value)}</span>
                      <span className="text-xs text-muted-foreground">{pct}%</span>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* ── Agent Cost Table ─────────────────────────────────────────── */}
      <div className="bg-card border border-border rounded-xl overflow-hidden">
        <div className="px-5 py-3 border-b border-border flex items-center justify-between">
          <h2 className="font-semibold text-sm">Agent Cost Intelligence</h2>
          <span className="text-xs text-muted-foreground">
            {filteredAgents.length} agent{filteredAgents.length !== 1 ? "s" : ""}
            {agentFilter && " (filtered)"}
          </span>
        </div>
        <AgentCostTable agents={filteredAgents} isLoading={agentCostLoading} />
      </div>

      {/* ── Cost Predictor ────────────────────────────────────────────── */}
      <CostPredictor agents={perAgent} dailyRemaining={dailyRemaining} />

      {/* ── Budget Modal ─────────────────────────────────────────────── */}
      {budgetModalOpen && <BudgetModal onClose={() => setBudgetModalOpen(false)} />}
    </div>
  );
}
