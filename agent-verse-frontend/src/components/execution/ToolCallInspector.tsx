import { useState } from "react";
import { Wrench, Clock, AlertTriangle } from "lucide-react";

interface ToolCallEvent {
  type: string;
  tool_name?: string;
  server_id?: string;
  arguments?: unknown;
  output?: unknown;
  error?: unknown;
  latency_ms?: number;
  risk?: string;
  success?: boolean;
  ts?: string;
}

interface ToolCallInspectorProps {
  toolEvents: ToolCallEvent[];
}

function riskColor(risk?: string): string {
  switch (risk) {
    case "critical": return "text-red-600 bg-red-100";
    case "high": return "text-orange-600 bg-orange-100";
    case "medium": return "text-yellow-600 bg-yellow-100";
    case "low":
    case "read": return "text-green-600 bg-green-100";
    default: return "text-muted-foreground bg-muted";
  }
}

function formatJson(value: unknown): string {
  if (value === undefined || value === null) return "—";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function ToolDetail({ event }: { event: ToolCallEvent }) {
  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="font-semibold text-base">
            {String(event.tool_name || "Unknown Tool")}
          </h3>
          {event.server_id && (
            <p className="text-xs text-muted-foreground font-mono mt-0.5">
              server_id: {String(event.server_id)}
            </p>
          )}
        </div>
        <div className="flex items-center gap-2 flex-shrink-0">
          {event.risk && (
            <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${riskColor(String(event.risk))}`}>
              {String(event.risk)}
            </span>
          )}
          <span
            className={`px-2 py-0.5 rounded-full text-xs font-medium ${
              event.success !== false
                ? "bg-green-100 text-green-700"
                : "bg-red-100 text-red-700"
            }`}
          >
            {event.success !== false ? "success" : "failed"}
          </span>
        </div>
      </div>

      {/* Metadata */}
      <div className="flex flex-wrap gap-4 text-xs text-muted-foreground">
        {event.latency_ms != null && (
          <span className="flex items-center gap-1">
            <Clock className="h-3 w-3" />
            {Number(event.latency_ms)}ms latency
          </span>
        )}
        {event.ts != null && (
          <span>{String(event.ts).slice(0, 19)}</span>
        )}
      </div>

      {/* Arguments */}
      <div>
        <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-1">
          Arguments
        </h4>
        <pre className="bg-muted rounded-lg px-3 py-2 text-xs overflow-x-auto whitespace-pre-wrap font-mono">
          {formatJson(event.arguments)}
        </pre>
      </div>

      {/* Output */}
      {event.output !== undefined && (
        <div>
          <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-1">
            Output
          </h4>
          <pre className="bg-muted rounded-lg px-3 py-2 text-xs overflow-x-auto whitespace-pre-wrap font-mono max-h-48">
            {formatJson(event.output)}
          </pre>
        </div>
      )}

      {/* Error */}
      {event.error != null && (
        <div>
          <h4 className="text-xs font-semibold text-red-600 uppercase tracking-wider mb-1 flex items-center gap-1">
            <AlertTriangle className="h-3 w-3" /> Error
          </h4>
          <pre className="bg-red-50 border border-red-200 rounded-lg px-3 py-2 text-xs overflow-x-auto whitespace-pre-wrap font-mono text-red-700">
            {formatJson(event.error)}
          </pre>
        </div>
      )}
    </div>
  );
}

export function ToolCallInspector({ toolEvents }: ToolCallInspectorProps) {
  const [selectedIdx, setSelectedIdx] = useState<number | null>(null);

  if (toolEvents.length === 0) return null;

  return (
    <div className="bg-card border border-border rounded-xl overflow-hidden">
      <div className="px-4 py-3 border-b border-border">
        <h2 className="font-semibold text-sm flex items-center gap-2">
          <Wrench className="h-4 w-4" />
          Tool Call Inspector
        </h2>
        <p className="text-xs text-muted-foreground mt-0.5">
          {toolEvents.length} tool call{toolEvents.length !== 1 ? "s" : ""}
        </p>
      </div>

      <div className="flex divide-x divide-border" style={{ minHeight: "300px" }}>
        {/* Left panel: tool call list */}
        <div className="w-64 flex-shrink-0 overflow-y-auto">
          {toolEvents.map((event, i) => {
            const succeeded = event.success !== false;
            return (
              <button
                key={i}
                onClick={() => setSelectedIdx(i)}
                className={`w-full text-left px-3 py-2.5 border-b border-border text-sm transition-colors hover:bg-accent ${
                  i === selectedIdx ? "bg-accent/70 border-l-2 border-l-primary" : ""
                }`}
              >
                <div className="flex items-center gap-2 min-w-0">
                  <span
                    className={`flex-shrink-0 w-2 h-2 rounded-full ${succeeded ? "bg-green-500" : "bg-red-500"}`}
                  />
                  <span className="truncate font-medium text-xs">
                    {String(event.tool_name || "unknown")}
                  </span>
                </div>
                {event.server_id && (
                  <p className="text-xs text-muted-foreground truncate mt-0.5 ml-4">
                    {String(event.server_id)}
                  </p>
                )}
                {event.latency_ms != null && (
                  <p className="text-xs text-muted-foreground mt-0.5 ml-4 flex items-center gap-1">
                    <Clock className="h-3 w-3" />
                    {event.latency_ms}ms
                  </p>
                )}
              </button>
            );
          })}
        </div>

        {/* Right panel: selected call detail */}
        <div className="flex-1 overflow-y-auto p-4 min-w-0">
          {selectedIdx === null ? (
            <div className="flex items-center justify-center h-full text-sm text-muted-foreground py-10">
              Select a tool call to inspect
            </div>
          ) : (
            <ToolDetail event={toolEvents[selectedIdx]} />
          )}
        </div>
      </div>
    </div>
  );
}
