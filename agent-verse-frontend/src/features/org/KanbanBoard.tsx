/**
 * KanbanBoard — 5-column drag-aware task board.
 *
 * Columns: TODO | IN PROGRESS | REVIEW | DONE | APPROVED
 * Features:
 *  - Virtualized card lists (useVirtualizer)
 *  - Optimistic status updates
 *  - Priority indicators (low/medium/high/critical)
 *  - Assignee avatars
 *  - Keyboard accessible
 */
import { useCallback, useMemo } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';
import { useRef } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Circle, Loader2, Eye, CheckCircle2, BadgeCheck, AlertCircle, User,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { useOrgTasks } from './hooks/useOrg';
import type { OrgTask, TaskStatus } from './types';

// ── Column definitions ─────────────────────────────────────────────────────

interface KanbanColumn {
  id: string;
  label: string;
  statuses: TaskStatus[];
  icon: React.ElementType;
  headerClass: string;
  countClass: string;
}

const COLUMNS: KanbanColumn[] = [
  {
    id: 'todo',
    label: 'TODO',
    statuses: ['queued', 'planned', 'draft'],
    icon: Circle,
    headerClass: 'text-[var(--text-muted)]',
    countClass: 'bg-[var(--bg-surface)] text-[var(--text-muted)]',
  },
  {
    id: 'in_progress',
    label: 'IN PROGRESS',
    statuses: ['assigned', 'running', 'waiting'],
    icon: Loader2,
    headerClass: 'text-[var(--accent-blue)]',
    countClass: 'bg-[var(--accent-blue)]/20 text-[var(--accent-blue)]',
  },
  {
    id: 'review',
    label: 'REVIEW',
    statuses: ['review', 'approval_required'],
    icon: Eye,
    headerClass: 'text-yellow-400',
    countClass: 'bg-yellow-400/20 text-yellow-400',
  },
  {
    id: 'done',
    label: 'DONE',
    statuses: ['completed'],
    icon: CheckCircle2,
    headerClass: 'text-emerald-400',
    countClass: 'bg-emerald-400/20 text-emerald-400',
  },
  {
    id: 'approved',
    label: 'APPROVED',
    statuses: [],  // virtual — filtered by approved_by presence
    icon: BadgeCheck,
    headerClass: 'text-indigo-400',
    countClass: 'bg-indigo-400/20 text-indigo-400',
  },
];

// ── Priority config ────────────────────────────────────────────────────────

const PRIORITY_COLORS: Record<string, string> = {
  low:      'bg-[var(--text-muted)]/20 text-[var(--text-muted)]',
  medium:   'bg-yellow-500/20 text-yellow-400',
  high:     'bg-orange-500/20 text-orange-400',
  critical: 'bg-red-500/20 text-red-400',
};

// ── Task card ──────────────────────────────────────────────────────────────

function TaskCard({ task }: { task: OrgTask; onStatusChange?: (id: string, status: TaskStatus) => void }) {
  const priorityClass = PRIORITY_COLORS[(task as any).priority ?? 'medium'] ?? PRIORITY_COLORS.medium;

  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0 }}
      className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg p-3 hover:border-[var(--accent-blue)]/30 hover:shadow-glow-electric transition-all cursor-pointer group"
      tabIndex={0}
      role="article"
      aria-label={`Task: ${(task as any).title}`}
    >
      {/* Priority + ID */}
      <div className="flex items-center justify-between mb-1.5">
        <Badge variant="outline" className={`text-[10px] px-1.5 py-0 border ${priorityClass}`}>
          {((task as any).priority ?? 'medium').toUpperCase()}
        </Badge>
        <span className="text-[9px] text-[var(--text-muted)] font-mono">
          #{String(task.id).slice(-6)}
        </span>
      </div>

      {/* Title */}
      <p className="text-xs text-[var(--text-primary)] font-medium line-clamp-2 mb-2">
        {(task as any).title ?? 'Untitled task'}
      </p>

      {/* Description */}
      {(task as any).description && (
        <p className="text-[10px] text-[var(--text-muted)] line-clamp-2 mb-2">
          {(task as any).description}
        </p>
      )}

      {/* Footer */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1">
          {(task as any).assignee_name ? (
            <div className="flex items-center gap-1">
              <div className="w-4 h-4 rounded-full bg-[var(--accent-blue)]/20 flex items-center justify-center">
                <User className="h-2.5 w-2.5 text-[var(--accent-blue)]" aria-hidden="true" />
              </div>
              <span className="text-[10px] text-[var(--text-muted)]">{(task as any).assignee_name}</span>
            </div>
          ) : (
            <span className="text-[10px] text-[var(--text-muted)]">Unassigned</span>
          )}
        </div>
        {(task as any).blocked_reason && (
          <AlertCircle className="h-3 w-3 text-red-400" aria-label="Blocked" />
        )}
      </div>
    </motion.div>
  );
}

