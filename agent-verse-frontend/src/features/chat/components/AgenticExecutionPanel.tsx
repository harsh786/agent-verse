/**
 * AgenticExecutionPanel — Right-side panel in ChatPage showing live execution.
 * Spec §6.1: Tool call cards, step progress nodes, knowledge hits, guardrail alerts.
 */
import { useEffect, useRef, useState } from 'react';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import { Zap, CheckCircle2, XCircle, BookOpen, Shield, Loader2, Clock } from 'lucide-react';
import type { GoalEvent } from '@/lib/sse/useGoalStream';
import { cn } from '@/lib/utils';

interface ExecutionItem {
  id:        string;
  type:      'tool' | 'step' | 'knowledge' | 'guardrail' | 'hitl' | 'complete' | 'failed';
  label:     string;
  detail?:   string;
  success?:  boolean;
  durationMs?: number;
  timestamp: number;
}

interface AgenticExecutionPanelProps {
  events:     GoalEvent[];
  isActive:   boolean;
  className?: string;
}

const TYPE_META: Record<ExecutionItem['type'], { color: string; Icon: React.ElementType }> = {
  tool:       { color: '#FFB300', Icon: Zap },
  step:       { color: '#6366F1', Icon: Loader2 },
  knowledge:  { color: '#34D399', Icon: BookOpen },
  guardrail:  { color: '#FF3366', Icon: Shield },
  hitl:       { color: '#FFB300', Icon: Clock },
  complete:   { color: '#00E676', Icon: CheckCircle2 },
  failed:     { color: '#FF3366', Icon: XCircle },
};

export function AgenticExecutionPanel({ events, isActive, className }: AgenticExecutionPanelProps) {
  const reduce   = useReducedMotion();
  const [items, setItems] = useState<ExecutionItem[]>([]);
  const scrollRef = useRef<HTMLDivElement>(null);
  const lastLen   = useRef(0);

  useEffect(() => {
    const newEvts = events.slice(lastLen.current);
    lastLen.current = events.length;
    if (!newEvts.length) return;

    setItems(prev => {
      const next = [...prev];
      for (const evt of newEvts) {
        const id = `${evt.type}-${Date.now()}-${Math.random()}`;
        if (evt.type === 'tool_call_complete' || evt.type === 'tool_call_failed') {
          next.push({ id, type: 'tool', label: String(evt.tool_name ?? evt.tool ?? 'Tool'),
            success: evt.type === 'tool_call_complete', timestamp: Date.now() });
        } else if (evt.type === 'step_started') {
          next.push({ id, type: 'step', label: String(evt.step ?? 'Step').slice(0, 60), timestamp: Date.now() });
        } else if (evt.type === 'knowledge_retrieved') {
          next.push({ id, type: 'knowledge', label: 'Knowledge retrieved', timestamp: Date.now() });
        } else if (evt.type === 'guardrail_rejected' || evt.type === 'tool_call_denied') {
          next.push({ id, type: 'guardrail', label: `Blocked: ${String(evt.rule ?? evt.reason ?? 'policy').slice(0, 40)}`, timestamp: Date.now() });
        } else if (evt.type === 'waiting_approval') {
          next.push({ id, type: 'hitl', label: 'Awaiting approval', detail: String(evt.action ?? ''), timestamp: Date.now() });
        } else if (evt.type === 'goal_complete') {
          next.push({ id, type: 'complete', label: 'Goal complete', timestamp: Date.now() });
        } else if (evt.type === 'goal_failed') {
          next.push({ id, type: 'failed', label: 'Goal failed', timestamp: Date.now() });
        }
      }
      return next.slice(-50);
    });
  }, [events]);

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [items]);

  if (!isActive && items.length === 0) return null;

  return (
    <motion.div
      initial={false}
      animate={{ opacity: 1, x: 0 }}
      exit={{ opacity: 0, x: 16 }}
      transition={{ type: 'spring', stiffness: 300, damping: 28 }}
      className={cn('flex flex-col bg-[#0A0F1A] rounded-xl border border-white/[0.07] overflow-hidden', className)}
    >
      <div className="flex items-center gap-2 px-3 py-2 border-b border-white/[0.06] shrink-0">
        {isActive && !reduce && (
          <span className="w-1.5 h-1.5 rounded-full bg-[#00D4FF] animate-pulse" aria-hidden />
        )}
        <span className="text-[11px] font-medium text-[#A0B4CC] uppercase tracking-wide">Execution</span>
      </div>

      <div ref={scrollRef} className="flex-1 overflow-y-auto px-2 py-1 space-y-0.5 max-h-64" role="log" aria-live="polite">
        {items.length === 0 && (
          <p className="text-center text-[11px] text-[#334155] py-4">Waiting…</p>
        )}
        <AnimatePresence mode="popLayout">
          {items.map(item => {
            const { color, Icon } = TYPE_META[item.type];
            return (
              <motion.div
                key={item.id}
                layout
                initial={false}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0 }}
                transition={{ type: 'spring', stiffness: 500, damping: 35 }}
                className="flex items-start gap-2 px-2 py-1.5 rounded-lg"
              >
                <div className="w-0.5 self-stretch rounded-full shrink-0" style={{ background: color, minHeight: 12 }} aria-hidden />
                <Icon className={cn('h-3 w-3 shrink-0 mt-0.5', item.type === 'step' && 'animate-spin')} style={{ color }} aria-hidden />
                <div className="flex-1 min-w-0">
                  <p className="text-[10px] font-medium text-[#F0F6FF] truncate">{item.label}</p>
                  {item.detail && <p className="text-[9px] text-[#5A7494] font-mono mt-0.5 truncate">{item.detail}</p>}
                </div>
              </motion.div>
            );
          })}
        </AnimatePresence>
      </div>
    </motion.div>
  );
}
