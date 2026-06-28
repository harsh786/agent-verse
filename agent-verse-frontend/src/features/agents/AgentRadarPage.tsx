/**
 * AgentRadarPage — 6-axis health radar for an agent.
 * Visualizes Speed, Accuracy, Cost Efficiency, Tool Coverage, Success Rate, Coherence.
 */
import { useParams, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { insightsApi, agentsApi } from "@/lib/api/client";
import { ThemedRadarChart } from "@/components/charts";
import { Skeleton } from "@/components/ui/Skeleton";
import { ArrowLeft, TrendingUp, TrendingDown } from "lucide-react";

const DIMENSION_LABELS: Record<string, string> = {
  speed: "Speed",
  accuracy: "Accuracy",
  cost_efficiency: "Cost Eff.",
  tool_coverage: "Tool Coverage",
  success_rate: "Success Rate",
  coherence: "Coherence",
};

const DIMENSION_DESCRIPTIONS: Record<string, string> = {
  speed: "How quickly goals are executed relative to iteration budget",
  accuracy: "Average task completion score from evaluations",
  cost_efficiency: "Cost optimization compared to baseline",
  tool_coverage: "Breadth of tools used effectively",
  success_rate: "Percentage of goals that complete successfully",
  coherence: "LLM-assessed logical coherence of execution plans",
};

export function AgentRadarPage() {
  const { agentId } = useParams<{ agentId: string }>();
  const navigate = useNavigate();

  const { data: agent } = useQuery({
    queryKey: ["agent", agentId],
    queryFn: () => agentsApi.get(agentId!),
    enabled: !!agentId,
  });

  const { data: health, isLoading } = useQuery({
    queryKey: ["agent-health", agentId],
    queryFn: () => insightsApi.getAgentHealth(agentId!),
    enabled: !!agentId,
  });

  const { data: benchmarks } = useQuery({
    queryKey: ["benchmarks"],
    queryFn: () => insightsApi.getBenchmarks(),
    staleTime: 600_000,
  });

  const radarData = health
    ? Object.entries(health.health).map(([key, value]) => ({
        metric: DIMENSION_LABELS[key] ?? key,
        value: Math.min(1, Math.max(0, value)),
        fullMark: 1,
      }))
    : [];

  const avgScore = health
    ? Object.values(health.health).reduce((a, b) => a + b, 0) / Object.keys(health.health).length
    : 0;

  const platformAvg = benchmarks?.platform_avg_success_rate ?? 0.74;
  const successRate = health?.health.success_rate ?? 0;
  const isAboveAvg = successRate > platformAvg;

  return (
    <div className="space-y-6 max-w-3xl">
      <div className="flex items-center gap-3">
        <button
          onClick={() => navigate(`/agents/${agentId}`)}
          className="p-1.5 rounded-lg hover:bg-muted/60 text-muted-foreground hover:text-foreground transition-colors"
          aria-label="Back to agent"
        >
          <ArrowLeft className="h-4 w-4" />
        </button>
        <div>
          <h1 className="text-xl font-bold">Agent Health Radar</h1>
          <p className="text-sm text-muted-foreground">
            {agent?.name ?? agentId}
          </p>
        </div>
        {health && (
          <div className="ml-auto text-right">
            <p className="text-2xl font-bold text-foreground">{Math.round(avgScore * 100)}%</p>
            <p className="text-xs text-muted-foreground">Overall health</p>
          </div>
        )}
      </div>

      {/* Platform benchmark comparison */}
      {health && benchmarks && (
        <div className={`flex items-center gap-3 p-3 rounded-xl border ${
          isAboveAvg
            ? "bg-green-50 dark:bg-green-950/30 border-green-200 dark:border-green-800 text-green-800 dark:text-green-300"
            : "bg-amber-50 dark:bg-amber-950/30 border-amber-200 dark:border-amber-800 text-amber-800 dark:text-amber-300"
        }`}>
          {isAboveAvg ? <TrendingUp className="h-4 w-4 shrink-0" /> : <TrendingDown className="h-4 w-4 shrink-0" />}
          <div className="text-sm">
            <span className="font-medium">
              {isAboveAvg ? "Above" : "Below"} platform average
            </span>
            <span className="ml-2 opacity-70 text-xs">
              Your success rate: {Math.round(successRate * 100)}% · Platform avg: {Math.round(platformAvg * 100)}%
              {health.sample_size > 0 ? ` · Based on ${health.sample_size} runs` : " · Insufficient data"}
            </span>
          </div>
        </div>
      )}

      {/* Radar chart */}
      <div className="bg-card border border-border rounded-xl p-6">
        {isLoading ? (
          <Skeleton className="h-64 w-full" />
        ) : (
          <ThemedRadarChart data={radarData} height={280} label="Score" />
        )}
      </div>

      {/* Dimension breakdown */}
      {health && (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {Object.entries(health.health).map(([key, value]) => {
            const pct = Math.round(value * 100);
            const label = DIMENSION_LABELS[key] ?? key;
            const desc = DIMENSION_DESCRIPTIONS[key] ?? "";
            return (
              <div key={key} className="bg-card border border-border rounded-xl p-4">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-sm font-medium">{label}</span>
                  <span className={`text-sm font-bold tabular-nums ${pct >= 80 ? "text-green-600 dark:text-green-400" : pct >= 60 ? "text-amber-600 dark:text-amber-400" : "text-red-500 dark:text-red-400"}`}>
                    {pct}%
                  </span>
                </div>
                <div className="w-full bg-muted rounded-full h-1.5 mb-2">
                  <div
                    className={`h-1.5 rounded-full transition-all ${pct >= 80 ? "bg-green-500" : pct >= 60 ? "bg-amber-500" : "bg-red-500"}`}
                    style={{ width: `${pct}%` }}
                    role="progressbar"
                    aria-valuenow={pct}
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-label={label}
                  />
                </div>
                <p className="text-xs text-muted-foreground">{desc}</p>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
