/**
 * CEO Dashboard — Strategy, Revenue, Risk, Org Health, Active Missions.
 * JARVIS spring motion throughout.
 */
import { TrendingUp, Target, DollarSign, AlertTriangle, Activity, Zap } from 'lucide-react';
import { JARVISStagger, JARVISStaggerItem } from '@/components/ui/JARVISPageShell';
import { useOrgHealth, useMissions } from '../hooks/useOrg';
import type { Organization } from '../types';

function KPI({ label, value, sub, icon: Icon, color = 'text-[#00D4FF]' }: {
  label: string; value: string | number; sub?: string;
  icon: React.ElementType; color?: string;
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

export function CEODashboard({ org }: { org: Organization }) {
  const { data: health }    = useOrgHealth(org.id);
  const { data: missionData } = useMissions(org.id, { status: 'active' });
  const activeMissions = missionData?.pages.flatMap(p => p.data) ?? [];

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-xl font-bold text-[#F1F5F9]">Executive Overview</h1>
        <p className="text-[12px] text-[#475569] mt-0.5">{org.name} · CEO view</p>
      </div>

      <JARVISStagger className="grid grid-cols-2 sm:grid-cols-3 gap-3">
        <JARVISStaggerItem><KPI icon={Activity}    label="Org Health"      value={(health as any)?.health_score_pct ?? '—'} sub="composite score"  color="text-emerald-400" /></JARVISStaggerItem>
        <JARVISStaggerItem><KPI icon={Zap}         label="Active Missions" value={(health as any)?.active_missions ?? 0}    sub="running now"     color="text-[#00D4FF]" /></JARVISStaggerItem>
        <JARVISStaggerItem><KPI icon={AlertTriangle} label="Pending Approvals" value={(health as any)?.pending_approvals ?? 0} sub="awaiting you" color="text-yellow-400" /></JARVISStaggerItem>
        <JARVISStaggerItem><KPI icon={TrendingUp}   label="Missions Completed" value="—"              sub="this month"       color="text-emerald-400" /></JARVISStaggerItem>
        <JARVISStaggerItem><KPI icon={DollarSign}   label="Budget Used"    value="—"                  sub="this month"       color="text-indigo-400" /></JARVISStaggerItem>
        <JARVISStaggerItem><KPI icon={Target}       label="Success Rate"   value="—"                  sub="30d avg"          color="text-[#00D4FF]" /></JARVISStaggerItem>
      </JARVISStagger>

      {/* Active missions */}
      {activeMissions.length > 0 && (
        <div className="bg-[#0F1623] border border-[#1E2535] rounded-xl p-4">
          <h2 className="text-xs font-medium text-[#475569] uppercase tracking-wider mb-3">Active Missions</h2>
          <div className="space-y-2">
            {activeMissions.slice(0, 5).map(m => (
              <div key={m.id} className="flex items-center gap-2">
                <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse flex-shrink-0" aria-hidden />
                <span className="text-sm text-[#94A3B8] truncate">{m.title}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
