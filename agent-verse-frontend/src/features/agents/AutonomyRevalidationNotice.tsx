import { AlertTriangle, Loader2 } from 'lucide-react';
import type { AutonomyRevalidation } from '@/lib/api/client';

function percent(value: number | null | undefined): string | null {
  return typeof value === 'number' ? `${Math.round(value * 100)}%` : null;
}

/**
 * a05-F095-04 decision: a config change to a fully-autonomous agent demotes it to
 * bounded-autonomous and re-runs its eval suite against the new config. While
 * that run is under way (or when it failed) the agent detail says so; a promoted
 * or cancelled re-validation needs no notice.
 */
export function AutonomyRevalidationNotice({
  revalidation,
}: {
  revalidation?: AutonomyRevalidation | null;
}) {
  if (!revalidation) return null;
  const suite = revalidation.eval_suite_id ? ` (eval suite ${revalidation.eval_suite_id})` : '';
  if (revalidation.state === 'pending') {
    return (
      <div
        role="status"
        data-testid="autonomy-revalidation"
        className="mt-3 flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-200"
      >
        <Loader2 className="mt-0.5 h-4 w-4 shrink-0 animate-spin" aria-hidden="true" />
        <p>
          <span className="font-medium">Re-validating</span> — will return to fully-autonomous
          if the eval suite passes{suite}. Its configuration changed, so it runs
          bounded-autonomous until then.
        </p>
      </div>
    );
  }
  if (revalidation.state === 'failed') {
    const rate = percent(revalidation.pass_rate);
    const needed = percent(revalidation.min_pass_rate_required);
    return (
      <div
        role="alert"
        data-testid="autonomy-revalidation"
        className="mt-3 flex items-start gap-2 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-200"
      >
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
        <p>
          <span className="font-medium">Re-validation failed</span> — stays bounded-autonomous
          {suite}.
          {rate && needed ? ` Pass rate ${rate}, ${needed} required.` : ''}
          {revalidation.error ? ` ${revalidation.error}` : ''}
        </p>
      </div>
    );
  }
  return null;
}
