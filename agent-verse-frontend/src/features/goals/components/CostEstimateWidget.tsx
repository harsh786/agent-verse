/**
 * CostEstimateWidget — shows pre-run cost/time estimates before submitting.
 * Fetches from /insights/estimate and renders confidence-banded estimates.
 */
import { useQuery } from "@tanstack/react-query";
import { insightsApi } from "@/lib/api/client";
import { Skeleton } from "@/components/ui/Skeleton";
import { TrendingUp, Clock, Zap, Info } from "lucide-react";

interface CostEstimateWidgetProps {
  goal: string;
  /** Only fetch when this is true (e.g. when goal text has >= 10 chars) */
  enabled?: boolean;
  className?: string;
}

const CONFIDENCE_COLOR = {
  low: "text-muted-foreground",
  medium: "text-amber-600 dark:text-amber-400",
  high: "text-green-600 dark:text-green-400",
};

export function CostEstimateWidget({ goal, enabled = true, className = "" }: CostEstimateWidgetProps) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["estimate", goal.slice(0, 50)],
    queryFn: () => insightsApi.estimateGoal(goal),
    enabled: enabled && goal.length >= 10,
    staleTime: 60_000,
    retry: false,
  });

  if (!enabled || goal.length < 10) return null;
  if (isLoading) return (
    <div className={`rounded-lg border border-border p-3 ${className}`}>
      <Skeleton className="h-4 w-32 mb-2" />
      <div className="grid grid-cols-3 gap-3">
        <Skeleton className="h-8" />
        <Skeleton className="h-8" />
        <Skeleton className="h-8" />
      </div>
    </div>
  );
  if (isError || !data) return null;

  const costStr = `$${data.estimated_cost_usd.mean.toFixed(3)}`;
  const costRange = `$${data.estimated_cost_usd.min.toFixed(3)}–$${data.estimated_cost_usd.max.toFixed(3)}`;
  const durationStr = data.estimated_duration_s.mean < 60
    ? `~${data.estimated_duration_s.mean}s`
    : `~${Math.round(data.estimated_duration_s.mean / 60)}m`;
  const successPct = Math.round(data.success_probability * 100);

  return (
    <div className={`rounded-lg border border-border bg-muted/30 p-3 ${className}`}>
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs font-medium text-foreground flex items-center gap-1.5">
          <Zap className="h-3.5 w-3.5 text-primary" aria-hidden="true" />
          Estimated run
        </span>
        <span className={`text-xs ${CONFIDENCE_COLOR[data.confidence]}`}>
          {data.confidence} confidence
          {data.similar_goals_count > 0 && ` · ${data.similar_goals_count} similar`}
        </span>
      </div>
      <div className="grid grid-cols-3 gap-3">
        <div className="text-center">
          <p className="text-xs text-muted-foreground flex items-center justify-center gap-1 mb-0.5">
            <TrendingUp className="h-3 w-3" aria-hidden="true" /> Cost
          </p>
          <p className="text-sm font-semibold tabular-nums" title={costRange}>{costStr}</p>
          <p className="text-[10px] text-muted-foreground/70">{costRange}</p>
        </div>
        <div className="text-center">
          <p className="text-xs text-muted-foreground flex items-center justify-center gap-1 mb-0.5">
            <Clock className="h-3 w-3" aria-hidden="true" /> Time
          </p>
          <p className="text-sm font-semibold">{durationStr}</p>
          <p className="text-[10px] text-muted-foreground/70">
            {data.estimated_iterations.min}–{data.estimated_iterations.max} steps
          </p>
        </div>
        <div className="text-center">
          <p className="text-xs text-muted-foreground flex items-center justify-center gap-1 mb-0.5">
            <Info className="h-3 w-3" aria-hidden="true" /> Success
          </p>
          <p className={`text-sm font-semibold ${successPct >= 80 ? "text-green-600 dark:text-green-400" : successPct >= 60 ? "text-amber-600 dark:text-amber-400" : "text-red-500 dark:text-red-400"}`}>
            {successPct}%
          </p>
          <p className="text-[10px] text-muted-foreground/70">probability</p>
        </div>
      </div>
    </div>
  );
}
