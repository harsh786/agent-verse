import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useAuthStore } from '@/stores/auth';
import { analyticsApi } from '@/lib/api/client';
import { ThemedBarChart, ThemedLineChart } from '@/components/charts';

const PERIODS = [7, 30, 90] as const;
type Period = typeof PERIODS[number];

export function AnalyticsDashboardPage() {
  const apiKey = useAuthStore((s) => s.apiKey);
  const [days, setDays] = useState<Period>(30);

  const { data: goals } = useQuery({
    queryKey: ['analytics-goals', days],
    queryFn: () => analyticsApi.getGoalMetrics(days),
    enabled: !!apiKey,
    refetchInterval: 60_000,
  });

  const { data: costs } = useQuery({
    queryKey: ['analytics-costs', days],
    queryFn: () => analyticsApi.getCostMetrics(days),
    enabled: !!apiKey,
    refetchInterval: 30_000,
  });

  const { data: evals } = useQuery({
    queryKey: ['analytics-evals', days],
    queryFn: () => analyticsApi.getEvalMetrics(days),
    enabled: !!apiKey,
    refetchInterval: 60_000,
  });

  const { data: tools } = useQuery({
    queryKey: ['analytics-tools'],
    queryFn: async () => {
      const res = await fetch(
        `${import.meta.env.VITE_API_URL ?? 'http://localhost:8000'}/analytics/tools`,
        { headers: { 'X-API-Key': apiKey } }
      );
      return res.ok ? res.json() : null;
    },
    enabled: !!apiKey,
    refetchInterval: 60_000,
  });

  const goalChartData = (goals as any)?.by_status
    ? Object.entries((goals as any).by_status as Record<string, number>).map(
        ([status, count]) => ({ status, count })
      )
    : [];

  const toolChartData = (((tools as any)?.tools ?? []) as any[]).slice(0, 10).map((t: any) => ({
    name: (t.name?.split(':').pop()?.slice(0, 12) || t.name) as string,
    success: t.success as number,
    failed: t.failed as number,
  }));

  const evalData = evals?.evals_by_day?.slice(-days) ?? [];

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Analytics</h1>
          <p className="text-muted-foreground text-sm mt-1">Goal, tool, eval, and cost insights</p>
        </div>
        {/* Time period selector */}
        <div className="flex gap-1 border rounded-lg overflow-hidden">
          {PERIODS.map((p) => (
            <button
              key={p}
              onClick={() => setDays(p)}
              aria-pressed={days === p}
              className={`px-3 py-1.5 text-sm transition-colors ${
                days === p ? 'bg-primary text-primary-foreground' : 'hover:bg-muted'
              }`}
            >
              {p}d
            </button>
          ))}
        </div>
      </div>

      {/* KPI row */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {[
          { label: 'Total Goals', value: (goals as any)?.total ?? '—' },
          {
            label: 'Success Rate',
            value: (goals as any)?.success_rate != null
              ? `${((goals as any).success_rate * 100).toFixed(1)}%`
              : '—',
          },
          {
            label: 'Eval Pass Rate',
            value: evals?.pass_rate != null ? `${(evals.pass_rate * 100).toFixed(1)}%` : '—',
          },
          {
            label: `Cost (${days}d)`,
            value: costs?.total_cost_usd != null ? `$${costs.total_cost_usd.toFixed(4)}` : '—',
          },
        ].map(({ label, value }) => (
          <div key={label} className="bg-card border border-border rounded-xl p-4">
            <p className="text-sm text-muted-foreground">{label}</p>
            <p className="text-2xl font-bold mt-1">{String(value)}</p>
          </div>
        ))}
      </div>

      {/* Charts */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="bg-card border border-border rounded-xl p-5">
          <h2 className="font-semibold text-sm mb-4">Goals by Status</h2>
          <ThemedBarChart
            data={goalChartData}
            bars={[{ key: 'count', label: 'Goals' }]}
            xKey="status"
            height={200}
          />
        </div>

        <div className="bg-card border border-border rounded-xl p-5">
          <h2 className="font-semibold text-sm mb-4">Top Tools (Success vs Failed)</h2>
          <ThemedBarChart
            data={toolChartData}
            bars={[
              { key: 'success', label: 'Success' },
              { key: 'failed', label: 'Failed' },
            ]}
            xKey="name"
            height={200}
          />
        </div>

        {/* Eval metrics over time */}
        <div className="bg-card border border-border rounded-xl p-5">
          <h2 className="font-semibold text-sm mb-4">Eval Pass Rate ({days}d)</h2>
          {evalData.length === 0 ? (
            <div className="h-48 flex items-center justify-center text-sm text-muted-foreground">
              No eval data yet
            </div>
          ) : (
            <ThemedLineChart
              data={evalData as Record<string, unknown>[]}
              lines={[{ key: 'pass_rate', label: 'Pass rate' }]}
              xKey="date"
              height={200}
              formatValue={(v) => `${(v * 100).toFixed(0)}%`}
            />
          )}
        </div>

        {/* Eval summary */}
        {evals && (
          <div className="bg-card border border-border rounded-xl p-5">
            <h2 className="font-semibold text-sm mb-4">Eval Summary ({days}d)</h2>
            <div className="grid grid-cols-2 gap-3">
              {[
                { label: 'Total Evals', value: evals.total_evals },
                { label: 'Pass Rate', value: `${(evals.pass_rate * 100).toFixed(1)}%` },
                { label: 'Avg Score', value: evals.avg_score.toFixed(2) },
              ].map(({ label, value }) => (
                <div key={label} className="p-3 border rounded-lg">
                  <p className="text-xs text-muted-foreground">{label}</p>
                  <p className="text-lg font-bold">{String(value)}</p>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
