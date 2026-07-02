import { Fragment, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  ChevronLeft,
  ChevronRight,
  Download,
  FileJson,
  Filter,
  RefreshCw,
  Search,
  ShieldCheck,
} from "lucide-react";
import {
  auditApi,
  governanceApi,
  type AuditChainResult,
  type AuditEvent,
  type AuditQuery,
} from "@/lib/api/client";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { toast } from "@/stores/toast";

// ── Utilities ─────────────────────────────────────────────────────────────────

const CSV_COLUMNS: (keyof AuditEvent)[] = [
  "event_id", "goal_id", "tool_name", "action_level",
  "outcome", "step_id", "approver", "note",
];

function toCsv(rows: AuditEvent[]): string {
  const header = CSV_COLUMNS.join(",");
  const body = rows
    .map((r) =>
      CSV_COLUMNS
        .map((c) => `"${String(r[c] ?? "").replace(/"/g, '""')}"`)
        .join(","),
    )
    .join("\n");
  return `${header}\n${body}`;
}

function triggerDownload(filename: string, content: string, mime: string): void {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

// ── Sub-components ────────────────────────────────────────────────────────────

const LEVEL_STYLES: Record<string, string> = {
  allow:     "bg-green-100  text-green-800  border-green-200  dark:bg-green-900/40  dark:text-green-300  dark:border-green-800",
  allow_log: "bg-blue-100   text-blue-800   border-blue-200   dark:bg-blue-900/40   dark:text-blue-300   dark:border-blue-800",
  approval:  "bg-orange-100 text-orange-800 border-orange-200 dark:bg-orange-900/40 dark:text-orange-300 dark:border-orange-800",
  deny:      "bg-red-100    text-red-800    border-red-200    dark:bg-red-900/40    dark:text-red-300    dark:border-red-800",
};

const OUTCOME_STYLES: Record<string, string> = {
  success: "bg-green-100 text-green-800 border-green-200 dark:bg-green-900/40 dark:text-green-300 dark:border-green-800",
  denied:  "bg-red-100   text-red-800   border-red-200   dark:bg-red-900/40   dark:text-red-300   dark:border-red-800",
  failed:  "bg-red-100   text-red-800   border-red-200   dark:bg-red-900/40   dark:text-red-300   dark:border-red-800",
  error:   "bg-rose-100  text-rose-800  border-rose-200  dark:bg-rose-900/40  dark:text-rose-300  dark:border-rose-800",
};

const BADGE_BASE = "inline-flex items-center border font-medium rounded-full px-2 py-0.5 text-xs";

function LevelBadge({ level }: { level: string }) {
  const cls = LEVEL_STYLES[level?.toLowerCase()] ?? "bg-muted text-muted-foreground border-border";
  return <span className={`${BADGE_BASE} ${cls}`}>{level || "—"}</span>;
}

function OutcomeBadge({ outcome }: { outcome: string }) {
  const cls = OUTCOME_STYLES[outcome?.toLowerCase()] ?? "bg-muted text-muted-foreground border-border";
  return <span className={`${BADGE_BASE} ${cls}`}>{outcome || "—"}</span>;
}

function StatCard({
  label, value, colorClass,
}: { label: string; value: number; colorClass: string }) {
  return (
    <div className={`flex flex-col gap-0.5 rounded-xl border px-4 py-3 ${colorClass}`}>
      <span className="text-2xl font-bold tabular-nums">{value}</span>
      <span className="text-xs font-medium opacity-75">{label}</span>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export function AuditExplorerPage() {
  // Draft filter inputs (not yet applied to server query)
  const [draftGoalId, setDraftGoalId]     = useState("");
  const [draftTool, setDraftTool]         = useState("");
  const [draftStart, setDraftStart]       = useState("");
  const [draftEnd, setDraftEnd]           = useState("");
  const [draftOutcome, setDraftOutcome]   = useState("");
  const [draftLimit, setDraftLimit]       = useState(100);

  // Committed query state sent to the API
  const [applied, setApplied]             = useState<AuditQuery>({ limit: 100 });
  const [appliedOutcome, setAppliedOutcome] = useState("");
  const [offset, setOffset]               = useState(0);

  // Client-side full-text filter
  const [text, setText]                   = useState("");

  // Row detail expansion
  const [expandedId, setExpandedId]       = useState<string | null>(null);

  // Hash-chain verification
  const [chainResult, setChainResult]     = useState<AuditChainResult | null>(null);
  const [verifying, setVerifying]         = useState(false);

  // ── Data fetching ──────────────────────────────────────────────────────────

  const { data: entries = [], isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ["audit", applied, offset],
    queryFn: () => auditApi.query({ ...applied, offset }),
    refetchInterval: 60_000,
  });

  // ── Client-side derived data ───────────────────────────────────────────────

  const filtered = useMemo(() => {
    let out = entries;
    if (appliedOutcome) {
      const q = appliedOutcome.toLowerCase();
      out = out.filter((e) => (e.outcome ?? "").toLowerCase().includes(q));
    }
    if (text.trim()) {
      const q = text.toLowerCase();
      out = out.filter((e) => JSON.stringify(e).toLowerCase().includes(q));
    }
    return out;
  }, [entries, appliedOutcome, text]);

  const stats = useMemo(() => ({
    total:    filtered.length,
    allowed:  filtered.filter((e) => ["allow", "allow_log"].includes(e.action_level?.toLowerCase())).length,
    denied:   filtered.filter((e) => e.action_level?.toLowerCase() === "deny").length,
    approval: filtered.filter((e) => e.action_level?.toLowerCase() === "approval").length,
  }), [filtered]);

  // ── Actions ────────────────────────────────────────────────────────────────

  function applyFilters() {
    setOffset(0);
    setApplied({
      limit:      draftLimit,
      goal_id:    draftGoalId  || undefined,
      tool_name:  draftTool    || undefined,
      start_time: draftStart   || undefined,
      end_time:   draftEnd     || undefined,
    });
    setAppliedOutcome(draftOutcome);
  }

  function resetFilters() {
    setDraftGoalId(""); setDraftTool(""); setDraftStart("");
    setDraftEnd(""); setDraftOutcome(""); setDraftLimit(100);
    setText(""); setOffset(0);
    setApplied({ limit: 100 });
    setAppliedOutcome("");
  }

  async function verifyChain() {
    setVerifying(true);
    try {
      const result = await governanceApi.verifyAuditChain();
      setChainResult(result);
      toast({
        kind: result.verified ? "success" : "warning",
        message: result.verified
          ? `Chain verified — ${result.verified_events} events intact.`
          : `Chain broken at ${result.broken_chain_at ?? "unknown"}.`,
      });
    } catch {
      toast({ kind: "error", message: "Failed to verify audit chain." });
    } finally {
      setVerifying(false);
    }
  }

  function exportJson() {
    if (!filtered.length) return toast({ kind: "info", message: "Nothing to export." });
    triggerDownload("audit-events.json", JSON.stringify(filtered, null, 2), "application/json");
    toast({ kind: "success", message: `Exported ${filtered.length} events as JSON.` });
  }

  function exportCsv() {
    if (!filtered.length) return toast({ kind: "info", message: "Nothing to export." });
    triggerDownload("audit-events.csv", toCsv(filtered), "text/csv");
    toast({ kind: "success", message: `Exported ${filtered.length} events as CSV.` });
  }

  // ── Pagination helpers ────────────────────────────────────────────────────

  const pageSize = applied.limit ?? 100;
  const hasPrev  = offset > 0;
  const hasNext  = entries.length === pageSize;

  // ── Render ─────────────────────────────────────────────────────────────────

  return (
    <div className="p-6 max-w-7xl mx-auto space-y-5">

      {/* ── Header ── */}
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-2">
          <Activity className="h-5 w-5 text-primary" aria-hidden="true" />
          <h1 className="text-2xl font-bold">Audit Explorer</h1>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <button
            data-testid="verify-chain-btn"
            onClick={() => void verifyChain()}
            disabled={verifying}
            aria-label="Verify audit chain integrity"
            className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-muted rounded-md text-sm hover:bg-accent disabled:opacity-60 transition-colors"
          >
            <ShieldCheck
              className={`h-4 w-4 ${verifying ? "animate-pulse text-amber-500" : "text-muted-foreground"}`}
              aria-hidden="true"
            />
            {verifying ? "Verifying…" : "Verify Chain"}
          </button>
          <button
            onClick={() => void refetch()}
            aria-label="Refresh audit events"
            className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-muted rounded-md text-sm hover:bg-accent transition-colors"
          >
            <RefreshCw className={`h-4 w-4 ${isFetching ? "animate-spin" : ""}`} aria-hidden="true" />
            Refresh
          </button>
          <button
            data-testid="export-csv-btn"
            onClick={exportCsv}
            aria-label="Export events as CSV"
            className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-muted rounded-md text-sm hover:bg-accent transition-colors"
          >
            <Download className="h-4 w-4" aria-hidden="true" />
            CSV
          </button>
          <button
            data-testid="export-json-btn"
            onClick={exportJson}
            aria-label="Export JSON"
            className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-muted rounded-md text-sm hover:bg-accent transition-colors"
          >
            <FileJson className="h-4 w-4" aria-hidden="true" />
            JSON
          </button>
        </div>
      </div>

      {/* ── Stats row ── */}
      <div data-testid="audit-stats" className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <StatCard label="Total Events"      value={stats.total}    colorClass="bg-card border-border text-foreground" />
        <StatCard label="Allowed"           value={stats.allowed}  colorClass="bg-green-50  border-green-200  text-green-900  dark:bg-green-950/40  dark:border-green-800  dark:text-green-100" />
        <StatCard label="Denied"            value={stats.denied}   colorClass="bg-red-50    border-red-200    text-red-900    dark:bg-red-950/40    dark:border-red-800    dark:text-red-100" />
        <StatCard label="Approval Required" value={stats.approval} colorClass="bg-orange-50 border-orange-200 text-orange-900 dark:bg-orange-950/40 dark:border-orange-800 dark:text-orange-100" />
      </div>

      {/* ── Hash-chain verification result ── */}
      {chainResult && (
        <div
          role="status"
          aria-live="polite"
          className={`flex items-center gap-2 rounded-lg border px-4 py-2.5 text-sm font-medium ${
            chainResult.verified
              ? "border-green-300 bg-green-50 text-green-800 dark:bg-green-950/40 dark:border-green-700 dark:text-green-200"
              : "border-red-300   bg-red-50   text-red-800   dark:bg-red-950/40   dark:border-red-700   dark:text-red-200"
          }`}
        >
          <ShieldCheck className="h-4 w-4 shrink-0" aria-hidden="true" />
          {chainResult.verified
            ? `Chain verified — ${chainResult.verified_events} events intact${chainResult.chain_tip_hash ? ` · tip: ${chainResult.chain_tip_hash.slice(0, 12)}…` : ""}`
            : `Chain broken${chainResult.broken_chain_at ? ` at ${chainResult.broken_chain_at}` : ""} · ${chainResult.verified_events} events verified before break`}
        </div>
      )}

      {/* ── Filter panel ── */}
      <div data-testid="audit-filters" className="bg-card border border-border rounded-xl p-4 space-y-3">
        <div className="flex items-center gap-2">
          <Filter className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
          <span className="text-sm font-semibold">Filters</span>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6 gap-3">
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            Goal ID
            <input
              id="audit-goal-filter"
              aria-label="Filter by Goal ID"
              value={draftGoalId}
              onChange={(e) => setDraftGoalId(e.target.value)}
              placeholder="goal-uuid…"
              className="px-2.5 py-1.5 border border-input rounded-md bg-background text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
            />
          </label>

          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            Tool Name
            <input
              id="audit-tool-filter"
              aria-label="Filter by Tool name"
              value={draftTool}
              onChange={(e) => setDraftTool(e.target.value)}
              placeholder="web_search…"
              className="px-2.5 py-1.5 border border-input rounded-md bg-background text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
            />
          </label>

          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            Start Date / Time
            <input
              type="datetime-local"
              aria-label="Filter by start date and time"
              value={draftStart}
              onChange={(e) => setDraftStart(e.target.value)}
              className="px-2.5 py-1.5 border border-input rounded-md bg-background text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-ring"
            />
          </label>

          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            End Date / Time
            <input
              type="datetime-local"
              aria-label="Filter by end date and time"
              value={draftEnd}
              onChange={(e) => setDraftEnd(e.target.value)}
              className="px-2.5 py-1.5 border border-input rounded-md bg-background text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-ring"
            />
          </label>

          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            Outcome
            <select
              aria-label="Filter by outcome"
              value={draftOutcome}
              onChange={(e) => setDraftOutcome(e.target.value)}
              className="px-2.5 py-1.5 border border-input rounded-md bg-background text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-ring"
            >
              <option value="">All outcomes</option>
              <option value="success">success</option>
              <option value="denied">denied</option>
              <option value="failed">failed</option>
              <option value="error">error</option>
            </select>
          </label>

          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            Limit
            <select
              aria-label="Select result limit"
              value={draftLimit}
              onChange={(e) => setDraftLimit(Number(e.target.value))}
              className="px-2.5 py-1.5 border border-input rounded-md bg-background text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-ring"
            >
              <option value={50}>50</option>
              <option value={100}>100</option>
              <option value={200}>200</option>
              <option value={500}>500</option>
            </select>
          </label>
        </div>

        <div className="flex items-center gap-2 pt-1">
          <button
            data-testid="apply-filters-btn"
            onClick={applyFilters}
            aria-label="Apply filters"
            className="px-4 py-1.5 bg-primary text-primary-foreground rounded-md text-sm font-medium hover:opacity-90 transition-opacity"
          >
            Apply
          </button>
          <button
            onClick={resetFilters}
            aria-label="Reset all filters"
            className="px-4 py-1.5 bg-muted text-foreground rounded-md text-sm font-medium hover:bg-accent transition-colors"
          >
            Reset
          </button>
        </div>
      </div>

      {/* ── Free-text client-side filter ── */}
      <div className="relative">
        <Search
          className="absolute left-2.5 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground pointer-events-none"
          aria-hidden="true"
        />
        <input
          aria-label="Free-text filter across all fields (client-side)"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Free-text search across all fields (client-side)…"
          className="w-full pl-9 pr-3 py-2 border border-input rounded-md bg-background text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
        />
      </div>

      {/* ── Table / States ── */}
      {isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 8 }).map((_, i) => (
            <Skeleton key={i} className="h-11 w-full rounded-lg" />
          ))}
        </div>
      ) : error ? (
        <div
          role="alert"
          className="rounded-xl border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive"
        >
          Failed to load audit log: {String(error)}
        </div>
      ) : filtered.length === 0 ? (
        <EmptyState
          title="No audit entries"
          description="No events match the current filters. Try adjusting or resetting the filter panel."
        />
      ) : (
        <div data-testid="audit-table" className="overflow-x-auto border border-border rounded-xl">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-muted/60 text-left border-b border-border">
                {["Event ID", "Tool", "Level", "Outcome", "Goal", "Approver"].map((h) => (
                  <th key={h} className="px-3 py-2.5 font-medium text-muted-foreground text-xs uppercase tracking-wide whitespace-nowrap">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filtered.map((e) => (
                <Fragment key={e.event_id}>
                  <tr
                    data-testid="audit-row"
                    onClick={() => setExpandedId(expandedId === e.event_id ? null : e.event_id)}
                    className="border-t border-border hover:bg-muted/30 cursor-pointer transition-colors select-none"
                    role="button"
                    aria-expanded={expandedId === e.event_id}
                    aria-label={`Audit event ${e.event_id}`}
                  >
                    <td className="px-3 py-2.5 font-mono text-xs text-muted-foreground whitespace-nowrap">
                      {e.event_id?.slice(0, 16) ?? "—"}…
                    </td>
                    <td className="px-3 py-2.5 font-mono text-xs font-medium whitespace-nowrap">
                      {e.tool_name || "—"}
                    </td>
                    <td className="px-3 py-2.5">
                      <LevelBadge level={e.action_level} />
                    </td>
                    <td className="px-3 py-2.5">
                      <OutcomeBadge outcome={e.outcome} />
                    </td>
                    <td className="px-3 py-2.5 font-mono text-xs text-muted-foreground whitespace-nowrap">
                      {e.goal_id?.slice(0, 16) ?? "—"}…
                    </td>
                    <td className="px-3 py-2.5 text-xs">
                      {e.approver ? (
                        <StatusBadge status="approved" size="sm" />
                      ) : (
                        <span className="text-muted-foreground">—</span>
                      )}
                    </td>
                  </tr>

                  {expandedId === e.event_id && (
                    <tr className="bg-muted/20 border-t border-border">
                      <td colSpan={6} className="px-5 py-4">
                        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-x-6 gap-y-3 text-xs">
                          {(Object.entries(e) as [string, unknown][]).map(([k, v]) => (
                            <div key={k} className="space-y-0.5">
                              <p className="text-muted-foreground font-medium uppercase tracking-wide text-[10px]">
                                {k.replace(/_/g, " ")}
                              </p>
                              <p className="font-mono break-all text-foreground">{String(v ?? "—")}</p>
                            </div>
                          ))}
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* ── Pagination ── */}
      {!isLoading && !error && entries.length > 0 && (
        <div className="flex items-center justify-between text-sm text-muted-foreground">
          <span>
            Showing&nbsp;
            <span className="font-medium text-foreground">{offset + 1}</span>
            –
            <span className="font-medium text-foreground">{offset + entries.length}</span>
            {hasNext && <span>&nbsp;of {offset + entries.length}+</span>}
          </span>
          <div className="flex items-center gap-1">
            <button
              onClick={() => setOffset(Math.max(0, offset - pageSize))}
              disabled={!hasPrev}
              aria-label="Previous page"
              className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md bg-muted text-sm hover:bg-accent disabled:opacity-40 transition-colors"
            >
              <ChevronLeft className="h-4 w-4" aria-hidden="true" />
              Prev
            </button>
            <button
              onClick={() => setOffset(offset + pageSize)}
              disabled={!hasNext}
              aria-label="Next page"
              className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md bg-muted text-sm hover:bg-accent disabled:opacity-40 transition-colors"
            >
              Next
              <ChevronRight className="h-4 w-4" aria-hidden="true" />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

export default AuditExplorerPage;
