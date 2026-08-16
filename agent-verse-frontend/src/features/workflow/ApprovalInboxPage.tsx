/**
 * ApprovalInboxPage — HITL approval inbox for reviewers.
 *
 * Features:
 * - Priority-sorted list of pending approvals
 * - One-click approve/reject with optional note
 * - Bulk actions
 * - SLA countdown badge
 * - SSE live updates
 * - Mobile-friendly cards
 * - WCAG 2.2 AA
 */
import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { motion, AnimatePresence } from 'framer-motion';
import {
  CheckCircle, XCircle, Clock, User,
  Loader2, RefreshCw, ChevronDown,
} from 'lucide-react';
import { workflowEngineApi } from '../../lib/api/client';
import { PRIORITY_COLORS } from './design/tokens';
import { nodeBounce, emptyStateFade, slaPulse, swipeTint, springs } from './design/motion';

// ── Priority badge ────────────────────────────────────────────────────────────

function PriorityBadge({ priority }: { priority: string }) {
  const cls = PRIORITY_COLORS[priority as keyof typeof PRIORITY_COLORS] ?? PRIORITY_COLORS.medium;
  const labels: Record<string, string> = {
    critical: '🔴 Critical', high: '🟠 High', medium: '🟡 Medium', low: '⚪ Low',
  };
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium border ${cls.bg} ${cls.text} ${cls.border}`}>
      {labels[priority] ?? priority}
    </span>
  );
}

// ── SLA countdown ─────────────────────────────────────────────────────────────

function SLACountdown({ deadline_at }: { deadline_at: string | null }) {
  if (!deadline_at) return null;

  const deadline = new Date(deadline_at);
  const now = new Date();
  const diffMs = deadline.getTime() - now.getTime();
  const diffH = Math.floor(diffMs / 3_600_000);
  const diffM = Math.floor((diffMs % 3_600_000) / 60_000);
  const isUrgent = diffMs < 4 * 3_600_000 && diffMs > 0;
  const isOverdue = diffMs <= 0;

  return (
    <motion.span
      animate={isUrgent ? 'animate' : 'initial'}
      variants={isUrgent ? slaPulse : undefined}
      className={`text-xs font-medium flex items-center gap-1 ${
        isOverdue ? 'text-red-400' : isUrgent ? 'text-amber-400' : 'text-white/40'
      }`}
      aria-label={`SLA deadline: ${isOverdue ? 'overdue' : `${diffH}h ${diffM}m remaining`}`}
    >
      <Clock className="h-3 w-3" aria-hidden />
      {isOverdue ? 'Overdue' : `${diffH}h ${diffM}m`}
    </motion.span>
  );
}

// ── Approval card ─────────────────────────────────────────────────────────────

interface ApprovalRequest {
  request_id: string;
  run_id: string;
  step_id: string;
  workflow_id: string;
  priority: string;
  status: string;
  context: Array<{ display_type: string; title: string; data: unknown }>;
  actions: Array<{ id: string; label: string }>;
  deadline_at: string | null;
  created_at: string;
  note?: string;
}

function ApprovalCard({
  req,
  selected,
  onSelect,
  onDecide,
  isDeciding,
}: {
  req: ApprovalRequest;
  selected: boolean;
  onSelect: (id: string) => void;
  onDecide: (requestId: string, action: string, note?: string) => void;
  isDeciding: boolean;
}) {
  const [note, setNote] = useState('');
  const [showNote, setShowNote] = useState(false);

  return (
    <motion.article
      layout
      variants={nodeBounce}
      initial="initial"
      animate="animate"
      exit="exit"
      className={`
        rounded-2xl border transition-colors
        ${selected ? 'border-sky-500/40 bg-sky-950/30' : 'border-white/8 bg-white/3'}
      `}
      role="article"
      aria-label={`Approval request for run ${req.run_id}`}
    >
      <div className="px-4 pt-4 pb-3">
        {/* Header */}
        <div className="flex items-start justify-between gap-3 mb-3">
          <div className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={selected}
              onChange={() => onSelect(req.request_id)}
              aria-label={`Select request ${req.request_id}`}
              className="rounded border-white/20 bg-white/10 text-sky-500
                         focus:ring-sky-500 focus:ring-offset-slate-900"
            />
            <PriorityBadge priority={req.priority} />
          </div>
          <SLACountdown deadline_at={req.deadline_at} />
        </div>

        {/* Meta */}
        <div className="flex items-center gap-3 text-xs text-white/40 mb-3">
          <span className="flex items-center gap-1">
            <User className="h-3 w-3" aria-hidden />
            {req.step_id}
          </span>
          <span className="truncate max-w-[150px] font-mono">{req.run_id.slice(0, 12)}…</span>
          <span className="ml-auto">{new Date(req.created_at).toLocaleTimeString()}</span>
        </div>

        {/* Context items */}
        {req.context?.length > 0 && (
          <div className="space-y-2 mb-3">
            {req.context.slice(0, 2).map((item, i) => (
              <div key={i} className="rounded-lg bg-white/4 border border-white/8 px-3 py-2">
                <p className="text-xs text-white/50 mb-1">{item.title}</p>
                <p className="text-xs text-white/80 font-mono truncate">
                  {typeof item.data === 'string' ? item.data : JSON.stringify(item.data)}
                </p>
              </div>
            ))}
          </div>
        )}

        {/* Note field toggle */}
        {showNote && (
          <textarea
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Add a note (optional)…"
            rows={2}
            className="w-full px-3 py-2 rounded-xl bg-white/5 border border-white/10 text-white/80
                       placeholder-white/30 text-xs focus:outline-none focus:ring-2 focus:ring-sky-500
                       resize-none mb-3"
            aria-label="Decision note"
          />
        )}

        {/* Actions */}
        <div className="flex items-center gap-2 flex-wrap">
          {/* Custom actions from DSL */}
          {req.actions?.length > 0 ? (
            req.actions.map((action) => (
              <button
                key={action.id}
                onClick={() => onDecide(req.request_id, action.id, note || undefined)}
                disabled={isDeciding}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-medium
                  transition-colors disabled:opacity-50 ${
                    action.id === 'approve' || action.id === 'approved'
                      ? 'bg-emerald-600/80 hover:bg-emerald-600 text-white'
                      : action.id === 'reject' || action.id === 'rejected'
                      ? 'bg-red-600/60 hover:bg-red-600/80 text-white'
                      : 'bg-sky-600/60 hover:bg-sky-600/80 text-white'
                  }`}
                aria-label={`${action.label || action.id} request`}
              >
                {isDeciding && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                {action.label || action.id}
              </button>
            ))
          ) : (
            <>
              <motion.button
                whileHover={swipeTint.approve}
                whileTap={{ scale: 0.96 }}
                transition={springs.snappy}
                onClick={() => onDecide(req.request_id, 'approved', note || undefined)}
                disabled={isDeciding}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-emerald-600/80
                           hover:bg-emerald-600 text-white text-xs font-medium transition-colors
                           disabled:opacity-50"
                aria-label="Approve request"
              >
                {isDeciding ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <CheckCircle className="h-3.5 w-3.5" />}
                Approve
              </motion.button>
              <motion.button
                whileHover={swipeTint.reject}
                whileTap={{ scale: 0.96 }}
                transition={springs.snappy}
                onClick={() => onDecide(req.request_id, 'rejected', note || undefined)}
                disabled={isDeciding}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-red-600/60
                           hover:bg-red-600/80 text-white text-xs font-medium transition-colors
                           disabled:opacity-50"
                aria-label="Reject request"
              >
                {isDeciding ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <XCircle className="h-3.5 w-3.5" />}
                Reject
              </motion.button>
            </>
          )}

          <button
            onClick={() => setShowNote((v) => !v)}
            className="flex items-center gap-1 px-2 py-1.5 rounded-xl bg-white/5
                       hover:bg-white/10 text-white/40 text-xs transition-colors ml-auto"
            aria-label={showNote ? 'Hide note field' : 'Add note'}
            aria-expanded={showNote}
          >
            <ChevronDown className={`h-3.5 w-3.5 transition-transform ${showNote ? 'rotate-180' : ''}`} />
            Note
          </button>
        </div>
      </div>
    </motion.article>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function ApprovalInboxPage() {
  const qc = useQueryClient();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [priorityFilter, setPriorityFilter] = useState('');
  const [decidingId, setDecidingId] = useState<string | null>(null);

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['workflow-engine', 'approvals', priorityFilter],
    queryFn: () => workflowEngineApi.listApprovals({
      priority: priorityFilter || undefined,
      per_page: 50,
    }),
    refetchInterval: 10_000,
  });

  const { data: stats } = useQuery({
    queryKey: ['workflow-engine', 'approval-stats'],
    queryFn: () => workflowEngineApi.approvalStats(),
    refetchInterval: 15_000,
  });

  const decideMutation = useMutation({
    mutationFn: ({ requestId, action, note }: { requestId: string; action: string; note?: string }) =>
      workflowEngineApi.decideApproval(requestId, { action, note }),
    onMutate: ({ requestId }) => setDecidingId(requestId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['workflow-engine', 'approvals'] });
      qc.invalidateQueries({ queryKey: ['workflow-engine', 'approval-stats'] });
    },
    onSettled: () => setDecidingId(null),
  });

  const toggleSelect = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const handleBulkDecide = (action: string) => {
    for (const id of selected) {
      decideMutation.mutate({ requestId: id, action });
    }
    setSelected(new Set());
  };

  const items = (data?.items ?? []) as ApprovalRequest[];

  return (
    <div className="min-h-screen bg-slate-950 text-white">
      {/* Header */}
      <header className="sticky top-0 z-30 border-b border-white/10 bg-slate-950/90 backdrop-blur-xl
                          px-6 py-4">
        <div className="max-w-3xl mx-auto flex items-center justify-between gap-4">
          <div>
            <h1 className="text-lg font-bold flex items-center gap-2">
              Approval Inbox
              {(stats?.pending_count ?? 0) > 0 && (
                <span className="px-2 py-0.5 rounded-full bg-rose-500/20 text-rose-400
                                  text-xs font-medium border border-rose-500/30"
                  aria-label={`${stats?.pending_count} pending`}>
                  {stats?.pending_count}
                </span>
              )}
            </h1>
            <p className="text-xs text-white/40 mt-0.5">
              Avg resolution: {stats?.avg_resolution_seconds
                ? `${Math.round(stats.avg_resolution_seconds / 60)}min`
                : '—'}
            </p>
          </div>
          <button onClick={() => refetch()} className="text-white/30 hover:text-white p-2
                                                         rounded-xl hover:bg-white/5 transition-colors"
            aria-label="Refresh inbox">
            <RefreshCw className="h-4 w-4" />
          </button>
        </div>
      </header>

      <main className="max-w-3xl mx-auto px-6 py-8">
        {/* Priority filter */}
        <div className="flex items-center gap-2 mb-6 flex-wrap">
          {['', 'critical', 'high', 'medium', 'low'].map((p) => (
            <button
              key={p}
              onClick={() => setPriorityFilter(p)}
              aria-pressed={priorityFilter === p}
              className={`px-3 py-1.5 rounded-xl text-xs font-medium transition-colors ${
                priorityFilter === p
                  ? 'bg-sky-600 text-white'
                  : 'bg-white/5 text-white/50 hover:text-white hover:bg-white/10'
              }`}
            >
              {p || 'All'}
            </button>
          ))}

          {/* Bulk actions */}
          {selected.size > 0 && (
            <div className="ml-auto flex items-center gap-2">
              <span className="text-xs text-white/40">{selected.size} selected</span>
              <button
                onClick={() => handleBulkDecide('approved')}
                className="px-3 py-1.5 rounded-xl bg-emerald-600/80 hover:bg-emerald-600 text-white
                           text-xs font-medium transition-colors"
                aria-label="Bulk approve selected"
              >
                Approve all
              </button>
              <button
                onClick={() => handleBulkDecide('rejected')}
                className="px-3 py-1.5 rounded-xl bg-red-600/60 hover:bg-red-600/80 text-white
                           text-xs font-medium transition-colors"
                aria-label="Bulk reject selected"
              >
                Reject all
              </button>
            </div>
          )}
        </div>

        {/* List */}
        {isLoading ? (
          <div className="flex items-center justify-center py-16">
            <Loader2 className="h-6 w-6 text-sky-400 animate-spin" />
          </div>
        ) : items.length === 0 ? (
          <motion.div
            variants={emptyStateFade}
            initial="initial"
            animate="animate"
            className="text-center py-20"
            role="status"
          >
            <CheckCircle className="h-14 w-14 mx-auto mb-4 text-emerald-400/30" aria-hidden />
            <p className="text-white/50 text-sm">All caught up! No pending approvals.</p>
          </motion.div>
        ) : (
          <AnimatePresence mode="popLayout">
            <div className="space-y-3" role="list" aria-label="Pending approvals">
              {items.map((req) => (
                <ApprovalCard
                  key={req.request_id}
                  req={req}
                  selected={selected.has(req.request_id)}
                  onSelect={toggleSelect}
                  onDecide={(id, action, note) => decideMutation.mutate({ requestId: id, action, note })}
                  isDeciding={decidingId === req.request_id}
                />
              ))}
            </div>
          </AnimatePresence>
        )}
      </main>
    </div>
  );
}
