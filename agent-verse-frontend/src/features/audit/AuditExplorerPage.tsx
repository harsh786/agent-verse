import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Download, RefreshCw } from "lucide-react";
import { auditApi, AuditEvent, AuditQuery } from "@/lib/api/client";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/EmptyState";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { toast } from "@/stores/toast";

const CSV_COLUMNS: (keyof AuditEvent)[] = [
  "event_id", "goal_id", "tool_name", "action_level", "outcome", "approver", "note",
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

function download(filename: string, content: string, mime: string): void {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export function AuditExplorerPage() {
  const [applied, setApplied] = useState<AuditQuery>({ limit: 200 });
  const [toolName, setToolName] = useState("");
  const [startTime, setStartTime] = useState("");
  const [endTime, setEndTime] = useState("");
  const [outcome, setOutcome] = useState("");
  const [text, setText] = useState("");

  const { data: entries = [], isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ["audit", applied],
    queryFn: () => auditApi.query(applied),
  });

  const filtered = useMemo(
    () =>
      entries.filter((e) => {
        if (outcome && !(e.outcome ?? "").toLowerCase().includes(outcome.toLowerCase())) return false;
        if (text && !JSON.stringify(e).toLowerCase().includes(text.toLowerCase())) return false;
        return true;
      }),
    [entries, outcome, text],
  );

  function applyFilters() {
    setApplied({
      limit: 200,
      tool_name: toolName || undefined,
      start_time: startTime || undefined,
      end_time: endTime || undefined,
    });
  }

  function exportJson() {
    if (!filtered.length) return toast({ kind: "info", message: "Nothing to export." });
    download("audit-events.json", JSON.stringify(filtered, null, 2), "application/json");
  }

  function exportCsv() {
    if (!filtered.length) return toast({ kind: "info", message: "Nothing to export." });
    download("audit-events.csv", toCsv(filtered), "text/csv");
  }

  return (
    <div className="p-6 max-w-6xl mx-auto space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Audit Explorer</h1>
        <div className="flex items-center gap-2">
          <button
            onClick={() => void refetch()}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-muted rounded-md text-sm hover:bg-accent"
          >
            <RefreshCw className={`h-4 w-4 ${isFetching ? "animate-spin" : ""}`} aria-hidden="true" />
            Refresh
          </button>
          <button
            onClick={exportCsv}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-muted rounded-md text-sm hover:bg-accent"
          >
            <Download className="h-4 w-4" aria-hidden="true" /> Export CSV
          </button>
          <button
            onClick={exportJson}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-muted rounded-md text-sm hover:bg-accent"
          >
            <Download className="h-4 w-4" aria-hidden="true" /> Export JSON
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-5 gap-3 bg-card border border-border rounded-xl p-4">
        <label className="flex flex-col gap-1 text-xs text-muted-foreground">
          Tool name
          <input
            aria-label="Tool name"
            value={toolName}
            onChange={(e) => setToolName(e.target.value)}
            className="px-2 py-1.5 border border-input rounded-md bg-background text-sm"
          />
        </label>
        <label className="flex flex-col gap-1 text-xs text-muted-foreground">
          Start time
          <input
            type="datetime-local"
            value={startTime}
            onChange={(e) => setStartTime(e.target.value)}
            className="px-2 py-1.5 border border-input rounded-md bg-background text-sm"
          />
        </label>
        <label className="flex flex-col gap-1 text-xs text-muted-foreground">
          End time
          <input
            type="datetime-local"
            value={endTime}
            onChange={(e) => setEndTime(e.target.value)}
            className="px-2 py-1.5 border border-input rounded-md bg-background text-sm"
          />
        </label>
        <label className="flex flex-col gap-1 text-xs text-muted-foreground">
          Outcome
          <input
            value={outcome}
            onChange={(e) => setOutcome(e.target.value)}
            placeholder="success / denied / fail"
            className="px-2 py-1.5 border border-input rounded-md bg-background text-sm"
          />
        </label>
        <div className="flex items-end">
          <button
            onClick={applyFilters}
            className="w-full px-3 py-1.5 bg-primary text-primary-foreground rounded-md text-sm hover:opacity-90"
          >
            Apply filters
          </button>
        </div>
      </div>

      <input
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="Free-text filter (client-side)…"
        className="w-full px-3 py-2 border border-input rounded-md bg-background text-sm"
      />

      {isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-10 w-full" />)}
        </div>
      ) : error ? (
        <div className="text-sm text-destructive">Failed to load audit log: {String(error)}</div>
      ) : filtered.length === 0 ? (
        <EmptyState title="No audit entries" description="No events match the current filters." />
      ) : (
        <div className="overflow-x-auto border border-border rounded-xl">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-muted text-left">
                <th className="px-3 py-2 font-medium">Tool</th>
                <th className="px-3 py-2 font-medium">Action</th>
                <th className="px-3 py-2 font-medium">Outcome</th>
                <th className="px-3 py-2 font-medium">Goal</th>
                <th className="px-3 py-2 font-medium">Approver</th>
              </tr>
            </thead>
            <tbody>
              {filtered.slice(0, 200).map((e) => (
                <tr key={e.event_id} className="border-t border-border">
                  <td className="px-3 py-2 font-mono text-xs">{e.tool_name || "—"}</td>
                  <td className="px-3 py-2"><StatusBadge status={e.action_level} /></td>
                  <td className="px-3 py-2">{e.outcome || "—"}</td>
                  <td className="px-3 py-2 font-mono text-xs text-muted-foreground">{e.goal_id?.slice(0, 16)}</td>
                  <td className="px-3 py-2 text-xs">{e.approver || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export default AuditExplorerPage;
