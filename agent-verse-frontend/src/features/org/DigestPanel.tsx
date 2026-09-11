/**
 * DigestPanel — "While You Were Away" summary panel.
 *
 * Shows:
 *  - Summary stats (completed missions, cost, decisions)
 *  - Completed work
 *  - Items needing attention (approvals, blocked, budget)
 *  - Intelligence insights (bottlenecks, opportunities)
 */
import { useQuery } from '@tanstack/react-query';
import { motion } from 'framer-motion';
import {
  CheckCircle2, AlertTriangle, Clock, Zap, TrendingUp,
  DollarSign, Target, RefreshCw, X, ChevronRight,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { apiFetch } from '@/lib/api/client';

// ── Types ──────────────────────────────────────────────────────────────────

interface DigestItem {
  category: string;
  title: string;
  summary: string;
  icon: string;
  priority: number;
  action_required: boolean;
  action_type: string | null;
  related_id: string | null;
  cost_usd: number | null;
  duration_str: string | null;
}

interface Digest {
  org_id: string;
  tenant_id: string;
  since: string;
  generated_at: string;
  completed_missions: DigestItem[];
  completed_tasks: DigestItem[];
  pending_approvals: DigestItem[];
  blocked_items: DigestItem[];
  budget_alerts: DigestItem[];
  insights: DigestItem[];
  total_cost_usd: number;
  missions_completed: number;
  missions_started: number;
  agents_active: number;
  decisions_made: number;
  summary_text: string;
}

// ── API hook ───────────────────────────────────────────────────────────────

function useDigest(orgId: string) {
  return useQuery({
    queryKey: ['digest', orgId],
    queryFn: () =>
      apiFetch<Digest>(`/v1/org/${orgId}/brief/morning`)
        .catch(() => null),
    staleTime: 5 * 60_000,
    refetchOnWindowFocus: false,
  });
}

// ── Section row ────────────────────────────────────────────────────────────

function DigestRow({ item }: { item: DigestItem }) {
  const bgClass =
    item.category === 'needs_attention'
      ? 'border-l-2 border-orange-400 bg-orange-400/5'
      : item.category === 'insight'
      ? 'border-l-2 border-indigo-400 bg-indigo-400/5'
      : 'border-l-2 border-emerald-400 bg-emerald-400/5';

  return (
    <div className={`flex items-start gap-3 rounded-r-lg px-3 py-2.5 ${bgClass}`}>
      <span className="text-base flex-shrink-0 mt-0.5" aria-hidden="true">{item.icon}</span>
      <div className="flex-1 min-w-0">
        <div className="flex items-start justify-between gap-2">
          <p className="text-xs font-medium text-[var(--text-primary)] line-clamp-1">{item.title}</p>
          {item.cost_usd !== null && (
            <span className="text-[10px] text-emerald-400 flex-shrink-0">${item.cost_usd.toFixed(2)}</span>
          )}
        </div>
        <p className="text-[10px] text-[var(--text-muted)] line-clamp-2 mt-0.5">{item.summary}</p>
        <div className="flex items-center gap-2 mt-1">
          {item.duration_str && (
            <span className="text-[9px] text-[var(--text-muted)]">{item.duration_str}</span>
          )}
          {item.action_required && (
            <Badge variant="outline" className="text-[9px] px-1 py-0 border-orange-400/50 text-orange-400">
              Action needed
            </Badge>
          )}
        </div>
      </div>
      {item.action_required && (
        <ChevronRight className="h-3.5 w-3.5 text-[var(--text-muted)] flex-shrink-0 mt-0.5" aria-hidden="true" />
      )}
    </div>
  );
}

// ── Stat card ──────────────────────────────────────────────────────────────

function StatCard({
  value, label, icon: Icon, color,
}: {
  value: number | string;
  label: string;
  icon: React.ElementType;
  color: string;
}) {
  return (
    <div className="bg-[var(--bg-surface)] rounded-xl p-3 text-center">
      <Icon className={`h-4 w-4 mx-auto mb-1 ${color}`} aria-hidden="true" />
      <div className={`text-xl font-bold ${color}`}>{value}</div>
      <div className="text-[10px] text-[var(--text-muted)]">{label}</div>
    </div>
  );
}

// ── Main component ─────────────────────────────────────────────────────────

interface DigestPanelProps {
  orgId: string;
  onClose?: () => void;
}

export function DigestPanel({ orgId, onClose }: DigestPanelProps) {
  const { data: digest, isLoading, refetch } = useDigest(orgId);

  const allNeedsAttention = digest
    ? [...digest.pending_approvals, ...digest.blocked_items, ...digest.budget_alerts]
    : [];
  const allCompleted = digest
    ? [...digest.completed_missions, ...digest.completed_tasks.slice(0, 5)]
    : [];

  return (
    <motion.div
      className="bg-[var(--bg-card)] border border-[var(--border)] rounded-2xl overflow-hidden w-full max-w-md shadow-2xl"
      initial={false}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, y: 10, scale: 0.97 }}
      role="complementary"
      aria-label="While You Were Away digest"
    >
      {/* Header */}
      <div className="flex items-center gap-3 px-4 py-3 border-b border-[var(--border)]">
        <Clock className="h-4 w-4 text-[var(--accent-blue)]" aria-hidden="true" />
        <div>
          <h2 className="text-sm font-semibold text-[var(--text-primary)]">While You Were Away</h2>
          {digest && (
            <p className="text-[10px] text-[var(--text-muted)]">
              Since {new Date(digest.since).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
            </p>
          )}
        </div>
        <div className="ml-auto flex items-center gap-1">
          <Button
            variant="ghost" size="icon" className="h-7 w-7"
            onClick={() => refetch()}
            aria-label="Refresh digest"
          >
            <RefreshCw className="h-3.5 w-3.5" />
          </Button>
          {onClose && (
            <Button variant="ghost" size="icon" className="h-7 w-7" onClick={onClose} aria-label="Close">
              <X className="h-3.5 w-3.5" />
            </Button>
          )}
        </div>
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center h-48" aria-live="polite">
          <RefreshCw className="h-5 w-5 animate-spin text-[var(--accent-blue)]" aria-label="Loading digest" />
        </div>
      ) : !digest ? (
        <div className="flex items-center justify-center h-48">
          <p className="text-sm text-[var(--text-muted)]">No digest available</p>
        </div>
      ) : (
        <div className="overflow-y-auto max-h-[80vh]">
          {/* Summary text */}
          {digest.summary_text && (
            <div className="px-4 py-3 bg-[var(--bg-surface)] text-xs text-[var(--text-muted)]">
              {digest.summary_text}
            </div>
          )}

          {/* Stats */}
          <div className="grid grid-cols-4 gap-2 p-4">
            <StatCard
              value={digest.missions_completed}
              label="Completed"
              icon={CheckCircle2}
              color="text-emerald-400"
            />
            <StatCard
              value={digest.missions_started}
              label="Started"
              icon={Target}
              color="text-[var(--accent-blue)]"
            />
            <StatCard
              value={`$${digest.total_cost_usd.toFixed(2)}`}
              label="Spent"
              icon={DollarSign}
              color="text-yellow-400"
            />
            <StatCard
              value={digest.decisions_made}
              label="Decisions"
              icon={Zap}
              color="text-indigo-400"
            />
          </div>

          {/* Needs attention */}
          {allNeedsAttention.length > 0 && (
            <div className="px-4 pb-3">
              <div className="flex items-center gap-1.5 mb-2">
                <AlertTriangle className="h-3.5 w-3.5 text-orange-400" aria-hidden="true" />
                <span className="text-xs font-semibold text-orange-400">
                  Needs attention ({allNeedsAttention.length})
                </span>
              </div>
              <div className="space-y-1.5" role="list" aria-label="Items needing attention">
                {allNeedsAttention.map((item, i) => (
                  <div key={i} role="listitem">
                    <DigestRow item={item} />
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Completed */}
          {allCompleted.length > 0 && (
            <div className="px-4 pb-3">
              <div className="flex items-center gap-1.5 mb-2">
                <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400" aria-hidden="true" />
                <span className="text-xs font-semibold text-emerald-400">
                  Completed ({allCompleted.length})
                </span>
              </div>
              <div className="space-y-1.5" role="list" aria-label="Completed items">
                {allCompleted.map((item, i) => (
                  <div key={i} role="listitem">
                    <DigestRow item={item} />
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Insights */}
          {digest.insights.length > 0 && (
            <div className="px-4 pb-4">
              <div className="flex items-center gap-1.5 mb-2">
                <TrendingUp className="h-3.5 w-3.5 text-indigo-400" aria-hidden="true" />
                <span className="text-xs font-semibold text-indigo-400">
                  Insights ({digest.insights.length})
                </span>
              </div>
              <div className="space-y-1.5" role="list" aria-label="Insights">
                {digest.insights.map((item, i) => (
                  <div key={i} role="listitem">
                    <DigestRow item={item} />
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* All-clear */}
          {allNeedsAttention.length === 0 && allCompleted.length === 0 && digest.insights.length === 0 && (
            <div className="flex flex-col items-center justify-center h-32 px-4 pb-4">
              <CheckCircle2 className="h-8 w-8 text-emerald-400 mb-2" aria-hidden="true" />
              <p className="text-sm text-emerald-400 font-medium">All clear</p>
              <p className="text-xs text-[var(--text-muted)] mt-1">No significant activity while you were away</p>
            </div>
          )}
        </div>
      )}
    </motion.div>
  );
}
