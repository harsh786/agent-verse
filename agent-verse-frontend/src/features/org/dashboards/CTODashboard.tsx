/**
 * CTO Dashboard — Engineering, AI, Infrastructure, Security, Reliability.
 * JARVIS spring motion throughout.
 */
import { Code2, Cpu, Shield, Activity, GitBranch, Server } from 'lucide-react';
import { JARVISStagger, JARVISStaggerItem } from '@/components/ui/JARVISPageShell';
import { useOrgHealth, useMissions } from '../hooks/useOrg';
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

export function CTODashboard({ org }: { org: Organization }) {
  const { data: health }    = useOrgHealth(org.id);
  const { data: missionData } = useMissions(org.id, { status: 'active' });
  const activeMissions = (missionData?.pages.flatMap(p => p.data) ?? [])
    .filter(m => ['engineering', 'ai_ml', 'devops'].some(kw => m.title.toLowerCase().includes(kw)));

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-xl font-bold text-[#F1F5F9]">Technology Overview</h1>
        <p className="text-[12px] text-[#475569] mt-0.5">{org.name} · CTO view</p>
      </div>

      <JARVISStagger className="grid grid-cols-2 sm:grid-cols-3 gap-3">
        <JARVISStaggerItem><KPI icon={Code2}    label="Eng Missions"     value={(health as any)?.active_missions ?? 0}     sub="active"         color="text-emerald-400" /></JARVISStaggerItem>
        <JARVISStaggerItem><KPI icon={Cpu}      label="AI Agents Active" value={(health as any)?.active_teams ?? 0}        sub="running tasks"  color="text-[#00D4FF]" /></JARVISStaggerItem>
        <JARVISStaggerItem><KPI icon={Shield}   label="Security"         value="OK"                                        sub="no alerts"      color="text-emerald-400" /></JARVISStaggerItem>
        <JARVISStaggerItem><KPI icon={Server}   label="Infrastructure"   value="Healthy"                                   sub="all systems up" color="text-emerald-400" /></JARVISStaggerItem>
        <JARVISStaggerItem><KPI icon={Activity} label="Reliability"      value="99.9%"                                     sub="uptime (30d)"   color="text-[#00D4FF]" /></JARVISStaggerItem>
        <JARVISStaggerItem><KPI icon={GitBranch} label="Deployments"     value="—"                                         sub="this week"      color="text-indigo-400" /></JARVISStaggerItem>
      </JARVISStagger>

      {activeMissions.length > 0 && (
        <div className="bg-[#0F1623] border border-[#1E2535] rounded-xl p-4">
          <h2 className="text-xs font-medium text-[#475569] uppercase tracking-wider mb-3">Engineering Missions</h2>
          <div className="space-y-2">
            {activeMissions.slice(0, 5).map(m => (
              <div key={m.id} className="flex items-center gap-2">
                <span className="h-1.5 w-1.5 rounded-full bg-blue-400 animate-pulse flex-shrink-0" aria-hidden />
                <span className="text-sm text-[#94A3B8] truncate">{m.title}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
