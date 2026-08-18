/**
 * CMO Dashboard — Campaigns, Growth, Content, Leads, Conversion.
 */
import { TrendingUp, Users, FileText, BarChart3, Megaphone, Target } from 'lucide-react';
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

export function CMODashboard({ org }: { org: Organization }) {
  const { data: health } = useOrgHealth(org.id);

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-xl font-bold text-[#F1F5F9]">Marketing Overview</h1>
        <p className="text-[12px] text-[#475569] mt-0.5">{org.name} · CMO view</p>
      </div>

      <JARVISStagger className="grid grid-cols-2 sm:grid-cols-3 gap-3">
        <JARVISStaggerItem><KPI icon={Megaphone}  label="Campaigns"       value={(health as any)?.active_missions ?? 0}   sub="active"          color="text-pink-400" /></JARVISStaggerItem>
        <JARVISStaggerItem><KPI icon={TrendingUp} label="Growth Rate"     value="—"                                       sub="month-over-month" color="text-emerald-400" /></JARVISStaggerItem>
        <JARVISStaggerItem><KPI icon={Users}      label="Leads Generated" value="—"                                       sub="this month"       color="text-[#00D4FF]" /></JARVISStaggerItem>
        <JARVISStaggerItem><KPI icon={Target}     label="Conversion"      value="—"                                       sub="lead → customer"  color="text-emerald-400" /></JARVISStaggerItem>
        <JARVISStaggerItem><KPI icon={FileText}   label="Content Pieces"  value="—"                                       sub="published"        color="text-indigo-400" /></JARVISStaggerItem>
        <JARVISStaggerItem><KPI icon={BarChart3}  label="CAC"             value="—"                                       sub="cost per acq."    color="text-yellow-400" /></JARVISStaggerItem>
      </JARVISStagger>
    </div>
  );
}
