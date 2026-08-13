import type { CoordinationEvent } from './types';

export function RunTimeline({ events }: { events: CoordinationEvent[] }) {
  return (
    <section aria-labelledby="run-timeline-heading">
      <h2 id="run-timeline-heading" className="text-lg font-semibold">Run timeline</h2>
      {events.length === 0 ? (
        <p className="mt-3 text-sm text-muted-foreground">No live events recorded yet.</p>
      ) : (
        <ol className="mt-4 space-y-3">
          {events.map((event) => (
            <li key={event.event_id} className="grid grid-cols-[4rem_1fr] gap-3 border-l-2 border-primary/40 pl-3 text-sm">
              <span className="font-mono text-xs text-muted-foreground">#{event.sequence}</span>
              <div><p className="font-medium">{event.event_type}</p><p className="text-xs text-muted-foreground">{event.producer}</p></div>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
