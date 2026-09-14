/**
 * TeamChannel — Team Channel v2: live cross-agent/department collaboration
 * feed for the Situation Room.
 *
 * Loads recent history via `situationApi.collaborationHistory` (TanStack
 * Query, newest-first) then layers live `org.collaboration.message` events
 * off the org realtime stream on top — deduped by id, capped at `maxItems`.
 * Each row shows a per-agent avatar, a kind chip, the message body, and a
 * compact latency/tokens/cost meta line; clicking (or activating via
 * keyboard) a row expands a payload inspector with the raw JSON.
 */
import { useCallback, useMemo, useState } from 'react';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import { useQuery } from '@tanstack/react-query';
import { MessagesSquare, ChevronDown, Loader2, AlertTriangle, Link2 } from 'lucide-react';
import { cn } from '@/lib/utils';
import { situationApi } from '@/features/org/api';
import type { CollaborationMessage } from '@/features/org/types';
import { useOrgRealtimeManager, ORG_EVENTS, type OrgEvent } from '../OrgRealtimeManager';

interface TeamChannelProps {
  orgId:      string;
  maxItems?:  number;
  className?: string;
}

const MAX_MESSAGES = 50;

// ── Kind chip design (see design-system doc: dark JARVIS + kind colors) ─────

interface KindConfig { label: string; icon: string; color: string }

const KIND_CONFIG: Record<string, KindConfig> = {
  update:   { label: 'Update',   icon: '•',  color: '#64748B' },
  proposal: { label: 'Proposal', icon: '💡', color: '#6366F1' },
  question: { label: 'Question', icon: '❓', color: '#00D4FF' },
  handoff:  { label: 'Handoff',  icon: '🤝', color: '#A855F7' },
  result:   { label: 'Result',   icon: '✅', color: '#10B981' },
  risk:     { label: 'Risk',     icon: '⚠️', color: '#F59E0B' },
  block:    { label: 'Block',    icon: '⛔', color: '#EF4444' },
};
const DEFAULT_KIND: KindConfig = { label: 'Update', icon: '•', color: '#64748B' };

function kindConfig(kind: string): KindConfig {
  return KIND_CONFIG[kind] ?? DEFAULT_KIND;
}

// ── Avatar: initials + a stable color hashed from the agent name ───────────

const AVATAR_PALETTE = ['#00D4FF', '#6366F1', '#A855F7', '#10B981', '#F59E0B', '#EF4444', '#EC4899', '#14B8A6'];

function initialsOf(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return '?';
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[1][0]).toUpperCase();
}

function colorOf(name: string): string {
  let hash = 0;
  for (let i = 0; i < name.length; i++) hash = (hash * 31 + name.charCodeAt(i)) >>> 0;
  return AVATAR_PALETTE[hash % AVATAR_PALETTE.length];
}

// ── Formatting ───────────────────────────────────────────────────────────────

function formatCost(cost: number): string {
  return cost < 0.01 ? `$${cost.toFixed(4)}` : `$${cost.toFixed(2)}`;
}

function formatMeta(m: CollaborationMessage): string {
  const parts: string[] = [m.to ? `${m.from_agent} → ${m.to}` : m.from_agent];
  if (m.latency_ms !== null) parts.push(`${m.latency_ms}ms`);
  if (m.tokens !== null)     parts.push(`${m.tokens} tok`);
  if (m.cost_usd !== null)   parts.push(formatCost(m.cost_usd));
  return parts.join(' · ');
}

function formatTimestamp(iso: string): string {
  try {
    return new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit' }).format(new Date(iso));
  } catch {
    return iso;
  }
}

// ── Live SSE event → CollaborationMessage ────────────────────────────────────

function fromLiveEvent(event: OrgEvent): CollaborationMessage {
  const payload = event.payload as Record<string, unknown>;
  const str = (v: unknown): string => (typeof v === 'string' ? v : '');
  const num = (v: unknown): number | null => (typeof v === 'number' ? v : null);
  const fromAgent = str(payload.from_agent) || 'unknown';
  return {
    // `payload.id` is the shared id `CollaborationTick` mints once and
    // threads through BOTH this SSE payload and the persisted `org_events`
    // row (`row.id` in `collaborationHistory` below) -- so a message
    // delivered live and later seen again in a history refetch dedupes to
    // one entry. Fall back to the old synthesized id for events that predate
    // this (or come from another source) so nothing crashes without it.
    id:         str(payload.id) || event.correlation_id || `${event.timestamp}-${fromAgent}`,
    from_agent: fromAgent,
    to:         str(payload.to),
    kind:       str(payload.kind) || 'update',
    message:    str(payload.message),
    latency_ms: num(payload.latency_ms),
    tokens:     num(payload.tokens),
    cost_usd:   num(payload.cost_usd),
    mission_id: typeof payload.mission_id === 'string' ? payload.mission_id : null,
    at:         event.timestamp,
  };
}

