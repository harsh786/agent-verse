import { useRef, useState } from 'react';
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, Download, Loader2, Plus, RefreshCw, ShieldCheck, Trash2 } from 'lucide-react';
import { JARVISPageShell } from '@/components/ui/JARVISPageShell';
import { Skeleton } from '@/components/ui/Skeleton';
import {
  modelsApi,
  type ConfiguredModel,
  type ModelRegistryAccess,
} from '@/lib/api/client';
import { useAuthStore } from '@/stores/auth';
import { toast } from '@/stores/toast';
import { STATE_CLASSES, TEXT_TONE } from './badgeStyles';
import { CatalogImportDialog } from './CatalogImportDialog';
import { CapabilitySection } from './CapabilitySection';
import { coverageOf, keyOf, type CapabilityMeta } from './registryRows';
import { Modal } from './Modal';
import { ModelFormDialog } from './ModelFormDialog';
import { EMPTY_FORM, formFromModel, type FormState } from './modelForm';
import { ResolvedModelsPanel } from './ResolvedModelsPanel';
import { useUnsavedChangesGuard } from './useUnsavedChangesGuard';

const CAPABILITIES: readonly CapabilityMeta[] = [
  { key: 'text_generation', label: 'Reasoning', hint: 'Planning, execution, verification', noun: 'reasoning model' },
  { key: 'embedding', label: 'Embeddings', hint: 'Vector search / RAG', noun: 'embedding model' },
  { key: 'vision', label: 'Vision', hint: 'Image understanding', noun: 'vision model' },
  { key: 'ocr', label: 'OCR', hint: 'Document text extraction', noun: 'OCR model' },
  { key: 'rerank', label: 'Reranker', hint: 'Retrieval reranking', noun: 'reranker' },
];

const errorText = (e: unknown, fallback: string) =>
  (e instanceof Error && e.message) || fallback;

