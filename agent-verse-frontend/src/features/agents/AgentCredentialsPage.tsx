import { useState } from 'react';
import { useParams } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { KeyRound, Plus, Trash2, ShieldCheck, Clock, Copy, Download, AlertCircle, CheckCircle2 } from 'lucide-react';
import {
  AGENT_CREDENTIAL_SCOPES,
  credentialStatus,
  credentialsApi,
  downloadPrivateKey,
  type AgentCredential,
  type IssueCredentialRequest,
  type IssuedCredential,
} from '@/lib/api/client';
import { JARVISPageShell, JARVISStagger, JARVISStaggerItem } from '@/components/ui/JARVISPageShell';
import { toast } from '@/stores/toast';

const GRANTABLE = new Set<string>(AGENT_CREDENTIAL_SCOPES);

export function AgentCredentialsPage() {
  const { agentId = '' } = useParams<{ agentId: string }>();
  const qc = useQueryClient();
  const [showIssue, setShowIssue] = useState(false);
  const [description, setDescription] = useState('');
  const [scopes, setScopes] = useState('goals:read goals:write');
  const [expiresInDays, setExpiresInDays] = useState('90');
  const [issued, setIssued] = useState<IssuedCredential | null>(null);

  const { data: credentials = [], isLoading, isError } = useQuery<AgentCredential[]>({
    queryKey: ['agent-credentials', agentId],
    queryFn: () => credentialsApi.list(agentId),
    enabled: !!agentId,
  });

  const issueCredential = useMutation({
    mutationFn: (body: IssueCredentialRequest) => credentialsApi.issue(agentId, body),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ['agent-credentials', agentId] });
      setIssued(data);
      setShowIssue(false);
      setDescription('');
    },
    onError: (e) => toast({ kind: 'error', message: `Failed to issue credential. ${String(e)}` }),
  });

  const revokeCredential = useMutation({
    mutationFn: (keyId: string) => credentialsApi.revoke(agentId, keyId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['agent-credentials', agentId] }),
    onError: (e) => toast({ kind: 'error', message: `Failed to revoke credential. ${String(e)}` }),
  });

  const scopeList = scopes.split(' ').filter(Boolean);
  const badScopes = scopeList.filter((s) => !GRANTABLE.has(s));
  const activeCount = credentials.filter((c) => credentialStatus(c) === 'active').length;

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
              RS256 service-account keys for agent <code className="text-[#00D4FF] font-mono text-xs">{agentId}</code>.
              The agent signs a client assertion with its private key to obtain short-lived tokens.
            </p>
          </div>
          <button
            onClick={() => setShowIssue(true)}
            className="flex items-center gap-2 px-4 py-2 rounded-xl bg-[#00D4FF]/20 border border-[#00D4FF]/30 text-[#00D4FF] text-sm font-medium hover:bg-[#00D4FF]/30 transition-colors"
          >
            <Plus className="h-4 w-4" /> Issue Key
          </button>
        </div>

        {/* One-time private key banner */}
        {issued && (
          <div className="flex items-start gap-3 rounded-xl border border-emerald-400/30 bg-emerald-400/5 p-4">
            <CheckCircle2 className="h-5 w-5 text-emerald-400 shrink-0 mt-0.5" />
            <div className="flex-1 min-w-0">
              <p className="text-sm font-semibold text-emerald-400 mb-1">
                Key <code className="font-mono">{issued.key_id}</code> issued — save the private key now, it won't be shown again
              </p>
              <pre className="block text-xs font-mono text-[#F1F5F9] bg-[#0F1117] border border-[#1E2535] p-2 rounded whitespace-pre-wrap break-all">{issued.private_key_pem}</pre>
            </div>
            <button
              onClick={() => downloadPrivateKey(issued)}
              className="p-1.5 rounded-lg text-emerald-400 hover:bg-emerald-400/10 transition-colors shrink-0"
              title="Download .pem"
            >
              <Download className="h-4 w-4" />
            </button>
            <button
              onClick={() => {
                void navigator.clipboard.writeText(issued.private_key_pem);
                toast({ kind: 'success', message: 'Private key copied' });
              }}
              className="p-1.5 rounded-lg text-emerald-400 hover:bg-emerald-400/10 transition-colors shrink-0"
              title="Copy"
            >
              <Copy className="h-4 w-4" />
            </button>
            <button onClick={() => setIssued(null)} className="p-1.5 rounded-lg text-[#64748B] hover:text-[#94A3B8] hover:bg-[#252B3B] transition-colors shrink-0">
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
            { label: 'Active', value: activeCount, color: 'text-emerald-400' },
            { label: 'Expired / Revoked', value: credentials.length - activeCount, color: 'text-rose-400' },
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
            <p className="text-sm mt-1">Issue a service-account key to let an external system act as this agent.</p>
          </div>
        ) : (
          <JARVISStagger className="space-y-3">
            {credentials.map((cred) => {
              const status = credentialStatus(cred);
              const inactive = status !== 'active';
              return (
                <JARVISStaggerItem key={cred.key_id} interactive>
                  <div className={`rounded-xl border p-4 transition-colors hover:border-[#2D3748] ${
                    inactive ? 'border-rose-400/20 bg-rose-400/5' : 'border-[#1E2535] bg-[#1A1F2E]'
                  }`}>
                    <div className="flex items-start justify-between gap-3">
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 mb-1">
                          <ShieldCheck className={`h-4 w-4 ${inactive ? 'text-rose-400' : 'text-[#00D4FF]'}`} />
                          <code className="text-xs font-mono text-[#94A3B8]">{cred.key_id}</code>
                          {inactive && (
                            <span className="px-1.5 py-0.5 rounded-full bg-rose-400/10 border border-rose-400/20 text-rose-400 text-[10px] font-medium">
                              {status === 'revoked' ? 'Revoked' : 'Expired'}
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
                          {cred.created_at && (
                            <span className="flex items-center gap-1">
                              <Clock className="h-3 w-3" />
                              Issued {new Date(cred.created_at).toLocaleDateString()}
                            </span>
                          )}
                          {cred.expires_at && (
                            <span className={status === 'expired' ? 'text-rose-400' : ''}>
                              Expires {new Date(cred.expires_at).toLocaleDateString()}
                            </span>
                          )}
                          {cred.last_used_at && (
                            <span>Last used {new Date(cred.last_used_at).toLocaleDateString()}</span>
                          )}
                        </div>
                      </div>
                      {status !== 'revoked' && (
                        <button
                          onClick={() => revokeCredential.mutate(cred.key_id)}
                          className="p-1.5 rounded-lg text-[#64748B] hover:text-rose-400 hover:bg-rose-400/10 transition-colors shrink-0"
                          title="Revoke credential"
                          aria-label="Revoke credential"
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      )}
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
              <h2 className="text-lg font-semibold text-[#F1F5F9] mb-4">Issue New Service-Account Key</h2>
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
                    placeholder="goals:read goals:write"
                    className="w-full rounded-lg border border-[#1E2535] bg-[#0F1117] px-3 py-2 text-sm text-[#F1F5F9] placeholder-[#475569] focus:outline-none focus:border-[#00D4FF] font-mono" />
                  {badScopes.length > 0 && (
                    <p className="mt-1 text-[11px] text-rose-400">
                      Scopes not grantable to an agent: {badScopes.join(', ')}
                    </p>
                  )}
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
                      scopes: scopeList,
                      expires_in_days: expiresInDays ? parseInt(expiresInDays) : undefined,
                    })}
                    disabled={
                      !description.trim() || scopeList.length === 0 || badScopes.length > 0 || issueCredential.isPending
                    }
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
