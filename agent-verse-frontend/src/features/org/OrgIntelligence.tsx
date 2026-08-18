/**
 * OrgIntelligence — bottleneck detection + opportunity insights panel.
 *
 * JARVIS motion system:
 *   - JARVISPageShell:  blur-in page entry
 *   - JARVISStagger:    insight cards stagger in
 *   - SPRING_FAST:      card hover lift
 *   - AnimatePresence:  section transitions
 */
import { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Zap, AlertTriangle,
  Brain, ArrowRight, RefreshCw,
  Lightbulb, ShieldAlert,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import {
  JARVISPageShell, JARVISStagger, JARVISStaggerItem,
} from '@/components/ui/JARVISPageShell';
import { Badge } from '@/components/ui/badge';
import { useQuery } from '@tanstack/react-query';
import { apiFetch } from '@/lib/api/client';

// ── Types ──────────────────────────────────────────────────────────────────

interface Insight {
  id: string;
  type: 'bottleneck' | 'opportunity' | 'risk' | 'pattern';
  severity: 'info' | 'warning' | 'critical';
  title: string;
  detail: string;
  department?: string;
  metric?: string;
  recommendation?: string;
  impact_estimate?: string;
}

interface IntelligenceData {
  bottlenecks: Insight[];
  opportunities: Insight[];
  risks: Insight[];
  patterns: Insight[];
  generated_at: string;
}

// ── Config ─────────────────────────────────────────────────────────────────

const INSIGHT_CONFIG = {
  bottleneck: {
    icon: AlertTriangle,
    color: 'text-orange-400',
    bg: 'bg-orange-500/5 border-orange-500/20',
    label: 'Bottleneck',
  },
  opportunity: {
    icon: Lightbulb,
    color: 'text-emerald-400',
    bg: 'bg-emerald-500/5 border-emerald-500/20',
    label: 'Opportunity',
  },
  risk: {
    icon: ShieldAlert,
    color: 'text-red-400',
    bg: 'bg-red-500/5 border-red-500/20',
    label: 'Risk',
  },
  pattern: {
    icon: Brain,
    color: 'text-indigo-400',
    bg: 'bg-indigo-500/5 border-indigo-500/20',
    label: 'Pattern',
  },
};

const SEVERITY_DOT: Record<string, string> = {
  info:     'bg-[#475569]',
  warning:  'bg-yellow-400',
  critical: 'bg-red-400 animate-pulse',
};

// ── API hook ───────────────────────────────────────────────────────────────

function useOrgIntelligence(orgId: string) {
  return useQuery({
    queryKey: ['org-intelligence', orgId],
    queryFn: async () => {
      try {
        const [work, cap] = await Promise.all([
          apiFetch<any>(`/v1/org/${orgId}/intelligence/work`).catch(() => ({ items: [] })),
          apiFetch<any>(`/v1/org/${orgId}/intelligence/capabilities`).catch(() => ({ items: [] })),
        ]);

        // Normalize to Insight[]
        const workItems: Insight[] = (work?.items ?? []).map((i: any, idx: number) => ({
          id: `w-${idx}`,
          type: i.type ?? 'bottleneck',
          severity: i.severity ?? 'warning',
          title: i.title ?? i.name ?? 'Insight',
          detail: i.detail ?? i.description ?? '',
          department: i.department,
          recommendation: i.recommendation,
          impact_estimate: i.impact_estimate,
        }));
        const capItems: Insight[] = (cap?.items ?? []).map((i: any, idx: number) => ({
          id: `c-${idx}`,
          type: i.type ?? 'opportunity',
          severity: i.severity ?? 'info',
          title: i.title ?? i.name ?? 'Capability',
          detail: i.detail ?? i.description ?? '',
          recommendation: i.recommendation,
          impact_estimate: i.impact_estimate,
        }));

        const all = [...workItems, ...capItems];
        return {
          bottlenecks:  all.filter(i => i.type === 'bottleneck'),
          opportunities: all.filter(i => i.type === 'opportunity'),
          risks:        all.filter(i => i.type === 'risk'),
          patterns:     all.filter(i => i.type === 'pattern'),
          generated_at: new Date().toISOString(),
        } as IntelligenceData;
      } catch {
        return {
          bottlenecks: [], opportunities: [], risks: [], patterns: [],
          generated_at: new Date().toISOString(),
        } as IntelligenceData;
      }
    },
    staleTime: 120_000,
    refetchInterval: 120_000,
  });
}

// ── Insight card ───────────────────────────────────────────────────────────

function InsightCard({ insight }: { insight: Insight }) {
  const conf      = INSIGHT_CONFIG[insight.type] ?? INSIGHT_CONFIG.pattern;
  const Icon      = conf.icon;
  const severityDot = SEVERITY_DOT[insight.severity] ?? SEVERITY_DOT.info;

  return (
    <motion.div
      layout
      whileHover={{ y: -2, transition: { type: 'spring', stiffness: 600, damping: 35 } }}
      className={cn(
        'rounded-xl border p-4 transition-shadow hover:shadow-glow-electric',
        conf.bg,
      )}
      role="article"
      aria-label={`${conf.label}: ${insight.title}`}
    >
      <div className="flex items-start gap-3">
        <div className="p-2 rounded-lg bg-[#0A0D14]/60 flex-shrink-0">
          <Icon className={cn('h-4 w-4', conf.color)} aria-hidden />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-start justify-between gap-2 mb-1.5">
            <div className="flex items-center gap-2">
              <span className={cn('h-1.5 w-1.5 rounded-full flex-shrink-0 mt-0.5', severityDot)} aria-hidden />
              <span className="text-sm font-medium text-[#F1F5F9]">{insight.title}</span>
            </div>
            <Badge
              variant="outline"
              className={cn('text-[10px] flex-shrink-0 capitalize border-current/30', conf.color)}
            >
              {conf.label}
            </Badge>
          </div>

          <p className="text-xs text-[#94A3B8] leading-relaxed mb-2">{insight.detail}</p>

          {insight.department && (
            <p className="text-[11px] text-[#475569] mb-1">
              Dept: <span className="text-[#94A3B8]">{insight.department}</span>
            </p>
          )}

          {insight.recommendation && (
            <div className="mt-2 flex items-start gap-1.5">
              <ArrowRight className="h-3 w-3 text-[#475569] mt-0.5 flex-shrink-0" aria-hidden />
              <p className="text-[11px] text-[#475569]">{insight.recommendation}</p>
            </div>
          )}

          {insight.impact_estimate && (
            <p className="text-[11px] text-emerald-400 mt-1.5">
              Est. impact: {insight.impact_estimate}
            </p>
          )}
        </div>
      </div>
    </motion.div>
  );
}

// ── Main ───────────────────────────────────────────────────────────────────

interface OrgIntelligenceProps {
  orgId: string;
  className?: string;
}

export function OrgIntelligence({ orgId, className }: OrgIntelligenceProps) {
  const [filter, setFilter] = useState<'all' | 'bottleneck' | 'opportunity' | 'risk' | 'pattern'>('all');
  const { data, isLoading, refetch, isFetching } = useOrgIntelligence(orgId);

  const allInsights: Insight[] = data
    ? [...data.bottlenecks, ...data.opportunities, ...data.risks, ...data.patterns]
    : [];

  const filtered = filter === 'all' ? allInsights : allInsights.filter(i => i.type === filter);

  const FILTERS = [
    { id: 'all',         label: `All (${allInsights.length})` },
    { id: 'bottleneck',  label: `Bottlenecks (${data?.bottlenecks.length ?? 0})` },
    { id: 'opportunity', label: `Opportunities (${data?.opportunities.length ?? 0})` },
    { id: 'risk',        label: `Risks (${data?.risks.length ?? 0})` },
  ] as const;

  return (
    <JARVISPageShell className={cn('flex flex-col h-full', className)}>
      {/* Header */}
      <div className="flex items-center justify-between mb-4 shrink-0">
        <div>
          <h2 className="text-base font-semibold text-[#F1F5F9] flex items-center gap-2">
            <Brain className="h-4 w-4 text-[#00D4FF]" aria-hidden />
            Org Intelligence
          </h2>
          <p className="text-[11px] text-[#475569] mt-0.5">
            {data?.generated_at
              ? `Refreshed ${new Date(data.generated_at).toLocaleTimeString()}`
              : 'Analyzing organization…'}
          </p>
        </div>
        <button
          onClick={() => refetch()}
          disabled={isFetching}
          aria-label="Refresh insights"
          style={{ touchAction: 'manipulation' }}
          className="p-2 rounded-lg text-[#475569] hover:text-[#94A3B8] hover:bg-[#1A1F2E] transition-colors min-w-[44px] min-h-[44px] flex items-center justify-center focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00D4FF]/50"
        >
          <RefreshCw className={cn('h-4 w-4', isFetching && 'animate-spin')} aria-hidden />
        </button>
      </div>

      {/* Filters */}
      <div
        className="flex gap-1 flex-wrap mb-4 shrink-0"
        role="tablist"
        aria-label="Filter insights"
      >
        {FILTERS.map(f => (
          <button
            key={f.id}
            role="tab"
            aria-selected={filter === f.id}
            onClick={() => setFilter(f.id)}
            style={{ touchAction: 'manipulation' }}
            className={cn(
              'px-3 py-1 rounded-full text-xs font-medium transition-colors',
              'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00D4FF]/50',
              filter === f.id
                ? 'bg-[#00D4FF]/10 text-[#00D4FF]'
                : 'text-[#475569] hover:text-[#94A3B8] hover:bg-[#1A1F2E]',
            )}
          >
            {f.label}
          </button>
        ))}
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto">
        {isLoading ? (
          <div className="space-y-3">
            {[1, 2, 3].map(i => (
              <div key={i} className="h-24 rounded-xl bg-[#0F1623] animate-pulse" aria-hidden />
            ))}
          </div>
        ) : filtered.length === 0 ? (
          <AnimatePresence>
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className="flex flex-col items-center py-16 gap-3"
            >
              <Zap className="h-10 w-10 text-[#1E2535]" aria-hidden />
              <p className="text-[#475569] text-sm">
                {filter === 'all' ? 'No insights available yet.' : `No ${filter}s detected.`}
              </p>
              <p className="text-[11px] text-[#475569]">Insights appear after missions run.</p>
            </motion.div>
          </AnimatePresence>
        ) : (
          <JARVISStagger className="space-y-3" staggerMs={60}>
            {filtered.map(insight => (
              <JARVISStaggerItem key={insight.id}>
                <InsightCard insight={insight} />
              </JARVISStaggerItem>
            ))}
          </JARVISStagger>
        )}
      </div>
    </JARVISPageShell>
  );
}

export default OrgIntelligence;
