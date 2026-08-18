/**
 * WorkflowMarketplacePage — template gallery with search, categories, fork.
 */
import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { motion, AnimatePresence } from 'framer-motion';
import { Search, GitFork, Loader2, LayoutTemplate, Tag } from 'lucide-react';
import { workflowEngineApi } from '../../lib/api/client';
import { nodeBounce, emptyStateFade } from './design/motion';
import { JARVISPageShell } from '@/components/ui/JARVISPageShell';
import { JARVISStagger } from '@/components/ui/JARVISPageShell';

const COMPLEXITY_COLORS: Record<string, string> = {
  simple:  'text-emerald-400 bg-emerald-500/15 border-emerald-500/30',
  medium:  'text-amber-400 bg-amber-500/15 border-amber-500/30',
  complex: 'text-red-400 bg-red-500/15 border-red-500/30',
};

interface Template {
  slug: string;
  name: string;
  description: string;
  category: string;
  tags: string[];
  complexity: string;
  popularity_score: number;
  definition?: Record<string, unknown>;
}

function TemplateCard({
  template,
  onFork,
  isForking,
}: {
  template: Template;
  onFork: (slug: string) => void;
  isForking: boolean;
}) {
  return (
    <motion.article
      layout
      variants={nodeBounce}
      initial="initial"
      animate="animate"
      exit="exit"
      className="rounded-2xl border border-white/8 bg-[#0F1826]/3 hover:bg-white/5 p-5
                 flex flex-col gap-3 transition-colors"
      role="article"
      aria-label={`Template: ${template.name}`}
    >
      {/* Header */}
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          <h3 className="text-white font-semibold text-sm leading-tight truncate">
            {template.name}
          </h3>
          <p className="text-xs text-white/40 mt-0.5">{template.category}</p>
        </div>
        <span className={`px-2 py-0.5 rounded-full text-xs font-medium border
          ${COMPLEXITY_COLORS[template.complexity] ?? COMPLEXITY_COLORS.medium}`}>
          {template.complexity}
        </span>
      </div>

      {/* Description */}
      <p className="text-xs text-white/60 leading-relaxed line-clamp-2">
        {template.description}
      </p>

      {/* Tags */}
      {template.tags?.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {template.tags.slice(0, 4).map((tag) => (
            <span key={tag}
              className="px-1.5 py-0.5 rounded bg-[#0F1826]/5 text-white/40 text-xs">
              #{tag}
            </span>
          ))}
        </div>
      )}

      {/* Actions */}
      <div className="flex items-center justify-between mt-auto pt-2 border-t border-white/5">
        <span className="text-xs text-white/30 flex items-center gap-1">
          <Tag className="h-3 w-3" aria-hidden />
          {template.popularity_score > 0 ? `${template.popularity_score} uses` : 'New'}
        </span>
        <button
          onClick={() => onFork(template.slug)}
          disabled={isForking}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-sky-600/80
                     hover:bg-sky-600 text-white text-xs font-medium transition-colors
                     disabled:opacity-50"
          aria-label={`Fork template: ${template.name}`}
        >
          {isForking
            ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
            : <GitFork className="h-3.5 w-3.5" />}
          Use Template
        </button>
      </div>
    </motion.article>
  );
}

