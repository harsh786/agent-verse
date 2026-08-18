/**
 * MemoryBrowser — paginated browser for an agent's long-term and episodic memory.
 * Shows memory entries with source, type, recency, and allows search + delete.
 *
 * Skills: frontend-design, emil-design-eng, impeccable-ui, web-guidelines
 */
import { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Brain, Search, Trash2, Clock, Tag, ChevronDown, ChevronUp } from 'lucide-react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiRequest } from '@/lib/api/client';
import { JARVISStagger, JARVISStaggerItem, SPRING_FAST } from '@/components/ui/JARVISPageShell';
import { EmptyState } from '@/components/ui/EmptyState';
import { StatusOrb } from '@/components/ui/StatusOrb';

interface MemoryEntry {
  id: string;
  content: string;
  source: 'execution' | 'user' | 'reflection' | 'knowledge';
  type: 'fact' | 'preference' | 'outcome' | 'context';
  created_at: string;
  relevance?: number;
  agent_id: string;
}

interface MemoryBrowserProps {
  agentId: string;
  className?: string;
}

const SOURCE_COLOR: Record<string, string> = {
  execution: '#00D4FF', user: '#6366F1', reflection: '#FFB300', knowledge: '#00E676',
};
const TYPE_BG: Record<string, string> = {
  fact: 'bg-[#00D4FF]/10 text-[#00D4FF]',
  preference: 'bg-[#6366F1]/10 text-[#6366F1]',
  outcome: 'bg-[#00E676]/10 text-[#00E676]',
  context: 'bg-[#FFB300]/10 text-[#FFB300]',
};

function useAgentMemory(agentId: string, search: string) {
  return useQuery<MemoryEntry[]>({
    queryKey: ['agent-memory', agentId, search],
    queryFn: () => apiRequest<MemoryEntry[]>('GET', `/v1/agents/${agentId}/memory?search=${encodeURIComponent(search)}`),
    staleTime: 30_000,
  });
}

export function MemoryBrowser({ agentId, className = '' }: MemoryBrowserProps) {
  const qc = useQueryClient();
  const [search, setSearch] = useState('');
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const { data: memories = [], isLoading } = useAgentMemory(agentId, search);
  const deleteMemory = useMutation({
    mutationFn: (id: string) => apiRequest('DELETE', `/v1/agents/${agentId}/memory/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['agent-memory', agentId] }),
  });

  return (
    <div className={`bg-[#0F1826] border border-white/[0.08] rounded-xl overflow-hidden ${className}`} role="region" aria-label="Agent memory browser">
      {/* Header */}
      <div className="flex items-center gap-3 px-4 py-3 border-b border-white/[0.06]">
        <Brain className="h-4 w-4 text-[#6366F1]" aria-hidden />
        <span className="text-[13px] font-semibold text-[#F0F6FF]">Memory</span>
        <span className="text-[11px] text-[#5A7494] tabular-nums ml-auto">{memories.length} entries</span>
      </div>

      {/* Search */}
      <div className="px-4 py-3 border-b border-white/[0.06]">
        <label htmlFor="memory-search" className="sr-only">Search memory</label>
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-[#5A7494]" aria-hidden />
          <input
            id="memory-search"
            type="search"
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Search memory…"
            className="w-full pl-8 pr-3 py-2 bg-white/[0.04] border border-white/[0.08] rounded-lg text-[12px] text-[#F0F6FF] placeholder:text-[#5A7494] focus:outline-none focus:border-[#00D4FF]/40 focus:bg-white/[0.06]"
          />
        </div>
      </div>

      {/* Memory list */}
      <div className="overflow-y-auto max-h-96">
        {isLoading ? (
          <div className="flex flex-col gap-2 p-4">
            {[1,2,3].map(i => (
              <div key={i} className="h-16 rounded-lg bg-white/[0.04] animate-pulse" />
            ))}
          </div>
        ) : memories.length === 0 ? (
          <EmptyState
            icon={<Brain size={32} />}
            title="No memory entries"
            description={search ? 'No matches found' : 'Memory is built through goal execution'}
            variant="float"
          />
        ) : (
          <JARVISStagger className="divide-y divide-white/[0.04]">
            {memories.map(mem => (
              <JARVISStaggerItem key={mem.id} interactive>
                <div className="px-4 py-3">
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex items-center gap-2 min-w-0">
                      <StatusOrb status={mem.source === 'execution' ? 'running' : 'idle'} size={6} />
                      <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium ${TYPE_BG[mem.type] ?? 'bg-white/[0.06] text-[#A0B4CC]'}`}>
                        <Tag size={9} aria-hidden />
                        {mem.type}
                      </span>
                      <span className="text-[11px] capitalize" style={{ color: SOURCE_COLOR[mem.source] ?? '#5A7494' }}>
                        {mem.source}
                      </span>
                    </div>
                    <div className="flex items-center gap-1 shrink-0">
                      <button
                        onClick={() => setExpandedId(expandedId === mem.id ? null : mem.id)}
                        aria-label={expandedId === mem.id ? 'Collapse' : 'Expand'}
                        className="p-1 rounded text-[#5A7494] hover:text-[#A0B4CC] hover:bg-white/[0.06]"
                      >
                        {expandedId === mem.id ? <ChevronUp size={13} aria-hidden /> : <ChevronDown size={13} aria-hidden />}
                      </button>
                      <motion.button
                        whileTap={{ scale: 0.9 }}
                        transition={SPRING_FAST}
                        onClick={() => deleteMemory.mutate(mem.id)}
                        aria-label="Delete memory entry"
                        className="p-1 rounded text-[#5A7494] hover:text-[#FF3366] hover:bg-[#FF3366]/10"
                      >
                        <Trash2 size={13} aria-hidden />
                      </motion.button>
                    </div>
                  </div>

                  <p className={`mt-1.5 text-[12px] text-[#A0B4CC] ${expandedId === mem.id ? '' : 'line-clamp-2'}`}>
                    {mem.content}
                  </p>

                  <AnimatePresence>
                    {expandedId === mem.id && (
                      <motion.div
                        initial={{ height: 0, opacity: 0 }}
                        animate={{ height: 'auto', opacity: 1 }}
                        exit={{ height: 0, opacity: 0 }}
                        transition={SPRING_FAST}
                        className="overflow-hidden"
                      >
                        <div className="flex items-center gap-1 mt-2 text-[11px] text-[#5A7494]">
                          <Clock size={10} aria-hidden />
                          <time dateTime={mem.created_at}>
                            {new Intl.DateTimeFormat('en', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(mem.created_at))}
                          </time>
                          {mem.relevance !== undefined && (
                            <span className="ml-2 tabular-nums">relevance: {(mem.relevance * 100).toFixed(0)}%</span>
                          )}
                        </div>
                      </motion.div>
                    )}
                  </AnimatePresence>
                </div>
              </JARVISStaggerItem>
            ))}
          </JARVISStagger>
        )}
      </div>
      <div aria-live="polite" aria-atomic="true" className="sr-only">
        {deleteMemory.isSuccess && 'Memory entry deleted.'}
      </div>
    </div>
  );
}
