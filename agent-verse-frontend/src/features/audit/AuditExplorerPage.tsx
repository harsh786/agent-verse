import React, { useEffect, useState } from 'react';

const API_BASE = (import.meta as any).env?.VITE_API_URL ?? 'http://localhost:8000';

export function AuditExplorerPage() {
  const [entries, setEntries] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState('');
  const apiKey =
    sessionStorage.getItem('av_api_key') ?? localStorage.getItem('av_api_key') ?? '';

  useEffect(() => {
    fetchAudit();
  }, []);

  const fetchAudit = async () => {
    setLoading(true);
    try {
      const resp = await fetch(`${API_BASE}/governance/audit?limit=200`, {
        headers: { 'X-API-Key': apiKey },
      });
      if (resp.ok) setEntries(await resp.json());
    } finally {
      setLoading(false);
    }
  };

  const filtered = entries.filter(
    e => !filter || JSON.stringify(e).toLowerCase().includes(filter.toLowerCase()),
  );

  return (
    <div className="p-6 max-w-6xl mx-auto">
      <div className="flex justify-between items-center mb-6">
        <h1 className="text-2xl font-bold">Audit Explorer</h1>
        <button
          onClick={fetchAudit}
          className="px-3 py-1 bg-gray-100 rounded hover:bg-gray-200 text-sm"
        >
          ↻ Refresh
        </button>
      </div>

      <input
        value={filter}
        onChange={e => setFilter(e.target.value)}
        placeholder="Filter by tool name, outcome, goal ID..."
        className="w-full px-3 py-2 border rounded mb-4 text-sm"
      />

      {loading ? (
        <div className="text-center py-8 text-gray-500">Loading audit log...</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="bg-gray-50 text-left">
                <th className="border px-3 py-2 font-medium">Time</th>
                <th className="border px-3 py-2 font-medium">Tool</th>
                <th className="border px-3 py-2 font-medium">Outcome</th>
                <th className="border px-3 py-2 font-medium">Goal ID</th>
              </tr>
            </thead>
            <tbody>
              {filtered.slice(0, 100).map((e: any, i: number) => (
                <tr key={i} className={i % 2 === 0 ? 'bg-white' : 'bg-gray-50'}>
                  <td className="border px-3 py-2 text-gray-500 whitespace-nowrap">
                    {e.created_at
                      ? new Date(e.created_at).toLocaleString()
                      : e.event_id?.slice(0, 8)}
                  </td>
                  <td className="border px-3 py-2 font-mono text-xs">
                    {e.tool_name || e.tool || '—'}
                  </td>
                  <td
                    className={`border px-3 py-2 ${
                      e.outcome?.includes('fail') || e.outcome?.includes('deny')
                        ? 'text-red-600'
                        : 'text-green-600'
                    }`}
                  >
                    {e.outcome || e.result || '—'}
                  </td>
                  <td className="border px-3 py-2 font-mono text-xs text-gray-500">
                    {e.goal_id?.slice(0, 16)}...
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {filtered.length === 0 && (
            <div className="text-center py-8 text-gray-400">No audit entries found.</div>
          )}
          {filtered.length > 100 && (
            <div className="text-center text-gray-500 text-sm mt-2">
              Showing 100 of {filtered.length} entries.
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default AuditExplorerPage;
