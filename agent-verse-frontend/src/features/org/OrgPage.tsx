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
import { motion, AnimatePresence } from 'framer-motion';
import { JARVISPageShell } from '@/components/ui/JARVISPageShell';
import { JARVISBootScreen } from '@/components/ui/JARVISBootScreen';
import { Building2, Plus, RefreshCw, Zap, Network, Mic, Plug, Clock, Cpu, Terminal, BookOpen } from 'lucide-react';
import { cn } from '@/lib/utils';
import { OrgHealthWidget }      from './components/OrgHealthWidget';
import { MissionsList }          from './components/MissionsList';
import { MissionDetail }         from './components/MissionDetail';
import { DepartmentTree }        from './components/DepartmentTree';
import { ActivityFeed }          from './components/ActivityFeed';
import { CreateMissionDrawer }   from './components/CreateMissionDrawer';
import { GraphifyProgress }      from './components/GraphifyProgress';
import { VoiceModal }            from './components/VoiceModal';
import { CursorPresence }        from './components/CursorPresence';
import { ConnectorMarketplace }  from './components/ConnectorMarketplace';
import { MorningBrief }          from './components/MorningBrief';
import { NowNextWhy }            from './components/NowNextWhy';
import { OrgHistoryNav }         from './components/OrgHistoryNav';
import { DigitalTwinPanel }      from './components/DigitalTwinPanel';
import { CommandHistoryPanel }   from './components/CommandHistoryPanel';
import { ObsidianVaultExplorer } from './components/ObsidianVaultExplorer';
import { MissionOrbit }           from './components/MissionOrbit';
import { ApprovalCenter }         from './ApprovalCenter';
import { LoginGreetingPlayer }   from '@/components/voice/LoginGreetingPlayer';
import { useVoiceAlerts }        from '@/lib/voice/useVoiceAlerts';
import { useOrganization, useOrgHealth, useMissions } from './hooks/useOrg';
import type { OrgMission }       from './types';

