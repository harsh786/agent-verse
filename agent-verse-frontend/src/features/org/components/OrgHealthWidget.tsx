/**
 * OrgHealthWidget — JARVIS-style health summary panel.
 * Shows active missions, teams, pending approvals, and overall health.
 * Uses Framer Motion spring stagger + cyan glow on active metrics.
 */
import { Activity, CheckCircle2, Clock, AlertTriangle } from 'lucide-react';
import { motion } from 'framer-motion';
import { JARVISStagger, JARVISStaggerItem } from '@/components/ui/JARVISPageShell';
import { useOrgHealth } from '../hooks/useOrg';

interface OrgHealthWidgetProps {
  orgId: string;
}

const SPRING = { type: 'spring', stiffness: 380, damping: 28 } as const;

export function OrgHealthWidget({ orgId }: OrgHealthWidgetProps) {
  const { data: health, isLoading, error } = useOrgHealth(orgId);

  if (isLoading) {
    return (
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4" aria-label="Loading health metrics">
        {[...Array(4)].map((_, i) => (
          <motion.div
            key={i}
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: [0.3, 0.6, 0.3], y: 0 }}
            transition={{ duration: 1.2, repeat: Infinity, delay: i * 0.12 }}
            className="h-20 rounded-xl bg-[#1A1F2E] border border-[#1E2535]"
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
    {
      label:   'Active Missions',
      value:   health.active_missions,
      icon:    Activity,
      color:   'text-[#00D4FF]',
      bgColor: 'bg-[#00D4FF]/10',
      glow:    health.active_missions > 0,
      glowColor: '0 0 20px rgba(0,212,255,0.25)',
    },
    {
      label:   'Active Teams',
      value:   health.active_teams,
      icon:    CheckCircle2,
      color:   'text-emerald-400',
      bgColor: 'bg-emerald-400/10',
      glow:    health.active_teams > 0,
      glowColor: '0 0 20px rgba(52,211,153,0.25)',
    },
    {
      label:   'Pending Approvals',
      value:   health.pending_approvals,
      icon:    Clock,
      color:   health.pending_approvals > 0 ? 'text-amber-400' : 'text-slate-400',
      bgColor: health.pending_approvals > 0 ? 'bg-amber-400/10' : 'bg-slate-400/10',
      glow:    health.pending_approvals > 0,
      glowColor: '0 0 20px rgba(251,191,36,0.25)',
    },
    {
      label:   'Need Attention',
      value:   health.items_needing_attention,
      icon:    AlertTriangle,
      color:   health.items_needing_attention > 0 ? 'text-rose-400' : 'text-emerald-400',
      bgColor: health.items_needing_attention > 0 ? 'bg-rose-400/10' : 'bg-emerald-400/10',
      glow:    health.items_needing_attention > 0,
      glowColor: '0 0 20px rgba(248,113,113,0.25)',
    },
  ];

  return (
    <JARVISStagger className="grid grid-cols-2 gap-3 sm:grid-cols-4" staggerMs={70} aria-label="Organization health metrics">
      {metrics.map(({ label, value, icon: Icon, color, bgColor, glow, glowColor }) => (
        <JARVISStaggerItem key={label} interactive>
          <motion.div
            className="rounded-xl border bg-[#1A1F2E] p-4 cursor-default"
            style={{
              borderColor: glow ? 'rgba(255,255,255,0.12)' : 'rgba(255,255,255,0.06)',
            }}
            animate={glow ? {
              boxShadow: [glowColor, glowColor.replace('0.25', '0.45'), glowColor],
            } : { boxShadow: 'none' }}
            transition={glow ? { duration: 2.4, repeat: Infinity, ease: 'easeInOut' } : {}}
            whileHover={{ scale: 1.02, transition: SPRING }}
          >
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs text-[#64748B]">{label}</span>
              <span className={`p-1.5 rounded-lg ${bgColor}`}>
                <Icon className={`h-3.5 w-3.5 ${color}`} aria-hidden="true" />
              </span>
            </div>
            <motion.p
              key={value}
              initial={{ scale: 0.8, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              transition={SPRING}
              className={`text-2xl font-bold tabular-nums ${color}`}
              aria-live="polite"
            >
              {value}
            </motion.p>
          </motion.div>
        </JARVISStaggerItem>
      ))}
    </JARVISStagger>
  );
}

