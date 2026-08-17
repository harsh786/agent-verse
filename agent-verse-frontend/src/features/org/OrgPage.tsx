/**
 * OrgPage — Full AI Organization OS Command Center.
 *
 * Layout:
 *   Header:   OrgHealthWidget (metrics at a glance)
 *   Left:     MissionsList (live, virtualized) + CreateMissionDrawer
 *   Right:    DepartmentTree + ActivityFeed
 *   Overlay:  MissionDetail panel (slides in from right)
 *
 * Skills:
 *   - frontend-design:  JARVIS dark, glassmorphism header, breathing pulse
 *   - web-guidelines:   skip nav, aria-labels, text-balance, keyboard nav
 *   - emil-design-eng:  page entry spring, panel slide animations
 *   - impeccable-ui:    clear section hierarchy, 3-level visual system
 *   - ui-ux-pro-max:    reduced motion, accessible layout, URL state
 */
import { useState, useCallback } from 'react';
import { useParams } from 'react-router-dom';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import { Building2, Plus, RefreshCw, Zap } from 'lucide-react';
import { cn } from '@/lib/utils';
import { OrgHealthWidget } from './components/OrgHealthWidget';
import { MissionsList }     from './components/MissionsList';
import { MissionDetail }    from './components/MissionDetail';
import { DepartmentTree }   from './components/DepartmentTree';
import { ActivityFeed }     from './components/ActivityFeed';
import { CreateMissionDrawer } from './components/CreateMissionDrawer';
import { useOrganization }  from './hooks/useOrg';
import type { OrgMission }  from './types';

const PAGE_SPRING = { type: 'spring', stiffness: 200, damping: 24 } as const;

