import { useState } from 'react';
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  ArrowDown,
  ArrowUp,
  Check,
  KeyRound,
  CircleCheck,
  Download,
  Info,
  Loader2,
  Pencil,
  Plug,
  Plus,
  RefreshCw,
  RotateCcw,
  Save,
  ShieldCheck,
  Trash2,
  TriangleAlert,
  XCircle,
  Zap,
} from 'lucide-react';
import { JARVISPageShell } from '@/components/ui/JARVISPageShell';
import {
  modelsApi,
  type CapabilityGroup,
  type ConfiguredModel,
  type ModelEndpointTestResult,
  type ModelRegistryAccess,
} from '@/lib/api/client';
import { useAuthStore } from '@/stores/auth';
import { CatalogImportDialog } from './CatalogImportDialog';
import {
  API_KEY_REJECTED_HINT,
  MAX_OUTPUT_DIMENSIONS,
  isApiKeyFailure,
  looksLikeChatModel,
  parseOutputDimensions,
} from './modelFormHelpers';

const CAPABILITIES = [
  { key: 'text_generation', label: 'Reasoning', hint: 'Planning, execution, verification' },
  { key: 'embedding', label: 'Embeddings', hint: 'Vector search / RAG' },
  { key: 'vision', label: 'Vision', hint: 'Image understanding' },
  { key: 'ocr', label: 'OCR', hint: 'Document text extraction' },
  { key: 'rerank', label: 'Reranker', hint: 'Retrieval reranking' },
] as const;

/** Providers the backend accepts on POST /models/configured. */
const PROVIDERS = [
  'nvidia', 'anthropic', 'openai', 'openai_compatible', 'azure_openai', 'gemini', 'google',
  'voyage', 'groq', 'xai', 'ollama', 'onprem', 'openrouter', 'bedrock', 'vertex', 'mistral',
  'cohere', 'custom',
] as const;

interface FormState {
  model_id: string;
  display_name: string;
  provider: string;
  /** Base URL of an OpenAI-compatible server; empty = the provider's configured API. */
  base_url: string;
  capabilities: string[];
  cost_per_1k_input: string;
  cost_per_1k_output: string;
  quality_score: string;
  supports_tools: boolean;
  supports_vision: boolean;
  /** Not editable in the form; carried over when editing so an upsert keeps it. */
  supports_structured_output?: boolean;
  /**
   * Write-only endpoint credential typed by the operator. Lives in component
   * state only (never localStorage), is sent on Save / Test connection and is
   * cleared when the dialog closes. The saved key is never shown.
   */
  api_key: string;
  /** The entry already has a saved (vault-encrypted) key — display only. */
  has_api_key: boolean;
  /** Remove the saved key on Save. */
  clear_api_key: boolean;
  /** Embedding models: the vector width to request; empty = native width. */
  output_dimensions: string;
}

const EMPTY_FORM: FormState = {
  model_id: '',
  display_name: '',
  provider: 'nvidia',
  base_url: '',
  capabilities: ['text_generation'],
  cost_per_1k_input: '0',
  cost_per_1k_output: '0',
  quality_score: '0.5',
  supports_tools: true,
  supports_vision: false,
  api_key: '',
  has_api_key: false,
  clear_api_key: false,
  output_dimensions: '',
};

const formFromModel = (m: ConfiguredModel): FormState => ({
  model_id: m.model_id,
  display_name: m.display_name && m.display_name !== m.model_id ? m.display_name : '',
  provider: m.provider,
  base_url: m.base_url ?? '',
  capabilities: [...m.capabilities],
  cost_per_1k_input: String(m.cost_per_1k_input ?? 0),
  cost_per_1k_output: String(m.cost_per_1k_output ?? 0),
  quality_score: String(m.quality_score ?? 0.5),
  supports_tools: !!m.supports_tools,
  supports_vision: !!m.supports_vision,
  supports_structured_output: m.supports_structured_output,
  api_key: '',
  has_api_key: !!m.has_api_key,
  clear_api_key: false,
  output_dimensions: m.output_dimensions ? String(m.output_dimensions) : '',
});

const keyOf = (m: ConfiguredModel) => m.key || `${m.provider}/${m.model_id}`;

/** Whether the model can serve now (older backends send no `servable`). */
const usable = (m: ConfiguredModel) => m.provider_ready && m.servable !== false;

type CoverageState = 'ready' | 'not_ready' | 'none';

/**
 * The truth per capability, from the registry rows themselves: ready when a
 * model can serve it now, not usable when models are listed but none can, not
 * configured when the capability has no model at all.
 */
const coverageOf = (group: CapabilityGroup | undefined): { state: CoverageState; ready: number } => {
  if (!group || group.models.length === 0) return { state: 'none', ready: 0 };
  const ready = group.ready_count
    ?? group.models.filter((m) => usable(m) && !m.refused).length;
  const state = group.status ?? (ready > 0 ? 'ready' : 'not_ready');
  return { state, ready };
};

/** host:port of an endpoint URL for compact display; the raw value if it does not parse. */
const endpointHost = (url: string) => {
  try {
    return new URL(url).host || url;
  } catch {
    return url;
  }
};

