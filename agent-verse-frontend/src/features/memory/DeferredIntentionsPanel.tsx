/**
 * Deferred intentions (prospective memory, MEM-16).
 *
 * An intention is something to do later ("check the deploy tomorrow"); when it
 * is due the platform runs it as a goal for this tenant. Agents create them with
 * the builtin `defer_intention` tool; this panel lists the pending ones and lets
 * a user schedule or cancel one.
 */
import { useState, type FormEvent, type JSX } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, CalendarClock, X } from 'lucide-react';
import { memoryApi, type ProspectiveIntention } from '@/lib/api/client';
import { toast } from '@/stores/toast';

function defaultDueLocal(): string {
  const d = new Date(Date.now() + 60 * 60 * 1000);
  d.setSeconds(0, 0);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function DeferredIntentionsPanel(): JSX.Element {
  const qc = useQueryClient();
  const [intention, setIntention] = useState('');
  const [dueLocal, setDueLocal] = useState(defaultDueLocal);

  const { data: items = [], isLoading, isError, refetch } = useQuery({
    queryKey: ['prospective-intentions'],
    queryFn: () => memoryApi.listIntentions({ includeFailed: true }),
  });

  const create = useMutation({
    mutationFn: () =>
      memoryApi.createIntention({ intention: intention.trim(), due_at: new Date(dueLocal).toISOString() }),
    onSuccess: () => {
      setIntention('');
      toast({ kind: 'success', message: 'Intention scheduled.' });
      void qc.invalidateQueries({ queryKey: ['prospective-intentions'] });
    },
    onError: (e) => toast({ kind: 'error', message: `Could not schedule: ${String(e)}` }),
  });

  const cancel = useMutation({
    mutationFn: (id: string) => memoryApi.cancelIntention(id),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['prospective-intentions'] }),
    onError: (e) => toast({ kind: 'error', message: `Could not cancel: ${String(e)}` }),
  });

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (intention.trim()) create.mutate();
  };

  return (
    <section
      aria-labelledby="deferred-intentions-heading"
      className="bg-panel-graphite border border-neural-violet/20 rounded-xl overflow-hidden"
    >
      <div className="px-5 py-3 border-b border-neural-violet/15 bg-command-black/40">
        <h2 id="deferred-intentions-heading" className="text-sm font-semibold flex items-center gap-2 text-white/80">
          <CalendarClock className="h-4 w-4 text-telemetry-cyan" aria-hidden="true" />
          Deferred intentions
          <span className="text-[10px] text-white/30 font-normal">(run as a goal when due)</span>
        </h2>
      </div>

      <form onSubmit={onSubmit} className="flex flex-wrap items-end gap-2 px-5 py-3 border-b border-neural-violet/10">
        <label className="flex-1 min-w-[12rem] text-xs text-white/50">
          Intention
          <input
            value={intention}
            onChange={(e) => setIntention(e.target.value)}
            placeholder="e.g. Check the nightly deploy status"
            maxLength={2000}
            className="mt-1 w-full rounded-md bg-command-black/40 border border-neural-violet/20 px-2 py-1.5 text-sm text-white/80"
          />
        </label>
        <label className="text-xs text-white/50">
          Due
          <input
            type="datetime-local"
            value={dueLocal}
            onChange={(e) => setDueLocal(e.target.value)}
            className="mt-1 block rounded-md bg-command-black/40 border border-neural-violet/20 px-2 py-1.5 text-sm text-white/80"
          />
        </label>
        <button
          type="submit"
          disabled={!intention.trim() || create.isPending}
          className="rounded-md bg-neural-violet/30 px-3 py-1.5 text-xs text-white hover:bg-neural-violet/40 disabled:opacity-40"
        >
          Schedule
        </button>
      </form>

      {isLoading ? (
        <div className="px-5 py-4 text-sm text-white/40">Loading intentions…</div>
      ) : isError ? (
        <div role="alert" className="px-5 py-4 flex items-center gap-3 text-sm text-mission-red">
          <AlertTriangle className="h-4 w-4 shrink-0" aria-hidden="true" />
          Could not load deferred intentions.
          <button type="button" onClick={() => void refetch()} className="underline text-white/70 hover:text-white">
            Retry
          </button>
        </div>
      ) : items.length === 0 ? (
        <div className="px-5 py-4 text-sm text-white/40">No pending intentions.</div>
      ) : (
        <ul className="divide-y divide-neural-violet/10" aria-label="Pending intentions">
          {items.map((it: ProspectiveIntention) => {
            const due = new Date(it.due_at);
            const overdue = due.getTime() <= Date.now();
            const failed = it.state === 'failed';
            const error = typeof it.result?.error === 'string' ? it.result.error : '';
            return (
              <li key={it.id} className="flex items-start gap-3 px-5 py-3">
                <div className="min-w-0 flex-1">
                  <p className="text-sm text-white/70">{it.intention}</p>
                  {failed ? (
                    <p className="text-[10px] text-mission-red font-mono" role="status">
                      failed after {it.attempts ?? 0} attempts{error ? ` · ${error}` : ''}
                    </p>
                  ) : (
                    <p className="text-[10px] text-white/30 font-mono">
                      {overdue ? 'due now' : `due ${due.toLocaleString()}`} · {it.state}
                      {it.attempts ? ` · ${it.attempts} attempt${it.attempts === 1 ? '' : 's'}` : ''}
                    </p>
                  )}
                </div>
                <button
                  type="button"
                  aria-label={`Cancel intention: ${it.intention}`}
                  onClick={() => cancel.mutate(it.id)}
                  className="p-1 text-white/40 hover:text-mission-red"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
