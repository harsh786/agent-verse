/**
 * LiveActivityStream — animated real-time goal activity feed.
 * Uses AnimatePresence popLayout so new SSE items slide in from top.
 */
import { useNavigate } from "react-router-dom";
import { motion, AnimatePresence } from "framer-motion";
import { StatusOrb } from "@/components/ui/StatusOrb";
import { LiveCostTicker } from "@/components/live/LiveCostTicker";
import { Zap, Clock } from "lucide-react";
import { SPRING_PAGE } from "@/components/ui/JARVISPageShell";

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
  } catch { return ""; }
}

export function LiveActivityStream({ goals, maxItems = 12 }: LiveActivityStreamProps) {
  const navigate = useNavigate();
  const displayed = goals.slice(0, maxItems);

  if (displayed.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-40 text-[#475569]">
        <Zap className="h-8 w-8 mb-2 opacity-30 text-[#00D4FF]" />
        <p className="text-sm">No recent activity</p>
        <p className="text-xs mt-1 opacity-60">Submit your first goal to see it here</p>
      </div>
    );
  }

  return (
    <div className="space-y-0.5 overflow-y-auto max-h-80 pr-1">
      <AnimatePresence mode="popLayout">
        {displayed.map((goal, i) => {
          const isLive = ["planning", "executing", "verifying", "running"].includes(goal.status);
          return (
            <motion.button
              key={goal.id}
              layout
              initial={{ opacity: 0, y: -10, scale: 0.97 }}
              animate={{ opacity: 1, y: 0,   scale: 1 }}
              exit={{    opacity: 0, x: 20,  scale: 0.95 }}
              transition={{ ...SPRING_PAGE, delay: i * 0.03 }}
              onClick={() => navigate(`/goals/${goal.id}`)}
              className="w-full flex items-center gap-3 p-2.5 rounded-lg
                         hover:bg-white/[0.04] hover:shadow-[0_0_12px_rgba(0,212,255,0.06)]
                         transition-shadow text-left group"
              aria-label={`View goal: ${goal.goal.slice(0, 60)}`}
            >
              <StatusOrb status={goal.status} size={8} className="shrink-0" />

              <div className="flex-1 min-w-0">
                <p className="text-sm truncate font-medium text-[#F1F5F9]">{goal.goal}</p>
                <div className="flex items-center gap-2 mt-0.5">
                  {goal.created_at && (
                    <span className="text-xs text-[#475569] flex items-center gap-0.5">
                      <Clock className="h-2.5 w-2.5" />
                      {timeAgo(goal.created_at)}
                    </span>
                  )}
                  {goal.iterations != null && (
                    <span className="text-xs text-[#475569]">
                      {goal.iterations} step{goal.iterations !== 1 ? "s" : ""}
                    </span>
                  )}
                </div>
              </div>

              <div className="shrink-0">
                <LiveCostTicker currentCost={goal.cost_usd ?? 0} isRunning={isLive} />
              </div>
            </motion.button>
          );
        })}
      </AnimatePresence>
    </div>
  );
}
