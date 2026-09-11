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
import { useState, useCallback, useRef, useEffect, useMemo } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import { JARVISPageShell } from '@/components/ui/JARVISPageShell';
import { JARVISBootScreen } from '@/components/ui/JARVISBootScreen';
import { Building2, Plus, RefreshCw, Zap, Network, Mic, Plug, Clock, Cpu, Terminal, BookOpen, Volume2, VolumeX, X } from 'lucide-react';
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
import { useOrganization, useOrgHealth, useMissions, useDepartments } from './hooks/useOrg';
import type { OrgMission }       from './types';

// Toolbar side-panels — each opens in the one right slide-over drawer.
type PanelKey = 'graphify' | 'connectors' | 'twin' | 'commands' | 'history' | 'obsidian' | 'approvals';
const PANEL_META: Record<PanelKey, { title: string; subtitle: string; Icon: typeof Network }> = {
  graphify:   { title: 'Knowledge Graph',    subtitle: 'Graphify build & progress',      Icon: Network },
  connectors: { title: 'Connectors',         subtitle: 'Integrations & tool marketplace', Icon: Plug },
  twin:       { title: 'Digital Twin',        subtitle: 'Capacity & load simulation',      Icon: Cpu },
  commands:   { title: 'Command History',     subtitle: 'Gateway command log',             Icon: Terminal },
  history:    { title: 'Org History',         subtitle: 'Timeline & navigation',           Icon: Clock },
  obsidian:   { title: 'Knowledge Vault',     subtitle: 'Obsidian-style explorer',         Icon: BookOpen },
  approvals:  { title: 'Approvals',           subtitle: 'Pending human sign-offs',         Icon: Zap },
};

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
  const [showVoice, setShowVoice]             = useState(false);
  // Toolbar side-panels all open in ONE right slide-over drawer — clear, labeled,
  // and obviously toggled — instead of injecting hidden panels into the column.
  const [activePanel, setActivePanel]         = useState<PanelKey | null>(null);
  const togglePanel = useCallback(
    (k: PanelKey) => setActivePanel((p) => (p === k ? null : k)),
    [],
  );
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

  // Live agent network data. Two layers, so the panel is never a dead hub:
  //   • Live agents stream in over SSE while a mission runs (neural.agents) —
  //     they execute, exchange messages, and hand off tasks.
  //   • When nothing is running, the org's real departments stand in as idle
  //     "standing by" robots. This is honest structure (these departments
  //     exist), not fabricated activity — no fake beams when idle.
  const { data: departments } = useDepartments(orgId ?? null);

  const liveAgents = useMemo(
    () => neural.agents.map(a => ({
      id:     a.id,
      label:  a.label,
      role:   a.role,
      status: (a.state === 'executing' || a.state === 'communicating')
        ? ('active' as const)
        : (a.state === 'error' || a.state === 'blocked')
          ? ('error' as const)
          : ('idle' as const),
      goalCount: a.goalCount,
    })),
    [neural.agents],
  );

  const departmentAgents = useMemo(
    () => (departments ?? [])
      .filter(d => d.status !== 'archived')
      .map(d => ({
        id:        `dept-${d.id}`,
        label:     d.name,
        role:      d.purpose || 'Department',
        status:    'idle' as const,
        goalCount: d.agent_count ?? 1,
      })),
    [departments],
  );

  // Prefer live agents; fall back to the department roster so the scene always
  // has a breathing population to render.
  const constellationAgents = liveAgents.length > 0 ? liveAgents : departmentAgents;
  const constellationIsLive  = liveAgents.length > 0;

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
              onClick={() => togglePanel('graphify')}
              aria-label="Open knowledge graph builder"
              title="Knowledge graph (Graphify)"
              style={{ touchAction: 'manipulation' }}
              className={cn(
                'p-2 rounded-lg transition-colors duration-150',
                'min-w-[44px] min-h-[44px] flex items-center justify-center',
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60',
                activePanel === 'graphify'
                  ? 'text-violet-300 bg-violet-500/10'
                  : 'text-[#475569] hover:text-[#94A3B8] hover:bg-[#1A1F2E]',
              )}
            >
              <Network className="h-4 w-4" aria-hidden />
            </button>

            {/* Connectors */}
            <button
              onClick={() => togglePanel('connectors')}
              aria-label="Connector marketplace"
              title="Connectors & integrations"
              style={{ touchAction: 'manipulation' }}
              className={cn(
                'p-2 rounded-lg transition-colors duration-150',
                'min-w-[44px] min-h-[44px] flex items-center justify-center',
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60',
                activePanel === 'connectors'
                  ? 'text-violet-300 bg-violet-500/10'
                  : 'text-[#475569] hover:text-[#94A3B8] hover:bg-[#1A1F2E]',
              )}
            >
              <Plug className="h-4 w-4" aria-hidden />
            </button>

            {/* Digital Twin */}
            <button
              onClick={() => togglePanel('twin')}
              aria-label="Digital Twin capacity view"
              title="Digital Twin — capacity & load"
              style={{ touchAction: 'manipulation' }}
              className={cn(
                'p-2 rounded-lg transition-colors duration-150',
                'min-w-[44px] min-h-[44px] flex items-center justify-center',
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60',
                activePanel === 'twin'
                  ? 'text-purple-300 bg-purple-500/10'
                  : 'text-[#475569] hover:text-[#94A3B8] hover:bg-[#1A1F2E]',
              )}
            >
              <Cpu className="h-4 w-4" aria-hidden />
            </button>

            {/* Command History */}
            <button
              onClick={() => togglePanel('commands')}
              aria-label="Command gateway history"
              title="Command history"
              style={{ touchAction: 'manipulation' }}
              className={cn(
                'p-2 rounded-lg transition-colors duration-150',
                'min-w-[44px] min-h-[44px] flex items-center justify-center',
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60',
                activePanel === 'commands'
                  ? 'text-emerald-300 bg-emerald-500/10'
                  : 'text-[#475569] hover:text-[#94A3B8] hover:bg-[#1A1F2E]',
              )}
            >
              <Terminal className="h-4 w-4" aria-hidden />
            </button>

            {/* History */}
            <button
              onClick={() => togglePanel('history')}
              aria-label="Organisation history"
              title="Org history & timeline"
              style={{ touchAction: 'manipulation' }}
              className={cn(
                'p-2 rounded-lg transition-colors duration-150',
                'min-w-[44px] min-h-[44px] flex items-center justify-center',
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60',
                activePanel === 'history'
                  ? 'text-amber-300 bg-amber-500/10'
                  : 'text-[#475569] hover:text-[#94A3B8] hover:bg-[#1A1F2E]',
              )}
            >
              <Clock className="h-4 w-4" aria-hidden />
            </button>

            {/* Obsidian Vault */}
            <button
              onClick={() => togglePanel('obsidian')}
              aria-label="Obsidian vault explorer"
              title="Knowledge vault"
              style={{ touchAction: 'manipulation' }}
              className={cn(
                'p-2 rounded-lg transition-colors duration-150',
                'min-w-[44px] min-h-[44px] flex items-center justify-center',
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60',
                activePanel === 'obsidian'
                  ? 'text-violet-300 bg-violet-500/10'
                  : 'text-[#475569] hover:text-[#94A3B8] hover:bg-[#1A1F2E]',
              )}
            >
              <BookOpen className="h-4 w-4" aria-hidden />
            </button>

            {/* G-05: Approvals button — amber badge when pending */}
            <button
              onClick={() => togglePanel('approvals')}
              aria-label={`Approvals${pendingApprovalCount > 0 ? ` (${pendingApprovalCount} pending)` : ''}`}
              title={`Approvals${pendingApprovalCount > 0 ? ` — ${pendingApprovalCount} pending` : ''}`}
              style={{ touchAction: 'manipulation' }}
              className={cn(
                'relative p-2 rounded-lg transition-colors duration-150',
                'min-w-[44px] min-h-[44px] flex items-center justify-center',
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-500/60',
                activePanel === 'approvals' || pendingApprovalCount > 0
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

          {/* Center (main): the JARVIS visualization is the hero — shown first,
              centered — then the mission list beneath it. */}
          <main
            className="flex-1 min-w-0 flex flex-col overflow-y-auto"
            aria-label="Live agent network and missions"
          >
            {/* ── JARVIS centerpiece: Live Agent Network ── */}
            <section
              className="relative shrink-0 border-b border-[#1E2535] bg-[radial-gradient(ellipse_at_top,rgba(0,212,255,0.08),transparent_72%)]"
              aria-label="Live agent network"
            >
              <div className="flex items-center justify-between px-6 pt-4 pb-1">
                <p className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.16em] text-[#00D4FF]/85">
                  <Network className="h-3 w-3" aria-hidden />
                  Live Agent Network
                  {constellationIsLive ? (
                    <span className="font-normal normal-case tracking-normal text-[#64748B]">
                      · {liveAgents.length} agent{liveAgents.length !== 1 ? 's' : ''} active
                    </span>
                  ) : departmentAgents.length > 0 ? (
                    <span className="font-normal normal-case tracking-normal text-[#64748B]">
                      · {departmentAgents.length} department{departmentAgents.length !== 1 ? 's' : ''} standing by
                    </span>
                  ) : null}
                </p>
                <button
                  onClick={() => setShowConstellation(v => !v)}
                  className="min-h-[32px] px-2 text-[10px] text-[#475569] transition-colors hover:text-[#94A3B8]"
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
                    <div className="flex items-center justify-center overflow-x-auto px-3 pb-5 pt-1">
                      <AgentConstellation
                        orgId={orgId}
                        missions={activeMissions}
                        agents={constellationAgents}
                        // Real message beams only — never fabricated when the
                        // scene is showing idle departments.
                        communicatingPairs={constellationIsLive ? neural.communicatingPairs : []}
                        className="max-w-full"
                      />
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </section>

            {/* ── Missions — beneath the visualization ── */}
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
            <div className="min-h-0 p-4">
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
            {/* Mission Orbit — active missions as orbiting nodes. The full
                agent constellation now lives in the center column as the hero. */}
            {activeMissions.length > 0 && (
              <section
                className="flex flex-col items-center justify-center border-b border-[#1E2535] py-4 min-w-0 shrink-0"
                aria-label="Active mission orbit visualization"
              >
                <p className="text-[10px] font-medium uppercase tracking-[0.1em] text-[#00D4FF]/60 mb-2 flex items-center gap-1.5">
                  <Cpu className="h-2.5 w-2.5" aria-hidden />
                  {activeMissions.length} Active Mission{activeMissions.length !== 1 ? 's' : ''}
                </p>
                <MissionOrbit missions={activeMissions} />
              </section>
            )}

            {/* Live activity feed — the JARVIS event stream, kept prominent right
                under the agent network so both are visible without scrolling. */}
            <section
              className="border-b border-[#1E2535] shrink-0 max-h-[22rem] overflow-y-auto p-4"
              aria-label="Live activity"
            >
              <ActivityFeed orgId={orgId} />
            </section>

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

      {/* ── Toolbar side-panel drawer — one clear, labeled slide-over ──────── */}
      {activePanel && (
          <div className="fixed inset-0 z-40 flex justify-end">
            <div
              className="absolute inset-0 bg-black/50 backdrop-blur-sm"
              onClick={() => setActivePanel(null)}
              aria-hidden
            />
            <motion.aside
              role="dialog"
              aria-modal="true"
              aria-label={PANEL_META[activePanel].title}
              className="relative z-10 h-full w-full sm:w-[420px] max-w-full
                         bg-[#0B0E14] border-l border-[#1E2535] shadow-2xl flex flex-col"
              initial={{ x: '100%' }}
              animate={{ x: 0 }}
              transition={{ type: 'spring', stiffness: 320, damping: 34 }}
            >
              <div className="flex items-center gap-2.5 px-4 py-3 border-b border-[#1E2535] shrink-0">
                {(() => {
                  const I = PANEL_META[activePanel].Icon;
                  return <I className="h-4 w-4 text-[#00D4FF] shrink-0" aria-hidden />;
                })()}
                <div className="min-w-0">
                  <p className="text-sm font-semibold text-[#F1F5F9] truncate">
                    {PANEL_META[activePanel].title}
                  </p>
                  <p className="text-[11px] text-[#475569] truncate">
                    {PANEL_META[activePanel].subtitle}
                  </p>
                </div>
                <button
                  onClick={() => setActivePanel(null)}
                  aria-label="Close panel"
                  className="ml-auto p-1.5 rounded-lg text-[#64748B] hover:text-[#F1F5F9] hover:bg-[#1A1F2E] transition-colors"
                >
                  <X className="h-4 w-4" aria-hidden />
                </button>
              </div>
              <div className="flex-1 overflow-y-auto">
                {activePanel === 'graphify' && (
                  <div className="p-4"><GraphifyProgress orgId={orgId} onClose={() => setActivePanel(null)} /></div>
                )}
                {activePanel === 'connectors' && (
                  <div className="h-full"><ConnectorMarketplace orgId={orgId} onClose={() => setActivePanel(null)} /></div>
                )}
                {activePanel === 'twin' && <div className="p-4"><DigitalTwinPanel orgId={orgId} /></div>}
                {activePanel === 'commands' && <div className="p-4"><CommandHistoryPanel orgId={orgId} /></div>}
                {activePanel === 'history' && <div className="p-4"><OrgHistoryNav orgId={orgId} compact /></div>}
                {activePanel === 'obsidian' && <div className="p-4"><ObsidianVaultExplorer orgId={orgId} compact /></div>}
                {activePanel === 'approvals' && <ApprovalCenter orgId={orgId} />}
              </div>
            </motion.aside>
          </div>
      )}
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
