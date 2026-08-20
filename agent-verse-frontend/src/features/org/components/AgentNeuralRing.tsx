/**
 * AgentNeuralRing — Agent identity card with reputation arc.
 * Spec §7.2: Reputation ring (0-100), status glow, breathing animation.
 */
import { useReducedMotion, motion } from 'framer-motion';
import { Bot, Cpu, Zap } from 'lucide-react';
import { cn } from '@/lib/utils';

interface AgentNeuralRingProps {
  agentId:         string;
  name:            string;
  role?:           string;
  status:          'active' | 'idle' | 'error' | 'completed';
  reputationScore: number;  // 0-100
  className?:      string;
  size?:           'sm' | 'md' | 'lg';
}

const SIZE_CFG = {
  sm: { ring: 48, stroke: 3, icon: 16, labelSize: '10px' },
  md: { ring: 64, stroke: 4, icon: 20, labelSize: '11px' },
  lg: { ring: 80, stroke: 5, icon: 24, labelSize: '12px' },
};

function repColor(score: number): string {
  if (score >= 90) return '#00E676';
  if (score >= 70) return '#00D4FF';
  if (score >= 50) return '#FFB300';
  return '#FF3366';
}

function glowColor(status: AgentNeuralRingProps['status']): string {
  switch (status) {
    case 'active':    return '0 0 16px rgba(0,212,255,0.40)';
    case 'error':     return '0 0 16px rgba(255,51,102,0.40)';
    case 'completed': return '0 0 12px rgba(0,230,118,0.30)';
    default:          return '0 0 4px rgba(255,255,255,0.06)';
  }
}

export function AgentNeuralRing({
  name, role, status, reputationScore, className, size = 'md',
}: AgentNeuralRingProps) {
  const reduce = useReducedMotion();
  const cfg    = SIZE_CFG[size];
  const r      = (cfg.ring - cfg.stroke * 2) / 2;
  const circ   = 2 * Math.PI * r;
  const arc    = circ * (reputationScore / 100);
  const rc     = repColor(reputationScore);

  const Icon = role?.toLowerCase().includes('ceo') || role?.toLowerCase().includes('lead')
    ? Zap : role?.toLowerCase().includes('engineer') || role?.toLowerCase().includes('cto')
    ? Cpu : Bot;

  return (
    <motion.div
      className={cn('flex flex-col items-center gap-1.5', className)}
      style={{ boxShadow: glowColor(status) }}
      animate={status === 'active' && !reduce
        ? { scale: [1, 1.04, 1] }
        : {}}
      transition={{ duration: 3, repeat: Infinity, ease: 'easeInOut' }}
    >
      <div className="relative" style={{ width: cfg.ring, height: cfg.ring }}>
        {/* Background ring */}
        <svg width={cfg.ring} height={cfg.ring} className="absolute inset-0" aria-hidden>
          <circle
            cx={cfg.ring / 2} cy={cfg.ring / 2} r={r}
            fill="none" stroke="rgba(255,255,255,0.06)" strokeWidth={cfg.stroke}
          />
          {/* Reputation arc */}
          <circle
            cx={cfg.ring / 2} cy={cfg.ring / 2} r={r}
            fill="none" stroke={rc} strokeWidth={cfg.stroke}
            strokeDasharray={`${arc} ${circ}`}
            strokeLinecap="round"
            transform={`rotate(-90 ${cfg.ring / 2} ${cfg.ring / 2})`}
            style={{ filter: `drop-shadow(0 0 4px ${rc})` }}
          />
        </svg>

        {/* Center icon */}
        <div className="absolute inset-0 flex items-center justify-center rounded-full bg-[#0A0F1A]">
          <Icon style={{ width: cfg.icon, height: cfg.icon, color: status === 'active' ? '#00D4FF' : '#5A7494' }} aria-hidden />
        </div>

        {/* Status dot */}
        <div className={cn(
          'absolute bottom-0 right-0 w-3 h-3 rounded-full border-2 border-[#0A0F1A]',
          status === 'active'    && 'bg-[#00D4FF]',
          status === 'idle'      && 'bg-[#475569]',
          status === 'error'     && 'bg-[#FF3366]',
          status === 'completed' && 'bg-[#00E676]',
        )} aria-hidden />
      </div>

      {/* Name */}
      <div className="text-center">
        <p className="font-medium text-[#F0F6FF] truncate max-w-[80px]" style={{ fontSize: cfg.labelSize }}>
          {name.split(' ')[0]}
        </p>
        {role && (
          <p className="text-[#5A7494] truncate max-w-[80px]" style={{ fontSize: '9px' }}>{role.slice(0, 20)}</p>
        )}
      </div>
    </motion.div>
  );
}
