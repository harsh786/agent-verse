import { useMutation, useQueryClient } from '@tanstack/react-query';
import { evalSuitesApi } from '@/lib/api/client';
import { toast } from '@/stores/toast';

/**
 * The fully-autonomous rollout gate of one agent (GET /agents/{id}/rollout-gate).
 *
 * MEM-52: the gate is vouched for only by a golden-suite run that executed THIS
 * agent with its CURRENT config, on the suite's current dataset version, over at
 * least the minimum suite size. The panel shows which run vouches (or why none
 * does) and can start a gate run against this agent.
 */
export interface RolloutGateReport {
  gate_passed?: boolean;
  gate_status?: string;
  reason?: string;
  eval_suite_id?: string | null;
  min_pass_rate_required?: number;
  min_suite_size?: number;
  pass_rate?: number;
  run_count?: number;
  run_id?: string | null;
  run_at?: string | null;
  total_tasks?: number;
  dataset_version?: number | null;
  current_dataset_version?: number | null;
  agent_config_hash?: string | null;
  run_agent_config_hash?: string | null;
  conditions?: string[];
}

export function RolloutGatePanel({ agentId, report }: { agentId: string; report: RolloutGateReport }) {
  const qc = useQueryClient();
  const gatePassed = report.gate_passed ?? report.gate_status === 'passed';
  const passRate = report.pass_rate ?? 0;
  const runCount = report.run_count ?? 0;
  const threshold = typeof report.min_pass_rate_required === 'number' ? report.min_pass_rate_required : null;
  const suiteId = report.eval_suite_id ?? null;
  const conditions = Array.isArray(report.conditions) ? report.conditions : [];
  const configMatches =
    report.run_agent_config_hash != null && report.run_agent_config_hash === report.agent_config_hash;

  const runGate = useMutation({
    mutationFn: () => evalSuitesApi.runSuite(suiteId!, agentId),
    onSuccess: (res) => {
      toast({ kind: 'success', message: `Gate run ${res.run_id} started against this agent` });
      qc.invalidateQueries({ queryKey: ['agent-rollout', agentId] });
    },
    onError: (e) => toast({ kind: 'error', message: String(e) }),
  });

  return (
    <div className="p-4 rounded-lg border bg-card space-y-3">
      <div className="flex items-center gap-3 flex-wrap">
        <span className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-sm font-semibold ${gatePassed ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400' : 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400'}`}>
          {gatePassed ? '✓ Gate passed' : '✗ Gate blocked'}
        </span>
        <span className="text-xs text-muted-foreground" data-testid="rollout-suite">
          Eval suite: {suiteId ?? 'none attached'}
        </span>
        {suiteId && (
          <button
            onClick={() => runGate.mutate()}
            disabled={runGate.isPending}
            className="ml-auto text-xs px-2.5 py-1 rounded border border-emerald-500/30 text-emerald-500 hover:bg-emerald-500/10 disabled:opacity-50"
          >
            {runGate.isPending ? 'Starting…' : 'Run gate suite'}
          </button>
        )}
      </div>
      <div className="grid grid-cols-3 gap-3 text-sm">
        <div className="bg-muted/40 rounded-lg p-3">
          <p className="text-xs text-muted-foreground">Pass rate</p>
          <p className="font-semibold text-lg">{(passRate * 100).toFixed(1)}%</p>
        </div>
        <div className="bg-muted/40 rounded-lg p-3">
          <p className="text-xs text-muted-foreground">Runs</p>
          <p className="font-semibold text-lg">{runCount}</p>
        </div>
        <div className="bg-muted/40 rounded-lg p-3">
          <p className="text-xs text-muted-foreground">Threshold</p>
          <p className="font-semibold text-lg">
            {threshold == null ? '—' : `${(threshold * 100).toFixed(0)}%`}
          </p>
        </div>
      </div>
      {report.run_id && (
        <p className="text-xs text-muted-foreground" data-testid="rollout-vouching-run">
          Latest run against this agent: {report.run_id}
          {report.total_tasks != null && ` · ${report.total_tasks} tasks`}
          {report.dataset_version != null && ` · dataset v${report.dataset_version}`}
          {report.current_dataset_version != null && report.dataset_version !== report.current_dataset_version
            && ` (current v${report.current_dataset_version})`}
          {` · ${configMatches ? 'current agent config' : 'older agent config'}`}
          {report.min_suite_size != null && ` · min suite size ${report.min_suite_size}`}
        </p>
      )}
      {report.reason && (
        <p className="text-sm text-muted-foreground italic">{report.reason}</p>
      )}
      {conditions.length > 0 && (
        <div>
          <p className="text-xs text-muted-foreground mb-1">Conditions</p>
          <ul className="list-disc pl-4 text-sm space-y-1">
            {conditions.map((c, i) => <li key={i}>{c}</li>)}
          </ul>
        </div>
      )}
    </div>
  );
}
