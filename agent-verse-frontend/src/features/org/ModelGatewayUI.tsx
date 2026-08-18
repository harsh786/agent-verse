/**
 * ModelGatewayUI — model routing dashboard.
 *
 * JARVIS motion system:
 *   - JARVISPageShell:  blur-in entry
 *   - JARVISStagger:    model card stagger
 *   - SPRING_FAST:      metric counters
 */
import { Cpu, Zap, DollarSign, AlertTriangle } from 'lucide-react';
import { cn } from '@/lib/utils';
import {
  JARVISPageShell, JARVISStagger, JARVISStaggerItem,
} from '@/components/ui/JARVISPageShell';
import { Badge } from '@/components/ui/badge';
import { useQuery } from '@tanstack/react-query';
import { apiFetch } from '@/lib/api/client';

// ── Types ──────────────────────────────────────────────────────────────────

interface ModelUsage {
  model_id: string;
  provider: string;
  profile: string;
  calls_24h: number;
  tokens_in_24h: number;
  tokens_out_24h: number;
  cost_usd_24h: number;
  avg_latency_ms: number;
  success_rate: number;
  fallback_count: number;
  is_primary: boolean;
  health: 'healthy' | 'degraded' | 'down';
}

// ── API hook ───────────────────────────────────────────────────────────────

function useModelUsage(orgId: string) {
  return useQuery({
    queryKey: ['model-usage', orgId],
    queryFn: () =>
      apiFetch<any>(`/v1/org/${orgId}/analytics/models`)
        .then(r => (Array.isArray(r) ? r : r?.data ?? r?.models ?? []))
        .catch(() => [] as ModelUsage[]),
    staleTime: 60_000,
    refetchInterval: 60_000,
  });
}

// ── Profile badge ──────────────────────────────────────────────────────────

const PROFILE_COLORS: Record<string, string> = {
  premium:    'text-[#00D4FF] border-[#00D4FF]/30',
  smart:      'text-indigo-400 border-indigo-400/30',
  coding:     'text-emerald-400 border-emerald-400/30',
  analytical: 'text-blue-400 border-blue-400/30',
  creative:   'text-pink-400 border-pink-400/30',
  fast:       'text-yellow-400 border-yellow-400/30',
  research:   'text-purple-400 border-purple-400/30',
  expert:     'text-orange-400 border-orange-400/30',
  worker:     'text-[#475569] border-[#475569]/30',
};

// ── Model card ─────────────────────────────────────────────────────────────

function ModelCard({ model }: { model: ModelUsage }) {
  const profileClass = PROFILE_COLORS[model.profile] ?? PROFILE_COLORS.smart;

  const healthDot = model.health === 'healthy' ? 'bg-emerald-400'
    : model.health === 'degraded' ? 'bg-yellow-400 animate-pulse' : 'bg-red-400 animate-pulse';

  return (
    <div
      className="bg-[#0F1623] border border-[#1E2535] rounded-xl p-4 hover:border-[#00D4FF]/20 hover:shadow-glow-electric transition-all"
      role="article"
      aria-label={`Model: ${model.model_id}`}
    >
      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2 min-w-0">
          <span className={cn('h-2 w-2 rounded-full flex-shrink-0', healthDot)} aria-label={model.health} />
          <span className="text-sm font-semibold text-[#F1F5F9] truncate font-mono">{model.model_id}</span>
          {model.is_primary && (
            <Badge variant="outline" className="text-[10px] border-emerald-500/30 text-emerald-400">Primary</Badge>
          )}
        </div>
        <Badge variant="outline" className={cn('text-[10px] capitalize flex-shrink-0', profileClass)}>
          {model.profile}
        </Badge>
      </div>

      {/* Metrics grid */}
      <div className="grid grid-cols-2 gap-2">
        <div className="bg-[#1A1F2E] rounded-lg px-2.5 py-2">
          <p className="text-[10px] text-[#475569]">Calls (24h)</p>
          <p className="text-base font-bold text-[#F1F5F9]">{model.calls_24h.toLocaleString()}</p>
        </div>
        <div className="bg-[#1A1F2E] rounded-lg px-2.5 py-2">
          <p className="text-[10px] text-[#475569]">Cost (24h)</p>
          <p className="text-base font-bold text-emerald-400">${model.cost_usd_24h.toFixed(2)}</p>
        </div>
        <div className="bg-[#1A1F2E] rounded-lg px-2.5 py-2">
          <p className="text-[10px] text-[#475569]">Avg latency</p>
          <p className="text-base font-bold text-[#F1F5F9]">{model.avg_latency_ms}ms</p>
        </div>
        <div className="bg-[#1A1F2E] rounded-lg px-2.5 py-2">
          <p className="text-[10px] text-[#475569]">Success rate</p>
          <p className={cn('text-base font-bold', model.success_rate >= 0.95 ? 'text-emerald-400' : 'text-yellow-400')}>
            {(model.success_rate * 100).toFixed(1)}%
          </p>
        </div>
      </div>

      {/* Fallbacks */}
      {model.fallback_count > 0 && (
        <div className="mt-2 flex items-center gap-1.5 text-[11px] text-yellow-400">
          <AlertTriangle className="h-3 w-3" aria-hidden />
          {model.fallback_count} fallback{model.fallback_count !== 1 ? 's' : ''} triggered (24h)
        </div>
      )}
    </div>
  );
}

