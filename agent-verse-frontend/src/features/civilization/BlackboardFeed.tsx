import type { BlackboardEntry } from '../../lib/api/civilizationApi';

export function BlackboardFeed({ entries }: { entries: BlackboardEntry[] }) {
  return (
    <div className="space-y-2 max-h-80 overflow-y-auto">
      {entries.length === 0 && (
        <div className="text-sm text-gray-400 text-center py-4">No findings posted yet</div>
      )}
      {entries.map(e => (
        <div key={e.id} className="border rounded-lg p-3 bg-white text-sm">
          <div className="flex justify-between items-start mb-1">
            <span className="font-medium text-purple-700 bg-purple-50 rounded px-1.5 py-0.5 text-xs">
              {e.topic}
            </span>
            <div className="flex items-center gap-1">
              <span className="text-xs text-gray-400">{e.author_agent_id.slice(0, 8)}</span>
              <div
                className="w-3 h-3 rounded-full"
                style={{ background: `hsl(${e.confidence * 120}, 60%, 50%)` }}
                title={`Confidence: ${(e.confidence * 100).toFixed(0)}%`}
              />
            </div>
          </div>
          <p className="text-gray-700 text-xs leading-relaxed">{e.content}</p>
          <div className="text-[10px] text-gray-400 mt-1">
            Conf: {(e.confidence * 100).toFixed(0)}% &middot; v{e.version}
          </div>
        </div>
      ))}
    </div>
  );
}
