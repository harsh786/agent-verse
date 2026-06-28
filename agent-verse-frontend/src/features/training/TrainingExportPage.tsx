import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { Download } from 'lucide-react';
import { trainingApi } from '@/lib/api/client';
import { toast } from '@/stores/toast';

type Format = 'openai' | 'anthropic';

function triggerDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export function TrainingExportPage() {
  const [format, setFormat] = useState<Format>('openai');
  const [minScore, setMinScore] = useState(0.8);
  const [limit, setLimit] = useState(1000);
  const [lastCount, setLastCount] = useState<number | null>(null);

  const exportMutation = useMutation({
    mutationFn: () => trainingApi.export({ format, minScore, limit }),
    onSuccess: ({ blob, filename, count }) => {
      setLastCount(count);
      if (count === 0) {
        toast({ kind: 'info', message: 'No examples matched the filters.' });
        return;
      }
      triggerDownload(blob, filename);
      toast({ kind: 'success', message: `Exported ${count} examples.` });
    },
    onError: () => toast({ kind: 'error', message: 'Export failed.' }),
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Training-Data Export</h1>
        <p className="text-muted-foreground text-sm mt-1">
          Export high-scoring goal runs as JSONL for fine-tuning
        </p>
      </div>

      <div className="bg-card border border-border rounded-xl p-5 space-y-4 max-w-lg">
        <div>
          <label htmlFor="format" className="text-sm text-muted-foreground">
            Format
          </label>
          <select
            id="format"
            value={format}
            onChange={(e) => setFormat(e.target.value as Format)}
            className="w-full px-3 py-2 border border-border rounded-md text-sm bg-background"
          >
            <option value="openai">OpenAI</option>
            <option value="anthropic">Anthropic</option>
          </select>
        </div>
        <div>
          <label htmlFor="min-score" className="text-sm text-muted-foreground">
            Minimum eval score: {minScore.toFixed(2)}
          </label>
          <input
            id="min-score"
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={minScore}
            onChange={(e) => setMinScore(Number(e.target.value))}
            className="w-full"
          />
        </div>
        <div>
          <label htmlFor="limit" className="text-sm text-muted-foreground">
            Max examples
          </label>
          <input
            id="limit"
            type="number"
            min={1}
            max={10000}
            value={limit}
            onChange={(e) => setLimit(Number(e.target.value))}
            className="w-full px-3 py-2 border border-border rounded-md text-sm bg-background"
          />
        </div>
        <button
          onClick={() => exportMutation.mutate()}
          disabled={exportMutation.isPending}
          className="flex items-center gap-1.5 px-3 py-2 bg-primary text-primary-foreground rounded-md text-sm disabled:opacity-50"
        >
          <Download className="h-4 w-4" />{' '}
          {exportMutation.isPending ? 'Exporting…' : 'Export JSONL'}
        </button>
        {lastCount != null && (
          <p className="text-sm text-muted-foreground">Last export: {lastCount} examples.</p>
        )}
      </div>
    </div>
  );
}