export function ModelRegistryPage() {
  const apiKey = useAuthStore((s: { apiKey: string | null }) => s.apiKey);
  const qc = useQueryClient();
  // The open Add / Edit dialog: its initial form and the row being edited.
  const [dialog, setDialog] = useState<{ initial: FormState; editingKey: string | null; n: number } | null>(null);
  const dialogSeq = useRef(0);
  const [showCatalog, setShowCatalog] = useState(false);
  const [confirmRemove, setConfirmRemove] = useState<ConfiguredModel | null>(null);
  // Optional platform admin key. Kept IN MEMORY ONLY (never written to
  // localStorage/sessionStorage) so this sensitive credential is not persisted
  // in the browser — it is re-entered per session and cleared on reload.
  // Tenant admins on an operator tenant don't need it at all.
  const [adminKey, setAdminKey] = useState<string>('');
  // Unsaved local reorderings, keyed by capability → ordered model keys.
  const [drafts, setDrafts] = useState<Record<string, string[]>>({});
  const [orderError, setOrderError] = useState<Record<string, string>>({});
  const key = adminKey || undefined;

  const { data, isLoading, isError, error, refetch, isFetching } = useQuery({
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

  const groups = data?.capabilities ?? [];
  const groupFor = (cap: string) => groups.find((g) => g.capability === cap);
  // The vector index width (EMBEDDING_DIM), as reported on the embedding rows.
  const indexDimension = groupFor('embedding')?.models.find((m) => m.index_dimension)?.index_dimension;

  const isDirty = (cap: string) => {
    const draft = drafts[cap];
    if (!draft) return false;
    return draft.join('|') !== (groupFor(cap)?.models ?? []).map(keyOf).join('|');
  };
  const anyDirty = CAPABILITIES.some((c) => isDirty(c.key));
  useUnsavedChangesGuard(anyDirty);

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ['configured-models'] });
    void qc.invalidateQueries({ queryKey: ['models-catalog'] });
    void qc.invalidateQueries({ queryKey: ['models-resolution'] });
  };

  const openAdd = (capability?: string) => {
    const initial: FormState = capability
      ? {
          ...EMPTY_FORM,
          capabilities: [capability],
          supports_tools: capability === 'text_generation',
          supports_vision: capability === 'vision',
        }
      : EMPTY_FORM;
    setDialog({ initial, editingKey: null, n: ++dialogSeq.current });
  };
  const openEdit = (m: ConfiguredModel) =>
    setDialog({ initial: formFromModel(m), editingKey: keyOf(m), n: ++dialogSeq.current });

  const remove = useMutation({
    mutationFn: (m: ConfiguredModel) => modelsApi.deleteConfigured(m.provider, m.model_id, key),
    onSuccess: (_r, m) => {
      setConfirmRemove(null);
      invalidate();
      toast({ kind: 'success', message: `Removed ${m.model_id}` });
    },
    onError: (e: Error, m) => {
      setConfirmRemove(null);
      toast({ kind: 'error', message: `Could not remove ${m.model_id}: ${errorText(e, 'request failed')}` });
    },
  });

  const reseed = useMutation({
    mutationFn: () => modelsApi.reseed(key),
    onSuccess: (r) => {
      invalidate();
      toast({ kind: 'success', message: `Reseeded from config: ${r?.configured_models ?? 0} models` });
    },
    onError: (e: Error) => toast({ kind: 'error', message: `Reseed failed: ${errorText(e, 'request failed')}` }),
  });

  const clearDraft = (cap: string) => {
    const without = <T,>(rec: Record<string, T>) => {
      const next = { ...rec };
      delete next[cap];
      return next;
    };
    setDrafts(without);
    setOrderError(without);
  };

  const capLabel = (cap: string) => CAPABILITIES.find((c) => c.key === cap)?.label ?? cap;

  const saveOrder = useMutation({
    mutationFn: ({ cap, order }: { cap: string; order: string[] }) =>
      modelsApi.savePreference(cap, order, key),
    onSuccess: (_r, { cap }) => {
      clearDraft(cap);
      invalidate();
      toast({ kind: 'success', message: `${capLabel(cap)} order saved` });
    },
    onError: (e: Error, { cap }) => {
      const message = errorText(e, 'Failed to save order');
      setOrderError((s) => ({ ...s, [cap]: message }));
      toast({ kind: 'error', message: `Could not save the ${capLabel(cap)} order: ${message}` });
    },
  });

  const resetOrder = useMutation({
    mutationFn: (cap: string) => modelsApi.resetPreference(cap, key),
    onSuccess: (_r, cap) => {
      clearDraft(cap);
      invalidate();
      toast({ kind: 'success', message: `${capLabel(cap)} reset to cheapest-first` });
    },
    onError: (e: Error, cap) => {
      const message = errorText(e, 'Failed to reset order');
      setOrderError((s) => ({ ...s, [cap]: message }));
      toast({ kind: 'error', message: `Could not reset the ${capLabel(cap)} order: ${message}` });
    },
  });
  const busy = saveOrder.isPending || resetOrder.isPending;

  return (
    <JARVISPageShell>
      <div className="space-y-6">
        <div className="flex flex-col gap-3 xl:flex-row xl:items-start xl:justify-between">
          <div>
            <h1 className="text-2xl font-bold">Model Registry</h1>
            <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
              Register a model your provider can serve, then rank the models of each capability:
              the first usable one runs and the next ones are its automatic fallbacks. Models that
              cannot be served right now (no API key, no endpoint, a vector width that does not fit
              the index) are skipped and shown greyed out with the reason. With no saved order, the{' '}
              <strong>cheapest</strong> usable model runs first (self-hosted models count as free);
              models imported from the catalog follow your own until you rank them.
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
                autoComplete="off"
                className="rounded-xl border border-input bg-background px-3 py-2 text-sm text-foreground outline-none focus:ring-2 focus:ring-ring"
              />
            )}
            <button
              type="button"
              onClick={() => reseed.mutate()}
              disabled={reseed.isPending || !canModify}
              title={canModify ? 'Re-read the models configured in the server environment' : denyTitle}
              className="inline-flex items-center gap-2 rounded-xl border border-border bg-card px-4 py-2 text-sm font-medium transition-colors hover:bg-muted focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-50"
            >
              <RefreshCw className={`h-4 w-4 ${reseed.isPending ? 'animate-spin' : ''}`} aria-hidden="true" />
              Reseed from config
            </button>
            <button
              type="button"
              onClick={() => setShowCatalog(true)}
              disabled={!canModify}
              title={canModify ? 'Import models from the provider catalog' : denyTitle}
              className="inline-flex items-center gap-2 rounded-xl border border-border bg-card px-4 py-2 text-sm font-medium transition-colors hover:bg-muted focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-50"
            >
              <Download className="h-4 w-4" aria-hidden="true" /> Import catalog
            </button>
            <button
              type="button"
              onClick={() => openAdd()}
              disabled={!canModify}
              title={canModify ? 'Add a model' : denyTitle}
              className="inline-flex items-center gap-2 rounded-xl bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-opacity hover:opacity-90 focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-50"
            >
              <Plus className="h-4 w-4" aria-hidden="true" /> Add Model
            </button>
          </div>
        </div>

        {access?.can_modify && access.via === 'tenant_admin' && (
          <p className={`inline-flex items-center gap-1.5 text-xs ${TEXT_TONE.success}`}>
            <ShieldCheck className="h-3.5 w-3.5" aria-hidden="true" /> Signed in as platform admin — you can modify the registry.
          </p>
        )}
        {access && !access.can_modify && (
          <p className={`text-xs ${TEXT_TONE.warning}`}>
            Viewing is open to your tenant.{' '}
            {access.reason || 'Adding, removing, reordering, or reseeding models is a platform-operator action.'}
          </p>
        )}

        {isLoading && (
          <div data-testid="registry-loading" aria-busy="true" className="space-y-4">
            <span className="sr-only">Loading models…</span>
            <Skeleton className="h-12 w-full rounded-2xl" />
            {CAPABILITIES.map((c) => (
              <div key={c.key} className="space-y-2 rounded-2xl border border-border bg-card p-5">
                <Skeleton className="h-5 w-40" />
                <Skeleton className="h-12 w-full" />
                <Skeleton className="h-12 w-full" />
              </div>
            ))}
          </div>
        )}
        {isError && (
          <div role="alert" className="flex flex-wrap items-center gap-3 rounded-2xl border border-border bg-card px-4 py-3 text-sm">
            <span className="text-destructive">
              Failed to load the model registry{error instanceof Error && error.message ? `: ${error.message}` : '.'}
            </span>
            <button
              type="button"
              onClick={() => void refetch()}
              disabled={isFetching}
              className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs font-medium hover:bg-muted focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-50"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${isFetching ? 'animate-spin' : ''}`} aria-hidden="true" /> Retry
            </button>
          </div>
        )}

        {!isLoading && !isError && data && (
          <div
            data-testid="capability-coverage"
            className="flex flex-wrap gap-2 rounded-2xl border border-border bg-card px-4 py-3 text-xs"
          >
            {CAPABILITIES.map((cap) => {
              const { state, ready } = coverageOf(groupFor(cap.key));
              const cls = state === 'ready'
                ? STATE_CLASSES.chipReady
                : state === 'not_ready'
                  ? STATE_CLASSES.chipNotReady
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
                  {state === 'ready' ? <Check className="h-3 w-3" aria-hidden="true" /> : null}
                  <strong className="font-semibold">{cap.label}</strong> {text}
                </span>
              );
            })}
          </div>
        )}

        {!isLoading && !isError && data && <ResolvedModelsPanel enabled={!!apiKey} />}

        {/* On a load error nothing is known: no "No model yet" claims. */}
        {!isLoading && !isError && CAPABILITIES.map((cap) => (
          <CapabilitySection
            key={cap.key}
            cap={cap}
            group={groupFor(cap.key)}
            draft={drafts[cap.key]}
            canModify={canModify}
            denyTitle={denyTitle}
            busy={busy}
            saving={saveOrder.isPending && saveOrder.variables?.cap === cap.key}
            orderError={orderError[cap.key]}
            onReorder={(order) => setDrafts((d) => ({ ...d, [cap.key]: order }))}
            onSave={(order) => saveOrder.mutate({ cap: cap.key, order })}
            onDiscard={() => clearDraft(cap.key)}
            onReset={() => resetOrder.mutate(cap.key)}
            onEdit={openEdit}
            onRemove={(m) => setConfirmRemove(m)}
            onAdd={openAdd}
          />
        ))}
      </div>

      {showCatalog && (
        <CatalogImportDialog
          adminKey={adminKey}
          canModify={canModify}
          onClose={() => setShowCatalog(false)}
          onImported={invalidate}
        />
      )}

      {dialog && (
        <ModelFormDialog
          key={dialog.n}
          initial={dialog.initial}
          editingKey={dialog.editingKey}
          adminKey={key}
          canModify={canModify}
          denyTitle={denyTitle}
          indexDimension={indexDimension}
          onClose={() => setDialog(null)}
          onSaved={(modelId) => {
            invalidate();
            setDialog(null);
            toast({ kind: 'success', message: `Saved ${modelId}` });
          }}
        />
      )}

      {confirmRemove && (
        <Modal
          labelledBy="mr-remove-title"
          describedBy="mr-remove-desc"
          role="alertdialog"
          widthClass="max-w-md"
          onClose={() => { if (!remove.isPending) setConfirmRemove(null); }}
        >
          <div className="space-y-3 p-6">
            <h3 id="mr-remove-title" className="flex items-center gap-2 text-base font-semibold">
              <Trash2 className="h-4 w-4 text-destructive" aria-hidden="true" />
              Remove {confirmRemove.model_id}?
            </h3>
            <p id="mr-remove-desc" className="text-sm text-muted-foreground">
              The {confirmRemove.provider} entry is removed from the registry for every tenant
              {confirmRemove.capabilities.length > 0
                ? ` (${confirmRemove.capabilities.map(capLabel).join(', ')})`
                : ''}
              . {confirmRemove.source === 'env'
                ? 'It comes from the server environment, so it reappears from the configuration; only changes saved here are dropped.'
                : 'Its saved API key is deleted with it.'}
            </p>
            <div className="flex justify-end gap-3 pt-2">
              <button
                type="button"
                onClick={() => setConfirmRemove(null)}
                disabled={remove.isPending}
                className="rounded-lg border border-border px-4 py-2 text-sm hover:bg-muted focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => remove.mutate(confirmRemove)}
                disabled={remove.isPending}
                className="inline-flex items-center gap-2 rounded-lg bg-destructive px-4 py-2 text-sm font-medium text-destructive-foreground hover:opacity-90 focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-50"
              >
                {remove.isPending && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
                Remove
              </button>
            </div>
          </div>
        </Modal>
      )}
    </JARVISPageShell>
  );
}
