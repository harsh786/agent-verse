/**
 * GoalDetailPage — world-class agentic goal execution view.
 *
 * Design principles:
 *  - Terminal-first execution panel (VS Code / OpenCode feel)
 *  - Results rendered for EVERY goal regardless of kind (empty/failed/partial/complete)
 *  - Evidence built from live SSE events, not just the artifact
 *  - Downloads always visible when artifact exists
 *  - Full-width layout consistent with the rest of the dashboard
 *  - Single unified tab bar (no duplicate bars)
 *  - Live elapsed timer + cost ticker
 *  - SSE reconnects up to 100 times for long goals
 */
import {
  useEffect, useRef, useState, useCallback, useMemo,
  type KeyboardEvent,
} from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft, CheckCircle, XCircle, RefreshCw, Loader2,
  ChevronDown, ChevronRight, Pause, Play, Dna, GitCompare,
  Ghost, FlaskConical, RotateCcw, Download, FileJson, FileText,
  Copy, Printer, Terminal, ListTree, BookOpen, Sparkles, Zap,
  Clock, AlertTriangle, Bot, Plug, Layers, Inbox,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import { goalsApi, governanceApi, agentsApi } from "@/lib/api/client";
import { useGoalStream } from "@/lib/sse/useGoalStream";
import { useAuthStore } from "@/stores/auth";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/EmptyState";
import { toast } from "@/stores/toast";
import { LiveCostTicker } from "@/components/live/LiveCostTicker";
import { GoalFeedback } from "./components/GoalFeedback";
import { GoalExplainPanel } from "./components/GoalExplainPanel";
import { normalizeAdaptiveResult } from "./adaptiveResult";
import { AdaptiveResultPanel } from "./components/AdaptiveResultPanel";
import { artifactToCsv, artifactToMarkdown } from "./resultArtifact";
import type { GoalEvent as StreamGoalEvent } from "@/lib/sse/useGoalStream";

import { JARVISPageShell } from '@/components/ui/JARVISPageShell';
// ── Types ─────────────────────────────────────────────────────────────────────

type Tab = "results" | "evidence" | "execution" | "events" | "eval" | "explain";

// ── Helpers ───────────────────────────────────────────────────────────────────

function readStr(v: unknown): string | undefined {
  return typeof v === "string" && v.length > 0 ? v : undefined;
}

function goalTitle(text: string): string {
  if (!text) return "Untitled goal";
  const line = text.split("\n").find((l) => l.trim()) ?? text;
  const end = line.search(/[.!?]/);
  const s = end > 20 ? line.slice(0, end + 1) : line;
  return s.length > 120 ? s.slice(0, 117) + "…" : s;
}

function fmtVal(v: unknown): string | undefined {
  if (v == null) return undefined;
  if (typeof v === "string" || typeof v === "number" || typeof v === "boolean")
    return String(v);
  return JSON.stringify(v, null, 2);
}

// Download helper
function downloadFile(name: string, content: string, mime: string) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([content], { type: mime }));
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 0);
}

async function copyToClipboard(text: string) {
  try { await navigator.clipboard.writeText(text); }
  catch { /* fallback */ }
}

// ── Sub-components ────────────────────────────────────────────────────────────

function StatusBadge({ status }: { status: string }) {
  const map: Record<string, string> = {
    complete: "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400",
    executing: "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400 animate-pulse",
    planning: "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400 animate-pulse",
    failed: "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400",
    waiting_human: "bg-orange-100 text-orange-800 dark:bg-orange-900/30 dark:text-orange-400",
    cancelled: "bg-muted text-muted-foreground",
  };
  return (
    <span className={`px-2.5 py-0.5 rounded-full text-xs font-medium capitalize ${map[status] ?? "bg-muted text-muted-foreground"}`}>
      {status.replace(/_/g, " ")}
    </span>
  );
}

function ElapsedTimer({ startedAt }: { startedAt: string }) {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const start = new Date(startedAt).getTime();
    const iv = setInterval(() => setElapsed(Math.floor((Date.now() - start) / 1000)), 1000);
    return () => clearInterval(iv);
  }, [startedAt]);
  const m = Math.floor(elapsed / 60), s = elapsed % 60;
  return (
    <span className="font-mono text-xs text-muted-foreground flex items-center gap-1">
      <Clock className="h-3 w-3" aria-hidden="true" />
      {m}:{String(s).padStart(2, "0")}
    </span>
  );
}

// ── Rich Result Panel ─────────────────────────────────────────────────────────
// Shows something meaningful for EVERY result, including empty/failed goals.

