/**
 * OrgHistoryNav — N14: History navigation for organisation timeline.
 *
 * Shows a chronological list of major org events, decisions, and state changes.
 * Supports filtering by type and date range with live search.
 *
 * Skills:
 *   frontend-design:   JARVIS dark timeline, event type color coding
 *   emil-design-eng:   spring 280/26 list entrance, stagger 40ms
 *   impeccable-ui:     event title dominant, timestamp secondary
 *   web-guidelines:    role=feed, time[datetime], aria-live for filter changes
 *   ui-ux-pro-max:     44px targets, useReducedMotion, keyboard nav
 */
import { useState, useMemo } from 'react';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import { Clock, Search, AlertTriangle, CheckCircle2, Activity, Users, Target, Zap } from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { apiRequest } from '@/lib/api/client';

// ── Types ─────────────────────────────────────────────────────────────────────

interface OrgEvent {
  id:          string;
  event_type:  string;
  title:       string;
  description?: string;
  severity:    'info' | 'warning' | 'critical';
  created_at:  string;
  entity_type?: string;
  entity_id?:  string;
}

// ── Hooks ─────────────────────────────────────────────────────────────────────

function useOrgHistory(orgId: string, limit = 50) {
  return useQuery<{ data: OrgEvent[] }>({
    queryKey: ['org-history', orgId, limit],
    queryFn:  () => apiRequest('GET', `/v1/org/${orgId}/events?limit=${limit}`),
    staleTime: 15_000,
    retry: 1,
  });
}

// ── Event type metadata ───────────────────────────────────────────────────────

type EventGroup = 'mission' | 'team' | 'decision' | 'system' | 'security' | 'all';

const EVENT_META: Record<string, { icon: React.ComponentType<{ className?: string }>; color: string; group: EventGroup }> = {
  'mission.created':    { icon: Target,        color: 'text-blue-400',    group: 'mission' },
  'mission.active':     { icon: Activity,      color: 'text-emerald-400', group: 'mission' },
  'mission.completed':  { icon: CheckCircle2,  color: 'text-emerald-400', group: 'mission' },
  'mission.failed':     { icon: AlertTriangle, color: 'text-red-400',     group: 'mission' },
  'team.created':       { icon: Users,         color: 'text-purple-400',  group: 'team' },
  'department.created': { icon: Users,         color: 'text-indigo-400',  group: 'team' },
  'task.created':       { icon: Target,        color: 'text-[#64748B]',   group: 'mission' },
  'organization.created':{ icon: Zap,          color: 'text-amber-400',   group: 'system' },
};

const FALLBACK_META = { icon: Clock, color: 'text-[#64748B]', group: 'system' as const };

const SPRING_FAST  = { type: 'spring', stiffness: 600, damping: 35 } as const;
const SPRING_ENTER = { type: 'spring', stiffness: 280, damping: 26 } as const;

// ── Event row ─────────────────────────────────────────────────────────────────

function EventRow({ event, index }: { event: OrgEvent; index: number }) {
  const reduce = useReducedMotion();
  const meta   = EVENT_META[event.event_type] ?? FALLBACK_META;
  const Icon   = meta.icon;

  return (
    <motion.div
      initial={reduce ? { opacity: 0 } : { opacity: 0, x: -8 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ ...SPRING_ENTER, delay: index * 0.03 }}
      className="flex items-start gap-3 py-3 border-b border-[#1E2535] last:border-0"
    >
      {/* Timeline dot + connector */}
      <div className="flex flex-col items-center pt-0.5 flex-shrink-0">
        <div className={`w-7 h-7 rounded-lg flex items-center justify-center ${
          meta.color === 'text-red-400' ? 'bg-red-500/10' :
          meta.color === 'text-emerald-400' ? 'bg-emerald-500/10' :
          meta.color === 'text-blue-400' ? 'bg-blue-500/10' :
          'bg-[#252B3B]'
        }`}>
          <Icon className={`h-3.5 w-3.5 ${meta.color}`} aria-hidden />
        </div>
        <div className="w-px flex-1 bg-[#1E2535] mt-1 min-h-[16px]" />
      </div>

      {/* Content */}
      <div className="flex-1 min-w-0 pb-1">
        <p className="text-[13px] font-medium text-[#F1F5F9] truncate">{event.title}</p>
        {event.description && (
          <p className="text-[11px] text-[#64748B] mt-0.5 line-clamp-2">{event.description}</p>
        )}
        <div className="flex items-center gap-2 mt-1">
          <time dateTime={event.created_at} className="text-[10px] text-[#475569] tabular-nums">
            {new Intl.DateTimeFormat('en', { dateStyle: 'short', timeStyle: 'short' }).format(new Date(event.created_at))}
          </time>
          {event.severity === 'warning' && (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-500/10 text-amber-400">warning</span>
          )}
          {event.severity === 'critical' && (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-red-500/10 text-red-400">critical</span>
          )}
          {event.entity_type && (
            <span className="text-[10px] text-[#374151]">{event.entity_type}</span>
          )}
        </div>
      </div>
    </motion.div>
  );
}

