/**
 * ConnectorMarketplace — browse and connect real MCP connectors for an org.
 *
 * Backed by the live Connectors API (app/api/connectors.py): the catalog, the
 * installed list, register + credential test, OAuth start, and remove are all
 * real. No mock install — a connector shows "Connected" only after the backend
 * has actually registered it (and, for credential connectors, validated it).
 *
 * Skills:
 *   - frontend-design:   JARVIS dark card grid, category pills
 *   - emil-design-eng:   spring card hover, stagger entrance
 *   - impeccable-ui:     connector name dominant, honest connected/failed state
 *   - web-guidelines:    aria-label on cards, keyboard nav, touch targets
 *   - ui-ux-pro-max:     empty states, loading skeletons, real error surfaces
 */
import { useState, useCallback, useMemo } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Plug, CheckCircle2, Search, Loader2, X, Zap, Trash2, Activity, KeyRound, ShieldCheck, AlertTriangle,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import {
  connectorsApi,
  isTestPassed,
  type CatalogConnector,
  type InstalledConnector,
  type ConnectorTestResult,
} from '../connectorsApi';
import { ConnectorCredentialForm } from './ConnectorCredentialForm';

interface ConnectorMarketplaceProps {
  orgId?:     string;   // reserved for future org-scoped filtering
  onClose?:   () => void;
  className?: string;
}

const CARD_SPRING = { type: 'spring', stiffness: 400, damping: 30 } as const;

const AUTH_LABEL: Record<string, string> = {
  api_key:           'API key',
  bearer:            'Token',
  basic:             'Username + token',
  oauth_ac:          'OAuth',
  connection_string: 'Connection string',
  none:              'Built-in',
};

