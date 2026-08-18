/**
 * DiscoveryPanel — agent/template/connector discovery with search, filters,
 * category chips, and animated result cards.
 *
 * Skills: frontend-design, emil-design-eng, impeccable-ui, web-guidelines
 */
import { useState, useId } from 'react';
import { motion } from 'framer-motion';
import { Search, Sparkles, Bot, Plug, LayoutTemplate, X } from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { apiRequest } from '@/lib/api/client';
import { JARVISStagger, JARVISStaggerItem, SPRING_FAST } from '@/components/ui/JARVISPageShell';
import { EmptyState } from '@/components/ui/EmptyState';

export type DiscoveryCategory = 'agents' | 'templates' | 'connectors' | 'all';

interface DiscoveryItem {
  id: string;
  name: string;
  description: string;
  category: DiscoveryCategory;
  tags: string[];
  popularity?: number;
}

interface DiscoveryPanelProps {
  onSelect?: (item: DiscoveryItem) => void;
  defaultCategory?: DiscoveryCategory;
  className?: string;
}

const CATEGORY_CONFIG = {
  all:        { label: 'All',        icon: Sparkles, color: '#00D4FF' },
  agents:     { label: 'Agents',     icon: Bot,      color: '#6366F1' },
  templates:  { label: 'Templates',  icon: LayoutTemplate, color: '#FFB300' },
  connectors: { label: 'Connectors', icon: Plug,     color: '#00E676' },
} as const;

function useDiscovery(search: string, category: DiscoveryCategory) {
  return useQuery<DiscoveryItem[]>({
    queryKey: ['discovery', search, category],
    queryFn: () => apiRequest<DiscoveryItem[]>('GET', `/v1/marketplace/search?q=${encodeURIComponent(search)}&category=${category}`),
    staleTime: 60_000,
    placeholderData: [],
  });
}

