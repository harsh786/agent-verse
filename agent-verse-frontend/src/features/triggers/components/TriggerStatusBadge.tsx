import { Zap, Pause, AlertTriangle } from 'lucide-react';

type TriggerStatus = 'active' | 'paused' | 'circuit_open';

interface TriggerStatusBadgeProps {
  paused: boolean;
  circuitOpen?: boolean;
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
};

export function TriggerStatusBadge({ paused, circuitOpen = false }: TriggerStatusBadgeProps) {
  const status: TriggerStatus = circuitOpen ? 'circuit_open' : paused ? 'paused' : 'active';
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
