/**
 * APIKeyManager — tenant API key management.
 * JARVIS motion: JARVISPageShell + JARVISStagger.
 */
import { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Key, Plus, Trash2, Copy, CheckCircle2, Shield } from 'lucide-react';
import { cn } from '@/lib/utils';
import { JARVISPageShell, JARVISStagger, JARVISStaggerItem, SPRING_FAST } from '@/components/ui/JARVISPageShell';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiFetch } from '@/lib/api/client';

interface APIKey {
  key_id: string;
  name: string;
  scopes: string[];
  last_used_at?: string;
  expires_at?: string;
  created_at: string;
  key_prefix?: string;  // first 8 chars for display
}

const SCOPE_COLORS: Record<string, string> = {
  'orgs:read':      'border-blue-500/30 text-blue-400',
  'missions:write': 'border-emerald-500/30 text-emerald-400',
  'approve':        'border-yellow-500/30 text-yellow-400',
  'admin':          'border-red-500/30 text-red-400',
};

function KeyCard({ apiKey, onDelete }: { apiKey: APIKey; onDelete: (id: string) => void }) {
  const [copied, setCopied] = useState(false);

  const copyPrefix = () => {
    if (apiKey.key_prefix) {
      navigator.clipboard.writeText(apiKey.key_prefix + '...');
      setCopied(true); setTimeout(() => setCopied(false), 1500);
    }
  };

  return (
    <motion.div layout className="bg-[#0F1623] border border-[#1E2535] rounded-xl p-4">
      <div className="flex items-start gap-3">
        <div className="p-2 rounded-lg bg-[#1A1F2E] flex-shrink-0">
          <Key className="h-4 w-4 text-[#00D4FF]" aria-hidden />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center justify-between mb-1">
            <span className="text-sm font-medium text-[#F1F5F9]">{apiKey.name}</span>
            <button
              onClick={() => onDelete(apiKey.key_id)}
              aria-label={`Delete key ${apiKey.name}`}
              style={{ touchAction: 'manipulation' }}
              className="p-1.5 rounded text-[#475569] hover:text-red-400 hover:bg-red-500/10 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-400/50"
            >
              <Trash2 className="h-3.5 w-3.5" aria-hidden />
            </button>
          </div>

          {apiKey.key_prefix && (
            <button onClick={copyPrefix} className="flex items-center gap-1.5 text-[11px] text-[#475569] hover:text-[#94A3B8] transition-colors mb-2 font-mono">
              {apiKey.key_prefix}••••••••
              {copied ? <CheckCircle2 className="h-3 w-3 text-emerald-400" /> : <Copy className="h-3 w-3" />}
            </button>
          )}

          <div className="flex flex-wrap gap-1.5">
            {apiKey.scopes.map(s => (
              <Badge key={s} variant="outline" className={cn('text-[10px]', SCOPE_COLORS[s] ?? 'border-[#1E2535] text-[#475569]')}>
                {s}
              </Badge>
            ))}
          </div>

          <p className="text-[10px] text-[#475569] mt-2">
            Created {new Date(apiKey.created_at).toLocaleDateString()}
            {apiKey.last_used_at && ` · Last used ${new Date(apiKey.last_used_at).toLocaleDateString()}`}
            {apiKey.expires_at && ` · Expires ${new Date(apiKey.expires_at).toLocaleDateString()}`}
          </p>
        </div>
      </div>
    </motion.div>
  );
}

interface APIKeyManagerProps { orgId: string; }

