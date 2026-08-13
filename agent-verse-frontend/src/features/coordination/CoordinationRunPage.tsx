import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Activity, ArrowRight, Gavel, Network, RefreshCw } from 'lucide-react';
import { FormEvent, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { coordinationApi } from './coordinationApi';
import { AuctionBidView } from './AuctionBidView';
import { MagenticLedgerView } from './MagenticLedgerView';
import { SwarmTopologyView } from './SwarmTopologyView';
import { ParentChildTopology } from './ParentChildTopology';
import { RunTimeline } from './RunTimeline';
import { SharedTranscript } from './SharedTranscript';
import { useCoordinationStream } from './useCoordinationStream';

export function CoordinationRunPage() {
  const { sessionId = '' } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState(sessionId);
  const query = useQuery({
    queryKey: ['coordination-run', sessionId],
    queryFn: () => coordinationApi.getRun(sessionId),
    enabled: Boolean(sessionId),
    refetchInterval: 5_000,
  });
  const stream = useCoordinationStream(sessionId);
  const transition = useMutation({
    mutationFn: (command: 'cancel' | 'resume') => {
      if (!query.data) throw new Error('Session is unavailable');
      return coordinationApi.transition(sessionId, command, query.data.session.version);
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['coordination-run', sessionId] }),
  });

  function openSession(event: FormEvent) {
    event.preventDefault();
    if (draft.trim()) navigate(`/coordination/${encodeURIComponent(draft.trim())}`);
  }

  const run = query.data;
  const patternCounts = [
    ['MoA layers', run?.moa.items.length ?? 0],
    ['CAMEL dialogues', run?.camel.items.length ?? 0],
    ['Generative agents', run?.generative.items.length ?? 0],
  ] as const;

  return (
    <main className="mx-auto max-w-[1500px] space-y-6 p-4 md:p-8">
      <header className="flex flex-col justify-between gap-5 border-b border-border pb-6 lg:flex-row lg:items-end">
        <div>
          <p className="font-mono text-xs uppercase tracking-[0.22em] text-primary">Session evidence</p>
          <h1 className="font-display mt-2 text-3xl font-semibold tracking-tight">Coordination ledger</h1>
          <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
            Follow the decisions, handoffs, and topology that produced this run—not just its final answer.
          </p>
        </div>
        <form onSubmit={openSession} className="flex w-full gap-2 lg:w-auto">
          <label htmlFor="coordination-session" className="sr-only">Session ID</label>
          <input id="coordination-session" value={draft} onChange={(event) => setDraft(event.target.value)}
            placeholder="Session ID" className="min-w-0 flex-1 rounded-md border bg-background px-3 py-2 font-mono text-sm lg:w-72" />
          <button className="inline-flex min-h-11 min-w-11 items-center justify-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground">
            Open <ArrowRight className="h-4 w-4" aria-hidden="true" />
          </button>
        </form>
      </header>

      {!sessionId && <p className="rounded-lg border border-dashed p-8 text-center text-muted-foreground">Enter a session ID to inspect its durable coordination record.</p>}
      {query.isLoading && <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground"><RefreshCw className="h-4 w-4 animate-spin" />Loading session evidence…</p>}
      {query.error && <div role="alert" className="rounded-lg border border-red-500/30 bg-red-500/10 p-4 text-sm">The coordination record could not be loaded. Check the session ID and your access.</div>}

      {run && (
        <>
        <section aria-label="Session controls" className="flex flex-wrap items-center justify-between gap-3 rounded-lg border px-4 py-3">
          <div aria-live="polite" className="flex flex-wrap items-center gap-2 text-sm">
            <span className="font-medium">{run.session.state}</span>
            <span className="text-muted-foreground">Stream: {stream.status}</span>
            <span className="font-mono text-xs text-muted-foreground">cursor {stream.lastSequence}</span>
          </div>
          <div className="flex gap-2">
            {['pending', 'active', 'paused'].includes(run.session.state) && (
              <button type="button" disabled={transition.isPending} onClick={() => window.confirm('Cancel this coordination session?') && transition.mutate('cancel')} className="min-h-11 min-w-11 rounded-md border px-3 py-1.5 text-sm disabled:opacity-50">Cancel</button>
            )}
            {run.session.state === 'paused' && (
              <button type="button" disabled={transition.isPending} onClick={() => transition.mutate('resume')} className="min-h-11 min-w-11 rounded-md bg-primary px-3 py-1.5 text-sm text-primary-foreground disabled:opacity-50">Resume</button>
            )}
          </div>
        </section>
        {transition.error && <p role="alert" className="text-sm text-red-600">The session command failed. Refresh and try again.</p>}
        <div className="grid gap-6 xl:grid-cols-[minmax(0,1.6fr)_minmax(330px,0.9fr)]">
          <section aria-labelledby="timeline-heading" className="rounded-xl border bg-card p-5">
            <div className="mb-5 flex items-center justify-between">
              <div><p className="font-mono text-xs text-muted-foreground">CAUSAL ORDER</p><h2 id="timeline-heading" className="mt-1 text-lg font-semibold">Transcript timeline</h2></div>
              <span className="rounded-full border px-2.5 py-1 font-mono text-xs">{run.messages.items.length} events</span>
            </div>
            <SharedTranscript messages={run.messages.items} />
            <div className="mt-8 border-t pt-6"><RunTimeline events={stream.events} /></div>
          </section>

          <aside className="space-y-6">
            <section className="rounded-xl border bg-card p-5">
              <Activity className="mb-3 h-4 w-4 text-primary" aria-hidden="true" />
              <MagenticLedgerView ledger={run.ledger} />
            </section>

            <section aria-label="Swarm topology" className="overflow-hidden rounded-xl border bg-card p-5">
              <Network className="mb-3 h-4 w-4 text-cyan-500" aria-hidden="true" />
              <SwarmTopologyView nodes={run.swarm.nodes} edges={run.swarm.edges} />
              <div className="mt-6 border-t pt-5"><ParentChildTopology nodes={run.swarm.nodes} /></div>
            </section>

            <section className="rounded-xl border bg-card p-5">
              <div className="flex items-center gap-2"><Gavel className="h-4 w-4 text-amber-500" /><h2 className="font-semibold">Pattern evidence</h2></div>
              <dl className="mt-4 divide-y divide-border text-sm">
                {patternCounts.map(([label, count]) => <div key={label} className="flex justify-between py-2.5"><dt className="text-muted-foreground">{label}</dt><dd className="font-mono">{count}</dd></div>)}
                <div className="flex justify-between py-2.5"><dt className="text-muted-foreground">Auction</dt><dd className="font-mono">{run.auction.sealed_bid_count} sealed bids</dd></div>
              </dl>
              <div className="mt-5 border-t pt-4"><AuctionBidView state={run.auction} /></div>
            </section>
          </aside>
        </div>
        </>
      )}
    </main>
  );
}

export default CoordinationRunPage;
