import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useAuthStore } from '../../../stores/auth';

const API = import.meta.env.VITE_API_BASE_URL || '';
function apiFetch(path: string, apiKey: string, opts?: RequestInit) {
  return fetch(`${API}${path}`, { ...opts, headers: { 'X-API-Key': apiKey, 'Content-Type': 'application/json', ...opts?.headers } }).then(r => { if (!r.ok) throw new Error(`${r.status}`); return r.json(); });
}

const BUNDLES = [
  { id: 'hipaa', name: 'HIPAA', icon: '🏥', color: 'blue', desc: 'Healthcare — 6yr audit, PHI masking, supervised mode' },
  { id: 'gdpr', name: 'GDPR', icon: '🇪🇺', color: 'indigo', desc: 'EU Data Protection — right to erasure, consent, DPA' },
  { id: 'soc2', name: 'SOC 2', icon: '🔐', color: 'purple', desc: 'Security & availability — access review, incident response' },
  { id: 'india_dpdp', name: 'India DPDP', icon: '🇮🇳', color: 'orange', desc: 'DPDP Act 2023 — Aadhaar masking, data residency, consent' },
  { id: 'pci_dss', name: 'PCI-DSS', icon: '💳', color: 'red', desc: 'Payment cards — no card storage, tokenization required' },
];

export function GovernancePanel() {
  const apiKey = useAuthStore(s => s.apiKey) || '';
  const qc = useQueryClient();

  const { data: bundleData } = useQuery({
    queryKey: ['compliance-bundles'],
    queryFn: () => apiFetch('/governance/compliance/bundles', apiKey),
    enabled: !!apiKey,
  });

  const { data: pendingData } = useQuery({
    queryKey: ['pending-approvals'],
    queryFn: () => apiFetch('/governance/approvals?status=pending', apiKey),
    enabled: !!apiKey,
    refetchInterval: 10000,
  });

  const enableMutation = useMutation({
    mutationFn: (bundleId: string) => apiFetch(`/governance/compliance/bundles/${bundleId}/enable`, apiKey, { method: 'POST' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['compliance-bundles'] }),
  });

  const activeBundles: string[] = bundleData?.active || [];
  const effectiveMode = bundleData?.effective_max_autonomy || 'fully-autonomous';
  const pendingApprovals = pendingData?.approvals || [];

  return (
    <div className="space-y-6">
      {/* HITL Pending */}
      <div className="rounded-xl border bg-card p-5">
        <div className="flex items-center justify-between mb-4">
          <h2 className="font-semibold text-foreground">Pending Approvals</h2>
          {pendingApprovals.length > 0 && (
            <span className="px-2 py-0.5 rounded-full bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400 text-xs font-bold">
              {pendingApprovals.length} pending
            </span>
          )}
        </div>
        {pendingApprovals.length === 0 ? (
          <p className="text-sm text-muted-foreground">No pending approvals. All agents are running smoothly.</p>
        ) : (
          <div className="space-y-2">
            {pendingApprovals.slice(0, 5).map((a: Record<string, unknown>) => (
              <div key={String(a.request_id)} className="rounded-lg border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-900/20 p-3">
                <p className="text-sm font-medium text-foreground">{String(a.action || 'High-risk action')}</p>
                <p className="text-xs text-muted-foreground mt-0.5">Goal: {String(a.goal_id)} · Required: {String(a.required_approvers)} approver(s)</p>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Compliance Bundles */}
      <div className="rounded-xl border bg-card p-5">
        <div className="flex items-center justify-between mb-2">
          <h2 className="font-semibold text-foreground">Compliance Bundles</h2>
          <span className="text-xs text-muted-foreground">
            Effective mode: <span className="font-medium text-foreground">{effectiveMode}</span>
          </span>
        </div>
        <p className="text-xs text-muted-foreground mb-4">
          Enable compliance bundles to automatically apply guardrails, HITL requirements, and audit settings for regulated verticals.
        </p>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
          {BUNDLES.map(bundle => {
            const isActive = activeBundles.includes(bundle.id);
            return (
              <div
                key={bundle.id}
                className={`rounded-xl border p-4 transition-[color,background-color,border-color,opacity,box-shadow,transform] ${
                  isActive ? 'border-primary bg-primary/5' : 'border-border bg-card hover:border-primary/50'
                }`}
              >
                <div className="flex items-start justify-between mb-2">
                  <div className="flex items-center gap-2">
                    <span className="text-xl">{bundle.icon}</span>
                    <span className="font-semibold text-sm text-foreground">{bundle.name}</span>
                  </div>
                  <button
                    onClick={() => enableMutation.mutate(bundle.id)}
                    className={`text-xs px-2 py-1 rounded transition-colors ${
                      isActive
                        ? 'bg-primary text-primary-foreground'
                        : 'border border-border text-foreground hover:bg-muted'
                    }`}
                  >
                    {isActive ? 'Enabled' : 'Enable'}
                  </button>
                </div>
                <p className="text-xs text-muted-foreground">{bundle.desc}</p>
              </div>
            );
          })}
        </div>
      </div>

      {/* Time-Based Rules */}
      <div className="rounded-xl border bg-card p-5">
        <h2 className="font-semibold text-foreground mb-3">Time-Based Rules</h2>
        <div className="space-y-2">
          {[
            { name: 'No destructive ops overnight', desc: 'delete_*, drop_*, truncate_*', hours: '22:00–06:00 UTC', active: true },
            { name: 'No prod deploys on weekends', desc: 'deploy_*, *_to_prod, terraform_apply', hours: 'Sat & Sun', active: true },
          ].map(rule => (
            <div key={rule.name} className="flex items-center justify-between p-3 rounded-lg border bg-muted/30">
              <div>
                <p className="text-sm font-medium text-foreground">{rule.name}</p>
                <p className="text-xs text-muted-foreground">{rule.desc} · {rule.hours}</p>
              </div>
              <span className="text-xs px-2 py-0.5 rounded-full bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400">
                Active
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
