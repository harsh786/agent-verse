/**
 * World-class Agent Lab — 4-tab AI experimentation laboratory:
 *  Tab 1: Pre-Flight Check  — governance + safety analysis before running
 *  Tab 2: Live Simulation   — SSE streaming with available-tools picker
 *  Tab 3: Prompt Lab        — PromptOptimizer A/B testing UI
 *  Tab 4: Score & Benchmark — eval suites, red-team, 7-dimension scorecard
 */
import { useState, useRef } from "react";
import type { JSX } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  FlaskConical,
  Shield,
  Play,
  Plus,
  Trash2,
  BarChart3,
  Beaker,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  ChevronDown,
  Crown,
  Swords,
  Zap,
  Square,
} from "lucide-react";
import {
  agentsApi,
  simulationApi,
  goalsApi,
  evalSuitesApi,
  promptVariantsApi,
  redTeamApi,
  API_BASE,
  type PromptVariantItem,
} from "@/lib/api/client";
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
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <p className="text-xs font-medium text-muted-foreground">Custom Mock Tools</p>
        <button
          onClick={() => onChange([...tools, { name: "", output: '{"result": "mocked"}' }])}
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
              onChange={(e) =>
                onChange(tools.map((t, idx) => (idx === i ? { ...t, name: e.target.value } : t)))
              }
              placeholder="tool_name"
              className="w-full border border-input rounded px-2 py-1.5 text-xs font-mono bg-background"
            />
            <textarea
              value={tool.output}
              onChange={(e) =>
                onChange(tools.map((t, idx) => (idx === i ? { ...t, output: e.target.value } : t)))
              }
              placeholder='{"result": "mock output"}'
              rows={2}
              className="w-full border border-input rounded px-2 py-1.5 text-xs font-mono bg-background resize-none"
            />
          </div>
          <button
            onClick={() => onChange(tools.filter((_, idx) => idx !== i))}
            className="p-1.5 text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20 rounded transition-colors mt-0.5"
            aria-label={`Remove tool ${tool.name || String(i)}`}
          >
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        </div>
      ))}
      {tools.length === 0 && (
        <p className="text-xs text-muted-foreground italic">
          No custom tools — agent uses real tools.
        </p>
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
                ? "bg-blue-500 text-foreground animate-pulse"
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

// ── Tab 1: Pre-Flight Check ───────────────────────────────────────────────────

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
      toast({ kind: "error", message: `Governance check failed: ${String(e)}` });
    } finally {
      setLoading(null);
    }
  };

  const runDryRun = async (): Promise<void> => {
    if (!goal.trim()) return;
    setLoading("plan");
    try {
      const result = await goalsApi.submit({
        goal,
        agent_id: agentId || undefined,
        dry_run: true,
      });
      setPlanResult(result as unknown as Record<string, unknown>);
    } catch (e) {
      toast({ kind: "error", message: `Dry-run failed: ${String(e)}` });
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
          <label className="block text-xs font-medium text-muted-foreground mb-1">
            Agent (optional)
          </label>
          <select
            value={agentId}
            onChange={(e) => setAgentId(e.target.value)}
            className="w-full max-w-xs border border-input rounded-lg px-3 py-2 text-sm bg-background"
          >
            <option value="">— Any agent —</option>
            {agents.map((a) => (
              <option key={a.agent_id} value={a.agent_id}>
                {a.name}
              </option>
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
          {govResult.summary != null &&
            typeof govResult.summary === "object" &&
            !Array.isArray(govResult.summary) && (
              <div className="grid grid-cols-2 gap-3">
                {Object.entries(
                  govResult.summary as Record<string, string | number | boolean>
                ).map(([k, v]) => (
                  <div key={k} className="flex justify-between bg-muted/40 rounded-lg px-3 py-2">
                    <span className="text-muted-foreground text-xs">{k.replace(/_/g, " ")}</span>
                    <span className="font-medium text-xs">{String(v)}</span>
                  </div>
                ))}
              </div>
            )}
          {Array.isArray(govResult.policy_checks) &&
            (govResult.policy_checks as Array<{ tool: string; result: string }>).length > 0 && (
              <div className="space-y-1">
                <p className="text-xs font-medium text-muted-foreground">Policy Checks</p>
                {(govResult.policy_checks as Array<{ tool: string; result: string }>).map(
                  (pc, i) => (
                    <div key={i} className="flex items-center gap-2 text-xs">
                      <span
                        className={`w-2 h-2 rounded-full flex-shrink-0 ${
                          pc.result === "allow" ? "bg-green-500" : "bg-red-500"
                        }`}
                      />
                      <span className="font-mono">{pc.tool}</span>
                      <span
                        className={`px-1.5 py-0.5 rounded text-[10px] font-semibold ${
                          pc.result === "allow"
                            ? "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-300"
                            : pc.result === "require_approval"
                            ? "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-300"
                            : "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300"
                        }`}
                      >
                        {pc.result.toUpperCase()}
                      </span>
                    </div>
                  )
                )}
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

// ── Tab 2: Live Simulation ────────────────────────────────────────────────────

function LiveSimTab(): JSX.Element {
  const apiKey = useAuthStore((s) => s.apiKey);
  const [goal, setGoal] = useState("");
  const [mockTools, setMockTools] = useState<MockTool[]>([]);
  const [steps, setSteps] = useState<SimStep[]>([]);
  const [running, setRunning] = useState(false);
  const [sessionCost, setSessionCost] = useState(0);
  const abortRef = useRef<AbortController | null>(null);

  // Load available tools from MCP
  const { data: availableToolsData } = useQuery({
    queryKey: ["available-tools"],
    queryFn: () => simulationApi.getAvailableTools(),
    staleTime: 60_000,
  });
  const availableTools = availableToolsData?.tools ?? [];

  const [checkedTools, setCheckedTools] = useState<Set<string>>(new Set());

  const toggleAvailableTool = (name: string): void => {
    setCheckedTools((prev) => {
      const next = new Set(prev);
      if (next.has(name)) {
        next.delete(name);
      } else {
        next.add(name);
      }
      return next;
    });
  };

  const runSim = async (): Promise<void> => {
    if (!goal.trim()) return;
    setRunning(true);
    setSteps([]);
    setSessionCost(0);

    const mockToolsMap: Record<string, string> = {
      ...Object.fromEntries(
        availableTools
          .filter((t) => checkedTools.has(t.name))
          .map((t) => [t.name, '{"result": "mocked"}'])
      ),
      ...Object.fromEntries(mockTools.map((t) => [t.name, t.output])),
    };

    const abort = new AbortController();
    abortRef.current = abort;

    try {
      const res = await fetch(`${API_BASE}/enterprise/simulation/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-API-Key": apiKey },
        body: JSON.stringify({ goal, mock_tools: mockToolsMap }),
        signal: abort.signal,
      });

      if (!res.ok || !res.body) {
        const result = await simulationApi.run(goal, mockToolsMap);
        const fallbackSteps: SimStep[] = (result.steps ?? []).map((s, i) => ({
          step: i + 1,
          description: String(s.step),
          tool: s.tool || undefined,
          output: s.output || undefined,
          status: "done" as const,
        }));
        setSteps(fallbackSteps);
        return;
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let stepCount = 0;
      let accCost = 0;

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n\n");
        buffer = lines.pop() ?? "";

        for (const chunk of lines) {
          if (!chunk.startsWith("data: ")) continue;
          try {
            const evt = JSON.parse(chunk.slice(6)) as Record<string, unknown>;
            if (evt.type === "step_started") {
              stepCount++;
              const desc = String(evt.description ?? evt.step ?? `Step ${stepCount}`);
              setSteps((prev) => [
                ...prev,
                { step: stepCount, description: desc, status: "running" },
              ]);
            } else if (evt.type === "step_completed") {
              accCost += Number(evt.cost_increment ?? 0);
              setSessionCost(accCost);
              setSteps((prev) =>
                prev.map((s) =>
                  s.step === stepCount
                    ? {
                        ...s,
                        tool: String(evt.tool_called ?? evt.tool ?? "") || undefined,
                        output: String(evt.output ?? ""),
                        status: "done",
                      }
                    : s
                )
              );
            }
          } catch {
            // skip malformed
          }
        }
      }
    } catch (e) {
      if ((e as Error).name !== "AbortError") {
        toast({ kind: "error", message: `Simulation error: ${String(e)}` });
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

          {availableTools.length > 0 && (
            <div>
              <p className="text-xs font-medium text-muted-foreground mb-1.5">Available Tools</p>
              <div className="grid grid-cols-2 gap-1.5 max-h-32 overflow-y-auto">
                {availableTools.map((t) => (
                  <label key={t.name} className="flex items-center gap-1.5 text-xs cursor-pointer">
                    <input
                      type="checkbox"
                      checked={checkedTools.has(t.name)}
                      onChange={() => toggleAvailableTool(t.name)}
                      className="rounded"
                    />
                    <span className="font-mono truncate">{t.name}</span>
                  </label>
                ))}
              </div>
            </div>
          )}

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
                className="flex items-center gap-2 px-4 py-2 border border-red-300 text-red-600 rounded-lg text-sm hover:bg-red-50 dark:hover:bg-red-900/20"
              >
                <Square className="h-4 w-4" /> Stop
              </button>
            )}
          </div>

          {sessionCost > 0 && (
            <p className="text-xs text-muted-foreground">
              Estimated cost so far: ${sessionCost.toFixed(4)}
            </p>
          )}
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

// ── Tab 3: Prompt Lab ─────────────────────────────────────────────────────────

const PROMPT_KEYS = ["planner", "executor", "verifier"] as const;
type PromptKey = (typeof PROMPT_KEYS)[number];

function VariantCard({
  variant,
  onPromote,
  onDelete,
  promoting,
}: {
  variant: PromptVariantItem;
  onPromote: () => void;
  onDelete: () => void;
  promoting: boolean;
}): JSX.Element {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="bg-card border border-border rounded-xl p-4 space-y-2">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Beaker className="h-4 w-4 text-sky-500" />
          <span className="text-sm font-semibold">{variant.name}</span>
          <span className="text-[10px] bg-sky-100 dark:bg-sky-900/30 text-sky-700 dark:text-sky-300 px-2 py-0.5 rounded-full font-medium">
            CHALLENGER
          </span>
        </div>
        <div className="flex items-center gap-1.5">
          <span className="text-xs text-muted-foreground">{variant.run_count} runs</span>
          <button
            onClick={() => setExpanded((e) => !e)}
            className="text-muted-foreground hover:text-foreground p-1"
          >
            <ChevronDown
              className={`h-3.5 w-3.5 transition-transform ${expanded ? "rotate-180" : ""}`}
            />
          </button>
        </div>
      </div>

      {expanded && (
        <p className="text-xs font-mono text-muted-foreground bg-muted/40 px-2 py-1.5 rounded">
          {variant.prompt_text}
        </p>
      )}

      <div className="flex items-center justify-between">
        <div className="flex gap-4 text-xs text-muted-foreground">
          <span>
            Mean: {variant.mean_score != null ? variant.mean_score.toFixed(3) : "—"}
          </span>
          <span>
            p95: {variant.p95_score != null ? variant.p95_score.toFixed(3) : "—"}
          </span>
        </div>
        <div className="flex gap-1.5">
          <button
            onClick={onPromote}
            disabled={promoting}
            className="flex items-center gap-1 text-xs px-2.5 py-1 bg-violet-600 text-foreground rounded-lg hover:bg-violet-700 disabled:opacity-50"
          >
            <Crown className="h-3 w-3" /> Promote
          </button>
          <button
            onClick={onDelete}
            className="p-1.5 text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 rounded-lg"
            aria-label={`Delete ${variant.name}`}
          >
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>
    </div>
  );
}

function PromptLabTab(): JSX.Element {
  const qc = useQueryClient();
  const [selectedKey, setSelectedKey] = useState<PromptKey>("planner");
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState("");
  const [newPrompt, setNewPrompt] = useState("");

  const { data: variants = [], isLoading } = useQuery({
    queryKey: ["prompt-variants", selectedKey],
    queryFn: () => promptVariantsApi.list(selectedKey),
  });

  const createMutation = useMutation({
    mutationFn: (data: { key: string; name: string; prompt_text: string }) =>
      promptVariantsApi.create(data),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["prompt-variants", selectedKey] });
      setShowCreate(false);
      setNewName("");
      setNewPrompt("");
      toast({ kind: "success", message: "Variant created" });
    },
    onError: (e) => toast({ kind: "error", message: `Create failed: ${String(e)}` }),
  });

  const promoteMutation = useMutation({
    mutationFn: (id: string) => promptVariantsApi.promote(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["prompt-variants", selectedKey] });
      toast({ kind: "success", message: "Variant promoted to control" });
    },
    onError: (e) => toast({ kind: "error", message: `Promote failed: ${String(e)}` }),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => promptVariantsApi.delete(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["prompt-variants", selectedKey] });
    },
    onError: (e) => toast({ kind: "error", message: `Delete failed: ${String(e)}` }),
  });

  const control = variants.find((v) => v.is_control);
  const challengers = variants.filter((v) => !v.is_control);

  return (
    <div className="space-y-5">
      <div className="flex items-end gap-3 flex-wrap">
        <div>
          <label className="block text-xs font-medium text-muted-foreground mb-1">
            Prompt Key
          </label>
          <div className="flex gap-1.5">
            {PROMPT_KEYS.map((k) => (
              <button
                key={k}
                onClick={() => setSelectedKey(k)}
                className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
                  selectedKey === k
                    ? "bg-violet-600 text-white"
                    : "border border-border hover:bg-muted"
                }`}
              >
                {k}
              </button>
            ))}
          </div>
        </div>
        <button
          onClick={() => setShowCreate(true)}
          className="flex items-center gap-1.5 px-3 py-2 border border-border rounded-lg text-sm hover:bg-muted"
        >
          <Plus className="h-4 w-4" /> Add Challenger Variant
        </button>
      </div>

      {showCreate && (
        <div className="bg-card border border-border rounded-xl p-4 space-y-3">
          <h3 className="text-sm font-semibold">New Challenger Variant</h3>
          <input
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="Variant name (e.g. Concise Planner)"
            className="w-full border border-input rounded-lg px-3 py-2 text-sm bg-background"
          />
          <textarea
            value={newPrompt}
            onChange={(e) => setNewPrompt(e.target.value)}
            placeholder="Prompt text for this variant…"
            rows={4}
            className="w-full border border-input rounded-lg px-3 py-2 text-sm bg-background resize-none"
          />
          <div className="flex gap-2">
            <button
              onClick={() =>
                createMutation.mutate({
                  key: selectedKey,
                  name: newName,
                  prompt_text: newPrompt,
                })
              }
              disabled={!newName.trim() || !newPrompt.trim() || createMutation.isPending}
              className="px-4 py-2 bg-violet-600 text-foreground rounded-lg text-sm hover:bg-violet-700 disabled:opacity-50"
            >
              {createMutation.isPending ? "Creating…" : "Create"}
            </button>
            <button
              onClick={() => setShowCreate(false)}
              className="px-4 py-2 border border-border rounded-lg text-sm hover:bg-muted"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {isLoading && (
        <div className="space-y-2">
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-20 w-full" />
        </div>
      )}

      {!isLoading && variants.length === 0 && (
        <EmptyState
          title="No variants registered"
          description={`No prompt variants for "${selectedKey}" yet. Add a challenger to start A/B testing.`}
        />
      )}

      {control && (
        <div className="bg-card border-2 border-violet-400/50 rounded-xl p-4 space-y-2">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Crown className="h-4 w-4 text-violet-500" />
              <span className="text-sm font-semibold">{control.name}</span>
              <span className="text-[10px] bg-violet-100 dark:bg-violet-900/30 text-violet-700 dark:text-violet-300 px-2 py-0.5 rounded-full font-medium">
                CONTROL
              </span>
            </div>
            <span className="text-xs text-muted-foreground">{control.run_count} runs</span>
          </div>
          <p className="text-xs text-muted-foreground line-clamp-2 font-mono bg-muted/40 px-2 py-1.5 rounded">
            {control.prompt_text}
          </p>
          <div className="flex gap-4 text-xs text-muted-foreground">
            <span>
              Mean: {control.mean_score != null ? control.mean_score.toFixed(3) : "—"}
            </span>
            <span>
              p95: {control.p95_score != null ? control.p95_score.toFixed(3) : "—"}
            </span>
          </div>
        </div>
      )}

      {challengers.map((v) => (
        <VariantCard
          key={v.id}
          variant={v}
          onPromote={() => promoteMutation.mutate(v.id)}
          onDelete={() => deleteMutation.mutate(v.id)}
          promoting={promoteMutation.isPending}
        />
      ))}
    </div>
  );
}

// ── Tab 4: Score & Benchmark ──────────────────────────────────────────────────

function ScoreTab(): JSX.Element {
  const [agentId, setAgentId] = useState("");
  const [selectedSuiteId, setSelectedSuiteId] = useState("");
  const [redTeamRunning, setRedTeamRunning] = useState(false);
  const [redTeamResult, setRedTeamResult] = useState<{
    passed: number;
    failed: number;
    total: number;
    results: Array<{ case: string; passed: boolean; details?: string }>;
  } | null>(null);

  const { data: agents = [] } = useQuery({
    queryKey: ["agents"],
    queryFn: () => agentsApi.list(),
  });

  const { data: suites = [] } = useQuery({
    queryKey: ["eval-suites"],
    queryFn: () => evalSuitesApi.listSuites(),
  });

  const effectiveSuiteId = selectedSuiteId || (suites[0]?.suite_id ?? "");

  const { data: suiteResults, isLoading: resultsLoading } = useQuery({
    queryKey: ["eval-suite-results", effectiveSuiteId],
    queryFn: () =>
      effectiveSuiteId ? evalSuitesApi.getSuiteResults(effectiveSuiteId) : Promise.resolve([]),
    enabled: !!effectiveSuiteId,
  });

  const chartData = (suiteResults ?? []).slice(-10).map((r) => ({
    run: r.run_id.slice(0, 8),
    score: r.overall_score,
    passed: r.passed,
    failed: r.failed,
  }));

  const runRedTeam = async (): Promise<void> => {
    setRedTeamRunning(true);
    try {
      const result = await redTeamApi.run();
      setRedTeamResult({
        passed: result.passed,
        failed: result.failed,
        total: result.total,
        results: result.results ?? [],
      });
    } catch (e) {
      toast({ kind: "error", message: `Red-team failed: ${String(e)}` });
    } finally {
      setRedTeamRunning(false);
    }
  };

  const securityScore =
    redTeamResult && redTeamResult.total > 0
      ? Math.round((redTeamResult.passed / redTeamResult.total) * 100)
      : null;

  return (
    <div className="space-y-5">
      <div className="flex gap-4 flex-wrap">
        <div>
          <label className="block text-xs font-medium text-muted-foreground mb-1">Agent</label>
          <select
            value={agentId}
            onChange={(e) => setAgentId(e.target.value)}
            className="w-48 border border-input rounded-lg px-3 py-2 text-sm bg-background"
          >
            <option value="">— All agents —</option>
            {agents.map((a) => (
              <option key={a.agent_id} value={a.agent_id}>
                {a.name}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="block text-xs font-medium text-muted-foreground mb-1">
            Eval Suite
          </label>
          <select
            value={effectiveSuiteId}
            onChange={(e) => setSelectedSuiteId(e.target.value)}
            className="w-48 border border-input rounded-lg px-3 py-2 text-sm bg-background"
            aria-label="Select eval suite"
          >
            {suites.length === 0 ? (
              <option value="">— No suites —</option>
            ) : (
              suites.map((s) => (
                <option key={s.suite_id} value={s.suite_id}>
                  {s.name || s.suite_id}
                </option>
              ))
            )}
          </select>
        </div>
      </div>

      <div className="bg-card border border-border rounded-xl p-5">
        <h3 className="text-sm font-semibold mb-4 flex items-center gap-2">
          <BarChart3 className="h-4 w-4" /> Eval Suite Results
          {effectiveSuiteId && (
            <span className="text-xs text-muted-foreground font-normal">
              —{" "}
              {suites.find((s) => s.suite_id === effectiveSuiteId)?.name ??
                effectiveSuiteId}
            </span>
          )}
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

      <div className="bg-card border border-border rounded-xl p-5 space-y-3">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold flex items-center gap-2">
            <Swords className="h-4 w-4" /> Red-Team Testing
          </h3>
          <button
            onClick={() => void runRedTeam()}
            disabled={redTeamRunning}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-red-600 text-foreground rounded-lg text-xs font-medium hover:bg-red-700 disabled:opacity-50"
          >
            {redTeamRunning ? (
              <>
                <Zap className="h-3.5 w-3.5 animate-pulse" /> Running…
              </>
            ) : (
              <>
                <Swords className="h-3.5 w-3.5" /> Run Red Team
              </>
            )}
          </button>
        </div>

        {redTeamRunning && (
          <div className="w-full bg-muted rounded-full h-1.5 overflow-hidden">
            <div className="h-full bg-red-500 animate-pulse rounded-full w-full" />
          </div>
        )}

        {redTeamResult ? (
          <div className="space-y-3">
            <div className="flex items-center gap-3">
              <span
                className={`text-2xl font-bold ${
                  securityScore! >= 80
                    ? "text-emerald-600"
                    : securityScore! >= 60
                    ? "text-amber-600"
                    : "text-red-600"
                }`}
              >
                {securityScore}%
              </span>
              <span className="text-sm text-muted-foreground">security score</span>
              <span className="text-xs text-muted-foreground">
                {redTeamResult.passed} blocked · {redTeamResult.failed} leaked
              </span>
            </div>
            <div className="space-y-1">
              {redTeamResult.results.map((r, i) => (
                <div key={i} className="flex items-center gap-2 text-xs">
                  {r.passed ? (
                    <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500 flex-shrink-0" />
                  ) : (
                    <XCircle className="h-3.5 w-3.5 text-red-500 flex-shrink-0" />
                  )}
                  <span className="font-mono truncate">{r.case}</span>
                  <span
                    className={`ml-auto px-1.5 py-0.5 rounded text-[10px] font-semibold flex-shrink-0 ${
                      r.passed
                        ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300"
                        : "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300"
                    }`}
                  >
                    {r.passed ? "BLOCKED" : "LEAKED"}
                  </span>
                </div>
              ))}
            </div>
          </div>
        ) : (
          <p className="text-xs text-muted-foreground">
            Red-team testing evaluates agent resilience to adversarial inputs, jailbreak
            attempts, and prompt injection. Click{" "}
            <span className="font-medium text-red-600">Run Red Team</span> to start.
          </p>
        )}
      </div>

      {!redTeamResult && (
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <AlertTriangle className="h-3.5 w-3.5" />
          Red-team has not been run yet for this agent.
        </div>
      )}
    </div>
  );
}

// ── AgentLabPage ──────────────────────────────────────────────────────────────

type LabTab = "preflight" | "sim" | "promptlab" | "score";

export function AgentLabPage(): JSX.Element {
  const [tab, setTab] = useState<LabTab>("preflight");

  const tabs: { key: LabTab; label: string; icon: JSX.Element }[] = [
    { key: "preflight", label: "Pre-Flight", icon: <Shield className="h-3.5 w-3.5" /> },
    { key: "sim", label: "Live Sim", icon: <Play className="h-3.5 w-3.5" /> },
    { key: "promptlab", label: "Prompt Lab", icon: <Beaker className="h-3.5 w-3.5" /> },
    { key: "score", label: "Score", icon: <BarChart3 className="h-3.5 w-3.5" /> },
  ];

  return (
    <div className="p-4 md:p-6 max-w-5xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-bold flex items-center gap-2">
          <FlaskConical className="h-6 w-6" /> Agent Lab
        </h1>
        <p className="text-sm text-muted-foreground mt-1">
          Pre-flight checks, live simulation, prompt A/B testing, and performance scoring.
        </p>
      </div>

      <div className="flex gap-1 border-b border-border">
        {tabs.map(({ key, label, icon }) => (
          <button
            key={key}
            role="tab"
            aria-selected={tab === key}
            onClick={() => setTab(key)}
            className={`flex items-center gap-1.5 px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
              tab === key
                ? "border-primary text-primary"
                : "border-transparent text-muted-foreground hover:text-foreground"
            }`}
          >
            {icon}
            {label}
          </button>
        ))}
      </div>

      {tab === "preflight" && <PreFlightTab />}
      {tab === "sim" && <LiveSimTab />}
      {tab === "promptlab" && <PromptLabTab />}
      {tab === "score" && <ScoreTab />}
    </div>
  );
}
