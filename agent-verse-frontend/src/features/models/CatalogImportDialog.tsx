import { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { ChevronDown, ChevronRight, Download, Loader2, XCircle } from 'lucide-react';
import { modelsApi, type CatalogProvider } from '@/lib/api/client';
import { Badge } from './Badge';
import { PANEL_CLASSES, TEXT_TONE } from './badgeStyles';
import { Modal } from './Modal';

interface Props {
  adminKey: string;
  canModify: boolean;
  onClose: () => void;
  onImported: () => void;
}

type Selection = Record<string, string[]>;

/**
 * Builds the import body from the per-provider selection. Fully selected
 * providers go out as `providers`; once any provider is only partially
 * selected the whole request is expressed as explicit `model_ids` so the
 * backend never has to combine the two filters.
 */
function buildImportBody(
  providers: CatalogProvider[],
  selection: Selection,
): { providers?: string[]; model_ids?: string[] } {
  const full: string[] = [];
  let partial = false;
  const ids: string[] = [];
  for (const p of providers) {
    const picked = selection[p.provider] ?? [];
    if (picked.length === 0) continue;
    // provider/model_id keys: the same model id can be served by two providers.
    ids.push(...picked.map((id) => `${p.provider}/${id}`));
    if (picked.length === p.models.length) full.push(p.provider);
    else partial = true;
  }
  return partial ? { model_ids: ids } : { providers: full };
}

export function CatalogImportDialog({ adminKey, canModify, onClose, onImported }: Props) {
  const [selection, setSelection] = useState<Selection>({});
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [result, setResult] = useState('');
  const [error, setError] = useState('');

  const { data, isLoading, isError } = useQuery({
    queryKey: ['models-catalog'],
    queryFn: () => modelsApi.catalog(adminKey || undefined),
    staleTime: 30_000,
  });
  const providers = data?.providers ?? [];

  const importMut = useMutation({
    mutationFn: (body: { providers?: string[]; model_ids?: string[] }) =>
      modelsApi.importCatalog(body, adminKey || undefined),
    onSuccess: (r) => {
      setError('');
      setResult(`Imported ${r.imported} model${r.imported === 1 ? '' : 's'}, skipped ${r.skipped}.`);
      setSelection({});
      onImported();
    },
    onError: (e: Error) => {
      setResult('');
      setError(e.message || 'Import failed');
    },
  });

  const toggleProvider = (p: CatalogProvider) =>
    setSelection((s) => {
      const picked = s[p.provider] ?? [];
      return { ...s, [p.provider]: picked.length === p.models.length ? [] : p.models.map((m) => m.model_id) };
    });

  const toggleModel = (provider: string, modelId: string) =>
    setSelection((s) => {
      const picked = s[provider] ?? [];
      return {
        ...s,
        [provider]: picked.includes(modelId) ? picked.filter((m) => m !== modelId) : [...picked, modelId],
      };
    });

  const hasSelection = Object.values(selection).some((v) => v.length > 0);

  return (
    <Modal labelledBy="catalog-import-title" onClose={onClose} widthClass="max-w-2xl">
        <div className="border-b border-border px-6 py-4">
          <h3 id="catalog-import-title" className="text-lg font-semibold">Import from model catalog</h3>
          <p className="mt-1 text-xs text-muted-foreground">
            Add a provider's known models (with costs and capabilities) to the registry in one step.
            Models from providers without an API key are imported but skipped at runtime until the
            key is set. Models already configured are skipped.
          </p>
        </div>

        <div className="flex-1 space-y-2 overflow-y-auto px-6 py-4">
          {isLoading && <p className="text-sm text-muted-foreground">Loading catalog…</p>}
          {isError && <p className="text-sm text-destructive">Failed to load the model catalog.</p>}
          {providers.map((p) => {
            const picked = selection[p.provider] ?? [];
            const all = p.models.length > 0 && picked.length === p.models.length;
            const some = picked.length > 0 && !all;
            const open = !!expanded[p.provider];
            return (
              <div key={p.provider} className="rounded-xl border border-border bg-background">
                <div className="flex items-center gap-3 px-4 py-3">
                  <input
                    type="checkbox"
                    aria-label={`Select ${p.label}`}
                    checked={all}
                    ref={(el) => { if (el) el.indeterminate = some; }}
                    onChange={() => toggleProvider(p)}
                    disabled={!canModify || p.models.length === 0}
                    className="h-4 w-4 rounded border-input accent-primary"
                  />
                  <button
                    type="button"
                    onClick={() => setExpanded((e) => ({ ...e, [p.provider]: !open }))}
                    aria-expanded={open}
                    aria-label={`${open ? 'Hide' : 'Show'} ${p.label} models`}
                    className="flex min-w-0 flex-1 items-center gap-2 text-left"
                  >
                    {open ? <ChevronDown className="h-4 w-4 shrink-0" /> : <ChevronRight className="h-4 w-4 shrink-0" />}
                    <span className="truncate font-medium">{p.label}</span>
                    <span className="text-xs text-muted-foreground">
                      {p.models.length} model{p.models.length === 1 ? '' : 's'}
                    </span>
                  </button>
                  {p.ready ? (
                    <Badge tone="solid-success">Ready</Badge>
                  ) : (
                    <Badge tone="warning">No key — set {p.env_hint}</Badge>
                  )}
                </div>
                {open && (
                  <ul className="space-y-1 border-t border-border px-4 py-2">
                    {p.models.map((m) => (
                      <li key={m.model_id} className="flex items-center gap-3 text-sm">
                        <input
                          type="checkbox"
                          aria-label={`Select ${m.model_id}`}
                          checked={picked.includes(m.model_id)}
                          onChange={() => toggleModel(p.provider, m.model_id)}
                          disabled={!canModify}
                          className="h-4 w-4 rounded border-input accent-primary"
                        />
                        <span className="min-w-0 flex-1 truncate">
                          {m.display_name || m.model_id}
                          {m.base_url && (
                            <span
                              title={m.base_url}
                              className="ml-2 font-mono text-[10px] text-muted-foreground"
                            >
                              {m.base_url}
                            </span>
                          )}
                        </span>
                        <span className="text-xs text-muted-foreground">
                          {m.capabilities.join(', ')} · ${m.cost_per_1k_input.toFixed(5)}/1k in
                        </span>
                        {m.already_configured && (
                          <Badge tone="neutral">configured</Badge>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            );
          })}
          {result && <p role="status" className={`text-sm ${TEXT_TONE.success}`}>{result}</p>}
          {error && (
            <div role="alert" className={`flex items-start gap-2 rounded-lg border px-3 py-2 text-xs ${PANEL_CLASSES.danger}`}>
              <XCircle className="mt-0.5 h-4 w-4 flex-shrink-0" /> {error}
            </div>
          )}
        </div>

        <div className="flex justify-end gap-3 border-t border-border px-6 py-4">
          <button type="button" onClick={onClose} className="rounded-lg border border-border px-4 py-2 text-sm hover:bg-muted focus:outline-none focus:ring-2 focus:ring-ring">
            Close
          </button>
          <button
            type="button"
            onClick={() => importMut.mutate({})}
            disabled={!canModify || importMut.isPending || providers.length === 0}
            className="rounded-lg border border-border px-4 py-2 text-sm font-medium hover:bg-muted disabled:opacity-50"
          >
            Import all
          </button>
          <button
            type="button"
            onClick={() => importMut.mutate(buildImportBody(providers, selection))}
            disabled={!canModify || importMut.isPending || !hasSelection}
            className="inline-flex items-center gap-2 rounded-lg bg-primary px-5 py-2 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:opacity-50"
          >
            {importMut.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
            Import selected
          </button>
        </div>
    </Modal>
  );
}
