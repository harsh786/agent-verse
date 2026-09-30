import { Zap, Pause, AlertTriangle, CalendarX } from 'lucide-react';

type TriggerStatus = 'active' | 'paused' | 'circuit_open' | 'expired';

interface TriggerStatusBadgeProps {
  paused: boolean;
  circuitOpen?: boolean;
  /** Past its expires_at_iso: it no longer fires (TRG-09). */
  expired?: boolean;
}

const STATUS_CONFIG: Record<TriggerStatus, {
  label: string;
  icon: React.ReactNode;
  className: string;
}> = {
  active: {
    label: 'Active',
    icon: <Zap className="h-3 w-3" />,
    className: 'bg-emerald-100 text-emerald-700 border-emerald-200 dark:bg-emerald-900/30 dark:text-emerald-300 dark:border-emerald-800',
  },
  paused: {
    label: 'Paused',
    icon: <Pause className="h-3 w-3" />,
    className: 'bg-amber-100 text-amber-700 border-amber-200 dark:bg-amber-900/30 dark:text-amber-300 dark:border-amber-800',
  },
  circuit_open: {
    label: 'Circuit Open',
    icon: <AlertTriangle className="h-3 w-3 animate-pulse" />,
    className: 'bg-red-100 text-red-700 border-red-200 dark:bg-red-900/30 dark:text-red-300 dark:border-red-800',
  },
  expired: {
    label: 'Expired',
    icon: <CalendarX className="h-3 w-3" />,
    className: 'bg-slate-100 text-slate-700 border-slate-200 dark:bg-slate-800/50 dark:text-slate-300 dark:border-slate-700',
  },
};

export function TriggerStatusBadge({ paused, circuitOpen = false, expired = false }: TriggerStatusBadgeProps) {
  const status: TriggerStatus = circuitOpen
    ? 'circuit_open'
    : expired
      ? 'expired'
      : paused
        ? 'paused'
        : 'active';
  const { label, icon, className } = STATUS_CONFIG[status];

  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium ${className}`}
      aria-label={`Trigger status: ${label.toLowerCase()}`}
    >
      {icon}
      {label}
    </span>
  );
}
