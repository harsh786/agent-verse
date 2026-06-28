import { useEffect, useRef, useState } from 'react';

const API_BASE = (import.meta as any).env?.VITE_API_URL ?? 'http://localhost:8000';

export function RpaLivePage() {
  const [sessions, setSessions] = useState<any[]>([]);
  const [selectedSession, setSelectedSession] = useState<string>('');
  const [lastScreenshot, setLastScreenshot] = useState<string>('');
  const [actionLog, setActionLog] = useState<string[]>([]);
  const [polling, setPolling] = useState(false);
  const intervalRef = useRef<number | null>(null);
  const apiKey =
    sessionStorage.getItem('av_api_key') ?? localStorage.getItem('av_api_key') ?? '';

  useEffect(() => {
    fetchSessions();
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, []);

  const fetchSessions = async () => {
    try {
      const resp = await fetch(`${API_BASE}/rpa/sessions`, {
        headers: { 'X-API-Key': apiKey },
      });
      if (resp.ok) setSessions(await resp.json());
    } catch {}
  };

  const startLiveView = (sessionId: string) => {
    setSelectedSession(sessionId);
    setPolling(true);
    setActionLog([]);
    if (intervalRef.current) clearInterval(intervalRef.current);
    intervalRef.current = setInterval(async () => {
      try {
        const resp = await fetch(`${API_BASE}/rpa/execute`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-API-Key': apiKey },
          body: JSON.stringify({
            tool_name: 'rpa_screenshot',
            arguments: {},
            session_id: sessionId,
          }),
        });
        if (resp.ok) {
          const data = await resp.json();
          if (data.artifact_url) setLastScreenshot(data.artifact_url);
          if (data.output) setActionLog(prev => [data.output, ...prev].slice(0, 20));
        }
      } catch {}
    }, 2000);
  };

  const stopLiveView = () => {
    if (intervalRef.current) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
    setPolling(false);
  };

  const requestTakeover = async () => {
    if (!selectedSession) return;
    await fetch(`${API_BASE}/rpa/sessions/${selectedSession}/takeover`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-API-Key': apiKey },
      body: JSON.stringify({ reason: 'Manual takeover requested by operator' }),
    });
    alert('Human takeover requested. You may now interact with the session.');
  };

  return (
    <div className="p-6 max-w-6xl mx-auto">
      <div className="flex justify-between items-center mb-6">
        <h1 className="text-2xl font-bold">RPA Live Sessions</h1>
        <button
          onClick={fetchSessions}
          className="px-3 py-1 bg-gray-100 rounded text-sm hover:bg-gray-200"
        >
          ↻ Refresh
        </button>
      </div>

      <div className="grid grid-cols-3 gap-6">
        {/* Session List */}
        <div className="border rounded-lg p-4">
          <h2 className="font-semibold mb-3">Active Sessions ({sessions.length})</h2>
          {sessions.length === 0 ? (
            <p className="text-gray-400 text-sm">No active RPA sessions.</p>
          ) : (
            sessions.map((s: any) => (
              <button
                key={s.session_id}
                onClick={() => startLiveView(s.session_id)}
                className={`w-full text-left px-3 py-2 rounded mb-1 text-sm ${
                  selectedSession === s.session_id
                    ? 'bg-blue-50 border border-blue-200'
                    : 'hover:bg-gray-50'
                }`}
              >
                <div className="font-mono text-xs text-gray-500">
                  {s.session_id.slice(0, 16)}...
                </div>
                <div
                  className={`text-xs ${
                    s.status === 'active' ? 'text-green-600' : 'text-gray-400'
                  }`}
                >
                  {s.status}
                </div>
              </button>
            ))
          )}
        </div>

        {/* Live View */}
        <div className="col-span-2 border rounded-lg p-4">
          {!selectedSession ? (
            <div className="flex items-center justify-center h-64 text-gray-400">
              Select a session to view live feed
            </div>
          ) : (
            <div>
              <div className="flex justify-between items-center mb-3">
                <span className="font-medium text-sm">
                  Session: {selectedSession.slice(0, 16)}...
                </span>
                <div className="flex gap-2">
                  {polling ? (
                    <button
                      onClick={stopLiveView}
                      className="px-3 py-1 bg-gray-200 rounded text-sm"
                    >
                      ⏸ Pause
                    </button>
                  ) : (
                    <button
                      onClick={() => startLiveView(selectedSession)}
                      className="px-3 py-1 bg-green-600 text-white rounded text-sm"
                    >
                      ▶ Resume
                    </button>
                  )}
                  <button
                    onClick={requestTakeover}
                    className="px-3 py-1 bg-orange-500 text-white rounded text-sm"
                  >
                    🖐 Takeover
                  </button>
                </div>
              </div>

              {lastScreenshot ? (
                <img
                  src={lastScreenshot}
                  alt="Live screenshot"
                  className="w-full rounded border"
                  style={{ maxHeight: '400px', objectFit: 'contain' }}
                />
              ) : (
                <div className="bg-gray-100 rounded h-64 flex items-center justify-center text-gray-400">
                  {polling ? 'Capturing screenshot...' : 'No screenshot available'}
                </div>
              )}

              <div className="mt-4">
                <h3 className="font-medium text-sm mb-2">Action Log</h3>
                <div className="bg-gray-900 text-green-400 rounded p-3 font-mono text-xs h-32 overflow-y-auto">
                  {actionLog.length === 0 ? (
                    <span className="text-gray-500">Waiting for actions...</span>
                  ) : (
                    actionLog.map((a, i) => <div key={i}>{a}</div>)
                  )}
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export default RpaLivePage;
