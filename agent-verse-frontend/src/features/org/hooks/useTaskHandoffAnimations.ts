/**
 * useTaskHandoffAnimations — WS-7 item 2 (task-handoff animation), live wiring.
 *
 * Reads the org's real task list (`useOrgTasks`, already refetched/invalidated
 * by real `org.agent.*` SSE events via OrgRealtimeManager), diffs consecutive
 * snapshots with the pure `computeTaskHandoffs`, and exposes the handoffs that
 * are currently "in flight" so a component can render a traveling packet along
 * the edge between the two real agent ids. Never driven by a timer — a handoff
 * only ever appears because a real task genuinely completed.
 *
 * The diff runs during render (not in a `useEffect`) — the standard React
 * "adjust state while rendering" pattern — so a fast-arriving update can
 * never coalesce past an intermediate snapshot and silently skip a real
 * transition (React may batch several state updates into one effect pass,
 * but it never skips a render whose inputs actually changed).
 */
import { useEffect, useRef, useState } from 'react';
import { useOrgTasks } from './useOrg';
import { computeTaskHandoffs, type TaskHandoff } from '../lib/taskHandoffs';
import type { OrgTask } from '../types';

/** How long a handoff stays "active" (i.e. rendered) after it's detected. */
export const HANDOFF_ANIMATION_MS = 1400;

export function useTaskHandoffAnimations(
  orgId: string | null | undefined,
  ttlMs: number = HANDOFF_ANIMATION_MS,
): TaskHandoff[] {
  const { data } = useOrgTasks(orgId ?? undefined, {});
  const tasks = ((data as { data?: OrgTask[] } | undefined)?.data ?? []);

  // Nothing to diff against on the very first snapshot — that's existing
  // state, not a live transition, so `prevTasks` starts equal to `tasks`.
  const [prevTasks, setPrevTasks] = useState<OrgTask[]>(tasks);
  const [queued, setQueued] = useState<TaskHandoff[]>([]);

  if (tasks !== prevTasks) {
    const newHandoffs = computeTaskHandoffs(prevTasks, tasks);
    setPrevTasks(tasks);
    if (newHandoffs.length > 0) {
      setQueued(current => [...current, ...newHandoffs]);
    }
  }

  const [active, setActive] = useState<TaskHandoff[]>([]);

  // Pending expiry timers, keyed only for bulk cleanup on unmount. This
  // effect intentionally does NOT return a per-run cleanup: `setQueued([])`
  // below changes `queued`'s identity on the very next render, and an
  // effect-level cleanup tied to that same dependency would cancel the timer
  // it had just scheduled before it ever fires.
  const timersRef = useRef<ReturnType<typeof setTimeout>[]>([]);

  useEffect(() => {
    if (queued.length === 0) return;
    const ids = queued.map(h => h.id);
    setActive(current => [...current, ...queued]);
    setQueued([]); // consumed — don't grow this list forever
    const timer = setTimeout(() => {
      setActive(current => current.filter(h => !ids.includes(h.id)));
    }, ttlMs);
    timersRef.current.push(timer);
  }, [queued, ttlMs]);

  useEffect(() => () => {
    timersRef.current.forEach(clearTimeout);
  }, []);

  return active;
}
