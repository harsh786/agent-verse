/**
 * ActivityFeed — real-time org event stream with staggered spring entries.
 *
 * Skills:
 *   - emil-design-eng: stagger 60ms, spring layout animation on new events
 *   - impeccable-ui:   time as tertiary, event title as primary
 *   - web-guidelines:  aria-live polite, tabular-nums on timestamps
 *   - ui-ux-pro-max:   empty state with action, reduced motion
 */
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import { Activity } from 'lucide-react';
import { cn } from '@/lib/utils';
import { useOrgEvents } from '../hooks/useOrg';

interface ActivityFeedProps {
  orgId:      string;
  maxItems?:  number;
  className?: string;
}

const SEVERITY_DOT: Record<string, string> = {
  info:     'bg-blue-400',
  success:  'bg-emerald-400',
  warning:  'bg-amber-400',
  error:    'bg-rose-400',
  critical: 'bg-rose-500',
};

const itemVariants = {
  hidden:  { opacity: 0, x: -8 },
  visible: { opacity: 1, x: 0 },
  exit:    { opacity: 0, x: 4 },
};

export function ActivityFeed({ orgId, maxItems = 20, className }: ActivityFeedProps) {
  const reduce = useReducedMotion();
  const { data: events, isLoading } = useOrgEvents(orgId);
  const displayEvents = (events ?? []).slice(0, maxItems);

  return (
    <section
      className={cn('flex flex-col gap-3', className)}
      aria-label="Organisation activity feed"
    >
      {/* Header */}
      <div className="flex items-center gap-2">
        <Activity className="h-3.5 w-3.5 text-blue-400" aria-hidden />
        <h3 className="text-[11px] font-medium uppercase tracking-[0.08em] text-[#475569]">
          Activity
        </h3>
        {/* web-guidelines: aria-live for real-time updates */}
        <span aria-live="polite" aria-atomic="false" className="sr-only">
          {displayEvents.length > 0 && `${displayEvents.length} recent events`}
        </span>
      </div>

      {/* Feed */}
      {isLoading ? (
        <SkeletonFeed />
      ) : displayEvents.length === 0 ? (
        <EmptyFeed />
      ) : (
        <ul
          className="space-y-1"
          aria-label="Recent activity"
        >
          <AnimatePresence mode="popLayout" initial={false}>
            {displayEvents.map((event, i) => {
              const dotColor = SEVERITY_DOT[event.severity ?? 'info'] ?? SEVERITY_DOT.info;
              return (
                <motion.li
                  key={event.id}
                  layout
                  variants={reduce ? {} : itemVariants}
                  initial="hidden"
                  animate="visible"
                  exit="exit"
                  // emil-design-eng: stagger 60ms between items
                  transition={{
                    type: 'spring',
                    stiffness: 400,
                    damping: 30,
                    delay: reduce ? 0 : i * 0.04,
                  }}
                  className="group flex items-start gap-2.5 py-1.5"
                >
                  {/* Dot */}
                  <span className={cn('mt-1.5 h-1.5 w-1.5 rounded-full shrink-0', dotColor)} aria-hidden />

                  {/* Content */}
                  <div className="flex-1 min-w-0">
                    <p className="text-[13px] text-[#94A3B8] leading-snug truncate">
                      {event.title || event.event_type.replace(/_/g, ' ')}
                    </p>
                    {event.description && (
                      <p className="text-[11px] text-[#475569] truncate mt-0.5">
                        {event.description}
                      </p>
                    )}
                  </div>

                  {/* Time — tertiary (impeccable-ui: clearly de-emphasized) */}
                  <time
                    dateTime={event.created_at}
                    className="shrink-0 text-[11px] text-[#475569] tabular-nums mt-0.5"
                  >
                    {new Intl.DateTimeFormat(undefined, {
                      hour: '2-digit', minute: '2-digit',
                    }).format(new Date(event.created_at))}
                  </time>
                </motion.li>
              );
            })}
          </AnimatePresence>
        </ul>
      )}
    </section>
  );
}

function SkeletonFeed() {
  return (
    <div className="space-y-2 animate-pulse">
      {[1, 2, 3, 4].map(i => (
        <div key={i} className="flex items-center gap-2.5">
          <div className="h-1.5 w-1.5 rounded-full bg-[#252B3B] shrink-0" />
          <div className="flex-1 h-3 rounded bg-[#252B3B]" />
          <div className="h-3 w-10 rounded bg-[#252B3B] shrink-0" />
        </div>
      ))}
    </div>
  );
}

function EmptyFeed() {
  return (
    <div className="py-4 text-center">
      <p className="text-[13px] text-[#475569]">No activity yet</p>
      <p className="text-[11px] text-[#3D4A5C] mt-1">Events will appear here as missions run</p>
    </div>
  );
}
