/**
 * MissionCommandLog — Live org mission event stream.
 * Spec §3.3: Tool calls, step progress, approvals, token flow. Virtualized.
 */
import { useEffect, useRef, useState } from 'react';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import {
  Zap, CheckCircle2, XCircle, Clock, Shield, BookOpen,
  AlertTriangle, RefreshCw, MessageSquare, Activity,
} from 'lucide-react';
import { cn } from '@/lib/utils';

interface CommandLogEntry {
  id:        string;
  timestamp: number;
  type:      string;
  label:     string;
  detail?:   string;
  color:     string;
  icon:      React.ElementType;
  cost?:     number;
}

interface MissionCommandLogProps {
  orgId?:        string;
  missionId?:    string;
  maxItems?:     number;
  className?:    string;
  /** External events to append (from SSE or polling) */
  externalEntries?: Array<{
    type:    string;
    label:   string;
    detail?: string;
    cost?:   number;
  }>;
}

const EVENT_META: Record<string, { color: string; icon: React.ElementType }> = {
  step_started:               { color: '#6366F1', icon: Activity },
  step_complete:              { color: '#00E676', icon: CheckCircle2 },
  tool_call_complete:         { color: '#00D4FF', icon: Zap },
  tool_call_failed:           { color: '#FF3366', icon: XCircle },
  tool_call_pending_approval: { color: '#FFB300', icon: Clock },
  waiting_approval:           { color: '#FFB300', icon: Clock },
  approval_granted:           { color: '#00E676', icon: CheckCircle2 },
  hitl_approved:              { color: '#00E676', icon: CheckCircle2 },
  hitl_rejected:              { color: '#FF3366', icon: XCircle },
  guardrail_rejected:         { color: '#FF3366', icon: Shield },
  tool_call_denied:           { color: '#FF3366', icon: Shield },
  knowledge_retrieved:        { color: '#34D399', icon: BookOpen },
  plan_ready:                 { color: '#6366F1', icon: MessageSquare },
  replan:                     { color: '#FFB300', icon: RefreshCw },
  goal_complete:              { color: '#00E676', icon: CheckCircle2 },
  goal_failed:                { color: '#FF3366', icon: XCircle },
  verification_done:          { color: '#00E676', icon: CheckCircle2 },
  grounding_warning:          { color: '#FFB300', icon: AlertTriangle },
  stuck_loop_detected:        { color: '#FF3366', icon: AlertTriangle },
  child_agent_spawned:        { color: '#A855F7', icon: Activity },
  default:                    { color: '#5A7494', icon: Activity },
};

function fmt(ms: number): string {
  const d = Math.floor((Date.now() - ms) / 1000);
  if (d < 60) return `${d}s`;
  if (d < 3600) return `${Math.floor(d / 60)}m`;
  return `${Math.floor(d / 3600)}h`;
}

const MAX = 200;

export function MissionCommandLog({
  maxItems = MAX, className, externalEntries = [],
}: MissionCommandLogProps) {
  const reduce  = useReducedMotion();
  const [entries, setEntries] = useState<CommandLogEntry[]>([]);
  const scrollRef = useRef<HTMLDivElement>(null);
  const lastExt   = useRef(0);

  useEffect(() => {
    const newOnes = externalEntries.slice(lastExt.current);
    lastExt.current = externalEntries.length;
    if (!newOnes.length) return;
    setEntries(prev => {
      const next = [...prev, ...newOnes.map(e => {
        const meta = EVENT_META[e.type] ?? EVENT_META.default;
        return {
          id:        `${e.type}-${Date.now()}-${Math.random()}`,
          timestamp: Date.now(),
          type:      e.type,
          label:     e.label,
          detail:    e.detail,
          color:     meta.color,
          icon:      meta.icon,
          cost:      e.cost,
        };
      })];
      return next.slice(-maxItems);
    });
  }, [externalEntries, maxItems]);

  // Auto-scroll
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [entries]);

  return (
    <div className={cn('flex flex-col bg-[#0A0F1A] rounded-xl border border-white/[0.07] overflow-hidden', className)}>
      <div className="flex items-center gap-2 px-3 py-2 border-b border-white/[0.06] shrink-0">
        <Activity className="h-3.5 w-3.5 text-[#00D4FF]" aria-hidden />
        <span className="text-[11px] font-medium text-[#A0B4CC] uppercase tracking-wide">Command Log</span>
        <span className="ml-auto text-[9px] text-[#5A7494] font-mono tabular-nums">{entries.length}</span>
      </div>

      <div
        ref={scrollRef}
        className="flex-1 overflow-y-auto px-2 py-1 space-y-0.5"
        style={{ maxHeight: 360 }}
        role="log"
        aria-label="Mission command log"
        aria-live="polite"
      >
        {entries.length === 0 && (
          <p className="text-center text-[11px] text-[#334155] py-6">Waiting for events…</p>
        )}
        <AnimatePresence mode="popLayout">
          {entries.slice(-maxItems).map(entry => {
            const Icon = entry.icon;
            return (
              <motion.div
                key={entry.id}
                layout
                initial={reduce ? { opacity: 0 } : { opacity: 0, x: -8 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: 4 }}
                transition={{ type: 'spring', stiffness: 400, damping: 35 }}
                className="flex items-start gap-2 px-2 py-1.5 rounded-lg hover:bg-white/[0.03] group"
              >
                {/* Color indicator */}
                <div
                  className="w-0.5 h-full self-stretch rounded-full mt-0.5 shrink-0"
                  style={{ background: entry.color, minHeight: 12 }}
                  aria-hidden
                />
                <Icon className="h-3 w-3 shrink-0 mt-0.5" style={{ color: entry.color }} aria-hidden />
                <div className="flex-1 min-w-0">
                  <p className="text-[10px] font-medium text-[#F0F6FF] leading-tight truncate">
                    {entry.label}
                  </p>
                  {entry.detail && (
                    <p className="text-[9px] text-[#5A7494] font-mono mt-0.5 line-clamp-1">{entry.detail}</p>
                  )}
                </div>
                <div className="flex items-center gap-1.5 shrink-0">
                  {entry.cost !== undefined && entry.cost > 0 && (
                    <span className="text-[9px] font-mono text-[#34D399]">+${entry.cost.toFixed(4)}</span>
                  )}
                  <span className="text-[9px] text-[#334155] font-mono tabular-nums">{fmt(entry.timestamp)}</span>
                </div>
              </motion.div>
            );
          })}
        </AnimatePresence>
      </div>
    </div>
  );
}
