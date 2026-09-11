/**
 * AutonomyControl — org-wide L0–L5 autonomy slider.
 *
 * JARVIS motion system:
 *   - SPRING_PANEL: level description slides in
 *   - SPRING_FAST:  indicator transitions
 *   - layout: smooth bar resize
 */
import { useState, useCallback } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Shield, Eye, ClipboardCheck, Zap, Cpu, Rocket,
  AlertTriangle, CheckCircle2, Info,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import {
  JARVISPageShell, SPRING_PANEL, SPRING_FAST,
} from '@/components/ui/JARVISPageShell';
import { useUpdateOrganization } from './hooks/useOrg';
import type { Organization } from './types';

// ── Level definitions ──────────────────────────────────────────────────────

const LEVELS = [
  {
    level: 0,
    label: 'L0 — Observe',
    icon: Eye,
    color: 'text-[#475569]',
    bar: 'bg-[#475569]',
    border: 'border-[#475569]/30',
    bg: 'bg-[#475569]/5',
    description: 'Shadow mode. Produces observation reports only.',
    consequence: 'Human approval required for EVERYTHING.',
    use_when: 'First deploy of sensitive depts (Legal, Finance, Security)',
    blocked: ['all actions, write operations, read operations requiring decision'],
  },
  {
    level: 1,
    label: 'L1 — Recommend',
    icon: ClipboardCheck,
    color: 'text-yellow-500',
    bar: 'bg-yellow-500',
    border: 'border-yellow-500/30',
    bg: 'bg-yellow-500/5',
    description: 'Analyzes and proposes actions. Never executes.',
    consequence: 'Human approval required before any action executes.',
    use_when: 'Auditable contexts, compliance-heavy operations',
    blocked: ['execution of any action'],
  },
  {
    level: 2,
    label: 'L2 — Supervised',
    icon: Shield,
    color: 'text-amber-400',
    bar: 'bg-amber-400',
    border: 'border-amber-400/30',
    bg: 'bg-amber-400/5',
    description: 'Executes read-only and draft creation. Blocks all writes.',
    consequence: 'Human approval required for any write operation.',
    use_when: 'Research, analysis, content drafting departments',
    blocked: ['write actions', 'email send', 'external publish'],
  },
  {
    level: 3,
    label: 'L3 — Standard',
    icon: Zap,
    color: 'text-emerald-400',
    bar: 'bg-emerald-400',
    border: 'border-emerald-400/30',
    bg: 'bg-emerald-400/5',
    description: 'Runs autonomously with configurable approval gates.',
    consequence: 'Approval gates on: spend > $X, external API writes, code deploy.',
    use_when: 'Standard operating mode for most departments',
    blocked: ['financial transfers >$10k', 'legal agreements', 'prod infra changes'],
  },
  {
    level: 4,
    label: 'L4 — Autonomous',
    icon: Cpu,
    color: 'text-blue-400',
    bar: 'bg-blue-400',
    border: 'border-blue-400/30',
    bg: 'bg-blue-400/5',
    description: 'Highly autonomous within policy. Minimal approval gates.',
    consequence: 'Approval only for constitutional violations + budget caps.',
    use_when: 'Mature departments with strong reputation scores',
    blocked: ['financial >$10k', 'legal binding agreements', 'PII bulk ops', 'prod infra'],
  },
  {
    level: 5,
    label: 'L5 — Mission Autonomous',
    icon: Rocket,
    color: 'text-[#00D4FF]',
    bar: 'bg-[#00D4FF]',
    border: 'border-[#00D4FF]/30',
    bg: 'bg-[#00D4FF]/5',
    description: 'Organization operates end-to-end on submitted goal.',
    consequence: 'Human receives: status updates, completion notice, "while you were away".',
    use_when: 'Highly trusted orgs on well-bounded missions',
    blocked: ['production infra destruction', 'mass customer data delete', 'financial >$10k', 'legal agreements', 'press releases'],
  },
];

// ── Props ──────────────────────────────────────────────────────────────────

interface AutonomyControlProps {
  org: Organization;
  onClose?: () => void;
  className?: string;
}

