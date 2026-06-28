import { useParams, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { useAuthStore } from '@/stores/auth';
import { ArrowLeft, Loader2 } from 'lucide-react';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, LineChart, Line } from 'recharts';
import { Skeleton } from '@/components/ui/Skeleton';

const API_BASE = (import.meta as any).env?.VITE_API_URL ?? 'http://localhost:8000';

export function AgentDashboardPage() {
  const { agentId } = useParams<{ agentId: string }>();
  const navigate = useNavigate();
  const apiKey = useAuthStore((s) => s.apiKey);

  const { data: agent, isLoading: agentLoading } = useQuery({
    queryKey: ['agent', agentId],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}/agents/${agentId}`, { headers: { 'X-API-Key': apiKey } });
      if (!res.ok) throw new Error(res.statusText);
      return res.json();
    },
    enabled: !!agentId && !!apiKey,
  });

  const { data: goals, isLoading: goalsLoading } = useQuery({
    queryKey: ['goals', 'byAgent', agentId],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}/goals`, { headers: { 'X-API-Key': apiKey } });
      if (!res.ok) throw new Error(res.statusText);
      const data = await res.json();
      const allGoals: Array<{ agent_id?: string; status: string; cost_usd?: number; created_at?: string; goal: string; goal_id?: string; id?: string }> = data.goals ?? data ?? [];
      return allGoals.filter((g) => g.agent_id === agentId);
    },
    enabled: !!agentId && !!apiKey,
  });

  const { data: analytics } = useQuery({
    queryKey: ['analytics-agent', agentId],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}/analytics/goals?agent_id=${agentId}&days=30`, {
        headers: { 'X-API-Key': apiKey },
      });
      if (!res.ok) return null;
      return res.json();
    },
    enabled: !!agentId && !!apiKey,
  });

  const totalGoals = goals?.length ?? 0;
  const successCount = goals?.filter((g) => g.status === 'complete').length ?? 0;
  const successRate = totalGoals > 0 ? ((successCount / totalGoals) * 100).toFixed(1) : '—';
  const totalCost = goals?.reduce((s, g) => s + (g.cost_usd ?? 0), 0) ?? 0;

  // Build daily chart data
  const byDay: Record<string, { date: string; goals: number; success: number }> = {};
  goals?.forEach((g) => {
    const d = g.created_at ? g.created_at.slice(0, 10) : 'unknown';
    if (!byDay[d]) byDay[d] = { date: d, goals: 0, success: 0 };
    byDay[d].goals++;
    if (g.status === 'complete') byDay[d].success++;
  });
  const chartData = Object.values(byDay).slice(-14).sort((a, b) => a.date.localeCompare(b.date));

  const statusData = Object.entries(
    (goals ?? []).reduce<Record<string, number>>((acc, g) => {
      acc[g.status] = (acc[g.status] ?? 0) + 1;
      return acc;
    }, {})
  ).map(([status, count]) => ({ status, count }));

  if (agentLoading) {
    return (
      <div className="flex items-center justify-center h-40">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <button
          onClick={() => navigate(`/agents/${agentId}`)}
          className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors"
        >
          <ArrowLeft className="h-4 w-4" /> Back to Agent
        </button>
      </div>

      <div>
        <h1 className="text-2xl font-bold">{agent?.name ?? agentId} — Dashboard</h1>
        <p className="text-muted-foreground text-sm mt-1">
          Goal execution metrics and performance for this agent
        </p>
      </div>

      {/* KPI cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {[
          { label: 'Total Goals', value: totalGoals },
          { label: 'Success Rate', value: `${successRate}%` },
          { label: 'Total Cost', value: `$${totalCost.toFixed(4)}` },
          { label: 'Active', value: goals?.filter((g) => g.status === 'executing').length ?? 0 },
        ].map(({ label, value }) => (
          <div key={label} className="bg-card border border-border rounded-xl p-4">
            <p className="text-sm text-muted-foreground">{label}</p>
            <p className="text-2xl font-bold mt-1">{String(value)}</p>
          </div>
        ))}
      </div>

      {/* Charts */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Goals over time */}
        <div className="bg-card border border-border rounded-xl p-5">
          <h2 className="font-semibold text-sm mb-4">Goals over time (last 14 days)</h2>
          {goalsLoading ? (
            <Skeleton className="h-48 w-full" />
          ) : (
            <ResponsiveContainer width="100%" height={200}>
              <LineChart data={chartData}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="date" tick={{ fontSize: 10 }} tickFormatter={(d) => d.slice(5)} />
                <YAxis tick={{ fontSize: 10 }} />
                <Tooltip />
                <Line type="monotone" dataKey="goals" stroke="#3b82f6" strokeWidth={2} dot={false} name="Goals" />
                <Line type="monotone" dataKey="success" stroke="#22c55e" strokeWidth={2} dot={false} name="Success" />
              </LineChart>
            </ResponsiveContainer>
          )}
        </div>

        {/* Goals by status */}
        <div className="bg-card border border-border rounded-xl p-5">
          <h2 className="font-semibold text-sm mb-4">Goals by status</h2>
          {goalsLoading ? (
            <Skeleton className="h-48 w-full" />
          ) : (
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={statusData}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="status" tick={{ fontSize: 10 }} />
                <YAxis tick={{ fontSize: 10 }} />
                <Tooltip />
                <Bar dataKey="count" fill="#6366f1" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>
      </div>

      {/* Analytics from /analytics/goals */}
      {analytics && (
        <div className="bg-card border border-border rounded-xl p-4">
          <h2 className="font-semibold text-sm mb-3">Platform Analytics</h2>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
            {Object.entries(analytics as Record<string, unknown>).slice(0, 8).map(([k, v]) => (
              <div key={k}>
                <p className="text-xs text-muted-foreground capitalize">{k.replace(/_/g, ' ')}</p>
                <p className="font-medium">{String(v)}</p>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Recent goals list */}
      <div className="bg-card border border-border rounded-xl overflow-hidden">
        <div className="px-5 py-3 border-b border-border">
          <h2 className="font-semibold text-sm">Recent Goals</h2>
        </div>
        {goalsLoading ? (
          <div className="p-4 space-y-2">
            {Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-10 w-full" />)}
          </div>
        ) : goals && goals.length > 0 ? (
          <div className="divide-y divide-border">
            {goals.slice(0, 10).map((g) => (
              <div
                key={g.goal_id ?? g.id}
                className="flex items-center justify-between px-5 py-3 text-sm"
              >
                <p className="truncate flex-1">{g.goal}</p>
                <span className={`ml-3 px-2 py-0.5 rounded-full text-xs flex-shrink-0 ${
                  g.status === 'complete' ? 'bg-green-100 text-green-700'
                  : g.status === 'failed' ? 'bg-red-100 text-red-700'
                  : 'bg-blue-100 text-blue-700'
                }`}>{g.status}</span>
              </div>
            ))}
          </div>
        ) : (
          <p className="px-5 py-4 text-sm text-muted-foreground">No goals run by this agent yet.</p>
        )}
      </div>
    </div>
  );
}
