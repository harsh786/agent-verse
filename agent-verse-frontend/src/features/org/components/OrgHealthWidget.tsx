/**
 * OrgHealthWidget — JARVIS-style health summary panel.
 * Shows active missions, teams, pending approvals, and overall health.
 */
import { Activity, CheckCircle2, Clock, AlertTriangle } from 'lucide-react';
import { useOrgHealth } from '../hooks/useOrg';

interface OrgHealthWidgetProps {
  orgId: string;
}

export function OrgHealthWidget({ orgId }: OrgHealthWidgetProps) {
  const { data: health, isLoading, error } = useOrgHealth(orgId);

  if (isLoading) {
    return (
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4" aria-label="Loading health metrics">
        {[...Array(4)].map((_, i) => (
          <div key={i} className="h-20 rounded-lg bg-[var(--bg-elevated)] animate-pulse" />
        ))}
      </div>
    );
  }

  if (error || !health) {
    return (
      <div className="flex items-center gap-2 text-sm text-[var(--text-muted)] py-4" role="alert">
        <AlertTriangle className="h-4 w-4 text-amber-400" aria-hidden="true" />
        Health data unavailable
      </div>
    );
  }

  const metrics = [
    {
      label:   'Active Missions',
      value:   health.active_missions,
      icon:    Activity,
      color:   'text-blue-400',
      bgColor: 'bg-blue-400/10',
    },
    {
      label:   'Active Teams',
      value:   health.active_teams,
      icon:    CheckCircle2,
      color:   'text-emerald-400',
      bgColor: 'bg-emerald-400/10',
    },
    {
      label:   'Pending Approvals',
      value:   health.pending_approvals,
      icon:    Clock,
      color:   health.pending_approvals > 0 ? 'text-amber-400' : 'text-slate-400',
      bgColor: health.pending_approvals > 0 ? 'bg-amber-400/10' : 'bg-slate-400/10',
    },
    {
      label:   'Need Attention',
      value:   health.items_needing_attention,
      icon:    AlertTriangle,
      color:   health.items_needing_attention > 0 ? 'text-rose-400' : 'text-emerald-400',
      bgColor: health.items_needing_attention > 0 ? 'bg-rose-400/10' : 'bg-emerald-400/10',
    },
  ];

  return (
    <div
      className="grid grid-cols-2 gap-3 sm:grid-cols-4"
      aria-label="Organization health metrics"
    >
      {metrics.map(({ label, value, icon: Icon, color, bgColor }) => (
        <div
          key={label}
          className="rounded-lg border border-[var(--border)] bg-[var(--bg-secondary)] p-4"
        >
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs text-[var(--text-muted)]">{label}</span>
            <span className={`p-1.5 rounded-md ${bgColor}`}>
              <Icon className={`h-3.5 w-3.5 ${color}`} aria-hidden="true" />
            </span>
          </div>
          <p className={`text-2xl font-bold ${color}`} aria-live="polite">
            {value}
          </p>
        </div>
      ))}
    </div>
  );
}
