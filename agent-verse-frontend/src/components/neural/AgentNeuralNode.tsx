/**
 * AgentNeuralNode — Universal animated agent node for constellation and graphs.
 * Spec §3.2: 9 neural states, tier-glow system, breathing for active agents.
 */
import { motion, useReducedMotion } from 'framer-motion';
import { Bot, Cpu, Zap, Loader2, AlertTriangle, CheckCircle2, Shield, Clock } from 'lucide-react';
import { cn } from '@/lib/utils';

export type AgentNeuralState =
  | 'idle' | 'thinking' | 'communicating' | 'debating'
  | 'executing' | 'waiting' | 'error' | 'completed' | 'blocked';

interface AgentNeuralNodeProps {
  agentId:    string;
  label:      string;
  role?:      string;
  state:      AgentNeuralState;
  isSelected?: boolean;
  onClick?:   (id: string) => void;
  size?:      'sm' | 'md' | 'lg';
  className?: string;
}

const GLOW: Record<AgentNeuralState, string> = {
  idle:          '0 0 8px rgba(0,212,255,0.10)',
  thinking:      '0 0 16px rgba(99,102,241,0.35)',
  communicating: '0 0 20px rgba(0,212,255,0.50), 0 0 4px rgba(0,212,255,0.80)',
  debating:      '0 0 16px rgba(168,85,247,0.45)',
  executing:     '0 0 20px rgba(0,212,255,0.45), 0 0 6px rgba(0,212,255,0.80)',
  waiting:       '0 0 12px rgba(255,179,0,0.30)',
  error:         '0 0 16px rgba(255,51,102,0.45)',
  completed:     '0 0 12px rgba(0,230,118,0.30)',
  blocked:       '0 0 12px rgba(255,51,102,0.30)',
};

const BORDER_COLOR: Record<AgentNeuralState, string> = {
  idle:          'rgba(255,255,255,0.08)',
  thinking:      'rgba(99,102,241,0.40)',
  communicating: 'rgba(0,212,255,0.60)',
  debating:      'rgba(168,85,247,0.50)',
  executing:     'rgba(0,212,255,0.50)',
  waiting:       'rgba(255,179,0,0.40)',
  error:         'rgba(255,51,102,0.50)',
  completed:     'rgba(0,230,118,0.40)',
  blocked:       'rgba(255,51,102,0.30)',
};

const STATE_ICON: Record<AgentNeuralState, React.ElementType> = {
  idle:          Bot,
  thinking:      Cpu,
  communicating: Zap,
  debating:      Zap,
  executing:     Loader2,
  waiting:       Clock,
  error:         AlertTriangle,
  completed:     CheckCircle2,
  blocked:       Shield,
};

const SIZE = { sm: 32, md: 44, lg: 56 };

export function AgentNeuralNode({
  agentId, label, state, isSelected, onClick, size = 'md', className,
}: AgentNeuralNodeProps) {
  const reduce = useReducedMotion();
  const Icon   = STATE_ICON[state];
  const sz     = SIZE[size];
  const isLive = ['executing', 'communicating', 'thinking'].includes(state);

  return (
    <motion.button
      onClick={() => onClick?.(agentId)}
      style={{ width: sz, height: sz, boxShadow: GLOW[state], borderColor: isSelected ? '#00D4FF' : BORDER_COLOR[state] }}
      className={cn(
        'relative rounded-full bg-[#0A0F1A] border-2 flex items-center justify-center',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00D4FF]',
        className,
      )}
      // WS-7 item 3 — "bots alive": a working agent breathes noticeably;
      // an idle one still gets the faintest life (a slow, subtle drift) so
      // the constellation never reads as frozen/dead — reduced-motion
      // collapses both to a static node.
      animate={reduce
        ? { scale: 1 }
        : isLive
          ? { scale: [1, 1.06, 1] }
          : { scale: [1, 1.015, 1], opacity: [0.92, 1, 0.92] }}
      transition={{ duration: isLive ? 3 : 6, repeat: Infinity, ease: 'easeInOut' }}
      whileHover={{ scale: 1.08, transition: { type: 'spring', stiffness: 600, damping: 35 } }}
      whileTap={{   scale: 0.93, transition: { type: 'spring', stiffness: 800, damping: 40 } }}
      aria-label={`Agent ${label}: ${state}`}
    >
      <Icon
        className={cn(state === 'executing' && 'animate-spin')}
        style={{
          width:  sz * 0.4,
          height: sz * 0.4,
          color:  state === 'completed' ? '#00E676'
                : state === 'error' || state === 'blocked' ? '#FF3366'
                : state === 'waiting' ? '#FFB300'
                : '#00D4FF',
        }}
        aria-hidden
      />
      {/* Pulse ring for active states */}
      {isLive && !reduce && (
        <motion.div
          className="absolute inset-0 rounded-full border"
          style={{ borderColor: BORDER_COLOR[state] }}
          animate={{ opacity: [0.6, 0.1, 0.6], scale: [1, 1.2, 1] }}
          transition={{ duration: 2, repeat: Infinity, ease: 'easeInOut' }}
          aria-hidden
        />
      )}
    </motion.button>
  );
}
