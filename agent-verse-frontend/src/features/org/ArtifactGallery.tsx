/**
 * ArtifactGallery — versioned mission outputs with approval workflow.
 *
 * Displays: documents, code, data, reports, plans, contracts, analyses
 * Features: filter by kind, status, search; version history; approve/reject
 */
import { useState, useMemo } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  FileText, Code2, Database, BarChart3, ClipboardList,
  FileCheck, Microscope, Search, CheckCircle2,
  XCircle, Clock, Eye, Download,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { apiFetch } from '@/lib/api/client';
import { useQuery } from '@tanstack/react-query';

// ── Types ──────────────────────────────────────────────────────────────────

interface Artifact {
  id: string;
  title: string;
  kind: string;
  status: string;
  version: number;
  agent_id?: string;
  model_id?: string;
  quality_score?: number;
  tags?: string[];
  created_at: string;
  updated_at: string;
  approved_by?: string;
  content?: string;
  content_url?: string;
}

// ── Kind config ────────────────────────────────────────────────────────────

const KIND_CONFIG: Record<string, { label: string; icon: React.ElementType; color: string }> = {
  document:     { label: 'Document',     icon: FileText,      color: 'text-blue-400' },
  code:         { label: 'Code',         icon: Code2,          color: 'text-emerald-400' },
  data:         { label: 'Data',         icon: Database,       color: 'text-purple-400' },
  report:       { label: 'Report',       icon: BarChart3,      color: 'text-indigo-400' },
  plan:         { label: 'Plan',         icon: ClipboardList,  color: 'text-yellow-400' },
  contract:     { label: 'Contract',     icon: FileCheck,      color: 'text-orange-400' },
  analysis:     { label: 'Analysis',     icon: Microscope,     color: 'text-pink-400' },
  presentation: { label: 'Presentation', icon: BarChart3,      color: 'text-cyan-400' },
};

const STATUS_CONFIG: Record<string, { label: string; icon: React.ElementType; class: string }> = {
  draft:        { label: 'Draft',        icon: Clock,         class: 'text-[var(--text-muted)]' },
  under_review: { label: 'In Review',    icon: Eye,           class: 'text-yellow-400' },
  approved:     { label: 'Approved',     icon: CheckCircle2,  class: 'text-emerald-400' },
  rejected:     { label: 'Rejected',     icon: XCircle,       class: 'text-red-400' },
  archived:     { label: 'Archived',     icon: Clock,         class: 'text-[var(--text-muted)]' },
};

const KIND_FILTER_OPTIONS = ['all', ...Object.keys(KIND_CONFIG)];
const STATUS_FILTER_OPTIONS = ['all', ...Object.keys(STATUS_CONFIG)];

// ── API hook ───────────────────────────────────────────────────────────────

function useArtifacts(orgId: string, missionId?: string) {
  const qs = missionId ? `?mission_id=${missionId}` : '';
  return useQuery({
    queryKey: ['artifacts', orgId, missionId],
    queryFn: () =>
      apiFetch<{ data: Artifact[] }>(`/v1/org/${orgId}/artifacts${qs}`)
        .then(r => (Array.isArray(r) ? r : r?.data ?? []))
        .catch(() => [] as Artifact[]),
    staleTime: 30_000,
  });
}

// ── Artifact card ──────────────────────────────────────────────────────────

function ArtifactCard({ artifact }: { artifact: Artifact }) {
  const kindConf = KIND_CONFIG[artifact.kind] ?? KIND_CONFIG.document;
  const statusConf = STATUS_CONFIG[artifact.status] ?? STATUS_CONFIG.draft;
  const KindIcon = kindConf.icon;
  const StatusIcon = statusConf.icon;

  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0 }}
      className="bg-[var(--bg-card)] border border-[var(--border)] rounded-xl p-4 hover:border-[var(--accent-blue)]/30 hover:shadow-glow-electric transition-all group"
      role="article"
      aria-label={`Artifact: ${artifact.title}`}
    >
      <div className="flex items-start gap-3">
        <div className={`p-2 rounded-lg bg-[var(--bg-surface)] flex-shrink-0 ${kindConf.color}`}>
          <KindIcon className="h-4 w-4" aria-hidden="true" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-start justify-between gap-2">
            <h3 className="text-sm font-medium text-[var(--text-primary)] truncate">
              {artifact.title}
            </h3>
            <div className="flex items-center gap-1 flex-shrink-0">
              <StatusIcon className={`h-3.5 w-3.5 ${statusConf.class}`} aria-hidden="true" />
              <span className={`text-[10px] ${statusConf.class}`}>{statusConf.label}</span>
            </div>
          </div>

          <div className="flex items-center gap-2 mt-1.5">
            <Badge variant="outline" className={`text-[10px] px-1.5 py-0 ${kindConf.color}`}>
              {kindConf.label}
            </Badge>
            <span className="text-[10px] text-[var(--text-muted)]">v{artifact.version}</span>
            {artifact.quality_score !== undefined && (
              <span className="text-[10px] text-[var(--text-muted)]">
                Quality: {Math.round(artifact.quality_score * 100)}%
              </span>
            )}
          </div>

          {artifact.tags && artifact.tags.length > 0 && (
            <div className="flex flex-wrap gap-1 mt-2">
              {artifact.tags.slice(0, 3).map(tag => (
                <Badge key={tag} variant="secondary" className="text-[9px] px-1 py-0">
                  {tag}
                </Badge>
              ))}
            </div>
          )}

          <div className="flex items-center justify-between mt-3 pt-2 border-t border-[var(--border)]">
            <span className="text-[10px] text-[var(--text-muted)]">
              {new Date(artifact.created_at).toLocaleDateString()}
            </span>
            <div className="flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
              <Button variant="ghost" size="icon" className="h-6 w-6" aria-label="View artifact">
                <Eye className="h-3 w-3" />
              </Button>
              {artifact.content_url && (
                <Button
                  variant="ghost" size="icon" className="h-6 w-6"
                  onClick={() => window.open(artifact.content_url!, '_blank')}
                  aria-label="Download artifact"
                >
                  <Download className="h-3 w-3" />
                </Button>
              )}
            </div>
          </div>
        </div>
      </div>
    </motion.div>
  );
}

