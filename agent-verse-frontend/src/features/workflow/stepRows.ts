import type { WEStepResult } from '../../lib/api/client';

/**
 * One row per step for the run timeline. A gate that suspended (waiting_hitl)
 * and then resumed is persisted as two rows, so the timeline used to show the
 * decided gate twice. The latest row wins, at the position where the step
 * first appeared.
 */
export function latestStepRows(steps: WEStepResult[]): WEStepResult[] {
  const order: string[] = [];
  const latest = new Map<string, WEStepResult>();
  for (const step of steps) {
    if (!latest.has(step.step_id)) order.push(step.step_id);
    latest.set(step.step_id, step);
  }
  return order.map((id) => latest.get(id)!);
}
