import type { LearningRecord } from '../../lib/api/civilizationApi';

const STATUS_BADGE: Record<string, string> = {
  candidate: 'bg-yellow-100 text-yellow-800',
  validated: 'bg-blue-100 text-blue-800',
  promoted: 'bg-green-100 text-green-800',
  rejected: 'bg-red-100 text-red-800',
};

export function LearningLedger({ records }: { records: LearningRecord[] }) {
  return (
    <div className="space-y-2 max-h-80 overflow-y-auto">
      {records.length === 0 && (
        <div className="text-sm text-gray-400 text-center py-4">No learning candidates yet</div>
      )}
      {records.map(r => (
        <div key={r.id} className="border rounded-lg p-3 bg-white text-sm">
          <div className="flex items-center gap-2 mb-1">
            <span className={`text-xs px-1.5 py-0.5 rounded font-medium ${STATUS_BADGE[r.status] ?? 'bg-gray-100'}`}>
              {r.status}
            </span>
            {r.eval_score != null && (
              <span className="text-xs text-gray-500">
                Score: {(r.eval_score * 100).toFixed(0)}%
              </span>
            )}
          </div>
          <p className="text-gray-700 text-xs">{r.candidate}</p>
          <div className="text-[10px] text-gray-400 mt-1">
            From: {r.source_agent_id.slice(0, 8)}
            {r.promoted_memory_id && ' \u00b7 \u2713 Promoted to LTM'}
          </div>
        </div>
      ))}
    </div>
  );
}