// ── Main ───────────────────────────────────────────────────────────────────

interface ModelGatewayUIProps {
  orgId: string;
  className?: string;
}

export function ModelGatewayUI({ orgId, className }: ModelGatewayUIProps) {
  const { data: models, isLoading } = useModelUsage(orgId);
  const modelList: ModelUsage[] = models ?? [];

  const totalCost    = modelList.reduce((s, m) => s + m.cost_usd_24h, 0);
  const degraded     = modelList.filter(m => m.health !== 'healthy').length;

  return (
    <JARVISPageShell className={cn('flex flex-col gap-4', className)}>
      {/* Header */}
      <div>
        <h2 className="text-base font-semibold text-[#F1F5F9] flex items-center gap-2">
          <Cpu className="h-4 w-4 text-[#00D4FF]" aria-hidden />
          Model Gateway
        </h2>
        <p className="text-[11px] text-[#475569] mt-0.5">
          Per-model usage, cost, and health (24h window)
        </p>
      </div>

      {/* Summary stats */}
      <div className="grid grid-cols-3 gap-2 shrink-0">
        {[
          { icon: Zap,        label: 'Models',    value: modelList.length,          color: 'text-[#00D4FF]' },
          { icon: DollarSign, label: 'Cost (24h)', value: `$${totalCost.toFixed(2)}`, color: 'text-emerald-400' },
          { icon: AlertTriangle, label: 'Degraded', value: degraded,                 color: degraded > 0 ? 'text-yellow-400' : 'text-[#475569]' },
        ].map(({ icon: Icon, label, value, color }) => (
          <div key={label} className="bg-[#0F1623] border border-[#1E2535] rounded-xl p-3">
            <div className="flex items-center gap-1.5 mb-1">
              <Icon className={cn('h-3.5 w-3.5', color)} aria-hidden />
              <span className="text-[10px] text-[#475569]">{label}</span>
            </div>
            <p className={cn('text-base font-bold', color)}>{value}</p>
          </div>
        ))}
      </div>

      {/* Model cards */}
      <div className="flex-1 overflow-y-auto">
        {isLoading ? (
          <div className="space-y-3">
            {[1, 2, 3].map(i => (
              <div key={i} className="h-36 rounded-xl bg-[#0F1623] animate-pulse" aria-hidden />
            ))}
          </div>
        ) : modelList.length === 0 ? (
          <div className="flex flex-col items-center py-12 gap-3">
            <Cpu className="h-10 w-10 text-[#1E2535]" aria-hidden />
            <p className="text-[#475569] text-sm">No model usage data yet.</p>
            <p className="text-[11px] text-[#475569]">Data appears after missions run.</p>
          </div>
        ) : (
          <JARVISStagger className="space-y-3">
            {modelList.map(m => (
              <JARVISStaggerItem key={m.model_id}>
                <ModelCard model={m} />
              </JARVISStaggerItem>
            ))}
          </JARVISStagger>
        )}
      </div>
    </JARVISPageShell>
  );
}

export default ModelGatewayUI;
