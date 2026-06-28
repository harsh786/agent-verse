import { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { Camera, ScanText, Sparkles } from 'lucide-react';
import { perceptionApi } from '@/lib/api/client';
import { toast } from '@/stores/toast';
import { StatusBadge } from '@/components/ui/StatusBadge';

export function PerceptionPage() {
  const [url, setUrl] = useState('');
  const [question, setQuestion] = useState('What is the main purpose and content of this page?');
  const [screenshot, setScreenshot] = useState<string | null>(null);
  const [analysis, setAnalysis] = useState<string | null>(null);
  const [extracted, setExtracted] = useState<{ text: string; charCount: number } | null>(null);

  const { data: status } = useQuery({
    queryKey: ['perception-status'],
    queryFn: () => perceptionApi.status(),
  });

  const playwrightOff = status ? !status.playwright_available : false;

  const screenshotMutation = useMutation({
    mutationFn: () => perceptionApi.screenshot(url, false),
    onSuccess: (r) => {
      if (!r.success) {
        toast({ kind: 'error', message: r.error ?? 'Screenshot failed.' });
        return;
      }
      setScreenshot(r.screenshot_b64);
    },
  });

  const analyzeMutation = useMutation({
    mutationFn: () =>
      perceptionApi.analyze(screenshot ? { screenshot_b64: screenshot, question } : { url, question }),
    onSuccess: (r) => setAnalysis(r.analysis),
  });

  const extractMutation = useMutation({
    mutationFn: () => perceptionApi.extract(url),
    onSuccess: (r) => {
      if (!r.success) {
        toast({ kind: 'error', message: r.error ?? 'Extraction failed.' });
        return;
      }
      setExtracted({ text: r.text, charCount: r.char_count });
    },
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Perception</h1>
        <p className="text-muted-foreground text-sm mt-1">
          Screenshot, analyze, and extract from web pages
        </p>
      </div>

      {/* Provider status */}
      <div className="bg-card border border-border rounded-xl p-4 flex flex-wrap gap-6">
        <div className="flex items-center gap-2">
          <span className="text-sm text-muted-foreground">Browser (Playwright)</span>
          <StatusBadge status={status?.playwright_available ? 'success' : 'failed'} />
        </div>
        <div className="flex items-center gap-2">
          <span className="text-sm text-muted-foreground">Vision LLM</span>
          <StatusBadge status={status?.vision_available ? 'success' : 'failed'} />
        </div>
      </div>

      {/* Controls */}
      <div className="bg-card border border-border rounded-xl p-4 space-y-3">
        <div>
          <label htmlFor="url" className="text-sm text-muted-foreground">
            URL
          </label>
          <input
            id="url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://example.com"
            className="w-full px-3 py-2 border border-border rounded-md text-sm bg-background"
          />
        </div>
        <div>
          <label htmlFor="question" className="text-sm text-muted-foreground">
            Analysis question
          </label>
          <input
            id="question"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            className="w-full px-3 py-2 border border-border rounded-md text-sm bg-background"
          />
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            onClick={() => screenshotMutation.mutate()}
            disabled={!url.trim() || playwrightOff || screenshotMutation.isPending}
            className="flex items-center gap-1.5 px-3 py-2 bg-primary text-primary-foreground rounded-md text-sm disabled:opacity-50"
          >
            <Camera className="h-4 w-4" /> Screenshot
          </button>
          <button
            onClick={() => analyzeMutation.mutate()}
            disabled={!url.trim() || playwrightOff || analyzeMutation.isPending}
            className="flex items-center gap-1.5 px-3 py-2 border border-border rounded-md text-sm disabled:opacity-50"
          >
            <Sparkles className="h-4 w-4" /> Analyze
          </button>
          <button
            onClick={() => extractMutation.mutate()}
            disabled={!url.trim() || playwrightOff || extractMutation.isPending}
            className="flex items-center gap-1.5 px-3 py-2 border border-border rounded-md text-sm disabled:opacity-50"
          >
            <ScanText className="h-4 w-4" /> Extract text
          </button>
        </div>
        {playwrightOff && (
          <p className="text-xs text-destructive">
            Playwright is unavailable on the server — browser actions are disabled.
          </p>
        )}
      </div>

      {screenshot && (
        <div className="bg-card border border-border rounded-xl p-4">
          <h2 className="font-semibold text-sm mb-2">Screenshot</h2>
          <img
            src={`data:image/png;base64,${screenshot}`}
            alt="Screenshot"
            className="max-w-full rounded-md border border-border"
          />
        </div>
      )}
      {analysis && (
        <div className="bg-card border border-border rounded-xl p-4">
          <h2 className="font-semibold text-sm mb-2">Analysis</h2>
          <p className="text-sm whitespace-pre-wrap">{analysis}</p>
        </div>
      )}
      {extracted && (
        <div className="bg-card border border-border rounded-xl p-4">
          <h2 className="font-semibold text-sm mb-2">
            Extracted text ({extracted.charCount} chars)
          </h2>
          <pre className="text-xs font-mono bg-muted rounded-md p-3 overflow-auto max-h-96 whitespace-pre-wrap">
            {extracted.text}
          </pre>
        </div>
      )}
    </div>
  );
}
