import { useEffect, useId, useMemo, useRef, useState } from "react";
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

interface TooltipState {
  eventIndex: number;
  left: number;
  top: number;
}

const TOOLTIP_WIDTH = 256;
const TOOLTIP_MARGIN = 12;
const TOOLTIP_OFFSET = 12;
const FALLBACK_TOOLTIP_HEIGHT = 112;

function clamp(value: number, min: number, max: number): number {
  if (max < min) return min;
  return Math.min(Math.max(value, min), max);
}

function getTooltipPosition(element: HTMLElement, tooltipHeight = FALLBACK_TOOLTIP_HEIGHT) {
  const rect = element.getBoundingClientRect();
  const viewportWidth = window.innerWidth || document.documentElement.clientWidth;
  const viewportHeight = window.innerHeight || document.documentElement.clientHeight;
  const tooltipWidth = Math.min(TOOLTIP_WIDTH, viewportWidth - TOOLTIP_MARGIN * 2);
  const tooltipHalfWidth = tooltipWidth / 2;
  const left = clamp(
    rect.left + rect.width / 2,
    tooltipHalfWidth + TOOLTIP_MARGIN,
    viewportWidth - tooltipHalfWidth - TOOLTIP_MARGIN
  );
  const aboveTop = rect.top - TOOLTIP_OFFSET - tooltipHeight;
  const belowTop = rect.bottom + TOOLTIP_OFFSET;

  if (aboveTop >= TOOLTIP_MARGIN) {
    return { left, top: aboveTop };
  }
  if (belowTop + tooltipHeight <= viewportHeight - TOOLTIP_MARGIN) {
    return { left, top: belowTop };
  }

  return {
    left,
    top: clamp(aboveTop, TOOLTIP_MARGIN, viewportHeight - tooltipHeight - TOOLTIP_MARGIN),
  };
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

function eventTooltip(event: GoalEvent) {
  return (
    <>
      <span className="block font-semibold">{event.type.replace(/_/g, " ")}</span>
      {event.step && (
        <span className="mt-1 block whitespace-normal">Step: {event.step as string}</span>
      )}
      {event.tool_name && (
        <span className="mt-1 block whitespace-normal">Tool: {event.tool_name as string}</span>
      )}
      {event.ts && (
        <span className="mt-1 block whitespace-normal">{String(event.ts).slice(0, 19)}</span>
      )}
    </>
  );
}

export function ExecutionTimeline({ events }: ExecutionTimelineProps) {
  const tooltipId = useId();
  const visibleEvents = useMemo(() => events.slice(0, 40), [events]);
  const triggerRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const tooltipRef = useRef<HTMLDivElement | null>(null);
  const [tooltip, setTooltip] = useState<TooltipState | null>(null);

  const activeEvent = tooltip === null ? undefined : visibleEvents[tooltip.eventIndex];

  function showTooltip(eventIndex: number, element: HTMLElement) {
    setTooltip({ eventIndex, ...getTooltipPosition(element) });
  }

  useEffect(() => {
    if (tooltip === null) return undefined;

    const updateTooltip = () => {
      const element = triggerRefs.current[tooltip.eventIndex];
      if (!element || !document.contains(element)) {
        setTooltip(null);
        return;
      }

      const measuredHeight = tooltipRef.current?.getBoundingClientRect().height ?? 0;
      const tooltipHeight = measuredHeight > 0 ? measuredHeight : FALLBACK_TOOLTIP_HEIGHT;
      setTooltip({ eventIndex: tooltip.eventIndex, ...getTooltipPosition(element, tooltipHeight) });
    };

    const dismissOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setTooltip(null);
      }
    };

    updateTooltip();
    window.addEventListener("resize", updateTooltip);
    window.addEventListener("scroll", updateTooltip, true);
    document.addEventListener("keydown", dismissOnEscape);

    return () => {
      window.removeEventListener("resize", updateTooltip);
      window.removeEventListener("scroll", updateTooltip, true);
      document.removeEventListener("keydown", dismissOnEscape);
    };
  }, [tooltip?.eventIndex]);

  if (visibleEvents.length === 0) return null;

  return (
    <div className="relative overflow-visible rounded-xl border border-border bg-card">
      <div className="px-4 py-3 border-b border-border">
        <h2 className="font-semibold text-sm">Execution Timeline</h2>
        <p className="text-xs text-muted-foreground mt-0.5">
          {events.length} event{events.length !== 1 ? "s" : ""}
        </p>
      </div>
      <div className="p-4 pb-16 overflow-x-auto overflow-y-visible">
        <div className="flex items-center gap-1 min-w-max">
          {visibleEvents.map((event, i) => (
            <div key={i} className="flex items-center">
              {/* Connector line */}
              {i > 0 && (
                <div className="w-4 h-px bg-border flex-shrink-0" />
              )}
              {/* Event block */}
              <button
                ref={(element) => {
                  triggerRefs.current[i] = element;
                }}
                type="button"
                aria-label={`${event.type.replace(/_/g, " ")} ${eventLabel(event)}`}
                aria-describedby={tooltip?.eventIndex === i ? tooltipId : undefined}
                onBlur={() => setTooltip(null)}
                onFocus={(focusEvent) => showTooltip(i, focusEvent.currentTarget)}
                onMouseEnter={(mouseEvent) => showTooltip(i, mouseEvent.currentTarget)}
                onMouseLeave={(mouseEvent) => {
                  if (document.activeElement !== mouseEvent.currentTarget) {
                    setTooltip(null);
                  }
                }}
                className="group relative flex min-w-20 flex-col items-center gap-1 rounded-xl bg-transparent p-0 outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 focus-visible:ring-offset-background"
              >
                <div
                  className={`flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-full ${getEventColor(event.type, event.success as boolean | undefined)}`}
                >
                  {getEventIcon(event.type)}
                </div>
                <span className="max-w-24 truncate text-center text-xs text-muted-foreground">
                  {eventLabel(event)}
                </span>
              </button>
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
      {tooltip && activeEvent && (
        <div
          ref={tooltipRef}
          id={tooltipId}
          role="tooltip"
          style={{ left: tooltip.left, top: tooltip.top }}
          className="pointer-events-none fixed z-50 w-64 max-w-[calc(100vw-1.5rem)] -translate-x-1/2 rounded-xl border bg-popover p-3 text-left text-xs text-popover-foreground shadow-2xl"
        >
          {eventTooltip(activeEvent)}
        </div>
      )}
    </div>
  );
}
