import { useState, useRef } from "react";
import type { JSX } from "react";
import { useQuery } from "@tanstack/react-query";
import { FlaskConical, Shield, Play, Plus, Trash2, BarChart3 } from "lucide-react";
import { agentsApi, simulationApi, goalsApi, evalSuitesApi, playgroundApi, API_BASE } from "@/lib/api/client";
import { useAuthStore } from "@/stores/auth";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { ThemedBarChart } from "@/components/charts";
import { toast } from "@/stores/toast";

// ── Types ─────────────────────────────────────────────────────────────────────

interface MockTool {
  name: string;
  output: string;
}

interface SimStep {
  step: number;
  description: string;
  tool?: string;
  output?: string;
  status: "pending" | "running" | "done" | "error";
}

// ── MockToolsBuilder ──────────────────────────────────────────────────────────

function MockToolsBuilder({
  tools,
  onChange,
}: {
  tools: MockTool[];
  onChange: (tools: MockTool[]) => void;
}): JSX.Element {
  const addTool = (): void =>
    onChange([...tools, { name: "", output: '{"result": "mocked"}' }]);

  const removeTool = (i: number): void =>
    onChange(tools.filter((_, idx) => idx !== i));

  const updateTool = (i: number, field: keyof MockTool, value: string): void =>
    onChange(tools.map((t, idx) => (idx === i ? { ...t, [field]: value } : t)));

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <p className="text-xs font-medium text-muted-foreground">Mock Tools</p>
        <button
          onClick={addTool}
          className="flex items-center gap-1 text-xs px-2 py-1 border border-border rounded hover:bg-muted transition-colors"
        >
          <Plus className="h-3 w-3" /> Add Tool
        </button>
      </div>
      {tools.map((tool, i) => (
        <div key={i} className="flex gap-2 items-start">
          <div className="flex-1 space-y-1">
            <input
              value={tool.name}
              onChange={(e) => updateTool(i, "name", e.target.value)}
              placeholder="tool_name"
              className="w-full border border-input rounded px-2 py-1.5 text-xs font-mono bg-background"
            />
            <textarea
              value={tool.output}
              onChange={(e) => updateTool(i, "output", e.target.value)}
              placeholder='{"result": "mock output"}'
              rows={2}
              className="w-full border border-input rounded px-2 py-1.5 text-xs font-mono bg-background resize-none"
            />
          </div>
          <button
            onClick={() => removeTool(i)}
            className="p-1.5 text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20 rounded transition-colors mt-0.5"
            aria-label={`Remove tool ${tool.name || String(i)}`}
          >
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        </div>
      ))}
      {tools.length === 0 && (
        <p className="text-xs text-muted-foreground italic">No mock tools — the agent will use real tools.</p>
      )}
    </div>
  );
}

// ── StepTimeline ──────────────────────────────────────────────────────────────

