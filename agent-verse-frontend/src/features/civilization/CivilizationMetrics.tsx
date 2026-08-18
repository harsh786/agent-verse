/**
 * CivilizationMetrics — world-class KPI dashboard for the society.
 */
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell } from 'recharts';
import { Users, TrendingUp, DollarSign, Moon } from 'lucide-react';
import type { CivilizationMetrics as MetricsType } from '../../lib/api/civilizationApi';

interface Props {
  metrics: MetricsType;
  spawnHistory?: { ts: string; spawns: number }[];
}

const DARK_TOOLTIP_STYLE = {
  background: 'rgba(15,23,42,0.95)',
  border: '1px solid rgba(255,255,255,0.1)',
  borderRadius: '8px',
  color: '#e2e8f0',
  fontSize: '11px',
};

export function CivilizationMetrics({ metrics, spawnHistory = [] }: Props) {
  const repPct = Math.round((metrics.avg_reputation ?? 0) * 100);
  const minRepPct = Math.round((metrics.min_reputation ?? 0) * 100);
  const maxRepPct = Math.round((metrics.max_reputation ?? 0) * 100);

  const memberBreakdown = [
    { name: 'Active', value: metrics.active_members ?? 0, color: '#3b82f6' },
    { name: 'Idle', value: metrics.idle_members ?? 0, color: '#64748b' },
    { name: 'Retired', value: metrics.retired_members ?? 0, color: '#374151' },
  ].filter(d => d.value > 0);

  const KPIs = [
    {
      icon: Users,
      label: 'Active',
      value: metrics.active_members ?? 0,
      color: 'text-blue-400',
      bg: 'rgba(59,130,246,0.1)',
      border: 'rgba(59,130,246,0.2)',
    },
    {
      icon: TrendingUp,
      label: 'Avg Rep',
      value: `${repPct}%`,
      color: repPct > 60 ? 'text-green-400' : repPct > 30 ? 'text-amber-400' : 'text-red-400',
      bg: 'rgba(34,197,94,0.08)',
      border: 'rgba(34,197,94,0.15)',
    },
    {
      icon: Users,
      label: 'Total',
      value: metrics.total_members ?? 0,
      color: 'text-violet-400',
      bg: 'rgba(139,92,246,0.1)',
      border: 'rgba(139,92,246,0.2)',
    },
    {
      icon: DollarSign,
      label: 'Spent',
      value: `$${(metrics.total_budget_spent_usd ?? 0).toFixed(2)}`,
      color: 'text-amber-400',
      bg: 'rgba(245,158,11,0.08)',
      border: 'rgba(245,158,11,0.15)',
    },
  ];

  return (
    <div className="space-y-4">
      {/* KPI grid */}
      <div className="grid grid-cols-2 gap-2">
        {KPIs.map(kpi => {
          const Icon = kpi.icon;
          return (
            <div
              key={kpi.label}
              className="rounded-xl p-3"
              style={{ background: kpi.bg, border: `1px solid ${kpi.border}` }}
            >
              <div className="flex items-center gap-2 mb-1">
                <Icon className={`h-3.5 w-3.5 ${kpi.color}`} />
                <span className="text-[10px] text-[#5A7494]">{kpi.label}</span>
              </div>
              <div className={`text-xl font-bold tabular-nums ${kpi.color}`}>{kpi.value}</div>
            </div>
          );
        })}
      </div>

      {/* Idle / Retired */}
      {((metrics.idle_members ?? 0) > 0 || (metrics.retired_members ?? 0) > 0) && (
        <div className="grid grid-cols-2 gap-2">
          <div
            className="rounded-xl p-3"
            style={{ background: 'rgba(100,116,139,0.08)', border: '1px solid rgba(100,116,139,0.15)' }}
          >
            <div className="flex items-center gap-2 mb-1">
              <Moon className="h-3.5 w-3.5 text-[#5A7494]" />
              <span className="text-[10px] text-[#5A7494]">Idle</span>
            </div>
            <div className="text-xl font-bold text-slate-400 tabular-nums">
              {metrics.idle_members ?? 0}
            </div>
          </div>
          <div
            className="rounded-xl p-3"
            style={{ background: 'rgba(55,65,81,0.15)', border: '1px solid rgba(55,65,81,0.25)' }}
          >
            <div className="flex items-center gap-2 mb-1">
              <span className="text-slate-600 text-xs">✕</span>
              <span className="text-[10px] text-[#5A7494]">Retired</span>
            </div>
            <div className="text-xl font-bold text-slate-600 tabular-nums">
              {metrics.retired_members ?? 0}
            </div>
          </div>
        </div>
      )}

      {/* Reputation range */}
      <div
        className="rounded-xl p-3 space-y-2"
        style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.06)' }}
      >
        <p className="text-[10px] font-semibold uppercase tracking-wider text-[#5A7494]">Reputation Range</p>
        <div className="relative h-3 bg-[#0F1826]/5 rounded-full overflow-hidden">
          {/* Range bar */}
          <div
            className="absolute h-full rounded-full"
            style={{
              left: `${minRepPct}%`,
              width: `${maxRepPct - minRepPct}%`,
              background: 'linear-gradient(90deg, #ef4444, #22c55e)',
            }}
          />
          {/* Avg marker */}
          <div
            className="absolute top-0 bottom-0 w-0.5 bg-[#0F1826] rounded"
            style={{ left: `${repPct}%`, boxShadow: '0 0 6px rgba(255,255,255,0.6)' }}
          />
        </div>
        <div className="flex items-center justify-between text-[10px] text-[#5A7494]">
          <span className="text-red-400">Min {minRepPct}%</span>
          <span className="text-white font-semibold">Avg {repPct}%</span>
          <span className="text-green-400">Max {maxRepPct}%</span>
        </div>
      </div>

      {/* Member composition donut-style bar */}
      {memberBreakdown.length > 1 && (
        <div
          className="rounded-xl p-3 space-y-2"
          style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.06)' }}
        >
          <p className="text-[10px] font-semibold uppercase tracking-wider text-[#5A7494]">Composition</p>
          <div className="flex h-2 rounded-full overflow-hidden gap-px">
            {memberBreakdown.map(d => (
              <div
                key={d.name}
                style={{
                  flex: d.value,
                  background: d.color,
                  transition: 'flex 0.5s ease',
                }}
              />
            ))}
          </div>
          <div className="flex items-center gap-3">
            {memberBreakdown.map(d => (
              <div key={d.name} className="flex items-center gap-1 text-[10px] text-slate-400">
                <span className="w-2 h-2 rounded-full" style={{ background: d.color }} />
                {d.name} ({d.value})
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Spawn history chart */}
      {spawnHistory.length > 1 && (
        <div
          className="rounded-xl p-3 space-y-2"
          style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.06)' }}
        >
          <p className="text-[10px] font-semibold uppercase tracking-wider text-[#5A7494]">Spawn Rate</p>
          <div className="h-20">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={spawnHistory} margin={{ top: 2, right: 2, left: 2, bottom: 2 }}>
                <XAxis dataKey="ts" hide />
                <YAxis hide />
                <Tooltip
                  contentStyle={DARK_TOOLTIP_STYLE}
                  cursor={{ fill: 'rgba(255,255,255,0.05)' }}
                />
                <Bar dataKey="spawns" radius={[3, 3, 0, 0]}>
                  {spawnHistory.map((_, i) => (
                    <Cell key={i} fill={`hsl(${250 + i * 5}, 70%, 60%)`} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}
    </div>
  );
}
