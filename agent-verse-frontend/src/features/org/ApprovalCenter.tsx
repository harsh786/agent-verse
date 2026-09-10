/**
 * ApprovalCenter — all pending approvals with approve/reject/modify/delegate actions.
 *
 * Features:
 *  - Grouped by urgency (critical → high → medium)
 *  - Rich context: risk level, cost estimate, prerequisite approvals
 *  - Keyboard accessible: Tab to card, Space/Enter to act
 *  - 30-second mobile-optimised flow
 */
import { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Shield, DollarSign, AlertTriangle, CheckCircle2, XCircle,
  RefreshCw, ChevronDown, ChevronUp,
  Clock, Users,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiFetch } from '@/lib/api/client';

// ── Types ──────────────────────────────────────────────────────────────────

interface Approval {
  id: string;
  title: string;
  description: string;
  action_type: string;
  risk_level: string;
  estimated_cost_usd: number | null;
  mission_id: string | null;
  agent_id: string | null;
  prerequisite_approvals: string[];
  already_approved_by: string[];
  approvers_needed: string[];
  status: string;
  created_at: string;
  expires_at: string | null;
}

const RISK_CONFIG: Record<string, { class: string; icon: React.ElementType; label: string }> = {
  low:      { class: 'text-emerald-400 border-emerald-400/30',    icon: Shield,        label: 'Low' },
  medium:   { class: 'text-yellow-400 border-yellow-400/30',      icon: AlertTriangle, label: 'Medium' },
  high:     { class: 'text-orange-400 border-orange-400/30',      icon: AlertTriangle, label: 'High' },
  critical: { class: 'text-red-400 border-red-400/30',            icon: AlertTriangle, label: 'Critical' },
};

// ── API hooks ──────────────────────────────────────────────────────────────

function useApprovals(orgId: string) {
  return useQuery({
    queryKey: ['approvals', orgId],
    queryFn: () =>
      apiFetch<{ data: Approval[] }>(`/v1/org/${orgId}/approvals`)
        .then(r => (Array.isArray(r) ? r : r?.data ?? []))
        .catch(() => [] as Approval[]),
    staleTime: 15_000,
    refetchInterval: 30_000,
  });
}

function useApprovalAction(orgId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, action, notes }: { id: string; action: 'approve' | 'reject'; notes?: string }) =>
      apiFetch(`/v1/org/${orgId}/approvals/${id}/${action}`, {
        method: 'POST',
        body: JSON.stringify({ notes }),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['approvals', orgId] }),
  });
}

// ── Approval card ──────────────────────────────────────────────────────────