function StepTimeline({ steps }: { steps: SimStep[] }): JSX.Element | null {
  if (steps.length === 0) return null;
  return (
    <div className="mt-4 space-y-2">
      {steps.map((step) => (
        <div
          key={step.step}
          className={`flex gap-3 rounded-lg px-4 py-3 border transition-all ${
            step.status === "running"
              ? "border-blue-300 bg-blue-50/40 dark:bg-blue-900/20"
              : step.status === "done"
              ? "border-green-300/50 bg-green-50/30 dark:bg-green-900/10"
              : step.status === "error"
              ? "border-red-300/50 bg-red-50/30 dark:bg-red-900/10"
              : "border-border bg-card"
          }`}
        >
          <div
            className={`w-6 h-6 rounded-full flex items-center justify-center flex-shrink-0 text-xs font-bold mt-0.5 ${
              step.status === "done"
                ? "bg-green-500 text-white"
                : step.status === "running"
                ? "bg-blue-500 text-white animate-pulse"
                : step.status === "error"
                ? "bg-red-500 text-white"
                : "bg-muted text-muted-foreground"
            }`}
          >
            {step.step}
          </div>
          <div className="min-w-0 flex-1">
            <p className="text-sm font-medium">{step.description}</p>
            {step.tool && (
              <p className="text-xs text-muted-foreground font-mono mt-0.5">Tool: {step.tool}</p>
            )}
            {step.output && (
              <pre className="mt-1 text-xs bg-background border border-border rounded px-2 py-1 overflow-auto max-h-20">
                {step.output}
              </pre>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

// ── Pre-Flight Tab ────────────────────────────────────────────────────────────

function PreFlightTab(): JSX.Element {
  const [goal, setGoal] = useState("");
  const [agentId, setAgentId] = useState("");
  const [govResult, setGovResult] = useState<Record<string, unknown> | null>(null);
  const [planResult, setPlanResult] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState<"gov" | "plan" | null>(null);

  const { data: agents = [] } = useQuery({
    queryKey: ["agents"],
    queryFn: () => agentsApi.list(),
  });

  const runGovCheck = async (): Promise<void> => {
    if (!goal.trim()) return;
    setLoading("gov");
    try {
      const result = await simulationApi.runGovernance(goal);
      setGovResult(result as unknown as Record<string, unknown>);
    } catch (e) {
      toast({ kind: "error", message: `Failed: governance check. ${String(e)}` });
    } finally {
      setLoading(null);
    }
  };

  const runDryRun = async (): Promise<void> => {
    if (!goal.trim()) return;
    setLoading("plan");
    try {
      const result = await goalsApi.submit({ goal, agent_id: agentId || undefined, dry_run: true });
      setPlanResult(result as unknown as Record<string, unknown>);
    } catch (e) {
      toast({ kind: "error", message: `Failed: dry-run plan. ${String(e)}` });
    } finally {
      setLoading(null);
    }
  };

  return (
    <div className="space-y-5">
      <div className="space-y-3">
        <div>
          <label className="block text-xs font-medium text-muted-foreground mb-1">Goal</label>
          <textarea
            value={goal}
            onChange={(e) => setGoal(e.target.value)}
            placeholder="Describe the goal to pre-flight check…"
            rows={3}
            className="w-full border border-input rounded-lg px-3 py-2.5 text-sm bg-background resize-none"
          />
        </div>
        <div>
          <label className="block text-xs font-medium text-muted-foreground mb-1">Agent (optional)</label>
          <select
            value={agentId}
            onChange={(e) => setAgentId(e.target.value)}
            className="w-full max-w-xs border border-input rounded-lg px-3 py-2 text-sm bg-background"
          >
            <option value="">— Any agent —</option>
            {agents.map((a) => (
              <option key={a.agent_id} value={a.agent_id}>{a.name}</option>
            ))}
          </select>
        </div>
      </div>

      <div className="flex gap-3 flex-wrap">
        <button
          onClick={() => void runGovCheck()}
          disabled={!goal.trim() || loading !== null}
          className="flex items-center gap-2 px-4 py-2 bg-primary text-primary-foreground rounded-lg text-sm hover:opacity-90 disabled:opacity-50"
        >
          <Shield className="h-4 w-4" />
          {loading === "gov" ? "Checking…" : "Run Governance Check"}
        </button>
        <button
          onClick={() => void runDryRun()}
          disabled={!goal.trim() || loading !== null}
          className="flex items-center gap-2 px-4 py-2 border border-border rounded-lg text-sm hover:bg-muted disabled:opacity-50"
        >
          <Play className="h-4 w-4" />
          {loading === "plan" ? "Planning…" : "Preview Plan (Dry Run)"}
        </button>
      </div>

      {govResult && (
        <div className="bg-card border border-border rounded-xl p-5 space-y-3">
          <h3 className="text-sm font-semibold flex items-center gap-2">
            <Shield className="h-4 w-4" /> Governance Analysis
          </h3>
          {govResult.summary != null && typeof govResult.summary === "object" && !Array.isArray(govResult.summary) && (
            <div className="grid grid-cols-2 gap-3 text-sm">
              {Object.entries(govResult.summary as Record<string, string | number | boolean>).map(([k, v]) => (
                <div key={k} className="flex justify-between bg-muted/40 rounded-lg px-3 py-2">
                  <span className="text-muted-foreground text-xs">{k.replace(/_/g, " ")}</span>
                  <span className="font-medium text-xs">{String(v)}</span>
                </div>
              ))}
            </div>
          )}
          {Array.isArray(govResult.policy_checks) && (govResult.policy_checks as Array<{ tool: string; result: string }>).length > 0 && (
            <div className="space-y-1">
              <p className="text-xs font-medium text-muted-foreground">Policy Checks</p>
              {(govResult.policy_checks as Array<{ tool: string; result: string }>).map((pc, i) => (
                <div key={i} className="flex items-center gap-2 text-xs">
                  <span
                    className={`w-2 h-2 rounded-full flex-shrink-0 ${
                      pc.result === "allow" ? "bg-green-500" : "bg-red-500"
                    }`}
                  />
                  <span className="font-mono">{pc.tool}</span>
                  <span className="text-muted-foreground">→ {pc.result}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {planResult && (
        <div className="bg-card border border-border rounded-xl p-5 space-y-3">
          <h3 className="text-sm font-semibold flex items-center gap-2">
            <Play className="h-4 w-4" /> Plan Preview
          </h3>
          <pre className="text-xs bg-muted/50 rounded-lg px-3 py-2.5 overflow-auto max-h-48">
            {JSON.stringify(planResult, null, 2)}
          </pre>
        </div>
      )}
    </div>
  );
}

// ── Live Sim Tab ──────────────────────────────────────────────────────────────

function LiveSimTab(): JSX.Element {
  const apiKey = useAuthStore((s) => s.apiKey);
  const [goal, setGoal] = useState("");
  const [mockTools, setMockTools] = useState<MockTool[]>([]);
  const [steps, setSteps] = useState<SimStep[]>([]);
  const [running, setRunning] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  const runSim = async (): Promise<void> => {
    if (!goal.trim()) return;
    setRunning(true);
    setSteps([]);

    const mockToolsMap: Record<string, string> = Object.fromEntries(
      mockTools.map((t) => [t.name, t.output])
    );

    const abort = new AbortController();
    abortRef.current = abort;

    try {
      const res = await fetch(`${API_BASE}/enterprise/simulation/stream`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-API-Key": apiKey,
        },
        body: JSON.stringify({ goal, mock_tools: mockToolsMap }),
        signal: abort.signal,
      });

      if (!res.ok || !res.body) {
        // Fallback to batch mode
        const result = await playgroundApi.simulate(goal, mockToolsMap);
        const fallbackSteps: SimStep[] = (result.steps ?? []).map((s, i) => ({
          step: i + 1,
          description: s.step,
          tool: s.tool,
          output: s.output,
          status: "done" as const,
        }));
        setSteps(fallbackSteps);
        return;
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let stepCount = 0;

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n\n");
        buffer = lines.pop() ?? "";

        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          try {
            const event = JSON.parse(line.slice(6)) as {
              step?: string;
              tool?: string;
              output?: string;
              status?: string;
            };
            stepCount++;
            const simStep: SimStep = {
              step: stepCount,
              description: event.step ?? `Step ${stepCount}`,
              tool: event.tool,
              output: event.output,
              status: event.status === "error" ? "error" : "done",
            };
            setSteps((prev) => [...prev, simStep]);
          } catch {
            // Skip malformed SSE events
          }
        }
      }
    } catch (e) {
      if ((e as Error).name !== "AbortError") {
        toast({ kind: "error", message: `Failed: simulation. ${String(e)}` });
      }
    } finally {
      setRunning(false);
    }
  };

  const stopSim = (): void => {
    abortRef.current?.abort();
    setRunning(false);
  };

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
        <div className="space-y-3">
          <div>
            <label className="block text-xs font-medium text-muted-foreground mb-1">Goal</label>
            <textarea
              value={goal}
              onChange={(e) => setGoal(e.target.value)}
              placeholder="Goal to simulate…"
              rows={3}
              className="w-full border border-input rounded-lg px-3 py-2.5 text-sm bg-background resize-none"
            />
          </div>
          <MockToolsBuilder tools={mockTools} onChange={setMockTools} />
          <div className="flex gap-2">
            <button
              onClick={() => void runSim()}
              disabled={!goal.trim() || running}
              className="flex items-center gap-2 px-4 py-2 bg-primary text-primary-foreground rounded-lg text-sm hover:opacity-90 disabled:opacity-50"
            >
              <Play className="h-4 w-4" /> {running ? "Simulating…" : "Run Simulation"}
            </button>
            {running && (
              <button
                onClick={stopSim}
                className="px-4 py-2 border border-red-300 text-red-600 rounded-lg text-sm hover:bg-red-50 dark:hover:bg-red-900/20"
              >
                Stop
              </button>
            )}
          </div>
        </div>

        <div>
          <p className="text-xs font-medium text-muted-foreground mb-2">Simulation Steps</p>
          {steps.length === 0 && !running ? (
            <div className="border-2 border-dashed border-border rounded-lg h-32 flex items-center justify-center text-xs text-muted-foreground">
              Run a simulation to see steps here
            </div>
          ) : (
            <StepTimeline steps={steps} />
          )}
          {running && steps.length === 0 && (
            <div className="flex items-center gap-2 text-sm text-muted-foreground mt-4">
              <span className="animate-pulse">Connecting to simulation engine…</span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Score Tab ─────────────────────────────────────────────────────────────────

function ScoreTab(): JSX.Element {
  const [agentId, setAgentId] = useState("");

  const { data: agents = [] } = useQuery({
    queryKey: ["agents"],
    queryFn: () => agentsApi.list(),
  });

  const { data: suiteResults, isLoading: resultsLoading } = useQuery({
    queryKey: ["eval-suite-results", agentId],
    queryFn: async () => {
      const suites = await evalSuitesApi.listSuites();
      if (suites.length === 0) return [];
      return evalSuitesApi.getSuiteResults(suites[0].suite_id);
    },
  });

  const chartData = (suiteResults ?? []).slice(-10).map((r) => ({
    run: r.run_id.slice(0, 8),
    score: r.overall_score,
    passed: r.passed,
    failed: r.failed,
  }));

  return (
    <div className="space-y-5">
      <div>
        <label className="block text-xs font-medium text-muted-foreground mb-1">Agent</label>
        <select
          value={agentId}
          onChange={(e) => setAgentId(e.target.value)}
          className="w-full max-w-xs border border-input rounded-lg px-3 py-2 text-sm bg-background"
        >
          <option value="">— All agents —</option>
          {agents.map((a) => (
            <option key={a.agent_id} value={a.agent_id}>{a.name}</option>
          ))}
        </select>
      </div>

      <div className="bg-card border border-border rounded-xl p-5">
        <h3 className="text-sm font-semibold mb-4 flex items-center gap-2">
          <BarChart3 className="h-4 w-4" /> Eval Suite Results
        </h3>
        {resultsLoading ? (
          <Skeleton className="h-40 w-full" />
        ) : chartData.length === 0 ? (
          <EmptyState
            title="No eval results"
            description="Run an eval suite to see performance history here."
          />
        ) : (
          <ThemedBarChart
            data={chartData}
            bars={[
              { key: "score", label: "Score", color: "#3b82f6" },
              { key: "passed", label: "Passed", color: "#22c55e" },
              { key: "failed", label: "Failed", color: "#ef4444" },
            ]}
            xKey="run"
            height={200}
          />
        )}
      </div>

      <div className="bg-card border border-border rounded-xl p-5">
        <h3 className="text-sm font-semibold mb-2 flex items-center gap-2">
          <Shield className="h-4 w-4" /> Red-Team Status
        </h3>
        <p className="text-xs text-muted-foreground">
          Red-team testing evaluates agent resilience to adversarial inputs, jailbreak attempts, and
          prompt injection. Configure scenarios in the{" "}
          <span className="text-primary">Enterprise</span> section.
        </p>
      </div>
    </div>
  );
}

// ── AgentLabPage ──────────────────────────────────────────────────────────────

type LabTab = "preflight" | "sim" | "score";

export function AgentLabPage(): JSX.Element {
  const [tab, setTab] = useState<LabTab>("preflight");

  return (
    <div className="p-4 md:p-6 max-w-5xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-bold flex items-center gap-2">
          <FlaskConical className="h-6 w-6" /> Agent Lab
        </h1>
        <p className="text-sm text-muted-foreground mt-1">
          Pre-flight governance checks, live simulation, and performance scoring.
        </p>
      </div>

      <div className="flex gap-1 border-b border-border">
        {(
          [
            { key: "preflight", label: "Pre-Flight" },
            { key: "sim", label: "Live Sim" },
            { key: "score", label: "Score" },
          ] as { key: LabTab; label: string }[]
        ).map(({ key, label }) => (
          <button
            key={key}
            role="tab"
            aria-selected={tab === key}
            onClick={() => setTab(key)}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
              tab === key
                ? "border-primary text-primary"
                : "border-transparent text-muted-foreground hover:text-foreground"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === "preflight" && <PreFlightTab />}
      {tab === "sim" && <LiveSimTab />}
      {tab === "score" && <ScoreTab />}
    </div>
  );
}
