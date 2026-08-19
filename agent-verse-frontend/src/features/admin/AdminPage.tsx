/**
 * AdminPage — Platform Administration
 *
 * Sections:
 *   1. Live metrics bar (tenants, active goals, platform health, avg latency)
 *   2. Plan distribution filter chips
 *   3. Tenant management table (search, filter, inline plan upgrade)
 *   4. System health panel
 *   5. Quick action links
 */

import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Activity, AlertCircle, CheckCircle2,
  Clock, Database, Loader2, RefreshCw,
  Search, Settings2, Shield, TrendingUp, Users, Zap,
} from 'lucide-react';
import { adminApi } from '@/lib/api/client';
import { JARVISPageShell, JARVISStagger } from '@/components/ui/JARVISPageShell';

// ── Types ─────────────────────────────────────────────────────────────────────

interface Tenant {
  tenant_id: string;
  name?: string;
  plan: string;
  created_at?: string;
  goal_count?: number;
}

interface PlatformUsage {
  active_goals: number;
  total_tenants: number;
  goals_today?: number;
  avg_latency_ms?: number;
}

// ── Constants ─────────────────────────────────────────────────────────────────

const PLANS = ['free', 'starter', 'professional', 'enterprise'] as const;
type Plan = typeof PLANS[number];

const PLAN_COLORS: Record<string, string> = {
  free:         'bg-[#252B3B]/60 text-[#CBD5E1] border border-[#1E2535]',
  starter:      'bg-blue-500/20 text-blue-400 border border-blue-500/40',
  professional: 'bg-purple-500/20 text-purple-400 border border-purple-500/40',
  enterprise:   'bg-amber-500/20 text-amber-400 border border-amber-500/40',
};

const PLAN_RANK: Record<string, number> = { free: 0, starter: 1, professional: 2, enterprise: 3 };

// ── Sub-components ─────────────────────────────────────────────────────────────

function MetricCard({
  label, value, icon: Icon, sub, accent = 'indigo', testId,
}: {
  label: string; value: string | number; icon: React.ElementType;
  sub?: string; accent?: 'indigo' | 'emerald' | 'amber' | 'red'; testId?: string;
}) {
  const colors = {
    indigo:  'text-indigo-400 bg-indigo-500/10',
    emerald: 'text-emerald-400 bg-emerald-500/10',
    amber:   'text-amber-400 bg-amber-500/10',
    red:     'text-red-400 bg-red-500/10',
  };
  return (
    <div className="flex items-center gap-4 rounded-xl border border-[#1E2535] bg-[#1A1F2E]/60 p-4" data-testid={testId}>
      <div className={`rounded-lg p-2.5 ${colors[accent]}`}>
        <Icon className={`h-5 w-5 ${colors[accent].split(' ')[0]}`} />
      </div>
      <div>
        <p className="text-xs text-[#5A7494] uppercase tracking-wider">{label}</p>
        <p className="text-2xl font-bold text-[#00D4FF]">{value}</p>
        {sub && <p className="text-xs text-[#5A7494] mt-0.5">{sub}</p>}
      </div>
    </div>
  );
}

