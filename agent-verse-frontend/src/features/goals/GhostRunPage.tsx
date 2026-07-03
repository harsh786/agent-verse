/**
 * GhostRunPage — World-class A/B strategy comparison platform.
 * 3 phases: Configuration → Running → Results
 */
import { useState, useEffect, useCallback } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { goalsApi, type GhostRunStrategy, type GhostRunResponse } from "@/lib/api/client";
import { StatusBadge } from "@/components/ui/StatusBadge";
import {
  Ghost, Play, ExternalLink, Trophy, Plus, Trash2, Settings2,
  CheckCircle2, XCircle, Clock, Zap, BarChart3, History, ChevronRight,
} from "lucide-react";

// ── Types ─────────────────────────────────────────────────────────────────────

type Phase = "config" | "running" | "results";

interface GoalStatus {
  status: string;
  cost_usd?: number;
  iterations?: number;
  current_step?: string;
  eval_score?: number;
  tool_calls?: number;
}

interface HistoryEntry {
  id: string;
  goal: string;
  date: string;
  winner: string;
  strategies: GhostRunStrategy[];
}

interface MetricConfig {
  key: keyof GoalStatus | "duration";
  label: string;
  enabled: boolean;
}

// ── Constants ─────────────────────────────────────────────────────────────────

const DEFAULT_STRATEGIES: GhostRunStrategy[] = [
  { name: "Standard", workflow_mode: "single_agent", priority: "normal" },
  { name: "Multi-Agent", workflow_mode: "multi_agent", priority: "normal" },
  { name: "High-Priority", workflow_mode: "single_agent", priority: "high" },
];

const WORKFLOW_MODES = [
  { value: "single_agent", label: "Single Agent" },
  { value: "multi_agent", label: "Multi-Agent" },
  { value: "supervisor", label: "Supervisor" },
  { value: "debate", label: "Debate" },
];

const PRIORITIES = ["normal", "high", "critical"];
const TERMINAL = new Set(["complete", "completed", "failed", "cancelled"]);

const HISTORY_KEY = "av_ghost_run_history";
const MAX_HISTORY = 5;

// ── Helpers ───────────────────────────────────────────────────────────────────

function loadHistory(): HistoryEntry[] {
  try {
    return JSON.parse(localStorage.getItem(HISTORY_KEY) ?? "[]");
  } catch {
    return [];
  }
}

function saveHistory(entries: HistoryEntry[]): void {
  try {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(entries.slice(0, MAX_HISTORY)));
  } catch {
    // ignore
  }
}

function formatCost(v?: number): string {
  return v != null ? `$${v.toFixed(4)}` : "—";
}

