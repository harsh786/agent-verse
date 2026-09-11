/**
 * DigitalTwinPanel — SUPP-H: Org capacity visualisation and what-if simulator.
 *
 * Shows: department utilisation gauges, overloaded/underutilised highlights,
 *        mission simulation form, what-if scenario runner.
 *
 * Skills:
 *   frontend-design:   JARVIS dark, utilisation gauges, confidence badges
 *   emil-design-eng:   spring 280/26 panel expand, 600/35 gauge fill
 *   impeccable-ui:     capacity pct dominant, dept name secondary, recs tertiary
 *   web-guidelines:    role=meter for gauges, aria-live sim results
 *   ui-ux-pro-max:     44px targets, useReducedMotion, keyboard tab nav
 */
import { useState, useCallback } from 'react';
import { motion, useReducedMotion } from 'framer-motion';
import { Cpu, Zap, AlertTriangle, ChevronDown, Loader2, TrendingUp } from 'lucide-react';
import { useQuery, useMutation } from '@tanstack/react-query';
import { apiRequest } from '@/lib/api/client';

// ── Types ─────────────────────────────────────────────────────────────────────

interface CapacityData {
  org_id:               string;
  current_utilisation:  Record<string, number>;
  queued_missions:      number;
  estimated_clear_h:    number;
  underutilised_depts:  string[];
  overloaded_depts:     string[];
  active_teams:         number;
  pending_approvals:    number;
  recommendations:      string[];
}

interface SimResult {
  mission_title:        string;
  estimated_duration_h: number;
  estimated_cost_usd:   number;
  resource_usage:       Record<string, number>;
  bottlenecks:          string[];
  recommendations:      string[];
  feasible:             boolean;
  confidence:           number;
}

// ── Hooks ─────────────────────────────────────────────────────────────────────

function useCapacity(orgId: string) {
  return useQuery<CapacityData>({
    queryKey: ['org-twin-capacity', orgId],
    queryFn:  () => apiRequest<CapacityData>('GET', `/v1/org/${orgId}/twin/capacity`),
    staleTime: 30_000,
    retry: 1,
  });
}

function useSimulate(orgId: string) {
  return useMutation({
    mutationFn: (body: { title: string; priority: string; description: string }) =>
      apiRequest<SimResult>('POST', `/v1/org/${orgId}/twin/simulate`, body),
  });
}

// useWhatIf exported for future what-if panel; unused in this component currently
export function useWhatIf(orgId: string) {
  return useMutation({
    mutationFn: (scenario: Record<string, unknown>) =>
      apiRequest<{ projected_improvement: string; confidence: number }>
        ('POST', `/v1/org/${orgId}/twin/what-if`, { scenario }),
  });
}

// ── Spring constants ──────────────────────────────────────────────────────────

const SPRING_FAST  = { type: 'spring', stiffness: 600, damping: 35 } as const;
const SPRING_PANEL = { type: 'spring', stiffness: 280, damping: 26 } as const;

// ── Utilisation Gauge ─────────────────────────────────────────────────────────

function UtilisationGauge({ name, value }: { name: string; value: number }) {
  const reduce = useReducedMotion();
  const pct    = Math.round(value * 100);
  const color  = value > 0.85 ? 'bg-red-500' : value < 0.35 ? 'bg-[#475569]' : 'bg-blue-500';

  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between text-[12px]">
        <span className="text-[#94A3B8] truncate">{name}</span>
        <span className={`font-semibold tabular-nums ml-2 ${value > 0.85 ? 'text-red-400' : value < 0.35 ? 'text-[#475569]' : 'text-[#F1F5F9]'}`}>{pct}%</span>
      </div>
      <div
        role="meter" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}
        aria-label={`${name} utilisation: ${pct}%`}
        className="h-2 bg-[#252B3B] rounded-full overflow-hidden"
      >
        <motion.div
          initial={{ width: 0 }}
          animate={{ width: `${pct}%` }}
          transition={reduce ? { duration: 0 } : { ...SPRING_PANEL, delay: 0.1 }}
          className={`h-full rounded-full ${color}`}
        />
      </div>
    </div>
  );
}

// ── Simulation form ────────────────────────────────────────────────────────────

