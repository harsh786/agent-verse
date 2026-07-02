/**
 * SimulationPage — world-class Simulation Studio.
 *
 * Uses the REAL simulation engine via SSE stream:
 *   POST /enterprise/simulation/stream
 *
 * Features:
 *  - Mock tool builder (load available tools, toggle + set response JSON)
 *  - Governance policy preview (debounced, /governance/simulate)
 *  - Live step feed via SSE with cost increments + mock hit badges
 *  - Run history (last 5 runs in component state)
 *  - Export steps as JSON
 *  - Cost summary card
 */
import { useState, useCallback, useRef, useEffect, useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  Play, Square, Download, Loader2, CheckCircle2, XCircle,
  AlertTriangle, ChevronDown, ChevronUp, Zap,
  Clock, FlaskConical, Wrench,
} from "lucide-react";
import { simulationApi, type SimulationSummary } from "@/lib/api/client";
import { useAuthStore } from "@/stores/auth";
import { Skeleton } from "@/components/ui/Skeleton";
import { toast } from "@/stores/toast";

const API_BASE = (import.meta.env.VITE_API_URL as string | undefined) ?? "http://localhost:8000";

// ── Types ─────────────────────────────────────────────────────────────────────

interface SimStep {
  number: number;
  description: string;
  status: "pending" | "running" | "complete" | "failed";
  toolCalled?: string;
  serverId?: string;
  mockHit?: boolean;
  output?: string;
  costIncrement?: number;
}

interface RunSummary {
  runId: string;
  totalSteps: number;
  totalCost: number;
  usedRealLlm: boolean;
  finalStatus: string;
  durationMs: number;
}

interface HistoryEntry {
  goal: string;
  totalCost: number;
  status: string;
  timestamp: Date;
  runId: string;
  steps: SimStep[];
}

// ── Governance preview ────────────────────────────────────────────────────────

function GovernancePreview({ goal }: { goal: string }) {
  const [data, setData] = useState<{ summary: SimulationSummary; policy_checks: Array<{ tool: string; result: string }> } | null>(null);
  const [loading, setLoading] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!goal.trim()) { setData(null); return; }
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(async () => {
      setLoading(true);
      try {
        const res = await simulationApi.runGovernance(goal);
        setData(res);
      } catch { /* silent */ }
      finally { setLoading(false); }
    }, 1000);
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current); };
  }, [goal]);

  if (!goal.trim()) return null;

  return (
    <div className="bg-card border border-border rounded-xl p-4 space-y-3">
      <div className="flex items-center gap-2">
        <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">Policy Preview</h3>
        {loading && <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />}
      </div>
      {data && (
        <>
          {data.summary.would_block_execution ? (
            <div className="flex items-center gap-2 text-xs text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-900/20 rounded-lg px-3 py-2">
              <XCircle className="h-4 w-4 shrink-0" /> Execution would be blocked
            </div>
          ) : (
            <div className="flex items-center gap-2 text-xs text-green-600 dark:text-green-400 bg-green-50 dark:bg-green-900/20 rounded-lg px-3 py-2">
              <CheckCircle2 className="h-4 w-4 shrink-0" /> Execution allowed
            </div>
          )}
          {(data.summary.denied_tools ?? []).length > 0 && (
            <div className="text-xs text-muted-foreground">
              <span className="font-medium text-red-500">Denied:</span> {data.summary.denied_tools.join(", ")}
            </div>
          )}
          {(data.summary.requires_approval ?? []).length > 0 && (
            <div className="text-xs text-muted-foreground">
              <span className="font-medium text-amber-500">Requires approval:</span> {data.summary.requires_approval.join(", ")}
            </div>
          )}
          {(data.summary.hitl_approvals_needed ?? 0) > 0 && (
            <div className="text-xs text-amber-600 dark:text-amber-400">
              {data.summary.hitl_approvals_needed} HITL approval(s) needed
            </div>
          )}
        </>
      )}
    </div>
  );
}