export function AutonomyControl({ org, className }: AutonomyControlProps) {
  const updateOrg = useUpdateOrganization(org.id);

  const [pending, setPending] = useState<number | null>(null);
  const [saving,  setSaving]  = useState(false);
  const [saved,   setSaved]   = useState(false);

  const current = org.autonomy_level ?? 3;
  const preview = pending ?? current;
  const levelConf = LEVELS[preview] ?? LEVELS[3];
  const LevelIcon = levelConf.icon;

  const handleSelect = useCallback((level: number) => {
    if (level !== current) setPending(level);
    else setPending(null);
  }, [current]);

  const handleSave = useCallback(async () => {
    if (pending === null || pending === current) return;
    setSaving(true);
    try {
      await updateOrg.mutateAsync({ autonomy_level: pending });
      setSaved(true);
      setPending(null);
      setTimeout(() => setSaved(false), 2000);
    } finally {
      setSaving(false);
    }
  }, [pending, current, updateOrg]);

  return (
    <JARVISPageShell className={cn('flex flex-col gap-5', className)}>
      {/* ── Header ── */}
      <div>
        <h2 className="text-base font-semibold text-[#F1F5F9] flex items-center gap-2">
          <Shield className="h-4 w-4 text-[#00D4FF]" aria-hidden />
          Autonomy Level
        </h2>
        <p className="text-[11px] text-[#475569] mt-0.5">
          Controls how much the organization acts independently. Hard limits always apply.
        </p>
      </div>

      {/* ── Level selector ── */}
      <div
        className="grid grid-cols-3 sm:grid-cols-6 gap-2"
        role="radiogroup"
        aria-label="Autonomy level"
      >
        {LEVELS.map(l => {
          const Icon    = l.icon;
          const active  = preview === l.level;
          const isCurrent = current === l.level;
          return (
            <button
              key={l.level}
              role="radio"
              aria-checked={active}
              onClick={() => handleSelect(l.level)}
              style={{ touchAction: 'manipulation' }}
              className={cn(
                'flex flex-col items-center gap-1.5 px-2 py-3 rounded-xl border transition-all',
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00D4FF]/50',
                'min-h-[44px]',
                active
                  ? `${l.bg} ${l.border} ${l.color}`
                  : 'border-[#1E2535] text-[#475569] hover:border-[#2E3545] hover:text-[#94A3B8]',
              )}
            >
              <Icon className="h-4 w-4" aria-hidden />
              <span className="text-[10px] font-bold">{`L${l.level}`}</span>
              {isCurrent && !active && (
                <span className="text-[8px] text-[#475569]">current</span>
              )}
            </button>
          );
        })}
      </div>

      {/* ── Level description ── */}
      <AnimatePresence mode="wait">
        <motion.div
          key={preview}
          initial={false}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0 }}
          transition={SPRING_FAST}
          className={cn('rounded-xl border p-4', levelConf.bg, levelConf.border)}
        >
          <div className="flex items-center gap-2 mb-2">
            <LevelIcon className={cn('h-4 w-4', levelConf.color)} aria-hidden />
            <span className={cn('text-sm font-semibold', levelConf.color)}>{levelConf.label}</span>
          </div>
          <p className="text-sm text-[#94A3B8] mb-2">{levelConf.description}</p>
          <p className="text-xs text-[#475569] mb-3">{levelConf.consequence}</p>

          {/* Hard limits */}
          <div className="space-y-1">
            <p className="text-[10px] text-[#475569] uppercase tracking-wider">Always blocked (hard limits):</p>
            {levelConf.blocked.slice(0, 3).map(b => (
              <div key={b} className="flex items-center gap-1.5">
                <AlertTriangle className="h-3 w-3 text-[#475569] flex-shrink-0" aria-hidden />
                <span className="text-[11px] text-[#475569] capitalize">{b}</span>
              </div>
            ))}
          </div>
        </motion.div>
      </AnimatePresence>

      {/* ── Progress bar ── */}
      <div aria-hidden>
        <div className="flex items-center justify-between text-[10px] text-[#475569] mb-1.5">
          <span>Observe</span><span>Full Autonomous</span>
        </div>
        <div className="h-1.5 rounded-full bg-[#1E2535] overflow-hidden">
          <motion.div
            className={cn('h-full rounded-full', levelConf.bar)}
            layout
            animate={{ width: `${(preview / 5) * 100}%` }}
            transition={SPRING_PANEL}
          />
        </div>
      </div>

      {/* ── Save button ── */}
      {pending !== null && pending !== current && (
        <div className="jarvis-rise-in">
          <button
            onClick={handleSave}
            disabled={saving}
            style={{ touchAction: 'manipulation' }}
            className={cn(
              'w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl text-sm font-semibold transition-all',
              'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00D4FF]/50',
              'min-h-[44px]',
              saved
                ? 'bg-emerald-500 text-white'
                : 'bg-[#00D4FF] text-[#0A0D14] hover:bg-[#00D4FF]/90',
            )}
          >
            {saved ? (
              <><CheckCircle2 className="h-4 w-4" aria-hidden /> Saved</>
            ) : (
              <>Set to {LEVELS[pending]?.label}</>
            )}
          </button>
        </div>
      )}

      {/* ── Info footer ── */}
      <div className="flex items-start gap-2 p-3 rounded-lg bg-[#0F1623] border border-[#1E2535]">
        <Info className="h-3.5 w-3.5 text-[#475569] flex-shrink-0 mt-0.5" aria-hidden />
        <p className="text-[11px] text-[#475569] leading-relaxed">
          Autonomy level can be overridden per department, team, or agent. 
          This sets the organization default. Hard limits apply at all levels.
        </p>
      </div>
    </JARVISPageShell>
  );
}

export default AutonomyControl;