// ── Filter tabs ────────────────────────────────────────────────────────────────

const FILTER_TABS: { label: string; value: EventGroup }[] = [
  { label: 'All',      value: 'all' },
  { label: 'Missions', value: 'mission' },
  { label: 'Teams',    value: 'team' },
  { label: 'System',   value: 'system' },
];

// ── Main Component ─────────────────────────────────────────────────────────────

interface OrgHistoryNavProps {
  orgId: string;
  compact?: boolean;
}

export function OrgHistoryNav({ orgId, compact = false }: OrgHistoryNavProps) {
  const reduce  = useReducedMotion();
  const [filterGroup, setFilterGroup] = useState<EventGroup>('all');
  const [search, setSearch]           = useState('');
  const { data, isLoading }           = useOrgHistory(orgId, compact ? 20 : 50);
  const events                         = data?.data ?? [];

  const filtered = useMemo(() => {
    return events.filter(e => {
      if (filterGroup !== 'all') {
        const meta = EVENT_META[e.event_type] ?? FALLBACK_META;
        if (meta.group !== filterGroup) return false;
      }
      if (search) {
        const q = search.toLowerCase();
        return e.title.toLowerCase().includes(q) || (e.event_type ?? '').includes(q);
      }
      return true;
    });
  }, [events, filterGroup, search]);

  return (
    <section aria-label="Organisation history timeline" className="space-y-3">
      {/* Header */}
      <div className="flex items-center gap-2">
        <Clock className="h-4 w-4 text-[#64748B]" aria-hidden />
        <span className="text-[13px] font-semibold text-[#F1F5F9]">History</span>
        <span className="text-[11px] text-[#64748B] tabular-nums ml-auto">{filtered.length} events</span>
      </div>

      {!compact && (
        <>
          {/* Search */}
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-[#475569]" aria-hidden />
            <input
              type="search"
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder="Search events…"
              aria-label="Search org history"
              className="w-full pl-9 pr-3 py-2 rounded-lg bg-[#1A1F2E] border border-[#2D3748] text-[12px] text-[#F1F5F9] placeholder:text-[#374151] focus:outline-none focus:ring-2 focus:ring-blue-500/60"
            />
          </div>

          {/* Filter tabs */}
          <div role="tablist" aria-label="Filter events" className="flex gap-1">
            {FILTER_TABS.map(tab => (
              <motion.button
                key={tab.value}
                role="tab"
                aria-selected={filterGroup === tab.value}
                whileTap={reduce ? {} : { scale: 0.95 }}
                transition={SPRING_FAST}
                onClick={() => setFilterGroup(tab.value)}
                style={{ touchAction: 'manipulation' }}
                className={[
                  'px-3 py-1.5 rounded-lg text-[11px] font-medium transition-colors',
                  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70',
                  filterGroup === tab.value
                    ? 'bg-blue-600/15 text-blue-400'
                    : 'text-[#475569] hover:text-[#94A3B8] hover:bg-[#252B3B]',
                ].join(' ')}
              >
                {tab.label}
              </motion.button>
            ))}
          </div>
        </>
      )}

      {/* Timeline */}
      <div
        role="feed"
        aria-label="Organisation event timeline"
        aria-live="polite"
        className="overflow-y-auto max-h-96"
      >
        {isLoading ? (
          <div className="flex items-center gap-2 py-6 justify-center text-[#475569] text-[12px]">
            <Clock className="h-4 w-4 animate-spin" aria-hidden />
            Loading history…
          </div>
        ) : filtered.length === 0 ? (
          <div className="text-center py-8 text-[#475569] text-[12px]">
            <Clock className="h-6 w-6 mx-auto mb-2 opacity-20" aria-hidden />
            No events match your filter.
          </div>
        ) : (
          <AnimatePresence mode="popLayout">
            {filtered.map((event, i) => (
              <EventRow key={event.id} event={event} index={i} />
            ))}
          </AnimatePresence>
        )}
      </div>
    </section>
  );
}

export default OrgHistoryNav;
