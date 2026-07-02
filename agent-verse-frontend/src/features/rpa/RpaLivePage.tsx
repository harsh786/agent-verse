/**
 * RpaLivePage — world-class RPA session control panel.
 *
 * Features:
 *  - Session list (create, close, status badges)
 *  - Live viewport: polls GET /rpa/sessions/{id}/screenshot for data URIs
 *  - Interactive click overlay: click on screenshot → sends rpa_click
 *  - Tool executor: browse 13 tools, fill params, execute
 *  - Action log with timestamps
 *  - Takeover flow with modal
 *  - Session health indicator
 */
import { useState, useEffect, useCallback, useRef } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Plus, Trash2, Terminal, RefreshCw,
  Loader2, AlertCircle, Zap, ChevronDown, ChevronUp, X, Clock,
  Mouse,
} from "lucide-react";
import { rpaApi, type RpaSession, type RpaTool, type RpaExecuteResult } from "@/lib/api/client";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/EmptyState";
import { ConfirmModal } from "@/components/ui/ConfirmModal";
import { toast } from "@/stores/toast";

// ── Helpers ───────────────────────────────────────────────────────────────────

const RISK_COLORS: Record<string, string> = {
  low:  "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-300",
  read: "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300",
  high: "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400",
};

const STATUS_COLORS: Record<string, string> = {
  active: "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-300",
  paused: "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300",
  closed: "bg-muted text-muted-foreground",
};

function timeAgo(iso?: string): string {
  if (!iso) return "";
  const s = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  return `${Math.floor(m / 60)}h`;
}

function duration(iso?: string): string {
  if (!iso) return "—";
  const s = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  const m = Math.floor(s / 60);
  const h = Math.floor(m / 60);
  if (h > 0) return `${h}h ${m % 60}m`;
  if (m > 0) return `${m}m ${s % 60}s`;
  return `${s}s`;
}

interface ActionEntry {
  id: string;
  toolName: string;
  output: string;
  success: boolean;
  risk: string;
  timestamp: Date;
}

// ── Tool executor ─────────────────────────────────────────────────────────────