export function TeamChannel({ orgId, maxItems = MAX_MESSAGES, className }: TeamChannelProps) {
  const reduce = useReducedMotion();
  const [liveMessages, setLiveMessages] = useState<CollaborationMessage[]>([]);
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());

  const { data: history, isLoading, isError, error } = useQuery({
    queryKey: ['org-collaboration', orgId],
    queryFn:  () => situationApi.collaborationHistory(orgId, maxItems),
  });

  // Stable identity (mirrors OrgPage's handleOrgEvent) so
  // useOrgRealtimeManager doesn't resubscribe on every render.
  const onEvent = useCallback((event: OrgEvent) => {
    if (event.event_type !== ORG_EVENTS.COLLABORATION_MESSAGE) return;
    setLiveMessages(prev => [fromLiveEvent(event), ...prev].slice(0, maxItems));
  }, [maxItems]);

  useOrgRealtimeManager(orgId, { onEvent });

  // Live messages are always newer than the history snapshot, so prepending
  // them before deduping preserves newest-first order.
  const messages = useMemo(() => {
    const merged = [...liveMessages, ...(history ?? [])];
    const seen = new Set<string>();
    const deduped: CollaborationMessage[] = [];
    for (const m of merged) {
      if (seen.has(m.id)) continue;
      seen.add(m.id);
      deduped.push(m);
    }
    return deduped.slice(0, maxItems);
  }, [liveMessages, history, maxItems]);

  const toggleExpand = useCallback((id: string) => {
    setExpandedIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }, []);

  return (
    <section className={cn('flex flex-col gap-3', className)} aria-label="Team channel">
      <div className="flex items-center gap-2">
        <MessagesSquare className="h-3.5 w-3.5 text-[#00D4FF]" aria-hidden />
        <h3 className="text-[11px] font-medium uppercase tracking-[0.08em] text-[#475569]">
          Team Channel
        </h3>
      </div>

      {isLoading ? (
        <div className="flex items-center gap-2 py-3 text-[13px] text-[#475569]">
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
          Loading team chatter…
        </div>
      ) : isError ? (
        <div className="flex items-center gap-2 py-3 text-[13px] text-red-400">
          <AlertTriangle className="h-3.5 w-3.5 flex-shrink-0" aria-hidden />
          Failed to load team channel{error instanceof Error ? `: ${error.message}` : '.'}
        </div>
      ) : messages.length === 0 ? (
        <div className="py-4 text-center">
          <p className="text-[13px] text-[#475569]">No team chatter yet</p>
        </div>
      ) : (
        <ul className="space-y-1.5" aria-label="Team collaboration messages" aria-live="polite">
          <AnimatePresence mode="popLayout" initial={false}>
            {messages.map((m, i) => {
              const kc = kindConfig(m.kind);
              const isExpanded = expandedIds.has(m.id);
              const avatarColor = colorOf(m.from_agent);

              return (
                <motion.li
                  key={m.id}
                  layout
                  initial={reduce ? false : { opacity: 0, x: -8 }}
                  animate={{ opacity: 1, x: 0 }}
                  exit={{ opacity: 0, x: 4 }}
                  transition={{ type: 'spring', stiffness: 400, damping: 30, delay: reduce ? 0 : i * 0.02 }}
                  className="rounded-lg border border-[#1E2535] bg-[#0F1623] overflow-hidden"
                >
                  <button
                    type="button"
                    onClick={() => toggleExpand(m.id)}
                    aria-expanded={isExpanded}
                    aria-label={`${m.from_agent}, ${kc.label}: ${m.message}`}
                    className="w-full flex items-start gap-2.5 px-3 py-2 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00D4FF]/70 focus-visible:ring-inset"
                  >
                    <span
                      aria-hidden
                      className="flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-full text-[10px] font-semibold"
                      style={{ backgroundColor: `${avatarColor}22`, color: avatarColor }}
                    >
                      {initialsOf(m.from_agent)}
                    </span>

                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span
                          className="inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide"
                          style={{ backgroundColor: `${kc.color}1A`, color: kc.color }}
                        >
                          <span aria-hidden>{kc.icon}</span>
                          {kc.label}
                        </span>
                        <time dateTime={m.at} className="shrink-0 text-[11px] text-[#475569] tabular-nums">
                          {formatTimestamp(m.at)}
                        </time>
                        <ChevronDown
                          aria-hidden
                          className={cn(
                            'ml-auto h-3.5 w-3.5 flex-shrink-0 text-[#475569] transition-transform',
                            isExpanded && 'rotate-180',
                          )}
                        />
                      </div>
                      <p className="mt-0.5 text-[13px] text-[#E2E8F0] leading-snug">
                        {m.message}
                      </p>
                      <p className="mt-1 font-mono text-[11px] text-[#64748B]">
                        {formatMeta(m)}
                      </p>
                    </div>
                  </button>

                  {isExpanded && (
                    <div className="border-t border-[#1E2535] bg-[#0B0E14] px-3 py-2.5">
                      <dl className="grid grid-cols-[auto_1fr] gap-x-2 gap-y-1 text-[11px]">
                        <dt className="text-[#475569]">From → To</dt>
                        <dd className="text-[#94A3B8]">{m.from_agent} → {m.to || '—'}</dd>
                        <dt className="text-[#475569]">Kind</dt>
                        <dd className="text-[#94A3B8]">{m.kind}</dd>
                        {m.mission_id && (
                          <>
                            <dt className="text-[#475569]">Mission</dt>
                            <dd className="inline-flex items-center gap-1 text-[#94A3B8]">
                              <Link2 className="h-3 w-3 text-[#00D4FF]" aria-hidden />
                              mission {m.mission_id.slice(0, 8)}
                            </dd>
                          </>
                        )}
                      </dl>
                      <pre className="mt-2 max-h-48 overflow-auto rounded-md bg-black/30 p-2 font-mono text-[10px] leading-relaxed text-[#64748B]">
                        {JSON.stringify(m, null, 2)}
                      </pre>
                    </div>
                  )}
                </motion.li>
              );
            })}
          </AnimatePresence>
        </ul>
      )}
    </section>
  );
}

export default TeamChannel;
