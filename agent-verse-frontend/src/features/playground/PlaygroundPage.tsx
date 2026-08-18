/**
 * World-class Agent Playground — 3-column sandbox with:
 *  Left:   Scenario Library + Quick Templates
 *  Center: Goal + Tool Picker + Execution Canvas (live SSE)
 *  Right:  Step Inspector + Session Stats
 */
import { useState, useRef, useEffect, useCallback } from "react";
import type { JSX } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Play,
  Square,
  RotateCcw,
  Save,
  Trash2,
  Plus,
  ChevronDown,
  ChevronRight,
  Zap,
  Terminal,
  CheckCircle2,
  Clock,
  DollarSign,
  Layers,
  BookOpen,
  FlaskConical,
  Github,
  MessageSquare,
  Database,
  FileDown,
} from "lucide-react";
import { simulationApi, apiFetch, API_BASE } from "@/lib/api/client";
import { useAuthStore } from "@/stores/auth";
import { JARVISPageShell } from '@/components/ui/JARVISPageShell';
import { JARVISStagger } from '@/components/ui/JARVISPageShell';

// ── Types ─────────────────────────────────────────────────────────────────────

interface MockTool {
  name: string;
  output: string;
  enabled: boolean;
}

interface ExecStep {
  index: number;
  description: string;
  type: "plan" | "tool" | "verify" | "complete" | "reasoning";
  tool?: string;
  output?: string;
  cost?: number;
  status: "running" | "done" | "error";
}

interface SessionStats {
  totalSteps: number;
  toolCalls: number;
  totalCost: number;
  elapsedSeconds: number;
}

interface Scenario {
  id: string;
  name: string;
  goal: string;
  tools: MockTool[];
  savedAt: string;
}

// ── Constants ─────────────────────────────────────────────────────────────────

const SCENARIO_STORAGE_KEY = "av_playground_scenarios";

const QUICK_TEMPLATES = [
  {
    icon: <FlaskConical className="h-3.5 w-3.5" />,
    name: "Jira Search",
    goal: "Search Jira for all open bugs in the BACKEND project and summarize them by priority",
    tools: [
      {
        name: "jira:search_issues",
        output: '{"issues":[{"id":"BACK-1","title":"API timeout on /goals","status":"open","priority":"high"},{"id":"BACK-2","title":"Memory leak in executor","status":"open","priority":"medium"}]}',
        enabled: true,
      },
    ],
  },
  {
    icon: <Github className="h-3.5 w-3.5" />,
    name: "GitHub PR Review",
    goal: "Review all open pull requests in the main repository and identify any that need immediate attention",
    tools: [
      {
        name: "github:list_pulls",
        output: '{"pulls":[{"number":42,"title":"Fix memory leak in executor","state":"open","draft":false,"changed_files":12}]}',
        enabled: true,
      },
    ],
  },
  {
    icon: <MessageSquare className="h-3.5 w-3.5" />,
    name: "Slack Alert",
    goal: "Send a daily summary of failed deployments to the #ops-alerts Slack channel",
    tools: [
      {
        name: "slack:send_message",
        output: '{"ok":true,"ts":"1719000000.123456","channel":"C1234567"}',
        enabled: true,
      },
    ],
  },
  {
    icon: <Database className="h-3.5 w-3.5" />,
    name: "Database Query",
    goal: "Query the PostgreSQL database for the top 10 customers by revenue this month and format as a report",
    tools: [
      {
        name: "postgres:query",
        output: '{"rows":[{"customer_id":1,"name":"Acme Corp","revenue":50000},{"customer_id":2,"name":"GlobalTech","revenue":43200}],"row_count":10}',
        enabled: true,
      },
    ],
  },
] as const;

// ── Helpers ───────────────────────────────────────────────────────────────────

/**
 * Classify a step description into a display/filter type.
 * Reasoning steps are detected from natural-language keywords.
 * Note: "complete" is only set externally on the final result node.
 */