function ToolExecutor({
  sessionId,
  onResult,
}: {
  sessionId: string;
  onResult: (entry: ActionEntry) => void;
}) {
  const [search, setSearch] = useState("");
  const [selectedTool, setSelectedTool] = useState<RpaTool | null>(null);
  const [params, setParams] = useState<Record<string, string>>({});
  const [lastResult, setLastResult] = useState<RpaExecuteResult | null>(null);

  const { data: toolData, isLoading: toolsLoading } = useQuery({
    queryKey: ["rpa-tools"],
    queryFn: () => rpaApi.listTools(),
    staleTime: 300_000,
  });
  const tools = toolData?.tools ?? [];

  const executeMutation = useMutation({
    mutationFn: () => {
      const args: Record<string, unknown> = {};
      Object.entries(params).forEach(([k, v]) => { if (v.trim()) args[k] = v; });
      return rpaApi.execute(selectedTool!.name, args, sessionId);
    },
    onSuccess: (result) => {
      setLastResult(result);
      onResult({
        id: Date.now().toString(),
        toolName: result.tool_name,
        output: result.output ?? result.error ?? "",
        success: result.success,
        risk: selectedTool?.risk ?? "low",
        timestamp: new Date(),
      });
    },
    onError: (e) => toast({ kind: "error", message: String(e) }),
  });

  const filtered = tools.filter((t) =>
    !search || t.name.toLowerCase().includes(search.toLowerCase()) || t.description?.toLowerCase().includes(search.toLowerCase())
  );

  return (
    <div className="p-4 space-y-4">
      <input
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        placeholder="Search tools…"
        className="w-full px-3 py-2 text-sm border border-input rounded-lg bg-background focus:outline-none focus:ring-1 focus:ring-primary"
        aria-label="Search RPA tools"
      />
      {toolsLoading && <Skeleton className="h-32" />}
      {!toolsLoading && (
        <div className="grid grid-cols-2 gap-2 max-h-48 overflow-y-auto">
          {filtered.map((t) => (
            <button
              key={t.name}
              onClick={() => { setSelectedTool(t); setParams({}); }}
              className={`text-left p-2 rounded-lg border text-xs transition-colors ${
                selectedTool?.name === t.name ? "border-primary bg-primary/5" : "border-border hover:bg-muted/40"
              }`}
            >
              <p className="font-medium truncate">{t.name}</p>
              <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${RISK_COLORS[t.risk] ?? RISK_COLORS.low}`}>
                {t.risk}
              </span>
            </button>
          ))}
        </div>
      )}

      {selectedTool && (
        <div className="space-y-2 border border-border rounded-lg p-3">
          <p className="text-xs font-semibold">{selectedTool.name}</p>
          <p className="text-[10px] text-muted-foreground">{selectedTool.description}</p>
          {/* Simple key-value param builder */}
          <div className="space-y-1.5">
            {["selector", "text", "url", "filename", "timeout"].map((param) => (
              <div key={param} className="flex items-center gap-2">
                <label className="text-[10px] text-muted-foreground w-16 shrink-0">{param}</label>
                <input
                  value={params[param] ?? ""}
                  onChange={(e) => setParams((p) => ({ ...p, [param]: e.target.value }))}
                  placeholder={`Enter ${param}…`}
                  className="flex-1 px-2 py-1 text-xs border border-input rounded bg-background focus:outline-none focus:ring-1 focus:ring-primary font-mono"
                />
              </div>
            ))}
          </div>
          <button
            onClick={() => executeMutation.mutate()}
            disabled={executeMutation.isPending}
            className="w-full flex items-center justify-center gap-1.5 py-1.5 bg-primary text-primary-foreground text-xs font-medium rounded-lg hover:opacity-90 disabled:opacity-50"
          >
            {executeMutation.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Zap className="h-3.5 w-3.5" />}
            Execute
          </button>
          {lastResult && (
            <div className={`rounded-lg p-2 text-xs ${lastResult.success ? "bg-green-50 dark:bg-green-950/30 text-green-700 dark:text-green-300" : "bg-red-50 dark:bg-red-950/30 text-red-600 dark:text-red-400"}`}>
              <p className="font-mono">{lastResult.output || lastResult.error || "No output"}</p>
              {lastResult.duration_ms && <p className="opacity-60">{lastResult.duration_ms}ms</p>}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export function RpaLivePage() {
  const qc = useQueryClient();
  const [activeSession, setActiveSession] = useState<string | null>(null);
  const [screenshot, setScreenshot] = useState<string | null>(null);
  const [screenshotUrl, setScreenshotUrl] = useState<string>("");
  const [screenshotLoading, setScreenshotLoading] = useState(false);
  const [actions, setActions] = useState<ActionEntry[]>([]);
  const [toolConsoleOpen, setToolConsoleOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
  const [takeoverOpen, setTakeoverOpen] = useState(false);
  const [takeoverReason, setTakeoverReason] = useState("");
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const imgRef = useRef<HTMLImageElement>(null);
  const [clickFlash, setClickFlash] = useState<{ x: number; y: number } | null>(null);

  const { data: sessions = [], isLoading: sessionsLoading } = useQuery<RpaSession[]>({
    queryKey: ["rpa-sessions"],
    queryFn: () => rpaApi.listSessions(),
    refetchInterval: 10_000,
  });

  // Screenshot polling
  const fetchScreenshot = useCallback(async () => {
    if (!activeSession) return;
    try {
      const data = await rpaApi.getScreenshot(activeSession);
      if (data.screenshot_data_uri) {
        setScreenshot(data.screenshot_data_uri);
        setScreenshotUrl(data.url ?? "");
      }
    } catch { /* ignore */ }
  }, [activeSession]);

  useEffect(() => {
    if (!activeSession) return;
    setScreenshotLoading(true);
    fetchScreenshot().finally(() => setScreenshotLoading(false));
    intervalRef.current = setInterval(fetchScreenshot, 2000);
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [activeSession, fetchScreenshot]);

  const createMutation = useMutation({
    mutationFn: () => rpaApi.createSession(),
    onSuccess: (s) => {
      toast({ kind: "success", message: `Session ${s.session_id.slice(0, 12)} created.` });
      qc.invalidateQueries({ queryKey: ["rpa-sessions"] });
      setActiveSession(s.session_id);
    },
    onError: (e) => toast({ kind: "error", message: `Create failed: ${String(e)}` }),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => rpaApi.deleteSession(id),
    onSuccess: (_v, id) => {
      toast({ kind: "success", message: "Session closed." });
      qc.invalidateQueries({ queryKey: ["rpa-sessions"] });
      if (activeSession === id) { setActiveSession(null); setScreenshot(null); }
      setDeleteTarget(null);
    },
    onError: (e) => toast({ kind: "error", message: String(e) }),
  });

  const takeoverMutation = useMutation({
    mutationFn: () => rpaApi.takeover(activeSession!, takeoverReason),
    onSuccess: (data) => {
      toast({ kind: "info", message: data.message });
      setTakeoverOpen(false);
      setTakeoverReason("");
    },
    onError: (e) => toast({ kind: "error", message: String(e) }),
  });

  // Interactive click on screenshot
  const handleScreenshotClick = useCallback(async (e: React.MouseEvent<HTMLImageElement>) => {
    if (!activeSession || !imgRef.current) return;
    const rect = imgRef.current.getBoundingClientRect();
    const x = Math.round(((e.clientX - rect.left) / rect.width) * 1920);
    const y = Math.round(((e.clientY - rect.top) / rect.height) * 1080);
    const flashX = e.clientX - rect.left;
    const flashY = e.clientY - rect.top;
    setClickFlash({ x: flashX, y: flashY });
    setTimeout(() => setClickFlash(null), 400);
    try {
      const result = await rpaApi.execute("rpa_click", { selector: `[data-coords="${x},${y}"]`, x, y }, activeSession);
      setActions((prev) => [{
        id: Date.now().toString(), toolName: "rpa_click",
        output: result.output || `Clicked at (${x}, ${y})`,
        success: result.success, risk: "high", timestamp: new Date(),
      }, ...prev].slice(0, 50));
    } catch { /* ignore */ }
  }, [activeSession]);

  const addAction = useCallback((entry: ActionEntry) => {
    setActions((prev) => [entry, ...prev].slice(0, 50));
  }, []);

  const activeSessionObj = sessions.find((s) => s.session_id === activeSession);

  return (
    <div className="flex flex-col h-[calc(100vh-8rem)] gap-4 max-w-7xl">
      {/* Page title (visually hidden but accessible) */}
      <h1 className="sr-only">RPA Live</h1>
      {/* Layout */}
      <div className="flex flex-1 gap-4 min-h-0">
      {/* Left: Session list */}
      <div className="w-64 flex flex-col bg-card border border-border rounded-xl overflow-hidden shrink-0">
        <div className="flex items-center justify-between px-4 py-3 border-b border-border bg-muted/20 shrink-0">
          <div className="flex items-center gap-2">
            <Terminal className="h-4 w-4 text-primary" aria-hidden="true" />
            <h2 className="text-sm font-semibold">Sessions</h2>
            {sessions.length > 0 && (
              <span className="text-[10px] bg-muted text-muted-foreground px-1.5 rounded">{sessions.length}</span>
            )}
          </div>
          <button
            onClick={() => createMutation.mutate()}
            disabled={createMutation.isPending}
            className="p-1.5 rounded-lg bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-50 transition-opacity"
            aria-label="New session"
            title="New session"
          >
            {createMutation.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Plus className="h-3.5 w-3.5" />}
          </button>
        </div>

        <div className="flex-1 overflow-y-auto">
          {sessionsLoading && <div className="p-4 space-y-2"><Skeleton className="h-12" /><Skeleton className="h-12" /></div>}
          {!sessionsLoading && sessions.length === 0 && (
            <div className="p-4 text-center">
              <p className="text-xs text-muted-foreground">No active sessions.</p>
              <button onClick={() => createMutation.mutate()} className="mt-2 text-xs text-primary hover:underline">
                Start a new one
              </button>
            </div>
          )}
          {sessions.map((s) => (
            <div
              key={s.session_id}
              onClick={() => setActiveSession(s.session_id)}
              className={`flex items-center gap-2 px-3 py-2.5 cursor-pointer hover:bg-muted/40 transition-colors border-l-2 group ${
                activeSession === s.session_id ? "border-l-primary bg-primary/5" : "border-l-transparent"
              }`}
              role="button"
              tabIndex={0}
            >
              <div className="flex-1 min-w-0">
                <p className="text-xs font-mono truncate font-medium">{s.session_id.slice(0, 14)}</p>
                <div className="flex items-center gap-1.5 mt-0.5">
                  <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${STATUS_COLORS[s.status] ?? STATUS_COLORS.active}`}>
                    {s.status}
                  </span>
                  <span className="text-[10px] text-muted-foreground">{timeAgo(s.created_at)}</span>
                </div>
              </div>
              <button
                onClick={(e) => { e.stopPropagation(); setDeleteTarget(s.session_id); }}
                className="p-1 opacity-0 group-hover:opacity-100 text-muted-foreground hover:text-destructive transition-all"
                aria-label="Close session"
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </div>
          ))}
        </div>
      </div>

      {/* Main: Viewport + tools */}
      <div className="flex-1 flex flex-col gap-4 min-w-0">
        {!activeSession ? (
          <div className="flex-1 bg-card border border-border rounded-xl flex items-center justify-center">
            <EmptyState
              title="No session selected"
              description="Select a session from the left panel or create a new one."
            />
          </div>
        ) : (
          <>
            {/* Control bar */}
            <div className="bg-card border border-border rounded-xl px-4 py-2.5 flex items-center gap-3 shrink-0">
              <p className="text-xs font-mono text-muted-foreground">{activeSession.slice(0, 20)}…</p>
              {activeSessionObj && (
                <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${STATUS_COLORS[activeSessionObj.status] ?? STATUS_COLORS.active}`}>
                  {activeSessionObj.status}
                </span>
              )}
              <div className="ml-auto flex items-center gap-2">
                <button
                  onClick={fetchScreenshot}
                  className="p-1.5 rounded-lg hover:bg-muted/60 text-muted-foreground transition-colors"
                  aria-label="Refresh screenshot"
                  title="Refresh screenshot"
                >
                  <RefreshCw className="h-3.5 w-3.5" />
                </button>
                <button
                  onClick={() => setTakeoverOpen(true)}
                  className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium border border-amber-300 text-amber-700 dark:text-amber-400 rounded-lg hover:bg-amber-50 dark:hover:bg-amber-900/20 transition-colors"
                >
                  <Mouse className="h-3.5 w-3.5" /> Takeover
                </button>
              </div>
            </div>

            {/* Viewport + action log */}
            <div className="flex flex-1 gap-4 min-h-0">
              {/* Screenshot */}
              <div className="flex-1 bg-card border border-border rounded-xl overflow-hidden flex flex-col min-w-0">
                <div className="relative flex-1 bg-black/90 flex items-center justify-center">
                  {screenshotLoading && !screenshot && (
                    <div className="flex flex-col items-center gap-2 text-muted-foreground">
                      <Loader2 className="h-6 w-6 animate-spin" />
                      <p className="text-xs">Loading viewport…</p>
                    </div>
                  )}
                  {screenshot ? (
                    <div className="relative w-full h-full">
                      <img
                        ref={imgRef}
                        src={screenshot}
                        alt="Browser viewport"
                        className="w-full h-full object-contain cursor-crosshair"
                        onClick={handleScreenshotClick}
                        data-testid="viewport-screenshot"
                      />
                      {clickFlash && (
                        <div
                          className="absolute w-6 h-6 rounded-full border-2 border-primary bg-primary/30 pointer-events-none animate-ping"
                          style={{ left: clickFlash.x - 12, top: clickFlash.y - 12 }}
                        />
                      )}
                    </div>
                  ) : !screenshotLoading ? (
                    <div className="text-muted-foreground text-center">
                      <AlertCircle className="h-8 w-8 mx-auto mb-2 opacity-30" />
                      <p className="text-xs">No screenshot available</p>
                    </div>
                  ) : null}
                </div>
                {screenshotUrl && (
                  <div className="px-3 py-1.5 border-t border-border bg-muted/20">
                    <p className="text-[10px] font-mono text-muted-foreground truncate">{screenshotUrl}</p>
                  </div>
                )}
              </div>

              {/* Right: Action log */}
              <div className="w-72 flex flex-col bg-card border border-border rounded-xl overflow-hidden shrink-0">
                <div className="flex items-center justify-between px-3 py-2.5 border-b border-border bg-muted/20 shrink-0">
                  <h3 className="text-xs font-semibold">Action Log</h3>
                  <div className="flex items-center gap-1.5">
                    <span className="text-[10px] text-muted-foreground">{actions.length}</span>
                    <button onClick={() => setActions([])} className="text-[10px] text-muted-foreground hover:text-foreground" aria-label="Clear log">
                      <X className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </div>
                <div className="flex-1 overflow-y-auto p-2 space-y-1.5">
                  {actions.length === 0 ? (
                    <p className="text-[10px] text-muted-foreground text-center py-4 italic">No actions yet. Click the viewport or use the tool console.</p>
                  ) : (
                    actions.map((a) => (
                      <div key={a.id} className={`rounded-lg px-2.5 py-2 text-[10px] space-y-0.5 ${a.success ? "bg-green-50 dark:bg-green-950/20" : "bg-red-50 dark:bg-red-950/20"}`}>
                        <div className="flex items-center gap-1.5">
                          <span className={`px-1.5 py-0.5 rounded font-medium font-mono ${RISK_COLORS[a.risk] ?? RISK_COLORS.low}`}>
                            {a.toolName}
                          </span>
                          <span className="text-muted-foreground ml-auto">{a.timestamp.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}</span>
                        </div>
                        <p className="text-muted-foreground truncate">{a.output}</p>
                      </div>
                    ))
                  )}
                </div>
                {/* Session health */}
                {activeSessionObj && (
                  <div className="border-t border-border px-3 py-2 bg-muted/10 text-[10px] text-muted-foreground flex items-center gap-3">
                    <Clock className="h-3 w-3 shrink-0" />
                    <span>Age: {duration(activeSessionObj.created_at)}</span>
                    <span>{actions.length} actions</span>
                  </div>
                )}
              </div>
            </div>

            {/* Tool console */}
            <div className="bg-card border border-border rounded-xl shrink-0">
              <button
                onClick={() => setToolConsoleOpen((v) => !v)}
                className="w-full flex items-center justify-between px-4 py-3 text-sm font-semibold hover:bg-muted/30 transition-colors"
                aria-expanded={toolConsoleOpen}
              >
                <div className="flex items-center gap-2">
                  <Terminal className="h-4 w-4 text-primary" />
                  Tool Console
                </div>
                {toolConsoleOpen ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
              </button>
              {toolConsoleOpen && (
                <div className="border-t border-border">
                  <ToolExecutor sessionId={activeSession} onResult={addAction} />
                </div>
              )}
            </div>
          </>
        )}
      </div>

      {/* Delete confirm */}
      <ConfirmModal
        open={!!deleteTarget}
        title="Close session?"
        description="This will terminate the browser session and free the Playwright instance."
        confirmLabel="Close session"
        variant="danger"
        isLoading={deleteMutation.isPending}
        onConfirm={() => deleteTarget && deleteMutation.mutate(deleteTarget)}
        onCancel={() => setDeleteTarget(null)}
      />

      {/* Takeover modal */}
      {takeoverOpen && (
        <div className="fixed inset-0 z-[300] flex items-center justify-center p-4">
          <div className="absolute inset-0 bg-black/50" onClick={() => setTakeoverOpen(false)} />
          <div className="relative bg-card border border-border rounded-xl shadow-2xl max-w-sm w-full p-5 space-y-4">
            <h2 className="text-base font-semibold">Take over browser session</h2>
            <p className="text-sm text-muted-foreground">Pause the agent and take manual control of the browser.</p>
            <textarea
              value={takeoverReason}
              onChange={(e) => setTakeoverReason(e.target.value)}
              placeholder="Reason for takeover…"
              rows={3}
              className="w-full px-3 py-2 text-sm border border-input rounded-lg bg-background focus:outline-none focus:ring-2 focus:ring-primary resize-none"
              aria-label="Takeover reason"
            />
            <div className="flex gap-3">
              <button
                onClick={() => takeoverMutation.mutate()}
                disabled={takeoverMutation.isPending}
                className="flex-1 py-2.5 bg-amber-600 text-white text-sm font-medium rounded-lg hover:bg-amber-700 disabled:opacity-50"
              >
                {takeoverMutation.isPending ? "Requesting…" : "Request Takeover"}
              </button>
              <button onClick={() => setTakeoverOpen(false)} className="px-4 py-2.5 border border-input text-sm rounded-lg hover:bg-muted/50">
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
      </div>{/* end layout div */}
    </div>
  );
}

export default RpaLivePage;
