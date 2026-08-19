import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { FlaskConical, Play, Plus, CheckCircle2, XCircle, Clock, AlertCircle } from 'lucide-react';
import { apiFetch } from '@/lib/api/client';
import { JARVISPageShell, JARVISStagger, JARVISStaggerItem } from '@/components/ui/JARVISPageShell';

interface EvalSuite {
  id: string;
  name: string;
  description?: string;
  task_count: number;
  last_run_at?: string;
  last_run_status?: 'passed' | 'failed' | 'running' | 'pending';
  pass_rate?: number;
  created_at: string;
}

interface CreateSuiteBody {
  name: string;
  description: string;
}

const STATUS_STYLES: Record<string, { label: string; color: string; icon: React.ElementType }> = {
  passed:  { label: 'Passed',  color: 'text-emerald-400 bg-emerald-400/10 border-emerald-400/20', icon: CheckCircle2 },
  failed:  { label: 'Failed',  color: 'text-rose-400 bg-rose-400/10 border-rose-400/20',         icon: XCircle      },
  running: { label: 'Running', color: 'text-[#00D4FF] bg-[#00D4FF]/10 border-[#00D4FF]/20',     icon: Clock        },
  pending: { label: 'Pending', color: 'text-[#64748B] bg-[#64748B]/10 border-[#64748B]/20',     icon: Clock        },
};