function classifyStep(description: string): "tool" | "verify" | "plan" | "reasoning" {
  const lower = description.toLowerCase();
  if (
    lower.includes("reasoning") || lower.includes("thinking") ||
    lower.includes("analyzing") || lower.includes("considering") ||
    lower.includes("evaluating options")
  ) return "reasoning";
  if (
    lower.includes("tool") || lower.includes("call") || lower.includes("execute") ||
    lower.includes("fetch") || lower.includes("run")
  ) return "tool";
  if (
    lower.includes("verify") || lower.includes("check") ||
    lower.includes("complet") || lower.includes("confirm")
  ) return "verify";
  return "plan";
}

function stepBorderColor(type: ExecStep["type"], status: ExecStep["status"]): string {
  if (status === "running") return "border-blue-400 bg-blue-50/40 dark:bg-blue-900/15";
  if (status === "error") return "border-red-400 bg-red-50/40 dark:bg-red-900/15";
  switch (type) {
    case "plan": return "border-violet-300/70 bg-violet-50/30 dark:bg-violet-900/10";
    case "tool": return "border-amber-300/70 bg-amber-50/30 dark:bg-amber-900/10";
    case "verify": return "border-emerald-300/70 bg-emerald-50/30 dark:bg-emerald-900/10";
    case "complete": return "border-green-400 bg-green-50/40 dark:bg-green-900/10";
    case "reasoning": return "border-sky-300/70 bg-sky-50/30 dark:bg-sky-900/10";
  }
}

function stepDotColor(type: ExecStep["type"], status: ExecStep["status"]): string {
  if (status === "running") return "bg-blue-500 animate-pulse";
  if (status === "error") return "bg-red-500";
  switch (type) {
    case "plan": return "bg-violet-500";
    case "tool": return "bg-amber-500";
    case "verify": return "bg-emerald-500";
    case "complete": return "bg-green-500";
    case "reasoning": return "bg-sky-500";
  }
}

function loadScenarios(): Scenario[] {
  try {
    return JSON.parse(localStorage.getItem(SCENARIO_STORAGE_KEY) ?? "[]") as Scenario[];
  } catch {
    return [];
  }
}

function saveScenarios(scenarios: Scenario[]): void {
  localStorage.setItem(SCENARIO_STORAGE_KEY, JSON.stringify(scenarios));
}

// ── ToolCard ──────────────────────────────────────────────────────────────────

