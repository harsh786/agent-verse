/**
 * DebateViewer — world-class debate transcript.
 *
 * Design: Dark transcript with two-column claim face-off,
 * confidence meters, verdict card with consensus level.
 */
import { useState } from 'react';
import { Scale, ChevronDown, ChevronRight, CheckCircle, Clock } from 'lucide-react';

interface DebateClaim {
  content?: string;
  confidence?: number;
}

interface DebateMessage {
  id?: string;
  from_agent_id?: string;
  topic?: string;
  outcome?: string;
  result?: string;
  participants?: string[];
  rounds?: number;
  concluded_at?: string;
  payload?: {
    trigger?: string;
    debate_id?: string;
    consensus?: string;
    consensus_level?: number;
    claim_a?: DebateClaim;
    claim_b?: DebateClaim;
    status?: string;
  };
  ts?: string;
}

function ConfidenceMeter({ value, colorClass }: { value: number; colorClass: string }) {
  const pct = Math.round(value * 100);
  return (
    <div className="mt-2 space-y-0.5">
      <div className="flex items-center justify-between text-[10px]">
        <span className="text-slate-500">Confidence</span>
        <span className={`font-semibold tabular-nums ${colorClass}`}>{pct}%</span>
      </div>
      <div className="h-1 bg-white/5 rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full transition-all duration-700 ${colorClass.includes('blue') ? 'bg-blue-500' : 'bg-orange-500'}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

function DebateCard({ debate, index }: { debate: DebateMessage; index: number }) {
  const [open, setOpen] = useState(index === 0);
  const payload = debate.payload ?? {};
  const hasClaims = payload.claim_a || payload.claim_b;
  const consensus = payload.consensus;
  const outcome = debate.outcome;
  const isResolved = consensus || outcome === 'consensus';
  const consensusLevel = payload.consensus_level;
  const rounds = debate.rounds;
  const timestamp = (debate.ts ?? debate.concluded_at ?? '').slice(11, 19);

  return (
    <div
      className="rounded-xl border overflow-hidden"
      style={{
        background: 'rgba(15,23,42,0.6)',
        borderColor: isResolved ? 'rgba(34,197,94,0.2)' : 'rgba(168,85,247,0.2)',
      }}
    >
      {/* Header */}
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center gap-3 px-3 py-2.5 hover:bg-white/[0.02] transition-colors text-left"
      >
        <Scale className="h-4 w-4 text-purple-400 flex-shrink-0" />
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-slate-200 truncate">
            {debate.topic || payload.trigger || 'Debate'}
          </p>
          <div className="flex items-center gap-2 mt-0.5">
            {payload.debate_id && (
              <span className="text-[10px] font-mono text-slate-600">
                #{payload.debate_id.slice(0, 8)}
              </span>
            )}
            {rounds !== undefined && (
              <span className="text-[10px] text-slate-600">{rounds} rounds</span>
            )}
            {timestamp && (
              <span className="text-[10px] text-slate-600 ml-auto">{timestamp}</span>
            )}
          </div>
        </div>
        {/* Status badge */}
        <span className={`flex-shrink-0 text-[10px] font-semibold px-2 py-0.5 rounded-full border ${
          isResolved
            ? 'bg-green-500/10 text-green-400 border-green-500/20'
            : 'bg-yellow-500/10 text-yellow-400 border-yellow-500/20'
        }`}>
          {isResolved ? 'Resolved' : payload.status ?? 'Open'}
        </span>
        {open ? <ChevronDown className="h-4 w-4 text-slate-500" /> : <ChevronRight className="h-4 w-4 text-slate-500" />}
      </button>

      {/* Expanded body */}
      {open && (
        <div className="px-3 pb-3 space-y-3 border-t border-white/5 pt-3">
          {/* Claims face-off */}
          {hasClaims && (
            <div className="grid grid-cols-2 gap-2">
              {/* Claim A */}
              <div
                className="rounded-lg p-2.5"
                style={{ background: 'rgba(59,130,246,0.08)', border: '1px solid rgba(59,130,246,0.15)' }}
              >
                <p className="text-[10px] font-semibold text-blue-400 mb-1">Claim A</p>
                <p className="text-xs text-slate-300 leading-relaxed">
                  {payload.claim_a?.content ?? '—'}
                </p>
                {payload.claim_a?.confidence !== undefined && (
                  <ConfidenceMeter value={payload.claim_a.confidence} colorClass="text-blue-400" />
                )}
              </div>
              {/* Claim B */}
              <div
                className="rounded-lg p-2.5"
                style={{ background: 'rgba(249,115,22,0.08)', border: '1px solid rgba(249,115,22,0.15)' }}
              >
                <p className="text-[10px] font-semibold text-orange-400 mb-1">Claim B</p>
                <p className="text-xs text-slate-300 leading-relaxed">
                  {payload.claim_b?.content ?? '—'}
                </p>
                {payload.claim_b?.confidence !== undefined && (
                  <ConfidenceMeter value={payload.claim_b.confidence} colorClass="text-orange-400" />
                )}
              </div>
            </div>
          )}

          {/* Consensus verdict */}
          {(consensus || (outcome === 'consensus' && debate.result)) && (
            <div
              className="rounded-lg p-2.5 flex items-start gap-2"
              style={{ background: 'rgba(34,197,94,0.08)', border: '1px solid rgba(34,197,94,0.2)' }}
            >
              <CheckCircle className="h-4 w-4 text-green-400 flex-shrink-0 mt-0.5" />
              <div className="min-w-0">
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-xs font-semibold text-green-300">Consensus Reached</span>
                  {consensusLevel !== undefined && (
                    <span className="text-[10px] text-green-500">
                      {(consensusLevel * 100).toFixed(0)}% confidence
                    </span>
                  )}
                </div>
                <p className="text-xs text-slate-300 leading-relaxed">
                  {consensus ?? debate.result}
                </p>
              </div>
            </div>
          )}

          {/* Pending outcome */}
          {!isResolved && payload.status && (
            <div className="flex items-center gap-2 text-xs text-amber-400">
              <Clock className="h-3.5 w-3.5" />
              <span>{payload.status}</span>
            </div>
          )}

          {/* Participants */}
          {Array.isArray(debate.participants) && debate.participants.length > 0 && (
            <div>
              <p className="text-[10px] text-slate-600 mb-1">Participants</p>
              <div className="flex flex-wrap gap-1">
                {debate.participants.map((p, i) => (
                  <span key={i} className="text-[10px] font-mono bg-white/5 border border-white/10 px-1.5 py-0.5 rounded">
                    {p.slice(0, 10)}…
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function DebateViewer({ debates }: { debates: DebateMessage[] }) {
  if (!debates || debates.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-12 text-center space-y-3">
        <div
          className="w-14 h-14 rounded-2xl flex items-center justify-center"
          style={{ background: 'rgba(168,85,247,0.1)', border: '1px solid rgba(168,85,247,0.2)' }}
        >
          <Scale className="h-7 w-7 text-purple-400" />
        </div>
        <div>
          <p className="text-sm font-medium text-slate-300">No debates yet</p>
          <p className="text-xs text-slate-600 mt-1 max-w-xs">
            Debates trigger automatically when agents post conflicting high-confidence findings
          </p>
        </div>
      </div>
    );
  }

  const resolved = debates.filter(d => d.outcome === 'consensus' || d.payload?.consensus);
  const open = debates.filter(d => d.outcome !== 'consensus' && !d.payload?.consensus);

  return (
    <div className="space-y-3">
      {/* Stats bar */}
      <div className="flex items-center gap-3 text-xs text-slate-500">
        <span className="text-green-400 font-semibold">{resolved.length} resolved</span>
        <span className="w-1 h-1 rounded-full bg-slate-700" />
        <span className="text-amber-400 font-semibold">{open.length} open</span>
        <span className="w-1 h-1 rounded-full bg-slate-700" />
        <span>{debates.length} total</span>
      </div>

      {debates.map((d, i) => (
        <DebateCard key={d.id ?? i} debate={d} index={i} />
      ))}
    </div>
  );
}

export default DebateViewer;
