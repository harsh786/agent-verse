/**
 * MemoryBrowser — org/dept/agent memory inspector.
 *
 * JARVIS motion system:
 *   - JARVISPageShell:  blur-in page entry
 *   - JARVISStagger:    memory item stagger
 *   - SPRING_FAST:      filter transitions
 *   - AnimatePresence:  scope/tier tabs
 */
import { useState } from 'react';
import { motion } from 'framer-motion';
import {
  Brain, Search, RefreshCw, Building2, Users, User,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import {
  JARVISPageShell, JARVISStagger, JARVISStaggerItem,
} from '@/components/ui/JARVISPageShell';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { useQuery } from '@tanstack/react-query';
import { apiFetch } from '@/lib/api/client';

// ── Types ──────────────────────────────────────────────────────────────────

interface MemoryItem {
  id: string;
  content: string;
  scope: 'org' | 'department' | 'team' | 'agent';
  scope_id: string;
  scope_name?: string;
  memory_type: string;
  confidence: number;
  source?: string;
  tags?: string[];
  created_at: string;
  last_accessed?: string;
  access_count?: number;
}

// ── Tier config ────────────────────────────────────────────────────────────

const SCOPE_CONFIG = {
  org:        { icon: Building2, color: 'text-[#00D4FF]',   label: 'Organization' },
  department: { icon: Building2, color: 'text-indigo-400',  label: 'Department'   },
  team:       { icon: Users,     color: 'text-emerald-400', label: 'Team'         },
  agent:      { icon: User,      color: 'text-purple-400',  label: 'Agent'        },
};

const TYPE_COLORS: Record<string, string> = {
  semantic:   'border-blue-500/30 text-blue-400',
  episodic:   'border-purple-500/30 text-purple-400',
  procedural: 'border-emerald-500/30 text-emerald-400',
  decision:   'border-yellow-500/30 text-yellow-400',
  preference: 'border-pink-500/30 text-pink-400',
};

// ── API hook ───────────────────────────────────────────────────────────────

function useOrgMemory(orgId: string, scope: string, deptId?: string) {
  const url = deptId
    ? `/v1/org/${orgId}/memory/departments/${deptId}`
    : `/v1/org/${orgId}/memory`;
  return useQuery({
    queryKey: ['org-memory', orgId, scope, deptId],
    queryFn: () =>
      apiFetch<any>(url)
        .then(r => (Array.isArray(r) ? r : r?.data ?? []))
        .catch(() => [] as MemoryItem[]),
    staleTime: 30_000,
  });
}

// ── Memory card ────────────────────────────────────────────────────────────

function MemoryCard({ item }: { item: MemoryItem }) {
  const scopeConf = SCOPE_CONFIG[item.scope] ?? SCOPE_CONFIG.org;
  const ScopeIcon = scopeConf.icon;
  const typeClass = TYPE_COLORS[item.memory_type] ?? 'border-[#1E2535] text-[#475569]';

  return (
    <motion.div
      layout
      className="bg-[#0F1623] border border-[#1E2535] rounded-xl p-3 hover:border-[#00D4FF]/20 hover:shadow-glow-electric transition-all group"
      role="article"
      aria-label={`Memory: ${item.content.slice(0, 60)}`}
    >
      <div className="flex items-start gap-2.5">
        <div className="p-1.5 rounded-lg bg-[#1A1F2E] flex-shrink-0 mt-0.5">
          <Brain className="h-3.5 w-3.5 text-[#475569]" aria-hidden />
        </div>
        <div className="flex-1 min-w-0">
          {/* Content */}
          <p className="text-xs text-[#94A3B8] leading-relaxed mb-2 line-clamp-3">{item.content}</p>

          {/* Meta row */}
          <div className="flex items-center flex-wrap gap-1.5">
            <div className="flex items-center gap-1">
              <ScopeIcon className={cn('h-3 w-3', scopeConf.color)} aria-hidden />
              <span className={cn('text-[10px]', scopeConf.color)}>
                {item.scope_name ?? scopeConf.label}
              </span>
            </div>

            <Badge variant="outline" className={cn('text-[10px] capitalize', typeClass)}>
              {item.memory_type}
            </Badge>

            {/* Confidence bar */}
            <div className="flex items-center gap-1 ml-auto">
              <div className="h-1 w-16 rounded-full bg-[#1E2535] overflow-hidden" aria-label={`Confidence: ${Math.round(item.confidence * 100)}%`}>
                <div
                  className={cn(
                    'h-full rounded-full',
                    item.confidence >= 0.8 ? 'bg-emerald-400' :
                    item.confidence >= 0.6 ? 'bg-yellow-400' : 'bg-red-400',
                  )}
                  style={{ width: `${item.confidence * 100}%` }}
                />
              </div>
              <span className="text-[10px] text-[#475569]">{Math.round(item.confidence * 100)}%</span>
            </div>
          </div>

          {/* Tags */}
          {item.tags && item.tags.length > 0 && (
            <div className="flex flex-wrap gap-1 mt-1.5">
              {item.tags.slice(0, 4).map(tag => (
                <span key={tag} className="text-[10px] text-[#475569] bg-[#1A1F2E] px-1.5 py-0.5 rounded">
                  {tag}
                </span>
              ))}
            </div>
          )}
        </div>
      </div>
    </motion.div>
  );
}

// ── Main ───────────────────────────────────────────────────────────────────

interface MemoryBrowserProps {
  orgId: string;
  className?: string;
}

export function MemoryBrowser({ orgId, className }: MemoryBrowserProps) {
  const [scope, setScope] = useState<'org' | 'department' | 'team' | 'agent'>('org');
  const [search, setSearch] = useState('');
  const [typeFilter, setTypeFilter] = useState('all');

  const { data: rawMemory, isLoading, refetch, isFetching } = useOrgMemory(orgId, scope);
  const items: MemoryItem[] = rawMemory ?? [];

  const filtered = items.filter(item => {
    const matchesSearch = !search || item.content.toLowerCase().includes(search.toLowerCase());
    const matchesType   = typeFilter === 'all' || item.memory_type === typeFilter;
    return matchesSearch && matchesType;
  });

  const SCOPES = [
    { id: 'org',        label: 'Organization' },
    { id: 'department', label: 'Departments'  },
    { id: 'team',       label: 'Teams'        },
    { id: 'agent',      label: 'Agents'       },
  ] as const;

  const types = ['all', ...Array.from(new Set(items.map(i => i.memory_type)))];

  return (
    <JARVISPageShell className={cn('flex flex-col gap-4', className)}>
      {/* Header */}
      <div className="flex items-center justify-between shrink-0">
        <h2 className="text-base font-semibold text-[#F1F5F9] flex items-center gap-2">
          <Brain className="h-4 w-4 text-[#00D4FF]" aria-hidden />
          Memory Browser
        </h2>
        <button
          onClick={() => refetch()}
          disabled={isFetching}
          aria-label="Refresh memory"
          style={{ touchAction: 'manipulation' }}
          className="p-2 rounded-lg text-[#475569] hover:text-[#94A3B8] hover:bg-[#1A1F2E] transition-colors min-w-[44px] min-h-[44px] flex items-center justify-center focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00D4FF]/50"
        >
          <RefreshCw className={cn('h-4 w-4', isFetching && 'animate-spin')} aria-hidden />
        </button>
      </div>

      {/* Scope tabs */}
      <div
        className="flex gap-1 shrink-0"
        role="tablist"
        aria-label="Memory scope"
      >
        {SCOPES.map(s => (
          <button
            key={s.id}
            role="tab"
            aria-selected={scope === s.id}
            onClick={() => setScope(s.id)}
            style={{ touchAction: 'manipulation' }}
            className={cn(
              'px-3 py-1.5 rounded-lg text-xs font-medium transition-colors',
              'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00D4FF]/50',
              scope === s.id
                ? 'bg-[#00D4FF]/10 text-[#00D4FF]'
                : 'text-[#475569] hover:text-[#94A3B8] hover:bg-[#1A1F2E]',
            )}
          >
            {s.label}
          </button>
        ))}
      </div>

      {/* Search + type filter */}
      <div className="flex gap-2 shrink-0">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-[#475569]" aria-hidden />
          <Input
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Search memory…"
            className="pl-9 bg-[#0F1623] border-[#1E2535] text-[#F1F5F9] placeholder:text-[#475569] text-xs h-9"
            aria-label="Search memory content"
          />
        </div>
        <select
          value={typeFilter}
          onChange={e => setTypeFilter(e.target.value)}
          className="bg-[#0F1623] border border-[#1E2535] text-[#94A3B8] text-xs rounded-lg px-2 h-9 focus:outline-none focus:ring-2 focus:ring-[#00D4FF]/50"
          aria-label="Filter by memory type"
        >
          {types.map(t => (
            <option key={t} value={t}>{t === 'all' ? 'All types' : t}</option>
          ))}
        </select>
      </div>

      {/* Count */}
      <p className="text-[11px] text-[#475569] shrink-0" aria-live="polite">
        {filtered.length} item{filtered.length !== 1 ? 's' : ''}
        {search && ` matching "${search}"`}
      </p>

      {/* Items */}
      <div className="flex-1 overflow-y-auto">
        {isLoading ? (
          <div className="space-y-2">
            {[1, 2, 3, 4].map(i => (
              <div key={i} className="h-20 rounded-xl bg-[#0F1623] animate-pulse" aria-hidden />
            ))}
          </div>
        ) : filtered.length === 0 ? (
          <div className="flex flex-col items-center py-12 gap-3">
            <Brain className="h-10 w-10 text-[#1E2535]" aria-hidden />
            <p className="text-[#475569] text-sm">
              {search ? 'No matching memory items.' : 'No memory items in this scope.'}
            </p>
          </div>
        ) : (
          <JARVISStagger className="space-y-2">
            {filtered.map(item => (
              <JARVISStaggerItem key={item.id}>
                <MemoryCard item={item} />
              </JARVISStaggerItem>
            ))}
          </JARVISStagger>
        )}
      </div>
    </JARVISPageShell>
  );
}

export default MemoryBrowser;
