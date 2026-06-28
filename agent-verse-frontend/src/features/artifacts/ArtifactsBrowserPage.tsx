import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Trash2, Download, Eye, FileText } from 'lucide-react';
import { artifactsApi, type Artifact } from '@/lib/api/client';
import { toast } from '@/stores/toast';
import { Skeleton } from '@/components/ui/Skeleton';
import { EmptyState } from '@/components/ui/EmptyState';

function isHttpUri(uri: string): boolean {
  return /^https?:\/\//.test(uri);
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

export function ArtifactsBrowserPage() {
  const qc = useQueryClient();
  const [preview, setPreview] = useState<Artifact | null>(null);

  const { data: artifacts = [], isLoading } = useQuery({
    queryKey: ['artifacts'],
    queryFn: () => artifactsApi.list({ limit: 100 }),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => artifactsApi.delete(id),
    onSuccess: () => {
      toast({ kind: 'success', message: 'Artifact deleted.' });
      qc.invalidateQueries({ queryKey: ['artifacts'] });
    },
  });

  const previewable = useMemo(() => {
    if (!preview) return null;
    if (!isHttpUri(preview.storage_uri)) return { kind: 'ref' as const };
    if (preview.content_type.startsWith('image/')) return { kind: 'image' as const };
    if (preview.content_type.startsWith('text/')) return { kind: 'text' as const };
    return { kind: 'ref' as const };
  }, [preview]);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Artifacts</h1>
        <p className="text-muted-foreground text-sm mt-1">Files produced by agent runs</p>
      </div>

      <div className="bg-card border border-border rounded-xl overflow-hidden">
        {isLoading ? (
          <div className="p-5 space-y-2">
            <Skeleton className="h-12" />
            <Skeleton className="h-12" />
          </div>
        ) : artifacts.length === 0 ? (
          <EmptyState
            title="No artifacts"
            description="Agent runs that produce files will appear here."
          />
        ) : (
          <ul className="divide-y divide-border">
            {artifacts.map((a) => (
              <li key={a.id} className="px-5 py-3 flex items-center justify-between gap-3">
                <div className="flex items-center gap-3 min-w-0">
                  <FileText
                    className="h-5 w-5 text-muted-foreground flex-shrink-0"
                    aria-hidden="true"
                  />
                  <div className="min-w-0">
                    <p className="text-sm font-medium truncate">{a.name}</p>
                    <p className="text-xs text-muted-foreground">
                      {a.content_type} · {formatBytes(a.size_bytes)} · goal {a.goal_id.slice(0, 8)}
                    </p>
                  </div>
                </div>
                <div className="flex items-center gap-3 flex-shrink-0">
                  <button
                    aria-label="Preview artifact"
                    onClick={() => setPreview(a)}
                    className="text-muted-foreground hover:text-foreground"
                  >
                    <Eye className="h-4 w-4" />
                  </button>
                  <a
                    aria-label="Download artifact"
                    href={isHttpUri(a.storage_uri) ? a.storage_uri : undefined}
                    download={a.name}
                    target="_blank"
                    rel="noopener noreferrer"
                    className={
                      isHttpUri(a.storage_uri)
                        ? 'text-muted-foreground hover:text-foreground'
                        : 'text-muted-foreground/40 pointer-events-none'
                    }
                    title={
                      isHttpUri(a.storage_uri)
                        ? 'Download'
                        : 'No direct URL — stored at ' + a.storage_uri
                    }
                  >
                    <Download className="h-4 w-4" />
                  </a>
                  <button
                    aria-label="Delete artifact"
                    onClick={() => deleteMutation.mutate(a.id)}
                    className="text-muted-foreground hover:text-destructive"
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* Preview drawer */}
      {preview && previewable && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
          role="dialog"
          aria-label="Artifact preview"
          onClick={() => setPreview(null)}
        >
          <div
            className="bg-card border border-border rounded-xl max-w-3xl w-full max-h-[80vh] overflow-auto p-5"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between mb-3">
              <h3 className="font-semibold text-sm">{preview.name}</h3>
              <button
                aria-label="Close preview"
                onClick={() => setPreview(null)}
                className="text-muted-foreground hover:text-foreground"
              >
                ×
              </button>
            </div>
            {previewable.kind === 'image' && (
              <img
                src={preview.storage_uri}
                alt={preview.name}
                className="max-w-full rounded-md"
              />
            )}
            {previewable.kind === 'text' && (
              <iframe
                src={preview.storage_uri}
                title={preview.name}
                className="w-full h-96 border border-border rounded-md"
              />
            )}
            {previewable.kind === 'ref' && (
              <p className="text-sm text-muted-foreground font-mono break-all">
                {preview.storage_uri}
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
