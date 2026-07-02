/**
 * DebateViewer — show debate transcripts with proposals, critiques, votes, consensus.
 */

interface DebateMessage {
  id?: string;
  from_agent_id?: string;
  topic?: string;
  // API response format (from GET /civilizations/:id/debates)
  outcome?: string;
  result?: string;
  participants?: string[];
  rounds?: number;
  concluded_at?: string;
  // SSE message format (from stream events)
  payload?: {
    trigger?: string;
    debate_id?: string;
    consensus?: string;
    consensus_level?: number;
    claim_a?: { content?: string; confidence?: number };
    claim_b?: { content?: string; confidence?: number };
    status?: string;
  };
  ts?: string;
}

export function DebateViewer({ debates }: { debates: DebateMessage[] }) {
  if (!debates || debates.length === 0) {
    return (
      <div className="text-sm text-gray-400 text-center py-6">
        <div className="text-2xl mb-2">⚖️</div>
        <div>No debates conducted yet.</div>
        <div className="text-xs mt-1">Debates trigger automatically when agents post conflicting high-confidence findings.</div>
      </div>
    );
  }

  return (
    <div className="space-y-4 max-h-96 overflow-y-auto">
      {debates.map((debate, i) => {
        const payload = debate.payload || {};

        return (
          <div key={debate.id || i} className="border rounded-lg overflow-hidden">
            {/* Debate header */}
            <div className="bg-purple-50 px-3 py-2 border-b">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="text-purple-600 text-sm">⚖️</span>
                  <span className="font-medium text-sm text-purple-800">
                    {debate.topic || 'Debate'}
                  </span>
                </div>
                <span className="text-xs text-gray-400">
                  {String(debate.ts || '').slice(11, 19)}
                </span>
              </div>
              {payload.debate_id && (
                <div className="text-xs text-gray-400 mt-0.5 font-mono">
                  ID: {payload.debate_id.slice(0, 12)}...
                </div>
              )}
            </div>

            <div className="p-3 space-y-2">
              {/* Claims */}
              {(payload.claim_a || payload.claim_b) && (
                <div className="grid grid-cols-2 gap-2">
                  {payload.claim_a && (
                    <div className="bg-blue-50 rounded p-2 text-xs">
                      <div className="font-medium text-blue-700 mb-1">Claim A</div>
                      <div className="text-gray-700">{payload.claim_a.content || ''}</div>
                      {payload.claim_a.confidence !== undefined && (
                        <div className="text-blue-500 mt-1">
                          Conf: {(payload.claim_a.confidence * 100).toFixed(0)}%
                        </div>
                      )}
                    </div>
                  )}
                  {payload.claim_b && (
                    <div className="bg-orange-50 rounded p-2 text-xs">
                      <div className="font-medium text-orange-700 mb-1">Claim B</div>
                      <div className="text-gray-700">{payload.claim_b.content || ''}</div>
                      {payload.claim_b.confidence !== undefined && (
                        <div className="text-orange-500 mt-1">
                          Conf: {(payload.claim_b.confidence * 100).toFixed(0)}%
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )}

              {/* Consensus (SSE format: payload.consensus) */}
              {payload.consensus && (
                <div className="bg-green-50 border border-green-200 rounded p-2">
                  <div className="flex items-center gap-1.5 mb-1">
                    <span className="text-green-600">✓</span>
                    <span className="text-xs font-medium text-green-700">Consensus Reached</span>
                    {payload.consensus_level !== undefined && (
                      <span className="text-xs text-green-500">
                        ({(payload.consensus_level * 100).toFixed(0)}% confidence)
                      </span>
                    )}
                  </div>
                  <div className="text-xs text-gray-700">{payload.consensus}</div>
                </div>
              )}

              {/* Outcome + result (API response format) */}
              {!payload.consensus && debate.outcome && (
                <div className={`border rounded p-2 ${
                  debate.outcome === 'consensus'
                    ? 'bg-green-50 border-green-200'
                    : 'bg-yellow-50 border-yellow-200'
                }`}>
                  <div className="flex items-center gap-1.5 mb-1">
                    <span className={debate.outcome === 'consensus' ? 'text-green-600' : 'text-yellow-600'}>
                      {debate.outcome === 'consensus' ? '✓' : '⏳'}
                    </span>
                    <span className={`text-xs font-medium capitalize ${
                      debate.outcome === 'consensus' ? 'text-green-700' : 'text-yellow-700'
                    }`}>
                      {debate.outcome === 'consensus' ? 'Consensus Reached' : debate.outcome}
                    </span>
                    {debate.rounds !== undefined && (
                      <span className="text-xs text-gray-400 ml-auto">{debate.rounds} rounds</span>
                    )}
                  </div>
                  {debate.result && (
                    <div className="text-xs text-gray-700">{debate.result}</div>
                  )}
                </div>
              )}

              {/* Status */}
              {payload.status && !payload.consensus && (
                <div className={`text-xs px-2 py-1 rounded inline-block ${
                  payload.status === 'concluded' ? 'bg-green-100 text-green-700' : 'bg-yellow-100 text-yellow-700'
                }`}>
                  {payload.status}
                </div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

export default DebateViewer;
