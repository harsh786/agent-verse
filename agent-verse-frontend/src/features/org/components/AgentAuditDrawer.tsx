/**
 * AgentAuditDrawer — per-agent audit trail ("black box for each robot").
 *
 * Slides in from the right when an agent node in the Live Agent Network is
 * selected. Shows every message/decision/task/event recorded for that agent,
 * newest-first (as returned by the backend), with cost + duration when known.
 * Decision entries carry an "explain" control that reveals the decision's
 * provenance — its full detail plus the `ref` (source table + row id) the
 * backend attached — so a decision is never just a headline.
 */
import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import {
  X, MessageSquare, Activity, Sparkles, ListChecks, AlertTriangle, ChevronDown,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { situationApi } from '@/features/org/api';
import type { AgentAuditEntry } from '@/features/org/types';

interface AgentAuditDrawerProps {
  orgId:    string;
  agentId:  string;
  onClose:  () => void;
}

const SPRING_PANEL = { type: 'spring', stiffness: 300, damping: 28 } as const;

const QUERY_KEY = (orgId: string, agentId: string) => ['org-agent-audit', orgId, agentId] as const;

type Kind = AgentAuditEntry['kind'];

/** Design-system colors per audit-entry kind (Task 10 brief). */
const KIND_CONFIG: Record<Kind, { label: string; color: string; icon: React.ElementType }> = {
  message:  { label: 'Message',  color: '#00D4FF', icon: MessageSquare },
  event:    { label: 'Event',    color: '#64748B', icon: Activity },
  decision: { label: 'Decision', color: '#A855F7', icon: Sparkles },
  task:     { label: 'Task',     color: '#10B981', icon: ListChecks },
};

/** Up-to-2-letter initials from an agent id/slug, e.g. "coder-agent" → "CA". */
function initialsOf(id: string): string {
  const parts = id.split(/[\s_-]+/).filter(Boolean);
  if (parts.length === 0) return '?';
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[1][0]).toUpperCase();
}

function formatTimestamp(iso: string): string {
  try {
    return new Intl.DateTimeFormat(undefined, {
      month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit',
    }).format(new Date(iso));
  } catch {
    return iso;
  }
}

function formatCost(cost: number | null): string | null {
  if (cost === null || cost === undefined) return null;
  return `$${cost.toFixed(cost > 0 && cost < 0.01 ? 4 : 2)}`;
}