// ── Mock tool builder ─────────────────────────────────────────────────────────

function MockToolBuilder({
  mockTools,
  onChange,
}: {
  mockTools: Record<string, string>;
  onChange: (updated: Record<string, string>) => void;
}) {
  const [search, setSearch] = useState("");
  const [open, setOpen] = useState(false);
  const [manualKey, setManualKey] = useState("");
  const [manualVal, setManualVal] = useState("");

  const { data: toolData, isLoading } = useQuery({
    queryKey: ["simulation-tools"],
    queryFn: () => simulationApi.getAvailableTools(),
    staleTime: 300_000,
    enabled: open,
  });
  const tools = toolData?.tools ?? [];

  const filteredTools = useMemo(
    () => tools.filter((t) => !search || t.name.toLowerCase().includes(search.toLowerCase())),
    [tools, search]
  );

  const toggle = (name: string) => {
    if (mockTools[name] !== undefined) {
      const next = { ...mockTools };
      delete next[name];
      onChange(next);
    } else {
      onChange({ ...mockTools, [name]: '{"result": "mocked response"}' });
    }
  };

  const addManual = () => {
    if (!manualKey.trim()) return;
    onChange({ ...mockTools, [manualKey.trim()]: manualVal || '{}' });
    setManualKey(""); setManualVal("");
  };

  const mockedCount = Object.keys(mockTools).length;

  return (
    <div className="bg-card border border-border rounded-xl overflow-hidden">
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between px-4 py-3 hover:bg-muted/30 transition-colors"
        aria-expanded={open}
      >
        <div className="flex items-center gap-2 text-sm font-semibold">
          <Wrench className="h-4 w-4 text-primary" />
          Mock Tools
          {mockedCount > 0 && (
            <span className="text-[10px] bg-primary text-primary-foreground px-2 py-0.5 rounded-full">{mockedCount} mocked</span>
          )}
        </div>
        {open ? <ChevronUp className="h-4 w-4 text-muted-foreground" /> : <ChevronDown className="h-4 w-4 text-muted-foreground" />}
      </button>

      {open && (
        <div className="border-t border-border p-4 space-y-4">
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search available tools…"
            className="w-full px-3 py-2 text-sm border border-input rounded-lg bg-background focus:outline-none focus:ring-1 focus:ring-primary"
          />

          {isLoading && <Skeleton className="h-20" />}
          {!isLoading && filteredTools.length > 0 && (
            <div className="space-y-2 max-h-48 overflow-y-auto">
              {filteredTools.map((t) => {
                const isMocked = mockTools[t.name] !== undefined;
                return (
                  <div key={t.name} className="space-y-1.5">
                    <div className="flex items-center gap-2">
                      <input
                        type="checkbox"
                        checked={isMocked}
                        onChange={() => toggle(t.name)}
                        className="h-4 w-4 accent-primary"
                        id={`mock-${t.name}`}
                      />
                      <label htmlFor={`mock-${t.name}`} className="text-xs font-mono cursor-pointer flex-1 truncate">
                        {t.name}
                        {t.server_id && <span className="text-muted-foreground ml-1">({t.server_id})</span>}
                      </label>
                    </div>
                    {isMocked && (
                      <textarea
                        value={mockTools[t.name]}
                        onChange={(e) => onChange({ ...mockTools, [t.name]: e.target.value })}
                        rows={2}
                        className="w-full px-2 py-1.5 text-xs font-mono border border-input rounded bg-muted/30 focus:outline-none focus:ring-1 focus:ring-primary resize-none ml-6"
                        placeholder='{"result": "mock response"}'
                        aria-label={`Mock response for ${t.name}`}
                      />
                    )}
                  </div>
                );
              })}
            </div>
          )}
          {!isLoading && tools.length === 0 && (
            <p className="text-xs text-muted-foreground italic">No tools available — backend may not be connected.</p>
          )}

          {/* Manual entry */}
          <div className="border-t border-border pt-3 space-y-2">
            <p className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wide">Add Manually</p>
            <div className="flex gap-2">
              <input value={manualKey} onChange={(e) => setManualKey(e.target.value)} placeholder="tool_name"
                className="flex-1 px-2 py-1.5 text-xs font-mono border border-input rounded bg-background focus:outline-none focus:ring-1 focus:ring-primary" />
              <button onClick={addManual} disabled={!manualKey.trim()} className="px-3 py-1.5 text-xs bg-primary text-primary-foreground rounded hover:opacity-90 disabled:opacity-50">Add</button>
            </div>
            {manualKey && (
              <textarea value={manualVal} onChange={(e) => setManualVal(e.target.value)} rows={2}
                className="w-full px-2 py-1.5 text-xs font-mono border border-input rounded bg-muted/30 focus:outline-none focus:ring-1 focus:ring-primary resize-none"
                placeholder='{"result": "mock response"}' />
            )}
          </div>

          {mockedCount > 0 && (
            <button onClick={() => onChange({})} className="text-xs text-muted-foreground hover:text-destructive transition-colors">
              Clear all mocks
            </button>
          )}
        </div>
      )}
    </div>
  );
}

