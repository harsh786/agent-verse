/**
 * LiveActivityStream — scrolling real-time goal activity feed.
 * Shows the last N goal events with animated entrance.
 */
import { useNavigate } from "react-router-dom";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { LiveCostTicker } from "@/components/live/LiveCostTicker";
import { Zap, Clock, ArrowRight } from "lucide-react";

interface GoalActivity {
  id: string;
  goal: string;
  status: string;
  created_at?: string;
  cost_usd?: number;
  iterations?: number;
}

interface LiveActivityStreamProps {
  goals: GoalActivity[];
  maxItems?: number;
}

function timeAgo(dateStr?: string): string {
  if (!dateStr) return "";
  try {
    const diff = Math.floor((Date.now() - new Date(dateStr).getTime()) / 1000);
    if (diff < 60) return `${diff}s ago`;
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    return `${Math.floor(diff / 86400)}d ago`;
  } catch {
    return "";
  }
}

export function LiveActivityStream({ goals, maxItems = 12 }: LiveActivityStreamProps) {
  const navigate = useNavigate();
  const displayed = goals.slice(0, maxItems);

  if (displayed.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-40 text-muted-foreground">
        <Zap className="h-8 w-8 mb-2 opacity-30" />
        <p className="text-sm">No recent activity</p>
        <p className="text-xs mt-1 opacity-60">Submit your first goal to see it here</p>
      </div>
    );
  }

  return (
    <div className="space-y-1 overflow-y-auto max-h-80 pr-1">
      {displayed.map((goal) => {
        const isLive = ["planning", "executing", "verifying"].includes(goal.status);
        return (
          <button
            key={goal.id}
            onClick={() => navigate(`/goals/${goal.id}`)}
            className="w-full flex items-center gap-3 p-2.5 rounded-lg hover:bg-muted/50 transition-colors text-left group"
            aria-label={`View goal: ${goal.goal.slice(0, 60)}`}
          >
            {/* Status indicator */}
            <div className="shrink-0">
              <StatusBadge status={goal.status} size="sm" />
            </div>

            {/* Goal text */}
            <div className="flex-1 min-w-0">
              <p className="text-sm truncate font-medium text-foreground">{goal.goal}</p>
              <div className="flex items-center gap-2 mt-0.5">
                {goal.created_at && (
                  <span className="text-xs text-muted-foreground flex items-center gap-0.5">
                    <Clock className="h-2.5 w-2.5" />
                    {timeAgo(goal.created_at)}
                  </span>
                )}
                {goal.iterations != null && (
                  <span className="text-xs text-muted-foreground">
                    {goal.iterations} step{goal.iterations !== 1 ? "s" : ""}
                  </span>
                )}
              </div>
            </div>

            {/* Cost */}
            <div className="shrink-0">
              <LiveCostTicker currentCost={goal.cost_usd ?? 0} isRunning={isLive} />
            </div>

            {/* Arrow */}
            <ArrowRight
              className="h-3.5 w-3.5 text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity shrink-0"
              aria-hidden="true"
            />
          </button>
        );
      })}
    </div>
  );
}