function formatDuration(ms?: number): string {
  if (ms == null) return "—";
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`;
}

// ── Sub-components ────────────────────────────────────────────────────────────

function StrategyCard({
  strategy,
  index,
  onChange,
  onRemove,
  canRemove,
}: {
  strategy: GhostRunStrategy;
  index: number;
  onChange: (s: GhostRunStrategy) => void;
  onRemove: () => void;
  canRemove: boolean;
}) {
  return (
    <div className="border border-border rounded-xl p-4 bg-card space-y-3">
      <div className="flex items-center gap-2">
        <span className="text-xs font-bold text-muted-foreground uppercase tracking-wide">
          Strategy {index + 1}
        </span>
        {canRemove && (
          <button
            onClick={onRemove}
            className="ml-auto text-muted-foreground hover:text-destructive transition-colors"
            aria-label={`Remove strategy ${index + 1}`}
          >
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        )}
      </div>

      <div className="grid grid-cols-2 gap-2">
        <div>
          <label className="text-[10px] text-muted-foreground block mb-1">Name</label>
          <input
            value={strategy.name}
            onChange={(e) => onChange({ ...strategy, name: e.target.value })}
            className="w-full text-xs border rounded px-2 py-1 bg-background focus:outline-none focus:ring-1 focus:ring-primary"
          />
        </div>
        <div>
          <label className="text-[10px] text-muted-foreground block mb-1">Priority</label>
          <select
            value={strategy.priority}
            onChange={(e) => onChange({ ...strategy, priority: e.target.value })}
            className="w-full text-xs border rounded px-2 py-1 bg-background focus:outline-none focus:ring-1 focus:ring-primary"
          >
            {PRIORITIES.map((p) => (
              <option key={p} value={p}>{p}</option>
            ))}
          </select>
        </div>
      </div>

      <div>
        <label className="text-[10px] text-muted-foreground block mb-1">Workflow Mode</label>
        <select
          value={strategy.workflow_mode}
          onChange={(e) => onChange({ ...strategy, workflow_mode: e.target.value })}
          className="w-full text-xs border rounded px-2 py-1 bg-background focus:outline-none focus:ring-1 focus:ring-primary"
        >
          {WORKFLOW_MODES.map((m) => (
            <option key={m.value} value={m.value}>{m.label}</option>
          ))}
        </select>
      </div>

      <div>
        <label className="text-[10px] text-muted-foreground block mb-1">
          Agent ID <span className="text-muted-foreground">(optional)</span>
        </label>
        <input
          value={strategy.agent_id ?? ""}
          onChange={(e) => onChange({ ...strategy, agent_id: e.target.value || undefined })}
          placeholder="agent-uuid"
          className="w-full text-xs border rounded px-2 py-1 bg-background focus:outline-none focus:ring-1 focus:ring-primary font-mono"
        />
      </div>
    </div>
  );
}

function LiveStrategyCard({
  strategyName,
  goalId,
  goalStatus,
  startTime,
  isWinner,
  onViewGoal,
}: {
  strategyName: string;
  goalId: string | null;
  goalStatus: GoalStatus | undefined;
  startTime: number | undefined;
  isWinner: boolean;
  onViewGoal: () => void;
}) {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    if (!startTime) return;
    const interval = setInterval(() => {
      setElapsed(Date.now() - startTime);
    }, 250);
    return () => clearInterval(interval);
  }, [startTime]);

  const st = goalStatus?.status ?? "queued";
  const isTerminal = TERMINAL.has(st);
  const isRunning = !isTerminal && st !== "queued";

  const statusColor =
    st === "complete" || st === "completed" ? "text-green-600" :
    st === "failed" || st === "cancelled" ? "text-red-500" :
    isRunning ? "text-blue-500" : "text-muted-foreground";

  return (
    <div
      className={`relative border-2 rounded-xl p-4 bg-card transition-all ${
        isWinner ? "border-amber-400 shadow-amber-100 shadow-md" :
        isRunning ? "border-blue-300 shadow-sm animate-pulse" :
        "border-border"
      }`}
    >
      {isWinner && (
        <div className="absolute -top-3 left-4 flex items-center gap-1 bg-amber-400 text-amber-900 text-[10px] font-bold px-2 py-0.5 rounded-full">
          <Trophy className="h-3 w-3" /> WINNER
        </div>
      )}

      <div className="flex items-center justify-between mb-2">
        <span className="text-sm font-semibold">{strategyName}</span>
        <StatusBadge status={st} size="sm" />
      </div>

      <div className={`text-xs font-medium mb-3 ${statusColor} flex items-center gap-1`}>
        {isRunning && <span className="inline-block w-1.5 h-1.5 rounded-full bg-blue-500 animate-ping" />}
        {goalStatus?.current_step
          ? <span className="italic truncate">{goalStatus.current_step}</span>
          : <span className="text-muted-foreground">Waiting…</span>
        }
      </div>

      <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
        <span>Duration</span>
        <span className="font-mono text-foreground">
          {isTerminal ? formatDuration(startTime ? Date.now() - startTime : undefined) : formatDuration(elapsed)}
        </span>
        <span>Cost</span>
        <span className="font-mono text-foreground">{formatCost(goalStatus?.cost_usd)}</span>
        <span>Steps</span>
        <span className="font-mono text-foreground">
          {goalStatus?.iterations != null ? goalStatus.iterations : "—"}
        </span>
      </div>

      {goalId && (
        <button
          onClick={onViewGoal}
          className="mt-3 w-full flex items-center justify-center gap-1.5 text-xs text-primary hover:underline"
        >
          View execution <ExternalLink className="h-3 w-3" />
        </button>
      )}
    </div>
  );
}

// ── Main Component ────────────────────────────────────────────────────────────

export function GhostRunPage() {
  const navigate = useNavigate();

  // Phase management
  const [phase, setPhase] = useState<Phase>("config");
  const [goal, setGoal] = useState("");
  const [strategies, setStrategies] = useState<GhostRunStrategy[]>(DEFAULT_STRATEGIES);
  const [metrics, setMetrics] = useState<MetricConfig[]>([
    { key: "cost_usd", label: "Cost", enabled: true },
    { key: "duration", label: "Speed", enabled: true },
    { key: "eval_score", label: "Quality Score", enabled: true },
    { key: "tool_calls", label: "Tool Calls", enabled: false },
  ]);
  const [stopOnFirst] = useState(false);

  // Runtime state
  const [ghostRunResp, setGhostRunResp] = useState<GhostRunResponse | null>(null);
  const [goalStatuses, setGoalStatuses] = useState<Record<string, GoalStatus>>({});
  const [startTimes, setStartTimes] = useState<Record<string, number>>({});

  // History
  const [history, setHistory] = useState<HistoryEntry[]>(() => loadHistory());

  // Launch mutation → POST /goals/ghost-run
  const launch = useMutation({
    mutationFn: () => goalsApi.ghostRun({ goal, strategies }),
    onSuccess: (resp) => {
      setGhostRunResp(resp);
      const now = Date.now();
      const times: Record<string, number> = {};
      Object.values(resp.goal_ids).forEach((id) => { times[id] = now; });
      setStartTimes(times);
      setPhase("running");
    },
  });

  // Poll goal statuses while in running phase
  const goalIds = ghostRunResp ? Object.values(ghostRunResp.goal_ids) : [];
  const { data: polledStatuses } = useQuery({
    queryKey: ["ghost-run-statuses", goalIds.join(",")],
    queryFn: async () => {
      const results = await Promise.allSettled(goalIds.map((id) => goalsApi.get(id)));
      const map: Record<string, GoalStatus> = {};
      results.forEach((r, i) => {
        const id = goalIds[i];
        if (r.status === "fulfilled") {
          const v = r.value as unknown as Record<string, unknown>;
          map[id] = {
            status: (v.status as string) ?? "planning",
            cost_usd: v.cost_usd as number | undefined,
            iterations: v.iterations as number | undefined,
            eval_score: (v.eval_score ?? (v as Record<string, unknown>).score) as number | undefined,
            tool_calls: (v.tool_calls ?? (v.steps as unknown[] | undefined)?.length) as number | undefined,
          };
        } else {
          map[id] = goalStatuses[id] ?? { status: "planning" };
        }
      });
      return map;
    },
    enabled: phase === "running" && goalIds.length > 0,
    refetchInterval: (query) => {
      const d = query.state.data;
      if (!d) return 2000;
      const allDone = Object.values(d).every((s) => TERMINAL.has(s.status));
      return allDone ? false : 2000;
    },
    staleTime: 0,
  });

  // Update statuses and check if we can move to results
  useEffect(() => {
    if (!polledStatuses) return;
    setGoalStatuses(polledStatuses);
    const allDone = Object.values(polledStatuses).every((s) => TERMINAL.has(s.status));
    const anyDone = stopOnFirst && Object.values(polledStatuses).some((s) => TERMINAL.has(s.status));
    if (allDone || anyDone) {
      setPhase("results");
      // Save to history
      if (ghostRunResp) {
        const winner = determineWinner(ghostRunResp, polledStatuses);
        const entry: HistoryEntry = {
          id: ghostRunResp.ghost_run_id,
          goal: goal.slice(0, 80),
          date: new Date().toLocaleDateString(),
          winner: winner ?? "—",
          strategies,
        };
        const newHistory = [entry, ...history].slice(0, MAX_HISTORY);
        setHistory(newHistory);
        saveHistory(newHistory);
      }
    }
  }, [polledStatuses]); // eslint-disable-line react-hooks/exhaustive-deps

  // Determine winner by comparing enabled metrics
  function determineWinner(
    resp: GhostRunResponse,
    statuses: Record<string, GoalStatus>
  ): string | null {
    const completed = resp.strategies.filter(
      (s) => s.goal_id && TERMINAL.has(statuses[s.goal_id]?.status ?? "")
        && statuses[s.goal_id]?.status !== "failed"
        && statuses[s.goal_id]?.status !== "cancelled"
    );
    if (!completed.length) return null;

    // Score each strategy: lower cost + lower duration = better
    const scored = completed.map((s) => {
      const gs = statuses[s.goal_id!] ?? {};
      const startMs = startTimes[s.goal_id!] ?? Date.now();
      const duration = Date.now() - startMs;
      return {
        name: s.name,
        cost: gs.cost_usd ?? 0,
        duration,
        quality: gs.eval_score ?? 0,
      };
    });

    // Find best by cost (primary), then quality (secondary)
    const best = scored.sort((a, b) => {
      if (a.cost !== b.cost) return a.cost - b.cost;
      return b.quality - a.quality;
    })[0];
    return best?.name ?? null;
  }

  const winnerName = ghostRunResp && goalStatuses
    ? determineWinner(ghostRunResp, goalStatuses)
    : null;

  const addStrategy = () => {
    if (strategies.length >= 5) return;
    setStrategies([...strategies, {
      name: `Strategy ${strategies.length + 1}`,
      workflow_mode: "single_agent",
      priority: "normal",
    }]);
  };

  const replayHistory = useCallback((entry: HistoryEntry) => {
    setGoal(entry.goal);
    setStrategies(entry.strategies);
    setPhase("config");
    setGhostRunResp(null);
    setGoalStatuses({});
  }, []);

  // ── Phase 1: Configuration ────────────────────────────────────────────────

  if (phase === "config") {
    return (
      <div className="space-y-6 max-w-4xl">
        {/* Header */}
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <Ghost className="h-6 w-6 text-primary" aria-hidden="true" />
            Ghost Run — Strategy Comparison
          </h1>
          <p className="text-sm text-muted-foreground mt-1">
            Run the same goal with multiple strategies simultaneously. Compare cost, speed, and quality.
          </p>
        </div>

        {/* Goal Input */}
        <div className="space-y-2">
          <label className="block text-sm font-medium" htmlFor="ghost-goal">
            Goal Description
          </label>
          <textarea
            id="ghost-goal"
            value={goal}
            onChange={(e) => setGoal(e.target.value)}
            rows={3}
            placeholder="Describe what you want to achieve…"
            className="w-full px-3 py-2.5 text-sm border border-input rounded-lg bg-background focus:outline-none focus:ring-2 focus:ring-primary resize-none"
          />
        </div>

        {/* Strategy Builder */}
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold flex items-center gap-2">
              <Settings2 className="h-4 w-4 text-primary" />
              Strategies ({strategies.length})
            </h2>
            {strategies.length < 5 && (
              <button
                onClick={addStrategy}
                className="flex items-center gap-1 text-xs text-primary hover:underline"
              >
                <Plus className="h-3.5 w-3.5" /> Add Strategy
              </button>
            )}
          </div>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            {strategies.map((s, i) => (
              <StrategyCard
                key={i}
                strategy={s}
                index={i}
                onChange={(updated) => {
                  const next = [...strategies];
                  next[i] = updated;
                  setStrategies(next);
                }}
                onRemove={() => setStrategies(strategies.filter((_, j) => j !== i))}
                canRemove={strategies.length > 1}
              />
            ))}
          </div>
        </div>

        {/* Comparison Settings */}
        <div className="border border-border rounded-xl p-4 bg-card/50 space-y-3">
          <h2 className="text-sm font-semibold flex items-center gap-2">
            <BarChart3 className="h-4 w-4 text-primary" />
            Compare By
          </h2>
          <div className="flex flex-wrap gap-2">
            {metrics.map((m, i) => (
              <button
                key={m.key}
                onClick={() => {
                  const next = [...metrics];
                  next[i] = { ...m, enabled: !m.enabled };
                  setMetrics(next);
                }}
                className={`text-xs px-3 py-1.5 rounded-full border transition-colors ${
                  m.enabled
                    ? "bg-primary text-primary-foreground border-primary"
                    : "bg-background text-muted-foreground border-border hover:border-primary"
                }`}
              >
                {m.label}
              </button>
            ))}
          </div>
        </div>

        {/* Launch Button */}
        <button
          onClick={() => launch.mutate()}
          disabled={!goal.trim() || launch.isPending}
          className="flex items-center gap-2 px-8 py-3 bg-primary text-primary-foreground text-sm font-semibold rounded-xl hover:opacity-90 disabled:opacity-50 transition-opacity shadow-sm"
        >
          <Ghost className="h-5 w-5" />
          {launch.isPending ? "Launching…" : "Launch Ghost Run"}
        </button>

        {launch.isError && (
          <p role="alert" className="text-sm text-destructive">
            Launch failed: {String(launch.error)}
          </p>
        )}

        {/* History Panel */}
        {history.length > 0 && (
          <div className="space-y-2">
            <h2 className="text-sm font-semibold flex items-center gap-2 text-muted-foreground">
              <History className="h-4 w-4" /> Recent Ghost Runs
            </h2>
            <div className="space-y-1">
              {history.map((entry) => (
                <button
                  key={entry.id}
                  onClick={() => replayHistory(entry)}
                  className="w-full flex items-center gap-3 p-3 bg-card border border-border rounded-lg hover:border-primary/50 transition-colors text-left"
                >
                  <div className="flex-1 min-w-0">
                    <p className="text-xs font-medium truncate">{entry.goal}</p>
                    <p className="text-[10px] text-muted-foreground">
                      {entry.date} · Winner: <span className="text-amber-500 font-medium">{entry.winner}</span>
                    </p>
                  </div>
                  <ChevronRight className="h-4 w-4 text-muted-foreground shrink-0" />
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    );
  }

  // ── Phase 2: Running ──────────────────────────────────────────────────────

  if (phase === "running") {
    return (
      <div className="space-y-6 max-w-5xl">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-xl font-bold flex items-center gap-2">
              <Ghost className="h-5 w-5 text-primary animate-pulse" />
              Ghost Run in progress
            </h1>
            <p className="text-sm text-muted-foreground mt-0.5 max-w-2xl truncate">
              {goal}
            </p>
          </div>
          <button
            onClick={() => {
              ghostRunResp?.strategies.forEach((s) => {
                if (s.goal_id) goalsApi.cancel(s.goal_id).catch(() => null);
              });
              setPhase("config");
            }}
            className="text-xs px-4 py-2 border border-destructive text-destructive rounded-lg hover:bg-destructive/10 transition-colors"
          >
            Cancel All
          </button>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {ghostRunResp?.strategies.map((s) => {
            const gid = s.goal_id;
            const gs = gid ? goalStatuses[gid] : undefined;
            const isWinner = winnerName === s.name;
            return (
              <LiveStrategyCard
                key={s.name}
                strategyName={s.name}
                goalId={gid}
                goalStatus={gs}
                startTime={gid ? startTimes[gid] : undefined}
                isWinner={isWinner}
                onViewGoal={() => gid && navigate(`/goals/${gid}`)}
              />
            );
          })}
        </div>

        <div className="text-xs text-muted-foreground text-center">
          Polling every 2 seconds · Results will appear automatically when all strategies finish
        </div>
      </div>
    );
  }

  // ── Phase 3: Results ──────────────────────────────────────────────────────

  return (
    <div className="space-y-6 max-w-5xl">
      {/* Winner Banner */}
      {winnerName && (
        <div className="flex items-center gap-3 p-4 bg-amber-50 dark:bg-amber-950/30 border border-amber-300 rounded-xl">
          <Trophy className="h-6 w-6 text-amber-500 shrink-0" />
          <div>
            <p className="font-bold text-amber-800 dark:text-amber-200">
              {winnerName} wins
            </p>
            <p className="text-xs text-amber-600 dark:text-amber-400 mt-0.5">
              Best combination of cost{metrics.find(m => m.key === "eval_score" && m.enabled) ? ", quality" : ""} and speed
            </p>
          </div>
          <button
            onClick={() => setPhase("config")}
            className="ml-auto text-xs px-3 py-1.5 bg-amber-400 text-amber-900 rounded-lg hover:bg-amber-500 font-medium"
          >
            Run Again
          </button>
        </div>
      )}

      <h1 className="text-xl font-bold flex items-center gap-2">
        <BarChart3 className="h-5 w-5 text-primary" />
        Comparison Results
      </h1>

      {/* Results Table */}
      <div className="overflow-x-auto border border-border rounded-xl">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b bg-muted/50">
              <th className="text-left px-4 py-2.5 font-medium text-muted-foreground w-28">Metric</th>
              {ghostRunResp?.strategies.map((s) => (
                <th key={s.name} className="text-left px-4 py-2.5 font-medium">
                  <div className="flex items-center gap-1.5">
                    {s.name}
                    {winnerName === s.name && <Trophy className="h-3 w-3 text-amber-500" />}
                  </div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {/* Status row */}
            <tr className="border-b">
              <td className="px-4 py-2 text-muted-foreground font-medium">Status</td>
              {ghostRunResp?.strategies.map((s) => {
                const st = s.goal_id ? goalStatuses[s.goal_id]?.status : s.status;
                const ok = st === "complete" || st === "completed";
                return (
                  <td key={s.name} className="px-4 py-2">
                    <div className="flex items-center gap-1">
                      {ok
                        ? <CheckCircle2 className="h-3.5 w-3.5 text-green-500" />
                        : st === "failed" ? <XCircle className="h-3.5 w-3.5 text-red-500" />
                        : <Clock className="h-3.5 w-3.5 text-muted-foreground" />
                      }
                      <span className={ok ? "text-green-600" : st === "failed" ? "text-red-500" : ""}>
                        {st ?? "—"}
                      </span>
                    </div>
                  </td>
                );
              })}
            </tr>
            {/* Cost row */}
            <tr className="border-b">
              <td className="px-4 py-2 text-muted-foreground font-medium">Total Cost</td>
              {ghostRunResp?.strategies.map((s) => {
                const gs = s.goal_id ? goalStatuses[s.goal_id] : undefined;
                const isWinner = winnerName === s.name && gs?.cost_usd != null;
                return (
                  <td key={s.name} className={`px-4 py-2 font-mono ${isWinner ? "bg-green-50 dark:bg-green-950/30 text-green-700 dark:text-green-400 font-bold" : ""}`}>
                    {formatCost(gs?.cost_usd)}
                  </td>
                );
              })}
            </tr>
            {/* Duration row */}
            <tr className="border-b">
              <td className="px-4 py-2 text-muted-foreground font-medium">Duration</td>
              {ghostRunResp?.strategies.map((s) => {
                const ms = s.goal_id ? startTimes[s.goal_id] : undefined;
                return (
                  <td key={s.name} className="px-4 py-2 font-mono">
                    {ms ? formatDuration(Date.now() - ms) : "—"}
                  </td>
                );
              })}
            </tr>
            {/* Steps row */}
            <tr className="border-b">
              <td className="px-4 py-2 text-muted-foreground font-medium">Steps</td>
              {ghostRunResp?.strategies.map((s) => {
                const gs = s.goal_id ? goalStatuses[s.goal_id] : undefined;
                return (
                  <td key={s.name} className="px-4 py-2 font-mono">{gs?.iterations ?? "—"}</td>
                );
              })}
            </tr>
            {/* Eval score row */}
            {metrics.find(m => m.key === "eval_score" && m.enabled) && (
              <tr className="border-b">
                <td className="px-4 py-2 text-muted-foreground font-medium">Eval Score</td>
                {ghostRunResp?.strategies.map((s) => {
                  const gs = s.goal_id ? goalStatuses[s.goal_id] : undefined;
                  return (
                    <td key={s.name} className="px-4 py-2 font-mono">
                      {gs?.eval_score != null ? gs.eval_score.toFixed(2) : "—"}
                    </td>
                  );
                })}
              </tr>
            )}
            {/* View links row */}
            <tr>
              <td className="px-4 py-2 text-muted-foreground font-medium">Execution</td>
              {ghostRunResp?.strategies.map((s) => (
                <td key={s.name} className="px-4 py-2">
                  {s.goal_id ? (
                    <button
                      onClick={() => navigate(`/goals/${s.goal_id}`)}
                      className="flex items-center gap-1 text-primary hover:underline"
                    >
                      View <ExternalLink className="h-3 w-3" />
                    </button>
                  ) : (
                    <span className="text-muted-foreground">—</span>
                  )}
                </td>
              ))}
            </tr>
          </tbody>
        </table>
      </div>

      <div className="flex gap-3">
        <button
          onClick={() => {
            setPhase("config");
            setGhostRunResp(null);
            setGoalStatuses({});
          }}
          className="flex items-center gap-2 px-6 py-2.5 bg-primary text-primary-foreground text-sm font-medium rounded-lg hover:opacity-90 transition-opacity"
        >
          <Play className="h-4 w-4" /> New Ghost Run
        </button>
        <button
          onClick={() => {
            setGhostRunResp(null);
            setGoalStatuses({});
            launch.mutate();
          }}
          className="flex items-center gap-2 px-6 py-2.5 border border-border text-sm font-medium rounded-lg hover:bg-muted transition-colors"
        >
          <Zap className="h-4 w-4" /> Run Again
        </button>
      </div>

      {/* History */}
      {history.length > 0 && (
        <div className="space-y-2">
          <h2 className="text-sm font-semibold flex items-center gap-2 text-muted-foreground">
            <History className="h-4 w-4" /> Recent Ghost Runs
          </h2>
          <div className="space-y-1">
            {history.map((entry) => (
              <button
                key={entry.id}
                onClick={() => replayHistory(entry)}
                className="w-full flex items-center gap-3 p-3 bg-card border border-border rounded-lg hover:border-primary/50 transition-colors text-left"
              >
                <div className="flex-1 min-w-0">
                  <p className="text-xs font-medium truncate">{entry.goal}</p>
                  <p className="text-[10px] text-muted-foreground">
                    {entry.date} · Winner: <span className="text-amber-500 font-medium">{entry.winner}</span>
                  </p>
                </div>
                <ChevronRight className="h-4 w-4 text-muted-foreground shrink-0" />
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
