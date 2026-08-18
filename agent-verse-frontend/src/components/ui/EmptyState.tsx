import type { ReactNode } from 'react';
import { motion, useReducedMotion } from 'framer-motion';
import { springs } from '@/lib/design/motion';

export type EmptyStateVariant = 'float' | 'pulse' | 'static';

interface EmptyStateProps {
  icon?:        ReactNode;
  title:        string;
  description?: string;
  action?:      ReactNode;
  variant?:     EmptyStateVariant;
  className?:   string;
}

export function EmptyState({
  icon, title, description, action,
  variant = 'float', className = '',
}: EmptyStateProps) {
  const reduce = useReducedMotion();

  const iconAnim = (() => {
    if (reduce || variant === 'static') return { animate: undefined, transition: undefined };
    if (variant === 'float') return {
      animate: { y: [0, -8, 0] as number[] },
      transition: { duration: 3, repeat: Infinity, ease: 'easeInOut' as const },
    };
    return {
      animate: { scale: [1, 1.08, 1] as number[] },
      transition: { duration: 2, repeat: Infinity, ease: 'easeInOut' as const },
    };
  })();

  return (
    <motion.div
      initial={reduce ? {} : { opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ ...springs.page, delay: 0.1 }}
      className={`flex flex-col items-center justify-center py-14 px-6 text-center gap-3 ${className}`}
    >
      {icon && (
        <motion.div
          animate={iconAnim.animate}
          transition={iconAnim.transition}
          className="text-[#00D4FF] opacity-35 mb-1"
        >
          {icon}
        </motion.div>
      )}
      <p className="text-sm font-semibold text-[#F1F5F9]">{title}</p>
      {description && (
        <p className="text-xs text-[#475569] max-w-xs leading-relaxed">{description}</p>
      )}
      {action && <div className="mt-2">{action}</div>}
    </motion.div>
  );
}
