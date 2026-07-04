import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { useAuthStore } from '../../stores/auth';

interface RoleCost {
  role: string;
  model: string;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  calls: number;
}

interface CostMetrics {
  goal_id: string;
  total_cost_usd: number;
  roles: RoleCost[];
  llm_cache?: {
    hits: number;
    misses: number;
    hit_rate: number;
    l1_hits: number;
    l2_hits: number;
  };
}

function RoleRow({ role }: { role: RoleCost }) {
  const colorMap: Record<string, string> = {
    planner: 'text-purple-600 dark:text-purple-400',
    executor: 'text-blue-600 dark:text-blue-400',
    verifier: 'text-green-600 dark:text-green-400',
  };
  return (
    <tr className="border-b last:border-0">
      <td className={`p-2 font-medium text-sm ${colorMap[role.role] || 'text-foreground'}`}>
        {role.role}
      </td>
      <td className="p-2 text-xs text-muted-foreground font-mono">{role.model}</td>
      <td className="p-2 text-right text-sm">{(role.input_tokens + role.output_tokens).toLocaleString()}</td>
      <td className="p-2 text-right text-sm">{role.calls}</td>
      <td className="p-2 text-right text-sm font-medium">${role.cost_usd.toFixed(6)}</td>
    </tr>
  );
}

export function CostBreakdown({ goalId }: { goalId: string }) {
  const apiKey = useAuthStore(s => s.apiKey) || '';

  const { data, isLoading } = useQuery<CostMetrics>({
    queryKey: ['cost-metrics', goalId],
    queryFn: async () => {
      const res = await fetch(`/api/goals/${goalId}/cost-metrics`, {
        headers: { 'X-API-Key': apiKey },
      });
      if (!res.ok) throw new Error(`${res.status}`);
      return res.json();
    },
    enabled: !!goalId && !!apiKey,
    staleTime: 30000,
  });

  if (isLoading) return <div className="text-sm text-muted-foreground animate-pulse">Loading cost data…</div>;
  if (!data || !data.roles?.length) return null;

  return (
    <div className="rounded-lg border bg-card p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-foreground">Cost Breakdown</h3>
        <span className="text-sm font-bold text-foreground">${data.total_cost_usd.toFixed(6)}</span>
      </div>

      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-muted-foreground border-b">
            <th className="p-2">Role</th>
            <th className="p-2">Model</th>
            <th className="p-2 text-right">Tokens</th>
            <th className="p-2 text-right">Calls</th>
            <th className="p-2 text-right">Cost</th>
          </tr>
        </thead>
        <tbody>
          {data.roles.map(r => <RoleRow key={r.role} role={r} />)}
        </tbody>
      </table>

      {data.llm_cache && (
        <div className="mt-3 pt-3 border-t text-xs text-muted-foreground">
          <span className="font-medium">LLM Cache:</span>{' '}
          {data.llm_cache.hits} hits / {data.llm_cache.misses + data.llm_cache.hits} total
          {' '}({(data.llm_cache.hit_rate * 100).toFixed(1)}% hit rate)
        </div>
      )}
    </div>
  );
}
