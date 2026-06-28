/**
 * Spawn Lineage Timeline — visualises agent ancestry over time.
 */
import type { SpawnRequest } from '../../lib/api/civilizationApi';

interface SpawnLineageTimelineProps {
  spawns: SpawnRequest[];
}

export function SpawnLineageTimeline({ spawns }: SpawnLineageTimelineProps) {
  if (spawns.length === 0) {
    return (
      <div className="text-sm text-gray-400 text-center py-6">
        No spawn events recorded yet.
      </div>
    );
  }

  return (
    <div className="relative pl-4 space-y-4">
      <div className="absolute left-1.5 top-0 bottom-0 w-px bg-muted" />
      {spawns.map(s => (
        <div key={s.id} className="relative flex gap-3 text-sm">
          <div className={`absolute -left-2.5 w-4 h-4 rounded-full border-2 bg-card ${
            s.decision === 'approved' ? 'border-green-400' : 'border-red-400'
          }`} />
          <div className="ml-3">
            <div className="font-medium text-gray-700">{s.requested_capability}</div>
            <div className="text-xs text-gray-400">
              {s.created_at?.slice(0, 16).replace('T', ' ')} &middot; {s.decision}
            </div>
            {s.reason && <div className="text-xs text-gray-500 mt-0.5">{s.reason}</div>}
          </div>
        </div>
      ))}
    </div>
  );
}
