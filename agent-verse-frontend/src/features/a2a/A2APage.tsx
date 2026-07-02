/**
 * A2APage — world-class Agent-to-Agent observability & control.
 *
 * Three tabs:
 *   1. Tasks      — dispatch goals + live-polling task list
 *   2. Agent Card — this platform's published capability card
 *   3. Remote Agents — registry of external agents
 */
import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Network, Send, RefreshCw, Copy, Check, Loader2, Plus,
  Trash2, Zap, ChevronDown, ChevronRight, ShieldCheck,
  ExternalLink, AlertCircle,
} from "lucide-react";
import { a2aApi, type AgentCard, type A2ATask } from "@/lib/api/client";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/EmptyState";
import { toast } from "@/stores/toast";

// ── Helpers ───────────────────────────────────────────────────────────────────

type Tab = "tasks" | "card" | "remotes";

const STATUS_STYLES: Record<string, string> = {
  accepted:   "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300",
  running:    "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400 animate-pulse",
  complete:   "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-300",
  failed:     "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400",
  processing: "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300 animate-pulse",
};

function timeAgo(iso?: string): string {
  if (!iso) return "";
  const s = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  return `${Math.floor(m / 60)}h ago`;
}

function hasLiveTask(tasks: A2ATask[]): boolean {
  return tasks.some((t) => t.status === "accepted" || t.status === "running" || t.status === "processing");
}

// ── Copy button ───────────────────────────────────────────────────────────────

