import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, Plus, RefreshCw, Trash2, Zap, XCircle, Loader2 } from 'lucide-react';
import { JARVISPageShell } from '@/components/ui/JARVISPageShell';
import { modelsApi, type ConfiguredModel } from '@/lib/api/client';
import { useAuthStore } from '@/stores/auth';

const CAPABILITIES = [
  { key: 'text_generation', label: 'Reasoning', hint: 'Planning, execution, verification' },
  { key: 'embedding', label: 'Embeddings', hint: 'Vector search / RAG' },
  { key: 'vision', label: 'Vision', hint: 'Image understanding' },
  { key: 'ocr', label: 'OCR', hint: 'Document text extraction' },
  { key: 'rerank', label: 'Reranker', hint: 'Retrieval reranking' },
] as const;

interface FormState {
  model_id: string;
  provider: string;
  capabilities: string[];
  cost_per_1k_input: string;
  supports_tools: boolean;
  supports_vision: boolean;
}

const EMPTY_FORM: FormState = {
  model_id: '',
  provider: '',
  capabilities: ['text_generation'],
  cost_per_1k_input: '0',
  supports_tools: true,
  supports_vision: false,
};

export function ModelRegistryPage() {
  const apiKey = useAuthStore((s: { apiKey: string | null }) => s.apiKey);
  const qc = useQueryClient();
  const [showModal, setShowModal] = useState(false);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [formError, setFormError] = useState('');

  const { data, isLoading, isError } = useQuery({
    queryKey: ['configured-models'],
    queryFn: () => modelsApi.listConfigured(),
    enabled: !!apiKey,
    staleTime: 30_000,
  });

  const invalidate = () => qc.invalidateQueries({ queryKey: ['configured-models'] });

  const upsert = useMutation({
    mutationFn: () =>
      modelsApi.upsertConfigured({
        model_id: form.model_id.trim(),
        provider: form.provider.trim() || 'custom',
        capabilities: form.capabilities,
        cost_per_1k_input: Number(form.cost_per_1k_input) || 0,
        supports_tools: form.supports_tools,
        supports_vision: form.supports_vision || form.capabilities.includes('vision'),
      }),
    onSuccess: () => {
      invalidate();
      setShowModal(false);
      setForm(EMPTY_FORM);
      setFormError('');
    },
    onError: (e: Error) => setFormError(e.message ?? 'Failed to save model'),
  });

  const remove = useMutation({
    mutationFn: (m: ConfiguredModel) => modelsApi.deleteConfigured(m.provider, m.model_id),
    onSuccess: invalidate,
  });

  const reseed = useMutation({ mutationFn: () => modelsApi.reseed(), onSuccess: invalidate });

  const groups = data?.capabilities ?? [];
  const groupFor = (cap: string) => groups.find((g) => g.capability === cap);

  const toggleCap = (cap: string) =>
    setForm((f) => ({
      ...f,
      capabilities: f.capabilities.includes(cap)
        ? f.capabilities.filter((c) => c !== cap)
        : [...f.capabilities, cap],
    }));

  return (
    <JARVISPageShell>
      <div className="space-y-6">
        <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h1 className="text-2xl font-bold">Model Registry</h1>
            <p className="mt-1 text-sm text-muted-foreground">
              Configure models per capability. When several are configured for the same capability,
              the platform automatically uses the <strong>lowest-cost</strong> one
              (self-hosted models count as free).
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => reseed.mutate()}
              disabled={reseed.isPending}
              className="inline-flex items-center gap-2 rounded-xl border border-border bg-card px-4 py-2 text-sm font-medium hover:bg-muted transition-colors"
            >
              <RefreshCw className={`h-4 w-4 ${reseed.isPending ? 'animate-spin' : ''}`} />
              Reseed from config
            </button>
            <button
              type="button"
              onClick={() => { setForm(EMPTY_FORM); setFormError(''); setShowModal(true); }}
              className="inline-flex items-center gap-2 rounded-xl bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:opacity-90 transition-opacity"
            >
              <Plus className="h-4 w-4" /> Add Model
            </button>
          </div>
        </div>

        {isLoading && <p className="text-sm text-muted-foreground">Loading models…</p>}
        {isError && <p className="text-sm text-destructive">Failed to load the model registry.</p>}

        {!isLoading && CAPABILITIES.map((cap) => {
          const group = groupFor(cap.key);
          const models = group?.models ?? [];
          return (
            <div key={cap.key} className="rounded-2xl border border-border bg-card p-5">
              <div className="mb-3 flex items-baseline justify-between">
                <div>
                  <h2 className="text-lg font-semibold">{cap.label}</h2>
                  <p className="text-xs text-muted-foreground">{cap.hint}</p>
                </div>
                <span className="text-xs text-muted-foreground">{models.length} configured</span>
              </div>
              {models.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  No model configured for {cap.label.toLowerCase()}.
                </p>
              ) : (
                <div className="space-y-2">
                  {models.map((m) => {
                    const selected = group?.selected_model_id === m.model_id;
                    return (
                      <div
                        key={`${m.provider}/${m.model_id}`}
                        className={`flex items-center justify-between rounded-xl border px-4 py-3 ${
                          selected
                            ? 'border-emerald-400 bg-emerald-50 dark:bg-emerald-900/20'
                            : 'border-border bg-background'
                        }`}
                      >
                        <div className="min-w-0">
                          <div className="flex items-center gap-2">
                            <span className="font-medium truncate">{m.model_id}</span>
                            {selected && (
                              <span className="inline-flex items-center gap-1 rounded-full bg-emerald-600 px-2 py-0.5 text-[10px] font-semibold text-white">
                                <Zap className="h-3 w-3" /> IN USE (cheapest)
                              </span>
                            )}
                          </div>
                          <div className="mt-0.5 text-xs text-muted-foreground">
                            {m.provider} · ${m.cost_per_1k_input.toFixed(5)}/1k in
                            {m.cost_per_1k_input === 0 && ' (self-hosted / free)'}
                            {m.supports_tools && ' · tools'}
                            {m.supports_vision && ' · vision'}
                          </div>
                        </div>
                        <button
                          type="button"
                          onClick={() => remove.mutate(m)}
                          disabled={remove.isPending}
                          className="ml-3 shrink-0 rounded-lg p-2 text-muted-foreground hover:bg-destructive/10 hover:text-destructive transition-colors"
                          aria-label={`Remove ${m.model_id}`}
                        >
                          <Trash2 className="h-4 w-4" />
                        </button>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {showModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <div className="w-full max-w-lg rounded-2xl bg-card shadow-xl">
            <div className="border-b border-border px-6 py-4">
              <h3 className="text-lg font-semibold">Add / override a model</h3>
              <p className="mt-1 text-xs text-muted-foreground">
                Register a model your provider can serve. Set its cost so the cheapest one is
                auto-selected when several cover the same capability.
              </p>
            </div>
            <div className="space-y-4 px-6 py-4">
              <div>
                <label className="mb-1 block text-xs font-medium text-muted-foreground">Model ID *</label>
                <input
                  value={form.model_id}
                  onChange={(e) => setForm((f) => ({ ...f, model_id: e.target.value }))}
                  placeholder="e.g. openai/gpt-oss-20b"
                  className="w-full rounded-lg border border-input bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary"
                />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="mb-1 block text-xs font-medium text-muted-foreground">Provider</label>
                  <input
                    value={form.provider}
                    onChange={(e) => setForm((f) => ({ ...f, provider: e.target.value }))}
                    placeholder="nvidia / openai / anthropic…"
                    className="w-full rounded-lg border border-input bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-xs font-medium text-muted-foreground">Cost / 1k input ($)</label>
                  <input
                    type="number" step="0.00001" min="0"
                    value={form.cost_per_1k_input}
                    onChange={(e) => setForm((f) => ({ ...f, cost_per_1k_input: e.target.value }))}
                    className="w-full rounded-lg border border-input bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary"
                  />
                </div>
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-muted-foreground">Capabilities *</label>
                <div className="flex flex-wrap gap-2">
                  {CAPABILITIES.map((c) => (
                    <button
                      key={c.key}
                      type="button"
                      onClick={() => toggleCap(c.key)}
                      className={`rounded-full px-3 py-1 text-xs font-medium transition-colors ${
                        form.capabilities.includes(c.key)
                          ? 'bg-primary text-primary-foreground'
                          : 'border border-border bg-background text-muted-foreground hover:bg-muted'
                      }`}
                    >
                      {c.label}
                    </button>
                  ))}
                </div>
              </div>
              <div className="flex gap-4">
                <label className="flex items-center gap-2 text-sm">
                  <input type="checkbox" checked={form.supports_tools}
                    onChange={(e) => setForm((f) => ({ ...f, supports_tools: e.target.checked }))}
                    className="h-4 w-4 rounded border-input accent-primary" />
                  Supports tools
                </label>
                <label className="flex items-center gap-2 text-sm">
                  <input type="checkbox" checked={form.supports_vision}
                    onChange={(e) => setForm((f) => ({ ...f, supports_vision: e.target.checked }))}
                    className="h-4 w-4 rounded border-input accent-primary" />
                  Supports vision
                </label>
              </div>
              {formError && (
                <div role="alert" className="flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-destructive dark:border-red-800 dark:bg-red-900/20">
                  <XCircle className="mt-0.5 h-4 w-4 flex-shrink-0" /> {formError}
                </div>
              )}
            </div>
            <div className="flex justify-end gap-3 border-t border-border px-6 py-4">
              <button onClick={() => setShowModal(false)} className="rounded-lg border border-border px-4 py-2 text-sm hover:bg-accent">Cancel</button>
              <button
                onClick={() => {
                  if (!form.model_id.trim() || form.capabilities.length === 0) {
                    setFormError('Model ID and at least one capability are required.');
                    return;
                  }
                  upsert.mutate();
                }}
                disabled={upsert.isPending}
                className="inline-flex items-center gap-2 rounded-lg bg-primary px-5 py-2 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:opacity-50"
              >
                {upsert.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
                {upsert.isPending ? 'Saving…' : (<><Check className="h-4 w-4" /> Save</>)}
              </button>
            </div>
          </div>
        </div>
      )}
    </JARVISPageShell>
  );
}