function HealthBadge({ status }: { status?: string }) {
  const isOk = !status || status === 'ok' || status === 'healthy';
  return (
    <span
      data-testid="health-badge"
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ${
        isOk
          ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/40'
          : 'bg-red-500/20 text-red-400 border border-red-500/40'
      }`}
    >
      {isOk ? <CheckCircle2 className="h-3 w-3" /> : <AlertCircle className="h-3 w-3" />}
      {isOk ? 'Healthy' : 'Degraded'}
    </span>
  );
}

// ── Main Page ─────────────────────────────────────────────────────────────────

export default function AdminPage() {
  const [search, setSearch]       = useState('');
  const [planFilter, setPlanFilter] = useState<Plan | 'all'>('all');
  const [updatingId, setUpdatingId] = useState<string | null>(null);
  const qc = useQueryClient();

  const { data: usage, isLoading: usageLoading, refetch: refetchUsage } = useQuery({
    queryKey: ['admin', 'usage'],
    queryFn: () => adminApi.getPlatformUsage() as Promise<PlatformUsage>,
    refetchInterval: 15_000,
  });

  const { data: tenantsData, isLoading: tenantsLoading, dataUpdatedAt } = useQuery({
    queryKey: ['admin', 'tenants'],
    queryFn: () => adminApi.listTenants({ limit: 200 }),
    refetchInterval: 30_000,
  });

  const { data: health } = useQuery({
    queryKey: ['admin', 'health'],
    queryFn: () =>
      fetch(`${import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'}/health`)
        .then((r) => (r.ok ? r.json() : { status: 'error' }))
        .catch(() => ({ status: 'error' })),
    refetchInterval: 30_000,
  });

  const planMutation = useMutation({
    mutationFn: ({ tenantId, plan }: { tenantId: string; plan: string }) =>
      adminApi.updatePlan(tenantId, plan),
    onMutate:   ({ tenantId }) => setUpdatingId(tenantId),
    onSettled:  () => { setUpdatingId(null); qc.invalidateQueries({ queryKey: ['admin'] }); },
  });

  const allTenants: Tenant[] = tenantsData?.tenants ?? [];
  const filtered = allTenants.filter((t) => {
    const matchSearch = !search || t.tenant_id.toLowerCase().includes(search.toLowerCase()) || (t.name ?? '').toLowerCase().includes(search.toLowerCase());
    const matchPlan = planFilter === 'all' || t.plan === planFilter;
    return matchSearch && matchPlan;
  });
  const sorted = [...filtered].sort((a, b) => (PLAN_RANK[b.plan] ?? 0) - (PLAN_RANK[a.plan] ?? 0));
  const planCounts = PLANS.reduce<Record<string, number>>((acc, p) => { acc[p] = allTenants.filter((t) => t.plan === p).length; return acc; }, {});
  const lastUpdated = dataUpdatedAt ? new Date(dataUpdatedAt).toLocaleTimeString() : '—';

  return (
    <JARVISPageShell>

      {/* Accessibility: announce loading state */}
      <div aria-live="polite" aria-atomic="true" className="sr-only"></div>
    <JARVISStagger className="flex flex-col gap-6 p-4 lg:p-6" data-testid="admin-page">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-[#F1F5F9] flex items-center gap-2">
            <Shield className="h-6 w-6 text-indigo-400" />
            Platform Administration
          </h1>
          <p className="mt-1 text-sm text-[#94A3B8]">Manage tenants, plans, and platform health</p>
        </div>
        <div className="flex items-center gap-3">
          <HealthBadge status={health?.status} />
          <button
            onClick={() => refetchUsage()}
            disabled={usageLoading}
            data-testid="refresh-btn"
            aria-label="Refresh metrics"
            className="flex items-center gap-1.5 rounded-lg border border-[#1E2535] px-3 py-1.5 text-xs text-[#94A3B8] hover:text-[#E2E8F0] hover:border-[#3D4D6A] transition-colors"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${usageLoading ? 'animate-spin' : ''}`} />
            Refresh
          </button>
        </div>
      </div>

      {/* Metrics */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4" data-testid="metrics-row">
        <MetricCard label="Total Tenants"  value={usageLoading ? '…' : (usage?.total_tenants ?? allTenants.length)} icon={Users}      accent="indigo"  testId="metric-tenants" />
        <MetricCard label="Active Goals"   value={usageLoading ? '…' : (usage?.active_goals ?? 0)}                  icon={Zap}        accent="emerald" sub="running now" testId="metric-active-goals" />
        <MetricCard label="Goals Today"    value={usage?.goals_today ?? '—'}                                         icon={TrendingUp} accent="indigo"  testId="metric-goals-today" />
        <MetricCard label="Avg Latency"    value={usage?.avg_latency_ms ? `${usage.avg_latency_ms}ms` : '—'}         icon={Clock}      accent={usage?.avg_latency_ms && usage.avg_latency_ms > 5000 ? 'amber' : 'emerald'} testId="metric-latency" />
      </div>

      {/* Plan distribution */}
      <div className="flex flex-wrap gap-2 rounded-xl border border-[#1E2535] bg-[#1A1F2E]/40 px-4 py-3" data-testid="plan-distribution">
        <span className="text-xs text-[#5A7494] mr-2 self-center">Plans:</span>
        {PLANS.map((p) => (
          <button
            key={p}
            data-testid={`plan-filter-${p}`}
            onClick={() => setPlanFilter(planFilter === p ? 'all' : p)}
            className={`rounded-full px-3 py-1 text-xs font-medium transition-[color,background-color,border-color,opacity,box-shadow,transform] ${planFilter === p ? PLAN_COLORS[p] : 'bg-[#252B3B]/40 text-[#94A3B8] hover:bg-[#252B3B] border border-transparent'}`}
          >
            {p.charAt(0).toUpperCase() + p.slice(1)} <span className="opacity-70">({planCounts[p] ?? 0})</span>
          </button>
        ))}
        {planFilter !== 'all' && (
          <button onClick={() => setPlanFilter('all')} className="ml-auto rounded-full px-3 py-1 text-xs text-[#5A7494] hover:text-[#CBD5E1]">Clear ×</button>
        )}
      </div>

      {/* Tenant table */}
      <div className="rounded-xl border border-[#1E2535] bg-[#1A1F2E]/40 overflow-hidden">
        <div className="flex items-center gap-3 border-b border-[#1E2535] px-4 py-3">
          <h2 className="font-semibold text-[#E2E8F0] flex-1 flex items-center gap-2">
            <Database className="h-4 w-4 text-[#5A7494]" />
            Tenants
            <span className="text-xs text-[#5A7494] font-normal">{sorted.length} / {allTenants.length}</span>
          </h2>
          <p className="hidden sm:block text-xs text-[#374151]">Updated {lastUpdated}</p>
          <div className="relative">
            <Search className="absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-[#5A7494]" />
            <input
              className="rounded-lg border border-[#1E2535] bg-[#0F1117] pl-7 pr-3 py-1.5 text-xs text-[#E2E8F0] placeholder:text-[#374151] focus:border-indigo-500 focus:outline-none w-44"
              placeholder="Search…"
              value={search}
              data-testid="tenant-search"
              aria-label="Search tenants"
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
        </div>

        {tenantsLoading ? (
          <div className="flex items-center justify-center gap-2 py-16 text-[#5A7494]" data-testid="tenants-loading">
            <Loader2 className="h-5 w-5 animate-spin" /> Loading tenants…
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm" data-testid="tenant-table">
              <thead>
                <tr className="border-b border-[#1E2535] text-xs uppercase tracking-wider text-[#5A7494]">
                  <th className="px-4 py-2.5 font-medium">Tenant ID</th>
                  <th className="px-4 py-2.5 font-medium hidden sm:table-cell">Name</th>
                  <th className="px-4 py-2.5 font-medium">Plan</th>
                  <th className="px-4 py-2.5 font-medium">Actions</th>
                </tr>
              </thead>
              <tbody>
                {sorted.length === 0 ? (
                  <tr>
                    <td colSpan={4} className="py-12 text-center text-sm text-[#5A7494]" data-testid="tenants-empty">
                      No tenants match your filter
                    </td>
                  </tr>
                ) : sorted.map((t) => (
                  <tr key={t.tenant_id} className="border-b border-[#1E2535] last:border-0 hover:bg-[#252B3B]/20 transition-colors" data-testid="tenant-row">
                    <td className="px-4 py-3 font-mono text-xs text-[#CBD5E1] max-w-[160px] truncate">{t.tenant_id}</td>
                    <td className="px-4 py-3 text-[#94A3B8] hidden sm:table-cell text-xs">{t.name ?? '—'}</td>
                    <td className="px-4 py-3">
                      <span className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${PLAN_COLORS[t.plan] ?? ''}`}>{t.plan}</span>
                    </td>
                    <td className="px-4 py-3">
                      {updatingId === t.tenant_id ? (
                        <Loader2 className="h-4 w-4 animate-spin text-indigo-400" />
                      ) : (
                        <select
                          className="rounded-lg border border-[#1E2535] bg-[#1A1F2E] px-2 py-1 text-xs text-[#E2E8F0] focus:border-indigo-500 focus:outline-none"
                          value={t.plan}
                          data-testid={`plan-select-${t.tenant_id}`}
                          disabled={planMutation.isPending}
                          aria-label={`Change plan for ${t.tenant_id}`}
                          onChange={(e) => e.target.value !== t.plan && planMutation.mutate({ tenantId: t.tenant_id, plan: e.target.value })}
                        >
                          {PLANS.map((p) => <option key={p} value={p} className="bg-[#0F1117]">{p.charAt(0).toUpperCase() + p.slice(1)}</option>)}
                        </select>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* System health */}
      <div className="rounded-xl border border-[#1E2535] bg-[#1A1F2E]/40 p-4" data-testid="system-health">
        <h2 className="mb-3 font-semibold text-[#E2E8F0] flex items-center gap-2">
          <Activity className="h-4 w-4 text-emerald-400" />
          System Health
        </h2>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {[
            { name: 'API',      status: health?.status },
            { name: 'Database', status: health?.db },
            { name: 'Redis',    status: health?.redis },
            { name: 'Workers',  status: health?.celery },
          ].map(({ name, status }) => {
            const isOk = !status || status === 'ok' || status === 'healthy';
            return (
              <div key={name} className={`flex items-center gap-2 rounded-lg border px-3 py-2.5 ${isOk ? 'border-emerald-800/50 bg-emerald-900/10' : 'border-red-800/50 bg-red-900/10'}`} data-testid={`health-${name.toLowerCase()}`}>
                <div className={`h-2 w-2 rounded-full shrink-0 ${isOk ? 'bg-emerald-500' : 'bg-red-500'}`} />
                <span className="text-xs text-[#CBD5E1]">{name}</span>
                <span className={`ml-auto text-xs ${isOk ? 'text-emerald-500' : 'text-red-400'}`}>{isOk ? 'ok' : (status ?? '—')}</span>
              </div>
            );
          })}
        </div>
      </div>

      {/* Quick actions */}
      <div className="flex flex-wrap gap-2">
        {[
          { label: 'View Audit Log',  icon: Settings2, href: '/audit' },
          { label: 'Governance',      icon: Shield,    href: '/governance' },
          { label: 'Observability',   icon: Activity,  href: '/observability' },
        ].map(({ label, icon: Icon, href }) => (
          <a key={label} href={href}
            data-testid={`quick-link-${label.toLowerCase().replace(/\s+/g, '-')}`}
            className="flex items-center gap-1.5 rounded-lg border border-[#1E2535] px-3 py-2 text-xs text-[#94A3B8] hover:border-[#3D4D6A] hover:text-[#E2E8F0] transition-colors"
          >
            <Icon className="h-3.5 w-3.5" /> {label}
          </a>
        ))}
      </div>
    </JARVISStagger>
    </JARVISPageShell>
  );
}
