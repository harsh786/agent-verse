import { motion } from 'framer-motion';
import type { LucideIcon } from 'lucide-react';
import { cn } from '@/lib/utils';
import { JARVISStaggerItem } from './JARVISPageShell';

interface KpiCardProps {
  icon: LucideIcon;
  label: string;
  value: string | number;
  sub?: string;
  trend?: 'up' | 'down' | 'neutral';
  accent?: 'blue' | 'green' | 'amber' | 'violet' | 'cyan' | 'rose';
  isLoading?: boolean;
  onClick?: () => void;
}

const ACCENT_COLORS = {
  blue:   { text: 'text-blue-400',    bg: 'bg-blue-400/10',    glow: 'rgba(96,165,250,0.3)' },
  green:  { text: 'text-emerald-400', bg: 'bg-emerald-400/10', glow: 'rgba(52,211,153,0.3)' },
  amber:  { text: 'text-amber-400',   bg: 'bg-amber-400/10',   glow: 'rgba(251,191,36,0.3)' },
  violet: { text: 'text-violet-400',  bg: 'bg-violet-400/10',  glow: 'rgba(167,139,250,0.3)' },
  cyan:   { text: 'text-[#00D4FF]',   bg: 'bg-[#00D4FF]/10',   glow: 'rgba(0,212,255,0.3)' },
  rose:   { text: 'text-rose-400',    bg: 'bg-rose-400/10',    glow: 'rgba(251,113,133,0.3)' },
};

export function KpiCard({
  icon: Icon,
  label,
  value,
  sub,
  trend,
  accent = 'cyan',
  isLoading,
  onClick,
}: KpiCardProps) {
  const colors = ACCENT_COLORS[accent];
  return (
    <JARVISStaggerItem interactive>
      <motion.button
        onClick={onClick}
        whileHover={{ scale: 1.02, boxShadow: `0 0 20px ${colors.glow}` }}
        whileTap={{ scale: 0.98 }}
        transition={{ type: 'spring', stiffness: 400, damping: 25 }}
        className={cn(
          'w-full text-left p-4 rounded-xl border border-[#1E2535] bg-[#1A1F2E]',
          'hover:border-[#2D3748] transition-colors duration-150',
          onClick && 'cursor-pointer',
          !onClick && 'cursor-default',
        )}
        type="button"
      >
        <div className="flex items-center justify-between mb-3">
          <span className="text-xs text-[#64748B] font-medium">{label}</span>
          <span className={cn('p-1.5 rounded-lg', colors.bg)}>
            <Icon className={cn('h-3.5 w-3.5', colors.text)} />
          </span>
        </div>
        {isLoading ? (
          <div className="h-7 w-16 bg-[#252B3B] rounded animate-pulse mb-1" />
        ) : (
          <motion.p
            key={String(value)}
            initial={{ scale: 0.85, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            transition={{ type: 'spring', stiffness: 500, damping: 25 }}
            className={cn('text-2xl font-bold tabular-nums', colors.text)}
          >
            {value}
          </motion.p>
        )}
        {sub && <p className="text-[11px] text-[#475569] mt-0.5">{sub}</p>}
        {trend && !isLoading && (
          <div className="mt-1">
            {trend === 'up' && <span className="text-[10px] text-emerald-400">↑ trending up</span>}
            {trend === 'down' && <span className="text-[10px] text-rose-400">↓ trending down</span>}
            {trend === 'neutral' && <span className="text-[10px] text-[#64748B]">→ stable</span>}
          </div>
        )}
      </motion.button>
    </JARVISStaggerItem>
  );
}
