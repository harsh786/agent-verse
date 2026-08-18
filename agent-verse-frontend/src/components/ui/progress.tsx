interface ProgressProps { value?: number; className?: string; }
export function Progress({ value = 0, className = '' }: ProgressProps) {
  return (
    <div className={`w-full h-2 bg-white/[0.08] rounded-full overflow-hidden ${className}`}>
      <div className="h-full bg-[#00D4FF] rounded-full transition-[width]" style={{ width: `${Math.min(100, Math.max(0, value))}%` }} />
    </div>
  );
}
