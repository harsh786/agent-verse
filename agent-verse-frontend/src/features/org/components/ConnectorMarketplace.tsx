/**
 * ConnectorMarketplace — browse and install MCP connectors for an org.
 * Shows available connectors by category, with OAuth2 install flow.
 *
 * Skills:
 *   - frontend-design:   JARVIS dark card grid, category pills
 *   - emil-design-eng:   spring card hover, stagger entrance
 *   - impeccable-ui:     connector name dominant, tool count secondary
 *   - web-guidelines:    aria-label on cards, keyboard nav, touch targets
 *   - ui-ux-pro-max:     empty states, loading skeletons, optimistic install
 */
import { useState, useCallback } from 'react';
import { motion, useReducedMotion } from 'framer-motion';
import { Plug, CheckCircle2, Search, Loader2, X } from 'lucide-react';
import { cn } from '@/lib/utils';

interface Connector {
  id:          string;
  name:        string;
  category:    string;
  description: string;
  toolCount:   number;
  logoUrl?:    string;
  verified:    boolean;
  installed:   boolean;
}

interface ConnectorMarketplaceProps {
  orgId?:     string;   // available for future org-scoped API calls
  onClose?:   () => void;
  className?: string;
}

// Mock data — replace with real API when endpoint exists
const MOCK_CONNECTORS: Connector[] = [
  { id: 'github',    name: 'GitHub',      category: 'code',          description: 'Access repos, PRs, issues, and CI',          toolCount: 47, verified: true,  installed: false },
  { id: 'slack',     name: 'Slack',       category: 'communication', description: 'Send messages, read channels, manage users',  toolCount: 28, verified: true,  installed: false },
  { id: 'notion',    name: 'Notion',      category: 'productivity',  description: 'Read and write pages, databases, blocks',     toolCount: 31, verified: true,  installed: false },
  { id: 'linear',    name: 'Linear',      category: 'productivity',  description: 'Manage issues, projects, and cycles',        toolCount: 24, verified: true,  installed: false },
  { id: 'jira',      name: 'Jira',        category: 'productivity',  description: 'Create and track issues, sprints, boards',   toolCount: 35, verified: true,  installed: false },
  { id: 'teams',     name: 'MS Teams',    category: 'communication', description: 'Teams messages, meetings, channels',         toolCount: 22, verified: true,  installed: false },
  { id: 'gmail',     name: 'Gmail',       category: 'email',         description: 'Read, send, and organise email',             toolCount: 18, verified: true,  installed: false },
  { id: 'postgres',  name: 'PostgreSQL',  category: 'data',          description: 'Query and manage PostgreSQL databases',      toolCount: 15, verified: true,  installed: false },
  { id: 'airtable',  name: 'Airtable',    category: 'data',          description: 'Read and write Airtable bases and records',  toolCount: 20, verified: false, installed: false },
  { id: 'gitlab',    name: 'GitLab',      category: 'code',          description: 'MRs, pipelines, registry, and issues',       toolCount: 38, verified: false, installed: false },
];

const CATEGORIES = ['all', 'code', 'communication', 'productivity', 'data', 'email'] as const;

const CARD_SPRING = { type: 'spring', stiffness: 400, damping: 30 } as const;

