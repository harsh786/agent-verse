/**
 * OrgHealthWidget — JARVIS-style health summary panel.
 * Shows active missions, teams, pending approvals, and overall health.
 * Uses Framer Motion spring stagger + cyan glow on active metrics.
 */
import { Activity, CheckCircle2, Clock, AlertTriangle } from 'lucide-react';
import { motion } from 'framer-motion';
import { cn } from '@/lib/utils';
import { useOrgHealth } from '../hooks/useOrg';

interface OrgHealthWidgetProps {
  orgId: string;
}


export function OrgHealthWidget({ orgId }: OrgHealthWidgetProps) {
  const { data: health, isLoading, error } = useOrgHealth(orgId);

  if (isLoading) {
    return (
      <div className="flex items-center gap-6 py-1" aria-label="Loading health metrics">
        {[...Array(4)].map((_, i) => (
          <motion.div
            key={i}
            animate={{ opacity: [0.3, 0.6, 0.3] }}
            transition={{ duration: 1.2, repeat: Infinity, delay: i * 0.12 }}
            className="h-6 w-24 rounded-md bg-[#1A1F2E]"
          />
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
    { label: 'Missions',  value: health.active_missions,          icon: Activity,      color: 'text-[#00D4FF]',   glowColor: '0 0 16px rgba(0,212,255,0.4)' },
    { label: 'Teams',     value: health.active_teams,             icon: CheckCircle2,  color: 'text-emerald-400', glowColor: '0 0 16px rgba(52,211,153,0.4)' },
    { label: 'Approvals', value: health.pending_approvals,        icon: Clock,         color: 'text-amber-400',   glowColor: '0 0 16px rgba(251,191,36,0.4)' },
    { label: 'Attention', value: health.items_needing_attention,  icon: AlertTriangle, color: 'text-rose-400',    glowColor: '0 0 16px rgba(248,113,113,0.4)' },
  ];

  // A living command bar: one quiet line, with glow reserved for the metrics that
  // are actually alive (non-zero). Zeros stay muted rather than filling the page
  // with big empty boxes.
  return (
    <div
      className="flex flex-wrap items-center gap-x-6 gap-y-2"
      aria-label="Organization health metrics"
    >
      {metrics.map(({ label, value, icon: Icon, color, glowColor }) => {
        const active = value > 0;
        return (
          <div key={label} className="flex items-center gap-2" aria-label={`${label}: ${value}`}>
            <motion.span
              className={cn(
                'flex h-6 w-6 items-center justify-center rounded-lg border',
                active ? 'border-white/10 bg-[#151B29]' : 'border-transparent bg-[#141824]',
              )}
              animate={active ? { boxShadow: [glowColor, glowColor.replace('0.4', '0.7'), glowColor] } : {}}
              transition={active ? { duration: 2.6, repeat: Infinity, ease: 'easeInOut' } : {}}
            >
              <Icon className={cn('h-3.5 w-3.5', active ? color : 'text-[#475569]')} aria-hidden />
            </motion.span>
            <span
              key={value}
              className={cn('jarvis-pop-in text-[17px] font-semibold tabular-nums leading-none', active ? color : 'text-[#94A3B8]')}
              aria-live="polite"
            >
              {value}
            </span>
            <span className="text-[12px] text-[#64748B] leading-none">{label}</span>
          </div>
        );
      })}
    </div>
  );
}

