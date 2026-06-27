import { useQuery } from '@tanstack/react-query';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts';
import { useAuthStore } from '@/stores/auth';
import { API_BASE } from '@/lib/api/client';

export function AnalyticsDashboardPage() {
  const apiKey = useAuthStore(s => s.apiKey);

  const fetchAnalytics = (path: string) =>
    fetch(`${API_BASE}/analytics/${path}`, { headers: { 'X-API-Key': apiKey } }).then(r => r.json());

  const { data: goals } = useQuery({
    queryKey: ['analytics-goals'],
    queryFn: () => fetchAnalytics('goals'),
    enabled: !!apiKey,
    refetchInterval: 60_000,
  });
  const { data: tools } = useQuery({
    queryKey: ['analytics-tools'],
    queryFn: () => fetchAnalytics('tools'),
    enabled: !!apiKey,
    refetchInterval: 60_000,
  });
  const { data: costs } = useQuery({
    queryKey: ['analytics-costs'],
    queryFn: () => fetchAnalytics('costs'),
    enabled: !!apiKey,
    refetchInterval: 30_000,
  });

  const goalChartData = goals?.by_status
    ? Object.entries(goals.by_status as Record<string, number>).map(([status, count]) => ({ status, count }))
    : [];

  const toolChartData = ((tools?.tools || []) as any[]).slice(0, 10).map((t: any) => ({
    name: (t.name?.split(':').pop()?.slice(0, 12) || t.name) as string,
    success: t.success as number,
    failed: t.failed as number,
  }));

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Analytics</h1>
        <p className="text-muted-foreground text-sm mt-1">Goal, tool, and cost insights</p>
      </div>

      {/* KPI row */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {[
          { label: 'Total Goals', value: goals?.total ?? '—' },
          { label: 'Success Rate', value: goals?.success_rate != null ? `${(goals.success_rate * 100).toFixed(1)}%` : '—' },
          { label: 'Cost Today', value: costs?.cost_today_usd != null ? `$${(costs.cost_today_usd as number).toFixed(4)}` : '—' },
          { label: 'Goals Today', value: costs?.goals_today ?? '—' },
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
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={goalChartData}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="status" tick={{ fontSize: 11 }} />
              <YAxis tick={{ fontSize: 11 }} />
              <Tooltip />
              <Bar dataKey="count" fill="#3b82f6" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>

        <div className="bg-card border border-border rounded-xl p-5">
          <h2 className="font-semibold text-sm mb-4">Top Tools (Success vs Failed)</h2>
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={toolChartData}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="name" tick={{ fontSize: 10 }} />
              <YAxis tick={{ fontSize: 11 }} />
              <Tooltip />
              <Bar dataKey="success" fill="#22c55e" radius={[2, 2, 0, 0]} />
              <Bar dataKey="failed" fill="#ef4444" radius={[2, 2, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  );
}