function SuiteCard({ suite, onRun }: { suite: EvalSuite; onRun: (id: string) => void }) {
  const st = suite.last_run_status ? STATUS_STYLES[suite.last_run_status] : null;
  const StatusIcon = st?.icon ?? Clock;
  return (
    <JARVISStaggerItem interactive>
      <div className="rounded-xl border border-[#1E2535] bg-[#1A1F2E] p-4 hover:border-[#2D3748] transition-colors duration-150">
        <div className="flex items-start justify-between gap-3">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-1">
              <FlaskConical className="h-4 w-4 text-violet-400 shrink-0" />
              <h3 className="font-semibold text-[#F1F5F9] text-sm truncate">{suite.name}</h3>
            </div>
            {suite.description && (
              <p className="text-xs text-[#64748B] line-clamp-2 mb-2">{suite.description}</p>
            )}
            <div className="flex items-center gap-3 text-xs text-[#475569]">
              <span>{suite.task_count} tasks</span>
              {suite.pass_rate !== undefined && (
                <span className="text-emerald-400">{Math.round(suite.pass_rate * 100)}% pass</span>
              )}
              {suite.last_run_at && (
                <span>Last: {new Date(suite.last_run_at).toLocaleDateString()}</span>
              )}
            </div>
          </div>
          <div className="flex flex-col items-end gap-2 shrink-0">
            {st && (
              <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium border ${st.color}`}>
                <StatusIcon className="h-3 w-3" aria-hidden />
                {st.label}
              </span>
            )}
            <button
              onClick={() => onRun(suite.id)}
              className="flex items-center gap-1 px-3 py-1.5 rounded-lg bg-violet-500/20 text-violet-400 hover:bg-violet-500/30 text-xs font-medium transition-colors"
              title="Run suite"
            >
              <Play className="h-3 w-3" /> Run
            </button>
          </div>
        </div>
      </div>
    </JARVISStaggerItem>
  );
}

export function EvalSuitesPage() {
  const qc = useQueryClient();
  const [showCreate, setShowCreate] = useState(false);
  const [form, setForm] = useState<CreateSuiteBody>({ name: '', description: '' });

  const { data: suites = [], isLoading, isError } = useQuery<EvalSuite[]>({
    queryKey: ['eval-suites'],
    queryFn: () => apiFetch<EvalSuite[]>('/intelligence/eval-suites'),
  });

  const createSuite = useMutation({
    mutationFn: (body: CreateSuiteBody) =>
      apiFetch('/intelligence/eval-suites', { method: 'POST', body: JSON.stringify(body) }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['eval-suites'] });
      setShowCreate(false);
      setForm({ name: '', description: '' });
    },
  });

  const runSuite = useMutation({
    mutationFn: (id: string) =>
      apiFetch(`/intelligence/eval-suites/${id}/run`, { method: 'POST' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['eval-suites'] }),
  });

  const totalSuites = suites.length;
  const passedSuites = suites.filter((s) => s.last_run_status === 'passed').length;
  const failedSuites = suites.filter((s) => s.last_run_status === 'failed').length;

  return (
    <JARVISPageShell>
      <div className="p-6 max-w-5xl mx-auto space-y-6">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-[#F1F5F9] flex items-center gap-2.5">
              <FlaskConical className="h-6 w-6 text-violet-400" />
              Eval Suites
            </h1>
            <p className="text-sm text-[#64748B] mt-0.5">
              Run automated evaluation suites to measure agent quality.
            </p>
          </div>
          <button
            onClick={() => setShowCreate(true)}
            className="flex items-center gap-2 px-4 py-2 rounded-xl bg-violet-500 hover:bg-violet-400 text-[#F1F5F9] text-sm font-medium transition-colors"
          >
            <Plus className="h-4 w-4" /> New Suite
          </button>
        </div>

        {/* KPI row */}
        <div className="grid grid-cols-3 gap-3">
          {[
            { label: 'Total Suites', value: totalSuites, color: 'text-[#00D4FF]' },
            { label: 'Passed',       value: passedSuites,  color: 'text-emerald-400' },
            { label: 'Failed',       value: failedSuites,  color: 'text-rose-400' },
          ].map(({ label, value, color }) => (
            <div key={label} className="rounded-xl border border-[#1E2535] bg-[#1A1F2E] p-4">
              <p className="text-xs text-[#64748B] mb-1">{label}</p>
              <p className={`text-2xl font-bold tabular-nums ${color}`}>{isLoading ? '—' : value}</p>
            </div>
          ))}
        </div>

        {/* Error */}
        {isError && (
          <div className="flex items-center gap-2 rounded-xl border border-rose-400/30 bg-rose-400/10 p-4 text-sm text-rose-400" role="alert">
            <AlertCircle className="h-4 w-4 shrink-0" />
            Failed to load eval suites.
          </div>
        )}

        {/* List */}
        {isLoading ? (
          <div className="space-y-3">
            {[1, 2, 3].map((i) => (
              <div key={i} className="h-20 rounded-xl bg-[#1A1F2E] animate-pulse border border-[#1E2535]" />
            ))}
          </div>
        ) : suites.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-20 text-[#475569] border border-[#1E2535] rounded-xl">
            <FlaskConical className="h-12 w-12 mb-3 opacity-20" />
            <p className="font-medium text-[#94A3B8]">No eval suites yet</p>
            <p className="text-sm mt-1">Create a suite to start evaluating agent quality.</p>
          </div>
        ) : (
          <JARVISStagger className="space-y-3">
            {suites.map((suite) => (
              <SuiteCard key={suite.id} suite={suite} onRun={(id) => runSuite.mutate(id)} />
            ))}
          </JARVISStagger>
        )}

        {/* Create modal */}
        {showCreate && (
          <div className="fixed inset-0 z-50 flex items-center justify-center p-4" role="dialog" aria-modal="true">
            <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={() => setShowCreate(false)} />
            <div className="relative w-full max-w-md rounded-2xl bg-[#1A1F2E] border border-[#2D3748] shadow-2xl p-6">
              <h2 className="text-lg font-semibold text-[#F1F5F9] mb-4">New Eval Suite</h2>
              <div className="space-y-4">
                <div>
                  <label className="block text-xs font-medium text-[#94A3B8] mb-1.5">Name</label>
                  <input
                    value={form.name}
                    onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                    placeholder="Q1 Quality Benchmarks"
                    className="w-full rounded-lg border border-[#1E2535] bg-[#0F1117] px-3 py-2 text-sm text-[#F1F5F9] placeholder-[#475569] focus:outline-none focus:border-violet-500"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-[#94A3B8] mb-1.5">Description</label>
                  <textarea
                    value={form.description}
                    onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
                    placeholder="What does this suite evaluate?"
                    rows={3}
                    className="w-full rounded-lg border border-[#1E2535] bg-[#0F1117] px-3 py-2 text-sm text-[#F1F5F9] placeholder-[#475569] focus:outline-none focus:border-violet-500 resize-none"
                  />
                </div>
                <div className="flex gap-2 justify-end pt-2">
                  <button
                    onClick={() => setShowCreate(false)}
                    className="px-4 py-2 rounded-lg border border-[#1E2535] text-sm text-[#94A3B8] hover:bg-[#252B3B] transition-colors"
                  >
                    Cancel
                  </button>
                  <button
                    onClick={() => createSuite.mutate(form)}
                    disabled={!form.name.trim() || createSuite.isPending}
                    className="px-4 py-2 rounded-lg bg-violet-500 text-[#F1F5F9] text-sm font-medium hover:bg-violet-400 disabled:opacity-50 transition-colors"
                  >
                    {createSuite.isPending ? 'Creating…' : 'Create Suite'}
                  </button>
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    </JARVISPageShell>
  );
}

export default EvalSuitesPage;
