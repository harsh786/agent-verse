/**
 * Per-workflow access levels (mirrors app/workflow/permissions.py).
 *
 * viewer < runner < editor < admin. The backend returns the caller's level as
 * `access` on GET /workflows/{id}; an older backend omits it, in which case the
 * UI does not restrict anything (the server still enforces).
 */
export type WorkflowAccess = 'viewer' | 'runner' | 'editor' | 'admin';

export const ACCESS_LEVELS: ReadonlyArray<{ id: WorkflowAccess; label: string; description: string }> = [
  { id: 'viewer', label: 'Viewer', description: 'See the workflow, its runs and history.' },
  { id: 'runner', label: 'Runner', description: 'Viewer, plus trigger runs and stop/pause/retry them.' },
  { id: 'editor', label: 'Editor', description: 'Runner, plus edit, publish, unpublish and delete.' },
  { id: 'admin', label: 'Admin', description: 'Editor, plus manage who has access.' },
];

const RANK: Record<WorkflowAccess, number> = { viewer: 0, runner: 1, editor: 2, admin: 3 };

export function hasWorkflowAccess(
  access: string | null | undefined,
  needed: WorkflowAccess,
): boolean {
  if (access === undefined) return true; // backend did not report a level
  if (access === null || !(access in RANK)) return false;
  return RANK[access as WorkflowAccess] >= RANK[needed];
}

/** A readable message for a failed workflow mutation (403 → permission hint). */
export function workflowErrorMessage(err: unknown, action: string): string {
  const status = (err as { status?: number } | null)?.status;
  if (status === 403) return `You don't have permission to ${action} this workflow.`;
  const message = err instanceof Error ? err.message : '';
  return message ? `Could not ${action}: ${message}` : `Could not ${action} the workflow.`;
}
