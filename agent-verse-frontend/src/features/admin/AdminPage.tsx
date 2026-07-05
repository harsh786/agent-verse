import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { adminApi } from '@/lib/api/client';

interface Tenant {
  tenant_id: string;
  plan: string;
}

function PlatformUsageCard({
  usage,
}: {
  usage: { active_goals: number; total_tenants: number };
}) {
  return (
    <div className="grid grid-cols-2 gap-4 mb-8">
      <div className="rounded-lg border bg-card p-6">
        <p className="text-sm text-muted-foreground">Active Goals</p>
        <p className="text-3xl font-bold text-foreground mt-1">{usage.active_goals}</p>
      </div>
      <div className="rounded-lg border bg-card p-6">
        <p className="text-sm text-muted-foreground">Total Tenants</p>
        <p className="text-3xl font-bold text-foreground mt-1">{usage.total_tenants}</p>
      </div>
    </div>
  );
}

const PLAN_BADGE: Record<string, string> = {
  free: 'bg-muted text-muted-foreground',
  starter: 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-300',
  professional: 'bg-purple-100 text-purple-800 dark:bg-purple-900/30 dark:text-purple-300',
  enterprise: 'bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-300',
};

export default function AdminPage() {
  const [search, setSearch] = useState('');
  const qc = useQueryClient();

  const { data: usage } = useQuery({
    queryKey: ['admin', 'usage'],
    queryFn: () => adminApi.getPlatformUsage(),
    refetchInterval: 15000,
  });

  const { data: tenantsData, isLoading } = useQuery({
    queryKey: ['admin', 'tenants'],
    queryFn: () => adminApi.listTenants(),
  });

  const planMutation = useMutation({
    mutationFn: ({ tenantId, plan }: { tenantId: string; plan: string }) =>
      adminApi.updatePlan(tenantId, plan),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['admin'] }),
  });

  const tenants: Tenant[] = (tenantsData?.tenants || []).filter(
    (t: Tenant) => !search || t.tenant_id.includes(search) || t.plan.includes(search)
  );

  return (
    <div className="p-6 max-w-6xl mx-auto">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-foreground">Platform Administration</h1>
        <p className="text-muted-foreground mt-1">Manage tenants, plans, and platform health</p>
      </div>

      {usage && <PlatformUsageCard usage={usage} />}

      <div className="rounded-lg border bg-card">
        <div className="p-4 border-b flex items-center gap-3">
          <h2 className="font-semibold text-foreground flex-1">Tenants</h2>
          <input
            className="border rounded px-3 py-1.5 text-sm bg-background text-foreground w-48"
            placeholder="Search tenants…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        {isLoading ? (
          <div className="p-8 text-center text-muted-foreground">Loading…</div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b text-left text-muted-foreground">
                <th className="p-3 font-medium">Tenant ID</th>
                <th className="p-3 font-medium">Plan</th>
                <th className="p-3 font-medium">Actions</th>
              </tr>
            </thead>
            <tbody>
              {tenants.map((t) => (
                <tr key={t.tenant_id} className="border-b last:border-0 hover:bg-muted/30">
                  <td className="p-3 font-mono text-xs">{t.tenant_id}</td>
                  <td className="p-3">
                    <span
                      className={`px-2 py-0.5 rounded text-xs font-medium ${PLAN_BADGE[t.plan] || ''}`}
                    >
                      {t.plan}
                    </span>
                  </td>
                  <td className="p-3">
                    <select
                      className="border rounded text-xs px-2 py-1 bg-background text-foreground"
                      value={t.plan}
                      onChange={(e) =>
                        planMutation.mutate({ tenantId: t.tenant_id, plan: e.target.value })
                      }
                    >
                      {['free', 'starter', 'professional', 'enterprise'].map((p) => (
                        <option key={p} value={p}>
                          {p}
                        </option>
                      ))}
                    </select>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