export function OrgPage() {
  const { orgId } = useParams<{ orgId: string }>();
  const reduce = useReducedMotion();

  const [selectedMission, setSelectedMission] = useState<string | null>(null);
  const [showCreate, setShowCreate]           = useState(false);
  const [statusFilter, setStatusFilter]       = useState<string | undefined>();

  const { data: org, isLoading: orgLoading, refetch } = useOrganization(orgId ?? null);

  const handleMissionClick = useCallback((mission: OrgMission) => {
    setSelectedMission(prev => prev === mission.id ? null : mission.id);
  }, []);

  const closeMissionDetail = useCallback(() => setSelectedMission(null), []);

  if (!orgId) {
    return (
      <div className="flex items-center justify-center h-full">
        <p className="text-[#94A3B8]">No organization selected.</p>
      </div>
    );
  }

  return (
    <>
      {/* web-guidelines: skip navigation link */}
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-[9999] focus:px-4 focus:py-2 focus:bg-blue-600 focus:text-white focus:rounded-lg"
      >
        Skip to main content
      </a>

      <motion.div
        initial={reduce ? { opacity: 0 } : { opacity: 0, y: 8, filter: 'blur(4px)' }}
        animate={{ opacity: 1, y: 0, filter: 'blur(0px)' }}
        transition={reduce ? { duration: 0.2 } : PAGE_SPRING}
        className="flex flex-col h-full bg-[#0A0D14] overflow-hidden"
        id="main-content"
      >
        {/* ── Top bar ────────────────────────────────────────────────────── */}
        <header className="flex items-center justify-between px-6 py-4 border-b border-[#1E2535] shrink-0">
          <div className="flex items-center gap-3 min-w-0">
            <div className="relative">
              <div className="h-8 w-8 rounded-lg bg-blue-500/10 border border-blue-500/20 flex items-center justify-center">
                <Building2 className="h-4 w-4 text-blue-400" aria-hidden />
              </div>
              {/* Active pulse (frontend-design signature) */}
              <span className="absolute -top-0.5 -right-0.5 h-2 w-2 rounded-full bg-emerald-400 animate-pulse-glow" aria-hidden />
            </div>
            <div className="min-w-0">
              <h1
                className="text-[15px] font-semibold text-[#F1F5F9] tracking-[-0.01em] truncate [text-wrap:balance]"
                aria-live="polite"
              >
                {orgLoading ? 'Loading…' : (org?.name ?? 'Organization')}
              </h1>
              <p className="text-[11px] text-[#475569] capitalize">{org?.status ?? ''}</p>
            </div>
          </div>

          <div className="flex items-center gap-2">
            {/* Refresh */}
            <button
              onClick={() => refetch()}
              aria-label="Refresh organization data"
              style={{ touchAction: 'manipulation' }}
              className={cn(
                'p-2 rounded-lg text-[#475569] hover:text-[#94A3B8] hover:bg-[#1A1F2E]',
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60',
                'transition-colors duration-150',
                'min-w-[44px] min-h-[44px] flex items-center justify-center',
              )}
            >
              <RefreshCw className="h-4 w-4" aria-hidden />
            </button>

            {/* New Mission CTA */}
            <button
              onClick={() => setShowCreate(true)}
              aria-label="Create new mission"
              style={{ touchAction: 'manipulation' }}
              className={cn(
                'flex items-center gap-1.5 px-3 py-2 rounded-lg',
                'bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium',
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400',
                'active:scale-[0.97] transition-[background-color,transform] duration-150',
                'min-h-[44px]',
              )}
            >
              <Plus className="h-4 w-4" aria-hidden />
              <span className="hidden sm:inline">New Mission</span>
            </button>
          </div>
        </header>

        {/* ── Health metrics strip ────────────────────────────────────────── */}
        <div className="px-6 py-3 border-b border-[#1E2535] shrink-0">
          <OrgHealthWidget orgId={orgId} />
        </div>

        {/* ── Main content area ───────────────────────────────────────────── */}
        <div className="flex-1 flex overflow-hidden">

          {/* Left: Missions */}
          <main
            className="flex-1 flex flex-col overflow-hidden border-r border-[#1E2535]"
            aria-label="Missions panel"
          >
            {/* Filter tabs */}
            <StatusFilterBar value={statusFilter} onChange={setStatusFilter} />

            {/* Virtualized missions list */}
            <div className="flex-1 overflow-hidden p-4">
              <MissionsList
                orgId={orgId}
                statusFilter={statusFilter}
                onMissionClick={handleMissionClick}
                onCreateClick={() => setShowCreate(true)}
              />
            </div>
          </main>

          {/* Right sidebar: Dept tree + Activity */}
          <aside
            className="w-72 shrink-0 hidden lg:flex flex-col overflow-y-auto"
            aria-label="Organisation sidebar"
          >
            {/* Department tree */}
            <section className="p-4 border-b border-[#1E2535]">
              <h2 className="text-[11px] font-medium uppercase tracking-[0.08em] text-[#475569] mb-3 flex items-center gap-1.5">
                <Zap className="h-3 w-3" aria-hidden />
                Departments
              </h2>
              <DepartmentTree orgId={orgId} />
            </section>

            {/* Activity feed */}
            <section className="flex-1 p-4 overflow-y-auto">
              <ActivityFeed orgId={orgId} />
            </section>
          </aside>
        </div>
      </motion.div>

      {/* ── Mission detail panel ────────────────────────────────────────── */}
      <AnimatePresence>
        {selectedMission && (
          <MissionDetail
            key={selectedMission}
            orgId={orgId}
            missionId={selectedMission}
            onClose={closeMissionDetail}
          />
        )}
      </AnimatePresence>

      {/* ── Create mission drawer ───────────────────────────────────────── */}
      <CreateMissionDrawer
        orgId={orgId}
        open={showCreate}
        onClose={() => setShowCreate(false)}
      />
    </>
  );
}

// ── Status filter bar ──────────────────────────────────────────────────────────

const STATUS_TABS = [
  { value: undefined,    label: 'All' },
  { value: 'active',     label: 'Active' },
  { value: 'queued',     label: 'Queued' },
  { value: 'completed',  label: 'Done' },
  { value: 'failed',     label: 'Failed' },
] as const;

function StatusFilterBar({
  value, onChange,
}: { value: string | undefined; onChange: (v: string | undefined) => void }) {
  return (
    <div
      role="tablist"
      aria-label="Filter missions by status"
      className="flex items-center gap-1 px-4 pt-3 pb-0 overflow-x-auto scrollbar-none shrink-0"
    >
      {STATUS_TABS.map(tab => (
        <button
          key={tab.label}
          role="tab"
          aria-selected={value === tab.value}
          onClick={() => onChange(tab.value as string | undefined)}
          style={{ touchAction: 'manipulation' }}
          className={cn(
            'px-3 py-1.5 rounded-lg text-[12px] font-medium whitespace-nowrap',
            'transition-[background-color,color] duration-150',
            'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60',
            'min-h-[32px]',
            value === tab.value
              ? 'bg-blue-500/10 text-blue-300 ring-1 ring-blue-500/30'
              : 'text-[#94A3B8] hover:text-[#F1F5F9] hover:bg-[#1A1F2E]',
          )}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}
