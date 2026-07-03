/**
 * SpawnLineageTimeline — visual tree of agent spawn events.
 *
 * Shows a vertical timeline with:
 * - Parent → child agent arrow
 * - Decision badge (approved/rejected)
 * - Capability chip
 * - Time + reason
 */
import { GitBranch, CheckCircle, XCircle, Clock } from 'lucide-react';
import type { SpawnRequest } from '../../lib/api/civilizationApi';

interface Props {
  spawns: SpawnRequest[];
}

export function SpawnLineageTimeline({ spawns }: Props) {
  if (spawns.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-12 text-center space-y-3">
        <div
          className="w-14 h-14 rounded-2xl flex items-center justify-center"
          style={{ background: 'rgba(99,102,241,0.1)', border: '1px solid rgba(99,102,241,0.2)' }}
        >
          <GitBranch className="h-7 w-7 text-indigo-400" />
        </div>
        <div>
          <p className="text-sm font-medium text-slate-300">No spawns yet</p>
          <p className="text-xs text-slate-600 mt-1">
            Spawn events appear when the society creates sub-agents
          </p>
        </div>
      </div>
    );
  }

  const approved = spawns.filter(s => s.decision === 'approved').length;
  const rejected = spawns.filter(s => s.decision !== 'approved').length;

  return (
    <div className="space-y-3">
      {/* Stats */}
      <div className="flex items-center gap-3 text-xs text-slate-500">
        <span className="flex items-center gap-1 text-green-400 font-semibold">
          <CheckCircle className="h-3.5 w-3.5" />
          {approved} approved
        </span>
        {rejected > 0 && (
          <>
            <span className="w-1 h-1 rounded-full bg-slate-700" />
            <span className="flex items-center gap-1 text-red-400 font-semibold">
              <XCircle className="h-3.5 w-3.5" />
              {rejected} rejected
            </span>
          </>
        )}
      </div>

      {/* Timeline */}
      <div className="relative">
        {/* Vertical line */}
        <div
          className="absolute left-4 top-5 bottom-5 w-px"
          style={{ background: 'linear-gradient(180deg, rgba(99,102,241,0.4) 0%, rgba(99,102,241,0.05) 100%)' }}
        />

        <div className="space-y-3">
          {spawns.map((s, i) => {
            const isApproved = s.decision === 'approved';
            const time = (s.created_at ?? '').slice(0, 16).replace('T', ' ');

            return (
              <div key={s.id ?? i} className="relative flex items-start gap-3 pl-10">
                {/* Dot */}
                <div
                  className={`absolute left-2 w-4 h-4 rounded-full border-2 flex items-center justify-center flex-shrink-0 ${
                    isApproved
                      ? 'bg-green-500/10 border-green-500'
                      : 'bg-red-500/10 border-red-500'
                  }`}
                  style={{ top: '2px' }}
                >
                  <span
                    className={`w-1.5 h-1.5 rounded-full ${isApproved ? 'bg-green-400' : 'bg-red-400'}`}
                  />
                </div>

                {/* Card */}
                <div
                  className="flex-1 rounded-xl border p-2.5 space-y-1.5"
                  style={{
                    background: 'rgba(15,23,42,0.6)',
                    borderColor: isApproved ? 'rgba(34,197,94,0.15)' : 'rgba(239,68,68,0.15)',
                  }}
                >
                  {/* Header row */}
                  <div className="flex items-center justify-between gap-2">
                    {/* Capability chip */}
                    <span
                      className="text-xs font-medium px-2 py-0.5 rounded-full border inline-block"
                      style={{
                        background: 'rgba(99,102,241,0.1)',
                        borderColor: 'rgba(99,102,241,0.3)',
                        color: '#a5b4fc',
                      }}
                    >
                      {s.requested_capability || 'unknown'}
                    </span>

                    {/* Decision badge */}
                    <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded border ${
                      isApproved
                        ? 'bg-green-500/10 text-green-400 border-green-500/20'
                        : 'bg-red-500/10 text-red-400 border-red-500/20'
                    }`}>
                      {s.decision ?? 'pending'}
                    </span>
                  </div>

                  {/* Parent → child */}
                  {s.requester_agent_id && (
                    <div className="flex items-center gap-1.5 text-[10px] font-mono text-slate-500">
                      <span className="text-slate-600">{s.requester_agent_id.slice(0, 10)}…</span>
                      <GitBranch className="h-3 w-3 text-indigo-500" />
                      <span className="text-indigo-400">new agent</span>
                    </div>
                  )}

                  {/* Reason */}
                  {s.reason && (
                    <p className="text-[11px] text-slate-400 leading-relaxed">{s.reason}</p>
                  )}

                  {/* Time */}
                  {time && (
                    <div className="flex items-center gap-1 text-[10px] text-slate-600">
                      <Clock className="h-3 w-3" />
                      {time}
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
