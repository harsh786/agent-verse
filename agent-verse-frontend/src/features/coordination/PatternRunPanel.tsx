import { useMutation } from '@tanstack/react-query';
import { Play } from 'lucide-react';
import { FormEvent, useRef, useState } from 'react';
import { coordinationApi, type PatternName, type PatternRunResult } from './coordinationApi';

const PATTERNS: Array<[PatternName, string]> = [
  ['magentic', 'Magentic (ledger + human review)'],
  ['mixture_of_agents', 'Mixture of agents'],
  ['camel', 'CAMEL role-play'],
  ['generative_agents', 'Generative agent'],
  ['decentralized_swarm', 'Decentralized swarm'],
  ['market_auction', 'Sealed-bid auction'],
];

export function PatternRunPanel({ sessionId, onChanged }: { sessionId: string; onChanged: () => void }) {
  const [pattern, setPattern] = useState<PatternName>('mixture_of_agents');
  const [objective, setObjective] = useState('');
  const [result, setResult] = useState<PatternRunResult | null>(null);
  // One key per intended run: a retry after an error resumes the same run; a new
  // pattern/objective or a finished run starts a new one.
  const runKey = useRef(crypto.randomUUID());
  const run = useMutation({
    mutationFn: () => coordinationApi.runPattern(sessionId, pattern, objective.trim(), runKey.current),
    onSuccess: (data) => { setResult(data); runKey.current = crypto.randomUUID(); onChanged(); },
  });
  const review = useMutation({
    mutationFn: (approved: boolean) => {
      const token = result?.human_review?.token;
      if (!token) throw new Error('No review pending');
      return coordinationApi.submitMagenticReview(sessionId, token, approved);
    },
    onSuccess: (data) => { setResult(data.run ?? null); onChanged(); },
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    if (objective.trim()) run.mutate();
  }

  return (
    <section aria-labelledby="pattern-run-heading">
      <h2 id="pattern-run-heading" className="font-semibold">Run a pattern</h2>
      <form onSubmit={submit} className="mt-3 space-y-2">
        <label htmlFor="pattern-select" className="sr-only">Pattern</label>
        <select id="pattern-select" value={pattern} onChange={(event) => { setPattern(event.target.value as PatternName); runKey.current = crypto.randomUUID(); }} className="w-full rounded-md border bg-background px-3 py-2 text-sm">
          {PATTERNS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
        <label htmlFor="pattern-objective" className="sr-only">Objective</label>
        <textarea id="pattern-objective" value={objective} onChange={(event) => { setObjective(event.target.value); runKey.current = crypto.randomUUID(); }} maxLength={4_000} rows={3} placeholder="Objective for the agents" className="w-full rounded-md border bg-background px-3 py-2 text-sm" />
        <button type="submit" disabled={run.isPending || !objective.trim()} className="inline-flex min-h-11 items-center gap-2 rounded-md bg-primary px-3 py-2 text-sm text-primary-foreground disabled:opacity-50">
          <Play className="h-4 w-4" aria-hidden="true" /> {run.isPending ? 'Running…' : 'Run'}
        </button>
      </form>
      {run.error && <p role="alert" className="mt-2 text-sm text-red-600">{run.error instanceof Error ? run.error.message : 'The pattern run failed.'}</p>}
      {result && (
        <div aria-live="polite" className="mt-3 rounded-md border p-3 text-sm">
          <p className="font-mono text-xs text-muted-foreground">{result.pattern} · {result.phase}{result.terminal_reason ? ` (${result.terminal_reason})` : ''} · {result.llm_calls} LLM calls</p>
          {result.safe_output && <p className="mt-2 whitespace-pre-wrap">{result.safe_output}</p>}
          {result.human_review && (
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <span>Human review required: the team exhausted its replans.</span>
              <button type="button" disabled={review.isPending} onClick={() => review.mutate(true)} className="min-h-11 rounded-md bg-primary px-3 py-1.5 text-primary-foreground disabled:opacity-50">Approve another replan</button>
              <button type="button" disabled={review.isPending} onClick={() => review.mutate(false)} className="min-h-11 rounded-md border px-3 py-1.5 disabled:opacity-50">Reject</button>
            </div>
          )}
        </div>
      )}
      {review.error && <p role="alert" className="mt-2 text-sm text-red-600">The review could not be submitted.</p>}
    </section>
  );
}
