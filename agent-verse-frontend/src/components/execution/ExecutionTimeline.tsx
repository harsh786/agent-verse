import { useMemo } from "react";
import { CheckCircle, XCircle, Wrench, Play, FileText } from "lucide-react";

interface GoalEvent {
  type: string;
  step?: string;
  status?: string;
  tool_name?: string;
  server_id?: string;
  success?: boolean;
  ts?: string;
  [key: string]: unknown;
}

interface ExecutionTimelineProps {
  events: GoalEvent[];
}

function getEventColor(type: string, success?: boolean): string {
  if (type === "goal_complete" || type === "step_complete" || success === true) {
    return "bg-green-500";
  }
  if (type === "goal_failed" || type === "tool_call_failed" || success === false) {
    return "bg-red-500";
  }
  if (type === "tool_call_complete") {
    return "bg-blue-500";
  }
  if (type === "plan_ready") {
    return "bg-purple-500";
  }
  if (type === "step_started" || type === "goal_started") {
    return "bg-yellow-500";
  }
  return "bg-gray-400";
}

function getEventIcon(type: string) {
  if (type === "tool_call_complete" || type === "tool_call_failed") {
    return <Wrench className="h-3 w-3 text-white" />;
  }
  if (type === "goal_started" || type === "step_started") {
    return <Play className="h-3 w-3 text-white" />;
  }
  if (type === "goal_complete" || type === "step_complete") {
    return <CheckCircle className="h-3 w-3 text-white" />;
  }
  if (type === "goal_failed") {
    return <XCircle className="h-3 w-3 text-white" />;
  }
  return <FileText className="h-3 w-3 text-white" />;
}

function eventLabel(event: GoalEvent): string {
  const type = event.type;
  if (type === "tool_call_complete" || type === "tool_call_failed") {
    return event.tool_name as string || "Tool call";
  }
  if (type === "step_started" || type === "step_complete") {
    const step = event.step as string;
    return step ? step.slice(0, 24) : type.replace(/_/g, " ");
  }
  return type.replace(/_/g, " ");
}

export function ExecutionTimeline({ events }: ExecutionTimelineProps) {
  const visibleEvents = useMemo(() => events.slice(0, 40), [events]);

  if (visibleEvents.length === 0) return null;

  return (
    <div className="bg-card border border-border rounded-xl overflow-hidden">
      <div className="px-4 py-3 border-b border-border">
        <h2 className="font-semibold text-sm">Execution Timeline</h2>
        <p className="text-xs text-muted-foreground mt-0.5">
          {events.length} event{events.length !== 1 ? "s" : ""}
        </p>
      </div>
      <div className="p-4 overflow-x-auto">
        <div className="flex items-center gap-1 min-w-max">
          {visibleEvents.map((event, i) => (
            <div key={i} className="flex items-center">
              {/* Connector line */}
              {i > 0 && (
                <div className="w-4 h-px bg-border flex-shrink-0" />
              )}
              {/* Event block */}
              <div className="group relative flex flex-col items-center gap-1">
                <div
                  className={`flex items-center justify-center w-7 h-7 rounded-full flex-shrink-0 ${getEventColor(event.type, event.success as boolean | undefined)}`}
                >
                  {getEventIcon(event.type)}
                </div>
                <span className="text-xs text-muted-foreground max-w-16 text-center truncate">
                  {eventLabel(event)}
                </span>
                {/* Tooltip */}
                <div className="absolute bottom-full mb-2 left-1/2 -translate-x-1/2 hidden group-hover:block z-10 bg-gray-900 text-white text-xs rounded px-2 py-1 whitespace-nowrap shadow-lg">
                  <p className="font-medium">{event.type.replace(/_/g, " ")}</p>
                  {event.step && <p>Step: {event.step as string}</p>}
                  {event.tool_name && <p>Tool: {event.tool_name as string}</p>}
                  {event.ts && <p>{String(event.ts).slice(0, 19)}</p>}
                </div>
              </div>
            </div>
          ))}
          {events.length > 40 && (
            <div className="flex items-center ml-2">
              <div className="w-4 h-px bg-border" />
              <span className="text-xs text-muted-foreground ml-1">
                +{events.length - 40} more
              </span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
