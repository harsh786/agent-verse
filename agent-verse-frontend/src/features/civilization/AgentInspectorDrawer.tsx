/**
 * AgentInspectorDrawer — detailed per-agent view.
 * Shows: config, current step, tool calls, cost, reputation history, bus messages.
 */
import { useEffect, useState } from 'react';
import { civilizationApi } from '../../lib/api/civilizationApi';

interface Props {
  civilizationId: string;
  agentId: string | null;
  onClose: () => void;
}

export function AgentInspectorDrawer({ civilizationId, agentId, onClose }: Props) {
  const [data, setData] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!agentId || !civilizationId) return;
    setLoading(true);
    civilizationApi.getAgentInspector(civilizationId, agentId)
      .then(setData)
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, [agentId, civilizationId]);

  if (!agentId) return null;

  const member = data?.member as Record<string, unknown> | undefined;
  const agentConfig = data?.agent_config as Record<string, unknown> | undefined;
  const messages = (data?.recent_messages as Record<string, unknown>[]) ?? [];

  return (
    <div className="fixed inset-y-0 right-0 w-96 bg-card shadow-2xl z-50 flex flex-col border-l">
      {/* Header */}
      <div className="flex items-center justify-between p-4 border-b bg-muted/30">
        <div>
          <h2 className="font-semibold text-foreground">Agent Inspector</h2>
          <div className="text-xs text-muted-foreground font-mono">{agentId?.slice(0, 16)}...</div>
        </div>
        <button
          onClick={onClose}
          className="text-muted-foreground hover:text-foreground/70 text-xl leading-none"
          aria-label="Close inspector"
        >
          ×
        </button>
      </div>

      {loading && (
        <div className="flex-1 flex items-center justify-center text-muted-foreground">Loading...</div>
      )}

      {!loading && (
        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          {/* Status & Reputation */}
          {member && (
            <div className="bg-blue-50 rounded-lg p-3">
              <div className="text-xs font-medium text-blue-700 mb-2">Member Stats</div>
              <div className="grid grid-cols-2 gap-2 text-xs">
                <div>
                  <div className="text-muted-foreground">Status</div>
                  <div className={`font-medium capitalize ${
                    member.status === 'active' ? 'text-blue-600' :
                    member.status === 'idle' ? 'text-slate-500' :
                    member.status === 'debating' ? 'text-purple-600' : 'text-muted-foreground'
                  }`}>{String(member.status)}</div>
                </div>
                <div>
                  <div className="text-muted-foreground">Reputation</div>
                  <div className="font-medium text-green-600">
                    {(Number(member.reputation || 0.5) * 100).toFixed(0)}%
                  </div>
                </div>
                <div>
                  <div className="text-muted-foreground">Depth</div>
                  <div className="font-medium">{String(member.depth ?? 0)}</div>
                </div>
                <div>
                  <div className="text-muted-foreground">Budget Spent</div>
                  <div className="font-medium">${Number(member.budget_spent_usd || 0).toFixed(3)}</div>
                </div>
              </div>
              {/* Reputation bar */}
              <div className="mt-2">
                <div className="text-xs text-muted-foreground mb-1">Reputation</div>
                <div className="w-full bg-muted rounded-full h-1.5">
                  <div
                    className="bg-green-500 h-1.5 rounded-full transition-all"
                    style={{ width: `${Number(member.reputation || 0.5) * 100}%` }}
                  />
                </div>
              </div>
            </div>
          )}

          {/* Agent Configuration */}
          {agentConfig && Object.keys(agentConfig).length > 0 && (
            <div>
              <div className="text-xs font-medium text-foreground/70 mb-2">Agent Configuration</div>
              <div className="space-y-1">
                {(['name', 'goal_template', 'autonomy_mode', 'system_prompt'] as const).map(key => {
                  const val = agentConfig[key];
                  if (!val) return null;
                  return (
                    <div key={key} className="text-xs">
                      <div className="text-muted-foreground uppercase tracking-wide">{key.replace(/_/g, ' ')}</div>
                      <div className="text-foreground bg-muted/30 rounded p-1.5 mt-0.5 font-mono break-all">
                        {String(val).slice(0, 150)}{String(val).length > 150 ? '...' : ''}
                      </div>
                    </div>
                  );
                })}
                {Array.isArray(agentConfig.connector_ids) && agentConfig.connector_ids.length > 0 && (
                  <div className="text-xs">
                    <div className="text-muted-foreground uppercase tracking-wide">Connectors</div>
                    <div className="flex flex-wrap gap-1 mt-0.5">
                      {(agentConfig.connector_ids as string[]).map((c, i) => (
                        <span key={i} className="bg-blue-100 text-blue-700 rounded px-1.5 py-0.5 text-[10px]">
                          {c}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Recent Bus Messages */}
          {messages.length > 0 && (
            <div>
              <div className="text-xs font-medium text-foreground/70 mb-2">
                Recent Messages ({messages.length})
              </div>
              <div className="space-y-1.5 max-h-48 overflow-y-auto">
                {messages.map((msg, i) => (
                  <div key={i} className="text-xs bg-muted/40 rounded p-2 border">
                    <div className="flex justify-between items-center mb-0.5">
                      <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${
                        msg.topic === 'findings' ? 'bg-green-100 text-green-700' :
                        msg.topic === 'debate' ? 'bg-purple-100 text-purple-700' :
                        'bg-muted text-foreground/70'
                      }`}>{String(msg.topic)}</span>
                      <span className="text-muted-foreground">
                        {String(msg.ts || '').slice(11, 19)}
                      </span>
                    </div>
                    <div className="text-foreground/70 text-[11px] leading-relaxed">
                      {JSON.stringify(msg.payload || {}).slice(0, 100)}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {!member && !loading && (
            <div className="text-sm text-muted-foreground text-center py-8">
              No data available for this agent.
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default AgentInspectorDrawer;
