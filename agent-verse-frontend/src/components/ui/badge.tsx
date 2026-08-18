import type { ReactNode } from 'react';

interface BadgeProps { children: ReactNode; variant?: 'default'|'secondary'|'destructive'|'outline'; className?: string; }
export function Badge({ children, variant = 'default', className = '' }: BadgeProps) {
  const base = 'inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium';
  const variants = {
    default: 'bg-[#00D4FF]/15 text-[#00D4FF]',
    secondary: 'bg-white/[0.08] text-[#A0B4CC]',
    destructive: 'bg-[#FF3366]/15 text-[#FF3366]',
    outline: 'border border-white/[0.15] text-[#A0B4CC]',
  };
  return <span className={`${base} ${variants[variant]} ${className}`}>{children}</span>;
}
