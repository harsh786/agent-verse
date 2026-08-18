/**
 * StatusOrb — Animated status indicator used across ALL pages.
 * Shows sonar-ping pulse ring for active states (running/pending).
 * Replaces all inline `w-2 h-2 rounded-full bg-green-500 animate-pulse` dots.
 */
import { motion, useReducedMotion } from 'framer-motion';

export type OrbStatus =
  | 'running' | 'executing' | 'active'
  | 'completed' | 'complete' | 'success' | 'published'
  | 'failed' | 'error'
  | 'pending' | 'planning' | 'waiting_human' | 'waiting' | 'queued'
  | 'idle' | 'paused' | 'draft'
  | 'offline' | 'unknown'
  | 'connected' | 'degraded'
  | string;

const ORB_COLOR: Record<string, string> = {
  running:       '#00D4FF', executing:    '#00D4FF', active:    '#00D4FF',
  connected:     '#00D4FF',
  completed:     '#10B981', complete:     '#10B981', success:   '#10B981',
  published:     '#10B981',
  failed:        '#EF4444', error:        '#EF4444',
  degraded:      '#F59E0B',
  pending:       '#F59E0B', planning:     '#F59E0B', waiting_human: '#F59E0B',
  waiting:       '#F59E0B', queued:       '#F59E0B',
  idle:          '#475569', paused:       '#475569', draft:     '#8B5CF6',
  offline:       '#1E2535', unknown:      '#1E2535',
};

const PULSE_SET = new Set([
  'running', 'executing', 'active', 'pending', 'planning',
  'waiting_human', 'queued', 'connected',
]);

interface StatusOrbProps {
  status: OrbStatus;
  /** Diameter in px. Default 8. */
  size?: number;
  className?: string;
}

export function StatusOrb({ status, size = 8, className = '' }: StatusOrbProps) {
  const reduce = useReducedMotion();
  const color  = ORB_COLOR[status] ?? ORB_COLOR.unknown;
  const active = PULSE_SET.has(status) && !reduce;

  return (
    <span
      className={`relative inline-flex shrink-0 items-center justify-center ${className}`}
      style={{ width: size, height: size }}
      role="img"
      aria-label={`Status: ${status}`}
    >
      {/* Sonar ring — expands outward and fades */}
      {active && (
        <motion.span
          className="absolute rounded-full"
          style={{ width: size, height: size, backgroundColor: color }}
          animate={{ scale: [1, 2.2, 1], opacity: [0.7, 0, 0.7] }}
          transition={{ duration: 1.8, repeat: Infinity, ease: 'easeInOut' }}
        />
      )}
      {/* Core dot */}
      <span
        className="relative rounded-full"
        style={{ width: size, height: size, backgroundColor: color }}
      />
    </span>
  );
}
