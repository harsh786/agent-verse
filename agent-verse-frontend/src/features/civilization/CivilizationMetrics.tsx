import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts';
import type { CivilizationMetrics as MetricsType } from '../../lib/api/civilizationApi';

interface Props {
  metrics: MetricsType;
  spawnHistory?: { ts: string; spawns: number }[];
}

export function CivilizationMetrics({ metrics, spawnHistory = [] }: Props) {
  return (
    <div className="grid grid-cols-2 gap-3">
      {/* KPI Cards */}
      <div className="bg-blue-50 rounded-lg p-3">
        <div className="text-2xl font-bold text-blue-700">{metrics.active_members}</div>
        <div className="text-xs text-blue-500">Active Agents</div>
      </div>
      <div className="bg-green-50 rounded-lg p-3">
        <div className="text-2xl font-bold text-green-700">{(metrics.avg_reputation * 100).toFixed(0)}%</div>
        <div className="text-xs text-green-500">Avg Reputation</div>
      </div>
      <div className="bg-purple-50 rounded-lg p-3">
        <div className="text-2xl font-bold text-purple-700">{metrics.total_members}</div>
        <div className="text-xs text-purple-500">Total Members</div>
      </div>
      <div className="bg-amber-50 rounded-lg p-3">
        <div className="text-2xl font-bold text-amber-700">${metrics.total_budget_spent_usd.toFixed(2)}</div>
        <div className="text-xs text-amber-500">Budget Spent</div>
      </div>

      {/* Idle / Retired summary */}
      <div className="bg-slate-50 rounded-lg p-3">
        <div className="text-2xl font-bold text-slate-600">{metrics.idle_members}</div>
        <div className="text-xs text-slate-500">Idle Agents</div>
      </div>
      <div className="bg-gray-50 rounded-lg p-3">
        <div className="text-2xl font-bold text-gray-600">{metrics.retired_members}</div>
        <div className="text-xs text-gray-500">Retired Agents</div>
      </div>

      {/* Reputation range */}
      <div className="col-span-2 bg-white border rounded-lg p-3 text-xs">
        <div className="text-gray-500 mb-2 font-medium">Reputation Range</div>
        <div className="flex gap-3 justify-between">
          <div>
            <span className="text-gray-400">Min </span>
            <span className="font-semibold text-red-600">{(metrics.min_reputation * 100).toFixed(0)}%</span>
          </div>
          <div>
            <span className="text-gray-400">Avg </span>
            <span className="font-semibold text-blue-600">{(metrics.avg_reputation * 100).toFixed(0)}%</span>
          </div>
          <div>
            <span className="text-gray-400">Max </span>
            <span className="font-semibold text-green-600">{(metrics.max_reputation * 100).toFixed(0)}%</span>
          </div>
        </div>
      </div>

      {/* Spawn history chart */}
      {spawnHistory.length > 0 && (
        <div className="col-span-2 h-24">
          <div className="text-xs text-gray-500 mb-1">Spawn Rate</div>
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={spawnHistory}>
              <XAxis dataKey="ts" hide />
              <YAxis hide />
              <Tooltip />
              <Bar dataKey="spawns" fill="#6366f1" radius={[2, 2, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}
