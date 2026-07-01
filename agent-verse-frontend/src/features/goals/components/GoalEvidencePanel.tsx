import { CheckCircle2, ShieldCheck, Wrench, XCircle } from 'lucide-react';
import { useId } from 'react';
import type { ResultArtifact } from '../resultArtifact';

function evidenceText(value: unknown): string {
  return String(value ?? '');
}

export function GoalEvidencePanel({ artifact }: { artifact: ResultArtifact }) {
  const headingId = useId();
  const tools = artifact.evidence.tools ?? [];
  const hasEvidence = Boolean(
    artifact.evidence.verification || artifact.evidence.query || artifact.evidence.connector || tools.length
  );

  return (
    <section aria-labelledby={headingId} className="rounded-2xl border bg-card p-5 shadow-sm">
      <h3 className="flex items-center gap-2 text-base font-semibold" id={headingId}>
        <ShieldCheck className="h-4 w-4 text-emerald-500" aria-hidden="true" />
        Evidence
      </h3>
      {!hasEvidence && (
        <p className="mt-3 text-sm text-muted-foreground">No evidence available yet.</p>
      )}
      {artifact.evidence.verification && (
        <div className="mt-3 rounded-xl bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
          <div className="text-xs font-semibold uppercase tracking-wide text-emerald-700">
            Verification
          </div>
          <p className="mt-1">{artifact.evidence.verification}</p>
        </div>
      )}
      {(artifact.evidence.query || artifact.evidence.connector) && (
        <dl className="mt-4 grid gap-3 text-sm md:grid-cols-2">
          {artifact.evidence.query && (
            <div className="rounded-xl border bg-muted/20 px-3 py-2">
              <dt className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Query
              </dt>
              <dd className="mt-1 break-words font-mono text-xs text-foreground">
                {artifact.evidence.query}
              </dd>
            </div>
          )}
          {artifact.evidence.connector && (
            <div className="rounded-xl border bg-muted/20 px-3 py-2">
              <dt className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Connector
              </dt>
              <dd className="mt-1 break-words text-foreground">{artifact.evidence.connector}</dd>
            </div>
          )}
        </dl>
      )}
      {tools.length > 0 && (
        <div className="mt-4">
          <h4 className="text-sm font-semibold">Tool evidence</h4>
          <ul aria-label="Tool evidence" className="mt-2 space-y-2">
            {tools.map((tool, index) => {
              const success = tool.success !== false;
              const StatusIcon = success ? CheckCircle2 : XCircle;
              return (
                <li
                  key={index}
                  className="flex min-w-0 flex-col gap-2 rounded-xl border px-3 py-2 text-sm sm:flex-row sm:items-center sm:justify-between"
                >
                  <div className="min-w-0">
                    <span className="flex min-w-0 items-center gap-2 font-medium">
                      <Wrench
                        className="h-4 w-4 shrink-0 text-muted-foreground"
                        aria-hidden="true"
                      />
                      <span className="min-w-0 break-words">{evidenceText(tool.name)}</span>
                    </span>
                    {typeof tool.server_id === 'string' && tool.server_id.length > 0 && (
                      <span className="mt-1 block break-words text-xs text-muted-foreground">
                        Server ID:{' '}
                        <span className="break-words">{evidenceText(tool.server_id)}</span>
                      </span>
                    )}
                  </div>
                  <span
                    className={`inline-flex w-fit items-center gap-1 rounded-full px-2 py-0.5 text-xs font-semibold ${success ? 'bg-emerald-50 text-emerald-700' : 'bg-rose-50 text-rose-700'}`}
                  >
                    <StatusIcon className="h-3.5 w-3.5" aria-hidden="true" />
                    {success ? 'Success' : 'Failed'}
                  </span>
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </section>
  );
}