function formatDuration(ms: number | null): string | null {
  if (ms === null || ms === undefined) return null;
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

export function AgentAuditDrawer({ orgId, agentId, onClose }: AgentAuditDrawerProps) {
  const reduce = useReducedMotion();
  const panelRef = useRef<HTMLDivElement>(null);
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());

  const { data, isLoading, isError, error } = useQuery({
    queryKey: QUERY_KEY(orgId, agentId),
    queryFn:  () => situationApi.agentAudit(orgId, agentId),
  });

  // Focus the drawer on open (minimal focus-trap-lite: keeps focus inside the
  // dialog on mount) and let Escape close it, like the other JARVIS overlays.
  useEffect(() => {
    panelRef.current?.focus();
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [onClose]);

  const toggleExpand = (id: string) => {
    setExpandedIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  return (
    <AnimatePresence>
      <motion.div
        key="backdrop"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        className="fixed inset-0 bg-black/40 z-40 backdrop-blur-sm"
        onClick={onClose}
        aria-hidden
      />
      <motion.aside
        key="panel"
        ref={panelRef}
        tabIndex={-1}
        initial={reduce ? false : { x: '100%' }}
        animate={{ x: 0, opacity: 1 }}
        exit={reduce ? { opacity: 0 } : { x: '100%', opacity: 0 }}
        transition={SPRING_PANEL}
        role="dialog"
        aria-modal
        aria-label={`Agent audit trail: ${agentId}`}
        className="fixed right-0 top-0 h-full w-full max-w-md bg-[#0B0E14] border-l border-[#1E2535] z-50 flex flex-col shadow-2xl focus:outline-none"
      >
        {/* Header */}
        <div className="flex items-center gap-3 px-5 py-4 border-b border-[#1E2535] shrink-0">
          <div
            className="h-10 w-10 rounded-xl bg-[#1A1F2E] border border-[#1E2535] flex items-center justify-center text-[#00D4FF] text-xs font-semibold shrink-0"
            aria-hidden
          >
            {initialsOf(agentId)}
          </div>
          <div className="flex-1 min-w-0">
            <h2 className="text-base font-semibold text-[#F1F5F9] truncate">{agentId}</h2>
            <p className="text-[11px] text-[#475569]">Audit trail</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            style={{ touchAction: 'manipulation' }}
            className="p-2 rounded-lg text-[#475569] hover:text-[#94A3B8] hover:bg-[#1A1F2E] transition-colors min-w-[44px] min-h-[44px] flex items-center justify-center focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00D4FF]/50"
          >
            <X className="h-4 w-4" aria-hidden />
          </button>
        </div>

        {/* Timeline */}
        <div className="flex-1 overflow-y-auto px-5 py-4">
          {isLoading ? (
            <div className="space-y-2" aria-label="Loading audit trail">
              {[0, 1, 2].map(i => (
                <div key={i} className="h-16 rounded-lg bg-[#0F1623] border border-[#1E2535] animate-pulse" />
              ))}
            </div>
          ) : isError ? (
            <div className="flex items-center gap-2 py-3 text-[13px] text-red-400">
              <AlertTriangle className="h-3.5 w-3.5 flex-shrink-0" aria-hidden />
              Failed to load audit trail{error instanceof Error ? `: ${error.message}` : '.'}
            </div>
          ) : !data || data.length === 0 ? (
            <div className="py-8 text-center">
              <p className="text-[13px] text-[#475569]">No recorded activity for this agent</p>
            </div>
          ) : (
            <ol className="space-y-2" aria-label="Agent audit timeline">
              {data.map(entry => {
                const conf = KIND_CONFIG[entry.kind];
                const Icon = conf.icon;
                const cost = formatCost(entry.cost_usd);
                const duration = formatDuration(entry.duration_ms);
                const isExpanded = expandedIds.has(entry.id);
                const hasRef = entry.ref && Object.keys(entry.ref).length > 0;

                return (
                  <li key={entry.id} className="rounded-lg border border-[#1E2535] bg-[#0F1623] overflow-hidden">
                    <div className="px-3 py-2.5">
                      <div className="flex items-start justify-between gap-2">
                        <div className="flex items-center gap-1.5 min-w-0">
                          <span
                            className="inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide shrink-0"
                            style={{ color: conf.color, backgroundColor: `${conf.color}1A` }}
                          >
                            <Icon className="h-2.5 w-2.5" aria-hidden />
                            {conf.label}
                          </span>
                          <span className="text-[13px] font-medium text-[#F1F5F9] truncate">{entry.title}</span>
                        </div>
                        <time dateTime={entry.at} className="text-[11px] text-[#475569] tabular-nums shrink-0">
                          {formatTimestamp(entry.at)}
                        </time>
                      </div>

                      {entry.detail && (
                        <p className="mt-1 text-[12px] leading-snug text-[#94A3B8]">{entry.detail}</p>
                      )}

                      <div className="mt-1.5 flex items-center gap-3">
                        {cost && <span className="text-[11px] font-mono text-[#475569]">{cost}</span>}
                        {duration && <span className="text-[11px] font-mono text-[#475569]">{duration}</span>}
                        {entry.kind === 'decision' && (
                          <button
                            type="button"
                            onClick={() => toggleExpand(entry.id)}
                            aria-expanded={isExpanded}
                            aria-label={`Explain decision: ${entry.title}`}
                            className="ml-auto inline-flex items-center gap-1 text-[11px] font-medium text-[#A855F7] hover:text-[#c084fc] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#A855F7]/50 rounded"
                          >
                            Explain
                            <ChevronDown
                              aria-hidden
                              className={cn('h-3 w-3 transition-transform', isExpanded && 'rotate-180')}
                            />
                          </button>
                        )}
                      </div>

                      {isExpanded && entry.kind === 'decision' && (
                        <div className="mt-2 border-t border-[#1E2535] pt-2">
                          <p className="text-[11px] text-[#94A3B8] leading-relaxed">{entry.detail}</p>
                          {hasRef && (
                            <dl className="mt-1.5 space-y-0.5">
                              {Object.entries(entry.ref).map(([k, v]) => (
                                <div key={k} className="flex gap-1.5 text-[11px] font-mono">
                                  <dt className="text-[#475569]">{k}:</dt>
                                  <dd className="text-[#64748B] truncate">{String(v)}</dd>
                                </div>
                              ))}
                            </dl>
                          )}
                        </div>
                      )}
                    </div>
                  </li>
                );
              })}
            </ol>
          )}
        </div>
      </motion.aside>
    </AnimatePresence>
  );
}

export default AgentAuditDrawer;
