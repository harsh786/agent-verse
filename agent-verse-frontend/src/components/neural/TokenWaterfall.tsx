/**
 * TokenWaterfall — Streams LLM tokens character by character with cursor.
 * Spec §4.6: Monospace electric cyan text, TPS badge, model name badge.
 */
import { useEffect, useRef } from 'react';
import { motion, useReducedMotion } from 'framer-motion';
import { cn } from '@/lib/utils';

interface TokenWaterfallProps {
  stepName:   string;
  tokens:     string;
  isActive:   boolean;
  model?:     string;
  className?: string;
}

const MODEL_COLORS: Record<string, string> = {
  claude:   '#CC785C',
  gpt:      '#10A37F',
  gemini:   '#4285F4',
  llama:    '#7C3AED',
  qwen:     '#E07B39',
  default:  '#6366F1',
};

function modelColor(model?: string): string {
  if (!model) return MODEL_COLORS.default;
  const k = model.toLowerCase();
  for (const [key, val] of Object.entries(MODEL_COLORS)) {
    if (k.includes(key)) return val;
  }
  return MODEL_COLORS.default;
}

export function TokenWaterfall({ stepName, tokens, isActive, model, className }: TokenWaterfallProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const reduce    = useReducedMotion();

  // Auto-scroll to bottom as tokens come in
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [tokens]);

  if (!isActive && !tokens) return null;

  const mc = modelColor(model);

  return (
    <motion.div
      initial={{ opacity: 1, height: 0 }}
      animate={{ opacity: 1, height: 'auto' }}
      exit={{ opacity: 0, height: 0 }}
      transition={{ type: 'spring', stiffness: 280, damping: 26 }}
      className={cn('rounded-xl bg-[#020408] border border-[#00D4FF]/20 overflow-hidden', className)}
    >
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-1.5 border-b border-[#00D4FF]/10">
        <div className="flex items-center gap-2">
          {isActive && !reduce && (
            <span className="w-1.5 h-1.5 rounded-full bg-[#00D4FF] animate-pulse" aria-hidden />
          )}
          <span className="text-[10px] font-medium text-[#5A7494] truncate max-w-[160px]">
            {stepName.slice(0, 50)}
          </span>
        </div>
        <div className="flex items-center gap-1.5">
          {model && (
            <span
              className="text-[9px] font-mono px-1.5 py-0.5 rounded border"
              style={{ color: mc, borderColor: `${mc}40`, background: `${mc}10` }}
            >
              {model.slice(0, 20)}
            </span>
          )}
          {tokens.length > 0 && (
            <span className="text-[9px] font-mono text-[#5A7494] tabular-nums">
              {tokens.length} tok
            </span>
          )}
        </div>
      </div>

      {/* Token content */}
      <div
        ref={scrollRef}
        className="px-3 py-2 max-h-32 overflow-y-auto scroll-smooth"
      >
        <pre className="font-mono text-[11px] text-[#00D4FF] leading-relaxed whitespace-pre-wrap break-words">
          {tokens}
          {isActive && (
            <motion.span
              className="inline-block w-1.5 h-3.5 bg-[#00D4FF] ml-0.5 align-bottom"
              animate={reduce ? {} : { opacity: [1, 0, 1] }}
              transition={{ duration: 0.8, repeat: Infinity }}
              aria-hidden
            />
          )}
        </pre>
      </div>
    </motion.div>
  );
}