/** The fields a connection test depends on — a result is only shown while they are unchanged. */
const testSignature = (f: FormState) =>
  [f.provider, f.model_id.trim(), f.base_url.trim(), f.api_key, f.output_dimensions.trim()].join('\n');

type TestOutcome =
  | { sig: string; kind: 'result'; result: ModelEndpointTestResult }
  | { sig: string; kind: 'error'; message: string };

const INPUT_CLS =
  'w-full rounded-lg border border-input bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary';

export function ModelRegistryPage() {
  const apiKey = useAuthStore((s: { apiKey: string | null }) => s.apiKey);
  const qc = useQueryClient();
  const [showModal, setShowModal] = useState(false);
  const [showCatalog, setShowCatalog] = useState(false);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [formError, setFormError] = useState('');
  // The row being edited ("provider/model_id"), or null when adding.
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [testOutcome, setTestOutcome] = useState<TestOutcome | null>(null);
  // Optional platform admin key. Kept IN MEMORY ONLY (never written to
  // localStorage/sessionStorage) so this sensitive credential is not persisted
  // in the browser — it is re-entered per session and cleared on reload.
  // Tenant admins on an operator tenant don't need it at all.
  const [adminKey, setAdminKey] = useState<string>('');
  // Unsaved local reorderings, keyed by capability → ordered model keys.
  const [drafts, setDrafts] = useState<Record<string, string[]>>({});
  const [orderError, setOrderError] = useState<Record<string, string>>({});
  const key = adminKey || undefined;

  const { data, isLoading, isError } = useQuery({
    queryKey: ['configured-models'],
    queryFn: () => modelsApi.listConfigured(key),
    enabled: !!apiKey,
    staleTime: 30_000,
  });

  const accessQuery = useQuery({
    queryKey: ['configured-models-access', adminKey],
    queryFn: () => modelsApi.access(key),
    enabled: !!apiKey,
    staleTime: 30_000,
    placeholderData: keepPreviousData,
  });
  // If the access probe itself fails (e.g. an older backend without the
  // endpoint), fall back to the admin-key-only behaviour; the backend still
  // enforces authorization on every mutation.
  const access: ModelRegistryAccess | undefined = accessQuery.data ?? (accessQuery.isError
    ? {
        can_modify: !!adminKey,
        via: adminKey ? 'admin_key' : null,
        needs_admin_key: true,
        reason: 'Enter the platform admin key to modify the registry.',
      }
    : undefined);
  const canModify = !!access?.can_modify;
  const showKeyInput = !!adminKey || (!!access && access.needs_admin_key && !access.can_modify);
  const denyTitle = access?.reason || 'You are not allowed to modify the model registry';

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ['configured-models'] });
    void qc.invalidateQueries({ queryKey: ['models-catalog'] });
  };

  const upsert = useMutation({
    mutationFn: () => {
      const baseUrl = form.base_url.trim();
      const apiKeyTyped = form.api_key.trim();
      const embedding = form.capabilities.includes('embedding');
      return modelsApi.upsertConfigured({
        model_id: form.model_id.trim(),
        display_name: form.display_name.trim() || undefined,
        provider: form.provider || 'custom',
        capabilities: form.capabilities,
        cost_per_1k_input: Number(form.cost_per_1k_input) || 0,
        cost_per_1k_output: Number(form.cost_per_1k_output) || 0,
        quality_score: Math.min(1, Math.max(0, Number(form.quality_score) || 0)),
        supports_tools: form.supports_tools,
        supports_vision: form.supports_vision || form.capabilities.includes('vision'),
        ...(form.supports_structured_output !== undefined
          ? { supports_structured_output: form.supports_structured_output }
          : {}),
        ...(baseUrl ? { base_url: baseUrl } : {}),
        ...(apiKeyTyped
          ? { api_key: apiKeyTyped }
          : form.clear_api_key ? { clear_api_key: true } : {}),
        ...(embedding ? { output_dimensions: parseOutputDimensions(form.output_dimensions) } : {}),
      }, key);
    },
    onSuccess: () => {
      invalidate();
      closeModal();
    },
    onError: (e: Error) => setFormError(e.message || 'Failed to save model'),
  });

  const testConn = useMutation({
    mutationFn: ({ f }: { f: FormState; sig: string }) =>
      modelsApi.testEndpoint({
        provider: f.provider || 'custom',
        model_id: f.model_id.trim(),
        base_url: f.base_url.trim(),
        capabilities: f.capabilities,
        ...(f.api_key.trim() ? { api_key: f.api_key.trim() } : {}),
        ...(f.capabilities.includes('embedding')
          ? { output_dimensions: parseOutputDimensions(f.output_dimensions) }
          : {}),
      }, key),
    onSuccess: (result, { sig }) => setTestOutcome({ sig, kind: 'result', result }),
    onError: (e: Error, { sig }) =>
      setTestOutcome({ sig, kind: 'error', message: e.message || 'Connection test failed' }),
  });

  const openAdd = () => {
    setForm(EMPTY_FORM);
    setEditingKey(null);
    setFormError('');
    setTestOutcome(null);
    setShowModal(true);
  };

  const openEdit = (m: ConfiguredModel) => {
    setForm(formFromModel(m));
    setEditingKey(keyOf(m));
    setFormError('');
    setTestOutcome(null);
    setShowModal(true);
  };

  function closeModal() {
    setShowModal(false);
    setForm(EMPTY_FORM);
    setEditingKey(null);
    setFormError('');
    setTestOutcome(null);
  }

  /** Update a form field; a change to what the connection test probed clears its result. */
  const setField = <K extends keyof FormState>(field: K, value: FormState[K]) => {
    const next = { ...form, [field]: value };
    if (testSignature(next) !== testSignature(form)) setTestOutcome(null);
    setForm(next);
  };

  const remove = useMutation({
    mutationFn: (m: ConfiguredModel) => modelsApi.deleteConfigured(m.provider, m.model_id, key),
    onSuccess: invalidate,
  });

  const reseed = useMutation({ mutationFn: () => modelsApi.reseed(key), onSuccess: invalidate });

  const clearDraft = (cap: string) => {
    const without = <T,>(rec: Record<string, T>) => {
      const next = { ...rec };
      delete next[cap];
      return next;
    };
    setDrafts(without);
    setOrderError(without);
  };

  const saveOrder = useMutation({
    mutationFn: ({ cap, order }: { cap: string; order: string[] }) =>
      modelsApi.savePreference(cap, order, key),
    onSuccess: (_r, { cap }) => { clearDraft(cap); invalidate(); },
    onError: (e: Error, { cap }) =>
      setOrderError((s) => ({ ...s, [cap]: e.message || 'Failed to save order' })),
  });

  const resetOrder = useMutation({
    mutationFn: (cap: string) => modelsApi.resetPreference(cap, key),
    onSuccess: (_r, cap) => { clearDraft(cap); invalidate(); },
    onError: (e: Error, cap) =>
      setOrderError((s) => ({ ...s, [cap]: e.message || 'Failed to reset order' })),
  });

  const groups = data?.capabilities ?? [];
  const groupFor = (cap: string) => groups.find((g) => g.capability === cap);
  // The vector index width (EMBEDDING_DIM), as reported on the embedding rows.
  const indexDimension = groupFor('embedding')?.models.find((m) => m.index_dimension)?.index_dimension;
  const outputDims = parseOutputDimensions(form.output_dimensions);
  const embeddingSelected = form.capabilities.includes('embedding');

  const move = (group: CapabilityGroup, from: number, to: number) => {
    const current = drafts[group.capability] ?? group.models.map(keyOf);
    if (to < 0 || to >= current.length) return;
    const next = [...current];
    const [item] = next.splice(from, 1);
    next.splice(to, 0, item);
    setDrafts((d) => ({ ...d, [group.capability]: next }));
  };

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
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <h1 className="text-2xl font-bold">Model Registry</h1>
            <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
              Models run in the saved <strong>preference order</strong> for each category: the first
              is the primary and the next one is the automatic fallback if it fails. Models whose
              provider has no API key are skipped. With no saved order, the{' '}
              <strong>cheapest</strong> model is used first (self-hosted models count as free);
              models imported from the catalog only follow your configured ones until you rank them.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {showKeyInput && (
              <input
                type="password"
                value={adminKey}
                onChange={(e) => setAdminKey(e.target.value)}
                placeholder="Platform admin key"
                aria-label="Platform admin key"
                title="Optional platform admin key — the registry is deployment-global"
                className="rounded-xl border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary"
              />
            )}
            <button
              type="button"
              onClick={() => reseed.mutate()}
              disabled={reseed.isPending || !canModify}
              title={canModify ? 'Reseed from config' : denyTitle}
              className="inline-flex items-center gap-2 rounded-xl border border-border bg-card px-4 py-2 text-sm font-medium hover:bg-muted transition-colors disabled:opacity-50"
            >
              <RefreshCw className={`h-4 w-4 ${reseed.isPending ? 'animate-spin' : ''}`} />
              Reseed from config
            </button>
            <button
              type="button"
              onClick={() => setShowCatalog(true)}
              disabled={!canModify}
              title={canModify ? 'Import models from the provider catalog' : denyTitle}
              className="inline-flex items-center gap-2 rounded-xl border border-border bg-card px-4 py-2 text-sm font-medium hover:bg-muted transition-colors disabled:opacity-50"
            >
              <Download className="h-4 w-4" /> Import catalog
            </button>
            <button
              type="button"
              onClick={openAdd}
              disabled={!canModify}
              title={canModify ? 'Add a model' : denyTitle}
              className="inline-flex items-center gap-2 rounded-xl bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:opacity-90 transition-opacity disabled:opacity-50"
            >
              <Plus className="h-4 w-4" /> Add Model
            </button>
          </div>
        </div>

        {access?.can_modify && access.via === 'tenant_admin' && (
          <p className="inline-flex items-center gap-1.5 text-xs text-emerald-700 dark:text-emerald-400">
            <ShieldCheck className="h-3.5 w-3.5" /> Signed in as platform admin — you can modify the registry.
          </p>
        )}
        {access && !access.can_modify && (
          <p className="text-xs text-amber-600 dark:text-amber-400">
            Viewing is open to your tenant.{' '}
            {access.reason || 'Adding, removing, reordering, or reseeding models is a platform-operator action.'}
          </p>
        )}

        {isLoading && <p className="text-sm text-muted-foreground">Loading models…</p>}
        {isError && <p className="text-sm text-destructive">Failed to load the model registry.</p>}

        {!isLoading && !isError && data && (
          <div
            data-testid="capability-coverage"
            className="flex flex-wrap gap-2 rounded-2xl border border-border bg-card px-4 py-3 text-xs"
          >
            {CAPABILITIES.map((cap) => {
              const { state, ready } = coverageOf(groupFor(cap.key));
              const cls = state === 'ready'
                ? 'border-emerald-300 bg-emerald-50 text-emerald-800 dark:border-emerald-800 dark:bg-emerald-900/20 dark:text-emerald-300'
                : state === 'not_ready'
                  ? 'border-amber-300 bg-amber-50 text-amber-800 dark:border-amber-800 dark:bg-amber-900/20 dark:text-amber-300'
                  : 'border-border text-muted-foreground';
              const text = state === 'ready'
                ? `ready (${ready})`
                : state === 'not_ready' ? 'not usable' : 'not configured';
              return (
                <span
                  key={cap.key}
                  data-testid={`coverage-${cap.key}`}
                  data-state={state}
                  className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 ${cls}`}
                >
                  {state === 'ready' ? <Check className="h-3 w-3" /> : null}
                  <strong className="font-semibold">{cap.label}</strong> {text}
                </span>
              );
            })}
          </div>
        )}

        {!isLoading && CAPABILITIES.map((cap) => {
          const group = groupFor(cap.key);
          const serverModels = group?.models ?? [];
          const draft = drafts[cap.key];
          const byKey = new Map(serverModels.map((m) => [keyOf(m), m]));
          const models = draft
            ? draft.map((k) => byKey.get(k)).filter((m): m is ConfiguredModel => !!m)
            : serverModels;
          const dirty = !!draft && draft.join('|') !== serverModels.map(keyOf).join('|');

          // Primary/fallback badges: the server's view when the order is saved,
          // a local preview (first ready model, then the next ready ones) while
          // an unsaved reordering is pending.
          let primaryIdx = -1;
          const fallbackNo = new Map<number, number>();
          if (dirty) {
            let n = 0;
            models.forEach((m, i) => {
              if (!usable(m) || m.refused) return;
              if (primaryIdx === -1) primaryIdx = i;
              else fallbackNo.set(i, ++n);
            });
          } else if (group) {
            primaryIdx = models.findIndex(
              (m) => m.model_id === group.selected_model_id && !m.refused,
            );
            // A refused row (e.g. wrong embedding dimension) is never badged primary.
            if (primaryIdx === -1) primaryIdx = models.findIndex((m) => m.rank === 1 && !m.refused);
            models.forEach((m, i) => {
              const fb = group.fallback_model_ids.indexOf(m.model_id);
              if (fb !== -1 && i !== primaryIdx) fallbackNo.set(i, fb + 1);
            });
          }
          const anyNotReady = models.some((m) => !m.provider_ready);
          const anyNotServed = models.some((m) => m.provider_ready && m.servable === false);
          const coverage = coverageOf(group);
          const busy = saveOrder.isPending || resetOrder.isPending;

          return (
            <div key={cap.key} className="rounded-2xl border border-border bg-card p-5">
              <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
                <div>
                  <h2 className="text-lg font-semibold">{cap.label}</h2>
                  <p className="text-xs text-muted-foreground">{cap.hint}</p>
                </div>
                <div className="flex items-center gap-3 text-xs text-muted-foreground">
                  {group && (
                    <span className="rounded-full border border-border px-2 py-0.5">
                      {group.order_mode === 'preference' ? 'Preference order' : 'Cheapest first'}
                    </span>
                  )}
                  {coverage.state === 'not_ready' && (
                    <span
                      className="rounded-full bg-amber-100 px-2 py-0.5 font-semibold text-amber-800 dark:bg-amber-900/40 dark:text-amber-300"
                      title="Models are listed, but none of them can serve this category right now"
                    >
                      Not usable
                    </span>
                  )}
                  <span data-testid={`capability-count-${cap.key}`}>
                    {group && group.models.length > 0
                      ? `${coverage.ready} of ${models.length} ready`
                      : `${models.length} configured`}
                  </span>
                </div>
              </div>
              {group?.note && (
                <p className="mb-3 flex items-start gap-1.5 text-xs text-muted-foreground">
                  <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" /> {group.note}
                </p>
              )}
              {models.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  No model configured for {cap.label.toLowerCase()}.
                </p>
              ) : (
                <ol className="space-y-2">
                  {models.map((m, i) => {
                    const primary = i === primaryIdx;
                    const fb = fallbackNo.get(i);
                    return (
                      <li
                        key={keyOf(m)}
                        className={`flex items-center justify-between rounded-xl border px-4 py-3 ${
                          primary
                            ? 'border-emerald-400 bg-emerald-50 dark:bg-emerald-900/20'
                            : 'border-border bg-background'
                        } ${usable(m) ? '' : 'opacity-60'}`}
                      >
                        <div className="flex min-w-0 items-center gap-3">
                          <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-semibold">
                            {i + 1}
                          </span>
                          <div className="min-w-0">
                            <div className="flex flex-wrap items-center gap-2">
                              <span className="truncate font-medium">{m.model_id}</span>
                              {primary && (
                                <span className="inline-flex items-center gap-1 rounded-full bg-emerald-600 px-2 py-0.5 text-[10px] font-semibold text-white">
                                  <Zap className="h-3 w-3" /> Primary
                                </span>
                              )}
                              {fb !== undefined && (
                                <span className="rounded-full bg-sky-100 px-2 py-0.5 text-[10px] font-semibold text-sky-800 dark:bg-sky-900/40 dark:text-sky-300">
                                  Fallback {fb}
                                </span>
                              )}
                              {!m.provider_ready && (
                                <span
                                  className="rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-semibold text-amber-800 dark:bg-amber-900/40 dark:text-amber-300"
                                  title="This provider has no API key configured, so the model is skipped at runtime"
                                >
                                  No API key
                                </span>
                              )}
                              {m.provider_ready && m.servable === false && (
                                <span
                                  className="rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-semibold text-amber-800 dark:bg-amber-900/40 dark:text-amber-300"
                                  title="Named in the server configuration, but no API key or endpoint is configured to serve it"
                                >
                                  Not served
                                </span>
                              )}
                              {m.has_api_key ? (
                                <span
                                  className="inline-flex items-center gap-1 rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-semibold text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300"
                                  title="This model has its own API key saved (encrypted; never shown)"
                                >
                                  <KeyRound className="h-3 w-3" /> Key saved
                                </span>
                              ) : usable(m) && (
                                <span
                                  className="rounded-full border border-border px-2 py-0.5 text-[10px] text-muted-foreground"
                                  title="No key saved with this model: the provider's key configured on the server is used"
                                >
                                  Provider key
                                </span>
                              )}
                              {m.source === 'env' && (
                                <span
                                  className="rounded-full border border-border px-2 py-0.5 text-[10px] text-muted-foreground"
                                  title="Seeded from environment configuration"
                                >
                                  env
                                </span>
                              )}
                            </div>
                            <div className="mt-0.5 text-xs text-muted-foreground">
                              {m.provider} · ${m.cost_per_1k_input.toFixed(5)}/1k in
                              {m.cost_per_1k_input === 0 && ' (self-hosted / free)'}
                              {m.supports_tools && ' · tools'}
                              {m.supports_vision && ' · vision'}
                              {m.output_dimensions ? ` · ${m.output_dimensions}-d requested` : ''}
                              {m.base_url && (
                                <>
                                  {' · '}
                                  <span
                                    title={m.base_url}
                                    aria-label={`Endpoint ${m.base_url}`}
                                    className="inline-flex items-center gap-1 font-mono"
                                  >
                                    <Plug className="h-3 w-3" />
                                    {endpointHost(m.base_url)}
                                  </span>
                                </>
                              )}
                            </div>
                          </div>
                        </div>
                        <div className="ml-3 flex shrink-0 items-center gap-1">
                          {canModify && models.length > 1 && (
                            <>
                              <button
                                type="button"
                                onClick={() => group && move(group, i, i - 1)}
                                disabled={i === 0 || busy}
                                aria-label={`Move ${m.model_id} up`}
                                title="Move up"
                                className="rounded-lg p-2 text-muted-foreground hover:bg-muted disabled:opacity-30"
                              >
                                <ArrowUp className="h-4 w-4" />
                              </button>
                              <button
                                type="button"
                                onClick={() => group && move(group, i, i + 1)}
                                disabled={i === models.length - 1 || busy}
                                aria-label={`Move ${m.model_id} down`}
                                title="Move down"
                                className="rounded-lg p-2 text-muted-foreground hover:bg-muted disabled:opacity-30"
                              >
                                <ArrowDown className="h-4 w-4" />
                              </button>
                            </>
                          )}
                          {canModify && (
                            <button
                              type="button"
                              onClick={() => openEdit(m)}
                              aria-label={`Edit ${m.model_id}`}
                              title="Edit model"
                              className="rounded-lg p-2 text-muted-foreground hover:bg-muted"
                            >
                              <Pencil className="h-4 w-4" />
                            </button>
                          )}
                          <button
                            type="button"
                            onClick={() => remove.mutate(m)}
                            disabled={remove.isPending || !canModify}
                            title={canModify ? 'Remove model' : denyTitle}
                            className="rounded-lg p-2 text-muted-foreground hover:bg-destructive/10 hover:text-destructive transition-colors disabled:opacity-40"
                            aria-label={`Remove ${m.model_id}`}
                          >
                            <Trash2 className="h-4 w-4" />
                          </button>
                        </div>
                      </li>
                    );
                  })}
                </ol>
              )}
              {anyNotReady && (
                <p className="mt-2 text-xs text-muted-foreground">
                  Models marked “No API key” are skipped at runtime until their provider key is set
                  or the model is saved with its own API key or endpoint URL.
                </p>
              )}
              {anyNotServed && (
                <p className="mt-2 text-xs text-muted-foreground">
                  Models marked “Not served” are named in the server configuration, but nothing is
                  configured to call them; add the model here with its endpoint URL or API key.
                </p>
              )}
              {group && canModify && models.length > 0 && (
                <div className="mt-3 flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    onClick={() => saveOrder.mutate({ cap: cap.key, order: models.map(keyOf) })}
                    disabled={!dirty || busy}
                    aria-label={`Save ${cap.label} order`}
                    className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground hover:opacity-90 disabled:opacity-50"
                  >
                    {saveOrder.isPending && saveOrder.variables?.cap === cap.key
                      ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      : <Save className="h-3.5 w-3.5" />}
                    Save order
                  </button>
                  {dirty && (
                    <button
                      type="button"
                      onClick={() => clearDraft(cap.key)}
                      disabled={busy}
                      className="rounded-lg border border-border px-3 py-1.5 text-xs hover:bg-muted disabled:opacity-50"
                    >
                      Discard changes
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => resetOrder.mutate(cap.key)}
                    disabled={busy || (group.order_mode === 'cost' && !dirty)}
                    aria-label={`Reset ${cap.label} to cost order`}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs hover:bg-muted disabled:opacity-50"
                  >
                    <RotateCcw className="h-3.5 w-3.5" /> Reset to cost order
                  </button>
                  {dirty && <span className="text-xs text-amber-600 dark:text-amber-400">Unsaved order</span>}
                </div>
              )}
              {orderError[cap.key] && (
                <p role="alert" className="mt-2 text-xs text-destructive">{orderError[cap.key]}</p>
              )}
            </div>
          );
        })}
      </div>

      {showCatalog && (
        <CatalogImportDialog
          adminKey={adminKey}
          canModify={canModify}
          onClose={() => setShowCatalog(false)}
          onImported={invalidate}
        />
      )}

      {showModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="mr-modal-title"
            className="flex max-h-[90vh] w-full max-w-lg flex-col rounded-2xl bg-card shadow-xl"
          >
            <div className="border-b border-border px-6 py-4">
              <h3 id="mr-modal-title" className="text-lg font-semibold">
                {editingKey ? 'Edit model' : 'Add / override a model'}
              </h3>
              <p className="mt-1 text-xs text-muted-foreground">
                Register a model your provider can serve. Set its cost so the cheapest one is used
                first when no preference order is saved for its capability.
              </p>
            </div>
            <div className="flex-1 space-y-4 overflow-y-auto px-6 py-4">
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label htmlFor="mr-model-id" className="mb-1 block text-xs font-medium text-muted-foreground">Model ID *</label>
                  <input
                    id="mr-model-id"
                    value={form.model_id}
                    onChange={(e) => setField('model_id', e.target.value)}
                    placeholder="e.g. openai/gpt-oss-20b"
                    className={INPUT_CLS}
                  />
                </div>
                <div>
                  <label htmlFor="mr-provider" className="mb-1 block text-xs font-medium text-muted-foreground">Provider</label>
                  <select
                    id="mr-provider"
                    value={form.provider}
                    onChange={(e) => setField('provider', e.target.value)}
                    className={INPUT_CLS}
                  >
                    {/* Keep an unknown provider from an existing row selectable when editing. */}
                    {!(PROVIDERS as readonly string[]).includes(form.provider) && form.provider && (
                      <option value={form.provider}>{form.provider}</option>
                    )}
                    {PROVIDERS.map((p) => <option key={p} value={p}>{p}</option>)}
                  </select>
                </div>
              </div>
              {editingKey && (
                <p className="-mt-2 text-xs text-muted-foreground">
                  Model ID and provider identify the entry — changing either creates a new entry
                  instead of updating <span className="font-mono">{editingKey}</span>.
                </p>
              )}
              <div>
                <label htmlFor="mr-base-url" className="mb-1 block text-xs font-medium text-muted-foreground">
                  Endpoint URL (optional)
                </label>
                <input
                  id="mr-base-url"
                  type="url"
                  inputMode="url"
                  value={form.base_url}
                  onChange={(e) => setField('base_url', e.target.value)}
                  placeholder="http://192.168.63.104:30080/v1"
                  aria-describedby="mr-base-url-help"
                  className={INPUT_CLS}
                />
                <p id="mr-base-url-help" className="mt-1 text-xs text-muted-foreground">
                  Base URL of an OpenAI-compatible server (vLLM, Ollama /v1, on-prem). Leave empty to
                  use the provider's configured API.
                </p>
              </div>
              <div>
                <div className="mb-1 flex items-center justify-between gap-2">
                  <label htmlFor="mr-api-key" className="block text-xs font-medium text-muted-foreground">
                    API key
                  </label>
                  {form.has_api_key && !form.clear_api_key && (
                    <span className="flex items-center gap-2">
                      <span
                        data-testid="api-key-saved"
                        className="inline-flex items-center gap-1 rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-semibold text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300"
                      >
                        <KeyRound className="h-3 w-3" /> Key saved
                      </span>
                      <button
                        type="button"
                        onClick={() => setField('clear_api_key', true)}
                        className="text-xs text-destructive hover:underline"
                      >
                        Remove key
                      </button>
                    </span>
                  )}
                  {form.has_api_key && form.clear_api_key && (
                    <span className="flex items-center gap-2 text-xs text-amber-600 dark:text-amber-400">
                      The saved key is removed on Save.
                      <button
                        type="button"
                        onClick={() => setField('clear_api_key', false)}
                        className="text-primary hover:underline"
                      >
                        Undo
                      </button>
                    </span>
                  )}
                </div>
                <input
                  id="mr-api-key"
                  type="password"
                  autoComplete="new-password"
                  spellCheck={false}
                  value={form.api_key}
                  onChange={(e) => setField('api_key', e.target.value)}
                  placeholder="Leave empty to keep the saved key / use the provider's configured key"
                  aria-describedby="mr-api-key-help"
                  className={INPUT_CLS}
                />
                <p id="mr-api-key-help" className="mt-1 text-xs text-muted-foreground">
                  Sent as the Bearer key to this model's endpoint. Stored encrypted on the server and
                  never shown again.
                </p>
                <div className="mt-2">
                  <button
                    type="button"
                    onClick={() => testConn.mutate({ f: form, sig: testSignature(form) })}
                    disabled={!canModify || !form.model_id.trim() || !form.base_url.trim() || testConn.isPending}
                    title={canModify ? 'Probe the endpoint with this model' : denyTitle}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs font-medium hover:bg-muted disabled:opacity-50"
                  >
                    {testConn.isPending
                      ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      : <Plug className="h-3.5 w-3.5" />}
                    Test connection
                  </button>
                </div>
                {testOutcome && testOutcome.sig === testSignature(form) && (
                  <TestOutcomeBlock outcome={testOutcome} modelId={form.model_id.trim()} />
                )}
              </div>
              <div>
                <label htmlFor="mr-display-name" className="mb-1 block text-xs font-medium text-muted-foreground">Display name</label>
                <input
                  id="mr-display-name"
                  value={form.display_name}
                  onChange={(e) => setField('display_name', e.target.value)}
                  placeholder="Optional"
                  className={INPUT_CLS}
                />
              </div>
              <div className="grid grid-cols-3 gap-3">
                <div>
                  <label htmlFor="mr-cost-in" className="mb-1 block text-xs font-medium text-muted-foreground">Cost / 1k input ($)</label>
                  <input
                    id="mr-cost-in"
                    type="number" step="0.00001" min="0"
                    value={form.cost_per_1k_input}
                    onChange={(e) => setField('cost_per_1k_input', e.target.value)}
                    className={INPUT_CLS}
                  />
                </div>
                <div>
                  <label htmlFor="mr-cost-out" className="mb-1 block text-xs font-medium text-muted-foreground">Cost / 1k output ($)</label>
                  <input
                    id="mr-cost-out"
                    type="number" step="0.00001" min="0"
                    value={form.cost_per_1k_output}
                    onChange={(e) => setField('cost_per_1k_output', e.target.value)}
                    className={INPUT_CLS}
                  />
                </div>
                <div>
                  <label htmlFor="mr-quality" className="mb-1 block text-xs font-medium text-muted-foreground">Quality (0–1)</label>
                  <input
                    id="mr-quality"
                    type="number" step="0.05" min="0" max="1"
                    value={form.quality_score}
                    onChange={(e) => setField('quality_score', e.target.value)}
                    className={INPUT_CLS}
                  />
                </div>
              </div>
              <div>
                <span className="mb-1 block text-xs font-medium text-muted-foreground">Capabilities *</span>
                <div className="flex flex-wrap gap-2">
                  {CAPABILITIES.map((c) => (
                    <button
                      key={c.key}
                      type="button"
                      onClick={() => toggleCap(c.key)}
                      aria-pressed={form.capabilities.includes(c.key)}
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
                {embeddingSelected && looksLikeChatModel(form.model_id) && (
                  <p
                    data-testid="chat-model-hint"
                    className="mt-2 flex items-start gap-1.5 text-xs text-amber-700 dark:text-amber-400"
                  >
                    <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                    <span>
                      This looks like a chat model; embedding models are usually named
                      …-embedding-… (e.g. <span className="font-mono">gemini-embedding-001</span>,{' '}
                      <span className="font-mono">text-embedding-3-small</span>).
                    </span>
                  </p>
                )}
              </div>
              {embeddingSelected && (
                <div>
                  <label htmlFor="mr-output-dims" className="mb-1 block text-xs font-medium text-muted-foreground">
                    Output dimensions (optional)
                  </label>
                  <input
                    id="mr-output-dims"
                    type="number"
                    min="1"
                    max={MAX_OUTPUT_DIMENSIONS}
                    step="1"
                    value={form.output_dimensions}
                    onChange={(e) => setField('output_dimensions', e.target.value)}
                    placeholder="Native width"
                    aria-describedby="mr-output-dims-help"
                    className={INPUT_CLS}
                  />
                  <p id="mr-output-dims-help" className="mt-1 text-xs text-muted-foreground">
                    Vector width to request (sent as <span className="font-mono">dimensions</span>) from
                    models that can shorten their vectors, e.g. gemini-embedding-001 (768 / 1536 / 3072)
                    or text-embedding-3-*. It must equal the vector index width
                    {indexDimension ? <> (<strong>{indexDimension}</strong>, EMBEDDING_DIM)</> : ' (EMBEDDING_DIM)'}.
                  </p>
                  {Number.isNaN(outputDims) && (
                    <p className="mt-1 text-xs text-destructive">
                      Enter a whole number from 1 to {MAX_OUTPUT_DIMENSIONS}.
                    </p>
                  )}
                  {!!outputDims && !!indexDimension && outputDims !== indexDimension && (
                    <p className="mt-1 flex items-start gap-1.5 text-xs text-amber-700 dark:text-amber-400">
                      <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                      {outputDims}-d does not match the {indexDimension}-d vector index: the model
                      will be refused for embeddings.
                    </p>
                  )}
                </div>
              )}
              <div className="flex gap-4">
                <label className="flex items-center gap-2 text-sm">
                  <input type="checkbox" checked={form.supports_tools}
                    onChange={(e) => setField('supports_tools', e.target.checked)}
                    className="h-4 w-4 rounded border-input accent-primary" />
                  Supports tools
                </label>
                <label className="flex items-center gap-2 text-sm">
                  <input type="checkbox" checked={form.supports_vision}
                    onChange={(e) => setField('supports_vision', e.target.checked)}
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
              <button onClick={closeModal} className="rounded-lg border border-border px-4 py-2 text-sm hover:bg-accent">Cancel</button>
              <button
                onClick={() => {
                  if (!form.model_id.trim() || form.capabilities.length === 0) {
                    setFormError('Model ID and at least one capability are required.');
                    return;
                  }
                  if (embeddingSelected && Number.isNaN(outputDims)) {
                    setFormError(`Output dimensions must be a whole number from 1 to ${MAX_OUTPUT_DIMENSIONS}.`);
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

const MAX_SERVED_SHOWN = 8;

function TestOutcomeBlock({ outcome, modelId }: { outcome: TestOutcome; modelId: string }) {
  if (outcome.kind === 'error' || !outcome.result.ok) {
    const message = outcome.kind === 'error'
      ? outcome.message
      : outcome.result.error || outcome.result.detail || 'Connection failed';
    return (
      <div
        role="alert"
        data-testid="endpoint-test-result"
        className="mt-2 flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-destructive dark:border-red-800 dark:bg-red-900/20"
      >
        <XCircle className="mt-0.5 h-4 w-4 flex-shrink-0" />
        {isApiKeyFailure(message) ? (
          <span className="min-w-0 space-y-1 break-words">
            <span className="block font-medium">{API_KEY_REJECTED_HINT}</span>
            <span className="block opacity-80">Connection failed: {message}</span>
          </span>
        ) : (
          <span className="min-w-0 break-words">Connection failed: {message}</span>
        )}
      </div>
    );
  }
  const r = outcome.result;
  const served = r.served_models ?? [];
  const shown = served.slice(0, MAX_SERVED_SHOWN).join(', ');
  const more = served.length > MAX_SERVED_SHOWN ? ` (+${served.length - MAX_SERVED_SHOWN} more)` : '';
  return (
    <div
      role="status"
      data-testid="endpoint-test-result"
      className="mt-2 space-y-1 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-800 dark:border-emerald-800 dark:bg-emerald-900/20 dark:text-emerald-300"
    >
      <p className="flex items-center gap-1.5 font-medium">
        <CircleCheck className="h-4 w-4 flex-shrink-0" />
        Connected · {Math.round(r.latency_ms)} ms · {r.probe}
      </p>
      {r.detail && <p className="break-words">{r.detail}</p>}
      {r.probe === 'embedding' && !!r.dimensions && (
        <p data-testid="endpoint-test-dimensions">
          Vector width {r.dimensions}-d
          {r.requested_dimensions ? ` (requested ${r.requested_dimensions})` : ''}
          {r.index_dimension ? ` · vector index ${r.index_dimension}-d (EMBEDDING_DIM)` : ''}
        </p>
      )}
      {r.dimensions_ignored && (
        <p className="flex items-start gap-1.5 text-amber-700 dark:text-amber-400">
          <TriangleAlert className="mt-0.5 h-3.5 w-3.5 flex-shrink-0" />
          <span className="min-w-0 break-words">
            The endpoint ignored the requested output dimensions: asked for{' '}
            {r.requested_dimensions}, got {r.dimensions}.
          </span>
        </p>
      )}
      {r.dimension_mismatch && (
        <p
          role="alert"
          data-testid="endpoint-test-dimension-mismatch"
          className="flex items-start gap-1.5 font-medium text-amber-700 dark:text-amber-400"
        >
          <TriangleAlert className="mt-0.5 h-3.5 w-3.5 flex-shrink-0" />
          <span className="min-w-0 break-words">
            Dimension mismatch: the model returns {r.dimensions}-d vectors but the vector index is{' '}
            {r.index_dimension}-d, so it will be refused for embeddings. Set Output dimensions to{' '}
            {r.index_dimension} if the model supports it, or set EMBEDDING_DIM={r.dimensions} and
            re-embed existing collections.
          </span>
        </p>
      )}
      {r.model_listed === false && (
        <p className="flex items-start gap-1.5 text-amber-700 dark:text-amber-400">
          <TriangleAlert className="mt-0.5 h-3.5 w-3.5 flex-shrink-0" />
          <span className="min-w-0 break-words">
            The server does not list {modelId}; it serves: {shown || 'no models'}{more}
          </span>
        </p>
      )}
    </div>
  );
}
