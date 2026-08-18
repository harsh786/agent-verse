/**
 * HRODashboard — Hiring, People, Performance, Workforce.
 */
import { Users, UserPlus, TrendingUp, Heart, Award, Target } from 'lucide-react';
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

export function HRODashboard({ org }: { org: Organization }) {
  const { data: health } = useOrgHealth(org.id);

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-xl font-bold text-[#F1F5F9]">People Overview</h1>
        <p className="text-[12px] text-[#475569] mt-0.5">{org.name} · CHRO view</p>
      </div>

      <JARVISStagger className="grid grid-cols-2 sm:grid-cols-3 gap-3">
        <JARVISStaggerItem><KPI icon={Users}    label="Total Agents"       value={(health as any)?.active_teams ?? 0}  sub="active"              color="text-purple-400" /></JARVISStaggerItem>
        <JARVISStaggerItem><KPI icon={UserPlus} label="Hiring"             value="—"                                   sub="open positions"      color="text-[#00D4FF]" /></JARVISStaggerItem>
        <JARVISStaggerItem><KPI icon={Award}    label="Avg Reputation"     value="—"                                   sub="across all agents"   color="text-emerald-400" /></JARVISStaggerItem>
        <JARVISStaggerItem><KPI icon={TrendingUp} label="Performance Trend" value="—"                                  sub="30d improvement"     color="text-emerald-400" /></JARVISStaggerItem>
        <JARVISStaggerItem><KPI icon={Heart}    label="Culture Score"      value="—"                                   sub="collaboration index" color="text-pink-400" /></JARVISStaggerItem>
        <JARVISStaggerItem><KPI icon={Target}   label="Training Goals"     value="—"                                   sub="completed this month" color="text-indigo-400" /></JARVISStaggerItem>
      </JARVISStagger>
    </div>
  );
}
