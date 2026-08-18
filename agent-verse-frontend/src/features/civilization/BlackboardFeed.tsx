/**
 * BlackboardFeed — world-class shared blackboard activity feed.
 */
import { Clipboard, RefreshCw } from 'lucide-react';
import type { BlackboardEntry } from '../../lib/api/civilizationApi';

const TOPIC_CONFIG: Record<string, { bg: string; border: string; text: string; dot: string }> = {
  findings: {
    bg: 'rgba(34,197,94,0.06)',
    border: 'rgba(34,197,94,0.2)',
    text: 'text-green-400',
    dot: '#22c55e',
  },
  debate: {
    bg: 'rgba(168,85,247,0.06)',
    border: 'rgba(168,85,247,0.2)',
    text: 'text-purple-400',
    dot: '#a855f7',
  },
  error: {
    bg: 'rgba(239,68,68,0.06)',
    border: 'rgba(239,68,68,0.2)',
    text: 'text-red-400',
    dot: '#ef4444',
  },
};

function topicCfg(topic: string) {
  return TOPIC_CONFIG[topic?.toLowerCase()] ?? {
    bg: 'rgba(99,102,241,0.06)',
    border: 'rgba(99,102,241,0.2)',
    text: 'text-indigo-400',
    dot: '#6366f1',
  };
}

export function BlackboardFeed({ entries }: { entries: BlackboardEntry[] }) {
  if (entries.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-12 text-center space-y-3">
        <div
          className="w-14 h-14 rounded-2xl flex items-center justify-center"
          style={{ background: 'rgba(99,102,241,0.1)', border: '1px solid rgba(99,102,241,0.2)' }}
        >
          <Clipboard className="h-7 w-7 text-indigo-400" />
        </div>
        <div>
          <p className="text-sm font-medium text-slate-300">Blackboard is empty</p>
          <p className="text-xs text-slate-600 mt-1">Agent findings will appear here in real time</p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-2.5">
      <div className="flex items-center justify-between text-[10px] text-[#5A7494]">
        <span>{entries.length} posting{entries.length !== 1 ? 's' : ''}</span>
        <span className="flex items-center gap-1">
          <RefreshCw className="h-3 w-3" />
          Live
        </span>
      </div>

      {entries.map(e => {
        const cfg = topicCfg(e.topic ?? '');
        const confPct = Math.round((e.confidence ?? 0) * 100);
        const confColor = confPct > 70 ? '#22c55e' : confPct > 40 ? '#f59e0b' : '#ef4444';

        return (
          <div
            key={e.id}
            className="rounded-xl p-3 space-y-2"
            style={{ background: cfg.bg, border: `1px solid ${cfg.border}` }}
          >
            {/* Header */}
            <div className="flex items-start justify-between gap-2">
              <div className="flex items-center gap-1.5">
                <span className="w-1.5 h-1.5 rounded-full flex-shrink-0" style={{ background: cfg.dot }} />
                <span className={`text-[10px] font-semibold uppercase tracking-wide ${cfg.text}`}>
                  {e.topic ?? 'posting'}
                </span>
              </div>
              <div className="flex items-center gap-2 flex-shrink-0">
                <span className="text-[10px] font-mono text-slate-600">{e.author_agent_id?.slice(0, 8)}</span>
                {/* Confidence dot */}
                <div
                  className="w-3 h-3 rounded-full flex-shrink-0 border border-black/20"
                  style={{ background: confColor }}
                  title={`Confidence: ${confPct}%`}
                />
              </div>
            </div>

            {/* Content */}
            <p className="text-xs text-slate-300 leading-relaxed">{e.content}</p>

            {/* Footer */}
            <div className="flex items-center justify-between text-[10px]">
              <div className="flex items-center gap-3">
                {/* Confidence bar */}
                <div className="flex items-center gap-1.5">
                  <div className="w-12 h-1 bg-[#0F1826]/5 rounded-full overflow-hidden">
                    <div
                      className="h-full rounded-full transition-[color,background-color,border-color,opacity,box-shadow,transform]"
                      style={{ width: `${confPct}%`, background: confColor }}
                    />
                  </div>
                  <span className="text-[#5A7494]">{confPct}%</span>
                </div>
                {e.version !== undefined && (
                  <span className="text-slate-600">v{e.version}</span>
                )}
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
