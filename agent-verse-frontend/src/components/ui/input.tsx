import type { InputHTMLAttributes } from 'react';

interface InputProps extends InputHTMLAttributes<HTMLInputElement> {}
export function Input({ className = '', ...props }: InputProps) {
  return <input className={`w-full h-9 px-3 py-1 text-sm bg-white/[0.04] border border-white/[0.10] rounded-lg text-[#F0F6FF] placeholder:text-[#5A7494] focus:outline-none focus:border-[#00D4FF]/40 focus:ring-1 focus:ring-[#00D4FF]/20 ${className}`} {...props} />;
}