// ── Main component ─────────────────────────────────────────────────────────

interface ArtifactGalleryProps {
  orgId: string;
  missionId?: string;
}

export function ArtifactGallery({ orgId, missionId }: ArtifactGalleryProps) {
  const { data: artifacts = [], isLoading } = useArtifacts(orgId, missionId);
  const [search, setSearch] = useState('');
  const [kindFilter, setKindFilter] = useState('all');
  const [statusFilter, setStatusFilter] = useState('all');

  const filtered = useMemo(() => {
    let result = artifacts as Artifact[];
    if (search) result = result.filter(a => a.title.toLowerCase().includes(search.toLowerCase()));
    if (kindFilter !== 'all') result = result.filter(a => a.kind === kindFilter);
    if (statusFilter !== 'all') result = result.filter(a => a.status === statusFilter);
    return result.sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime());
  }, [artifacts, search, kindFilter, statusFilter]);

  return (
    <div className="space-y-4" role="region" aria-label="Artifact gallery">
      {/* Toolbar */}
      <div className="flex items-center gap-3 flex-wrap">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-2.5 top-2.5 h-3.5 w-3.5 text-[var(--text-muted)]" aria-hidden="true" />
          <Input
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Search artifacts…"
            className="pl-8 h-8 text-xs bg-[var(--bg-card)] border-[var(--border)]"
            aria-label="Search artifacts"
          />
        </div>

        <select
          value={kindFilter}
          onChange={e => setKindFilter(e.target.value)}
          className="h-8 text-xs bg-[var(--bg-card)] border border-[var(--border)] rounded-md px-2 text-[var(--text-primary)] outline-none"
          aria-label="Filter by kind"
        >
          {KIND_FILTER_OPTIONS.map(k => (
            <option key={k} value={k}>{k === 'all' ? 'All kinds' : KIND_CONFIG[k]?.label ?? k}</option>
          ))}
        </select>

        <select
          value={statusFilter}
          onChange={e => setStatusFilter(e.target.value)}
          className="h-8 text-xs bg-[var(--bg-card)] border border-[var(--border)] rounded-md px-2 text-[var(--text-primary)] outline-none"
          aria-label="Filter by status"
        >
          {STATUS_FILTER_OPTIONS.map(s => (
            <option key={s} value={s}>{s === 'all' ? 'All statuses' : STATUS_CONFIG[s]?.label ?? s}</option>
          ))}
        </select>

        <span className="text-xs text-[var(--text-muted)] ml-auto">
          {filtered.length} artifact{filtered.length !== 1 ? 's' : ''}
        </span>
      </div>

      {/* Grid */}
      {isLoading ? (
        <div className="flex items-center justify-center h-32" aria-live="polite">
          <span className="text-sm text-[var(--text-muted)]">Loading artifacts…</span>
        </div>
      ) : filtered.length === 0 ? (
        <div className="flex flex-col items-center justify-center h-32 border border-dashed border-[var(--border)] rounded-xl">
          <FileText className="h-8 w-8 text-[var(--text-muted)] mb-2" aria-hidden="true" />
          <p className="text-sm text-[var(--text-muted)]">No artifacts yet</p>
          <p className="text-xs text-[var(--text-muted)]">Agents create artifacts as they complete tasks</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          <AnimatePresence>
            {filtered.map(artifact => (
              <ArtifactCard key={artifact.id} artifact={artifact} />
            ))}
          </AnimatePresence>
        </div>
      )}
    </div>
  );
}