function ToolCard({
  tool,
  selected,
  onToggle,
  onEditOutput,
}: {
  tool: { name: string; description: string; server_id: string };
  selected: MockTool | undefined;
  onToggle: () => void;
  onEditOutput: (val: string) => void;
}): JSX.Element {
  const [expanded, setExpanded] = useState(false);
  const isOn = selected?.enabled ?? false;

  return (
    <div
      className={`border rounded-lg transition-[color,background-color,border-color,opacity,box-shadow,transform] ${
        isOn ? "border-violet-400 bg-violet-50/30 dark:bg-violet-900/10" : "border-border"
      }`}
    >
      <div className="flex items-center gap-2 px-3 py-2">
        <button
          onClick={onToggle}
          className={`w-8 h-4 rounded-full transition-colors flex-shrink-0 relative ${
            isOn ? "bg-violet-500" : "bg-muted"
          }`}
          aria-label={`Toggle ${tool.name}`}
        >
          <span
            className={`absolute top-0.5 w-3 h-3 rounded-full bg-[#0F1826] shadow transition-transform ${
              isOn ? "left-[18px]" : "left-0.5"
            }`}
          />
        </button>
        <span className="text-xs font-mono font-medium flex-1 truncate">{tool.name}</span>
        {tool.description && (
          <span className="text-xs text-muted-foreground truncate max-w-[120px] hidden sm:block">
            {tool.description}
          </span>
        )}
        {isOn && (
          <button
            onClick={() => setExpanded((e) => !e)}
            className="text-muted-foreground hover:text-foreground"
          >
            {expanded ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
          </button>
        )}
      </div>
      {isOn && expanded && (
        <div className="px-3 pb-3">
          <label className="text-[10px] font-medium text-muted-foreground uppercase tracking-wide">
            Mock Response (JSON)
          </label>
          <textarea
            value={selected?.output ?? "{}"}
            onChange={(e) => onEditOutput(e.target.value)}
            rows={3}
            className="w-full mt-1 border border-input rounded px-2 py-1.5 text-xs font-mono bg-background resize-none"
          />
        </div>
      )}
    </div>
  );
}

// ── Main Page ─────────────────────────────────────────────────────────────────

export function PlaygroundPage(): JSX.Element {
  const apiKey = useAuthStore((s) => s.apiKey);

  // ── State ──────────────────────────────────────────────────────────────────
  const [goal, setGoal] = useState("");
  const [mockTools, setMockTools] = useState<MockTool[]>([]);
  const [steps, setSteps] = useState<ExecStep[]>([]);
  const [selectedStep, setSelectedStep] = useState<ExecStep | null>(null);
  const [running, setRunning] = useState(false);
  const [finalResult, setFinalResult] = useState<{
    status: string;
    totalCost: number;
    iterations: number;
  } | null>(null);
  const [stats, setStats] = useState<SessionStats>({
    totalSteps: 0,
    toolCalls: 0,
    totalCost: 0,
    elapsedSeconds: 0,
  });
  const [showOptions, setShowOptions] = useState({
    toolCalls: true,
    reasoning: false,
    costs: true,
  });

  // Scenario library
  const [scenarios, setScenarios] = useState<Scenario[]>(() => loadScenarios());
  const [saveDialogOpen, setSaveDialogOpen] = useState(false);
  const [saveName, setSaveName] = useState("");

  const abortRef = useRef<AbortController | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const startTimeRef = useRef<number>(0);

  // ── Load available tools ────────────────────────────────────────────────────
  const { data: availableToolsData } = useQuery({
    queryKey: ["available-tools"],
    queryFn: () => simulationApi.getAvailableTools(),
    staleTime: 60_000,
    enabled: !!apiKey,
  });
  const availableTools = availableToolsData?.tools ?? [];

  // ── Timer ──────────────────────────────────────────────────────────────────
  useEffect(() => {
    if (running) {
      startTimeRef.current = Date.now();
      timerRef.current = setInterval(() => {
        setStats((s) => ({
          ...s,
          elapsedSeconds: Math.floor((Date.now() - startTimeRef.current) / 1000),
        }));
      }, 1000);
    } else {
      if (timerRef.current) clearInterval(timerRef.current);
    }
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [running]);

  // ── Tool toggle ────────────────────────────────────────────────────────────
  const toggleTool = useCallback((toolName: string) => {
    setMockTools((prev) => {
      const existing = prev.find((t) => t.name === toolName);
      if (existing) {
        return prev.map((t) =>
          t.name === toolName ? { ...t, enabled: !t.enabled } : t
        );
      }
      return [
        ...prev,
        { name: toolName, output: '{"result": "mocked response"}', enabled: true },
      ];
    });
  }, []);

  const updateToolOutput = useCallback((name: string, output: string) => {
    setMockTools((prev) =>
      prev.map((t) => (t.name === name ? { ...t, output } : t))
    );
  }, []);

  // ── Add custom tool ────────────────────────────────────────────────────────
  const addCustomTool = (): void => {
    const id = Math.random().toString(36).slice(2, 7);
    setMockTools((prev) => [
      ...prev,
      { name: `custom:tool_${id}`, output: '{"result": "custom mock"}', enabled: true },
    ]);
  };

  // ── Quick template ─────────────────────────────────────────────────────────
  const applyTemplate = (tpl: (typeof QUICK_TEMPLATES)[number]): void => {
    setGoal(tpl.goal);
    setMockTools(tpl.tools.map((t) => ({ ...t })));
    setSteps([]);
    setFinalResult(null);
    setSelectedStep(null);
  };

  // ── Scenario library ───────────────────────────────────────────────────────
  const saveScenario = async (): Promise<void> => {
    if (!saveName.trim()) return;
    const scenario: Scenario = {
      id: crypto.randomUUID(),
      name: saveName.trim(),
      goal,
      tools: mockTools,
      savedAt: new Date().toISOString(),
    };
    const updated = [scenario, ...scenarios];
    setScenarios(updated);
    saveScenarios(updated);
    setSaveDialogOpen(false);
    setSaveName("");

    // Best-effort backend persistence
    try {
      await apiFetch<void>('/playground/scenarios', {
        method: 'POST',
        body: JSON.stringify(scenario),
      });
    } catch {
      // Silently ignore — localStorage is the source of truth
    }
  };

  const loadScenario = (s: Scenario): void => {
    setGoal(s.goal);
    setMockTools(s.tools);
    setSteps([]);
    setFinalResult(null);
    setSelectedStep(null);
  };

  const deleteScenario = (id: string): void => {
    const updated = scenarios.filter((s) => s.id !== id);
    setScenarios(updated);
    saveScenarios(updated);
  };

  const clearAll = (): void => {
    setGoal("");
    setMockTools([]);
    setSteps([]);
    setFinalResult(null);
    setSelectedStep(null);
    setStats({ totalSteps: 0, toolCalls: 0, totalCost: 0, elapsedSeconds: 0 });
  };

  // ── Export simulation trace ────────────────────────────────────────────────
  const exportTrace = (): void => {
    if (!steps.length) return;
    const enabledTools = mockTools.filter((t) => t.enabled);
    const trace = {
      goal,
      tools: enabledTools,
      run_date: new Date().toISOString(),
      steps,
      stats: {
        total_steps: steps.length,
        tool_calls: steps.filter(s => classifyStep(s.description ?? '') === 'tool').length,
        duration_ms: stats.elapsedSeconds * 1000,
      },
    };
    const blob = new Blob([JSON.stringify(trace, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `simulation-${Date.now()}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  // ── Run simulation ─────────────────────────────────────────────────────────
  const runSim = async (): Promise<void> => {
    if (!goal.trim() || running) return;
    setRunning(true);
    setSteps([]);
    setFinalResult(null);
    setSelectedStep(null);
    setStats({ totalSteps: 0, toolCalls: 0, totalCost: 0, elapsedSeconds: 0 });

    const mockToolsMap: Record<string, string> = Object.fromEntries(
      mockTools.filter((t) => t.enabled).map((t) => [t.name, t.output])
    );

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
        // Fallback to batch mode
        const result = await simulationApi.run(goal, mockToolsMap);
        const fallbackSteps: ExecStep[] = (result.steps ?? []).map((s, i) => ({
          index: i + 1,
          description: String(s.step),
          type: s.tool ? "tool" : classifyStep(String(s.step)),
          tool: s.tool || undefined,
          output: s.output || undefined,
          status: "done" as const,
        }));
        setSteps(fallbackSteps);
        setFinalResult({
          status: result.status,
          totalCost: result.cost_usd ?? 0,
          iterations: result.iterations ?? fallbackSteps.length,
        });
        return;
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let stepIndex = 0;
      let accCost = 0;
      let toolCallCount = 0;

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
              stepIndex++;
              const desc = String(evt.description ?? evt.step ?? `Step ${stepIndex}`);
              const tool = String(evt.tool_called ?? evt.tool ?? "");
              const newStep: ExecStep = {
                index: stepIndex,
                description: desc,
                type: tool ? "tool" : classifyStep(desc),
                tool: tool || undefined,
                status: "running",
              };
              setSteps((prev) => [...prev, newStep]);
            } else if (evt.type === "step_completed") {
              const cost = Number(evt.cost_increment ?? evt.cost ?? 0);
              accCost += cost;
              const toolCalled = String(evt.tool_called ?? evt.tool ?? "");
              if (toolCalled) toolCallCount++;
              setSteps((prev) =>
                prev.map((s) =>
                  s.index === stepIndex
                    ? {
                        ...s,
                        output: String(evt.output ?? ""),
                        tool: toolCalled || s.tool,
                        cost,
                        status: "done",
                      }
                    : s
                )
              );
              setStats((prev) => ({
                ...prev,
                totalSteps: stepIndex,
                toolCalls: toolCallCount,
                totalCost: accCost,
              }));
            } else if (evt.type === "simulation_complete") {
              const totalCost = Number(evt.total_cost ?? accCost);
              const totalStepsN = Number(evt.total_steps ?? stepIndex);
              setFinalResult({
                status: String(evt.final_status ?? "complete"),
                totalCost,
                iterations: totalStepsN,
              });
              setStats((prev) => ({
                ...prev,
                totalSteps: totalStepsN,
                totalCost,
              }));
            } else if (evt.type === "simulation_error") {
              setFinalResult({ status: "error", totalCost: accCost, iterations: stepIndex });
            }
          } catch {
            // skip malformed events
          }
        }
      }
    } catch (e) {
      if ((e as Error).name !== "AbortError") {
        console.error("Simulation error:", e);
      }
    } finally {
      setRunning(false);
    }
  };

  const abortSim = (): void => {
    abortRef.current?.abort();
    setRunning(false);
    setSteps((prev) =>
      prev.map((s) => (s.status === "running" ? { ...s, status: "error" } : s))
    );
  };

  // ── Render ─────────────────────────────────────────────────────────────────
  const enabledTools = mockTools.filter((t) => t.enabled);

  // Apply visibility filters based on show toggles
  const visibleSteps = steps.filter(step => {
    const type = classifyStep(step.description ?? '');
    if (type === 'reasoning' && !showOptions.reasoning) return false;
    if (type === 'tool' && !showOptions.toolCalls) return false;
    return true;
  });

  return (
    <JARVISPageShell>
    <JARVISStagger className="flex h-[calc(100vh-4rem)] overflow-hidden">
      {/* ── Left Sidebar ──────────────────────────────────────────────────── */}
      <div className="w-64 flex-shrink-0 border-r border-border flex flex-col bg-card/50 overflow-y-auto">
        <div className="p-3 space-y-3">
          {/* New scenario + clear */}
          <div className="flex gap-2">
            <button
              onClick={clearAll}
              className="flex-1 flex items-center justify-center gap-1.5 text-xs px-3 py-2 bg-violet-600 text-foreground rounded-lg hover:bg-violet-700 font-medium"
            >
              <Plus className="h-3.5 w-3.5" /> New Scenario
            </button>
            <button
              onClick={clearAll}
              title="Clear all"
              className="px-2 py-2 border border-border rounded-lg hover:bg-accent text-muted-foreground"
            >
              <RotateCcw className="h-3.5 w-3.5" />
            </button>
          </div>

          {/* Quick Templates */}
          <div>
            <p className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground mb-1.5 flex items-center gap-1">
              <Zap className="h-3 w-3" /> Quick Templates
            </p>
            <div className="space-y-1">
              {QUICK_TEMPLATES.map((tpl) => (
                <button
                  key={tpl.name}
                  onClick={() => applyTemplate(tpl)}
                  className="w-full flex items-center gap-2 text-left px-2.5 py-2 rounded-lg text-xs hover:bg-accent transition-colors"
                >
                  <span className="text-muted-foreground">{tpl.icon}</span>
                  {tpl.name}
                </button>
              ))}
            </div>
          </div>

          {/* Scenario Library */}
          <div>
            <div className="flex items-center justify-between mb-1.5">
              <p className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground flex items-center gap-1">
                <BookOpen className="h-3 w-3" /> Saved Scenarios
              </p>
              <button
                onClick={() => setSaveDialogOpen(true)}
                title="Save current"
                disabled={!goal.trim()}
                className="text-muted-foreground hover:text-foreground disabled:opacity-30"
              >
                <Save className="h-3.5 w-3.5" />
              </button>
            </div>

            {saveDialogOpen && (
              <div className="mb-2 p-2 bg-background border border-border rounded-lg space-y-2">
                <input
                  autoFocus
                  value={saveName}
                  onChange={(e) => setSaveName(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && void saveScenario()}
                  placeholder="Scenario name…"
                  className="w-full border border-input rounded px-2 py-1 text-xs bg-background"
                />
                <div className="flex gap-1">
                  <button
                    onClick={() => void saveScenario()}
                    className="flex-1 text-xs bg-violet-600 text-foreground rounded px-2 py-1 hover:bg-violet-700"
                  >
                    Save
                  </button>
                  <button
                    onClick={() => setSaveDialogOpen(false)}
                    className="text-xs border border-border rounded px-2 py-1 hover:bg-accent"
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}

            {scenarios.length === 0 ? (
              <p className="text-xs text-muted-foreground italic px-1">
                No saved scenarios yet.
              </p>
            ) : (
              <div className="space-y-1">
                {scenarios.map((s) => (
                  <div
                    key={s.id}
                    className="flex items-center gap-1 group rounded-lg px-2 py-1.5 hover:bg-accent"
                  >
                    <button
                      onClick={() => loadScenario(s)}
                      className="flex-1 text-left min-w-0"
                    >
                      <p className="text-xs font-medium truncate">{s.name}</p>
                      <p className="text-[10px] text-muted-foreground">
                        {new Date(s.savedAt).toLocaleDateString()}
                      </p>
                    </button>
                    <button
                      onClick={() => deleteScenario(s.id)}
                      className="opacity-0 group-hover:opacity-100 text-red-400 hover:text-red-600"
                    >
                      <Trash2 className="h-3 w-3" />
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* ── Main Canvas ───────────────────────────────────────────────────── */}
      <div className="flex-1 min-w-0 flex flex-col overflow-y-auto">
        <div className="p-4 space-y-4">
          {/* Header */}
          <div>
            <h1 className="text-xl font-bold">Agent Playground</h1>
            <p className="text-xs text-muted-foreground mt-0.5">
              Sandbox agents with mocked tools — zero real side-effects
            </p>
          </div>

          {/* Goal textarea */}
          <div>
            <div className="flex items-center justify-between mb-1">
              <label className="text-sm font-medium">Goal</label>
              <span className="text-xs text-muted-foreground">{goal.length} chars</span>
            </div>
            <textarea
              autoFocus
              value={goal}
              onChange={(e) => setGoal(e.target.value)}
              rows={3}
              placeholder="Describe what the agent should accomplish…"
              className="w-full border border-input rounded-xl px-4 py-3 text-sm bg-background outline-none focus:ring-2 focus:ring-violet-500 resize-none"
            />
          </div>

          {/* Tool Configuration */}
          <div>
            <div className="flex items-center justify-between mb-2">
              <label className="text-sm font-medium flex items-center gap-1.5">
                <Terminal className="h-4 w-4" /> Tool Configuration
                {enabledTools.length > 0 && (
                  <span className="text-xs bg-violet-100 dark:bg-violet-900/30 text-violet-700 dark:text-violet-300 px-1.5 py-0.5 rounded-full">
                    {enabledTools.length} active
                  </span>
                )}
              </label>
              <button
                onClick={addCustomTool}
                className="flex items-center gap-1 text-xs px-2.5 py-1 border border-border rounded-lg hover:bg-accent"
              >
                <Plus className="h-3 w-3" /> Custom
              </button>
            </div>

            {availableTools.length > 0 ? (
              <div className="space-y-1.5 max-h-48 overflow-y-auto pr-1">
                {availableTools.map((at) => (
                  <ToolCard
                    key={at.name}
                    tool={at}
                    selected={mockTools.find((t) => t.name === at.name)}
                    onToggle={() => toggleTool(at.name)}
                    onEditOutput={(val) => updateToolOutput(at.name, val)}
                  />
                ))}
              </div>
            ) : (
              <div className="space-y-1.5">
                {mockTools.map((t, i) => (
                  <div key={i} className="flex gap-2 items-start">
                    <div className="flex-1 space-y-1">
                      <input
                        value={t.name}
                        onChange={(e) =>
                          setMockTools((prev) =>
                            prev.map((mt, idx) =>
                              idx === i ? { ...mt, name: e.target.value } : mt
                            )
                          )
                        }
                        placeholder="tool_name"
                        className="w-full border border-input rounded px-2 py-1.5 text-xs font-mono bg-background"
                      />
                      <textarea
                        value={t.output}
                        onChange={(e) =>
                          setMockTools((prev) =>
                            prev.map((mt, idx) =>
                              idx === i ? { ...mt, output: e.target.value } : mt
                            )
                          )
                        }
                        placeholder='{"result": "mock"}'
                        rows={2}
                        className="w-full border border-input rounded px-2 py-1.5 text-xs font-mono bg-background resize-none"
                      />
                    </div>
                    <button
                      onClick={() =>
                        setMockTools((prev) => prev.filter((_, idx) => idx !== i))
                      }
                      className="p-1.5 text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 rounded"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                ))}
                {mockTools.length === 0 && (
                  <p className="text-xs text-muted-foreground italic">
                    No tools configured — add custom tools or connect MCP servers.
                  </p>
                )}
              </div>
            )}
          </div>

          {/* Run Controls */}
          <div className="flex items-center gap-3 flex-wrap">
            {!running ? (
              <button
                onClick={() => void runSim()}
                disabled={!goal.trim()}
                className="flex items-center gap-2 px-6 py-2.5 bg-gradient-to-r from-violet-600 to-violet-700 text-foreground rounded-xl text-sm font-medium hover:from-violet-700 hover:to-violet-800 disabled:opacity-40 shadow-sm"
              >
                <Play className="h-4 w-4" /> Run Simulation
              </button>
            ) : (
              <button
                onClick={abortSim}
                className="flex items-center gap-2 px-6 py-2.5 bg-red-600 text-foreground rounded-xl text-sm font-medium hover:bg-red-700 shadow-sm"
              >
                <Square className="h-4 w-4" /> Abort
              </button>
            )}

            {/* Show toggles */}
            <div className="flex gap-2">
              {(["toolCalls", "reasoning", "costs"] as const).map((opt) => (
                <button
                  key={opt}
                  onClick={() => setShowOptions((s) => ({ ...s, [opt]: !s[opt] }))}
                  className={`text-xs px-2.5 py-1.5 rounded-lg border transition-colors ${
                    showOptions[opt]
                      ? "border-violet-400 bg-violet-50 dark:bg-violet-900/20 text-violet-700 dark:text-violet-300"
                      : "border-border text-muted-foreground"
                  }`}
                >
                  {opt === "toolCalls" ? "Tool Calls" : opt === "reasoning" ? "Reasoning" : "Costs"}
                </button>
              ))}
            </div>
          </div>

          {/* Execution Canvas */}
          <div className="space-y-2 pb-6">
            {/* Canvas header with Export Trace button */}
            {steps.length > 0 && !running && (
              <div className="flex items-center justify-between mb-1">
                <span className="text-xs text-muted-foreground">
                  {visibleSteps.length} of {steps.length} steps shown
                </span>
                <button
                  onClick={exportTrace}
                  className="flex items-center gap-1.5 text-xs px-2.5 py-1 border border-border rounded-lg hover:bg-accent text-muted-foreground hover:text-foreground"
                  title="Export simulation trace as JSON"
                >
                  <FileDown className="h-3.5 w-3.5" /> Export Trace
                </button>
              </div>
            )}

            {running && steps.length === 0 && (
              <div className="flex items-center gap-2 text-sm text-muted-foreground py-4">
                <span className="w-2 h-2 rounded-full bg-violet-500 animate-pulse" />
                Connecting to simulation engine…
              </div>
            )}

            {visibleSteps.map((step) => (
              <button
                key={step.index}
                onClick={() => setSelectedStep(step === selectedStep ? null : step)}
                className={`w-full text-left border rounded-xl px-4 py-3 transition-[color,background-color,border-color,opacity,box-shadow,transform] ${
                  stepBorderColor(step.type, step.status)
                } ${selectedStep?.index === step.index ? "ring-2 ring-violet-400" : ""}`}
              >
                <div className="flex items-start gap-3">
                  <span
                    className={`w-5 h-5 rounded-full flex items-center justify-center flex-shrink-0 text-[10px] font-bold text-foreground mt-0.5 ${stepDotColor(step.type, step.status)}`}
                  >
                    {step.status === "done" ? (
                      <CheckCircle2 className="h-3.5 w-3.5" />
                    ) : (
                      step.index
                    )}
                  </span>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium">{step.description}</p>
                    {step.tool && showOptions.toolCalls && (
                      <p className="text-xs text-amber-600 dark:text-amber-400 font-mono mt-0.5">
                        → {step.tool}
                      </p>
                    )}
                    {step.output && (
                      <p className="text-xs text-muted-foreground mt-1 truncate">
                        {step.output}
                      </p>
                    )}
                  </div>
                  {showOptions.costs && step.cost != null && (
                    <span className="text-[10px] text-muted-foreground flex-shrink-0">
                      ${step.cost.toFixed(4)}
                    </span>
                  )}
                </div>
              </button>
            ))}

            {/* Final result */}
            {finalResult && (
              <div
                className={`border rounded-xl px-4 py-4 ${
                  finalResult.status.includes("error")
                    ? "border-red-400 bg-red-50/30 dark:bg-red-900/10"
                    : "border-emerald-400 bg-emerald-50/30 dark:bg-emerald-900/10"
                }`}
              >
                <div className="flex items-center gap-3">
                  <span
                    className={`px-2.5 py-1 rounded-full text-xs font-semibold ${
                      finalResult.status.includes("error")
                        ? "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300"
                        : "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300"
                    }`}
                  >
                    {finalResult.status}
                  </span>
                  <span className="text-xs text-muted-foreground">
                    {finalResult.iterations} steps · ${finalResult.totalCost.toFixed(4)} simulated
                  </span>
                </div>
              </div>
            )}

            {!running && steps.length === 0 && !finalResult && (
              <div className="flex flex-col items-center justify-center h-40 text-muted-foreground border-2 border-dashed border-border rounded-xl">
                <Play className="h-10 w-10 mb-2 opacity-20" />
                <p className="text-sm">Run a simulation to see execution here</p>
                <p className="text-xs mt-1 opacity-60">
                  No real tools are called — safe to experiment
                </p>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* ── Right Inspector ────────────────────────────────────────────────── */}
      <div className="w-72 flex-shrink-0 border-l border-border flex flex-col bg-card/50 overflow-y-auto">
        <div className="p-3 space-y-4">
          {/* Step Detail */}
          {selectedStep ? (
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                  Step {selectedStep.index}
                </p>
                <span
                  className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${
                    selectedStep.type === "tool"
                      ? "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300"
                      : selectedStep.type === "verify"
                      ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300"
                      : selectedStep.type === "reasoning"
                      ? "bg-sky-100 text-sky-700 dark:bg-sky-900/30 dark:text-sky-300"
                      : "bg-violet-100 text-violet-700 dark:bg-violet-900/30 dark:text-violet-300"
                  }`}
                >
                  {selectedStep.type}
                </span>
              </div>

              <p className="text-sm">{selectedStep.description}</p>

              {selectedStep.tool && (
                <div>
                  <p className="text-[10px] text-muted-foreground uppercase tracking-wide mb-1">
                    Tool
                  </p>
                  <code className="text-xs bg-muted px-2 py-1 rounded font-mono">
                    {selectedStep.tool}
                  </code>
                </div>
              )}

              {selectedStep.output && (
                <div>
                  <p className="text-[10px] text-muted-foreground uppercase tracking-wide mb-1">
                    Output
                  </p>
                  <pre className="text-xs bg-muted rounded-lg px-3 py-2 overflow-auto max-h-40 whitespace-pre-wrap break-words">
                    {(() => {
                      try {
                        return JSON.stringify(JSON.parse(selectedStep.output!), null, 2);
                      } catch {
                        return selectedStep.output;
                      }
                    })()}
                  </pre>
                </div>
              )}

              {selectedStep.cost != null && (
                <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                  <DollarSign className="h-3.5 w-3.5" />${selectedStep.cost.toFixed(4)} estimated
                </div>
              )}
            </div>
          ) : (
            <div className="flex flex-col items-center justify-center h-24 text-muted-foreground">
              <Layers className="h-8 w-8 mb-2 opacity-20" />
              <p className="text-xs text-center">Click a step to inspect details</p>
            </div>
          )}

          {/* Divider */}
          <div className="border-t border-border" />

          {/* Session Stats */}
          <div>
            <p className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground mb-2">
              Session Stats
            </p>
            <div className="space-y-2">
              {[
                { icon: <Layers className="h-3.5 w-3.5" />, label: "Steps", value: stats.totalSteps },
                {
                  icon: <Terminal className="h-3.5 w-3.5" />,
                  label: "Tool Calls",
                  value: stats.toolCalls,
                },
                {
                  icon: <DollarSign className="h-3.5 w-3.5" />,
                  label: "Est. Cost",
                  value: `$${stats.totalCost.toFixed(4)}`,
                },
                {
                  icon: <Clock className="h-3.5 w-3.5" />,
                  label: "Elapsed",
                  value: `${stats.elapsedSeconds}s`,
                },
              ].map(({ icon, label, value }) => (
                <div key={label} className="flex items-center justify-between text-xs">
                  <span className="flex items-center gap-1.5 text-muted-foreground">
                    {icon} {label}
                  </span>
                  <span className="font-medium tabular-nums">{value}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </JARVISStagger>
    </JARVISPageShell>
  );
}
