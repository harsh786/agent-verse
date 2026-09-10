/**
 * taskHandoffs — pure "what just handed off to whom" computation.
 *
 * WS-7 item 2 (task-handoff animation). The backend does not (yet) emit a
 * literal task.moved{from,to} event — `OrgTask` only carries a single
 * `assigned_to` — so per the WS-7 brief's own fallback ("task event with
 * from/to, OR mission progress") we derive handoffs from real mission
 * progress: the moment a task genuinely flips to 'completed' for one agent,
 * if another task in the *same* mission is actively assigned to a
 * *different* agent, that's a real, observable handoff of work within the
 * mission — not a fabricated one. This function is pure so the transition
 * logic can be tested without React, SSE, or animation timing.
 */
import type { OrgTask } from '../types';

export interface TaskHandoff {
  /** Stable id for this handoff instance — used as a React key + de-dupe key. */
  id:          string;
  missionId:   string;
  fromAgentId: string;
  toAgentId:   string;
  fromTaskId:  string;
  toTaskId:    string;
  /** What to show on the traveling packet — the task now moving to `toAgentId`. */
  label:       string;
}

const IN_FLIGHT_STATUSES: ReadonlySet<OrgTask['status']> = new Set([
  'assigned', 'running', 'queued',
]);

/**
 * Diff two task snapshots (e.g. consecutive `useOrgTasks` results) and return
 * the handoffs implied by tasks that just completed since `prev`.
 */
export function computeTaskHandoffs(prev: OrgTask[], next: OrgTask[]): TaskHandoff[] {
  const prevById = new Map(prev.map(t => [t.id, t]));
  const handoffs: TaskHandoff[] = [];

  for (const task of next) {
    if (task.status !== 'completed') continue;
    if (!task.mission_id || !task.assigned_to) continue;

    const before = prevById.get(task.id);
    // Only a genuine transition counts — a task that was already completed
    // last time we looked isn't a new handoff.
    if (!before || before.status === 'completed') continue;

    const candidate = next.find(t =>
      t.id !== task.id &&
      t.mission_id === task.mission_id &&
      !!t.assigned_to &&
      t.assigned_to !== task.assigned_to &&
      IN_FLIGHT_STATUSES.has(t.status),
    );
    if (!candidate?.assigned_to) continue;

    handoffs.push({
      id:          `${task.id}->${candidate.id}`,
      missionId:   task.mission_id,
      fromAgentId: task.assigned_to,
      toAgentId:   candidate.assigned_to,
      fromTaskId:  task.id,
      toTaskId:    candidate.id,
      label:       candidate.title,
    });
  }

  return handoffs;
}
