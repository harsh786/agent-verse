import { useState } from 'react';
import { Play, Pause, Trash2, Zap, ChevronRight, Clock, AlertCircle } from 'lucide-react';
import type { Trigger } from '../types';
import { usePauseTrigger, useResumeTrigger, useDeleteTrigger, useFireTriggerNow } from '../hooks';
import { TriggerDetailDrawer } from './TriggerDetailDrawer';

interface TriggerCardProps {
  trigger: Trigger;
}

const STATUS_COLORS = {
  active: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300',
  paused: 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300',
};

export function TriggerCard({ trigger }: TriggerCardProps) {
  const [showDetail, setShowDetail] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const pause = usePauseTrigger();
  const resume = useResumeTrigger();
  const del = useDeleteTrigger();
  const fireNow = useFireTriggerNow();

  const statusKey = trigger.paused ? 'paused' : 'active';

  function handleTogglePause(e: React.MouseEvent) {
    e.stopPropagation();
    if (trigger.paused) {
      resume.mutate(trigger.schedule_id);
    } else {
      pause.mutate(trigger.schedule_id);
    }
  }

  function handleDelete(e: React.MouseEvent) {
    e.stopPropagation();
    if (!confirmDelete) {
      setConfirmDelete(true);
      setTimeout(() => setConfirmDelete(false), 3000);
      return;
    }
    del.mutate(trigger.schedule_id);
  }

  function handleFire(e: React.MouseEvent) {
    e.stopPropagation();
    fireNow.mutate({ scheduleId: trigger.schedule_id });
  }

  return (
    <>
      <div
        role="button"
        tabIndex={0}
        onClick={() => setShowDetail(true)}
        onKeyDown={(e) => e.key === 'Enter' && setShowDetail(true)}
        className="flex items-center gap-4 px-4 py-3 hover:bg-muted/30 cursor-pointer transition-colors group"
        aria-label={`View trigger ${trigger.spec.name ?? trigger.spec.trigger_type}`}
      >
        {/* Type badge */}
        <div className="shrink-0 rounded-lg bg-muted/60 px-2 py-1 text-xs font-mono text-muted-foreground min-w-[110px] text-center">
          {trigger.spec.trigger_type}
        </div>

        {/* Main content */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium truncate">
              {trigger.spec.name ?? trigger.goal_template}
            </span>
            <span className={`inline-flex items-center rounded-full text-xs px-2 py-0.5 font-medium ${STATUS_COLORS[statusKey]}`}>
              {statusKey}
            </span>
          </div>
          <div className="flex items-center gap-3 mt-0.5 text-xs text-muted-foreground">
            <span className="truncate">{trigger.goal_template}</span>
            {trigger.next_fire_at && (
              <span className="flex items-center gap-1 shrink-0">
                <Clock className="h-3 w-3" />
                {new Date(trigger.next_fire_at).toLocaleString()}
              </span>
            )}
            {trigger.fire_count !== undefined && (
              <span className="shrink-0">fired {trigger.fire_count}×</span>
            )}
          </div>
        </div>

        {/* Actions */}
        <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity" onClick={(e) => e.stopPropagation()}>
          <ActionButton
            onClick={handleFire}
            disabled={fireNow.isPending}
            title="Fire now"
            aria-label="Fire trigger now"
          >
            <Zap className="h-3.5 w-3.5" />
          </ActionButton>
          <ActionButton
            onClick={handleTogglePause}
            disabled={pause.isPending || resume.isPending}
            title={trigger.paused ? 'Resume' : 'Pause'}
            aria-label={trigger.paused ? 'Resume trigger' : 'Pause trigger'}
          >
            {trigger.paused ? <Play className="h-3.5 w-3.5" /> : <Pause className="h-3.5 w-3.5" />}
          </ActionButton>
          <ActionButton
            onClick={handleDelete}
            disabled={del.isPending}
            title={confirmDelete ? 'Click again to confirm' : 'Delete'}
            className={confirmDelete ? 'text-destructive hover:bg-destructive/10' : undefined}
            aria-label="Delete trigger"
          >
            {confirmDelete ? <AlertCircle className="h-3.5 w-3.5" /> : <Trash2 className="h-3.5 w-3.5" />}
          </ActionButton>
        </div>

        <ChevronRight className="h-4 w-4 text-muted-foreground shrink-0 group-hover:translate-x-0.5 transition-transform" />
      </div>

      {showDetail && (
        <TriggerDetailDrawer trigger={trigger} onClose={() => setShowDetail(false)} />
      )}
    </>
  );
}

function ActionButton({
  onClick,
  disabled,
  children,
  title,
  className,
  'aria-label': ariaLabel,
}: {
  onClick: (e: React.MouseEvent) => void;
  disabled?: boolean;
  children: React.ReactNode;
  title?: string;
  className?: string;
  'aria-label'?: string;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      title={title}
      aria-label={ariaLabel}
      className={`rounded-md p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground transition-colors disabled:opacity-50 ${className ?? ''}`}
    >
      {children}
    </button>
  );
}
