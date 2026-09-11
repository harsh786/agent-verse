/**
 * AutonomyControl — slider to set an agent's autonomy level (0–4).
 * Levels: Supervised → Guided → Balanced → Proactive → Autonomous
 *
 * Skills: frontend-design, emil-design-eng, impeccable-ui, web-guidelines
 */
import { useId } from 'react';
import { motion, useReducedMotion } from 'framer-motion';
import { Shield, Zap, Eye, Brain, Cpu } from 'lucide-react';
import { SPRING_FAST } from '@/components/ui/JARVISPageShell';

export type AutonomyLevel = 0 | 1 | 2 | 3 | 4;

interface AutonomyControlProps {
  value: AutonomyLevel;
  onChange: (level: AutonomyLevel) => void;
  disabled?: boolean;
  className?: string;
}

const LEVELS = [
  { label: 'Supervised',  desc: 'Human approves every action', color: '#00E676', icon: Eye,    risk: 'low' },
  { label: 'Guided',      desc: 'Human approves high-risk actions', color: '#00D4FF', icon: Shield, risk: 'low' },
  { label: 'Balanced',    desc: 'Auto-execute routine tasks only', color: '#6366F1', icon: Brain,  risk: 'medium' },
  { label: 'Proactive',   desc: 'Execute freely, log for review', color: '#FFB300', icon: Zap,    risk: 'medium' },
  { label: 'Autonomous',  desc: 'Full self-direction, no approvals', color: '#FF3366', icon: Cpu,    risk: 'high' },
] as const;

export function AutonomyControl({ value, onChange, disabled = false, className = '' }: AutonomyControlProps) {
  const reduce = useReducedMotion();
  const id = useId();
  const current = LEVELS[value];
  const CurrentIcon = current.icon;

  return (
    <div className={`bg-[#0F1826] border border-white/[0.08] rounded-xl p-5 ${className}`} role="group" aria-labelledby={`${id}-label`}>
      <div className="flex items-center justify-between mb-4">
        <div>
          <p id={`${id}-label`} className="text-[13px] font-semibold text-[#F0F6FF]">Autonomy Level</p>
          <p className="text-[11px] text-[#5A7494] mt-0.5">{current.desc}</p>
        </div>
        <div
          key={value}
          className="jarvis-pop-in flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-semibold"
          style={{ color: current.color, background: `${current.color}22` }}
          aria-live="polite"
        >
          <CurrentIcon size={11} aria-hidden />
          {current.label}
        </div>
      </div>

      {/* Track + steps */}
      <div className="relative">
        {/* Background track */}
        <div className="h-2 rounded-full bg-white/[0.06] overflow-hidden">
          <motion.div
            className="h-full rounded-full"
            style={{ background: `linear-gradient(90deg, #00E676, ${current.color})` }}
            animate={{ width: `${(value / 4) * 100}%` }}
            transition={reduce ? { duration: 0 } : SPRING_FAST}
            aria-hidden
          />
        </div>

        {/* Step buttons */}
        <div className="flex justify-between mt-2 -mx-1">
          {LEVELS.map((level, i) => {
            const Icon = level.icon;
            const isActive = i <= value;
            const isCurrent = i === value;
            return (
              <motion.button
                key={i}
                whileTap={reduce ? {} : { scale: 0.90 }}
                transition={SPRING_FAST}
                onClick={() => !disabled && onChange(i as AutonomyLevel)}
                disabled={disabled}
                aria-label={`Set autonomy to ${level.label}: ${level.desc}`}
                aria-pressed={isCurrent}
                style={{ touchAction: 'manipulation' }}
                className={[
                  'flex flex-col items-center gap-1 px-1 py-1 rounded-lg min-w-[44px] min-h-[44px] justify-center',
                  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00D4FF]/60',
                  disabled ? 'cursor-not-allowed opacity-40' : 'cursor-pointer',
                  isCurrent ? 'bg-white/[0.08]' : 'hover:bg-white/[0.04]',
                ].join(' ')}
              >
                <motion.div
                  animate={{ color: isActive ? level.color : '#5A7494' }}
                  transition={SPRING_FAST}
                >
                  <Icon size={14} aria-hidden />
                </motion.div>
                <span className={`text-[9px] font-medium hidden sm:block ${isCurrent ? 'text-[#F0F6FF]' : 'text-[#5A7494]'}`}>
                  {level.label.slice(0, 4)}
                </span>
              </motion.button>
            );
          })}
        </div>
      </div>

      {/* Risk warning for high autonomy */}
      {value >= 3 && (
        <p
          className="jarvis-rise-in mt-3 text-[11px] px-3 py-2 rounded-lg"
          style={{
            color: value === 4 ? '#FF3366' : '#FFB300',
            background: value === 4 ? 'rgba(255,51,102,0.10)' : 'rgba(255,179,0,0.10)',
          }}
          role="alert"
        >
          {value === 4
            ? '⚠ Fully autonomous — agent will execute without any approvals'
            : '⚡ Proactive mode — monitor logs regularly for unintended actions'}
        </p>
      )}
    </div>
  );
}
