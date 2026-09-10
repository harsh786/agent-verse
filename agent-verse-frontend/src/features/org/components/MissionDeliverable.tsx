/**
 * MissionDeliverable — renders the aggregated result an org mission produced.
 *
 * WS-2's finalize_mission writes the mission's `outputs` (the deliverable) and
 * `evidence` (supporting artifacts). This surfaces them in the live view. It is
 * a pure presentational component (no data fetching) so it can be unit-tested in
 * isolation from MissionDetail's SSE stream and mutations. Renders nothing when
 * there is no deliverable yet — nothing is fabricated.
 */
import { FileText } from 'lucide-react';

export function MissionDeliverable({
  outputs,
  evidence,
}: {
  outputs: unknown[];
  evidence: unknown[];
}) {
  if (outputs.length === 0) return null;

  return (
    <div className="rounded-xl border border-emerald-500/20 bg-emerald-500/5 p-3 space-y-2">
      <div className="flex items-center gap-2">
        <FileText className="h-3 w-3 text-emerald-400" aria-hidden="true" />
        <span className="text-[11px] font-semibold text-emerald-400 uppercase tracking-wider">
          Deliverable
        </span>
        <span className="ml-auto text-[10px] text-[#475569] tabular-nums">
          {outputs.length} output{outputs.length === 1 ? '' : 's'}
        </span>
      </div>
      <div className="space-y-1.5">
        {outputs.slice(0, 8).map((o, i) => (
          <div
            key={i}
            className="text-[11px] text-[#94A3B8] bg-[#0F1117] border border-[#1E2535] rounded-lg px-2.5 py-1.5 whitespace-pre-wrap break-words"
          >
            {typeof o === 'string' ? o : JSON.stringify(o, null, 2)}
          </div>
        ))}
      </div>
      {evidence.length > 0 && (
        <p className="text-[10px] text-[#475569]">
          {evidence.length} evidence item{evidence.length === 1 ? '' : 's'} attached
        </p>
      )}
    </div>
  );
}