export default function WorkflowMarketplacePage() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [search, setSearch] = useState('');
  const [category, setCategory] = useState('');
  const [forkingSlug, setForkingSlug] = useState<string | null>(null);

  const { data: categories } = useQuery({
    queryKey: ['workflow-engine', 'template-categories'],
    queryFn: () => workflowEngineApi.listTemplateCategories(),
    staleTime: 300_000,
  });

  const { data: templates, isLoading } = useQuery({
    queryKey: ['workflow-engine', 'templates', search, category],
    queryFn: () => workflowEngineApi.listTemplates({
      q: search || undefined,
      category: category || undefined,
      per_page: 100,
    }),
    staleTime: 60_000,
  });

  const forkMutation = useMutation({
    mutationFn: (slug: string) => workflowEngineApi.forkTemplate(slug),
    onMutate: (slug) => setForkingSlug(slug),
    onSuccess: (wf) => {
      qc.invalidateQueries({ queryKey: ['workflow-engine', 'list'] });
      navigate(`/workflows/${wf.id}/edit`);
    },
    onSettled: () => setForkingSlug(null),
  });

  return (
    <JARVISPageShell>
    <JARVISStagger className="min-h-screen bg-slate-950 text-white">
      {/* Header */}
      <header className="sticky top-0 z-30 border-b border-white/10 bg-slate-950/90
                          backdrop-blur-xl px-6 py-4">
        <div className="max-w-6xl mx-auto flex items-center justify-between gap-4">
          <div>
            <h1 className="text-lg font-bold flex items-center gap-2">
              <LayoutTemplate className="h-5 w-5 text-sky-400" aria-hidden />
              Template Marketplace
            </h1>
            <p className="text-xs text-white/40 mt-0.5">
              {templates?.total ?? 0} ready-to-use workflow templates
            </p>
          </div>
        </div>
      </header>

      <main className="max-w-6xl mx-auto px-6 py-8">
        {/* Search + filters */}
        <div className="flex flex-wrap gap-3 mb-8 items-start">
          {/* Search */}
          <div className="relative flex-1 min-w-0 max-w-xs">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-white/30" aria-hidden />
            <input
              type="search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search templates…"
              aria-label="Search templates"
              className="w-full pl-9 pr-3 py-2 rounded-xl border border-white/10 bg-[#0F1826]/5
                         text-white placeholder-white/30 text-sm focus:outline-none
                         focus:ring-2 focus:ring-sky-500"
            />
          </div>

          {/* Category pills */}
          <div className="flex flex-wrap gap-1.5">
            <button
              onClick={() => setCategory('')}
              aria-pressed={category === ''}
              className={`px-3 py-1.5 rounded-xl text-xs font-medium transition-colors ${
                category === '' ? 'bg-sky-600 text-white' : 'bg-[#0F1826]/5 text-white/50 hover:text-white hover:bg-white/10'
              }`}
            >
              All
            </button>
            {(categories ?? []).map((c) => (
              <button
                key={c.category}
                onClick={() => setCategory(c.category)}
                aria-pressed={category === c.category}
                className={`px-3 py-1.5 rounded-xl text-xs font-medium transition-colors ${
                  category === c.category ? 'bg-sky-600 text-white' : 'bg-[#0F1826]/5 text-white/50 hover:text-white hover:bg-white/10'
                }`}
              >
                {c.category} <span className="opacity-60">({c.count})</span>
              </button>
            ))}
          </div>
        </div>

        {/* Grid */}
        {isLoading ? (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {Array.from({ length: 9 }).map((_, i) => (
              <div key={i} className="h-48 rounded-2xl bg-[#0F1826]/5 animate-pulse" />
            ))}
          </div>
        ) : (templates?.items ?? []).length === 0 ? (
          <motion.div
            variants={emptyStateFade}
            initial="initial"
            animate="animate"
            className="text-center py-20"
            role="status"
          >
            <Search className="h-12 w-12 mx-auto mb-4 text-white/10" aria-hidden />
            <p className="text-white/40 text-sm">No templates found for "{search}"</p>
          </motion.div>
        ) : (
          <AnimatePresence mode="popLayout">
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4"
              role="list" aria-label="Workflow templates">
              {(templates?.items ?? []).map((t) => (
                <TemplateCard
                  key={(t as Template).slug}
                  template={t as Template}
                  onFork={(slug) => forkMutation.mutate(slug)}
                  isForking={forkingSlug === (t as Template).slug}
                />
              ))}
            </div>
          </AnimatePresence>
        )}
      </main>
    </JARVISStagger>
    </JARVISPageShell>
  );
}