export function ConnectorMarketplace({ orgId: _orgId, onClose, className }: ConnectorMarketplaceProps) {
  const qc = useQueryClient();
  const [search, setSearch] = useState('');
  const [cat, setCat] = useState<string>('all');
  const [formConnector, setFormConnector] = useState<CatalogConnector | null>(null);
  const [oauthBusy, setOauthBusy] = useState<string | null>(null);
  const [testResults, setTestResults] = useState<Record<string, ConnectorTestResult>>({});
  const [testingId, setTestingId] = useState<string | null>(null);

  const catalogQ = useQuery({
    queryKey: ['connectors', 'catalog'],
    queryFn: connectorsApi.catalog,
    staleTime: 60_000,
  });
  const installedQ = useQuery({
    queryKey: ['connectors', 'installed'],
    queryFn: connectorsApi.installed,
    staleTime: 30_000,
  });

  const refetchAll = useCallback(() => {
    qc.invalidateQueries({ queryKey: ['connectors'] });
  }, [qc]);

  // Map a connector name → its registered record, so we can test/remove it.
  const installedByName = useMemo(() => {
    const m = new Map<string, InstalledConnector>();
    for (const c of installedQ.data ?? []) m.set(c.name.toLowerCase().trim(), c);
    return m;
  }, [installedQ.data]);

  const categories = useMemo(() => {
    const set = new Set<string>();
    for (const c of catalogQ.data ?? []) set.add(c.category);
    return ['all', ...[...set].sort()];
  }, [catalogQ.data]);

  const connectedCount = useMemo(
    () => (catalogQ.data ?? []).filter(c => c.is_configured || installedByName.has(c.name.toLowerCase())).length,
    [catalogQ.data, installedByName],
  );

  const filtered = useMemo(() => {
    const q = search.toLowerCase().trim();
    return (catalogQ.data ?? []).filter(c => {
      const matchCat = cat === 'all' || c.category === cat;
      const matchSearch =
        !q ||
        c.display_name.toLowerCase().includes(q) ||
        c.name.toLowerCase().includes(q) ||
        c.description.toLowerCase().includes(q);
      return matchCat && matchSearch;
    });
  }, [catalogQ.data, cat, search]);

  const removeMut = useMutation({
    mutationFn: (serverId: string) => connectorsApi.remove(serverId),
    onSuccess: refetchAll,
  });

  const handleConnect = useCallback((c: CatalogConnector) => {
    if (c.auth_type === 'oauth_ac') {
      // Try real server-side OAuth: get the provider authorize URL + open a
      // popup. If the server has no OAuth app configured for this connector,
      // the endpoint 400s — fall back to the credential form so the user can
      // enter their own OAuth app's client id/secret.
      setOauthBusy(c.name);
      connectorsApi.oauthStart(c.name)
        .then(({ auth_url }) => {
          window.open(auth_url, `oauth_${c.name}`, 'width=560,height=720,menubar=no,toolbar=no');
        })
        .catch(() => {
          setFormConnector(c);
        })
        .finally(() => setOauthBusy(null));
      return;
    }
    setFormConnector(c);
  }, []);

  const handleTest = useCallback(async (serverId: string) => {
    setTestingId(serverId);
    try {
      const r = await connectorsApi.test(serverId);
      setTestResults(prev => ({ ...prev, [serverId]: r }));
    } catch (err) {
      setTestResults(prev => ({
        ...prev,
        [serverId]: { status: 'failed', error: err instanceof Error ? err.message : 'Test failed' },
      }));
    } finally {
      setTestingId(null);
    }
  }, []);

  const loading = catalogQ.isLoading;
  const loadError = catalogQ.error;

  return (
    <div className={cn('relative flex flex-col h-full', className)}>
      {/* Header */}
      <div className="flex items-center gap-3 px-5 py-4 border-b border-[#1E2535] shrink-0">
        <div className="h-8 w-8 rounded-lg bg-violet-500/10 border border-violet-500/20 flex items-center justify-center">
          <Plug className="h-4 w-4 text-violet-400" aria-hidden />
        </div>
        <div className="flex-1 min-w-0">
          <h2 className="text-[15px] font-semibold text-[#F1F5F9] tracking-[-0.01em]">
            Connector Marketplace
          </h2>
          <p className="text-[11px] text-[#475569]">
            {loading ? 'Loading…' : `${connectedCount} connected · ${catalogQ.data?.length ?? 0} available`}
          </p>
        </div>
        {onClose && (
          <button onClick={onClose} aria-label="Close" style={{ touchAction: 'manipulation' }}
            className="p-2 rounded-lg text-[#475569] hover:text-[#94A3B8] hover:bg-[#1A1F2E] transition-colors min-w-[36px] min-h-[36px] flex items-center justify-center">
            <X className="h-4 w-4" aria-hidden />
          </button>
        )}
      </div>

      {/* Search + category filter */}
      <div className="px-4 py-3 space-y-2 shrink-0">
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-[#475569]" aria-hidden />
          <input
            type="search"
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Search 240+ connectors…"
            aria-label="Search connectors"
            className={cn(
              'w-full pl-8 pr-3 py-2 rounded-lg text-[13px]',
              'bg-[#252B3B] border border-[#2D3748] text-[#F1F5F9]',
              'placeholder:text-[#475569]',
              'focus:outline-none focus:ring-2 focus:ring-blue-500/60',
            )}
          />
        </div>
        <div role="tablist" aria-label="Filter by category" className="flex gap-1 overflow-x-auto scrollbar-none">
          {categories.map(c => (
            <button
              key={c}
              role="tab"
              aria-selected={cat === c}
              onClick={() => setCat(c)}
              style={{ touchAction: 'manipulation' }}
              className={cn(
                'px-2.5 py-1 rounded-lg text-[11px] font-medium capitalize whitespace-nowrap transition-colors duration-150',
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60',
                cat === c
                  ? 'bg-violet-500/10 text-violet-300 ring-1 ring-violet-500/30'
                  : 'text-[#94A3B8] hover:text-[#F1F5F9] hover:bg-[#1A1F2E]',
              )}
            >
              {c.replace(/_/g, ' ')}
            </button>
          ))}
        </div>
      </div>

      {/* Connector grid */}
      <div className="flex-1 overflow-y-auto px-4 pb-4">
        {loading ? (
          <div className="space-y-2" aria-label="Loading connectors">
            {[...Array(6)].map((_, i) => (
              <div key={i} className="h-16 rounded-xl bg-[#1A1F2E] animate-pulse" />
            ))}
          </div>
        ) : loadError ? (
          <div className="flex flex-col items-center justify-center py-16 text-center" role="alert">
            <AlertTriangle className="h-8 w-8 text-rose-400/70 mb-3" aria-hidden />
            <p className="text-[14px] text-[#94A3B8]">Couldn&apos;t load the connector catalog.</p>
            <button onClick={refetchAll} className="mt-3 px-3 py-1.5 rounded-lg text-[12px] text-blue-300 bg-blue-600/20 ring-1 ring-blue-500/30">
              Retry
            </button>
          </div>
        ) : filtered.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 text-center">
            <Plug className="h-8 w-8 text-[#475569] mb-3" aria-hidden />
            <p className="text-[14px] text-[#94A3B8]">No connectors match “{search}”.</p>
          </div>
        ) : (
          <div className="space-y-2">
            {filtered.map((connector) => {
              const installed = installedByName.get(connector.name.toLowerCase());
              const isConnected = connector.is_configured || !!installed;
              const testResult = installed ? testResults[installed.server_id] : undefined;
              return (
                <motion.div
                  key={connector.name}
                  layout
                  initial={false}
                  animate={{ opacity: 1, y: 0 }}
                  transition={CARD_SPRING}
                  className={cn(
                    'rounded-xl bg-[#1A1F2E] border transition-colors duration-150',
                    isConnected ? 'border-emerald-500/20' : 'border-[#1E2535] hover:border-[#2D3748]',
                  )}
                >
                  <div className="flex items-center gap-3 p-3">
                    {/* Icon */}
                    <div className="h-10 w-10 rounded-xl bg-[#252B3B] border border-[#2D3748] flex items-center justify-center shrink-0 text-[16px] font-semibold text-[#CBD5E1] uppercase">
                      {connector.display_name.slice(0, 1)}
                    </div>

                    {/* Info */}
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-1.5">
                        <span className="text-[14px] font-semibold text-[#F1F5F9] truncate">
                          {connector.display_name}
                        </span>
                        {connector.has_builtin && (
                          <span title="Built-in native handler" className="inline-flex items-center gap-0.5 text-[9px] font-semibold uppercase tracking-wide text-amber-300/90">
                            <Zap className="h-2.5 w-2.5" aria-hidden />built-in
                          </span>
                        )}
                        {isConnected && (
                          <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400 shrink-0" aria-label="Connected" />
                        )}
                      </div>
                      <div className="flex items-center gap-2 mt-0.5">
                        <span className="inline-flex items-center gap-1 text-[11px] text-[#64748B]">
                          <KeyRound className="h-2.5 w-2.5" aria-hidden />
                          {AUTH_LABEL[connector.auth_type] ?? connector.auth_type}
                        </span>
                        <span className="text-[11px] text-[#475569] capitalize">· {connector.category.replace(/_/g, ' ')}</span>
                      </div>
                    </div>

                    {/* Action */}
                    {isConnected ? (
                      installed ? (
                        <div className="flex items-center gap-1.5 shrink-0">
                          <button
                            onClick={() => handleTest(installed.server_id)}
                            disabled={testingId === installed.server_id}
                            aria-label={`Test ${connector.display_name}`}
                            style={{ touchAction: 'manipulation' }}
                            className="p-1.5 rounded-lg text-[#94A3B8] hover:text-blue-300 hover:bg-blue-500/10 transition-colors min-h-[32px] min-w-[32px] flex items-center justify-center disabled:opacity-50"
                          >
                            {testingId === installed.server_id
                              ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                              : <Activity className="h-3.5 w-3.5" aria-hidden />}
                          </button>
                          <button
                            onClick={() => removeMut.mutate(installed.server_id)}
                            disabled={removeMut.isPending}
                            aria-label={`Remove ${connector.display_name}`}
                            style={{ touchAction: 'manipulation' }}
                            className="p-1.5 rounded-lg text-[#94A3B8] hover:text-rose-400 hover:bg-rose-500/10 transition-colors min-h-[32px] min-w-[32px] flex items-center justify-center disabled:opacity-50"
                          >
                            <Trash2 className="h-3.5 w-3.5" aria-hidden />
                          </button>
                        </div>
                      ) : (
                        <span className="px-3 py-1.5 rounded-lg text-[12px] font-medium text-emerald-400 bg-emerald-500/10 ring-1 ring-emerald-500/20 shrink-0">
                          Connected
                        </span>
                      )
                    ) : (
                      <button
                        onClick={() => handleConnect(connector)}
                        disabled={oauthBusy === connector.name}
                        aria-label={`Connect ${connector.display_name}`}
                        style={{ touchAction: 'manipulation' }}
                        className={cn(
                          'px-3 py-1.5 rounded-lg text-[12px] font-medium min-h-[32px] shrink-0',
                          'bg-blue-600/20 text-blue-300 hover:bg-blue-600/30 ring-1 ring-blue-500/30',
                          'transition-colors disabled:opacity-50 flex items-center gap-1',
                        )}
                      >
                        {oauthBusy === connector.name
                          ? <><Loader2 className="h-3 w-3 animate-spin" aria-hidden />Opening…</>
                          : 'Connect'}
                      </button>
                    )}
                  </div>

                  {/* Inline test result for an installed connector */}
                  {testResult && (
                    <div
                      role="status"
                      className={cn(
                        'mx-3 mb-3 -mt-0.5 flex items-start gap-1.5 rounded-lg px-2.5 py-1.5 text-[11px]',
                        isTestPassed(testResult)
                          ? 'bg-emerald-500/10 text-emerald-300'
                          : 'bg-rose-500/10 text-rose-300',
                      )}
                    >
                      {isTestPassed(testResult)
                        ? <ShieldCheck className="h-3.5 w-3.5 shrink-0 mt-px" aria-hidden />
                        : <AlertTriangle className="h-3.5 w-3.5 shrink-0 mt-px" aria-hidden />}
                      <span className="min-w-0 whitespace-pre-wrap break-words">
                        {isTestPassed(testResult)
                          ? (testResult.detail || 'Verified.')
                          : (testResult.error || 'Verification failed.')}
                        {typeof testResult.latency_ms === 'number' && (
                          <span className="text-[#64748B]"> · {testResult.latency_ms}ms</span>
                        )}
                      </span>
                    </div>
                  )}
                </motion.div>
              );
            })}
          </div>
        )}
      </div>

      {/* Credential form modal */}
      <AnimatePresence>
        {formConnector && (
          <motion.div
            className="absolute inset-0 z-20 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm"
            initial={false}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={() => setFormConnector(null)}
          >
            <div onClick={(e) => e.stopPropagation()} className="w-full flex justify-center">
              <ConnectorCredentialForm
                connector={formConnector}
                onClose={() => setFormConnector(null)}
                onInstalled={refetchAll}
              />
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
