import type { TextareaHTMLAttributes } from 'react';
interface TextareaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {}
export function Textarea({ className = '', ...props }: TextareaProps) {
  return <textarea className={`w-full px-3 py-2 text-sm bg-white/[0.04] border border-white/[0.10] rounded-lg text-[#F0F6FF] placeholder:text-[#5A7494] focus:outline-none focus:border-[#00D4FF]/40 focus:ring-1 focus:ring-[#00D4FF]/20 resize-y min-h-[80px] ${className}`} {...props} />;
}
