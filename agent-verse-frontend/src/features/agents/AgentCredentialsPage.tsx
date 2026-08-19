import { useState } from 'react';
import { useParams } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { KeyRound, Plus, Trash2, ShieldCheck, Clock, Copy, AlertCircle, CheckCircle2 } from 'lucide-react';
import { apiFetch } from '@/lib/api/client';
import { JARVISPageShell, JARVISStagger, JARVISStaggerItem } from '@/components/ui/JARVISPageShell';
import { toast } from '@/stores/toast';

interface AgentCredential {
  credential_id: string;
  key_prefix: string;
  description: string;
  scopes: string[];
  issued_at: string;
  expires_at?: string;
  last_used_at?: string;
}

interface IssueCredentialBody {
  description: string;
  scopes: string[];
  expires_in_days?: number;
}

export function AgentCredentialsPage() {
  const { agentId = '' } = useParams<{ agentId: string }>();
  const qc = useQueryClient();
  const [showIssue, setShowIssue] = useState(false);
  const [description, setDescription] = useState('');
  const [scopes, setScopes] = useState('read:goals write:goals');
  const [expiresInDays, setExpiresInDays] = useState('90');
  const [newKey, setNewKey] = useState<string | null>(null);

  const { data: credentials = [], isLoading, isError } = useQuery<AgentCredential[]>({
    queryKey: ['agent-credentials', agentId],
    queryFn: () => apiFetch<AgentCredential[]>(`/agents/${agentId}/credentials`),
    enabled: !!agentId,
  });

  const issueCredential = useMutation({
    mutationFn: (body: IssueCredentialBody) =>
      apiFetch<{ credential_id: string; api_key: string }>(`/agents/${agentId}/credentials`, {
        method: 'POST',
        body: JSON.stringify(body),
      }),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ['agent-credentials', agentId] });
      setNewKey(data.api_key);
      setShowIssue(false);
      setDescription('');
    },
  });

  const revokeCredential = useMutation({
    mutationFn: (credId: string) =>
      apiFetch(`/agents/${agentId}/credentials/${credId}`, { method: 'DELETE' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['agent-credentials', agentId] }),
  });

  const isExpired = (expiresAt?: string) =>
    expiresAt ? new Date(expiresAt) < new Date() : false;

  return (
    <JARVISPageShell>
      <div className="p-6 max-w-4xl mx-auto space-y-6">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-[#F1F5F9] flex items-center gap-2.5">
              <KeyRound className="h-6 w-6 text-[#00D4FF]" />
              Agent Credentials
            </h1>
            <p className="text-sm text-[#64748B] mt-0.5">
              API keys issued to agent <code className="text-[#00D4FF] font-mono text-xs">{agentId}</code> for external access.
            </p>
          </div>
          <button
            onClick={() => setShowIssue(true)}
            className="flex items-center gap-2 px-4 py-2 rounded-xl bg-[#00D4FF]/20 border border-[#00D4FF]/30 text-[#00D4FF] text-sm font-medium hover:bg-[#00D4FF]/30 transition-colors"
          >
            <Plus className="h-4 w-4" /> Issue Key
          </button>
        </div>

        {/* New key banner */}
        {newKey && (
          <div className="flex items-start gap-3 rounded-xl border border-emerald-400/30 bg-emerald-400/5 p-4">
            <CheckCircle2 className="h-5 w-5 text-emerald-400 shrink-0 mt-0.5" />
            <div className="flex-1 min-w-0">
              <p className="text-sm font-semibold text-emerald-400 mb-1">Key issued — copy it now, it won't be shown again</p>
              <code className="block text-xs font-mono text-[#F1F5F9] bg-[#0F1117] border border-[#1E2535] p-2 rounded break-all">{newKey}</code>
            </div>
            <button
              onClick={() => {
                void navigator.clipboard.writeText(newKey);
                toast({ kind: 'success', message: 'API key copied' });
              }}
              className="p-1.5 rounded-lg text-emerald-400 hover:bg-emerald-400/10 transition-colors shrink-0"
              title="Copy"
            >
              <Copy className="h-4 w-4" />
            </button>
            <button onClick={() => setNewKey(null)} className="p-1.5 rounded-lg text-[#64748B] hover:text-[#94A3B8] hover:bg-[#252B3B] transition-colors shrink-0">
              ✕
            </button>
          </div>
        )}

        {isError && (
          <div className="flex items-center gap-2 rounded-xl border border-rose-400/30 bg-rose-400/10 p-4 text-sm text-rose-400" role="alert">
            <AlertCircle className="h-4 w-4 shrink-0" />
            Failed to load credentials.
          </div>
        )}

        {/* KPI row */}
        <div className="grid grid-cols-3 gap-3">
          {[
            { label: 'Total Keys', value: credentials.length, color: 'text-[#00D4FF]' },
            { label: 'Active', value: credentials.filter((c) => !isExpired(c.expires_at)).length, color: 'text-emerald-400' },
            { label: 'Expired', value: credentials.filter((c) => isExpired(c.expires_at)).length, color: 'text-rose-400' },
          ].map(({ label, value, color }) => (
            <div key={label} className="rounded-xl border border-[#1E2535] bg-[#1A1F2E] p-4">
              <p className="text-xs text-[#64748B] mb-1">{label}</p>
              <p className={`text-2xl font-bold tabular-nums ${color}`}>{isLoading ? '—' : value}</p>
            </div>
          ))}
        </div>

        {/* Credentials list */}
        {isLoading ? (
          <div className="space-y-3">
            {[1, 2, 3].map((i) => <div key={i} className="h-20 rounded-xl bg-[#1A1F2E] animate-pulse border border-[#1E2535]" />)}
          </div>
        ) : credentials.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-20 text-[#475569] border border-[#1E2535] rounded-xl">
            <KeyRound className="h-12 w-12 mb-3 opacity-20" />
            <p className="font-medium text-[#94A3B8]">No credentials issued</p>
            <p className="text-sm mt-1">Issue an API key to allow external systems to act as this agent.</p>
          </div>
        ) : (
          <JARVISStagger className="space-y-3">
            {credentials.map((cred) => {
              const expired = isExpired(cred.expires_at);
              return (
                <JARVISStaggerItem key={cred.credential_id} interactive>
                  <div className={`rounded-xl border p-4 transition-colors hover:border-[#2D3748] ${
                    expired ? 'border-rose-400/20 bg-rose-400/5' : 'border-[#1E2535] bg-[#1A1F2E]'
                  }`}>
                    <div className="flex items-start justify-between gap-3">
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 mb-1">
                          <ShieldCheck className={`h-4 w-4 ${expired ? 'text-rose-400' : 'text-[#00D4FF]'}`} />
                          <code className="text-xs font-mono text-[#94A3B8]">{cred.key_prefix}…</code>
                          {expired && (
                            <span className="px-1.5 py-0.5 rounded-full bg-rose-400/10 border border-rose-400/20 text-rose-400 text-[10px] font-medium">
                              Expired
                            </span>
                          )}
                        </div>
                        <p className="text-sm text-[#F1F5F9] mb-1">{cred.description || 'No description'}</p>
                        <div className="flex flex-wrap gap-1 mb-2">
                          {cred.scopes.map((s) => (
                            <span key={s} className="px-1.5 py-0.5 rounded bg-[#00D4FF]/10 border border-[#00D4FF]/20 text-[#00D4FF] text-[10px] font-mono">
                              {s}
                            </span>
                          ))}
                        </div>
                        <div className="flex items-center gap-3 text-[11px] text-[#475569]">
                          <span className="flex items-center gap-1">
                            <Clock className="h-3 w-3" />
                            Issued {new Date(cred.issued_at).toLocaleDateString()}
                          </span>
                          {cred.expires_at && (
                            <span className={expired ? 'text-rose-400' : ''}>
                              Expires {new Date(cred.expires_at).toLocaleDateString()}
                            </span>
                          )}
                          {cred.last_used_at && (
                            <span>Last used {new Date(cred.last_used_at).toLocaleDateString()}</span>
                          )}
                        </div>
                      </div>
                      <button
                        onClick={() => revokeCredential.mutate(cred.credential_id)}
                        className="p-1.5 rounded-lg text-[#64748B] hover:text-rose-400 hover:bg-rose-400/10 transition-colors shrink-0"
                        title="Revoke credential"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  </div>
                </JARVISStaggerItem>
              );
            })}
          </JARVISStagger>
        )}

        {/* Issue modal */}
        {showIssue && (
          <div className="fixed inset-0 z-50 flex items-center justify-center p-4" role="dialog" aria-modal="true">
            <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={() => setShowIssue(false)} />
            <div className="relative w-full max-w-md rounded-2xl bg-[#1A1F2E] border border-[#2D3748] shadow-2xl p-6">
              <h2 className="text-lg font-semibold text-[#F1F5F9] mb-4">Issue New API Key</h2>
              <div className="space-y-4">
                <div>
                  <label className="block text-xs font-medium text-[#94A3B8] mb-1.5">Description</label>
                  <input value={description} onChange={(e) => setDescription(e.target.value)}
                    placeholder="e.g. CI pipeline integration"
                    className="w-full rounded-lg border border-[#1E2535] bg-[#0F1117] px-3 py-2 text-sm text-[#F1F5F9] placeholder-[#475569] focus:outline-none focus:border-[#00D4FF]" />
                </div>
                <div>
                  <label className="block text-xs font-medium text-[#94A3B8] mb-1.5">Scopes (space-separated)</label>
                  <input value={scopes} onChange={(e) => setScopes(e.target.value)}
                    placeholder="read:goals write:goals"
                    className="w-full rounded-lg border border-[#1E2535] bg-[#0F1117] px-3 py-2 text-sm text-[#F1F5F9] placeholder-[#475569] focus:outline-none focus:border-[#00D4FF] font-mono" />
                </div>
                <div>
                  <label className="block text-xs font-medium text-[#94A3B8] mb-1.5">Expires in (days, blank = no expiry)</label>
                  <input value={expiresInDays} onChange={(e) => setExpiresInDays(e.target.value)}
                    type="number" min="1" placeholder="90"
                    className="w-full rounded-lg border border-[#1E2535] bg-[#0F1117] px-3 py-2 text-sm text-[#F1F5F9] placeholder-[#475569] focus:outline-none focus:border-[#00D4FF]" />
                </div>
                <div className="flex gap-2 justify-end pt-2">
                  <button onClick={() => setShowIssue(false)}
                    className="px-4 py-2 rounded-lg border border-[#1E2535] text-sm text-[#94A3B8] hover:bg-[#252B3B] transition-colors">
                    Cancel
                  </button>
                  <button
                    onClick={() => issueCredential.mutate({
                      description,
                      scopes: scopes.split(' ').filter(Boolean),
                      expires_in_days: expiresInDays ? parseInt(expiresInDays) : undefined,
                    })}
                    disabled={!description.trim() || issueCredential.isPending}
                    className="px-4 py-2 rounded-lg bg-[#00D4FF]/20 border border-[#00D4FF]/30 text-[#00D4FF] text-sm font-medium hover:bg-[#00D4FF]/30 disabled:opacity-50 transition-colors">
                    {issueCredential.isPending ? 'Issuing…' : 'Issue Key'}
                  </button>
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    </JARVISPageShell>
  );
}

export default AgentCredentialsPage;