export function OrgPage() {
  const { orgId } = useParams<{ orgId: string }>();

  // D-6: Proactive voice alerts — plays TTS audio when mission fails/approval needed
  useVoiceAlerts({ enabled: !!orgId });

  // JARVIS boot screen — show on every org visit
  const [isBooted, setIsBooted] = useState(false);

  const [selectedMission, setSelectedMission] = useState<string | null>(null);
  const [showCreate, setShowCreate]           = useState(false);
  const [statusFilter, setStatusFilter]       = useState<string | undefined>();
  const [showGraphify, setShowGraphify]       = useState(false);
  const [showVoice, setShowVoice]             = useState(false);
  const [showConnectors, setShowConnectors]   = useState(false);
  const [showTwin, setShowTwin]               = useState(false);
  const [showHistory, setShowHistory]         = useState(false);
  const [showCommands, setShowCommands]       = useState(false);
  const [showObsidian, setShowObsidian]       = useState(false);
  const [showApprovals, setShowApprovals]     = useState(false);

  const { data: org, isLoading: orgLoading, refetch } = useOrganization(orgId ?? null);
  const { data: health }    = useOrgHealth(orgId ?? null);
  // Pending approval count for badge — use after health is declared
  const pendingApprovalCount = (health as any)?.pending_approvals ?? 0;
  const { data: missionInf } = useMissions(orgId, {});
  const activeMissions = (missionInf?.pages?.flatMap(p => p.data ?? []) ?? []).filter((m: OrgMission) => m.status === 'active');

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

  // JARVIS boot screen renders until animation completes
  if (!isBooted) {
    return (
      <JARVISBootScreen
        orgName={org?.name ?? 'AgentVerse OS'}
        onComplete={() => setIsBooted(true)}
        duration={3200}
      />
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

      <JARVISPageShell className="flex flex-col h-full bg-[#0A0D14] overflow-hidden">
        <div id="main-content" className="contents">
        {/* ── Top bar ────────────────────────────────────────────────────── */}
        <header className="flex items-center justify-between px-6 py-4 border-b border-[#1E2535] shrink-0">
          <div className="flex items-center gap-3 min-w-0">
            <div className="relative">
              <div className="h-8 w-8 rounded-lg bg-blue-500/10 border border-blue-500/20 flex items-center justify-center">
                <Building2 className="h-4 w-4 text-[#00D4FF]" aria-hidden />
              </div>
              {/* Active pulse (frontend-design signature) */}
              <span className="absolute -top-0.5 -right-0.5 h-2 w-2 rounded-full bg-emerald-400 animate-pulse-glow" aria-hidden />
            </div>
            {/* D-7/D-2/D-5: Spoken login greeting via OmniVoice TTS */}
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
            {/* Cursor presence — who else is viewing */}
            <CursorPresence orgId={orgId} className="hidden sm:flex" />

            {/* D-7: OmniVoice greeting player — shows animated indicator while playing */}
            {orgId && <LoginGreetingPlayer orgId={orgId} userName={org?.name} className="hidden sm:flex" />}

            {/* Voice input */}
            <button
              onClick={() => setShowVoice(true)}
              aria-label="Voice input"
              style={{ touchAction: 'manipulation' }}
              className={cn(
                'p-2 rounded-lg text-[#475569] hover:text-[#94A3B8] hover:bg-[#1A1F2E]',
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60',
                'transition-colors duration-150 min-w-[44px] min-h-[44px] flex items-center justify-center',
              )}
            >
              <Mic className="h-4 w-4" aria-hidden />
            </button>

            {/* Graphify */}
            <button
              onClick={() => setShowGraphify(v => !v)}
              aria-label="Open knowledge graph builder"
              style={{ touchAction: 'manipulation' }}
              className={cn(
                'p-2 rounded-lg transition-colors duration-150',
                'min-w-[44px] min-h-[44px] flex items-center justify-center',
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60',
                showGraphify
                  ? 'text-violet-300 bg-violet-500/10'
                  : 'text-[#475569] hover:text-[#94A3B8] hover:bg-[#1A1F2E]',
              )}
            >
              <Network className="h-4 w-4" aria-hidden />
            </button>

            {/* Connectors */}
            <button
              onClick={() => setShowConnectors(v => !v)}
              aria-label="Connector marketplace"
              style={{ touchAction: 'manipulation' }}
              className={cn(
                'p-2 rounded-lg transition-colors duration-150',
                'min-w-[44px] min-h-[44px] flex items-center justify-center',
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60',
                showConnectors
                  ? 'text-violet-300 bg-violet-500/10'
                  : 'text-[#475569] hover:text-[#94A3B8] hover:bg-[#1A1F2E]',
              )}
            >
              <Plug className="h-4 w-4" aria-hidden />
            </button>

            {/* Digital Twin */}
            <button
              onClick={() => setShowTwin(v => !v)}
              aria-label="Digital Twin capacity view"
              style={{ touchAction: 'manipulation' }}
              className={cn(
                'p-2 rounded-lg transition-colors duration-150',
                'min-w-[44px] min-h-[44px] flex items-center justify-center',
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60',
                showTwin
                  ? 'text-purple-300 bg-purple-500/10'
                  : 'text-[#475569] hover:text-[#94A3B8] hover:bg-[#1A1F2E]',
              )}
            >
              <Cpu className="h-4 w-4" aria-hidden />
            </button>

            {/* Command History */}
            <button
              onClick={() => setShowCommands(v => !v)}
              aria-label="Command gateway history"
              style={{ touchAction: 'manipulation' }}
              className={cn(
                'p-2 rounded-lg transition-colors duration-150',
                'min-w-[44px] min-h-[44px] flex items-center justify-center',
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60',
                showCommands
                  ? 'text-emerald-300 bg-emerald-500/10'
                  : 'text-[#475569] hover:text-[#94A3B8] hover:bg-[#1A1F2E]',
              )}
            >
              <Terminal className="h-4 w-4" aria-hidden />
            </button>

            {/* History */}
            <button
              onClick={() => setShowHistory(v => !v)}
              aria-label="Organisation history"
              style={{ touchAction: 'manipulation' }}
              className={cn(
                'p-2 rounded-lg transition-colors duration-150',
                'min-w-[44px] min-h-[44px] flex items-center justify-center',
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60',
                showHistory
                  ? 'text-amber-300 bg-amber-500/10'
                  : 'text-[#475569] hover:text-[#94A3B8] hover:bg-[#1A1F2E]',
              )}
            >
              <Clock className="h-4 w-4" aria-hidden />
            </button>

            {/* Obsidian Vault */}
            <button
              onClick={() => setShowObsidian(v => !v)}
              aria-label="Obsidian vault explorer"
              style={{ touchAction: 'manipulation' }}
              className={cn(
                'p-2 rounded-lg transition-colors duration-150',
                'min-w-[44px] min-h-[44px] flex items-center justify-center',
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60',
                showObsidian
                  ? 'text-violet-300 bg-violet-500/10'
                  : 'text-[#475569] hover:text-[#94A3B8] hover:bg-[#1A1F2E]',
              )}
            >
              <BookOpen className="h-4 w-4" aria-hidden />
            </button>

            {/* G-05: Approvals button — amber badge when pending */}
            <button
              onClick={() => setShowApprovals(v => !v)}
              aria-label={`Approvals${pendingApprovalCount > 0 ? ` (${pendingApprovalCount} pending)` : ''}`}
              style={{ touchAction: 'manipulation' }}
              className={cn(
                'relative p-2 rounded-lg transition-colors duration-150',
                'min-w-[44px] min-h-[44px] flex items-center justify-center',
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-500/60',
                showApprovals || pendingApprovalCount > 0
                  ? 'text-amber-400 bg-amber-500/10'
                  : 'text-[#475569] hover:text-[#94A3B8] hover:bg-[#1A1F2E]',
              )}
            >
              <Zap className="h-4 w-4" aria-hidden />
              {pendingApprovalCount > 0 && (
                <span className="absolute -top-0.5 -right-0.5 h-4 w-4 rounded-full bg-amber-500 text-[9px] font-bold text-white flex items-center justify-center">
                  {pendingApprovalCount > 9 ? '9+' : pendingApprovalCount}
                </span>
              )}
            </button>

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

          {/* Right sidebar: Graphify / Connectors / Dept tree + Activity */}
          <aside
            className="w-72 shrink-0 hidden lg:flex flex-col overflow-y-auto"
            aria-label="Organisation sidebar"
          >
            {/* Digital Twin panel */}
            <AnimatePresence mode="wait">
              {showTwin && (
                <motion.div
                  key="twin"
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: 'auto' }}
                  exit={{ opacity: 0, height: 0 }}
                  transition={{ type: 'spring', stiffness: 300, damping: 28 }}
                  style={{ overflow: 'hidden' }}
                  className="border-b border-[#1E2535]"
                >
                  <div className="p-4">
                    <DigitalTwinPanel orgId={orgId} />
                  </div>
                </motion.div>
              )}
            </AnimatePresence>

            {/* Command history panel */}
            <AnimatePresence mode="wait">
              {showCommands && (
                <motion.div
                  key="commands"
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: 'auto' }}
                  exit={{ opacity: 0, height: 0 }}
                  transition={{ type: 'spring', stiffness: 300, damping: 28 }}
                  style={{ overflow: 'hidden' }}
                  className="border-b border-[#1E2535]"
                >
                  <div className="p-4 max-h-96 overflow-y-auto">
                    <CommandHistoryPanel orgId={orgId} />
                  </div>
                </motion.div>
              )}
            </AnimatePresence>

            {/* History nav panel */}
            <AnimatePresence mode="wait">
              {showHistory && (
                <motion.div
                  key="history"
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: 'auto' }}
                  exit={{ opacity: 0, height: 0 }}
                  transition={{ type: 'spring', stiffness: 300, damping: 28 }}
                  style={{ overflow: 'hidden' }}
                  className="border-b border-[#1E2535]"
                >
                  <div className="p-4">
                    <OrgHistoryNav orgId={orgId} compact />
                  </div>
                </motion.div>
              )}
            </AnimatePresence>

            {/* Obsidian Vault Explorer panel */}
            <AnimatePresence mode="wait">
              {showObsidian && (
                <motion.div
                  key="obsidian"
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: 'auto' }}
                  exit={{ opacity: 0, height: 0 }}
                  transition={{ type: 'spring', stiffness: 300, damping: 28 }}
                  style={{ overflow: 'hidden' }}
                  className="border-b border-[#1E2535]"
                >
                  <div className="p-4">
                    <ObsidianVaultExplorer orgId={orgId} compact />
                  </div>
                </motion.div>
              )}
            </AnimatePresence>

            {/* G-05: ApprovalCenter panel — shown when showApprovals or pending > 0 */}
            <AnimatePresence mode="wait">
              {(showApprovals || pendingApprovalCount > 0) && (
                <motion.div
                  key="approvals"
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: 'auto' }}
                  exit={{ opacity: 0, height: 0 }}
                  transition={{ type: 'spring', stiffness: 300, damping: 28 }}
                  style={{ overflow: 'hidden' }}
                  className="border-b border-amber-500/20 bg-amber-500/5"
                >
                  <div className="max-h-96 overflow-y-auto">
                    <ApprovalCenter orgId={orgId} />
                  </div>
                </motion.div>
              )}
            </AnimatePresence>

            {/* ── Mission Orbit — glowing nodes, visible immediately at top ── */}
            {activeMissions.length > 0 && (
              <section
                className="flex flex-col items-center py-4 border-b border-[#1E2535]"
                aria-label="Active mission orbit visualization"
              >
                <p className="text-[10px] font-medium uppercase tracking-[0.1em] text-[#00D4FF]/60 mb-2 flex items-center gap-1.5">
                  <Cpu className="h-2.5 w-2.5" aria-hidden />
                  {activeMissions.length} Active Mission{activeMissions.length !== 1 ? 's' : ''}
                </p>
                <MissionOrbit missions={activeMissions} />
              </section>
            )}

            {/* Morning brief panel */}
            <div className="p-4 border-b border-[#1E2535]">
              <MorningBrief orgId={orgId} compact />
            </div>

            {/* Now/Next/Why panel */}
            <div className="p-4 border-b border-[#1E2535]">
              <NowNextWhy
                health={health as Parameters<typeof NowNextWhy>[0]['health']}
                missions={activeMissions}
                isLoading={orgLoading}
              />
            </div>

            {/* Graphify panel */}
            <AnimatePresence mode="wait">
              {showGraphify && (
                <motion.div
                  key="graphify"
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: 'auto' }}
                  exit={{ opacity: 0, height: 0 }}
                  transition={{ type: 'spring', stiffness: 300, damping: 28 }}
                  style={{ overflow: 'hidden' }}
                  className="border-b border-[#1E2535]"
                >
                  <div className="p-4">
                    <GraphifyProgress orgId={orgId} onClose={() => setShowGraphify(false)} />
                  </div>
                </motion.div>
              )}

              {/* Connector marketplace panel */}
              {showConnectors && (
                <motion.div
                  key="connectors"
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: 'auto' }}
                  exit={{ opacity: 0, height: 0 }}
                  transition={{ type: 'spring', stiffness: 300, damping: 28 }}
                  style={{ overflow: 'hidden' }}
                  className="border-b border-[#1E2535]"
                >
                  <div className="h-80">
                    <ConnectorMarketplace orgId={orgId} onClose={() => setShowConnectors(false)} />
                  </div>
                </motion.div>
              )}
            </AnimatePresence>


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
        </div>
      </JARVISPageShell>

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

      {/* ── Voice modal ─────────────────────────────────────────────────── */}
      <VoiceModal
        open={showVoice}
        onClose={() => setShowVoice(false)}
        orgId={orgId}
        onTranscript={(text) => {
          setShowVoice(false);
          setShowCreate(true);
          // Pre-fill title via URL state or context in a real implementation
          void text;
        }}
        placeholder="Describe a mission for your agents…"
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
              ? 'bg-blue-500/10 text-[#00D4FF] ring-1 ring-blue-500/30'
              : 'text-[#94A3B8] hover:text-[#F1F5F9] hover:bg-[#1A1F2E]',
          )}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}
