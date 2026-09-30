/**
 * Turn a failed eval read (GET /goals/:id/eval or /eval/suggestions) into the
 * message the operator should see. A 404 (the goal does not exist for this
 * tenant) and a 503 (the scorecard store could not be read) mean different
 * things and must not both collapse into "no evaluation yet".
 */
import { ApiError } from '@/lib/api/client';

export interface EvalLoadErrorView {
  title: string;
  detail: string;
}

export function evalLoadError(error: unknown): EvalLoadErrorView {
  const status = error instanceof ApiError ? error.status : undefined;
  if (status === 404) {
    return {
      title: 'Goal not found',
      detail: 'This goal does not exist or is not visible to your workspace.',
    };
  }
  if (status === 503) {
    return {
      title: 'Evaluation temporarily unavailable',
      detail: 'The scorecard store could not be read. Try again in a moment.',
    };
  }
  return {
    title: 'Could not load the evaluation',
    detail: error instanceof Error && error.message ? error.message : 'Unexpected error.',
  };
}
