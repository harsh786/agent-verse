import { useState } from 'react';
import { Play, Pause, Trash2, Zap, ChevronRight, Clock, AlertCircle, Repeat, History, Calendar } from 'lucide-react';
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

/** Human-readable schedule summary derived from the trigger spec. */
function scheduleSummary(spec: Trigger['spec']): string | null {
  if (spec.cron_expression) return spec.cron_expression;
  if (spec.interval_seconds) {
    const s = spec.interval_seconds;
    if (s % 86400 === 0) return `every ${s / 86400}d`;
    if (s % 3600 === 0) return `every ${s / 3600}h`;
    if (s % 60 === 0) return `every ${s / 60}m`;
    return `every ${s}s`;
  }
  if (spec.fire_at_iso) return `once @ ${new Date(spec.fire_at_iso).toLocaleString()}`;
  if (spec.poll_url) return `poll ${spec.poll_url}`;
  if (spec.webhook_token || spec.webhook_signature_secret) return 'on webhook call';
  if (spec.watch_goal_id) return `watch goal ${spec.watch_goal_id.slice(0, 8)}`;
  if (spec.condition || spec.condition_expression) return `when: ${spec.condition || spec.condition_expression}`;
  if (spec.mqtt_topic) return `mqtt: ${spec.mqtt_topic}`;
  return null;
}

/** Compact absolute+relative timestamp, e.g. "3/14, 9:00 AM (in 2h)". */
function fmtTime(iso?: string): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  const diffMs = d.getTime() - Date.now();
  const abs = Math.abs(diffMs);
  const mins = Math.round(abs / 60000);
  const hrs = Math.round(abs / 3600000);
  const days = Math.round(abs / 86400000);
  let rel: string;
  if (mins < 1) rel = 'just now';
  else if (mins < 60) rel = `${mins}m`;
  else if (hrs < 24) rel = `${hrs}h`;
  else rel = `${days}d`;
  const suffix = mins < 1 ? '' : diffMs >= 0 ? ` (in ${rel})` : ` (${rel} ago)`;
  return `${d.toLocaleString([], { dateStyle: 'short', timeStyle: 'short' })}${suffix}`;
}

export function TriggerCard({ trigger }: TriggerCardProps) {
  const [showDetail, setShowDetail] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const pause = usePauseTrigger();
  const resume = useResumeTrigger();
  const del = useDeleteTrigger();
  const fireNow = useFireTriggerNow();

  const statusKey = trigger.paused ? 'paused' : 'active';
  const schedule = scheduleSummary(trigger.spec);
  const nextFire = fmtTime(trigger.next_fire_at);
  const lastFire = fmtTime(trigger.last_fired_at);
  const created = fmtTime(trigger.created_at);

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
        aria-label={`View trigger ${trigger.spec.description ?? trigger.spec.trigger_type}`}
      >
        {/* Type badge */}
        <div className="shrink-0 rounded-lg bg-muted/60 px-2 py-1 text-xs font-mono text-muted-foreground min-w-[110px] text-center">
          {trigger.spec.trigger_type}
        </div>

        {/* Main content */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium truncate">
              {trigger.spec.description || trigger.goal_template || `${trigger.spec.trigger_type} trigger`}
            </span>
            <span className={`inline-flex items-center rounded-full text-xs px-2 py-0.5 font-medium ${STATUS_COLORS[statusKey]}`}>
              {statusKey}
            </span>
          </div>
          {/* Goal linkage — a trigger fires a goal template (or a bound goal id) */}
          <div className="text-xs text-muted-foreground truncate mt-0.5">
            {trigger.goal_template
              ? trigger.goal_template
              : trigger.goal_id
                ? <>goal <span className="font-mono">{trigger.goal_id}</span></>
                : <span className="italic opacity-70">no goal bound</span>}
          </div>
          {/* Metadata chips — schedule, next/last fire, created */}
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 mt-1 text-xs text-muted-foreground">
            {schedule && (
              <span className="flex items-center gap-1 shrink-0 font-mono">
                <Repeat className="h-3 w-3" />
                {schedule}
              </span>
            )}
            {nextFire && (
              <span className="flex items-center gap-1 shrink-0" title="Next fire">
                <Clock className="h-3 w-3" />
                next {nextFire}
              </span>
            )}
            {lastFire && (
              <span className="flex items-center gap-1 shrink-0" title="Last fired">
                <History className="h-3 w-3" />
                last {lastFire}
              </span>
            )}
            {created && (
              <span className="flex items-center gap-1 shrink-0" title="Created">
                <Calendar className="h-3 w-3" />
                created {created}
              </span>
            )}
            <span className="flex items-center gap-1 shrink-0 opacity-60 font-mono" title="Schedule ID">
              #{trigger.schedule_id.slice(0, 8)}
            </span>
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
