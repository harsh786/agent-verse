import type { IngestionQuota } from '../types';
import { motion } from 'framer-motion';
import { SPRING_SLOW } from '@/components/ui/JARVISPageShell';

interface Props { quota: IngestionQuota; }

export function QuotaUsageBar({ quota }: Props) {
  const sourcePct = quota.sources_limit ? (quota.sources_used / quota.sources_limit) * 100 : 0;
  const tokenPct  = quota.tokens_limit_month ? (quota.tokens_used_month / quota.tokens_limit_month) * 100 : 0;
  const isWarning = sourcePct > 80 || tokenPct > 80;
  const isCritical = sourcePct > 95 || tokenPct > 95;

  return (
    <div className="rounded-xl border border-border bg-card p-4 space-y-3">
      <div className="flex items-center justify-between">
        <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Quota Usage</span>
        <span className="text-xs text-muted-foreground capitalize">{quota.plan} plan</span>
      </div>
      <QuotaRow
        label="Sources"
        used={quota.sources_used}
        limit={quota.sources_limit}
        pct={sourcePct}
        isCritical={isCritical}
      />
      <QuotaRow
        label="Tokens this month"
        used={quota.tokens_used_month}
        limit={quota.tokens_limit_month}
        pct={tokenPct}
        isCritical={isCritical}
        format={n => n >= 1_000_000 ? `${(n/1_000_000).toFixed(1)}M` : `${(n/1_000).toFixed(0)}K`}
      />
      {quota.cost_usd_month > 0 && (
        <div className="text-xs text-muted-foreground text-right">
          Embedding cost: ${quota.cost_usd_month.toFixed(2)} / month
        </div>
      )}
      {isWarning && (
        <p className={`text-xs ${isCritical ? 'text-destructive' : 'text-amber-600 dark:text-amber-400'}`}>
          {isCritical ? '⚠ Quota nearly exceeded. Upgrade plan or reduce source count.' : '📊 Approaching quota limit.'}
        </p>
      )}
    </div>
  );
}

function QuotaRow({ label, used, limit, pct, isCritical, format = String }: {
  label: string; used: number; limit: number | null; pct: number;
  isCritical: boolean; format?: (n: number) => string;
}) {
  const color = isCritical ? 'bg-destructive' : pct > 80 ? 'bg-amber-500' : 'bg-primary';
  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <span className="text-xs text-muted-foreground">{label}</span>
        <span className="text-xs text-muted-foreground">
          {format(used)}{limit !== null ? ` / ${format(limit)}` : ' (unlimited)'}
        </span>
      </div>
      <div className="h-1.5 rounded-full bg-muted overflow-hidden" role="progressbar" aria-valuenow={Math.round(pct)} aria-valuemin={0} aria-valuemax={100}>
        <motion.div
          initial={{ width: 0 }}
          animate={{ width: `${Math.min(pct, 100)}%` }}
          transition={{ ...SPRING_SLOW, delay: 0.1 }}
          className={`h-full rounded-full ${color}`}
        />
      </div>
    </div>
  );
}