export function DiscoveryPanel({ onSelect, defaultCategory = 'all', className = '' }: DiscoveryPanelProps) {
  const labelId = useId();
  const [search, setSearch] = useState('');
  const [category, setCategory] = useState<DiscoveryCategory>(defaultCategory);
  const [activeTag, setActiveTag] = useState<string | null>(null);

  const { data: items = [], isLoading } = useDiscovery(search, category);

  const filteredItems = activeTag
    ? items.filter(i => i.tags.includes(activeTag))
    : items;

  const allTags = [...new Set(items.flatMap(i => i.tags))].slice(0, 12);

  return (
    <div className={`flex flex-col bg-[#0F1826] border border-white/[0.08] rounded-xl overflow-hidden ${className}`} role="region" aria-labelledby={labelId}>
      {/* Header */}
      <div className="px-4 pt-4 pb-3 border-b border-white/[0.06]">
        <p id={labelId} className="text-[13px] font-semibold text-[#F0F6FF] flex items-center gap-2 mb-3">
          <Search className="h-4 w-4 text-[#00D4FF]" aria-hidden />
          Discover
        </p>

        {/* Search input */}
        <div className="relative mb-3">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-[#5A7494]" aria-hidden />
          <input
            type="search"
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Search agents, templates, connectors…"
            aria-label="Discovery search"
            className="w-full pl-8 pr-3 py-2 bg-white/[0.04] border border-white/[0.08] rounded-lg text-[12px] text-[#F0F6FF] placeholder:text-[#5A7494] focus:outline-none focus:border-[#00D4FF]/40"
          />
        </div>

        {/* Category tabs */}
        <div className="flex gap-1.5" role="tablist" aria-label="Category filter">
          {(Object.entries(CATEGORY_CONFIG) as [DiscoveryCategory, typeof CATEGORY_CONFIG['all']][]).map(([cat, cfg]) => {
            const Icon = cfg.icon;
            const isActive = category === cat;
            return (
              <motion.button
                key={cat}
                whileTap={{ scale: 0.95 }}
                transition={SPRING_FAST}
                role="tab"
                aria-selected={isActive}
                onClick={() => setCategory(cat)}
                className={[
                  'flex items-center gap-1 px-2.5 py-1 rounded-lg text-[11px] font-medium',
                  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00D4FF]/60',
                  isActive
                    ? 'text-white border'
                    : 'text-[#5A7494] hover:text-[#A0B4CC] hover:bg-white/[0.04]',
                ].join(' ')}
                style={isActive ? { color: cfg.color, background: `${cfg.color}22`, borderColor: `${cfg.color}60` } : {}}
              >
                <Icon size={11} aria-hidden />
                {cfg.label}
              </motion.button>
            );
          })}
        </div>
      </div>

      {/* Tag filter chips */}
      {allTags.length > 0 && (
        <div className="px-4 py-2 flex gap-1.5 overflow-x-auto scrollbar-none border-b border-white/[0.06]">
          {activeTag && (
            <button
              onClick={() => setActiveTag(null)}
              className="flex items-center gap-0.5 px-2 py-0.5 rounded bg-[#00D4FF]/20 text-[#00D4FF] text-[10px] font-medium shrink-0"
              aria-label="Clear tag filter"
            >
              <X size={9} aria-hidden />
              Clear
            </button>
          )}
          {allTags.map(tag => (
            <button
              key={tag}
              onClick={() => setActiveTag(activeTag === tag ? null : tag)}
              aria-pressed={activeTag === tag}
              className={`px-2 py-0.5 rounded text-[10px] font-medium shrink-0 transition-colors ${activeTag === tag ? 'bg-[#00D4FF]/20 text-[#00D4FF]' : 'bg-white/[0.06] text-[#A0B4CC] hover:bg-white/[0.10]'}`}
            >
              {tag}
            </button>
          ))}
        </div>
      )}

      {/* Results */}
      <div className="flex-1 overflow-y-auto min-h-0 p-4">
        {isLoading ? (
          <div className="flex flex-col gap-2">
            {[1,2,3].map(i => <div key={i} className="h-16 rounded-lg bg-white/[0.04] animate-pulse" />)}
          </div>
        ) : filteredItems.length === 0 ? (
          <EmptyState
            icon={<Search size={32} />}
            title="No results found"
            description={search ? `No matches for "${search}"` : 'Try a different category'}
            variant="float"
          />
        ) : (
          <JARVISStagger className="flex flex-col gap-2">
            {filteredItems.map(item => {
              const catCfg = CATEGORY_CONFIG[item.category as keyof typeof CATEGORY_CONFIG] ?? CATEGORY_CONFIG.all;
              const CatIcon = catCfg.icon;
              return (
                <JARVISStaggerItem key={item.id} interactive>
                  <button
                    onClick={() => onSelect?.(item)}
                    className="w-full text-left flex items-start gap-3 p-3 rounded-xl bg-white/[0.03] hover:bg-white/[0.07] border border-white/[0.06] hover:border-[rgba(0,212,255,0.20)] transition-[background,border] cursor-pointer"
                    aria-label={`Select ${item.name}`}
                  >
                    <div className="w-8 h-8 rounded-lg flex items-center justify-center shrink-0 mt-0.5" style={{ background: `${catCfg.color}22` }}>
                      <CatIcon size={14} style={{ color: catCfg.color }} aria-hidden />
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="text-[13px] font-semibold text-[#F0F6FF] truncate">{item.name}</p>
                      <p className="text-[11px] text-[#5A7494] mt-0.5 line-clamp-2">{item.description}</p>
                      {item.tags.length > 0 && (
                        <div className="flex flex-wrap gap-1 mt-1.5">
                          {item.tags.slice(0, 3).map(tag => (
                            <span key={tag} className="px-1.5 py-0.5 rounded bg-white/[0.06] text-[9px] text-[#5A7494]">{tag}</span>
                          ))}
                        </div>
                      )}
                    </div>
                    {item.popularity !== undefined && (
                      <span className="text-[10px] text-[#5A7494] tabular-nums shrink-0">{item.popularity}★</span>
                    )}
                  </button>
                </JARVISStaggerItem>
              );
            })}
          </JARVISStagger>
        )}
      </div>

      <div aria-live="polite" aria-atomic="true" className="sr-only">
        {!isLoading && `${filteredItems.length} results for ${category}`}
      </div>
    </div>
  );
}
