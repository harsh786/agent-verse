/**
 * NarrationTicker — a human-readable running narration of what the org is doing.
 *
 * Translates the raw org SSE event stream into short, plain-English lines
 * ("Launched 'Q3 competitor scan'", "ComplianceAgent: flagging vendor risk…")
 * so an operator can read what's happening without decoding event_type
 * strings or JSON payloads. Deliberately noise-filtered: routine bookkeeping
 * events (memory/artifact/model/policy/health/digest churn, decisions the
 * brain executed normally) are not narrated here — BrainFeed/ActivityFeed
 * already cover those in full detail. This is the "what is my org doing
 * right now, in one sentence" surface.
 *
 * Task 12 (Situation Room craft pass) — see
 * .superpowers/sdd/2026-09-14-situation-room-ux/task-12-brief.md.
 */
import { useCallback, useRef, useState } from 'react';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import { Radio } from 'lucide-react';
import { cn } from '@/lib/utils';
import { useOrgRealtimeManager, ORG_EVENTS, type OrgEvent } from '../OrgRealtimeManager';

export type NarrationTone = 'info' | 'warn' | 'critical';

export interface NarrationLine {
  id:   string;
  text: string;
  at:   string; // ISO timestamp, from the event
  tone: NarrationTone;
}

interface NarrationTickerProps {
  orgId:      string;
  className?: string;
  /** Max lines retained (oldest dropped off the end). Default 30. */
  maxItems?:  number;
}

const TRUNCATE_AT = 100;

function truncate(s: string, n = TRUNCATE_AT): string {
  const trimmed = s.trim();
  return trimmed.length > n ? `${trimmed.slice(0, n - 1)}…` : trimmed;
}

function str(payload: Record<string, unknown>, key: string): string | undefined {
  const v = payload[key];
  return typeof v === 'string' && v.trim() !== '' ? v : undefined;
}

/** First non-empty string field among several candidate keys — the backend
 *  hasn't been perfectly consistent about naming ("title" vs "gate" vs
 *  "action" for approval prompts, etc.), so try a short list defensively. */
function firstStr(payload: Record<string, unknown>, keys: string[]): string | undefined {
  for (const key of keys) {
    const v = str(payload, key);
    if (v) return v;
  }
  return undefined;
}

/** True when a recorded decision reflects something the brain declined to do
 *  rather than something it executed normally — mirrors BrainFeed's
 *  `isHeldBack`. Only held-back decisions are narrated; routine "executed"
 *  decisions fire on every tick and would drown out everything else. */
function isHeldBackDecision(payload: Record<string, unknown>): boolean {
  const verdict = (str(payload, 'guardrail_verdict') ?? '').toLowerCase();
  const kind     = (str(payload, 'kind') ?? '').toLowerCase();
  const action   = (str(payload, 'action') ?? '').toLowerCase();
  return verdict.includes('block') || verdict.includes('deny') || verdict.includes('reject')
    || kind.includes('block') || action.includes('block');
}

/**
 * Pure: turn one OrgEvent into a single human-readable narration line, or
 * null to skip it (noise, or an event type this ticker doesn't narrate).
 * Exported for direct unit testing independent of the SSE plumbing.
 */
export function narrate(event: OrgEvent): string | null {
  const p = (event.payload ?? {}) as Record<string, unknown>;

  switch (event.event_type) {
    case ORG_EVENTS.MISSION_CREATED: {
      const title = firstStr(p, ['title', 'mission_title']);
      return title ? `Proposed mission '${title}'` : 'Proposed a new mission';
    }
    case ORG_EVENTS.MISSION_STARTED: {
      const title = firstStr(p, ['title', 'mission_title']);
      return title ? `Launched '${title}'` : 'Launched a mission';
    }
    case ORG_EVENTS.MISSION_COMPLETED: {
      const title = firstStr(p, ['title', 'mission_title']);
      return title ? `Completed '${title}'` : 'Completed a mission';
    }
    case ORG_EVENTS.MISSION_FAILED: {
      const title = firstStr(p, ['title', 'mission_title']);
      return title ? `Failed '${title}'` : 'A mission failed';
    }
    case ORG_EVENTS.MISSION_BLOCKED: {
      const title = firstStr(p, ['title', 'mission_title']);
      return title ? `Blocked '${title}'` : 'A mission is blocked';
    }
    case ORG_EVENTS.MISSION_PAUSED: {
      const title = firstStr(p, ['title', 'mission_title']);
      return title ? `Paused '${title}'` : 'Paused a mission';
    }
    case ORG_EVENTS.TEAM_FORMED: {
      const dept = firstStr(p, ['department', 'dept_name']);
      return dept ? `Team assembled in ${dept}` : 'A new team was assembled';
    }
    case ORG_EVENTS.AGENT_ACTIVATED: {
      const role = firstStr(p, ['role', 'label']);
      return role ? `${role} activated` : 'A new agent activated';
    }
    case ORG_EVENTS.AGENT_BLOCKED: {
      const role = firstStr(p, ['role', 'label']);
      return role ? `${role} is blocked` : 'An agent is blocked';
    }
    case ORG_EVENTS.AGENT_ESCALATED: {
      const role = firstStr(p, ['role', 'label']);
      return role ? `${role} escalated for help` : 'An agent escalated for help';
    }
    case ORG_EVENTS.APPROVAL_REQUESTED: {
      const title = firstStr(p, ['title', 'gate', 'action']);
      return title ? `Awaiting approval: ${title}` : 'Awaiting your approval';
    }
    case ORG_EVENTS.APPROVAL_GRANTED: {
      const title = firstStr(p, ['title', 'gate', 'action']);
      return title ? `Approved: ${title}` : 'Approval granted';
    }
    case ORG_EVENTS.APPROVAL_REJECTED: {
      const title = firstStr(p, ['title', 'gate', 'action']);
      return title ? `Rejected: ${title}` : 'Approval rejected';
    }
    case ORG_EVENTS.BUDGET_THRESHOLD_80:
      return 'Daily budget at 80%';
    case ORG_EVENTS.BUDGET_THRESHOLD_95:
      return 'Daily budget at 95%';
    case ORG_EVENTS.BUDGET_EXCEEDED:
      return 'Daily budget exceeded';
    case ORG_EVENTS.COLLABORATION_MESSAGE: {
      const from    = firstStr(p, ['from_agent']) ?? 'An agent';
      const message = firstStr(p, ['message']);
      return message ? `${from}: ${truncate(message)}` : null;
    }
    case ORG_EVENTS.DECISION_RECORDED: {
      if (!isHeldBackDecision(p)) return null; // executed normally — not narration-worthy noise
      const reason = firstStr(p, ['reason', 'rationale']);
      return reason ? `Held back: ${truncate(reason)}` : 'Held back a decision';
    }
    case ORG_EVENTS.EMERGENCY_STOP:
      return 'EMERGENCY STOP';
    case ORG_EVENTS.EMERGENCY_RESUMED:
      return 'Autonomy resumed';
    default:
      return null; // team.forming/disbanded, agent.idle/completed_task/failed_task,
      // approval.timeout, model.*, memory.*, artifact.*, health.*, digest.ready,
      // policy.violation — deliberately not narrated here (noise, or covered
      // elsewhere).
  }
}

