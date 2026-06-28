import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Trash2, Search } from 'lucide-react';
import { memoryApi, type RecallResult } from '@/lib/api/client';
import { toast } from '@/stores/toast';
import { Skeleton } from '@/components/ui/Skeleton';
import { EmptyState } from '@/components/ui/EmptyState';

export function MemoryExplorerPage() {
  const qc = useQueryClient();
  const [query, setQuery] = useState('');
  const [recalled, setRecalled] = useState<RecallResult[] | null>(null);

  const { data: memories = [], isLoading } = useQuery({
    queryKey: ['memories'],
    queryFn: () => memoryApi.list({ limit: 100 }),
  });

  const { data: reliability = [] } = useQuery({
    queryKey: ['tool-reliability'],
    queryFn: () => memoryApi.toolReliability(),
  });

  const recallMutation = useMutation({
    mutationFn: () => memoryApi.recall(query, 10),
    onSuccess: (rows) => setRecalled(rows),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => memoryApi.delete(id),
    onSuccess: () => {
      toast({ kind: 'success', message: 'Memory deleted.' });
      qc.invalidateQueries({ queryKey: ['memories'] });
    },
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Memory Explorer</h1>
        <p className="text-muted-foreground text-sm mt-1">
          Long-term memories, semantic recall, and tool reliability
        </p>
      </div>

      {/* Recall */}
      <div className="bg-card border border-border rounded-xl p-4">
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (query.trim()) recallMutation.mutate();
          }}
        >
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Recall memories relevant to…"
            aria-label="Recall query"
            className="flex-1 px-3 py-2 border border-border rounded-md text-sm bg-background"
          />
          <button
            type="submit"
            disabled={recallMutation.isPending || !query.trim()}
            className="flex items-center gap-1.5 px-3 py-2 bg-primary text-primary-foreground rounded-md text-sm disabled:opacity-50"
          >
            <Search className="h-4 w-4" /> Recall
          </button>
        </form>
        {recalled && (
          <div className="mt-3 space-y-2">
            {recalled.length === 0 ? (
              <p className="text-sm text-muted-foreground">No relevant memories.</p>
            ) : (
              recalled.map((r, i) => (
                <div key={i} className="text-sm border border-border rounded-md p-2">
                  <p>{r.content}</p>
                  <p className="text-xs text-muted-foreground mt-1">
                    {r.memory_type} · confidence {(r.confidence * 100).toFixed(0)}%
                  </p>
                </div>
              ))
            )}
          </div>
        )}
      </div>

      {/* All memories */}
      <div className="bg-card border border-border rounded-xl overflow-hidden">
        <div className="px-5 py-4 border-b border-border">
          <h2 className="font-semibold text-sm">Long-term Memories</h2>
        </div>
        {isLoading ? (
          <div className="p-5 space-y-2">
            <Skeleton className="h-12" />
            <Skeleton className="h-12" />
          </div>
        ) : memories.length === 0 ? (
          <EmptyState
            title="No memories yet"
            description="Memories accumulate as agents complete goals."
          />
        ) : (
          <ul className="divide-y divide-border">
            {memories.map((m) => (
              <li key={m.id} className="px-5 py-3 flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-sm">{m.content}</p>
                  <p className="text-xs text-muted-foreground mt-0.5">
                    {m.memory_type} · {(m.confidence * 100).toFixed(0)}%
                    {m.tags.length > 0 && ` · ${m.tags.join(', ')}`}
                  </p>
                </div>
                <button
                  aria-label="Delete memory"
                  onClick={() => deleteMutation.mutate(m.id)}
                  className="text-muted-foreground hover:text-destructive flex-shrink-0"
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* Tool reliability */}
      <div className="bg-card border border-border rounded-xl overflow-hidden">
        <div className="px-5 py-4 border-b border-border">
          <h2 className="font-semibold text-sm">Tool Reliability</h2>
        </div>
        {reliability.length === 0 ? (
          <EmptyState
            title="All tools reliable"
            description="No tools below the reliability threshold."
          />
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-muted-foreground border-b border-border">
                <th className="px-5 py-2 font-medium">Tool</th>
                <th className="px-5 py-2 font-medium">Calls</th>
                <th className="px-5 py-2 font-medium">Failures</th>
                <th className="px-5 py-2 font-medium">Success rate</th>
              </tr>
            </thead>
            <tbody>
              {reliability.map((t) => (
                <tr key={t.tool_name} className="border-b border-border last:border-0">
                  <td className="px-5 py-2 font-mono text-xs">{t.tool_name}</td>
                  <td className="px-5 py-2">{t.total_calls}</td>
                  <td className="px-5 py-2 text-destructive">{t.failures}</td>
                  <td className="px-5 py-2">{(t.success_rate * 100).toFixed(0)}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
