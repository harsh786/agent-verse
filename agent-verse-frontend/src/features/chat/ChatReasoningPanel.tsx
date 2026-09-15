/**
 * ChatReasoningPanel — collapsible agent-transparency panel that surfaces the
 * accumulated reasoning/thinking stream. Collapsed by default; respects
 * prefers-reduced-motion (via framer-motion's useReducedMotion, matching
 * AgenticExecutionPanel) by dropping the expand animation.
 */

import { useState, type JSX } from 'react';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import { Brain, ChevronDown } from 'lucide-react';

interface Props {
  reasoning: string;
  /** Streaming = still accumulating; shows a subtle live indicator. */
  isStreaming?: boolean;
  defaultOpen?: boolean;
}

export function ChatReasoningPanel({ reasoning, isStreaming, defaultOpen = false }: Props): JSX.Element | null {
  const reduce = useReducedMotion();
  const [open, setOpen] = useState(defaultOpen);

  if (!reasoning.trim()) return null;

  return (
    <div className="mx-4 my-2 rounded-xl border border-border bg-background overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-controls="chat-reasoning-body"
        className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-white/[0.03] transition-colors"
      >
        <Brain className="h-3.5 w-3.5 text-muted-foreground/70 shrink-0" aria-hidden />
        <span className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
          Reasoning
        </span>
        {isStreaming && !reduce && (
          <span className="w-1.5 h-1.5 rounded-full bg-primary animate-pulse" aria-hidden />
        )}
        <ChevronDown
          className={`ml-auto h-3.5 w-3.5 text-muted-foreground/70 transition-transform ${open ? 'rotate-180' : ''}`}
          aria-hidden
        />
      </button>

      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            id="chat-reasoning-body"
            initial={reduce ? false : { height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={reduce ? { opacity: 0 } : { height: 0, opacity: 0 }}
            transition={reduce ? { duration: 0 } : { duration: 0.2 }}
            className="overflow-hidden"
          >
            <pre className="px-3 pb-3 pt-1 text-[11px] leading-relaxed text-muted-foreground/70 whitespace-pre-wrap break-words font-mono max-h-64 overflow-y-auto">
              {reasoning}
            </pre>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
