import { clsx } from 'clsx';

const STATUS_STYLES: Record<string, string> = {
  success:  'bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300',
  running:  'bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-300',
  pending:  'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/40 dark:text-yellow-300',
  failed:   'bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300',
  complete: 'bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300',
  planning: 'bg-purple-100 text-purple-800 dark:bg-purple-900/40 dark:text-purple-300',
  paused:   'bg-gray-100 text-gray-800 dark:bg-gray-800 dark:text-gray-300',
  cancelled:'bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400',
};

const DEFAULT_STYLE = 'bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300';

interface StatusBadgeProps {
  status: string;
  className?: string;
}

export function StatusBadge({ status, className }: StatusBadgeProps) {
  const styles = STATUS_STYLES[status.toLowerCase()] ?? DEFAULT_STYLE;
  return (
    <span
      className={clsx(
        'inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium capitalize',
        styles,
        className
      )}
    >
      {status}
    </span>
  );
}
