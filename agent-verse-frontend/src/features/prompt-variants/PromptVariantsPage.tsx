import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Braces, TrendingUp, TrendingDown, ChevronUp, Trash2, Plus, AlertCircle, Copy } from 'lucide-react';
import { apiFetch } from '@/lib/api/client';
import { JARVISPageShell, JARVISStagger, JARVISStaggerItem } from '@/components/ui/JARVISPageShell';
import { toast } from '@/stores/toast';

interface PromptVariant {
  variant_id: string;
  key: string;
  label: string;
  content: string;
  is_active: boolean;
  win_rate?: number;
  usage_count?: number;
  created_at: string;
}

export function PromptVariantsPage() {
  const qc = useQueryClient();
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [newKey, setNewKey] = useState('');
  const [newLabel, setNewLabel] = useState('');
  const [newContent, setNewContent] = useState('');

  const { data: variants = [], isLoading, isError } = useQuery<PromptVariant[]>({
    queryKey: ['prompt-variants'],
    queryFn: () => apiFetch<PromptVariant[]>('/intelligence/prompt-variants'),
  });

  const { data: keyVariants = [] } = useQuery<PromptVariant[]>({
    queryKey: ['prompt-variants', selectedKey],
    queryFn: () => apiFetch<PromptVariant[]>(`/intelligence/prompt-variants/${selectedKey}`),
    enabled: !!selectedKey,
  });

  const createVariant = useMutation({
    mutationFn: (body: { key: string; label: string; content: string }) =>
      apiFetch('/intelligence/prompt-variants', { method: 'POST', body: JSON.stringify(body) }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['prompt-variants'] });
      setShowCreate(false);
      setNewKey(''); setNewLabel(''); setNewContent('');
    },
  });

  const promoteVariant = useMutation({
    mutationFn: ({ key, variantId }: { key: string; variantId: string }) =>
      apiFetch(`/intelligence/prompt-variants/${key}/${variantId}/promote`, { method: 'POST' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['prompt-variants'] }),
  });

  const deleteVariant = useMutation({
    mutationFn: ({ key, variantId }: { key: string; variantId: string }) =>
      apiFetch(`/intelligence/prompt-variants/${key}/${variantId}`, { method: 'DELETE' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['prompt-variants'] }),
  });

  // Group variants by key
  const grouped = variants.reduce<Record<string, PromptVariant[]>>((acc, v) => {
    acc[v.key] = acc[v.key] ?? [];
    acc[v.key].push(v);
    return acc;
  }, {});

  const uniqueKeys = Object.keys(grouped);
  const displayVariants = selectedKey ? keyVariants : variants;

  return (
    <JARVISPageShell>
      <div className="p-6 max-w-6xl mx-auto space-y-6">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-[#F1F5F9] flex items-center gap-2.5">
              <Braces className="h-6 w-6 text-amber-400" />
              Prompt Variants
            </h1>
            <p className="text-sm text-[#64748B] mt-0.5">
              A/B test prompt templates and promote winners to production.
            </p>
          </div>
          <button
            onClick={() => setShowCreate(true)}
            className="flex items-center gap-2 px-4 py-2 rounded-xl bg-amber-500 hover:bg-amber-400 text-[#0A0D14] text-sm font-medium transition-colors"
          >
            <Plus className="h-4 w-4" /> New Variant
          </button>
        </div>

        {/* Key filter chips */}
        {uniqueKeys.length > 0 && (
          <div className="flex flex-wrap gap-2">
            <button
              onClick={() => setSelectedKey(null)}
              className={`px-3 py-1.5 rounded-full text-xs font-medium border transition-colors ${
                !selectedKey
                  ? 'bg-amber-500/20 border-amber-500/40 text-amber-400'
                  : 'border-[#1E2535] text-[#64748B] hover:border-amber-500/40 hover:text-amber-400'
              }`}
            >
              All
            </button>
            {uniqueKeys.map((k) => (
              <button
                key={k}
                onClick={() => setSelectedKey(k === selectedKey ? null : k)}
                className={`px-3 py-1.5 rounded-full text-xs font-medium border transition-colors ${
                  selectedKey === k
                    ? 'bg-amber-500/20 border-amber-500/40 text-amber-400'
                    : 'border-[#1E2535] text-[#64748B] hover:border-amber-500/40 hover:text-amber-400'
                }`}
              >
                {k}
                <span className="ml-1.5 opacity-60">({grouped[k]?.length ?? 0})</span>
              </button>
            ))}
          </div>
        )}

        {isError && (
          <div className="flex items-center gap-2 rounded-xl border border-rose-400/30 bg-rose-400/10 p-4 text-sm text-rose-400" role="alert">
            <AlertCircle className="h-4 w-4 shrink-0" />
            Failed to load prompt variants.
          </div>
        )}

        {isLoading ? (
          <div className="space-y-3">
            {[1, 2, 3].map((i) => <div key={i} className="h-28 rounded-xl bg-[#1A1F2E] animate-pulse border border-[#1E2535]" />)}
          </div>
        ) : displayVariants.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-20 text-[#475569] border border-[#1E2535] rounded-xl">
            <Braces className="h-12 w-12 mb-3 opacity-20" />
            <p className="font-medium text-[#94A3B8]">No prompt variants yet</p>
            <p className="text-sm mt-1">Create variants to start A/B testing prompt templates.</p>
          </div>
        ) : (
          <JARVISStagger className="space-y-3">
            {displayVariants.map((v) => (
              <JARVISStaggerItem key={v.variant_id} interactive>
                <div className="rounded-xl border border-[#1E2535] bg-[#1A1F2E] p-4 hover:border-[#2D3748] transition-colors">
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1">
                        <code className="text-xs bg-[#0F1117] border border-[#1E2535] px-2 py-0.5 rounded text-amber-400 font-mono">{v.key}</code>
                        <span className="font-semibold text-[#F1F5F9] text-sm">{v.label}</span>
                        {v.is_active && (
                          <span className="px-2 py-0.5 rounded-full bg-emerald-400/10 border border-emerald-400/20 text-emerald-400 text-[10px] font-medium">
                            Active
                          </span>
                        )}
                      </div>
                      <p className="text-xs text-[#64748B] line-clamp-2 font-mono bg-[#0F1117] p-2 rounded border border-[#1E2535] mt-2">
                        {v.content.slice(0, 120)}{v.content.length > 120 ? '…' : ''}
                      </p>
                      <div className="flex items-center gap-3 mt-2 text-xs text-[#475569]">
                        {v.usage_count !== undefined && <span>{v.usage_count.toLocaleString()} uses</span>}
                        {v.win_rate !== undefined && (
                          <span className={v.win_rate > 0.5 ? 'text-emerald-400' : 'text-rose-400'}>
                            {v.win_rate > 0.5 ? <TrendingUp className="inline h-3 w-3 mr-0.5" /> : <TrendingDown className="inline h-3 w-3 mr-0.5" />}
                            {Math.round(v.win_rate * 100)}% win rate
                          </span>
                        )}
                      </div>
                    </div>
                    <div className="flex flex-col gap-1.5 shrink-0">
                      <button
                        onClick={() => {
                          void navigator.clipboard.writeText(v.content);
                          toast({ kind: 'success', message: 'Copied to clipboard' });
                        }}
                        className="p-1.5 rounded-lg text-[#64748B] hover:text-[#94A3B8] hover:bg-[#252B3B] transition-colors"
                        title="Copy content"
                      >
                        <Copy className="h-3.5 w-3.5" />
                      </button>
                      {!v.is_active && (
                        <button
                          onClick={() => promoteVariant.mutate({ key: v.key, variantId: v.variant_id })}
                          className="p-1.5 rounded-lg text-emerald-400 hover:bg-emerald-400/10 transition-colors"
                          title="Promote to active"
                        >
                          <ChevronUp className="h-3.5 w-3.5" />
                        </button>
                      )}
                      <button
                        onClick={() => deleteVariant.mutate({ key: v.key, variantId: v.variant_id })}
                        className="p-1.5 rounded-lg text-[#64748B] hover:text-rose-400 hover:bg-rose-400/10 transition-colors"
                        title="Delete variant"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  </div>
                </div>
              </JARVISStaggerItem>
            ))}
          </JARVISStagger>
        )}

        {/* Create modal */}
        {showCreate && (
          <div className="fixed inset-0 z-50 flex items-center justify-center p-4" role="dialog" aria-modal="true">
            <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={() => setShowCreate(false)} />
            <div className="relative w-full max-w-lg rounded-2xl bg-[#1A1F2E] border border-[#2D3748] shadow-2xl p-6">
              <h2 className="text-lg font-semibold text-[#F1F5F9] mb-4">New Prompt Variant</h2>
              <div className="space-y-4">
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block text-xs font-medium text-[#94A3B8] mb-1.5">Key</label>
                    <input value={newKey} onChange={(e) => setNewKey(e.target.value)} placeholder="system_prompt"
                      className="w-full rounded-lg border border-[#1E2535] bg-[#0F1117] px-3 py-2 text-sm text-[#F1F5F9] placeholder-[#475569] focus:outline-none focus:border-amber-500 font-mono" />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-[#94A3B8] mb-1.5">Label</label>
                    <input value={newLabel} onChange={(e) => setNewLabel(e.target.value)} placeholder="Concise variant"
                      className="w-full rounded-lg border border-[#1E2535] bg-[#0F1117] px-3 py-2 text-sm text-[#F1F5F9] placeholder-[#475569] focus:outline-none focus:border-amber-500" />
                  </div>
                </div>
                <div>
                  <label className="block text-xs font-medium text-[#94A3B8] mb-1.5">Prompt Content</label>
                  <textarea value={newContent} onChange={(e) => setNewContent(e.target.value)}
                    placeholder="You are a helpful assistant…" rows={5}
                    className="w-full rounded-lg border border-[#1E2535] bg-[#0F1117] px-3 py-2 text-sm text-[#F1F5F9] placeholder-[#475569] focus:outline-none focus:border-amber-500 resize-none font-mono" />
                </div>
                <div className="flex gap-2 justify-end pt-2">
                  <button onClick={() => setShowCreate(false)}
                    className="px-4 py-2 rounded-lg border border-[#1E2535] text-sm text-[#94A3B8] hover:bg-[#252B3B] transition-colors">
                    Cancel
                  </button>
                  <button
                    onClick={() => createVariant.mutate({ key: newKey, label: newLabel, content: newContent })}
                    disabled={!newKey.trim() || !newContent.trim() || createVariant.isPending}
                    className="px-4 py-2 rounded-lg bg-amber-500 text-[#0A0D14] text-sm font-medium hover:bg-amber-400 disabled:opacity-50 transition-colors">
                    {createVariant.isPending ? 'Creating…' : 'Create Variant'}
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

export default PromptVariantsPage;
