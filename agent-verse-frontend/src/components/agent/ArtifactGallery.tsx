/**
 * ArtifactGallery — grid/list view of agent-generated artifacts
 * (files, images, code, reports) with preview, download, and filter.
 *
 * Skills: frontend-design, emil-design-eng, impeccable-ui, web-guidelines
 */
import { useState } from 'react';
import { motion } from 'framer-motion';
import { FileText, Image, Code2, Download, ExternalLink, LayoutGrid, List, Search } from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { apiRequest } from '@/lib/api/client';
import { JARVISStagger, JARVISStaggerItem, SPRING_FAST } from '@/components/ui/JARVISPageShell';
import { EmptyState } from '@/components/ui/EmptyState';

type ArtifactType = 'file' | 'image' | 'code' | 'report' | 'data';

interface Artifact {
  id: string;
  name: string;
  type: ArtifactType;
  size_bytes: number;
  goal_id?: string;
  created_at: string;
  url?: string;
  preview?: string;
}

interface ArtifactGalleryProps {
  goalId?: string;
  agentId?: string;
  className?: string;
}

const TYPE_CONFIG: Record<ArtifactType, { icon: typeof FileText; color: string; bg: string }> = {
  file:   { icon: FileText, color: '#A0B4CC', bg: 'rgba(160,180,204,0.12)' },
  image:  { icon: Image,    color: '#6366F1', bg: 'rgba(99,102,241,0.12)' },
  code:   { icon: Code2,    color: '#00D4FF', bg: 'rgba(0,212,255,0.12)' },
  report: { icon: FileText, color: '#FFB300', bg: 'rgba(255,179,0,0.12)' },
  data:   { icon: FileText, color: '#00E676', bg: 'rgba(0,230,118,0.12)' },
};

