/**
 * SalesDashboard — Pipeline, Deals, Forecast, Accounts.
 */
import { TrendingUp, DollarSign, Users, Target, BarChart3, Zap } from 'lucide-react';
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

export function SalesDashboard({ org }: { org: Organization }) {
  const { data: health } = useOrgHealth(org.id);

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-xl font-bold text-[#F1F5F9]">Sales Overview</h1>
        <p className="text-[12px] text-[#475569] mt-0.5">{org.name} · Sales view</p>
      </div>

      <JARVISStagger className="grid grid-cols-2 sm:grid-cols-3 gap-3">
        <JARVISStaggerItem><KPI icon={DollarSign}  label="Pipeline Value"   value="—"                                    sub="qualified leads"  color="text-emerald-400" /></JARVISStaggerItem>
        <JARVISStaggerItem><KPI icon={Target}      label="Active Deals"     value="—"                                    sub="in progress"      color="text-[#00D4FF]" /></JARVISStaggerItem>
        <JARVISStaggerItem><KPI icon={TrendingUp}  label="Conversion Rate"  value="—"                                    sub="lead to close"    color="text-emerald-400" /></JARVISStaggerItem>
        <JARVISStaggerItem><KPI icon={BarChart3}   label="Revenue (MTD)"    value="—"                                    sub="month to date"    color="text-indigo-400" /></JARVISStaggerItem>
        <JARVISStaggerItem><KPI icon={Users}       label="Accounts"         value="—"                                    sub="total managed"    color="text-[#00D4FF]" /></JARVISStaggerItem>
        <JARVISStaggerItem><KPI icon={Zap}         label="Sales Missions"   value={(health as any)?.active_missions ?? 0} sub="running"         color="text-yellow-400" /></JARVISStaggerItem>
      </JARVISStagger>
    </div>
  );
}
