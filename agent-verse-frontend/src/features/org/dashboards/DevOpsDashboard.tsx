/**
 * DevOpsDashboard — Deployments, Incidents, Infrastructure, SLOs.
 */
import { Server, AlertTriangle, Activity, GitBranch, Clock, CheckCircle2 } from 'lucide-react';
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

export function DevOpsDashboard({ org }: { org: Organization }) {
  useOrgHealth(org.id);

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-xl font-bold text-[#F1F5F9]">DevOps Overview</h1>
        <p className="text-[12px] text-[#475569] mt-0.5">{org.name} · DevOps / SRE view</p>
      </div>

      <JARVISStagger className="grid grid-cols-2 sm:grid-cols-3 gap-3">
        <JARVISStaggerItem><KPI icon={Server}        label="Infra Health"     value="Healthy"                               sub="all systems up"    color="text-emerald-400" /></JARVISStaggerItem>
        <JARVISStaggerItem><KPI icon={AlertTriangle} label="Open Incidents"   value="0"                                     sub="current"           color="text-[#475569]" /></JARVISStaggerItem>
        <JARVISStaggerItem><KPI icon={Activity}      label="Uptime (30d)"     value="99.9%"                                 sub="SLO target: 99.9%" color="text-emerald-400" /></JARVISStaggerItem>
        <JARVISStaggerItem><KPI icon={GitBranch}     label="Deployments"      value="—"                                     sub="this week"         color="text-[#00D4FF]" /></JARVISStaggerItem>
        <JARVISStaggerItem><KPI icon={Clock}         label="MTTR"             value="—"                                     sub="mean time to recover" color="text-indigo-400" /></JARVISStaggerItem>
        <JARVISStaggerItem><KPI icon={CheckCircle2}  label="SLO Compliance"   value="—"                                     sub="last 30 days"      color="text-emerald-400" /></JARVISStaggerItem>
      </JARVISStagger>

      <div className="bg-[#0F1623] border border-[#1E2535] rounded-xl p-4">
        <h2 className="text-xs font-medium text-[#475569] uppercase tracking-wider mb-3">Service Status</h2>
        {['API Gateway', 'Agent Runtime', 'Knowledge Store', 'Redis Cache', 'Celery Workers'].map(svc => (
          <div key={svc} className="flex items-center justify-between py-2 border-b border-[#1E2535] last:border-0">
            <span className="text-sm text-[#94A3B8]">{svc}</span>
            <div className="flex items-center gap-1.5">
              <span className="h-2 w-2 rounded-full bg-emerald-400" aria-hidden />
              <span className="text-[11px] text-emerald-400">Operational</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
