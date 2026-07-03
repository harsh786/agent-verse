/**
 * AgentInspectorDrawer — world-class slide-over inspector for civilization agents.
 *
 * Features:
 * - Tabbed: Overview / Messages / Config
 * - Reputation ring + KPI grid
 * - Bus message feed with topic colour coding
 * - Connector chips
 * - Animated open/close
 */
import { useEffect, useState } from 'react';
import { X, Activity, MessageSquare, Settings, Cpu } from 'lucide-react';
import { civilizationApi } from '../../lib/api/civilizationApi';

type DrawerTab = 'overview' | 'messages' | 'config';

interface Props {
  civilizationId: string;
  agentId: string | null;
  onClose: () => void;
}

const TOPIC_COLORS: Record<string, string> = {
  findings: 'bg-green-500/15 text-green-300 border-green-500/25',
  debate: 'bg-purple-500/15 text-purple-300 border-purple-500/25',
  spawning: 'bg-amber-500/15 text-amber-300 border-amber-500/25',
  learning: 'bg-blue-500/15 text-blue-300 border-blue-500/25',
  error: 'bg-red-500/15 text-red-300 border-red-500/25',
};

function topicClass(topic: string) {
  return TOPIC_COLORS[topic?.toLowerCase()] ?? 'bg-slate-500/15 text-slate-400 border-slate-500/25';
}