function formatBytes(b: number): string {
  if (b < 1024) return `${b}B`;
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)}KB`;
  return `${(b / 1024 / 1024).toFixed(1)}MB`;
}

function useArtifacts(goalId?: string, agentId?: string) {
  const params = new URLSearchParams();
  if (goalId) params.set('goal_id', goalId);
  if (agentId) params.set('agent_id', agentId);
  return useQuery<Artifact[]>({
    queryKey: ['artifacts', goalId, agentId],
    queryFn: () => apiRequest<Artifact[]>('GET', `/v1/artifacts?${params}`),
    staleTime: 30_000,
  });
}

export function ArtifactGallery({ goalId, agentId, className = '' }: ArtifactGalleryProps) {
  const [view, setView] = useState<'grid' | 'list'>('grid');
  const [search, setSearch] = useState('');
  const [typeFilter, setTypeFilter] = useState<ArtifactType | 'all'>('all');

  const { data: artifacts = [], isLoading } = useArtifacts(goalId, agentId);

  const filtered = artifacts.filter(a => {
    if (typeFilter !== 'all' && a.type !== typeFilter) return false;
    if (search && !a.name.toLowerCase().includes(search.toLowerCase())) return false;
    return true;
  });

  return (
    <div className={`bg-[#0F1826] border border-white/[0.08] rounded-xl overflow-hidden ${className}`} role="region" aria-label="Artifact gallery">
      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-2 px-4 py-3 border-b border-white/[0.06]">
        <div className="relative flex-1 min-w-[140px]">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-[#5A7494]" aria-hidden />
          <input
            type="search"
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Search artifacts…"
            aria-label="Search artifacts"
            className="w-full pl-7 pr-3 py-1.5 bg-white/[0.04] border border-white/[0.06] rounded-lg text-[11px] text-[#F0F6FF] placeholder:text-[#5A7494] focus:outline-none focus:border-[#00D4FF]/40"
          />
        </div>

        <select
          value={typeFilter}
          onChange={e => setTypeFilter(e.target.value as ArtifactType | 'all')}
          aria-label="Filter by type"
          className="bg-[#0F1826] border border-white/[0.08] rounded-lg text-[11px] text-[#A0B4CC] px-2 py-1.5 focus:outline-none focus:border-[#00D4FF]/40"
        >
          <option value="all">All types</option>
          {(['file','image','code','report','data'] as ArtifactType[]).map(t => (
            <option key={t} value={t}>{t}</option>
          ))}
        </select>

        <div className="flex border border-white/[0.08] rounded-lg overflow-hidden">
          {(['grid','list'] as const).map(v => (
            <motion.button
              key={v}
              whileTap={{ scale: 0.92 }}
              transition={SPRING_FAST}
              onClick={() => setView(v)}
              aria-label={`${v} view`}
              aria-pressed={view === v}
              className={`p-1.5 ${view === v ? 'bg-[#00D4FF]/15 text-[#00D4FF]' : 'text-[#5A7494] hover:text-[#A0B4CC]'}`}
            >
              {v === 'grid' ? <LayoutGrid size={13} aria-hidden /> : <List size={13} aria-hidden />}
            </motion.button>
          ))}
        </div>
      </div>

      {/* Content */}
      <div className="p-4 overflow-y-auto max-h-80">
        {isLoading ? (
          <div className={view === 'grid' ? 'grid grid-cols-3 gap-2' : 'flex flex-col gap-2'}>
            {[1,2,3,4,5,6].map(i => <div key={i} className="h-20 rounded-lg bg-white/[0.04] animate-pulse" />)}
          </div>
        ) : filtered.length === 0 ? (
          <EmptyState
            icon={<FileText size={32} />}
            title="No artifacts yet"
            description={search ? 'No matches found' : 'Artifacts appear after goal execution'}
            variant="float"
          />
        ) : view === 'grid' ? (
          <JARVISStagger className="grid grid-cols-2 sm:grid-cols-3 gap-2">
            {filtered.map(art => {
              const cfg = TYPE_CONFIG[art.type] ?? TYPE_CONFIG.file;
              const Icon = cfg.icon;
              return (
                <JARVISStaggerItem key={art.id} interactive>
                  <div className="flex flex-col items-center gap-2 p-3 rounded-xl bg-white/[0.03] border border-white/[0.06] hover:border-[rgba(0,212,255,0.20)] transition-[border] cursor-pointer group">
                    <div className="w-10 h-10 rounded-xl flex items-center justify-center" style={{ background: cfg.bg }}>
                      <Icon size={18} style={{ color: cfg.color }} aria-hidden />
                    </div>
                    <p className="text-[11px] font-medium text-[#F0F6FF] truncate max-w-full text-center" title={art.name}>{art.name}</p>
                    <p className="text-[10px] text-[#5A7494] tabular-nums">{formatBytes(art.size_bytes)}</p>
                    <div className="flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                      {art.url && (
                        <a href={art.url} download={art.name} aria-label={`Download ${art.name}`} className="p-1 rounded text-[#5A7494] hover:text-[#00D4FF]">
                          <Download size={11} aria-hidden />
                        </a>
                      )}
                      {art.url && (
                        <a href={art.url} target="_blank" rel="noopener noreferrer" aria-label={`Open ${art.name}`} className="p-1 rounded text-[#5A7494] hover:text-[#00D4FF]">
                          <ExternalLink size={11} aria-hidden />
                        </a>
                      )}
                    </div>
                  </div>
                </JARVISStaggerItem>
              );
            })}
          </JARVISStagger>
        ) : (
          <JARVISStagger className="flex flex-col gap-1">
            {filtered.map(art => {
              const cfg = TYPE_CONFIG[art.type] ?? TYPE_CONFIG.file;
              const Icon = cfg.icon;
              return (
                <JARVISStaggerItem key={art.id} interactive>
                  <div className="flex items-center gap-3 px-3 py-2 rounded-lg hover:bg-white/[0.04] cursor-pointer">
                    <div className="w-7 h-7 rounded-lg flex items-center justify-center shrink-0" style={{ background: cfg.bg }}>
                      <Icon size={13} style={{ color: cfg.color }} aria-hidden />
                    </div>
                    <span className="flex-1 text-[12px] text-[#F0F6FF] truncate">{art.name}</span>
                    <span className="text-[11px] text-[#5A7494] tabular-nums">{formatBytes(art.size_bytes)}</span>
                    {art.url && (
                      <a href={art.url} download={art.name} aria-label={`Download ${art.name}`} onClick={e => e.stopPropagation()} className="p-1 rounded text-[#5A7494] hover:text-[#00D4FF]">
                        <Download size={12} aria-hidden />
                      </a>
                    )}
                  </div>
                </JARVISStaggerItem>
              );
            })}
          </JARVISStagger>
        )}
      </div>
    </div>
  );
}