function CopyBtn({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const handle = async () => {
    await navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };
  return (
    <button onClick={handle} className="p-1 rounded text-muted-foreground hover:text-foreground transition-colors" aria-label="Copy">
      {copied ? <Check className="h-3.5 w-3.5 text-green-500" /> : <Copy className="h-3.5 w-3.5" />}
    </button>
  );
}

// ── Task row ──────────────────────────────────────────────────────────────────

function TaskRow({ task }: { task: A2ATask }) {
  const [expanded, setExpanded] = useState(false);
  const statusClass = STATUS_STYLES[task.status] ?? "bg-muted text-muted-foreground";
  return (
    <div className="border border-border rounded-xl overflow-hidden" data-testid="task-row">
      <div className="flex items-center gap-3 px-4 py-3">
        <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium shrink-0 ${statusClass}`}>
          {task.status}
        </span>
        <p className="text-sm truncate flex-1" title={task.goal}>{task.goal}</p>
        <span className="text-[10px] text-muted-foreground shrink-0 font-mono">{task.task_id.slice(0, 12)}…</span>
        <span className="text-[10px] text-muted-foreground shrink-0">{timeAgo(task.created_at)}</span>
        {task.result && (
          <button onClick={() => setExpanded((v) => !v)} className="text-muted-foreground hover:text-foreground shrink-0">
            {expanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
          </button>
        )}
      </div>
      {expanded && task.result && (
        <div className="px-4 pb-3 border-t border-border bg-muted/20">
          <pre className="text-xs font-mono whitespace-pre-wrap text-muted-foreground max-h-48 overflow-auto">
            {task.result}
          </pre>
        </div>
      )}
    </div>
  );
}

// ── Task tab ──────────────────────────────────────────────────────────────────

function TasksTab() {
  const qc = useQueryClient();
  const [goal, setGoal] = useState("");
  const [priority, setPriority] = useState("normal");
  const [requesterId, setRequesterId] = useState("");
  const [callbackUrl, setCallbackUrl] = useState("");
  const [submitted, setSubmitted] = useState<{ task_id: string } | null>(null);

  const { data: tasks = [], isLoading, refetch } = useQuery<A2ATask[]>({
    queryKey: ["a2a-tasks"],
    queryFn: () => a2aApi.listTasks(50),
    refetchInterval: (query) => hasLiveTask(query.state.data ?? []) ? 5000 : 30_000,
    staleTime: 3_000,
  });

  const submitMutation = useMutation({
    mutationFn: () =>
      a2aApi.submitTask({
        goal: goal.trim(),
        priority,
        requester_agent_id: requesterId.trim() || undefined,
        callback_url: callbackUrl.trim() || undefined,
      }),
    onSuccess: (data) => {
      setSubmitted(data);
      toast({ kind: "success", message: `Task ${data.task_id.slice(0, 12)} dispatched.` });
      qc.invalidateQueries({ queryKey: ["a2a-tasks"] });
      setGoal("");
    },
    onError: (e) => toast({ kind: "error", message: `Dispatch failed: ${String(e)}` }),
  });

  return (
    <div className="grid grid-cols-1 lg:grid-cols-5 gap-6">
      {/* Dispatch form (2/5) */}
      <div className="lg:col-span-2 space-y-4">
        <div className="bg-card border border-border rounded-xl p-5 space-y-4">
          <h3 className="text-sm font-semibold flex items-center gap-2">
            <Send className="h-4 w-4 text-primary" /> Dispatch Task
          </h3>
          <div>
            <label className="block text-xs font-medium mb-1" htmlFor="a2a-goal">Goal</label>
            <textarea
              id="a2a-goal"
              value={goal}
              onChange={(e) => setGoal(e.target.value)}
              placeholder="Describe what you want the agent to do…"
              rows={4}
              className="w-full px-3 py-2 text-sm border border-input rounded-lg bg-background focus:outline-none focus:ring-2 focus:ring-primary resize-none"
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium mb-1" htmlFor="a2a-priority">Priority</label>
              <select
                id="a2a-priority"
                value={priority}
                onChange={(e) => setPriority(e.target.value)}
                className="w-full px-3 py-2 text-sm border border-input rounded-lg bg-background focus:outline-none focus:ring-1 focus:ring-primary"
              >
                <option value="normal">Normal</option>
                <option value="high">High</option>
                <option value="critical">Critical</option>
              </select>
            </div>
            <div>
              <label className="block text-xs font-medium mb-1" htmlFor="a2a-requester">Requester Agent ID</label>
              <input
                id="a2a-requester"
                value={requesterId}
                onChange={(e) => setRequesterId(e.target.value)}
                placeholder="optional"
                className="w-full px-3 py-2 text-sm border border-input rounded-lg bg-background focus:outline-none focus:ring-1 focus:ring-primary"
              />
            </div>
          </div>
          <div>
            <label className="block text-xs font-medium mb-1" htmlFor="a2a-callback">Callback URL</label>
            <input
              id="a2a-callback"
              value={callbackUrl}
              onChange={(e) => setCallbackUrl(e.target.value)}
              placeholder="https://your-service.com/callback"
              className="w-full px-3 py-2 text-sm border border-input rounded-lg bg-background focus:outline-none focus:ring-1 focus:ring-primary"
            />
          </div>
          <button
            onClick={() => submitMutation.mutate()}
            disabled={!goal.trim() || submitMutation.isPending}
            className="w-full flex items-center justify-center gap-2 py-2.5 bg-primary text-primary-foreground text-sm font-medium rounded-lg hover:opacity-90 disabled:opacity-50 transition-opacity"
          >
            {submitMutation.isPending
              ? <><Loader2 className="h-4 w-4 animate-spin" /> Dispatching…</>
              : <><Zap className="h-4 w-4" /> Dispatch Task</>}
          </button>

          {submitted && (
            <div className="bg-green-50 dark:bg-green-950/30 border border-green-200 dark:border-green-800 rounded-lg px-3 py-2 text-xs">
              <p className="text-green-700 dark:text-green-300 font-medium">Task dispatched!</p>
              <div className="flex items-center gap-1.5 mt-0.5">
                <span className="font-mono text-green-600 dark:text-green-400">{submitted.task_id.slice(0, 20)}…</span>
                <CopyBtn text={submitted.task_id} />
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Task list (3/5) */}
      <div className="lg:col-span-3 space-y-3">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold">
            Recent Tasks
            {tasks.length > 0 && <span className="text-muted-foreground font-normal ml-1.5">({tasks.length})</span>}
          </h3>
          <div className="flex items-center gap-2">
            {hasLiveTask(tasks) && (
              <span className="flex items-center gap-1 text-[10px] text-amber-600 dark:text-amber-400">
                <span className="w-1.5 h-1.5 rounded-full bg-amber-500 animate-pulse" />
                Live
              </span>
            )}
            <button
              onClick={() => refetch()}
              className="p-1.5 rounded-lg hover:bg-muted/60 text-muted-foreground"
              aria-label="Refresh tasks"
            >
              <RefreshCw className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>

        {isLoading && <div className="space-y-2">{Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-14 rounded-xl" />)}</div>}
        {!isLoading && tasks.length === 0 && (
          <EmptyState title="No tasks yet" description="Dispatch your first task using the form." />
        )}
        {tasks.map((t) => <TaskRow key={t.task_id} task={t} />)}
      </div>
    </div>
  );
}

// ── Agent card tab ────────────────────────────────────────────────────────────

function AgentCardTab() {
  const { data: card, isLoading, error } = useQuery<AgentCard>({
    queryKey: ["a2a-agent-card"],
    queryFn: () => a2aApi.agentCard(),
    staleTime: 300_000,
  });
  const [testResult, setTestResult] = useState<"idle" | "loading" | "ok" | "err">("idle");

  const testConnectivity = async () => {
    if (!card) return;
    setTestResult("loading");
    try {
      const res = await fetch(`${card.endpoint}/.well-known/agent.json`);
      setTestResult(res.ok ? "ok" : "err");
    } catch { setTestResult("err"); }
  };

  if (isLoading) return <div className="p-8 text-center"><Loader2 className="h-6 w-6 animate-spin mx-auto" /></div>;
  if (error || !card) return (
    <div className="p-8 text-center text-muted-foreground">
      <AlertCircle className="h-8 w-8 mx-auto mb-2 opacity-40" />
      <p className="text-sm">Failed to load agent card</p>
    </div>
  );

  return (
    <div className="max-w-2xl space-y-5">
      {/* Header */}
      <div className="bg-card border border-border rounded-xl p-5 space-y-4">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-lg font-bold">{card.name}</h2>
            <p className="text-xs text-muted-foreground font-mono">{card.agent_id} · v{card.version}</p>
          </div>
          <div className="flex gap-2">
            <button
              onClick={testConnectivity}
              disabled={testResult === "loading"}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs border border-border rounded-lg hover:bg-muted/60 disabled:opacity-50"
            >
              {testResult === "loading" ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
               : testResult === "ok" ? <Check className="h-3.5 w-3.5 text-green-500" />
               : testResult === "err" ? <AlertCircle className="h-3.5 w-3.5 text-red-500" />
               : <Zap className="h-3.5 w-3.5" />}
              Test Connectivity
            </button>
            <button
              onClick={() => { navigator.clipboard.writeText(JSON.stringify(card, null, 2)); toast({ kind: "success", message: "Agent card JSON copied." }); }}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs border border-border rounded-lg hover:bg-muted/60"
            >
              <Copy className="h-3.5 w-3.5" /> Copy JSON
            </button>
          </div>
        </div>

        <p className="text-sm text-muted-foreground">{card.description}</p>

        {/* Endpoint */}
        <div>
          <p className="text-xs font-semibold mb-1.5 text-muted-foreground uppercase tracking-wide">Endpoint</p>
          <div className="flex items-center gap-2 bg-muted/40 rounded-lg px-3 py-2">
            <code className="text-xs font-mono flex-1 text-muted-foreground">{card.endpoint}</code>
            <CopyBtn text={card.endpoint} />
          </div>
        </div>

        {/* Auth */}
        <div>
          <p className="text-xs font-semibold mb-1.5 text-muted-foreground uppercase tracking-wide">Authentication</p>
          <div className="flex items-center gap-2">
            <span className="flex items-center gap-1.5 text-xs bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300 px-3 py-1 rounded-full">
              <ShieldCheck className="h-3.5 w-3.5" /> {card.authentication?.scheme}
            </span>
            <span className="text-xs text-muted-foreground">{card.authentication?.header}</span>
          </div>
        </div>

        {/* Capabilities */}
        <div>
          <p className="text-xs font-semibold mb-1.5 text-muted-foreground uppercase tracking-wide">Capabilities</p>
          <div className="flex flex-wrap gap-1.5">
            {(card.capabilities ?? []).map((c) => (
              <span key={c} className="text-[10px] bg-muted px-2 py-0.5 rounded-full text-muted-foreground">{c.replace(/_/g, " ")}</span>
            ))}
          </div>
        </div>

        {/* Task types */}
        <div>
          <p className="text-xs font-semibold mb-1.5 text-muted-foreground uppercase tracking-wide">Task Types</p>
          <div className="flex flex-wrap gap-1.5">
            {(card.supported_task_types ?? []).map((t) => (
              <span key={t} className="text-[10px] bg-primary/10 text-primary px-2 py-0.5 rounded-full font-medium">{t}</span>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Remote agents tab ─────────────────────────────────────────────────────────

interface RemoteAgent { name: string; url: string; card?: AgentCard; error?: string }

function RemoteAgentsTab({ onDispatch }: { onDispatch: (endpoint: string) => void }) {
  const STORAGE_KEY = "a2a_remote_agents";
  const [agents, setAgents] = useState<RemoteAgent[]>(() => {
    try { return JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "[]"); } catch { return []; }
  });
  const [registerOpen, setRegisterOpen] = useState(false);
  const [regUrl, setRegUrl] = useState("");
  const [regName, setRegName] = useState("");
  const [regLoading, setRegLoading] = useState(false);

  const save = (updated: RemoteAgent[]) => {
    setAgents(updated);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(updated));
  };

  const register = async () => {
    setRegLoading(true);
    try {
      const res = await fetch(regUrl.trim());
      const card: AgentCard = await res.json();
      save([...agents, { name: regName || card.name, url: regUrl.trim(), card }]);
      setRegisterOpen(false);
      setRegUrl(""); setRegName("");
    } catch { save([...agents, { name: regName || regUrl, url: regUrl.trim(), error: "Failed to fetch card" }]); }
    finally { setRegLoading(false); }
  };

  const ping = async (idx: number) => {
    try {
      const res = await fetch(agents[idx].url);
      const card: AgentCard = await res.json();
      const updated = [...agents];
      updated[idx] = { ...updated[idx], card, error: undefined };
      save(updated);
    } catch {
      const updated = [...agents];
      updated[idx] = { ...updated[idx], error: "Ping failed" };
      save(updated);
    }
  };

  const remove = (idx: number) => save(agents.filter((_, i) => i !== idx));

  return (
    <div className="space-y-4 max-w-3xl">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold">Remote Agent Registry</h3>
        <button
          onClick={() => setRegisterOpen(true)}
          className="flex items-center gap-1.5 px-3 py-1.5 bg-primary text-primary-foreground text-xs font-medium rounded-lg hover:opacity-90"
        >
          <Plus className="h-3.5 w-3.5" /> Register Agent
        </button>
      </div>

      {agents.length === 0 ? (
        <EmptyState title="No remote agents registered" description="Add agents to build your A2A network." />
      ) : (
        <div className="space-y-3">
          {agents.map((a, i) => (
            <div key={i} className="bg-card border border-border rounded-xl p-4 space-y-3">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <p className="text-sm font-semibold">{a.name}</p>
                  {a.card && <p className="text-[10px] text-muted-foreground">v{a.card.version}</p>}
                  {a.error && <p className="text-[10px] text-red-500">{a.error}</p>}
                </div>
                <div className="flex gap-1.5">
                  <button onClick={() => ping(i)} className="p-1.5 rounded text-muted-foreground hover:text-foreground" title="Ping"><RefreshCw className="h-3.5 w-3.5" /></button>
                  <button onClick={() => onDispatch(a.card?.endpoint ?? a.url)} className="p-1.5 rounded text-muted-foreground hover:text-primary" title="Dispatch"><Zap className="h-3.5 w-3.5" /></button>
                  <button onClick={() => remove(i)} className="p-1.5 rounded text-muted-foreground hover:text-destructive" title="Remove"><Trash2 className="h-3.5 w-3.5" /></button>
                </div>
              </div>
              <div className="flex items-center gap-2 bg-muted/30 rounded px-2 py-1">
                <ExternalLink className="h-3 w-3 text-muted-foreground shrink-0" />
                <code className="text-[10px] font-mono text-muted-foreground truncate">{a.url}</code>
              </div>
              {a.card && (
                <div className="flex flex-wrap gap-1">
                  {(a.card.capabilities ?? []).slice(0, 5).map((c) => (
                    <span key={c} className="text-[10px] bg-muted px-1.5 py-0.5 rounded text-muted-foreground">{c.replace(/_/g, " ")}</span>
                  ))}
                  <span className="text-[10px] text-muted-foreground">{a.card.supported_task_types?.length ?? 0} task types</span>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {registerOpen && (
        <div className="fixed inset-0 z-[300] flex items-center justify-center p-4">
          <div className="absolute inset-0 bg-black/50" onClick={() => setRegisterOpen(false)} />
          <div className="relative bg-card border border-border rounded-xl shadow-2xl max-w-sm w-full p-5 space-y-4">
            <h2 className="text-base font-semibold">Register Remote Agent</h2>
            <div className="space-y-3">
              <div>
                <label className="block text-xs font-medium mb-1">Agent Card URL</label>
                <input value={regUrl} onChange={(e) => setRegUrl(e.target.value)} placeholder="https://agent.example.com/.well-known/agent.json"
                  className="w-full px-3 py-2 text-sm border border-input rounded-lg bg-background focus:outline-none focus:ring-2 focus:ring-primary" />
              </div>
              <div>
                <label className="block text-xs font-medium mb-1">Display Name (optional)</label>
                <input value={regName} onChange={(e) => setRegName(e.target.value)} placeholder="My Agent"
                  className="w-full px-3 py-2 text-sm border border-input rounded-lg bg-background focus:outline-none focus:ring-2 focus:ring-primary" />
              </div>
            </div>
            <div className="flex gap-3">
              <button onClick={register} disabled={!regUrl.trim() || regLoading}
                className="flex-1 py-2.5 bg-primary text-primary-foreground text-sm font-medium rounded-lg hover:opacity-90 disabled:opacity-50">
                {regLoading ? "Fetching…" : "Register"}
              </button>
              <button onClick={() => setRegisterOpen(false)} className="px-4 py-2.5 border border-input text-sm rounded-lg hover:bg-muted/50">Cancel</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export function A2APage() {
  const [tab, setTab] = useState<Tab>("tasks");

  return (
    <div className="space-y-6 max-w-5xl">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold flex items-center gap-2">
          <Network className="h-6 w-6 text-primary" aria-hidden="true" />
          A2A Network
        </h1>
        <p className="text-sm text-muted-foreground mt-0.5">
          Agent-to-agent protocol: dispatch tasks, observe the network, manage remote agents
        </p>
      </div>

      {/* Tabs */}
      <div role="tablist" className="flex gap-1 border-b border-border">
        {([
          { key: "tasks",   label: "Tasks" },
          { key: "card",    label: "Agent Card" },
          { key: "remotes", label: "Remote Agents" },
        ] as { key: Tab; label: string }[]).map(({ key, label }) => (
          <button
            key={key}
            role="tab"
            aria-selected={tab === key}
            onClick={() => setTab(key)}
            className={`px-4 py-2.5 text-sm font-medium border-b-2 -mb-px transition-colors ${
              tab === key ? "border-primary text-primary" : "border-transparent text-muted-foreground hover:text-foreground"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === "tasks"   && <TasksTab />}
      {tab === "card"    && <AgentCardTab />}
      {tab === "remotes" && <RemoteAgentsTab onDispatch={() => setTab("tasks")} />}
    </div>
  );
}