// ── Step card ─────────────────────────────────────────────────────────────────

function StepCard({ step }: { step: SimStep }) {
  const [expanded, setExpanded] = useState(false);
  const StatusIcon =
    step.status === "complete" ? CheckCircle2 :
    step.status === "failed"   ? XCircle :
    step.status === "running"  ? Loader2 :
    AlertTriangle;
  const statusColor =
    step.status === "complete" ? "text-green-500" :
    step.status === "failed"   ? "text-red-500" :
    step.status === "running"  ? "text-blue-500" :
    "text-muted-foreground/40";

  return (
    <div className="bg-card border border-border rounded-xl overflow-hidden" data-testid="simulation-step">
      <div className="flex items-center gap-3 px-4 py-2.5">
        <StatusIcon className={`h-4 w-4 shrink-0 ${statusColor} ${step.status === "running" ? "animate-spin" : ""}`} aria-hidden="true" />
        <span className="text-[10px] text-muted-foreground shrink-0 font-mono w-6">S{step.number}</span>
        <p className="text-sm flex-1 truncate">{step.description}</p>
        <div className="flex items-center gap-2 shrink-0">
          {step.mockHit && (
            <span className="text-[10px] bg-violet-100 text-violet-700 dark:bg-violet-900/30 dark:text-violet-300 px-1.5 py-0.5 rounded font-medium">
              🎭 mocked
            </span>
          )}
          {step.toolCalled && (
            <span className="text-[10px] bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300 px-1.5 py-0.5 rounded font-mono">
              {step.toolCalled}
            </span>
          )}
          {step.costIncrement != null && step.costIncrement > 0 && (
            <span className="text-[10px] text-emerald-600 dark:text-emerald-400 font-mono">
              +${step.costIncrement.toFixed(4)}
            </span>
          )}
          {step.output && (
            <button onClick={() => setExpanded((v) => !v)} className="text-muted-foreground hover:text-foreground">
              {expanded ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
            </button>
          )}
        </div>
      </div>
      {expanded && step.output && (
        <div className="px-4 pb-3 border-t border-border bg-muted/20">
          <pre className="text-xs font-mono whitespace-pre-wrap text-muted-foreground max-h-32 overflow-auto">
            {step.output}
          </pre>
        </div>
      )}
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export function SimulationPage() {
  const navigate = useNavigate();
  const apiKey = useAuthStore((s) => s.apiKey);
  const [goal, setGoal] = useState("");
  const [agentId, setAgentId] = useState("");
  const [mockTools, setMockTools] = useState<Record<string, string>>({});
  const [status, setStatus] = useState<"idle" | "running" | "complete" | "failed">("idle");
  const [steps, setSteps] = useState<SimStep[]>([]);
  const [summary, setSummary] = useState<RunSummary | null>(null);
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [_startTime, setStartTime] = useState<number>(0);
  const abortRef = useRef<AbortController | null>(null);

  const isRunning = status === "running";

  const stopSimulation = useCallback(() => {
    abortRef.current?.abort();
    setStatus("failed");
  }, []);

  const runSimulation = useCallback(async () => {
    if (!goal.trim()) return;
    abortRef.current?.abort();
    const abort = new AbortController();
    abortRef.current = abort;

    setStatus("running");
    setSteps([]);
    setSummary(null);
    const start = Date.now();
    setStartTime(start);

    try {
      const res = await fetch(`${API_BASE}/enterprise/simulation/stream`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Accept": "text/event-stream",
          "X-API-Key": sessionStorage.getItem("av_api_key") ?? localStorage.getItem("av_api_key") ?? apiKey ?? "",
        },
        body: JSON.stringify({
          goal: goal.trim(),
          mock_tools: mockTools,
          agent_id: agentId.trim() || undefined,
          max_steps: 15,
        }),
        signal: abort.signal,
      });

      if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const frames = buffer.split("\n\n");
        buffer = frames.pop() ?? "";

        for (const frame of frames) {
          for (const line of frame.split("\n")) {
            const data = line.startsWith("data: ") ? line.slice(6).trim() : null;
            if (!data) continue;
            try {
              const evt = JSON.parse(data) as Record<string, unknown>;
              const type = evt.type as string;

              if (type === "step_started") {
                setSteps((prev) => [...prev, {
                  number: evt.step_number as number ?? prev.length + 1,
                  description: evt.description as string ?? `Step ${prev.length + 1}`,
                  status: "running",
                }]);
              } else if (type === "step_completed") {
                setSteps((prev) =>
                  prev.map((s) =>
                    s.number === (evt.step_number as number) || s.status === "running"
                      ? {
                          ...s,
                          status: "complete",
                          output: evt.output as string | undefined,
                          toolCalled: evt.tool_called as string | undefined,
                          serverId: evt.server_id as string | undefined,
                          mockHit: evt.mock_hit as boolean | undefined,
                          costIncrement: evt.cost_increment as number | undefined,
                        }
                      : s
                  )
                );
              } else if (type === "simulation_complete") {
                const durationMs = Date.now() - start;
                const summ: RunSummary = {
                  runId: evt.run_id as string ?? "unknown",
                  totalSteps: evt.total_steps as number ?? 0,
                  totalCost: evt.total_cost as number ?? 0,
                  usedRealLlm: evt.used_real_llm as boolean ?? false,
                  finalStatus: evt.final_status as string ?? "complete",
                  durationMs,
                };
                setSummary(summ);
                setStatus("complete");
                setSteps((prev) =>
                  prev.map((s) => s.status === "running" ? { ...s, status: "complete" } : s)
                );
                setHistory((h) => [{
                  goal: goal.slice(0, 60),
                  totalCost: summ.totalCost,
                  status: summ.finalStatus,
                  timestamp: new Date(),
                  runId: summ.runId,
                  steps: [],
                }, ...h].slice(0, 5));
              } else if (type === "simulation_error") {
                toast({ kind: "error", message: evt.message as string ?? "Simulation failed" });
                setStatus("failed");
              }
            } catch { /* ignore */ }
          }
        }
      }
      if (status === "running") setStatus("complete");
    } catch (err) {
      if ((err as Error).name !== "AbortError") {
        toast({ kind: "error", message: `Simulation failed: ${String(err)}` });
        setStatus("failed");
      }
    }
  }, [goal, mockTools, agentId, apiKey]);

  const exportSteps = () => {
    const blob = new Blob([JSON.stringify(steps, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = `simulation-steps-${Date.now()}.json`; a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="space-y-6 max-w-6xl">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold flex items-center gap-2">
          <FlaskConical className="h-6 w-6 text-primary" aria-hidden="true" />
          Simulation Studio
        </h1>
        <p className="text-sm text-muted-foreground mt-0.5">
          Run agent goals safely with mock tools — stream live execution steps, estimate cost, validate policies
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-5 gap-6">
        {/* Left: Config (2/5) */}
        <div className="lg:col-span-2 space-y-4">
          {/* Goal */}
          <div className="bg-card border border-border rounded-xl p-4 space-y-3">
            <h3 className="text-sm font-semibold">Goal</h3>
            <textarea
              value={goal}
              onChange={(e) => setGoal(e.target.value)}
              placeholder="Describe the goal to simulate, e.g. 'Search Jira for all open P1 bugs and create a summary report'"
              rows={4}
              className="w-full px-3 py-2 text-sm border border-input rounded-lg bg-background focus:outline-none focus:ring-2 focus:ring-primary resize-none"
              aria-label="Simulation goal"
            />
            <div>
              <label className="block text-xs font-medium mb-1" htmlFor="sim-agent">Agent ID (optional)</label>
              <input
                id="sim-agent"
                value={agentId}
                onChange={(e) => setAgentId(e.target.value)}
                placeholder="Use a specific agent's config"
                className="w-full px-3 py-2 text-sm border border-input rounded-lg bg-background focus:outline-none focus:ring-1 focus:ring-primary"
              />
            </div>
          </div>

          {/* Mock tools */}
          <MockToolBuilder mockTools={mockTools} onChange={setMockTools} />

          {/* Governance preview */}
          <GovernancePreview goal={goal} />

          {/* Run button */}
          {isRunning ? (
            <button
              onClick={stopSimulation}
              className="w-full flex items-center justify-center gap-2 py-3 bg-red-600 text-white text-sm font-medium rounded-xl hover:bg-red-700 transition-colors"
            >
              <Square className="h-4 w-4" /> Stop Simulation
            </button>
          ) : (
            <button
              onClick={runSimulation}
              disabled={!goal.trim()}
              className="w-full flex items-center justify-center gap-2 py-3 bg-primary text-primary-foreground text-sm font-medium rounded-xl hover:opacity-90 disabled:opacity-50 transition-opacity"
            >
              <Play className="h-4 w-4" /> Run Simulation
            </button>
          )}
        </div>

        {/* Right: Results (3/5) */}
        <div className="lg:col-span-3 space-y-4">
          {/* Status bar */}
          <div className="bg-card border border-border rounded-xl px-4 py-3 flex items-center gap-3">
            {isRunning && <Loader2 className="h-4 w-4 animate-spin text-blue-500" />}
            {status === "complete" && <CheckCircle2 className="h-4 w-4 text-green-500" />}
            {status === "failed" && <XCircle className="h-4 w-4 text-red-500" />}
            {status === "idle" && <FlaskConical className="h-4 w-4 text-muted-foreground" />}
            <span className="text-sm font-medium">
              {isRunning ? "Running simulation…" :
               status === "complete" ? `Complete${summary ? ` · $${summary.totalCost.toFixed(4)}` : ""}` :
               status === "failed" ? "Simulation failed" :
               "Ready to simulate"}
            </span>
            {summary && (
              <>
                <span className={`ml-auto text-[10px] px-2 py-0.5 rounded-full font-medium ${summary.usedRealLlm ? "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300" : "bg-muted text-muted-foreground"}`}>
                  {summary.usedRealLlm ? "Real LLM" : "Stub mode"}
                </span>
                <button onClick={exportSteps} className="text-muted-foreground hover:text-foreground" title="Export steps as JSON">
                  <Download className="h-4 w-4" />
                </button>
              </>
            )}
          </div>

          {/* Live steps */}
          {steps.length === 0 && status === "idle" && (
            <div className="bg-card border border-border rounded-xl flex items-center justify-center py-16 text-muted-foreground text-center">
              <div>
                <FlaskConical className="h-10 w-10 mx-auto mb-2 opacity-20" />
                <p className="text-sm">Configure a goal and click "Run Simulation"</p>
                <p className="text-xs mt-1 opacity-60">Steps will stream live here</p>
              </div>
            </div>
          )}
          {steps.length > 0 && (
            <div className="space-y-2">
              {steps.map((s) => <StepCard key={s.number} step={s} />)}
            </div>
          )}

          {/* Summary card */}
          {summary && (
            <div className="bg-card border border-border rounded-xl p-5 space-y-4">
              <h3 className="text-sm font-semibold flex items-center gap-2">
                <CheckCircle2 className="h-4 w-4 text-green-500" /> Simulation Complete
              </h3>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
                <div className="bg-muted/30 rounded-lg p-2.5 text-center">
                  <p className="text-muted-foreground">Steps</p>
                  <p className="text-lg font-bold">{summary.totalSteps}</p>
                </div>
                <div className="bg-muted/30 rounded-lg p-2.5 text-center">
                  <p className="text-muted-foreground">Cost</p>
                  <p className="text-lg font-bold">${summary.totalCost.toFixed(4)}</p>
                </div>
                <div className="bg-muted/30 rounded-lg p-2.5 text-center">
                  <p className="text-muted-foreground">Duration</p>
                  <p className="text-lg font-bold">{(summary.durationMs / 1000).toFixed(1)}s</p>
                </div>
                <div className="bg-muted/30 rounded-lg p-2.5 text-center">
                  <p className="text-muted-foreground">Status</p>
                  <p className="text-sm font-bold capitalize">{summary.finalStatus}</p>
                </div>
              </div>
              <div className="flex items-center gap-2 bg-muted/30 rounded px-3 py-2">
                <p className="text-[10px] text-muted-foreground">Run ID</p>
                <code className="text-xs font-mono flex-1 truncate">{summary.runId}</code>
              </div>
              <div className="flex gap-2">
                <button
                  onClick={() => navigate(`/goals?goal=${encodeURIComponent(goal)}`)}
                  className="flex-1 flex items-center justify-center gap-1.5 py-2 border border-border text-xs rounded-lg hover:bg-muted/60"
                >
                  <Zap className="h-3.5 w-3.5" /> Run Live
                </button>
                <button onClick={exportSteps} className="flex items-center justify-center gap-1.5 px-4 py-2 border border-border text-xs rounded-lg hover:bg-muted/60">
                  <Download className="h-3.5 w-3.5" /> Export JSON
                </button>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Run history */}
      {history.length > 0 && (
        <div className="bg-card border border-border rounded-xl overflow-hidden">
          <button
            onClick={() => setHistoryOpen((v) => !v)}
            className="w-full flex items-center justify-between px-4 py-3 hover:bg-muted/30 text-sm font-semibold"
            aria-expanded={historyOpen}
          >
            <div className="flex items-center gap-2">
              <Clock className="h-4 w-4 text-primary" /> Run History ({history.length})
            </div>
            {historyOpen ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
          </button>
          {historyOpen && (
            <div className="border-t border-border divide-y divide-border">
              {history.map((h, i) => (
                <div key={i} className="flex items-center gap-3 px-4 py-3 text-xs hover:bg-muted/20">
                  <span className={`px-1.5 py-0.5 rounded-full font-medium ${h.status === "complete" ? "bg-green-100 text-green-700" : "bg-red-100 text-red-700"}`}>
                    {h.status}
                  </span>
                  <span className="flex-1 truncate text-muted-foreground">{h.goal}</span>
                  <span className="text-emerald-600 dark:text-emerald-400 font-mono">${h.totalCost.toFixed(4)}</span>
                  <span className="text-muted-foreground">{h.timestamp.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
                  <button
                    onClick={() => { setGoal(h.goal); window.scrollTo(0, 0); }}
                    className="text-primary hover:underline text-[10px]"
                  >
                    Re-run
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
export default SimulationPage;
