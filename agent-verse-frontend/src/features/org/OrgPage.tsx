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
import { useState, useCallback, useRef, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import { JARVISPageShell } from '@/components/ui/JARVISPageShell';
import { JARVISBootScreen } from '@/components/ui/JARVISBootScreen';
import { Building2, Plus, RefreshCw, Zap, Network, Mic, Plug, Clock, Cpu, Terminal, BookOpen, Volume2, VolumeX } from 'lucide-react';
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
import { useJarvisSpeech }       from '@/lib/voice/useJarvisSpeech';
import { useVoicePrefsStore }    from '@/stores/voicePrefs';
import { useOrgRealtimeManager, type OrgEvent } from './OrgRealtimeManager';
import { useOrgNeuralState } from './hooks/useOrgNeuralState';
import { AgentConstellation } from './components/AgentConstellation';
import { useOrganization, useOrgHealth, useMissions } from './hooks/useOrg';
import type { OrgMission }       from './types';

export function OrgPage() {
  const { orgId } = useParams<{ orgId: string }>();

  // D-6: Proactive voice alerts — plays TTS audio when mission fails/approval needed
  useVoiceAlerts({ enabled: !!orgId });

  // Live agent constellation state — accumulates agent/team events into the
  // force-graph's node/edge model so bots spawn and light up as work happens.
  const neural = useOrgNeuralState(orgId ?? null);

  // WS-7 item 4 — "JARVIS speaking". Opt-in (persisted, default OFF), skips
  // itself under prefers-reduced-motion, and debounces bursts of events —
  // see useJarvisSpeech for the guardrails.
  const jarvis = useJarvisSpeech();
  const jarvisSpeechEnabled = useVoicePrefsStore(s => s.jarvisSpeechEnabled);
  const toggleJarvisSpeech  = useVoicePrefsStore(s => s.toggleJarvisSpeech);

  // useOrgRealtimeManager only re-subscribes `onEvent` when orgId/apiKey
  // change (see OrgRealtimeManager.ts), so this wrapper must have a stable
  // identity — but jarvis.handleEvent's identity legitimately changes when
  // the mute toggle flips. Route through refs kept fresh every render so the
  // stable callback always calls whatever handler is current.
  const neuralApplyEventRef = useRef(neural.applyEvent);
  neuralApplyEventRef.current = neural.applyEvent;
  const jarvisHandleEventRef = useRef(jarvis.handleEvent);
  jarvisHandleEventRef.current = jarvis.handleEvent;
  const handleOrgEvent = useCallback((event: OrgEvent) => {
    neuralApplyEventRef.current(event);
    jarvisHandleEventRef.current(event);
  }, []);

  // Live org event stream — missions forming, teams assembling, agents activating,
  // approvals — pushed over SSE. It invalidates the query cache so the whole
  // console updates in real time, feeds the constellation (onEvent) so new
  // agents animate into the graph as they spawn, and drives JARVIS's spoken
  // narration of key events.
  useOrgRealtimeManager(orgId, { onEvent: handleOrgEvent });

  // JARVIS boot screen — play the full cinematic boot ONCE per browser session
  // (it's a delight the first time, a 3s tax on every subsequent org visit), and
  // skip it entirely for users who prefer reduced motion.
  const reduceMotion = useReducedMotion();
  const [isBooted, setIsBooted] = useState(() => {
    if (reduceMotion) return true;
    try {
      return sessionStorage.getItem('av_org_booted') === '1';
    } catch {
      return false;
    }
  });
  const markBooted = useCallback(() => {
    try {
      sessionStorage.setItem('av_org_booted', '1');
    } catch {
      /* sessionStorage unavailable (private mode) — boot will just replay */
    }
    setIsBooted(true);
  }, []);

  const [selectedMission, setSelectedMission] = useState<string | null>(null);
  const [showCreate, setShowCreate]           = useState(false);
  const [statusFilter, setStatusFilter]       = useState<string | undefined>();
  // Clicking a department scopes the missions pane to that department.
  const [deptFilter, setDeptFilter]           = useState<{ id: string; name: string } | null>(null);
  const [showGraphify, setShowGraphify]       = useState(false);
  const [showVoice, setShowVoice]             = useState(false);
  const [showConnectors, setShowConnectors]   = useState(false);
  const [showTwin, setShowTwin]               = useState(false);
  const [showHistory, setShowHistory]         = useState(false);
  const [showCommands, setShowCommands]       = useState(false);
  const [showObsidian, setShowObsidian]       = useState(false);
  const [showApprovals, setShowApprovals]     = useState(false);
  const [showConstellation, setShowConstellation] = useState(true);

  // Resizable command panel (right pane). Width persisted per-browser.
  const RIGHT_MIN = 320;
  const RIGHT_MAX = 720;
  const [rightWidth, setRightWidth] = useState<number>(() => {
    try {
      const v = Number(localStorage.getItem('av-org-right-w'));
      return v >= RIGHT_MIN && v <= RIGHT_MAX ? v : 400;
    } catch {
      return 400;
    }
  });
  useEffect(() => {
    try {
      localStorage.setItem('av-org-right-w', String(rightWidth));
    } catch {
      /* localStorage unavailable — width just won't persist */
    }
  }, [rightWidth]);
  const startResize = useCallback(
    (e: React.PointerEvent) => {
      e.preventDefault();
      const startX = e.clientX;
      const startW = rightWidth;
      const onMove = (ev: PointerEvent) => {
        // Panel is on the right, so dragging left (smaller clientX) widens it.
        const next = Math.min(RIGHT_MAX, Math.max(RIGHT_MIN, startW + (startX - ev.clientX)));
        setRightWidth(next);
      };
      const onUp = () => {
        window.removeEventListener('pointermove', onMove);
        window.removeEventListener('pointerup', onUp);
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
      };
      window.addEventListener('pointermove', onMove);
      window.addEventListener('pointerup', onUp);
      document.body.style.cursor = 'col-resize';
      document.body.style.userSelect = 'none';
    },
    [rightWidth],
  );

  const { data: org, isLoading: orgLoading, refetch } = useOrganization(orgId ?? null);
  const { data: health }    = useOrgHealth(orgId ?? null);
  // Pending approval count for badge — use after health is declared
  const pendingApprovalCount = (health as any)?.pending_approvals ?? 0;
  const { data: missionInf } = useMissions(orgId, {});
  const activeMissions = (missionInf?.pages?.flatMap(p => p.data ?? []) ?? []).filter((m: OrgMission) => m.status === 'active');

  const navigate = useNavigate();
  // Open the full mission board (Tasks / Team workstreams / Artifacts / Activity)
  // — the view users expect when they click a mission — instead of only toggling
  // the slide-in live panel.
  const handleMissionClick = useCallback((mission: OrgMission) => {
    if (orgId) navigate(`/org/${orgId}/mission/${mission.id}`);
  }, [navigate, orgId]);

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
        onComplete={markBooted}
        duration={2400}
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
              title="Voice input — describe a mission"
              style={{ touchAction: 'manipulation' }}
              className={cn(
                'p-2 rounded-lg text-[#475569] hover:text-[#94A3B8] hover:bg-[#1A1F2E]',
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60',
                'transition-colors duration-150 min-w-[44px] min-h-[44px] flex items-center justify-center',
              )}
            >
              <Mic className="h-4 w-4" aria-hidden />
            </button>

            {/* WS-7 item 4: JARVIS speaking mute/unmute — opt-in, persisted, default OFF */}
            <button
              onClick={toggleJarvisSpeech}
              aria-pressed={jarvisSpeechEnabled}
              aria-label={jarvisSpeechEnabled ? 'Mute JARVIS voice narration' : 'Unmute JARVIS voice narration'}
              title={jarvisSpeechEnabled ? 'JARVIS voice: on' : 'JARVIS voice: off'}
              style={{ touchAction: 'manipulation' }}
              className={cn(
                'p-2 rounded-lg transition-colors duration-150',
                'min-w-[44px] min-h-[44px] flex items-center justify-center',
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60',
                jarvisSpeechEnabled
                  ? 'text-[#00D4FF] bg-[#00D4FF]/10'
                  : 'text-[#475569] hover:text-[#94A3B8] hover:bg-[#1A1F2E]',
              )}
            >
              {jarvisSpeechEnabled
                ? <Volume2 className="h-4 w-4" aria-hidden />
                : <VolumeX className="h-4 w-4" aria-hidden />}
            </button>

            {/* Graphify */}
            <button
              onClick={() => setShowGraphify(v => !v)}
              aria-label="Open knowledge graph builder"
              title="Knowledge graph (Graphify)"
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
              title="Connectors & integrations"
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
              title="Command history"
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
              title="Org history & timeline"
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
              title="Knowledge vault"
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
              title="Refresh"
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

          {/* Left (main): Active Missions — the star of the page */}
          <main
            className="flex-1 min-w-0 flex flex-col overflow-hidden"
            aria-label="Missions panel"
          >
            {/* Filter tabs */}
            <StatusFilterBar value={statusFilter} onChange={setStatusFilter} />

            {/* Active department filter chip */}
            {deptFilter && (
              <div className="px-4 pt-2 shrink-0">
                <button
                  onClick={() => setDeptFilter(null)}
                  className="inline-flex items-center gap-1.5 rounded-full bg-blue-500/10 px-3 py-1
                             text-[12px] font-medium text-[#00D4FF] ring-1 ring-blue-500/30
                             hover:bg-blue-500/20 transition-colors"
                  aria-label={`Clear department filter: ${deptFilter.name}`}
                >
                  <Zap className="h-3 w-3" aria-hidden />
                  {deptFilter.name}
                  <span className="text-[#94A3B8]">✕</span>
                </button>
              </div>
            )}

            {/* Virtualized missions list */}
            <div className="flex-1 overflow-hidden p-4">
              <MissionsList
                orgId={orgId}
                statusFilter={statusFilter}
                deptFilter={deptFilter?.id}
                onMissionClick={handleMissionClick}
                onCreateClick={() => setShowCreate(true)}
              />
            </div>
          </main>

          {/* Drag handle — resize the command panel */}
          <div
            role="separator"
            aria-orientation="vertical"
            aria-label="Resize command panel"
            onPointerDown={startResize}
            className={cn(
              'hidden lg:flex w-1.5 shrink-0 cursor-col-resize items-center justify-center group',
              'bg-[#1E2535] hover:bg-blue-500/40 active:bg-blue-500/60 transition-colors',
            )}
          >
            <span className="h-8 w-0.5 rounded-full bg-[#334155] group-hover:bg-blue-400" />
          </div>

          {/* Right (command panel): agent network + orbit + activity + departments — resizable */}
          <aside
            style={{ width: rightWidth }}
            className="shrink-0 hidden lg:flex flex-col overflow-y-auto border-l border-[#1E2535] bg-[#0B0E14]"
            aria-label="Command panel"
          >
            {/* Live agent constellation — moved here so missions own the main pane.
                Obsidian-style force graph where each member lights up as it works. */}
            <section className="border-b border-[#1E2535] shrink-0" aria-label="Live agent network">
              <div className="flex items-center justify-between px-4 pt-3 pb-1">
                <p className="text-[10px] font-medium uppercase tracking-[0.14em] text-[#00D4FF]/70 flex items-center gap-1.5">
                  <Network className="h-2.5 w-2.5" aria-hidden />
                  Live Agent Network
                  {neural.agents.length > 0 && (
                    <span className="text-[#475569] normal-case tracking-normal">
                      · {neural.agents.length} agent{neural.agents.length !== 1 ? 's' : ''} active
                    </span>
                  )}
                </p>
                <button
                  onClick={() => setShowConstellation(v => !v)}
                  className="text-[10px] text-[#475569] hover:text-[#94A3B8] transition-colors min-h-[32px] px-2"
                  aria-expanded={showConstellation}
                  aria-label={showConstellation ? 'Hide agent network' : 'Show agent network'}
                >
                  {showConstellation ? 'Hide' : 'Show'}
                </button>
              </div>
              <AnimatePresence initial={false}>
                {showConstellation && (
                  <motion.div
                    initial={{ height: 0, opacity: 0 }}
                    animate={{ height: 'auto', opacity: 1 }}
                    exit={{ height: 0, opacity: 0 }}
                    transition={{ duration: 0.35, ease: [0.4, 0, 0.2, 1] }}
                    className="overflow-hidden"
                  >
                    <div className="flex items-center justify-center p-3 overflow-auto">
                      <AgentConstellation
                        orgId={orgId}
                        missions={activeMissions}
                        agents={neural.agents.map(a => ({
                          id: a.id,
                          label: a.label,
                          role: a.role,
                          status:
                            a.state === 'executing' || a.state === 'communicating'
                              ? 'active'
                              : a.state === 'error' || a.state === 'blocked'
                                ? 'error'
                                : 'idle',
                          goalCount: a.goalCount,
                        }))}
                        communicatingPairs={neural.communicatingPairs}
                        className="max-w-full"
                      />
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </section>
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
                <span className="normal-case tracking-normal text-[#334155]">· click to filter missions</span>
              </h2>
              <DepartmentTree
                orgId={orgId}
                onDeptSelect={(d) =>
                  setDeptFilter((prev) => (prev?.id === d.id ? null : { id: d.id, name: d.name }))
                }
              />
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