export function APIKeyManager({ orgId }: APIKeyManagerProps) {
  const qc = useQueryClient();
  const [showCreate, setShowCreate]   = useState(false);
  const [newKeyName, setNewKeyName]   = useState('');
  const [newKey, setNewKey]           = useState<string | null>(null);
  const [scopes, setScopes]           = useState<string[]>(['orgs:read', 'missions:write']);

  const AVAILABLE_SCOPES = ['orgs:read', 'missions:write', 'approve', 'admin'];

  const { data, isLoading } = useQuery({
    queryKey: ['api-keys', orgId],
    queryFn: () =>
      apiFetch<any>(`/v1/tenants/api-keys`)
        .then(r => (Array.isArray(r) ? r : r?.data ?? []))
        .catch(() => [] as APIKey[]),
    staleTime: 60_000,
  });

  const createKey = useMutation({
    mutationFn: () =>
      apiFetch<any>(`/v1/tenants/api-keys`, {
        method: 'POST',
        body: JSON.stringify({ name: newKeyName, scopes, org_id: orgId }),
      }),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ['api-keys', orgId] });
      setNewKey(res?.key ?? res?.api_key ?? null);
      setShowCreate(false);
      setNewKeyName('');
    },
  });

  const deleteKey = useMutation({
    mutationFn: (id: string) => apiFetch(`/v1/tenants/api-keys/${id}`, { method: 'DELETE' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['api-keys', orgId] }),
  });

  const keys: APIKey[] = data ?? [];

  return (
    <JARVISPageShell className="flex flex-col gap-5 max-w-2xl">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-base font-semibold text-[#F1F5F9] flex items-center gap-2">
            <Key className="h-4 w-4 text-[#00D4FF]" aria-hidden />
            API Keys
          </h1>
          <p className="text-[11px] text-[#475569] mt-0.5">
            Scoped keys for REST API, SDK, and channel integrations.
          </p>
        </div>
        <button
          onClick={() => setShowCreate(v => !v)}
          style={{ touchAction: 'manipulation' }}
          className="flex items-center gap-2 px-3 py-2 rounded-xl bg-[#1A1F2E] border border-[#1E2535] text-sm text-[#94A3B8] hover:text-[#F1F5F9] hover:border-[#00D4FF]/30 transition-colors min-h-[44px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00D4FF]/50"
        >
          <Plus className="h-4 w-4" aria-hidden />
          Create key
        </button>
      </div>

      {/* New key reveal */}
      <AnimatePresence>
        {newKey && (
          <motion.div
            initial={{ opacity: 0, y: -8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            transition={SPRING_FAST}
            className="bg-emerald-500/5 border border-emerald-500/30 rounded-xl p-4"
            role="alert"
          >
            <div className="flex items-center gap-2 mb-2">
              <Shield className="h-4 w-4 text-emerald-400" aria-hidden />
              <span className="text-sm font-medium text-emerald-400">Key created — copy it now</span>
            </div>
            <p className="text-[11px] text-[#475569] mb-2">This key will not be shown again.</p>
            <div className="flex items-center gap-2 bg-[#1A1F2E] rounded-lg px-3 py-2">
              <code className="text-xs text-[#94A3B8] flex-1 font-mono break-all">{newKey}</code>
              <button
                onClick={() => { navigator.clipboard.writeText(newKey); }}
                aria-label="Copy API key"
                className="text-[#475569] hover:text-[#00D4FF] transition-colors"
              >
                <Copy className="h-4 w-4" aria-hidden />
              </button>
            </div>
            <button onClick={() => setNewKey(null)} className="text-[11px] text-[#475569] hover:text-[#94A3B8] mt-2">Dismiss</button>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Create form */}
      <AnimatePresence>
        {showCreate && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={SPRING_FAST}
            style={{ overflow: 'hidden' }}
          >
            <div className="bg-[#0F1623] border border-[#1E2535] rounded-xl p-4 space-y-3">
              <div className="space-y-1.5">
                <label className="text-xs text-[#475569]">Key name</label>
                <Input value={newKeyName} onChange={e => setNewKeyName(e.target.value)} placeholder="Production key" className="bg-[#1A1F2E] border-[#1E2535] text-[#F1F5F9] placeholder:text-[#475569]" />
              </div>
              <div className="space-y-1.5">
                <p className="text-xs text-[#475569]">Scopes</p>
                <div className="flex flex-wrap gap-2">
                  {AVAILABLE_SCOPES.map(s => (
                    <button
                      key={s}
                      onClick={() => setScopes(ss => ss.includes(s) ? ss.filter(x => x !== s) : [...ss, s])}
                      style={{ touchAction: 'manipulation' }}
                      className={cn(
                        'px-2.5 py-1 rounded-lg text-xs border transition-colors',
                        scopes.includes(s) ? 'bg-[#00D4FF]/10 border-[#00D4FF]/30 text-[#00D4FF]' : 'border-[#1E2535] text-[#475569] hover:text-[#94A3B8]',
                      )}
                    >
                      {s}
                    </button>
                  ))}
                </div>
              </div>
              <button
                onClick={() => createKey.mutate()}
                disabled={createKey.isPending || !newKeyName.trim() || scopes.length === 0}
                style={{ touchAction: 'manipulation' }}
                className="px-4 py-2 rounded-lg bg-[#00D4FF] text-[#0A0D14] text-sm font-semibold hover:bg-[#00D4FF]/90 transition-colors disabled:opacity-40 min-h-[44px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00D4FF]/50"
              >
                {createKey.isPending ? 'Creating…' : 'Create Key'}
              </button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Key list */}
      {isLoading ? (
        <div className="space-y-2">
          {[1, 2].map(i => <div key={i} className="h-24 rounded-xl bg-[#0F1623] animate-pulse" aria-hidden />)}
        </div>
      ) : keys.length === 0 ? (
        <div className="flex flex-col items-center py-12 gap-3">
          <Key className="h-10 w-10 text-[#1E2535]" aria-hidden />
          <p className="text-[#475569] text-sm">No API keys yet.</p>
        </div>
      ) : (
        <JARVISStagger className="space-y-3">
          {keys.map(k => (
            <JARVISStaggerItem key={k.key_id}>
              <KeyCard apiKey={k} onDelete={id => deleteKey.mutate(id)} />
            </JARVISStaggerItem>
          ))}
        </JARVISStagger>
      )}
    </JARVISPageShell>
  );
}

export default APIKeyManager;