export function AgentInspectorDrawer({ civilizationId, agentId, onClose }: Props) {
  const [data, setData] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(false);
  const [tab, setTab] = useState<DrawerTab>('overview');

  useEffect(() => {
    if (!agentId) return;
    setLoading(true);
    setData(null);
    setTab('overview');
    civilizationApi.getAgentInspector(civilizationId, agentId)
      .then(d => setData(d as Record<string, unknown>))
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, [agentId, civilizationId]);

  if (!agentId) return null;

  const member = data?.member as Record<string, unknown> | undefined;
  const agentConfig = data?.agent_config as Record<string, unknown> | undefined;
  const messages = (data?.recent_messages as Record<string, unknown>[]) ?? [];
  const repPct = Math.round(Number(member?.reputation ?? 0.5) * 100);
  const repColor = repPct > 60 ? '#22c55e' : repPct > 30 ? '#f59e0b' : '#ef4444';
  const circumference = 2 * Math.PI * 24;
  const strokeDash = (repPct / 100) * circumference;

  const TABS: { key: DrawerTab; icon: React.ComponentType<{ className?: string }>; label: string }[] = [
    { key: 'overview', icon: Activity, label: 'Overview' },
    { key: 'messages', icon: MessageSquare, label: `Messages${messages.length ? ` (${messages.length})` : ''}` },
    { key: 'config', icon: Settings, label: 'Config' },
  ];

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-40 bg-black/40 backdrop-blur-sm"
        onClick={onClose}
        aria-hidden
      />

      {/* Drawer */}
      <div
        className="fixed inset-y-0 right-0 w-[380px] z-50 flex flex-col shadow-2xl"
        style={{
          background: 'linear-gradient(180deg, rgba(15,23,42,0.98) 0%, rgba(10,15,30,0.99) 100%)',
          borderLeft: '1px solid rgba(255,255,255,0.08)',
          backdropFilter: 'blur(20px)',
        }}
        role="complementary"
        aria-label="Agent Inspector"
      >
        {/* Header */}
        <div
          className="flex items-center gap-3 px-4 py-3 border-b"
          style={{ borderColor: 'rgba(255,255,255,0.06)' }}
        >
          <div
            className="w-9 h-9 rounded-xl flex items-center justify-center flex-shrink-0"
            style={{ background: 'rgba(99,102,241,0.15)', border: '1px solid rgba(99,102,241,0.25)' }}
          >
            <Cpu className="h-4.5 w-4.5 text-indigo-400" aria-hidden />
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-sm font-semibold text-slate-100">
              {agentConfig ? String(agentConfig.name ?? 'Agent') : 'Agent Inspector'}
            </p>
            <p className="text-[10px] font-mono text-slate-500">{agentId.slice(0, 16)}…</p>
          </div>
          <button
            onClick={onClose}
            aria-label="Close inspector"
            className="w-7 h-7 rounded-lg flex items-center justify-center text-slate-500 hover:text-slate-200 hover:bg-white/10 transition-all"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Tab bar */}
        <div
          className="flex border-b"
          style={{ borderColor: 'rgba(255,255,255,0.06)' }}
        >
          {TABS.map(({ key, icon: Icon, label }) => (
            <button
              key={key}
              onClick={() => setTab(key)}
              className={`flex items-center gap-1.5 flex-1 justify-center px-2 py-2.5 text-xs font-medium transition-colors border-b-2 ${
                tab === key
                  ? 'border-indigo-500 text-indigo-300'
                  : 'border-transparent text-slate-500 hover:text-slate-300'
              }`}
            >
              <Icon className="h-3.5 w-3.5" />
              <span className="hidden sm:inline">{label}</span>
            </button>
          ))}
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto">
          {loading && (
            <div className="flex items-center justify-center h-40 gap-3 text-slate-500">
              <span className="w-4 h-4 border-2 border-slate-600 border-t-indigo-400 rounded-full animate-spin" />
              <span className="text-sm">Loading…</span>
            </div>
          )}

          {!loading && (
            <div className="p-4">
              {/* ── Overview Tab ── */}
              {tab === 'overview' && (
                <div className="space-y-4">
                  {member ? (
                    <>
                      {/* Rep ring + KPIs */}
                      <div className="flex items-center gap-4">
                        {/* SVG rep ring */}
                        <div className="relative flex-shrink-0">
                          <svg width="60" height="60" className="-rotate-90">
                            <circle cx="30" cy="30" r="24" fill="none" stroke="rgba(255,255,255,0.05)" strokeWidth="4" />
                            <circle
                              cx="30" cy="30" r="24"
                              fill="none"
                              stroke={repColor}
                              strokeWidth="4"
                              strokeLinecap="round"
                              strokeDasharray={`${strokeDash} ${circumference}`}
                              style={{ transition: 'stroke-dasharray 0.8s ease', filter: `drop-shadow(0 0 6px ${repColor})` }}
                            />
                          </svg>
                          <div className="absolute inset-0 flex flex-col items-center justify-center">
                            <span className="text-sm font-bold tabular-nums" style={{ color: repColor }}>
                              {repPct}%
                            </span>
                            <span className="text-[9px] text-slate-600">rep</span>
                          </div>
                        </div>

                        {/* KPI grid */}
                        <div className="grid grid-cols-2 gap-2 flex-1">
                          {[
                            { label: 'Status', value: String(member.status ?? '—'), color: member.status === 'active' ? 'text-blue-400' : member.status === 'debating' ? 'text-purple-400' : 'text-slate-400' },
                            { label: 'Depth', value: `D:${member.depth ?? 0}` },
                            { label: 'Role', value: String(member.role ?? '—'), color: 'text-indigo-300' },
                            { label: 'Spent', value: `$${Number(member.budget_spent_usd ?? 0).toFixed(3)}`, color: 'text-amber-400' },
                          ].map(kpi => (
                            <div key={kpi.label}
                              className="rounded-lg p-2 text-center"
                              style={{ background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.06)' }}
                            >
                              <p className={`text-xs font-semibold ${kpi.color ?? 'text-slate-200'}`}>{kpi.value}</p>
                              <p className="text-[10px] text-slate-600 mt-0.5">{kpi.label}</p>
                            </div>
                          ))}
                        </div>
                      </div>

                      {/* Budget bar */}
                      {member.budget_usd !== undefined && (
                        <div className="space-y-1">
                          <div className="flex items-center justify-between text-[10px]">
                            <span className="text-slate-500">Budget used</span>
                            <span className="font-mono text-slate-400">
                              ${Number(member.budget_spent_usd ?? 0).toFixed(2)} / ${Number(member.budget_usd).toFixed(2)}
                            </span>
                          </div>
                          <div className="h-1.5 bg-white/5 rounded-full overflow-hidden">
                            <div
                              className="h-full rounded-full transition-all duration-700"
                              style={{
                                width: `${Math.min(100, (Number(member.budget_spent_usd) / Number(member.budget_usd)) * 100)}%`,
                                background: 'linear-gradient(90deg, #6366f1 0%, #a855f7 100%)',
                              }}
                            />
                          </div>
                        </div>
                      )}

                      {/* Timestamps */}
                      <div className="space-y-1 text-[10px] text-slate-600">
                        {member.spawned_at != null && (
                          <div>Spawned: {String(member.spawned_at).slice(0, 19).replace('T', ' ')}</div>
                        )}
                        {member.last_active_at != null && (
                          <div>Last active: {String(member.last_active_at).slice(0, 19).replace('T', ' ')}</div>
                        )}
                      </div>
                    </>
                  ) : (
                    <p className="text-sm text-slate-500 text-center py-8">No member data available.</p>
                  )}
                </div>
              )}

              {/* ── Messages Tab ── */}
              {tab === 'messages' && (
                <div className="space-y-2">
                  {messages.length === 0 ? (
                    <div className="flex flex-col items-center justify-center py-10 space-y-2 text-center">
                      <MessageSquare className="h-8 w-8 text-slate-700" />
                      <p className="text-sm text-slate-500">No recent messages</p>
                    </div>
                  ) : messages.map((msg, i) => (
                    <div
                      key={i}
                      className="rounded-lg p-2.5 space-y-1.5"
                      style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.06)' }}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded-full border ${topicClass(String(msg.topic ?? ''))}`}>
                          {String(msg.topic ?? 'message')}
                        </span>
                        <span className="text-[10px] text-slate-600 font-mono">
                          {String(msg.ts ?? '').slice(11, 19)}
                        </span>
                      </div>
                      <p className="text-[11px] text-slate-400 leading-relaxed font-mono break-all">
                        {JSON.stringify(msg.payload ?? {}).slice(0, 140)}
                        {JSON.stringify(msg.payload ?? {}).length > 140 ? '…' : ''}
                      </p>
                    </div>
                  ))}
                </div>
              )}

              {/* ── Config Tab ── */}
              {tab === 'config' && (
                <div className="space-y-3">
                  {agentConfig ? (
                    <>
                      {(['name', 'goal_template', 'autonomy_mode', 'system_prompt'] as const).map(key => {
                        const val = agentConfig[key as keyof typeof agentConfig];
                        if (!val) return null;
                        return (
                          <div key={key} className="space-y-1">
                            <p className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">
                              {key.replace(/_/g, ' ')}
                            </p>
                            <div
                              className="rounded-lg px-2.5 py-2 text-xs font-mono text-slate-300 break-all leading-relaxed"
                              style={{ background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.06)' }}
                            >
                              {String(val).slice(0, 200)}{String(val).length > 200 ? '…' : ''}
                            </div>
                          </div>
                        );
                      })}

                      {Array.isArray(agentConfig.connector_ids) && agentConfig.connector_ids.length > 0 && (
                        <div className="space-y-1">
                          <p className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">Connectors</p>
                          <div className="flex flex-wrap gap-1.5">
                            {(agentConfig.connector_ids as string[]).map((c, i) => (
                              <span
                                key={i}
                                className="text-[10px] font-mono px-2 py-0.5 rounded-full border"
                                style={{ background: 'rgba(99,102,241,0.1)', borderColor: 'rgba(99,102,241,0.3)', color: '#a5b4fc' }}
                              >
                                {c}
                              </span>
                            ))}
                          </div>
                        </div>
                      )}
                    </>
                  ) : (
                    <p className="text-sm text-slate-500 text-center py-8">No config available.</p>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </>
  );
}

export default AgentInspectorDrawer;
