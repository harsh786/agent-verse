import type { EvalSuiteResult } from '@/lib/api/client';

// Run lifecycle of a durable, worker-executed eval-suite run (MEM-53).

/** Poll a suite's runs every 5 s while one is in progress, otherwise not at all. */
export function runsRefetchInterval(runs: EvalSuiteResult[] | undefined): number | false {
  return (runs ?? []).some((r) => r.status === 'running') ? 5000 : false;
}

export function runStatusText(r: EvalSuiteResult): string {
  const total = r.progress?.total ?? r.total ?? (r.passed ?? 0) + (r.failed ?? 0);
  if (r.status === 'running') {
    const p = r.progress;
    return p
      ? `running · ${p.done}/${p.total} done · ${p.running} in flight`
      : 'running';
  }
  if (r.status === 'abandoned') return 'stalled · no worker progress, being resumed';
  if (r.status === 'failed') return `failed${r.error ? ` · ${r.error}` : ''}`;
  return `${r.status ?? 'completed'} · ${r.passed ?? 0}/${total} pass`;
}