export function ConnectorMarketplace({ orgId: _orgId, onClose, className }: ConnectorMarketplaceProps) {
  const reduce     = useReducedMotion();
  const [search, setSearch]     = useState('');
  const [cat, setCat]           = useState<string>('all');
  const [installing, setInstalling] = useState<string | null>(null);
  const [connectors, setConnectors] = useState(MOCK_CONNECTORS);

  const handleInstall = useCallback(async (connectorId: string) => {
    setInstalling(connectorId);
    // Simulate OAuth2 flow — in production this opens a popup
    await new Promise(r => setTimeout(r, 1200));
    setConnectors(prev =>
      prev.map(c => c.id === connectorId ? { ...c, installed: true } : c)
    );
    setInstalling(null);
  }, []);

  const handleUninstall = useCallback((connectorId: string) => {
    setConnectors(prev =>
      prev.map(c => c.id === connectorId ? { ...c, installed: false } : c)
    );
  }, []);

  const filtered = connectors.filter(c => {
    const matchCat    = cat === 'all' || c.category === cat;
    const matchSearch = !search || c.name.toLowerCase().includes(search.toLowerCase()) || c.description.toLowerCase().includes(search.toLowerCase());
    return matchCat && matchSearch;
  });

  return (
    <div className={cn('flex flex-col h-full', className)}>
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
            {connectors.filter(c => c.installed).length} installed · {connectors.length} available
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
            placeholder="Search connectors…"
            aria-label="Search connectors"
            className={cn(
              'w-full pl-8 pr-3 py-2 rounded-lg text-[13px]',
              'bg-[#252B3B] border border-[#2D3748] text-[#F1F5F9]',
              'placeholder:text-[#475569]',
              'focus:outline-none focus:ring-2 focus:ring-blue-500/60',
            )}
          />
        </div>
        <div
          role="tablist"
          aria-label="Filter by category"
          className="flex gap-1 overflow-x-auto scrollbar-none"
        >
          {CATEGORIES.map(c => (
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
              {c}
            </button>
          ))}
        </div>
      </div>

      {/* Connector grid */}
      <div className="flex-1 overflow-y-auto px-4 pb-4">
        {filtered.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 text-center">
            <Plug className="h-8 w-8 text-[#475569] mb-3" aria-hidden />
            <p className="text-[14px] text-[#94A3B8]">No connectors found</p>
          </div>
        ) : (
          <div className="space-y-2">
            {filtered.map((connector, i) => (
              <motion.div
                key={connector.id}
                layout
                initial={reduce ? { opacity: 0 } : { opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ ...CARD_SPRING, delay: reduce ? 0 : i * 0.04 }}
                whileHover={reduce ? {} : { scale: 1.01 }}
                className={cn(
                  'flex items-center gap-3 p-3 rounded-xl',
                  'bg-[#1A1F2E] border transition-colors duration-150',
                  connector.installed
                    ? 'border-emerald-500/20'
                    : 'border-[#1E2535] hover:border-[#2D3748]',
                )}
              >
                {/* Logo */}
                <div className="h-10 w-10 rounded-xl bg-[#252B3B] border border-[#2D3748] flex items-center justify-center shrink-0 text-[18px]">
                  {connector.logoUrl
                    ? <img src={connector.logoUrl} alt={connector.name} className="h-6 w-6 object-contain" loading="lazy" />
                    : connector.name.slice(0, 1)
                  }
                </div>

                {/* Info */}
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-1.5">
                    {/* impeccable-ui: name is dominant */}
                    <span className="text-[14px] font-semibold text-[#F1F5F9] truncate">
                      {connector.name}
                    </span>
                    {connector.verified && (
                      <CheckCircle2 className="h-3 w-3 text-blue-400 shrink-0" aria-label="Verified" />
                    )}
                  </div>
                  <div className="flex items-center gap-2 mt-0.5">
                    {/* tool count — secondary */}
                    <span className="text-[11px] text-[#475569] tabular-nums">
                      {connector.toolCount} tools
                    </span>
                    <span className="text-[11px] text-[#475569] capitalize">· {connector.category}</span>
                  </div>
                </div>

                {/* Action */}
                {connector.installed ? (
                  <button
                    onClick={() => handleUninstall(connector.id)}
                    aria-label={`Remove ${connector.name}`}
                    style={{ touchAction: 'manipulation' }}
                    className="px-3 py-1.5 rounded-lg text-[12px] font-medium text-emerald-400 bg-emerald-500/10 hover:bg-rose-500/10 hover:text-rose-400 ring-1 ring-emerald-500/20 transition-colors min-h-[32px]"
                  >
                    Installed
                  </button>
                ) : (
                  <button
                    onClick={() => handleInstall(connector.id)}
                    disabled={installing === connector.id}
                    aria-label={`Install ${connector.name}`}
                    style={{ touchAction: 'manipulation' }}
                    className={cn(
                      'px-3 py-1.5 rounded-lg text-[12px] font-medium min-h-[32px]',
                      'bg-blue-600/20 text-blue-300 hover:bg-blue-600/30 ring-1 ring-blue-500/30',
                      'transition-colors disabled:opacity-50',
                      'flex items-center gap-1',
                    )}
                  >
                    {installing === connector.id
                      ? <><Loader2 className="h-3 w-3 animate-spin" aria-hidden />Installing</>
                      : 'Install'}
                  </button>
                )}
              </motion.div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