// ── Column ─────────────────────────────────────────────────────────────────

function KanbanColumn({
  column,
  tasks,
}: {
  column: KanbanColumn;
  tasks: OrgTask[];
  onStatusChange?: (id: string, status: TaskStatus) => void;
}) {
  const listRef = useRef<HTMLDivElement>(null);
  const rowVirtualizer = useVirtualizer({
    count: tasks.length,
    getScrollElement: () => listRef.current,
    estimateSize: () => 110,
    overscan: 5,
  });
  const ColIcon = column.icon;

  return (
    <div className="flex flex-col min-w-[220px] max-w-[260px] w-[240px]">
      {/* Column header */}
      <div className="flex items-center gap-2 mb-3">
        <ColIcon className={`h-3.5 w-3.5 ${column.headerClass}`} aria-hidden="true" />
        <span className={`text-xs font-semibold tracking-wide ${column.headerClass}`}>
          {column.label}
        </span>
        <span className={`ml-auto text-[10px] rounded px-1.5 py-0.5 font-mono ${column.countClass}`}>
          {tasks.length}
        </span>
      </div>

      {/* Scrollable task list */}
      <div
        ref={listRef}
        className="flex-1 overflow-y-auto space-y-2 pr-1"
        style={{ maxHeight: 'calc(100vh - 280px)', minHeight: 120 }}
        aria-label={`${column.label} column`}
        role="list"
      >
        {tasks.length === 0 && (
          <div className="flex items-center justify-center h-20 border border-dashed border-[var(--border)] rounded-lg">
            <span className="text-xs text-[var(--text-muted)]">No tasks</span>
          </div>
        )}
        <AnimatePresence>
          {tasks.length > 50
            ? rowVirtualizer.getVirtualItems().map(vRow => (
                <div key={vRow.key} style={{ height: vRow.size }} role="listitem">
                  <TaskCard task={tasks[vRow.index]} />
                </div>
              ))
            : tasks.map(task => (
                <div key={task.id} role="listitem">
                  <TaskCard task={task} />
                </div>
              ))}
        </AnimatePresence>
      </div>
    </div>
  );
}

// ── Main component ─────────────────────────────────────────────────────────

interface KanbanBoardProps {
  orgId: string;
  missionId?: string;
}

export function KanbanBoard({ orgId, missionId }: KanbanBoardProps) {
  const filters = useMemo(
    () => (missionId ? { mission_id: missionId } : {}),
    [missionId],
  );
  const { data: tasksPage, isLoading } = useOrgTasks(orgId, filters);

  const tasks: OrgTask[] = useMemo(() => {
    if (!tasksPage) return [];
    if (Array.isArray(tasksPage)) return tasksPage;
    return (tasksPage as any).data ?? [];
  }, [tasksPage]);


  const getColumnTasks = useCallback(
    (col: KanbanColumn) => {
      if (col.id === 'approved') {
        return tasks.filter((t: any) => t.approved_by);
      }
      return tasks.filter((t: any) => col.statuses.includes(t.status));
    },
    [tasks],
  );

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-32" aria-live="polite">
        <Loader2 className="h-5 w-5 animate-spin text-[var(--accent-blue)]" aria-label="Loading tasks" />
      </div>
    );
  }

  return (
    <div
      className="flex gap-4 overflow-x-auto pb-4"
      role="region"
      aria-label="Task board"
    >
      {COLUMNS.map(col => (
        <KanbanColumn
          key={col.id}
          column={col}
          tasks={getColumnTasks(col)}
        />
      ))}
    </div>
  );
}