/** Visual urgency for a line — derived from the event type, with the
 *  "Held back" text itself as a fallback signal (kept in sync with narrate()
 *  above, since only that string is produced by the held-back branch). */
function toneFor(event: OrgEvent, text: string): NarrationTone {
  if (event.event_type === ORG_EVENTS.EMERGENCY_STOP) return 'critical';
  if (
    event.event_type === ORG_EVENTS.MISSION_FAILED ||
    event.event_type === ORG_EVENTS.MISSION_BLOCKED ||
    event.event_type === ORG_EVENTS.AGENT_BLOCKED ||
    event.event_type === ORG_EVENTS.APPROVAL_REQUESTED ||
    event.event_type === ORG_EVENTS.APPROVAL_REJECTED ||
    event.event_type === ORG_EVENTS.BUDGET_EXCEEDED ||
    text.startsWith('Held back')
  ) {
    return 'warn';
  }
  return 'info';
}

const TONE_DOT: Record<NarrationTone, string> = {
  info:     'bg-[#00D4FF]',
  warn:     'bg-amber-400',
  critical: 'bg-red-500',
};

const TONE_TEXT: Record<NarrationTone, string> = {
  info:     'text-[#94A3B8]',
  warn:     'text-amber-300',
  critical: 'text-red-400 font-bold',
};

function formatTime(iso: string): string {
  try {
    return new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit' }).format(new Date(iso));
  } catch {
    return '';
  }
}

export function NarrationTicker({ orgId, className, maxItems = 30 }: NarrationTickerProps) {
  const reduce = useReducedMotion();
  const [lines, setLines] = useState<NarrationLine[]>([]);
  const counterRef = useRef(0);

  // Stable identity: useOrgRealtimeManager only re-subscribes when
  // orgId/apiKey change (see OrgRealtimeManager.ts), so `onEvent` must not
  // be recreated every render — maxItems is read inside the updater instead
  // of captured in the closure.
  const maxItemsRef = useRef(maxItems);
  maxItemsRef.current = maxItems;

  const onEvent = useCallback((event: OrgEvent) => {
    const text = narrate(event);
    if (!text) return;
    counterRef.current += 1;
    const line: NarrationLine = {
      id:   `${event.timestamp}-${counterRef.current}`,
      text,
      at:   event.timestamp,
      tone: toneFor(event, text),
    };
    setLines((prev) => [line, ...prev].slice(0, maxItemsRef.current));
  }, []);

  useOrgRealtimeManager(orgId, { onEvent });

  return (
    <section className={cn('flex flex-col gap-2', className)} aria-label="Mission narration">
      <div className="flex items-center gap-2">
        <Radio className="h-3.5 w-3.5 text-[#00D4FF]" aria-hidden />
        <h3 className="text-[11px] font-medium uppercase tracking-[0.08em] text-[#475569]">
          Narration
        </h3>
      </div>

      {lines.length === 0 ? (
        <p className="text-[12px] text-[#475569] italic py-1">Quiet — no activity yet.</p>
      ) : (
        <ul
          className="flex flex-col gap-1 max-h-48 overflow-y-auto pr-1"
          aria-live="polite"
          aria-atomic="false"
          aria-label="Recent narration"
        >
          <AnimatePresence initial={false} mode="popLayout">
            {lines.map((line) => (
              <motion.li
                key={line.id}
                layout
                initial={reduce ? false : { opacity: 0, y: -6 }}
                animate={{ opacity: 1, y: 0 }}
                exit={reduce ? undefined : { opacity: 0 }}
                transition={{ duration: 0.2 }}
                className="flex items-start gap-2 text-[12px] leading-snug"
              >
                <span className={cn('mt-1.5 h-1.5 w-1.5 rounded-full shrink-0', TONE_DOT[line.tone])} aria-hidden />
                <span className={cn('flex-1 min-w-0 break-words', TONE_TEXT[line.tone])}>{line.text}</span>
                <time dateTime={line.at} className="shrink-0 text-[10px] text-[#334155] tabular-nums mt-0.5">
                  {formatTime(line.at)}
                </time>
              </motion.li>
            ))}
          </AnimatePresence>
        </ul>
      )}
    </section>
  );
}

export default NarrationTicker;