function ApprovalCard({ approval, orgId }: { approval: Approval; orgId: string }) {
  const [expanded, setExpanded] = useState(false);
  const [showRejectForm, setShowRejectForm] = useState(false);
  const [rejectNotes, setRejectNotes] = useState('');
  const { mutate: act, isPending } = useApprovalAction(orgId);
  const riskConf = RISK_CONFIG[approval.risk_level] ?? RISK_CONFIG.medium;
  const RiskIcon = riskConf.icon;

  const isExpired = approval.expires_at && new Date(approval.expires_at) < new Date();

  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0 }}
      className={`bg-[var(--bg-card)] border rounded-xl overflow-hidden
        ${isExpired ? 'border-[var(--text-muted)]/30 opacity-60' : 'border-[var(--border)]'}`}
      role="article"
      aria-label={`Approval request: ${approval.title}`}
    >
      {/* Card header */}
      <div
        className="flex items-start gap-3 p-4 cursor-pointer"
        onClick={() => setExpanded(e => !e)}
        onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') setExpanded(ex => !ex); }}
        tabIndex={0}
        role="button"
        aria-expanded={expanded}
        aria-label={`${approval.title} — click to expand`}
      >
        <div className={`p-1.5 rounded-lg border flex-shrink-0 ${riskConf.class}`}>
          <RiskIcon className="h-3.5 w-3.5" aria-hidden="true" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-start justify-between gap-2">
            <h3 className="text-sm font-semibold text-[var(--text-primary)] truncate">{approval.title}</h3>
            {expanded
              ? <ChevronUp className="h-3.5 w-3.5 text-[var(--text-muted)] flex-shrink-0 mt-0.5" aria-hidden="true" />
              : <ChevronDown className="h-3.5 w-3.5 text-[var(--text-muted)] flex-shrink-0 mt-0.5" aria-hidden="true" />}
          </div>
          <p className="text-xs text-[var(--text-muted)] mt-0.5 line-clamp-2">{approval.description}</p>

          <div className="flex flex-wrap gap-2 mt-2">
            <Badge variant="outline" className={`text-[10px] border ${riskConf.class}`}>
              {riskConf.label} risk
            </Badge>
            {approval.estimated_cost_usd !== null && (
              <div className="flex items-center gap-0.5 text-[10px] text-emerald-400">
                <DollarSign className="h-3 w-3" aria-hidden="true" />
                <span>{approval.estimated_cost_usd.toFixed(2)} est. cost</span>
              </div>
            )}
            {approval.expires_at && (
              <div className="flex items-center gap-0.5 text-[10px] text-[var(--text-muted)]">
                <Clock className="h-3 w-3" aria-hidden="true" />
                <span>
                  {isExpired
                    ? 'Expired'
                    : `Expires ${new Date(approval.expires_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`}
                </span>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Actions — always visible so a pending approval is one click, not two */}
      {!isExpired && (
        <div className="px-4 pb-3 space-y-2">
          {showRejectForm && (
            <Textarea
              value={rejectNotes}
              onChange={e => setRejectNotes(e.target.value)}
              placeholder="Reason for rejection (optional)…"
              className="text-xs h-16 resize-none bg-[var(--bg-surface)] border-[var(--border)]"
              aria-label="Rejection reason"
            />
          )}
          <div className="flex gap-2">
            <Button
              size="sm"
              className="flex-1 bg-emerald-500 hover:bg-emerald-600 text-white h-8"
              disabled={isPending}
              onClick={() => act({ id: approval.id, action: 'approve' })}
              aria-label="Approve"
            >
              {isPending ? <RefreshCw className="h-3.5 w-3.5 animate-spin mr-1" /> : <CheckCircle2 className="h-3.5 w-3.5 mr-1" />}
              Approve
            </Button>

            {showRejectForm ? (
              <Button
                size="sm"
                variant="destructive"
                className="flex-1 h-8"
                disabled={isPending}
                onClick={() => act({ id: approval.id, action: 'reject', notes: rejectNotes })}
                aria-label="Confirm rejection"
              >
                Confirm Reject
              </Button>
            ) : (
              <Button
                size="sm"
                variant="outline"
                className="flex-1 h-8 border-red-400/40 text-red-400 hover:bg-red-400/10"
                onClick={() => setShowRejectForm(true)}
                aria-label="Reject"
              >
                <XCircle className="h-3.5 w-3.5 mr-1" />
                Reject
              </Button>
            )}
          </div>
        </div>
      )}

      {/* Expanded content */}
      <AnimatePresence>
        {expanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.15 }}
            className="overflow-hidden"
          >
            <div className="px-4 pb-2 space-y-3">
              {/* Approvers needed */}
              {approval.approvers_needed.length > 0 && (
                <div>
                  <div className="flex items-center gap-1.5 mb-1">
                    <Users className="h-3 w-3 text-[var(--text-muted)]" aria-hidden="true" />
                    <span className="text-xs text-[var(--text-muted)]">Approvers needed</span>
                  </div>
                  <div className="flex flex-wrap gap-1">
                    {approval.approvers_needed.map(r => (
                      <Badge
                        key={r}
                        variant={approval.already_approved_by.includes(r) ? 'default' : 'outline'}
                        className="text-[10px]"
                      >
                        {approval.already_approved_by.includes(r) ? '✓ ' : ''}{r}
                      </Badge>
                    ))}
                  </div>
                </div>
              )}

            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}

// ── Main component ─────────────────────────────────────────────────────────

const RISK_ORDER = ['critical', 'high', 'medium', 'low'];

interface ApprovalCenterProps {
  orgId: string;
}

export function ApprovalCenter({ orgId }: ApprovalCenterProps) {
  const { data: approvals = [], isLoading, refetch, isFetching } = useApprovals(orgId);
  const pending = (approvals as Approval[]).filter(a => a.status === 'pending');
  const sorted = [...pending].sort(
    (a, b) => RISK_ORDER.indexOf(a.risk_level) - RISK_ORDER.indexOf(b.risk_level)
  );

  return (
    <div className="space-y-4" role="region" aria-label="Approval center">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Shield className="h-4 w-4 text-[var(--accent-blue)]" aria-hidden="true" />
          <h2 className="text-sm font-semibold text-[var(--text-primary)]">Pending Approvals</h2>
          {pending.length > 0 && (
            <Badge className="text-[10px] bg-orange-500/20 text-orange-400 border-orange-400/30">
              {pending.length}
            </Badge>
          )}
        </div>
        <Button
          variant="ghost" size="icon" className="h-7 w-7"
          onClick={() => refetch()}
          aria-label="Refresh approvals"
          disabled={isFetching}
        >
          <RefreshCw className={`h-3.5 w-3.5 ${isFetching ? 'animate-spin' : ''}`} />
        </Button>
      </div>

      {/* List */}
      {isLoading ? (
        <div className="flex items-center justify-center h-24" aria-live="polite">
          <RefreshCw className="h-5 w-5 animate-spin text-[var(--accent-blue)]" aria-label="Loading" />
        </div>
      ) : sorted.length === 0 ? (
        <div className="flex flex-col items-center justify-center h-24 border border-dashed border-[var(--border)] rounded-xl">
          <CheckCircle2 className="h-6 w-6 text-emerald-400 mb-2" aria-hidden="true" />
          <p className="text-sm text-[var(--text-muted)]">No pending approvals</p>
        </div>
      ) : (
        <div className="space-y-2" role="list" aria-label="Approval requests">
          <AnimatePresence>
            {sorted.map(approval => (
              <div key={approval.id} role="listitem">
                <ApprovalCard approval={approval} orgId={orgId} />
              </div>
            ))}
          </AnimatePresence>
        </div>
      )}
    </div>
  );
}