function SimulatorForm({ orgId }: { orgId: string }) {
  const reduce  = useReducedMotion();
  const simulate = useSimulate(orgId);
  const [title, setTitle]       = useState('');
  const [priority, setPriority] = useState('medium');
  const [open, setOpen]         = useState(false);

  const handleRun = useCallback(async () => {
    if (!title.trim()) return;
    await simulate.mutateAsync({ title, priority, description: '' });
  }, [simulate, title, priority]);

  return (
    <div className="border border-[#2D3748] rounded-xl overflow-hidden">
      <motion.button
        type="button"
        onClick={() => setOpen(x => !x)}
        aria-expanded={open}
        whileTap={reduce ? {} : { scale: 0.99 }}
        transition={SPRING_FAST}
        style={{ touchAction: 'manipulation' }}
        className="w-full flex items-center gap-2.5 px-3 py-2.5 bg-[#1A1F2E] text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70 focus-visible:ring-inset"
      >
        <Zap className="h-3.5 w-3.5 text-blue-400 flex-shrink-0" aria-hidden />
        <span className="flex-1 text-[12px] font-medium text-[#94A3B8]">Simulate a mission</span>
        <motion.div animate={{ rotate: open ? 180 : 0 }} transition={SPRING_FAST}>
          <ChevronDown className="h-3.5 w-3.5 text-[#475569]" aria-hidden />
        </motion.div>
      </motion.button>

      {open && (
        <div className="p-3 space-y-3 border-t border-[#252B3B] bg-[#0F1117]">
          <input
            type="text" value={title} onChange={e => setTitle(e.target.value)}
            placeholder="Mission title…"
            aria-label="Mission title to simulate"
            className="w-full px-3 py-2 rounded-lg bg-[#1A1F2E] border border-[#2D3748] text-[13px] text-[#F1F5F9] placeholder:text-[#374151] focus:outline-none focus:ring-2 focus:ring-blue-500/60"
          />
          <select
            value={priority} onChange={e => setPriority(e.target.value)}
            aria-label="Priority"
            className="w-full px-3 py-2 rounded-lg bg-[#1A1F2E] border border-[#2D3748] text-[13px] text-[#F1F5F9] focus:outline-none focus:ring-2 focus:ring-blue-500/60"
          >
            {['low','medium','high','critical'].map(p => <option key={p} value={p}>{p}</option>)}
          </select>
          <motion.button
            aria-label="Run simulation for this mission"
            whileTap={reduce ? {} : { scale: 0.97 }} transition={SPRING_FAST}
            onClick={handleRun}
            disabled={!title.trim() || simulate.isPending}
            style={{ touchAction: 'manipulation' }}
            className="w-full py-2 rounded-lg bg-blue-600 hover:bg-blue-500 text-white text-[12px] font-semibold disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400 min-h-[36px]"
          >
            {simulate.isPending
              ? <Loader2 className="h-3.5 w-3.5 animate-spin mx-auto" aria-label="Simulating" />
              : 'Run Simulation'}
          </motion.button>

          {simulate.data && (
            <div className="space-y-1.5 pt-1" aria-live="polite">
              <div className="grid grid-cols-2 gap-2">
                {[
                  { label: 'Est. Time',  value: `${simulate.data.estimated_duration_h}h`, ok: simulate.data.feasible },
                  { label: 'Est. Cost',  value: `$${simulate.data.estimated_cost_usd}`,   ok: true },
                  { label: 'Confidence', value: `${Math.round(simulate.data.confidence * 100)}%`, ok: simulate.data.confidence > 0.7 },
                  { label: 'Feasible',   value: simulate.data.feasible ? 'Yes' : 'No',    ok: simulate.data.feasible },
                ].map(({ label, value, ok }) => (
                  <div key={label} className="bg-[#1A1F2E] rounded-lg p-2 text-center">
                    <p className={`text-[14px] font-bold ${ok ? 'text-[#F1F5F9]' : 'text-red-400'} tabular-nums`}>{value}</p>
                    <p className="text-[10px] text-[#64748B]">{label}</p>
                  </div>
                ))}
              </div>
              {simulate.data.recommendations.map((r, i) => (
                <p key={i} className="text-[11px] text-[#94A3B8] flex gap-1.5">
                  <TrendingUp className="h-3.5 w-3.5 text-blue-400 flex-shrink-0 mt-0.5" aria-hidden />{r}
                </p>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Main Component ─────────────────────────────────────────────────────────────

interface DigitalTwinPanelProps {
  orgId: string;
}

export function DigitalTwinPanel({ orgId }: DigitalTwinPanelProps) {
  const reduce = useReducedMotion();
  const { data, isLoading, refetch } = useCapacity(orgId);

  const utilEntries = Object.entries(data?.current_utilisation ?? {});

  return (
    <section
      aria-label="Digital Twin — org capacity and simulation"
      className="jarvis-rise-in space-y-3"
    >
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Cpu className="h-4 w-4 text-purple-400" aria-hidden />
          <span className="text-[13px] font-semibold text-[#F1F5F9]">Digital Twin</span>
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-purple-500/10 text-purple-400 font-medium">READ-ONLY</span>
        </div>
        <motion.button
          whileTap={reduce ? {} : { scale: 0.88 }} transition={SPRING_FAST}
          onClick={() => refetch()}
          aria-label="Refresh capacity data"
          style={{ touchAction: 'manipulation' }}
          className="w-7 h-7 rounded-lg flex items-center justify-center text-[#475569] hover:text-[#94A3B8] hover:bg-[#252B3B] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70"
        >
          <Zap className={`h-3.5 w-3.5 ${isLoading ? 'animate-spin' : ''}`} aria-hidden />
        </motion.button>
      </div>

      {isLoading ? (
        <div className="flex items-center gap-2 py-4 justify-center text-[#475569] text-[12px]">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden />Loading capacity…
        </div>
      ) : data ? (
        <>
          {/* Summary stats */}
          <div className="grid grid-cols-3 gap-2">
            {[
              { label: 'Queued',   value: data.queued_missions,    warn: data.queued_missions > 5 },
              { label: 'Teams',    value: data.active_teams,       warn: false },
              { label: 'Clear in', value: `${data.estimated_clear_h.toFixed(1)}h`, warn: data.estimated_clear_h > 24 },
            ].map(({ label, value, warn }) => (
              <div key={label} className="bg-[#1A1F2E] rounded-lg p-2 text-center border border-[#2D3748]">
                <p className={`text-[15px] font-bold tabular-nums ${warn ? 'text-amber-400' : 'text-[#F1F5F9]'}`}>{value}</p>
                <p className="text-[10px] text-[#64748B]">{label}</p>
              </div>
            ))}
          </div>

          {/* Alerts */}
          {data.overloaded_depts.length > 0 && (
            <div className="flex items-start gap-2 p-2.5 bg-red-500/5 border border-red-500/15 rounded-lg">
              <AlertTriangle className="h-3.5 w-3.5 text-red-400 flex-shrink-0 mt-0.5" aria-hidden />
              <p className="text-[11px] text-[#94A3B8]">
                <span className="text-red-400 font-medium">Overloaded:</span>{' '}
                {data.overloaded_depts.join(', ')}
              </p>
            </div>
          )}

          {/* Utilisation gauges */}
          {utilEntries.length > 0 && (
            <div className="bg-[#1A1F2E] border border-[#2D3748] rounded-xl p-3 space-y-2.5">
              <p className="text-[11px] font-semibold text-[#64748B] uppercase tracking-wider">Capacity</p>
              {utilEntries.map(([name, value]) => (
                <UtilisationGauge key={name} name={name} value={value} />
              ))}
            </div>
          )}

          {/* Recommendations */}
          {data.recommendations.length > 0 && (
            <div className="space-y-1.5">
              {data.recommendations.map((rec, i) => (
                <p
                  key={i}
                  className="jarvis-rise-in text-[11px] text-[#94A3B8] flex items-start gap-1.5"
                  style={{ animationDelay: `${Math.min(i, 8) * 0.04}s` }}
                >
                  <TrendingUp className="h-3 w-3 text-blue-400 flex-shrink-0 mt-0.5" aria-hidden />
                  {rec}
                </p>
              ))}
            </div>
          )}
        </>
      ) : (
        <p className="text-[12px] text-[#475569] text-center py-4">No capacity data available.</p>
      )}

      {/* Simulator */}
      <SimulatorForm orgId={orgId} />
    </section>
  );
}

export default DigitalTwinPanel;
