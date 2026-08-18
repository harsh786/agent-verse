import { useState } from 'react';
import { useQuery, useMutation } from '@tanstack/react-query';
import {
  Activity, Zap, Brain, Eye, Wrench, RefreshCw,
  CheckCircle, XCircle, Loader2,
} from 'lucide-react';
import { toast } from '@/stores/toast';
import { StatusOrb } from '@/components/ui/StatusOrb';

export function ModelControlCenter() {
  const [selectedProvider, setSelectedProvider] = useState<string | null>(null);
  const [testingModel, setTestingModel] = useState<string | null>(null);

  const { data: modelsData, isLoading, refetch } = useQuery({
    queryKey: ['models', selectedProvider],
    queryFn: async () => {
      const { apiFetch } = await import('@/lib/api/client');
      return apiFetch<any>(`/models${selectedProvider ? `?provider=${selectedProvider}` : ''}`);
    },
    staleTime: 30_000,
  });

  const { data: healthData } = useQuery({
    queryKey: ['models-health'],
    queryFn: async () => {
      const { apiFetch } = await import('@/lib/api/client');
      return apiFetch<any>('/models/health');
    },
    refetchInterval: 30_000,
  });

  const testMutation = useMutation({
    mutationFn: async ({ provider, model_id }: { provider: string; model_id: string }) => {
      const { apiFetch } = await import('@/lib/api/client');
      return apiFetch<any>('/models/test', {
        method: 'POST',
        body: JSON.stringify({ provider, model_id }),
      });
    },
    onSuccess: (data) => {
      if (data.status === 'ok') {
        toast({ kind: 'success', message: `${data.model} responded in ${data.latency_ms}ms` });
      } else {
        toast({ kind: 'error', message: `Test failed: ${data.error ?? data.reason}` });
      }
      setTestingModel(null);
    },
    onError: (e) => {
      toast({ kind: 'error', message: String(e) });
      setTestingModel(null);
    },
  });

  const models: any[] = modelsData?.models ?? [];
  const providers: string[] = [
    ...new Set<string>(models.map((m: any) => String(m.provider))),
  ];

  const CAPABILITY_ICONS: Record<string, React.ElementType> = {
    tool_use: Wrench,
    vision: Eye,
    embedding: Brain,
    text_generation: Activity,
  };

  const PROVIDER_COLORS: Record<string, string> = {
    anthropic: 'bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-300',
    openai: 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-300',
    gemini: 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-300',
    groq: 'bg-purple-100 text-purple-800 dark:bg-purple-900/30 dark:text-purple-300',
    voyage: 'bg-cyan-100 text-cyan-800 dark:bg-cyan-900/30 dark:text-cyan-300',
  };

  return (
    <div className="space-y-6 max-w-6xl">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <Brain className="h-6 w-6 text-primary" />
            Model Control Center
          </h1>
          <p className="text-sm text-muted-foreground mt-0.5">
            {models.length} models across {providers.length} providers · Route policies · Health monitoring
          </p>
        </div>
        <button
          onClick={() => refetch()}
          className="flex items-center gap-2 px-3 py-2 text-sm border border-input rounded-lg hover:bg-muted/50"
        >
          <RefreshCw className="h-4 w-4" />
          Refresh
        </button>
      </div>

      {/* Provider Health Strip */}
      {healthData?.providers && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {healthData.providers.map((p: any) => (
            <div
              key={p.provider}
              className={`p-3 border rounded-xl flex items-center gap-3 ${
                p.is_healthy
                  ? 'border-green-200 bg-green-50/50 dark:border-green-800'
                  : 'border-red-200 bg-red-50/50 dark:border-red-800'
              }`}
            >
              {p.is_healthy
                ? <CheckCircle className="h-5 w-5 text-green-500 shrink-0" />
                : <XCircle className="h-5 w-5 text-red-500 shrink-0" />}
              <div className="min-w-0">
                <p className="text-xs font-medium capitalize">{p.provider}</p>
                <p className="text-[10px] text-muted-foreground">
                  {p.avg_latency_ms > 0 ? `${Math.round(p.avg_latency_ms)}ms` : 'Not tested'}
                </p>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Provider Filter */}
      <div className="flex flex-wrap gap-2">
        <button
          onClick={() => setSelectedProvider(null)}
          className={`px-3 py-1.5 text-xs rounded-lg border transition-colors ${
            !selectedProvider
              ? 'bg-primary text-primary-foreground border-primary'
              : 'border-input hover:bg-muted/50'
          }`}
        >
          All Providers
        </button>
        {providers.map((p) => (
          <button
            key={p}
            onClick={() => setSelectedProvider(p === selectedProvider ? null : p)}
            className={`px-3 py-1.5 text-xs rounded-lg border transition-colors capitalize ${
              selectedProvider === p
                ? 'bg-primary text-primary-foreground border-primary'
                : 'border-input hover:bg-muted/50'
            }`}
          >
            {p}
          </button>
        ))}
      </div>

      {/* Model Grid */}
      {isLoading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="h-48 bg-muted animate-pulse rounded-xl" />
          ))}
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {models.map((m: any) => {
            const modelKey = `${m.provider}/${m.model_id}`;
            return (
              <div
                key={modelKey}
                className="bg-card border border-border rounded-xl p-5 space-y-3 hover:border-primary/30 transition-colors"
              >
                {/* Header */}
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="text-sm font-semibold truncate">{m.display_name}</p>
                    <span
                      className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${
                        PROVIDER_COLORS[m.provider] ?? 'bg-muted text-muted-foreground'
                      }`}
                    >
                      {m.provider}
                    </span>
                  </div>
                  <div className="flex items-center gap-1">
                    {m.health?.is_healthy
                      ? <StatusOrb status="active" size={8} />
                      : <StatusOrb status="failed" size={8} />}
                  </div>
                </div>

                {/* Capabilities */}
                <div className="flex flex-wrap gap-1">
                  {m.capabilities.slice(0, 4).map((cap: string) => {
                    const Icon = CAPABILITY_ICONS[cap] ?? Activity;
                    return (
                      <span
                        key={cap}
                        className="flex items-center gap-0.5 text-[9px] bg-primary/10 text-primary px-1.5 py-0.5 rounded font-mono"
                      >
                        <Icon className="h-2.5 w-2.5" />
                        {cap.replace('_', ' ')}
                      </span>
                    );
                  })}
                </div>

                {/* Stats */}
                <div className="grid grid-cols-3 gap-2 text-center">
                  <div className="bg-muted/50 rounded-lg p-2">
                    <p className="text-[10px] text-muted-foreground">Quality</p>
                    <p className="text-sm font-bold text-primary">
                      {Math.round(m.quality_score * 100)}%
                    </p>
                  </div>
                  <div className="bg-muted/50 rounded-lg p-2">
                    <p className="text-[10px] text-muted-foreground">In/1K</p>
                    <p className="text-sm font-bold">${m.cost_per_1k_input.toFixed(4)}</p>
                  </div>
                  <div className="bg-muted/50 rounded-lg p-2">
                    <p className="text-[10px] text-muted-foreground">Context</p>
                    <p className="text-sm font-bold">{(m.context_window / 1000).toFixed(0)}K</p>
                  </div>
                </div>

                {/* Actions */}
                <button
                  onClick={() => {
                    setTestingModel(modelKey);
                    testMutation.mutate({ provider: m.provider, model_id: m.model_id });
                  }}
                  disabled={testingModel === modelKey || testMutation.isPending}
                  className="w-full py-2 text-xs border border-input rounded-lg hover:bg-muted/50 disabled:opacity-50 flex items-center justify-center gap-1.5"
                >
                  {testingModel === modelKey ? (
                    <><Loader2 className="h-3.5 w-3.5 animate-spin" /> Testing…</>
                  ) : (
                    <><Zap className="h-3.5 w-3.5" /> Test Connection</>
                  )}
                </button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
