/**
 * TeamChannel — live cross-agent/department collaboration chat.
 *
 * Consumes `org.collaboration.message` events off the org realtime stream
 * (org.collaboration_enabled brain setting) and renders them as a running
 * chat log: `payload.lead` (the agent/department speaking) + `payload.message`.
 * Purely client-side/ephemeral — no history endpoint, so the list starts
 * empty on mount and only grows from live events for the rest of the session.
 */
import { useCallback, useState } from 'react';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import { MessagesSquare } from 'lucide-react';
import { cn } from '@/lib/utils';
import { useOrgRealtimeManager, ORG_EVENTS, type OrgEvent } from '../OrgRealtimeManager';

interface TeamChannelProps {
  orgId:      string;
  maxItems?:  number;
  className?: string;
}

interface ChatMessage {
  id:      string;
  lead:    string;
  message: string;
  at:      string;
}

const MAX_MESSAGES = 50;

export function TeamChannel({ orgId, maxItems = MAX_MESSAGES, className }: TeamChannelProps) {
  const reduce = useReducedMotion();
  const [messages, setMessages] = useState<ChatMessage[]>([]);

  // Stable identity (mirrors OrgPage's handleOrgEvent) so
  // useOrgRealtimeManager doesn't resubscribe on every render.
  const onEvent = useCallback((event: OrgEvent) => {
    if (event.event_type !== ORG_EVENTS.COLLABORATION_MESSAGE) return;
    const payload = event.payload as Record<string, unknown>;
    const lead    = String(payload.lead ?? 'Unknown');
    const message = String(payload.message ?? '');
    setMessages((prev) => [
      { id: `${event.timestamp}-${prev.length}`, lead, message, at: event.timestamp },
      ...prev,
    ].slice(0, maxItems));
  }, [maxItems]);

  useOrgRealtimeManager(orgId, { onEvent });

  return (
    <section className={cn('flex flex-col gap-3', className)} aria-label="Team channel">
      <div className="flex items-center gap-2">
        <MessagesSquare className="h-3.5 w-3.5 text-[#00D4FF]" aria-hidden />
        <h3 className="text-[11px] font-medium uppercase tracking-[0.08em] text-[#475569]">
          Team Channel
        </h3>
        <span aria-live="polite" aria-atomic="false" className="sr-only">
          {messages.length > 0 && `${messages.length} team messages`}
        </span>
      </div>

      {messages.length === 0 ? (
        <div className="py-4 text-center">
          <p className="text-[13px] text-[#475569]">No team chatter yet</p>
        </div>
      ) : (
        <ul className="space-y-1.5" aria-label="Team collaboration messages">
          <AnimatePresence mode="popLayout" initial={false}>
            {messages.map((m, i) => (
              <motion.li
                key={m.id}
                layout
                initial={reduce ? false : { opacity: 0, x: -8 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: 4 }}
                transition={{ type: 'spring', stiffness: 400, damping: 30, delay: reduce ? 0 : i * 0.02 }}
                className="rounded-lg bg-[#0F1623] border border-[#1E2535] px-3 py-2"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="text-[12px] font-semibold text-[#00D4FF] truncate">
                    {m.lead}
                  </span>
                  <time dateTime={m.at} className="shrink-0 text-[11px] text-[#475569] tabular-nums">
                    {new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit' }).format(new Date(m.at))}
                  </time>
                </div>
                <p className="mt-0.5 text-[13px] text-[#94A3B8] leading-snug">
                  {m.message}
                </p>
              </motion.li>
            ))}
          </AnimatePresence>
        </ul>
      )}
    </section>
  );
}

export default TeamChannel;