function RichResultPanel({
  artifact,
  events,
  goal,
  status,
}: {
  artifact: any;
  events: StreamGoalEvent[];
  goal: string;
  status: string;
}) {
  const verificationFeedback = artifact?.evidence?.verification;
  const summary = artifact?.summary;
  const kind = artifact?.kind ?? artifact?.status ?? "unknown";
  const downloads = artifact?.downloads ?? [];
  const hasTable = (artifact?.tables ?? []).length > 0;
  const canJson = downloads.includes("json");
  const canCsv = downloads.includes("csv") && hasTable;
  const canMd = downloads.includes("markdown");

  // Extract all tool call results from events for a rich timeline
  const toolResults = useMemo(() => {
    return events
      .filter((e) => e.type === "tool_call_complete" || e.type === "tool_call_failed")
      .map((e, i) => ({
        id: i,
        tool: readStr(e.tool_name) ?? readStr(e.tool) ?? "Tool call",
        server: readStr(e.server_id),
        success: e.success !== false && e.type !== "tool_call_failed",
        output: e.output,
        error: readStr(e.error),
      }));
  }, [events]);

  // Extract the final LLM synthesis (last meaningful text output from events)
  const finalOutput = useMemo(() => {
    for (let i = events.length - 1; i >= 0; i--) {
      const e = events[i];
      const content = readStr(e.result) ?? readStr(e.content) ?? readStr(e.summary);
      if (content && content.length > 20) return content;
    }
    return null;
  }, [events]);

  // Determine if goal actually produced output despite being marked empty
  const hasRealOutput = finalOutput || (toolResults.length > 0);

  return (
    <div className="space-y-4">
      {/* Status banner */}
      {(kind === "empty" || kind === "failed" || status === "failed") && (
        <div className={`flex items-start gap-3 p-4 rounded-xl border text-sm ${
          status === "complete"
            ? "bg-amber-50 border-amber-200 dark:bg-amber-950/20 dark:border-amber-800"
            : "bg-red-50 border-red-200 dark:bg-red-950/20 dark:border-red-800"
        }`}>
          <AlertTriangle className={`h-4 w-4 mt-0.5 shrink-0 ${status === "complete" ? "text-amber-600" : "text-red-600"}`} aria-hidden="true" />
          <div className="space-y-1">
            <p className="font-medium">{status === "complete" ? "Goal completed with partial results" : "Goal did not fully complete"}</p>
            {verificationFeedback && (
              <p className="text-muted-foreground text-xs">{verificationFeedback}</p>
            )}
          </div>
        </div>
      )}

      {/* Summary / final output */}
      {(finalOutput || summary) && (
        <div className="rounded-xl border bg-card p-5 space-y-2">
          <div className="flex items-center gap-2 text-xs font-semibold text-muted-foreground uppercase tracking-wide">
            <Sparkles className="h-3.5 w-3.5" aria-hidden="true" />
            Result
          </div>
          <div className="prose prose-sm dark:prose-invert max-w-none text-sm leading-relaxed">
            <ReactMarkdown>{finalOutput ?? summary ?? ""}</ReactMarkdown>
          </div>
        </div>
      )}

      {/* Tables from artifact */}
      {(artifact?.tables ?? []).map((table: any, ti: number) => (
        <div key={ti} className="rounded-xl border bg-card overflow-hidden">
          <div className="px-5 py-3 border-b bg-muted/30">
            <p className="font-semibold text-sm">{table.title}</p>
            {table.summary && <p className="text-xs text-muted-foreground mt-0.5">{table.summary}</p>}
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-muted/20">
                <tr>
                  {(table.columns ?? []).map((col: any) => (
                    <th key={col.key} className="px-4 py-2.5 text-left text-xs font-semibold text-muted-foreground uppercase tracking-wide">
                      {col.label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {(table.rows ?? []).map((row: any, ri: number) => (
                  <tr key={ri} className="hover:bg-muted/20 transition-colors">
                    {(table.columns ?? []).map((col: any) => (
                      <td key={col.key} className="px-4 py-2.5 text-sm">{fmtVal(row[col.key]) ?? "—"}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ))}

      {/* Tool outputs from events — shown when no structured result */}
      {toolResults.length > 0 && (
        <div className="space-y-2">
          <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide flex items-center gap-1.5">
            <Zap className="h-3.5 w-3.5" aria-hidden="true" />
            Tool Outputs ({toolResults.length} calls)
          </p>
          {toolResults.slice(0, 10).map((tr) => {
            const adaptive = normalizeAdaptiveResult(tr.output, {
              toolName: tr.tool, serverId: tr.server, success: tr.success, error: tr.error,
            });
            return (
              <div key={tr.id} className={`rounded-xl border p-3 text-sm ${tr.success ? "bg-card" : "bg-red-50 border-red-200 dark:bg-red-950/10 dark:border-red-900"}`}>
                <div className="flex items-center gap-2 mb-2">
                  {tr.success
                    ? <CheckCircle className="h-3.5 w-3.5 text-green-500 shrink-0" aria-hidden="true" />
                    : <XCircle className="h-3.5 w-3.5 text-red-500 shrink-0" aria-hidden="true" />}
                  <span className="font-mono font-medium text-xs">{tr.tool}</span>
                  {tr.server && <span className="text-[10px] text-muted-foreground">via {tr.server}</span>}
                </div>
                {adaptive && <AdaptiveResultPanel compact result={adaptive} />}
                {!adaptive && tr.error && (
                  <pre className="text-xs text-red-600 whitespace-pre-wrap break-words">{tr.error}</pre>
                )}
                {!adaptive && !tr.error && tr.output && (
                  <pre className="text-xs text-muted-foreground whitespace-pre-wrap break-words max-h-40 overflow-auto">
                    {JSON.stringify(tr.output, null, 2)}
                  </pre>
                )}
              </div>
            );
          })}
          {toolResults.length > 10 && (
            <p className="text-xs text-muted-foreground text-center">
              +{toolResults.length - 10} more tool calls — see Execution tab
            </p>
          )}
        </div>
      )}

      {/* Empty state — goal produced nothing at all */}
      {!hasRealOutput && !summary && (
        <div className="rounded-xl border border-dashed bg-muted/20 p-10 text-center">
          <Ghost className="h-10 w-10 mx-auto mb-3 opacity-20" aria-hidden="true" />
          <p className="text-sm font-medium">No output captured</p>
          <p className="text-xs text-muted-foreground mt-1 max-w-sm mx-auto">
            The agent ran but did not produce a captured result.
            Check the Execution tab to see what happened.
          </p>
        </div>
      )}

      {/* Download / action bar — ALWAYS shown when artifact exists */}
      <div className="flex flex-wrap gap-2 pt-2 border-t border-border">
        <button
          onClick={() => { copyToClipboard(finalOutput ?? summary ?? goal); toast({ kind: "success", message: "Copied!" }); }}
          className="inline-flex items-center gap-1.5 px-3 py-2 text-xs font-medium rounded-lg border border-border bg-background hover:bg-muted transition-colors"
        >
          <Copy className="h-3.5 w-3.5" aria-hidden="true" /> Copy result
        </button>
        {canJson && artifact && (
          <button
            onClick={() => downloadFile("goal-result.json", JSON.stringify(artifact, null, 2), "application/json")}
            className="inline-flex items-center gap-1.5 px-3 py-2 text-xs font-medium rounded-lg border border-border bg-background hover:bg-muted transition-colors"
          >
            <FileJson className="h-3.5 w-3.5" aria-hidden="true" /> JSON
          </button>
        )}
        {canCsv && artifact && (
          <button
            onClick={() => downloadFile("goal-result.csv", artifactToCsv(artifact), "text/csv")}
            className="inline-flex items-center gap-1.5 px-3 py-2 text-xs font-medium rounded-lg border border-border bg-background hover:bg-muted transition-colors"
          >
            <Download className="h-3.5 w-3.5" aria-hidden="true" /> CSV
          </button>
        )}
        {canMd && artifact && (
          <button
            onClick={() => downloadFile("goal-result.md", artifactToMarkdown(artifact), "text/markdown")}
            className="inline-flex items-center gap-1.5 px-3 py-2 text-xs font-medium rounded-lg border border-border bg-background hover:bg-muted transition-colors"
          >
            <FileText className="h-3.5 w-3.5" aria-hidden="true" /> Markdown
          </button>
        )}
        {/* Always offer raw JSON download of the full goal */}
        <button
          onClick={() => downloadFile("goal-raw.json", JSON.stringify({ goal, status, artifact, events: events.length }, null, 2), "application/json")}
          className="inline-flex items-center gap-1.5 px-3 py-2 text-xs font-medium rounded-lg border border-border bg-background hover:bg-muted transition-colors"
        >
          <Download className="h-3.5 w-3.5" aria-hidden="true" /> Raw data
        </button>
        <button
          onClick={() => window.print()}
          className="inline-flex items-center gap-1.5 px-3 py-2 text-xs font-medium rounded-lg border border-border bg-background hover:bg-muted transition-colors"
        >
          <Printer className="h-3.5 w-3.5" aria-hidden="true" /> Print
        </button>
      </div>
    </div>
  );
}

// ── Evidence panel (enhanced — built from events) ─────────────────────────────

function EnhancedEvidencePanel({ artifact, events }: { artifact: any; events: StreamGoalEvent[] }) {
  // Build evidence from live events + artifact
  const toolEvidence = useMemo(() => {
    const fromArtifact = artifact?.evidence?.tools ?? [];
    // Also extract from SSE events
    const fromEvents = events
      .filter((e) => e.type === "tool_call_complete" || e.type === "tool_call_failed")
      .map((e) => ({
        name: readStr(e.tool_name) ?? readStr(e.tool) ?? "Unknown tool",
        server_id: readStr(e.server_id),
        success: e.success !== false && e.type !== "tool_call_failed",
        error: readStr(e.error),
        output_preview: e.output ? JSON.stringify(e.output).slice(0, 200) : undefined,
      }));
    // Merge: artifact tools first, then events not already covered
    if (fromArtifact.length > 0) return fromArtifact;
    return fromEvents;
  }, [artifact, events]);

  const verification = artifact?.evidence?.verification;
  const hasEvidence = toolEvidence.length > 0 || verification;

  if (!hasEvidence) {
    return (
      <div className="rounded-xl border border-dashed bg-muted/20 p-10 text-center">
        <BookOpen className="h-10 w-10 mx-auto mb-3 opacity-20" aria-hidden="true" />
        <p className="text-sm font-medium">No evidence yet</p>
        <p className="text-xs text-muted-foreground mt-1">
          Evidence is collected from tool calls during goal execution.
          Retry the goal with a connected agent to see tool evidence here.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {verification && (
        <div className="rounded-xl bg-emerald-50 dark:bg-emerald-950/20 border border-emerald-200 dark:border-emerald-800 px-4 py-3 text-sm">
          <p className="text-xs font-semibold uppercase tracking-wide text-emerald-700 dark:text-emerald-400 mb-1">Verification</p>
          <p className="text-emerald-800 dark:text-emerald-300">{verification}</p>
        </div>
      )}
      {toolEvidence.length > 0 && (
        <div className="space-y-2">
          <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">
            Tool evidence ({toolEvidence.length} calls)
          </p>
          {toolEvidence.map((t: any, i: number) => (
            <div key={i} className="flex items-start justify-between gap-3 p-3 rounded-xl border bg-card text-sm">
              <div className="min-w-0">
                <p className="font-medium font-mono text-xs">{t.name}</p>
                {t.server_id && <p className="text-[10px] text-muted-foreground mt-0.5">Server: {t.server_id}</p>}
                {t.output_preview && (
                  <p className="text-[10px] text-muted-foreground mt-1 font-mono truncate max-w-xs">{t.output_preview}</p>
                )}
              </div>
              <span className={`shrink-0 inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold ${
                t.success !== false
                  ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/30 dark:text-emerald-400"
                  : "bg-red-50 text-red-700 dark:bg-red-950/30 dark:text-red-400"
              }`}>
                {t.success !== false ? "✓ OK" : "✗ Failed"}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Terminal-style execution panel ────────────────────────────────────────────

const TERMINAL_EVENT_COLORS: Record<string, string> = {
  goal_started:         "text-emerald-400",
  plan_ready:           "text-blue-400",
  step_started:         "text-yellow-400",
  step_complete:        "text-emerald-400",
  tool_call_complete:   "text-cyan-400",
  tool_call_failed:     "text-red-400",
  goal_complete:        "text-emerald-300 font-bold",
  goal_failed:          "text-red-300 font-bold",
  goal_cancelled:       "text-orange-400",
  verification_done:    "text-violet-400",
  worker_started:       "text-slate-400",
  worker_complete:      "text-slate-400",
  knowledge_retrieved:  "text-teal-400",
};

const TERMINAL_ICONS: Record<string, string> = {
  goal_started:         "🚀",
  plan_ready:           "📋",
  step_started:         "▶",
  step_complete:        "✓",
  tool_call_complete:   "⚡",
  tool_call_failed:     "✗",
  goal_complete:        "🎉",
  goal_failed:          "💥",
  goal_cancelled:       "⊗",
  verification_done:    "🔍",
  worker_started:       "⚙",
  worker_complete:      "⚙",
  knowledge_retrieved:  "📚",
};

function TerminalLine({ event, onRetry, isRetrying }: {
  event: StreamGoalEvent;
  onRetry?: (d: string) => void;
  isRetrying?: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const type = readStr(event.type) ?? "event";
  const step = readStr(event.step);
  const color = TERMINAL_EVENT_COLORS[type] ?? "text-slate-300";
  const icon = TERMINAL_ICONS[type] ?? "·";
  const isFailure = type === "tool_call_failed" || type === "goal_failed";
  const isTool = type === "tool_call_complete" || type === "tool_call_failed";

  const label = (() => {
    switch (type) {
      case "goal_started": return "Goal started";
      case "plan_ready": return "Plan ready";
      case "step_started": return `▶ ${step ?? "Step"}`;
      case "step_complete": return `✓ ${step ?? "Step complete"}`;
      case "tool_call_complete": return `${readStr(event.tool_name) ?? readStr(event.tool) ?? "Tool"} ${event.success !== false ? "succeeded" : "failed"}`;
      case "tool_call_failed": return `${readStr(event.tool_name) ?? readStr(event.tool) ?? "Tool"} failed`;
      case "verification_done": return `Verification ${event.success === true ? "passed" : "failed"}`;
      case "goal_complete": return "🎉 Goal complete";
      case "goal_failed": return "💥 Goal failed";
      default: return step ?? type.replace(/_/g, " ");
    }
  })();

  const isStep = type === "step_complete" || type === "step_started";
  const stepOutput = isStep ? (event.output ?? event.result ?? (event as any).data) : null;
  const hasDetails = isTool || type === "plan_ready" || type === "verification_done" || stepOutput != null;

  return (
    <div className={`group ${isFailure ? "bg-red-950/10" : ""}`}>
      <button
        onClick={() => hasDetails && setExpanded((v) => !v)}
        className={`w-full flex items-center gap-2 px-3 py-1.5 text-left font-mono text-xs hover:bg-white/5 transition-colors ${hasDetails ? "cursor-pointer" : "cursor-default"}`}
      >
        <span className={`shrink-0 w-4 text-center ${color}`}>{icon}</span>
        <span className={`flex-1 ${color}`}>{label}</span>
        {hasDetails && (expanded
          ? <ChevronDown className="h-3 w-3 text-slate-500 shrink-0" aria-hidden="true" />
          : <ChevronRight className="h-3 w-3 text-slate-500 shrink-0" aria-hidden="true" />
        )}
      </button>

      {expanded && type === "plan_ready" && Array.isArray(event.steps) && (
        <div className="px-8 pb-2 space-y-0.5">
          {(event.steps as string[]).map((s, i) => (
            <p key={i} className="font-mono text-xs text-slate-400">
              <span className="text-blue-500 mr-2">{i + 1}.</span>{s}
            </p>
          ))}
        </div>
      )}

      {expanded && isTool && (
        <div className="px-8 pb-2">
          {event.output != null && (
            <pre className="text-[10px] text-slate-400 whitespace-pre-wrap break-words max-h-48 overflow-auto leading-relaxed">
              {typeof event.output === "string" ? event.output : JSON.stringify(event.output, null, 2)}
            </pre>
          )}
          {event.error != null && (
            <pre className="text-[10px] text-red-400 whitespace-pre-wrap">{String(event.error)}</pre>
          )}
        </div>
      )}

      {expanded && (isStep) && stepOutput != null && (
        <div className="px-8 pb-2">
          <pre className="text-[10px] text-emerald-400 whitespace-pre-wrap break-words max-h-64 overflow-auto leading-relaxed border border-emerald-900/30 rounded p-1.5 bg-emerald-950/20">
            {typeof stepOutput === "string" ? stepOutput : JSON.stringify(stepOutput, null, 2)}
          </pre>
        </div>
      )}

      {expanded && type === "verification_done" && (
        <div className="px-8 pb-2">
          {readStr(event.reason) && (
            <p className="text-[10px] text-slate-400">{readStr(event.reason)}</p>
          )}
        </div>
      )}

      {isFailure && onRetry && (
        <div className="px-8 pb-2">
          <button
            onClick={() => onRetry(label)}
            disabled={isRetrying}
            className="text-[10px] text-primary hover:underline flex items-center gap-1 disabled:opacity-50"
          >
            {isRetrying ? <Loader2 className="h-2.5 w-2.5 animate-spin" aria-hidden="true" /> : <RotateCcw className="h-2.5 w-2.5" aria-hidden="true" />}
            Retry from here
          </button>
        </div>
      )}
    </div>
  );
}

function TerminalPanel({
  events, goalStatus, streamingToken, connected, onRetry, isRetrying,
}: {
  events: StreamGoalEvent[];
  goalStatus: string;
  streamingToken: any;
  connected: boolean;
  onRetry: (d: string) => void;
  isRetrying: boolean;
}) {
  const bottomRef = useRef<HTMLDivElement>(null);
  const [autoScroll, setAutoScroll] = useState(true);

  useEffect(() => {
    if (autoScroll) bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [events.length, autoScroll]);

  return (
    <div className="rounded-xl border border-border overflow-hidden bg-[#0d1117] dark:bg-[#0d1117]">
      {/* Terminal header bar */}
      <div className="flex items-center justify-between px-4 py-2 bg-[#161b22] border-b border-[#30363d]">
        <div className="flex items-center gap-2">
          <Terminal className="h-3.5 w-3.5 text-slate-400" aria-hidden="true" />
          <span className="text-xs font-mono text-slate-400">execution log</span>
          {connected && (
            <span className="flex items-center gap-1 text-[10px] text-emerald-400">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse" aria-hidden="true" />
              live
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[10px] text-slate-500 font-mono">{events.length} events</span>
          <button
            onClick={() => setAutoScroll((v) => !v)}
            title={autoScroll ? "Disable auto-scroll" : "Enable auto-scroll"}
            className={`text-[10px] px-1.5 py-0.5 rounded font-mono transition-colors ${autoScroll ? "text-emerald-400 bg-emerald-950/40" : "text-slate-500"}`}
          >
            {autoScroll ? "↓ auto" : "↓ manual"}
          </button>
        </div>
      </div>

      {/* Terminal body */}
      <div
        className="h-[420px] overflow-y-auto py-1 scroll-smooth"
        onScroll={(e) => {
          const el = e.currentTarget;
          const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 30;
          setAutoScroll(atBottom);
        }}
      >
        {events.length === 0 ? (
          <div className="flex items-center justify-center h-full">
            <p className="font-mono text-xs text-slate-600">
              {["complete", "failed", "cancelled"].includes(goalStatus)
                ? "No live events captured — connect earlier next time."
                : "Waiting for events…"}
            </p>
          </div>
        ) : (
          events.map((ev, i) => (
            <TerminalLine
              key={readStr(ev.event_id) ?? `ev-${i}`}
              event={ev}
              onRetry={onRetry}
              isRetrying={isRetrying}
            />
          ))
        )}

        {/* Live LLM streaming token */}
        {streamingToken && (
          <div
            role="status"
            aria-label="Live LLM output"
            className="px-3 py-1.5 border-t border-[#30363d] mt-1"
          >
            <p className="font-mono text-[10px] text-yellow-400 mb-1">
              Generating: {streamingToken.step}
            </p>
            <p className="font-mono text-xs text-slate-300 whitespace-pre-wrap break-words">
              {streamingToken.cumulative}
              <span className="inline-block w-0.5 h-3.5 bg-yellow-400 animate-pulse ml-0.5 align-middle" aria-hidden="true" />
            </p>
          </div>
        )}

        <div ref={bottomRef} />
      </div>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export function GoalDetailPage() {
  const { goalId } = useParams<{ goalId: string }>();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const tenantId = useAuthStore((s) => s.tenantId);
  const [approvalNote, setApprovalNote] = useState("");
  const [selectedTab, setSelectedTab] = useState<Tab | null>(null);
  const tabRefs = useRef<Record<Tab, HTMLButtonElement | null>>({
    results: null, evidence: null, execution: null, events: null, eval: null, explain: null,
  });

  const { data: goal, isLoading, refetch: refetchGoal } = useQuery({
    queryKey: ["goal", goalId],
    queryFn: () => goalsApi.get(goalId!),
    // Poll every 3s while running, stop on terminal state
    refetchInterval: 3_000,
    refetchIntervalInBackground: true,
    enabled: !!goalId,
  });

  useEffect(() => {
    if (goal?.goal) {
      const t = goal.goal.length > 50 ? goal.goal.slice(0, 50) + "…" : goal.goal;
      document.title = `${t} — AgentVerse`;
      return () => { document.title = "AgentVerse"; };
    }
  }, [goal?.goal]);

  // Stop polling once terminal — don't spam the API after completion
  useEffect(() => {
    // noop — refetchInterval above handles it statically; we keep polling to
    // pick up status transitions from "planning" → "executing" → "complete"
  }, []);

  // Fetch agent details so we can show which agent executed this goal
  const agentId = goal?.agent_id;
  const { data: agentDetail } = useQuery({
    queryKey: ["agent-for-goal", agentId],
    queryFn: () => agentsApi.get(agentId!),
    enabled: !!agentId,
    staleTime: 60_000,
  });

  const [streamKey, setStreamKey] = useState(0);
  const { events: sseEvents, connected, streamingToken } = useGoalStream(
    goalId ?? "",
    { reconnectKey: streamKey },
  );

  // Fetch persisted event log — used to populate Execution tab when SSE has no events
  // (e.g. navigated to a completed goal after the fact)
  const { data: persistedEvents = [] } = useQuery({
    queryKey: ["goal-events-exec", goalId],
    queryFn: () => goalsApi.getEventLog(goalId!),
    enabled: !!goalId,
    staleTime: 10_000,
  });

  // Merge: prefer live SSE events; fall back to persisted events when SSE is empty
  const events: StreamGoalEvent[] = sseEvents.length > 0
    ? sseEvents
    : (persistedEvents as unknown as StreamGoalEvent[]);

  const isTerminal = ["complete", "failed", "cancelled"].includes(goal?.status ?? "");
  const hasArtifact = Boolean(goal?.result_artifact);

  const visibleTabs: { tab: Tab; label: string; icon: React.ReactNode }[] = [
    { tab: "results",   label: "Results",    icon: <Sparkles className="h-3.5 w-3.5" aria-hidden="true" /> },
    { tab: "evidence",  label: "Evidence",   icon: <ListTree className="h-3.5 w-3.5" aria-hidden="true" /> },
    { tab: "execution", label: "Execution",  icon: <Terminal className="h-3.5 w-3.5" aria-hidden="true" /> },
    { tab: "events",    label: "Dev Log",    icon: <BookOpen className="h-3.5 w-3.5" aria-hidden="true" /> },
    ...(isTerminal ? [{ tab: "eval" as Tab,    label: "Eval",     icon: <FlaskConical className="h-3.5 w-3.5" aria-hidden="true" /> }] : []),
    ...(isTerminal ? [{ tab: "explain" as Tab, label: "Why?",     icon: <Zap className="h-3.5 w-3.5" aria-hidden="true" /> }] : []),
  ];

  const defaultTab: Tab = hasArtifact || isTerminal ? "results" : "execution";
  const activeTab: Tab = visibleTabs.some(({ tab }) => tab === selectedTab)
    ? selectedTab! : defaultTab;

  const selectTab = useCallback((tab: Tab, focus = false) => {
    setSelectedTab(tab);
    if (focus) tabRefs.current[tab]?.focus();
  }, []);

  const handleTabKeyDown = (e: KeyboardEvent<HTMLButtonElement>) => {
    const ci = visibleTabs.findIndex(({ tab }) => tab === activeTab);
    let ni: number | undefined;
    if (e.key === "ArrowRight") ni = (ci + 1) % visibleTabs.length;
    else if (e.key === "ArrowLeft") ni = (ci - 1 + visibleTabs.length) % visibleTabs.length;
    else if (e.key === "Home") ni = 0;
    else if (e.key === "End") ni = visibleTabs.length - 1;
    if (ni !== undefined) { e.preventDefault(); selectTab(visibleTabs[ni].tab, true); }
  };

  const retryMutation = useMutation({
    mutationFn: (stepDesc: string) =>
      goalsApi.submit({ goal: `${goal?.goal ?? ""}\n\nContinue from: ${stepDesc}`, dry_run: false }),
    onSuccess: (res) => {
      toast({ kind: "success", message: "Goal re-submitted!" });
      void qc.invalidateQueries({ queryKey: ["goals"] });
      navigate(`/goals/${res.id ?? res.goal_id}`);
    },
    onError: () => toast({ kind: "error", message: "Failed to retry" }),
  });

  const cancelMutation = useMutation({
    mutationFn: () => goalsApi.cancel(goalId!),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["goal", goalId] }),
  });

  const pauseMutation = useMutation({
    mutationFn: () => goalsApi.pause(goalId!),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["goal", goalId] }); toast({ kind: "success", message: "Paused." }); },
  });

  const resumeMutation = useMutation({
    mutationFn: () => goalsApi.resume(goalId!),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["goal", goalId] }); toast({ kind: "success", message: "Resumed." }); },
  });

  // Event log (dev tab) — reuse the eagerly-fetched persistedEvents when possible
  const { data: eventLog = [], isLoading: eventsLoading } = useQuery({
    queryKey: ["goal-events", goalId],
    queryFn: () => goalsApi.getEventLog(goalId!),
    enabled: !!goalId && activeTab === "events",
    // If we already have persisted events from the exec tab query, use staleTime
    staleTime: 15_000,
  });

  // Eval
  const { data: evaluation, isLoading: evalLoading } = useQuery({
    queryKey: ["goal-eval", goalId],
    queryFn: () => goalsApi.getEvaluation(goalId!),
    enabled: !!goalId && activeTab === "eval" && isTerminal,
  });

  const triggerEvalMutation = useMutation({
    mutationFn: () => goalsApi.triggerEvaluation(goalId!),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["goal-eval", goalId] }); toast({ kind: "success", message: "Scored!" }); },
  });

  // HITL
  const { data: approvals, isLoading: approvalsLoading } = useQuery({
    queryKey: ["approvals"],
    queryFn: () => governanceApi.listApprovals(),
    enabled: goal?.status === "waiting_human",
    refetchInterval: 3_000,
  });
  const pendingApproval = approvals?.find(
    (a) => a.goal_id === (goal?.goal_id ?? goal?.id) && a.status === "pending"
  );
  const approveMutation = useMutation({
    mutationFn: () => {
      if (!pendingApproval) throw new Error("No pending approval");
      return governanceApi.approve(pendingApproval.request_id, `user:${tenantId?.slice(0, 8)}`, approvalNote);
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["goal", goalId] }); qc.invalidateQueries({ queryKey: ["approvals"] }); },
  });
  const rejectMutation = useMutation({
    mutationFn: () => {
      if (!pendingApproval) throw new Error("No pending approval");
      return governanceApi.reject(pendingApproval.request_id, `user:${tenantId?.slice(0, 8)}`, approvalNote);
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["goal", goalId] }); qc.invalidateQueries({ queryKey: ["approvals"] }); },
  });

  if (isLoading) return (
    <div className="flex items-center justify-center h-60">
      <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" aria-label="Loading…" />
    </div>
  );
  if (!goal) return (
    <div className="text-center py-20 text-muted-foreground">
      Goal not found.{" "}
      <button onClick={() => navigate("/goals")} className="text-primary hover:underline">Back to goals</button>
    </div>
  );

  const artifact = goal.result_artifact as any;

  return (
    <JARVISPageShell>
    <div className="space-y-5 w-full">
      {/* ── Header ── */}
      <div>
        <button
          onClick={() => navigate("/goals")}
          className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground mb-3 transition-colors"
        >
          <ArrowLeft className="h-4 w-4" aria-hidden="true" /> Back to goals
        </button>
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div className="min-w-0 flex-1">
            <h1 className="text-xl font-bold leading-snug break-words">{goalTitle(goal.goal)}</h1>
            <p className="text-xs text-muted-foreground font-mono mt-1">{goal.goal_id ?? goalId}</p>
          </div>
          <div className="flex items-center gap-2 flex-shrink-0 flex-wrap">
            <StatusBadge status={goal.status} />
            {["executing", "planning"].includes(goal.status) && goal.created_at && (
              <ElapsedTimer startedAt={goal.created_at} />
            )}
            <LiveCostTicker
              currentCost={goal.cost_usd ?? 0}
              isRunning={["planning", "executing"].includes(goal.status)}
            />
          </div>
        </div>

        {/* ── Agent execution context ── */}
        <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
          {/* Agent */}
          {agentId ? (
            <button
              onClick={() => navigate(`/agents/${agentId}`)}
              className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-border bg-card hover:bg-muted hover:border-primary/30 transition-colors group"
              title="View agent details"
            >
              <Bot className="h-3.5 w-3.5 text-primary shrink-0" aria-hidden="true" />
              <div className="flex flex-col items-start min-w-0">
                <span className="font-semibold text-foreground group-hover:text-primary transition-colors">
                  {agentDetail?.name ?? "Loading agent…"}
                </span>
                {agentDetail?.autonomy_mode && (
                  <span className="text-[10px] text-muted-foreground capitalize">
                    {agentDetail.autonomy_mode.replace(/-/g, " ")}
                  </span>
                )}
              </div>
            </button>
          ) : (
            <span className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-dashed border-border text-muted-foreground">
              <Bot className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
              No agent assigned
            </span>
          )}

          {/* Connectors used */}
          {agentDetail && (agentDetail as any).connector_ids?.length > 0 && (
            <span className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-border bg-card">
              <Plug className="h-3.5 w-3.5 text-muted-foreground shrink-0" aria-hidden="true" />
              <span className="text-muted-foreground">
                {((agentDetail as any).connector_ids as string[])
                  .slice(0, 4)
                  .join(", ")}
                {((agentDetail as any).connector_ids as string[]).length > 4 &&
                  ` +${((agentDetail as any).connector_ids as string[]).length - 4}`}
              </span>
            </span>
          )}

          {/* Workflow mode */}
          {goal.workflow_mode && goal.workflow_mode !== "single_agent" && (
            <span className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-border bg-card">
              <Layers className="h-3.5 w-3.5 text-muted-foreground shrink-0" aria-hidden="true" />
              <span className="text-muted-foreground capitalize">
                {goal.workflow_mode.replace(/_/g, " ")}
              </span>
            </span>
          )}

          {/* Iterations count */}
          {goal.iterations != null && goal.iterations > 0 && (
            <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-muted text-muted-foreground">
              <span className="font-mono font-semibold text-foreground">{goal.iterations}</span>
              {goal.iterations === 1 ? " iteration" : " iterations"}
            </span>
          )}

          {/* Priority */}
          {goal.priority && goal.priority !== "normal" && (
            <span className={`inline-flex items-center gap-1 px-2 py-1 rounded-full text-[10px] font-semibold ${
              goal.priority === "high"
                ? "bg-orange-100 text-orange-800 dark:bg-orange-900/30 dark:text-orange-300"
                : "bg-muted text-muted-foreground"
            }`}>
              {goal.priority}
            </span>
          )}
        </div>
      </div>

      {/* ── Action buttons ── */}
      <div className="flex flex-wrap gap-2">
        {["executing", "planning"].includes(goal.status) && (
          <>
            <button
              onClick={() => cancelMutation.mutate()}
              disabled={cancelMutation.isPending}
              className="flex items-center gap-1.5 px-3 py-1.5 text-sm border border-destructive text-destructive rounded-lg hover:bg-destructive/10 transition-colors disabled:opacity-50"
            >
              <XCircle className="h-4 w-4" aria-hidden="true" />
              Cancel
            </button>
            {goal.status === "executing" && (
              <button
                onClick={() => pauseMutation.mutate()}
                disabled={pauseMutation.isPending}
                className="flex items-center gap-1.5 px-3 py-1.5 text-sm border border-yellow-300 text-yellow-700 rounded-lg hover:bg-yellow-50 transition-colors disabled:opacity-50"
              >
                <Pause className="h-4 w-4" aria-hidden="true" /> Pause
              </button>
            )}
          </>
        )}
        {goal.status === "paused" && (
          <button
            onClick={() => resumeMutation.mutate()}
            disabled={resumeMutation.isPending}
            className="flex items-center gap-1.5 px-3 py-1.5 text-sm border border-green-300 text-green-700 rounded-lg hover:bg-green-50 transition-colors disabled:opacity-50"
          >
            <Play className="h-4 w-4" aria-hidden="true" /> Resume
          </button>
        )}
        <button
          onClick={() => {
            void refetchGoal();
            void qc.invalidateQueries({ queryKey: ["goal-events-exec", goalId] });
            void qc.invalidateQueries({ queryKey: ["goal-events", goalId] });
            // Force SSE reconnect by bumping the stream key
            setStreamKey((k) => k + 1);
          }}
          className="flex items-center gap-1.5 px-3 py-1.5 text-sm border border-border rounded-lg hover:bg-accent transition-colors"
        >
          <RefreshCw className="h-4 w-4" aria-hidden="true" /> Refresh
        </button>
        {isTerminal && (
          <button
            onClick={() => navigate("/goals", { state: { prefillGoal: goal.goal } })}
            className="flex items-center gap-1.5 px-3 py-1.5 text-sm bg-primary text-primary-foreground rounded-lg hover:opacity-90 transition-opacity"
          >
            <RotateCcw className="h-4 w-4" aria-hidden="true" /> Rerun
          </button>
        )}
        <div className="flex items-center gap-1 ml-auto">
          <button onClick={() => navigate(`/goals/${goalId}/dna`)} title="View DNA" className="p-2 rounded-lg border border-border hover:bg-muted transition-colors"><Dna className="h-4 w-4" aria-hidden="true" /></button>
          <button onClick={() => navigate(`/goals/${goalId}/diff`)} title="Diff Run" className="p-2 rounded-lg border border-border hover:bg-muted transition-colors"><GitCompare className="h-4 w-4" aria-hidden="true" /></button>
          <button onClick={() => navigate("/goals/ghost-run")} title="Ghost Run" className="p-2 rounded-lg border border-border hover:bg-muted transition-colors"><Ghost className="h-4 w-4" aria-hidden="true" /></button>
        </div>
      </div>

      {/* ── HITL approval ── */}
      {goal.status === "waiting_human" && (
        <div className="bg-orange-50 dark:bg-orange-950/20 border border-orange-200 dark:border-orange-800 rounded-xl p-5 space-y-3">
          <h2 className="font-semibold text-sm text-orange-800 dark:text-orange-300">⏳ Human approval required</h2>
          {approvalsLoading ? (
            <Skeleton className="h-8 w-full" />
          ) : !pendingApproval ? (
            <p className="text-xs text-orange-600 italic">Awaiting approval request from backend…</p>
          ) : (
            <div className="space-y-3">
              {pendingApproval.action && <p className="text-xs text-orange-700">Action: <code className="font-mono">{pendingApproval.action}</code></p>}
              <textarea
                value={approvalNote}
                onChange={(e) => setApprovalNote(e.target.value)}
                placeholder="Optional note…"
                rows={2}
                className="w-full px-3 py-2 text-sm border border-orange-300 rounded-lg bg-background resize-none focus:outline-none focus:ring-2 focus:ring-orange-400"
              />
              <div className="flex gap-2">
                <button onClick={() => approveMutation.mutate()} disabled={approveMutation.isPending} className="flex items-center gap-1.5 px-4 py-2 bg-green-600 text-white text-sm rounded-lg hover:bg-green-700 disabled:opacity-50">
                  {approveMutation.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" /> : <CheckCircle className="h-3.5 w-3.5" aria-hidden="true" />}
                  Approve
                </button>
                <button onClick={() => rejectMutation.mutate()} disabled={rejectMutation.isPending} className="flex items-center gap-1.5 px-4 py-2 bg-red-600 text-white text-sm rounded-lg hover:bg-red-700 disabled:opacity-50">
                  <XCircle className="h-3.5 w-3.5" aria-hidden="true" /> Reject
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {/* ── SINGLE unified tab bar ── */}
      <div role="tablist" aria-label="Goal detail tabs" className="flex gap-0.5 border-b border-border">
        {visibleTabs.map(({ tab, label, icon }) => (
          <button
            key={tab}
            id={`goal-tab-${tab}`}
            ref={(el) => { tabRefs.current[tab] = el; }}
            role="tab"
            aria-controls={`goal-tabpanel-${tab}`}
            aria-selected={activeTab === tab}
            tabIndex={activeTab === tab ? 0 : -1}
            onClick={() => selectTab(tab)}
            onKeyDown={handleTabKeyDown}
            className={`flex items-center gap-1.5 px-3 py-2.5 text-sm font-medium border-b-2 transition-colors ${
              activeTab === tab
                ? "border-primary text-primary"
                : "border-transparent text-muted-foreground hover:text-foreground hover:border-border"
            }`}
          >
            {icon}
            {label}
            {tab === "execution" && events.length > 0 && (
              <span className="text-[10px] bg-muted text-muted-foreground px-1.5 py-0.5 rounded-full font-mono ml-1">
                {events.length}
              </span>
            )}
          </button>
        ))}
      </div>

      {/* ── Tab panels ── */}

      {/* Results — ALWAYS shows something */}
      {activeTab === "results" && (
        <div id="goal-tabpanel-results" role="tabpanel" aria-labelledby="goal-tab-results" className="space-y-4">
          <RichResultPanel
            artifact={artifact}
            events={events}
            goal={goal.goal}
            status={goal.status}
          />
          {goalId && <GoalFeedback goalId={goalId} status={goal.status} />}
        </div>
      )}

      {/* Evidence — built from events + artifact */}
      {activeTab === "evidence" && (
        <div id="goal-tabpanel-evidence" role="tabpanel" aria-labelledby="goal-tab-evidence">
          <EnhancedEvidencePanel artifact={artifact} events={events} />
        </div>
      )}

      {/* Execution — terminal-style */}
      {activeTab === "execution" && (
        <div id="goal-tabpanel-execution" role="tabpanel" aria-labelledby="goal-tab-execution" className="space-y-3">
          <TerminalPanel
            events={events}
            goalStatus={goal.status}
            streamingToken={streamingToken}
            connected={connected}
            onRetry={(d) => retryMutation.mutate(d)}
            isRetrying={retryMutation.isPending}
          />
        </div>
      )}

      {/* Developer Log */}
      {activeTab === "events" && (
        <div id="goal-tabpanel-events" role="tabpanel" aria-labelledby="goal-tab-events" className="space-y-2">
          {eventsLoading
            ? Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-10 w-full" />)
            : eventLog.length === 0
            ? <EmptyState
          icon={<Inbox size={40} />}
          title="No persisted events"
          description="Events appear after goal execution completes."
          variant="float"
        />
            : eventLog.map((ev, i) => (
              <div key={i} className="flex items-start gap-3 p-3 rounded-lg border bg-card text-sm">
                <span className="font-mono text-xs text-muted-foreground whitespace-nowrap">
                  {(ev as any).created_at || (ev as any).ts
                    ? new Date((ev as any).created_at ?? (ev as any).ts).toLocaleTimeString()
                    : `#${i + 1}`}
                </span>
                <span className="font-medium">{(ev as any).type?.replace(/_/g, " ")}</span>
                {((ev as any).payload?.message ?? (ev as any).data?.message) != null && (
                  <span className="text-muted-foreground text-xs">
                    {String((ev as any).payload?.message ?? (ev as any).data?.message)}
                  </span>
                )}
              </div>
            ))
          }
        </div>
      )}

      {/* Eval */}
      {activeTab === "eval" && (
        <div id="goal-tabpanel-eval" role="tabpanel" aria-labelledby="goal-tab-eval" className="space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="text-sm font-semibold">Evaluation Scorecard</h3>
              <p className="text-xs text-muted-foreground mt-0.5">7-dimension quality assessment</p>
            </div>
            <button
              onClick={() => triggerEvalMutation.mutate()}
              disabled={triggerEvalMutation.isPending || !isTerminal}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-50"
            >
              {triggerEvalMutation.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" /> : <FlaskConical className="h-3.5 w-3.5" aria-hidden="true" />}
              {evaluation ? "Re-score" : "Run Eval"}
            </button>
          </div>
          {evalLoading || triggerEvalMutation.isPending ? (
            <div className="space-y-2">{Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-12 w-full" />)}</div>
          ) : !evaluation || (evaluation as any).status === "not_evaluated" ? (
            <div className="rounded-xl border border-dashed bg-muted/20 p-8 text-center">
              <FlaskConical className="mx-auto h-8 w-8 opacity-30 mb-3" aria-hidden="true" />
              <p className="text-sm font-medium">No evaluation yet</p>
              <p className="text-xs text-muted-foreground mt-1">Click "Run Eval" to score this goal on 7 quality dimensions.</p>
            </div>
          ) : (
            <div className="space-y-3">
              <div className={`flex items-center gap-4 p-4 rounded-xl border-2 ${evaluation.passed ? "border-emerald-200 bg-emerald-50 dark:border-emerald-800 dark:bg-emerald-950/20" : "border-red-200 bg-red-50 dark:border-red-800 dark:bg-red-950/20"}`}>
                <div className={`text-4xl font-bold tabular-nums ${evaluation.passed ? "text-emerald-700 dark:text-emerald-300" : "text-red-700 dark:text-red-300"}`}>
                  {(((evaluation.average_score ?? (evaluation as any).score ?? 0)) * 100).toFixed(0)}%
                </div>
                <div>
                  <p className="text-sm font-semibold">Overall Score</p>
                  <span className={`inline-flex items-center gap-1 text-xs font-bold px-2 py-0.5 rounded-full ${evaluation.passed ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40" : "bg-red-100 text-red-700 dark:bg-red-900/40"}`}>
                    {evaluation.passed ? "✓ PASSED" : "✗ FAILED"}
                  </span>
                </div>
              </div>
              {evaluation.scores && (
                <div className="rounded-xl border bg-card overflow-hidden">
                  <div className="px-4 py-3 border-b bg-muted/30">
                    <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Dimension Breakdown</h4>
                  </div>
                  <div className="divide-y">
                    {Object.entries(evaluation.scores).map(([dim, rawScore]) => {
                      const pct = Math.round((rawScore as number) * 100);
                      const labels: Record<string, string> = { task_completion: "Task Completion", efficiency: "Efficiency", accuracy: "Accuracy", safety: "Safety", coherence: "Coherence", sla: "SLA", tool_relevance: "Tool Relevance" };
                      const colors: Record<string, string> = { task_completion: "bg-blue-500", efficiency: "bg-green-500", accuracy: "bg-violet-500", safety: "bg-orange-500", coherence: "bg-teal-500", sla: "bg-sky-500", tool_relevance: "bg-amber-500" };
                      return (
                        <div key={dim} className="flex items-center gap-3 px-4 py-3">
                          <p className="w-36 text-xs font-medium shrink-0">{labels[dim] ?? dim.replace(/_/g, " ")}</p>
                          <div className="flex-1 h-2 bg-muted rounded-full overflow-hidden">
                            <div className={`h-full rounded-full ${colors[dim] ?? "bg-primary"}`} style={{ width: `${pct}%` }} />
                          </div>
                          <span className="w-10 text-right text-xs font-semibold tabular-nums">{pct}%</span>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Explain */}
      {activeTab === "explain" && (
        <div id="goal-tabpanel-explain" role="tabpanel" aria-labelledby="goal-tab-explain">
          <GoalExplainPanel goalId={goalId!} />
        </div>
      )}

      {import.meta.env.DEV && (
        <details className="mt-4">
          <summary className="text-xs text-muted-foreground cursor-pointer">Debug: raw goal state</summary>
          <pre className="mt-2 text-[10px] bg-muted rounded p-3 overflow-auto max-h-64">{JSON.stringify(goal, null, 2)}</pre>
        </details>
      )}
    </div>
    </JARVISPageShell>
  );
}
