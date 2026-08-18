/**
 * CFO Dashboard — Revenue, Expenses, Forecast, Cash, Risk.
 */
import { DollarSign, TrendingUp, TrendingDown, BarChart3, AlertTriangle, PieChart } from 'lucide-react';
import { JARVISStagger, JARVISStaggerItem } from '@/components/ui/JARVISPageShell';
import { useOrgHealth } from '../hooks/useOrg';
import type { Organization } from '../types';

function KPI({ label, value, sub, icon: Icon, color = 'text-[#00D4FF]' }: {
  label: string; value: string | number; sub?: string; icon: React.ElementType; color?: string;
}) {
  return (
    <div className="bg-[#0F1623] border border-[#1E2535] rounded-xl p-4">
      <div className="flex items-center justify-between mb-2">
        <span className="text-[11px] text-[#475569] uppercase tracking-wider">{label}</span>
        <Icon className={`h-4 w-4 ${color}`} aria-hidden />
      </div>
      <p className="text-2xl font-bold text-[#F1F5F9]">{value}</p>
      {sub && <p className="text-[11px] text-[#475569] mt-0.5">{sub}</p>}
    </div>
  );
}

export function CFODashboard({ org }: { org: Organization }) {
  const { data: health } = useOrgHealth(org.id);
  const budget    = org.monthly_budget_usd ?? 0;
  const budgetUsed = (health as any)?.budget_used_usd ?? 0;
  const budgetPct  = budget > 0 ? ((budgetUsed / budget) * 100).toFixed(1) : '—';

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-xl font-bold text-[#F1F5F9]">Finance Overview</h1>
        <p className="text-[12px] text-[#475569] mt-0.5">{org.name} · CFO view</p>
      </div>

      <JARVISStagger className="grid grid-cols-2 sm:grid-cols-3 gap-3">
        <JARVISStaggerItem><KPI icon={DollarSign}   label="Monthly Budget"    value={budget > 0 ? `$${budget.toLocaleString()}` : '—'}   sub="allocated"    color="text-emerald-400" /></JARVISStaggerItem>
        <JARVISStaggerItem><KPI icon={TrendingUp}   label="AI Compute Spend"  value={budgetUsed > 0 ? `$${budgetUsed.toFixed(2)}` : '—'} sub="this month"   color="text-[#00D4FF]" /></JARVISStaggerItem>
        <JARVISStaggerItem><KPI icon={PieChart}     label="Budget Used"       value={budgetPct !== '—' ? `${budgetPct}%` : '—'}           sub="of monthly"   color={parseFloat(budgetPct) > 80 ? 'text-yellow-400' : 'text-emerald-400'} /></JARVISStaggerItem>
        <JARVISStaggerItem><KPI icon={BarChart3}    label="Pending Approvals" value={(health as any)?.pending_approvals ?? 0}              sub="awaiting CFO" color="text-yellow-400" /></JARVISStaggerItem>
        <JARVISStaggerItem><KPI icon={AlertTriangle} label="Budget Alerts"    value="—"                                                   sub="active"       color="text-[#475569]" /></JARVISStaggerItem>
        <JARVISStaggerItem><KPI icon={TrendingDown}  label="Cost / Mission"  value="—"                                                   sub="30d avg"      color="text-indigo-400" /></JARVISStaggerItem>
      </JARVISStagger>

      {/* Budget bar */}
      {budget > 0 && (
        <div className="bg-[#0F1623] border border-[#1E2535] rounded-xl p-4">
          <div className="flex items-center justify-between text-xs text-[#475569] mb-2">
            <span>Monthly Budget Utilization</span>
            <span>${budgetUsed.toFixed(2)} / ${budget.toLocaleString()}</span>
          </div>
          <div className="h-2 rounded-full bg-[#1E2535] overflow-hidden">
            <div
              className={`h-full rounded-full transition-all ${
                parseFloat(budgetPct) > 80 ? 'bg-yellow-400' : 'bg-emerald-400'
              }`}
              style={{ width: `${Math.min(parseFloat(budgetPct) || 0, 100)}%` }}
              aria-label={`${budgetPct}% budget used`}
            />
          </div>
        </div>
      )}
    </div>
  );
}
