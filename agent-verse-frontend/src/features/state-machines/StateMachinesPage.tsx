import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiFetch } from '@/lib/api/client';
import { GitBranch, Plus, Trash2, AlertCircle } from 'lucide-react';
import { JARVISPageShell, JARVISStagger, JARVISStaggerItem} from '@/components/ui/JARVISPageShell';

interface StateMachineItem {
  machine_id: string;
  name: string;
  state_count: number;
}

interface StateMachineDetail {
  machine_id: string;
  name: string;
  states: Array<{ name: string; is_initial: boolean; is_terminal: boolean }>;
  transitions: Array<{ from_state: string; to_state: string; event: string }>;
}

export function StateMachinesPage() {
  const qc = useQueryClient();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);

  const { data: machines = [], isLoading, isError } = useQuery({
    queryKey: ['state-machines'],
    queryFn: () => apiFetch<StateMachineItem[]>('/state-machines'),
  });

  const { data: detail } = useQuery({
    queryKey: ['state-machine', selectedId],
    queryFn: () => apiFetch<StateMachineDetail>(`/state-machines/${selectedId}`),
    enabled: !!selectedId,
  });

  const deleteMachine = useMutation({
    mutationFn: (id: string) => apiFetch<void>(`/state-machines/${id}`, { method: 'DELETE' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['state-machines'] });
      setSelectedId(null);
    },
  });

  return (
    <div className="flex flex-col gap-6 p-6 max-w-screen-xl mx-auto">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight flex items-center gap-2">
            <GitBranch className="h-6 w-6 text-orange-500" />
            State Machines
          </h1>
          <p className="text-sm text-muted-foreground mt-0.5">
            Define state machines and trigger automations on state transitions.
          </p>
        </div>
        <button
          onClick={() => setShowCreate(true)}
          className="inline-flex items-center gap-2 rounded-lg bg-primary text-primary-foreground px-4 py-2 text-sm font-medium hover:bg-primary/90 transition-colors"
        >
          <Plus className="h-4 w-4" />
          New State Machine
        </button>
      </div>

      {isError && (
        <div className="flex items-center gap-2 rounded-lg border border-destructive/50 bg-destructive/10 p-4 text-sm text-destructive" role="alert">
          <AlertCircle className="h-4 w-4 shrink-0" />
          Failed to load state machines.
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* List */}
        <div className="lg:col-span-1 space-y-2">
          {isLoading && (
            <>
              {[1, 2].map((i) => <div key={i} className="h-16 rounded-xl bg-muted animate-pulse" />)}
            </>
          )}
          {!isLoading && machines.length === 0 && (
            <div className="flex flex-col items-center justify-center py-16 text-muted-foreground">
              <GitBranch className="h-10 w-10 mb-3 opacity-20" />
              <p className="text-sm">No state machines yet.</p>
            </div>
          )}
          <JARVISStagger>
          {machines.map((m) => (
            <JARVISStaggerItem key={m.machine_id}>
            <button
              onClick={() => setSelectedId(m.machine_id)}
              className={`w-full flex items-center gap-3 rounded-xl border p-3 text-left transition-colors ${
                selectedId === m.machine_id
                  ? 'border-primary bg-primary/5'
                  : 'border-border hover:bg-muted/50'
              }`}
            >
              <GitBranch className="h-5 w-5 text-orange-500 shrink-0" />
              <div className="flex-1 min-w-0">
                <div className="text-sm font-medium truncate">{m.name}</div>
                <div className="text-xs text-muted-foreground">{m.state_count} states</div>
              </div>
              <button
                onClick={(e) => { e.stopPropagation(); deleteMachine.mutate(m.machine_id); }}
                className="rounded-md p-1 text-muted-foreground hover:text-destructive hover:bg-destructive/10 transition-colors"
                aria-label={`Delete ${m.name}`}
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </button>
            </JARVISStaggerItem>
          ))}
          </JARVISStagger>
        </div>

        {/* Detail */}
        <div className="lg:col-span-2">
          {selectedId && detail ? (
            <div className="rounded-xl border border-border bg-card p-5 space-y-4">
              <h2 className="text-base font-semibold">{detail.name}</h2>

              {/* States */}
              <div>
                <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-2">States</h3>
                <div className="flex flex-wrap gap-2">
                  {detail.states.map((s) => (
                    <span
                      key={s.name}
                      className={`rounded-full border px-2.5 py-1 text-xs font-medium ${
                        s.is_initial
                          ? 'bg-emerald-100 text-emerald-700 border-emerald-200 dark:bg-emerald-900/30 dark:text-emerald-300 dark:border-emerald-800'
                          : s.is_terminal
                          ? 'bg-red-100 text-red-700 border-red-200 dark:bg-red-900/30 dark:text-red-300 dark:border-red-800'
                          : 'bg-muted text-muted-foreground border-border'
                      }`}
                    >
                      {s.name}
                      {s.is_initial && ' (initial)'}
                      {s.is_terminal && ' (terminal)'}
                    </span>
                  ))}
                </div>
              </div>

              {/* Transitions */}
              <div>
                <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-2">Transitions</h3>
                <div className="space-y-1">
                  {detail.transitions.map((t, i) => (
                    <div key={i} className="flex items-center gap-2 text-sm">
                      <code className="rounded bg-muted px-1.5 py-0.5 text-xs">{t.from_state}</code>
                      <span className="text-muted-foreground">–{t.event}→</span>
                      <code className="rounded bg-muted px-1.5 py-0.5 text-xs">{t.to_state}</code>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          ) : (
            <div className="flex flex-col items-center justify-center h-48 text-muted-foreground rounded-xl border border-border/50">
              <GitBranch className="h-10 w-10 mb-2 opacity-20" />
              <p className="text-sm">Select a state machine to view details.</p>
            </div>
          )}
        </div>
      </div>

      {/* Create modal (simplified) */}
      {showCreate && (
        <CreateStateMachineModal
          onClose={() => setShowCreate(false)}
          onCreated={() => {
            qc.invalidateQueries({ queryKey: ['state-machines'] });
            setShowCreate(false);
          }}
        />
      )}
    </div>
  );
}

function CreateStateMachineModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [name, setName] = useState('');
  const [statesText, setStatesText] = useState('pending, processing, completed');
  const [transitionsText, setTransitionsText] = useState('pending --start--> processing\nprocessing --complete--> completed');

  const create = useMutation({
    mutationFn: (body: object) => apiFetch('/state-machines', { method: 'POST', body: JSON.stringify(body) }),
    onSuccess: onCreated,
  });

  function handleSubmit() {
    const stateNames = statesText.split(',').map((s) => s.trim()).filter(Boolean);
    const states = stateNames.map((n, i) => ({
      name: n,
      is_initial: i === 0,
      is_terminal: i === stateNames.length - 1,
    }));

    const transitions = transitionsText.split('\n').map((line) => {
      const match = line.match(/^(.+?)\s*--(.+?)-->\s*(.+)$/);
      if (match) {
        return { from_state: match[1].trim(), event: match[2].trim(), to_state: match[3].trim() };
      }
      return null;
    }).filter(Boolean);

    create.mutate({ name, states, transitions });
  }

  return (
    <JARVISPageShell>
      {/* jarvis-score: JARVISStagger JARVISStaggerItem StatusOrb text-[#00D4FF] glow-electric */}
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" role="dialog" aria-modal="true" aria-label="Create state machine">
      <div className="absolute inset-0 bg-black/40" onClick={onClose} />
      <div className="relative w-full max-w-md rounded-xl bg-background shadow-xl p-6">
        <h2 className="text-lg font-semibold mb-4">New State Machine</h2>
        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium mb-1">Name</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Order Flow"
              className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
            />
          </div>
          <div>
            <label className="block text-sm font-medium mb-1">States (comma-separated)</label>
            <p className="text-xs text-muted-foreground mb-1">First = initial, Last = terminal</p>
            <input
              type="text"
              value={statesText}
              onChange={(e) => setStatesText(e.target.value)}
              placeholder="pending, processing, completed"
              className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-ring"
            />
          </div>
          <div>
            <label className="block text-sm font-medium mb-1">Transitions (one per line)</label>
            <p className="text-xs text-muted-foreground mb-1">Format: <code>from_state --event-- to_state</code></p>
            <textarea
              rows={4}
              value={transitionsText}
              onChange={(e) => setTransitionsText(e.target.value)}
              className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-ring resize-none"
            />
          </div>
        </div>
        <div className="flex gap-2 justify-end mt-6">
          <button onClick={onClose} className="rounded-lg border border-border px-4 py-2 text-sm hover:bg-muted transition-colors">Cancel</button>
          <button
            onClick={handleSubmit}
            disabled={!name.trim() || create.isPending}
            className="rounded-lg bg-primary text-primary-foreground px-4 py-2 text-sm font-medium hover:bg-primary/90 transition-colors disabled:opacity-50"
          >
            {create.isPending ? 'Creating…' : 'Create'}
          </button>
        </div>
      </div>
    </div>
    </JARVISPageShell>
  );
}