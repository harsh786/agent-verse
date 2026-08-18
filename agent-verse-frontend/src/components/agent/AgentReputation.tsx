/**
 * AgentReputation — displays an agent's performance reputation score with
 * animated ring, tier badge, and trend sparkline.
 *
 * Skills: frontend-design, emil-design-eng, impeccable-ui, web-guidelines
 */
import { motion, useReducedMotion } from 'framer-motion';
import { TrendingUp, TrendingDown, Minus, Star, Award, Zap } from 'lucide-react';
import { SPRING_SLOW } from '@/components/ui/JARVISPageShell';

export type ReputationTier = 'legendary' | 'expert' | 'proficient' | 'learning' | 'new';

interface AgentReputationProps {
  score: number;          // 0–100
  trend?: number;         // +/- change since last week
  tier?: ReputationTier;
  completedGoals?: number;
  successRate?: number;   // 0–1
  compact?: boolean;
  className?: string;
}

const TIER_CONFIG: Record<ReputationTier, { label: string; color: string; bg: string; icon: typeof Star }> = {
  legendary:  { label: 'Legendary',  color: '#FFB300', bg: 'rgba(255,179,0,0.15)',  icon: Award },
  expert:     { label: 'Expert',     color: '#00D4FF', bg: 'rgba(0,212,255,0.15)',  icon: Zap },
  proficient: { label: 'Proficient', color: '#6366F1', bg: 'rgba(99,102,241,0.15)', icon: Star },
  learning:   { label: 'Learning',   color: '#00E676', bg: 'rgba(0,230,118,0.15)',  icon: TrendingUp },
  new:        { label: 'New',        color: '#5A7494', bg: 'rgba(90,116,148,0.15)', icon: Minus },
};

function scoreTier(score: number): ReputationTier {
  if (score >= 90) return 'legendary';
  if (score >= 75) return 'expert';
  if (score >= 55) return 'proficient';
  if (score >= 30) return 'learning';
  return 'new';
}

const CIRCUMFERENCE = 2 * Math.PI * 28; // r=28

export function AgentReputation({
  score,
  trend,
  tier: tierProp,
  completedGoals,
  successRate,
  compact = false,
  className = '',
}: AgentReputationProps) {
  const reduce = useReducedMotion();
  const tier   = tierProp ?? scoreTier(score);
  const cfg    = TIER_CONFIG[tier];
  const TierIcon = cfg.icon;
  const dashOffset = CIRCUMFERENCE * (1 - score / 100);

  const TrendIcon = trend === undefined ? Minus : trend > 0 ? TrendingUp : trend < 0 ? TrendingDown : Minus;
  const trendColor = trend === undefined ? '#5A7494' : trend > 0 ? '#00E676' : trend < 0 ? '#FF3366' : '#5A7494';

  if (compact) {
    return (
      <div className={`flex items-center gap-2 ${className}`} aria-label={`Reputation: ${score}/100 — ${cfg.label}`}>
        <svg width="32" height="32" viewBox="0 0 64 64" className="shrink-0" aria-hidden="true">
          <circle cx="32" cy="32" r="28" fill="none" stroke="rgba(255,255,255,0.06)" strokeWidth="5" />
          <motion.circle
            cx="32" cy="32" r="28" fill="none"
            stroke={cfg.color} strokeWidth="5"
            strokeDasharray={CIRCUMFERENCE}
            strokeLinecap="round"
            transform="rotate(-90 32 32)"
            initial={{ strokeDashoffset: CIRCUMFERENCE }}
            animate={{ strokeDashoffset: reduce ? dashOffset : dashOffset }}
            transition={reduce ? { duration: 0 } : { ...SPRING_SLOW, delay: 0.2 }}
          />
        </svg>
        <span className="text-[13px] font-bold tabular-nums" style={{ color: cfg.color }}>{score}</span>
        <span className="text-[11px] px-1.5 py-0.5 rounded-full font-medium" style={{ color: cfg.color, background: cfg.bg }}>
          {cfg.label}
        </span>
      </div>
    );
  }

  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.95 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={reduce ? { duration: 0 } : SPRING_SLOW}
      className={`bg-[#0F1826] border border-white/[0.08] rounded-xl p-5 ${className}`}
      role="region"
      aria-label="Agent reputation"
    >
      <div className="flex items-center gap-5">
        {/* Score ring */}
        <div className="relative shrink-0">
          <svg width="80" height="80" viewBox="0 0 64 64" aria-hidden="true">
            <circle cx="32" cy="32" r="28" fill="none" stroke="rgba(255,255,255,0.06)" strokeWidth="5" />
            <motion.circle
              cx="32" cy="32" r="28" fill="none"
              stroke={cfg.color} strokeWidth="5"
              strokeDasharray={CIRCUMFERENCE}
              strokeLinecap="round"
              transform="rotate(-90 32 32)"
              initial={{ strokeDashoffset: CIRCUMFERENCE }}
              animate={{ strokeDashoffset: dashOffset }}
              transition={reduce ? { duration: 0 } : { ...SPRING_SLOW, delay: 0.3 }}
            />
          </svg>
          <div className="absolute inset-0 flex items-center justify-center">
            <span className="text-[18px] font-bold tabular-nums" style={{ color: cfg.color }} aria-label={`${score} out of 100`}>
              {score}
            </span>
          </div>
        </div>

        {/* Info */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1.5">
            <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-semibold" style={{ color: cfg.color, background: cfg.bg }}>
              <TierIcon size={11} aria-hidden />
              {cfg.label}
            </span>
            {trend !== undefined && (
              <span className="inline-flex items-center gap-0.5 text-[11px] tabular-nums font-medium" style={{ color: trendColor }}>
                <TrendIcon size={11} aria-hidden />
                {Math.abs(trend)}%
              </span>
            )}
          </div>

          {completedGoals !== undefined && (
            <p className="text-[12px] text-[#A0B4CC]">
              <span className="font-semibold text-[#F0F6FF] tabular-nums">{completedGoals}</span> goals completed
            </p>
          )}
          {successRate !== undefined && (
            <p className="text-[12px] text-[#A0B4CC] mt-0.5">
              <span className="font-semibold text-[#F0F6FF] tabular-nums">{Math.round(successRate * 100)}%</span> success rate
            </p>
          )}
        </div>
      </div>

      {/* Score bar */}
      <div className="mt-4 h-1 rounded-full bg-white/[0.06] overflow-hidden" role="progressbar" aria-valuenow={score} aria-valuemin={0} aria-valuemax={100}>
        <motion.div
          className="h-full rounded-full"
          style={{ background: `linear-gradient(90deg, ${cfg.color}88, ${cfg.color})` }}
          initial={{ width: 0 }}
          animate={{ width: `${score}%` }}
          transition={reduce ? { duration: 0 } : { ...SPRING_SLOW, delay: 0.4 }}
        />
      </div>
    </motion.div>
  );
}
