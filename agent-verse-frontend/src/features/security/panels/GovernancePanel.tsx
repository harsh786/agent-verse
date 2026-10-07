import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { useAuthStore, getAuthHeader } from '../../../stores/auth';
import { API_BASE, governanceApi, type ApprovalRequest } from '@/lib/api/client';
import { formatWindowDays, formatWindowHours, hasTimeWindow } from '@/features/governance/policyWindow';

function apiFetch(path: string, opts?: RequestInit) {
  return fetch(`${API_BASE}${path}`, { ...opts, headers: { ...getAuthHeader(), 'Content-Type': 'application/json', ...opts?.headers } }).then(r => { if (!r.ok) throw new Error(`${r.status}`); return r.json(); });
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
    queryFn: () => apiFetch('/trust/compliance-bundles/active'),
    enabled: !!apiKey,
  });

  // The HITL gateway's queue (a03-F057-01): these requests really hold the
  // step until decided, with quorum. The old /trust/approvals list recorded
  // approvals nothing ever read, and is retired (410).
  const { data: pendingData } = useQuery({
    queryKey: ['approvals'],
    queryFn: () => governanceApi.listApprovals(),
    enabled: !!apiKey,
    refetchInterval: 10000,
  });

  // Tenant policies with a time window (hours / weekdays in the policy's own
  // IANA timezone): the real time-based rules the policy engine enforces. The
  // static "no destructive ops overnight / no weekend deploys" text described
  // the removed app.governance.time_policy module, which never ran.
  const { data: policies, isError: policiesError } = useQuery({
    queryKey: ['governance-policies'],
    queryFn: () => governanceApi.listGovernancePolicies(),
    enabled: !!apiKey,
  });
  const timeWindowed = Array.isArray(policies) ? policies.filter(hasTimeWindow) : [];

  const enableMutation = useMutation({
    mutationFn: (bundleId: string) => apiFetch(`/trust/compliance-bundles/${bundleId}/enable`, { method: 'POST' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['compliance-bundles'] }),
  });

  const activeBundles: string[] = bundleData?.active || [];
  const effectiveMode = bundleData?.effective_max_autonomy || 'fully-autonomous';
  const pendingApprovals: ApprovalRequest[] = Array.isArray(pendingData)
    ? pendingData.filter((a) => !a.status || a.status === 'pending')
    : [];

  return (
    <div className="space-y-6">
      {/* HITL Pending */}
      <div className="rounded-xl border bg-card p-5">
        <div className="flex items-center justify-between mb-4">
          <h2 className="font-semibold text-foreground">Pending Approvals</h2>
          <div className="flex items-center gap-3">
            {pendingApprovals.length > 0 && (
              <span className="px-2 py-0.5 rounded-full bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400 text-xs font-bold">
                {pendingApprovals.length} pending
              </span>
            )}
            <Link to="/approvals" className="text-xs text-primary hover:underline">
              Review in Approvals
            </Link>
          </div>
        </div>
        {pendingApprovals.length === 0 ? (
          <p className="text-sm text-muted-foreground">No pending approvals. All agents are running smoothly.</p>
        ) : (
          <div className="space-y-2">
            {pendingApprovals.slice(0, 5).map((a) => (
              <div key={a.request_id} className="rounded-lg border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-900/20 p-3">
                <p className="text-sm font-medium text-foreground">{a.action || 'High-risk action'}</p>
                <p className="text-xs text-muted-foreground mt-0.5">
                  Goal: {a.goal_id}
                  {a.risk_level ? ` · Risk: ${a.risk_level}` : ''}
                  {` · Approvals: ${a.approvals_received ?? 0}/${a.required_approvers ?? 1}`}
                </p>
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

      {/* Time-Based Rules: tenant policies with a time window */}
      <div className="rounded-xl border bg-card p-5">
        <div className="flex items-center justify-between mb-3">
          <h2 className="font-semibold text-foreground">Time-Based Rules</h2>
          <Link to="/governance" className="text-xs text-primary hover:underline">
            Manage policies
          </Link>
        </div>
        {policiesError ? (
          <p className="text-sm text-red-600 dark:text-red-400" role="alert">
            Could not load policies.
          </p>
        ) : timeWindowed.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No time-windowed policies. Give a policy active hours or weekdays (in its own
            timezone) in Governance to restrict tools by time.
          </p>
        ) : (
          <div className="space-y-2">
            {timeWindowed.map((p) => (
              <div
                key={p.policy_id}
                data-testid={`time-rule-${p.policy_id}`}
                className="flex items-center justify-between p-3 rounded-lg border bg-muted/30"
              >
                <div>
                  <p className="text-sm font-medium text-foreground">{p.name}</p>
                  <p className="text-xs text-muted-foreground">
                    {p.tools_pattern} · Active {formatWindowHours(p)} · {formatWindowDays(p)}
                  </p>
                </div>
                <span className="text-xs px-2 py-0.5 rounded-full bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400">
                  {p.action === 'deny' ? 'Deny' : 'Needs approval'}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
