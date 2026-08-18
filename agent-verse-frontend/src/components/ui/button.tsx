import type { ReactNode, ButtonHTMLAttributes } from 'react';

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> { children: ReactNode; variant?: 'default'|'ghost'|'outline'|'destructive'; size?: 'sm'|'default'|'lg'|'icon'; }
export function Button({ children, variant = 'default', size = 'default', className = '', ...props }: ButtonProps) {
  const base = 'inline-flex items-center justify-center rounded-lg font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00D4FF]/60 disabled:opacity-50 disabled:pointer-events-none';
  const variants = { default: 'bg-[#00D4FF] text-[#0A0F1A] hover:bg-[#00D4FF]/90', ghost: 'hover:bg-white/[0.06] text-[#A0B4CC]', outline: 'border border-white/[0.12] text-[#F0F6FF] hover:bg-white/[0.06]', destructive: 'bg-[#FF3366]/15 text-[#FF3366] hover:bg-[#FF3366]/25' };
  const sizes = { sm: 'h-8 px-3 text-xs', default: 'h-9 px-4 text-sm', lg: 'h-11 px-6 text-sm', icon: 'h-9 w-9 p-0' };
  return <button className={`${base} ${variants[variant]} ${sizes[size]} ${className}`} {...props}>{children}</button>;
}
