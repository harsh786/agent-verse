import { Clock, CheckCircle, MinusCircle, RefreshCw } from 'lucide-react';
import type { TriggerEvent } from '../types';
import { useTriggerEvents } from '../hooks';

interface TriggerHistoryPanelProps {
  scheduleId: string;
}

export function TriggerHistoryPanel({ scheduleId }: TriggerHistoryPanelProps) {
  const { data: events, isLoading, refetch } = useTriggerEvents(scheduleId, 50);

  if (isLoading) {
    return (
      <div className="space-y-2">
        {[1, 2, 3].map((i) => (
          <div key={i} className="h-12 rounded-lg bg-muted animate-pulse" />
        ))}
      </div>
    );
  }

  if (!events?.length) {
    return (
      <div className="flex flex-col items-center justify-center py-10 text-muted-foreground">
        <Clock className="h-8 w-8 mb-2 opacity-30" />
        <p className="text-sm">This trigger has never fired.</p>
      </div>
    );
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <span className="text-xs text-muted-foreground">{events.length} events (most recent first)</span>
        <button
          onClick={() => refetch()}
          className="inline-flex items-center gap-1 rounded-lg border border-border px-2 py-1 text-xs hover:bg-muted transition-colors"
          aria-label="Refresh event history"
        >
          <RefreshCw className="h-3 w-3" />
          Refresh
        </button>
      </div>
      <div className="space-y-1.5 max-h-80 overflow-y-auto">
        {events.map((ev: TriggerEvent) => (
          <HistoryRow key={ev.event_id} event={ev} />
        ))}
      </div>
    </div>
  );
}

function HistoryRow({ event }: { event: TriggerEvent }) {
  const fired = new Date(event.fired_at);
  const skipCfg = event.simulated
    ? { label: 'Simulated', className: 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300' }
    : null;

  return (
    <div className="flex items-start gap-3 rounded-lg border border-border/50 bg-card px-3 py-2 text-xs">
      {/* Status icon */}
      <div className="shrink-0 mt-0.5">
        {event.goal_id_created ? (
          <CheckCircle className="h-3.5 w-3.5 text-emerald-500" aria-label="Goal created" />
        ) : event.simulated ? (
          <MinusCircle className="h-3.5 w-3.5 text-amber-500" aria-label="Simulated" />
        ) : (
          <MinusCircle className="h-3.5 w-3.5 text-muted-foreground" aria-label="Skipped" />
        )}
      </div>

      {/* Timestamp */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <time
            dateTime={event.fired_at}
            className="text-muted-foreground"
            title={fired.toISOString()}
          >
            {fired.toLocaleString()}
          </time>
          {event.goal_id_created && (
            <span className="rounded bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300 px-1.5 py-0.5">
              → {event.goal_id_created.slice(0, 8)}…
            </span>
          )}
          {skipCfg && (
            <span className={`rounded px-1.5 py-0.5 ${skipCfg.className}`}>
              {skipCfg.label}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
