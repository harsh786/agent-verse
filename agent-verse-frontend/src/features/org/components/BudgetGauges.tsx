/**
 * BudgetGauges — radial burn-rate gauges for the org-brain's spending caps.
 *
 * Daily and per-mission-ceiling gauges are derived from recorded brain
 * decisions' `est_cost_usd` — the model's pre-execution ESTIMATE, not metered
 * actual spend, so every fill is captioned as such. Collaboration has no live
 * spend feed on the frontend at all: rather than fabricate a number, that
 * gauge shows only the configured cap with an honest "no live spend feed"
 * caption and an empty ring.
 */
import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Gauge, AlertTriangle, Loader2 } from 'lucide-react';
import { cn } from '@/lib/utils';
import { orgAutonomyApi } from '@/features/org/api';

interface BudgetGaugesProps {
  orgId:      string;
  className?: string;
}

const DECISIONS_LIMIT = 100;

const AUTONOMY_KEY  = (orgId: string) => ['org-autonomy', orgId] as const;
// Distinct from BrainFeed's `['org-brain-decisions', orgId]` key (which fetches
// 50 rows for the feed): this gauge needs a wider window to aggregate spend, so
// the limit is part of the key — otherwise both components would race to share
// one TanStack Query cache entry keyed only by orgId, with whichever queryFn
// last ran silently overwriting the other's data (see task-11 review finding).
const DECISIONS_KEY = (orgId: string) => ['org-brain-decisions', orgId, DECISIONS_LIMIT] as const;

const CYAN  = '#00D4FF';
const AMBER = '#F59E0B';
const RED   = '#EF4444';
const MUTED = '#475569';

/** Ring/text color for a fill percentage (0-100+): amber >= 80%, red >= 100%. */
function gaugeColor(pct: number): string {
  if (pct >= 100) return RED;
  if (pct >= 80) return AMBER;
  return CYAN;
}

function isToday(iso: string): boolean {
  const d = new Date(iso);
  const now = new Date();
  return (
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate()
  );
}

function formatUsd(v: number): string {
  return `$${v.toFixed(2)}`;
}

interface GaugeArcProps {
  label:    string;
  /** null = no live spend figure available for this gauge (collaboration). */
  spend:    number | null;
  cap:      number;
  caption?: string;
}

function GaugeArc({ label, spend, cap, caption }: GaugeArcProps) {
  const size   = 72;
  const stroke = 6;
  const r      = (size - stroke) / 2;
  const circ   = 2 * Math.PI * r;
  const noCap  = !cap || cap <= 0;
  const hasFill = !noCap && spend != null;
  const pct     = hasFill ? (spend! / cap) * 100 : 0;
  const clampedPct = Math.min(pct, 100);
  const color   = hasFill ? gaugeColor(pct) : MUTED;
  const arcLen  = circ * (clampedPct / 100);

  const summary = noCap
    ? 'no cap set'
    : !hasFill
      ? `${formatUsd(cap)} cap · no live spend feed`
      : `${Math.round(pct)}% of ${formatUsd(cap)}`;

  return (
    <div
      className="flex flex-col items-center gap-1.5"
      role="group"
      aria-label={`${label} budget: ${summary}`}
    >
      <div className="relative" style={{ width: size, height: size }}>
        <svg width={size} height={size} className="absolute inset-0" aria-hidden>
          <circle
            cx={size / 2} cy={size / 2} r={r}
            fill="none" stroke="rgba(255,255,255,0.06)" strokeWidth={stroke}
          />
          {hasFill && (
            <circle
              cx={size / 2} cy={size / 2} r={r}
              fill="none" stroke={color} strokeWidth={stroke}
              strokeDasharray={`${arcLen} ${circ}`}
              strokeLinecap="round"
              transform={`rotate(-90 ${size / 2} ${size / 2})`}
              style={{ filter: `drop-shadow(0 0 4px ${color})` }}
            />
          )}
        </svg>
        <div className="absolute inset-0 flex items-center justify-center">
          <span
            className="text-[11px] font-mono font-semibold tabular-nums"
            style={{ color: noCap ? MUTED : hasFill ? color : '#94A3B8' }}
          >
            {noCap ? '—' : hasFill ? `${Math.round(pct)}%` : formatUsd(cap)}
          </span>
        </div>
      </div>
      <p className="text-[10px] font-medium uppercase tracking-wide text-[#64748B] text-center">
        {label}
      </p>
      <p className="text-[9px] leading-tight text-[#475569] text-center">
        {caption ?? summary}
      </p>
    </div>
  );
}

export function BudgetGauges({ orgId, className }: BudgetGaugesProps) {
  const {
    data: autonomy, isLoading: autonomyLoading, isError: autonomyError, error: autonomyErr,
  } = useQuery({
    queryKey: AUTONOMY_KEY(orgId),
    queryFn:  () => orgAutonomyApi.get(orgId),
  });
  const {
    data: decisions, isLoading: decisionsLoading, isError: decisionsError, error: decisionsErr,
  } = useQuery({
    queryKey: DECISIONS_KEY(orgId),
    queryFn:  () => orgAutonomyApi.decisions(orgId, DECISIONS_LIMIT),
  });

  const isLoading = autonomyLoading || decisionsLoading;
  const isError   = autonomyError || decisionsError;
  const firstError = autonomyErr ?? decisionsErr;

  // Client-side aggregate over recorded decisions — an ESTIMATE, not metered
  // spend (see file header). Today's spend sums every decision's est_cost_usd
  // for the local calendar day; the per-mission figure is the single largest
  // estimated cost seen across the fetched window.
  const { dailySpend, maxSingle } = useMemo(() => {
    const rows = decisions ?? [];
    let daily = 0;
    let max = 0;
    for (const d of rows) {
      if (d.est_cost_usd == null) continue;
      if (isToday(d.created_at)) daily += d.est_cost_usd;
      if (d.est_cost_usd > max) max = d.est_cost_usd;
    }
    return { dailySpend: daily, maxSingle: max };
  }, [decisions]);

  const settings = autonomy?.settings;

  return (
    <section className={cn('flex flex-col gap-3', className)} aria-label="Budget burn">
      <div className="flex items-center gap-2">
        <Gauge className="h-3.5 w-3.5 text-[#00D4FF]" aria-hidden />
        <h3 className="text-[11px] font-medium uppercase tracking-[0.08em] text-[#475569]">
          Budget Burn
        </h3>
      </div>

      {isLoading ? (
        <div className="flex items-center gap-2 py-3 text-[12px] text-[#475569]">
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
          Loading budgets…
        </div>
      ) : isError || !settings ? (
        <div className="flex items-center gap-2 py-3 text-[12px] text-red-400">
          <AlertTriangle className="h-3.5 w-3.5 flex-shrink-0" aria-hidden />
          Failed to load budgets{firstError instanceof Error ? `: ${firstError.message}` : '.'}
        </div>
      ) : (
        <>
          <div className="flex items-start justify-around gap-2">
            <GaugeArc label="Daily" spend={dailySpend} cap={settings.daily_budget_usd} />
            <GaugeArc
              label="Per-Mission"
              spend={maxSingle}
              cap={settings.per_mission_cost_ceiling_usd}
            />
            <GaugeArc
              label="Collaboration"
              spend={null}
              cap={settings.collaboration_daily_budget_usd}
            />
          </div>
          <p className="text-[9px] leading-snug text-[#334155]">
            Daily &amp; per-mission figures are estimated from recorded decisions, not metered
            actual spend.
          </p>
        </>
      )}
    </section>
  );
}

export default BudgetGauges;
