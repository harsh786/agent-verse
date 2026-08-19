/**
 * CivilizationPage — world-class Agent Civilization Theater.
 *
 * Design:
 * - Dark command-center aesthetic (navy/slate palette)
 * - Left: React Flow canvas (65%) with glassmorphic agent nodes
 * - Right: Icon-tab panel (35%) — Overview, Blackboard, Learnings, Spawns, Debates, Constitution, Replay
 * - Live event ticker at canvas bottom
 * - Agent Inspector slide-over on node click
 * - CivilizationList: bento grid with live metrics
 */
import { useState, useCallback } from 'react';
import { useParams, Link } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Globe, Clipboard, BookOpen, GitBranch, Scale, Settings2,
  Radio, BarChart2, ArrowLeft, Plus, Loader2, AlertTriangle,
  Wifi, WifiOff, Zap, Users, X,
} from 'lucide-react';
import { civilizationApi } from '../../lib/api/civilizationApi';
import { apiFetch } from '@/lib/api/client';
import { toast } from '@/stores/toast';
import { useCivilizationStream } from '../../lib/sse/useCivilizationStream';
import { StatusOrb } from '@/components/ui/StatusOrb';
import { CivilizationMap } from './CivilizationMap';
import { CivilizationMetrics } from './CivilizationMetrics';
import { BlackboardFeed } from './BlackboardFeed';
import { LearningLedger } from './LearningLedger';
import { ControlBar } from './ControlBar';
import { AgentInspectorDrawer } from './AgentInspectorDrawer';
import { DebateViewer } from './DebateViewer';
import { ConstitutionEditor } from './ConstitutionEditor';
import { SpawnLineageTimeline } from './SpawnLineageTimeline';
import { MembersPanel } from './MembersPanel';
import type { CivilizationEvent, Civilization } from '../../lib/api/civilizationApi';
import { JARVISPageShell } from '@/components/ui/JARVISPageShell';
import { JARVISStagger } from '@/components/ui/JARVISPageShell';

type Panel = 'overview' | 'members' | 'blackboard' | 'learnings' | 'spawns' | 'debates' | 'constitution' | 'replay';

// ── Civilization List ─────────────────────────────────────────────────────────

function StatusDot({ status }: { status: string }) {
  return (
    <span className={`w-2 h-2 rounded-full flex-shrink-0 inline-block ${
      status === 'active' ? 'bg-green-400 animate-pulse' :
      status === 'paused' ? 'bg-amber-400' :
      'bg-[#64748B]'
    }`} />
  );
}

function CivilizationList() {
  const qc = useQueryClient();
  const { data: civilizations, isLoading, error } = useQuery({
    queryKey: ['civilizations'],
    queryFn: () => civilizationApi.list(),
    refetchInterval: 8000,
  });

  const civs = (civilizations as Civilization[] | undefined) ?? [];

  const [showNewCivModal, setShowNewCivModal] = useState(false);
  const [newCivForm, setNewCivForm] = useState({ name: '', description: '', max_agents: 5, autonomy_level: 'bounded-autonomous' });

  return (
    <div
      className="min-h-screen"
      style={{ background: 'linear-gradient(135deg, #0F1117 0%, #0d1625 100%)' }}
    >
      {/* Header */}
      <div
        className="border-b px-6 py-4"
        style={{ borderColor: 'rgba(255,255,255,0.06)', background: 'rgba(15,23,42,0.8)', backdropFilter: 'blur(12px)' }}
      >
        <div className="max-w-5xl mx-auto flex items-center justify-between">
          <div>
            <h1 className="text-xl font-bold text-[#F1F5F9] flex items-center gap-2">
              <Globe className="h-5 w-5 text-indigo-400" />
              Agent Civilizations
            </h1>
            <p className="text-[#5A7494] text-sm mt-0.5">
              Autonomous multi-agent societies — each solves goals collectively
            </p>
          </div>
          {/* Create civilization button */}
          <button
            onClick={() => setShowNewCivModal(true)}
            className="flex items-center gap-2 px-4 py-2 bg-[#00D4FF] text-[#00D4FF]-foreground text-sm font-medium rounded-lg hover:opacity-90 transition-opacity"
            aria-label="Create a new civilization"
          >
            <Plus className="h-4 w-4" />
            New Civilization
          </button>
        </div>
      </div>

      <div className="max-w-5xl mx-auto px-6 py-8">
        {isLoading && (
          <div className="flex items-center justify-center h-64 gap-3 text-[#5A7494]">
            <Loader2 className="h-5 w-5 animate-spin" />
            <span className="text-sm">Loading civilizations…</span>
          </div>
        )}

        {error && (
          <div className="flex items-center justify-center h-64">
            <div
              className="rounded-2xl p-6 text-center max-w-sm"
              style={{ background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.2)' }}
            >
              <AlertTriangle className="h-8 w-8 text-red-400 mx-auto mb-3" />
              <p className="text-sm font-medium text-red-300">Failed to load civilizations</p>
              <p className="text-xs text-red-500 mt-1">Is the backend running?</p>
            </div>
          </div>
        )}

        {!isLoading && !error && civs.length === 0 && (
          <div className="flex items-center justify-center h-64">
            <div
              className="rounded-2xl p-10 text-center max-w-md"
              style={{ background: 'rgba(255,255,255,0.03)', border: '2px dashed rgba(255,255,255,0.08)' }}
            >
              <Globe className="h-12 w-12 text-[#A0B4CC] mx-auto mb-4" />
              <p className="text-base font-semibold text-[#CBD5E1]">No civilizations yet</p>
              <p className="text-sm text-[#374151] mt-1">
                Create one via the API or backend to get started
              </p>
            </div>
          </div>
        )}

        {civs.length > 0 && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {civs.map((civ: Civilization) => {
              const active = civ.metrics?.active_members ?? 0;
              const total = civ.metrics?.total_members ?? 0;
              const spent = civ.metrics?.total_budget_spent_usd ?? 0;
              const rep = civ.metrics?.avg_reputation ?? 0;

              return (
                <Link
                  key={civ.id}
                  to={`/civilization/${civ.id}`}
                  className="group block rounded-2xl transition-[color,background-color,border-color,opacity,box-shadow,transform] duration-200 hover:scale-[1.01]"
                  style={{
                    background: 'linear-gradient(135deg, rgba(255,255,255,0.04) 0%, rgba(255,255,255,0.02) 100%)',
                    border: '1px solid rgba(255,255,255,0.08)',
                    boxShadow: '0 4px 24px rgba(0,0,0,0.3)',
                  }}
                  onMouseEnter={e => {
                    (e.currentTarget as HTMLAnchorElement).style.borderColor = 'rgba(99,102,241,0.4)';
                    (e.currentTarget as HTMLAnchorElement).style.boxShadow = '0 8px 32px rgba(99,102,241,0.15)';
                  }}
                  onMouseLeave={e => {
                    (e.currentTarget as HTMLAnchorElement).style.borderColor = 'rgba(255,255,255,0.08)';
                    (e.currentTarget as HTMLAnchorElement).style.boxShadow = '0 4px 24px rgba(0,0,0,0.3)';
                  }}
                >
                  {/* Accent gradient top */}
                  <div
                    className="h-0.5 rounded-t-2xl"
                    style={{ background: 'linear-gradient(90deg, #6366f1 0%, #a855f7 50%, #6366f1 100%)' }}
                  />

                  <div className="p-5">
                    {/* Header */}
                    <div className="flex items-start justify-between gap-3 mb-4">
                      <div className="flex items-center gap-3">
                        <div
                          className="w-10 h-10 rounded-xl flex items-center justify-center flex-shrink-0"
                          style={{ background: 'rgba(99,102,241,0.15)', border: '1px solid rgba(99,102,241,0.25)' }}
                        >
                          <Globe className="h-5 w-5 text-indigo-400" />
                        </div>
                        <div>
                          <h2 className="font-bold text-[#F1F5F9] group-hover:text-[#F1F5F9] transition-colors leading-tight">
                            {civ.name}
                          </h2>
                          <p className="text-[10px] font-mono text-[#374151] mt-0.5">
                            {civ.id.slice(0, 16)}…
                          </p>
                        </div>
                      </div>
                      <div className="flex items-center gap-1.5 flex-shrink-0">
                        <StatusDot status={civ.status} />
                        <span className="text-xs text-[#94A3B8] capitalize">{civ.status}</span>
                      </div>
                    </div>

                    {/* Metrics grid */}
                    <div className="grid grid-cols-4 gap-2">
                      {[
                        { label: 'Active', value: active, color: '#3b82f6' },
                        { label: 'Total', value: total, color: '#8b5cf6' },
                        { label: 'Rep', value: `${Math.round(rep * 100)}%`, color: rep > 0.6 ? '#22c55e' : '#f59e0b' },
                        { label: 'Spent', value: `$${spent.toFixed(2)}`, color: '#f59e0b' },
                      ].map(m => (
                        <div
                          key={m.label}
                          className="rounded-lg p-2 text-center"
                          style={{ background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.06)' }}
                        >
                          <p className="text-sm font-bold tabular-nums" style={{ color: m.color }}>
                            {m.value}
                          </p>
                          <p className="text-[9px] text-[#374151] mt-px">{m.label}</p>
                        </div>
                      ))}
                    </div>

                    {/* Footer */}
                    <div className="flex items-center justify-between mt-3">
                      <span className="text-[10px] text-[#374151]">
                        Created {new Date(civ.created_at).toLocaleDateString()}
                      </span>
                      <span className="text-[10px] text-indigo-400 font-medium group-hover:text-indigo-300 transition-colors">
                        Enter Theater →
                      </span>
                    </div>
                  </div>
                </Link>
              );
            })}
          </div>
        )}
      </div>

      {/* ── New Civilization Modal ─────────────────────────────────────── */}
      {showNewCivModal && (
        <div className="fixed inset-0 z-[200] flex items-center justify-center p-4">
          <div className="absolute inset-0 bg-black/50 backdrop-blur-sm" onClick={() => setShowNewCivModal(false)} />
          <div className="relative bg-card border border-border rounded-xl shadow-xl max-w-md w-full p-6 space-y-4">
            <div className="flex items-center justify-between">
              <h2 className="text-base font-semibold">New Civilization</h2>
              <button onClick={() => setShowNewCivModal(false)} className="text-muted-foreground hover:text-foreground">
                <X className="h-4 w-4" />
              </button>
            </div>

            <div className="space-y-3">
              <div>
                <label className="block text-xs font-medium mb-1">Name *</label>
                <input
                  autoFocus
                  value={newCivForm.name}
                  onChange={e => setNewCivForm(f => ({ ...f, name: e.target.value }))}
                  placeholder="e.g. Research Cluster Alpha"
                  className="w-full px-3 py-2 text-sm border border-input rounded-lg bg-background focus:outline-none focus:ring-2 focus:ring-[#00D4FF]"
                />
              </div>
              <div>
                <label className="block text-xs font-medium mb-1">Description</label>
                <textarea
                  value={newCivForm.description}
                  onChange={e => setNewCivForm(f => ({ ...f, description: e.target.value }))}
                  rows={2}
                  placeholder="What is this civilization for?"
                  className="w-full px-3 py-2 text-sm border border-input rounded-lg bg-background resize-none focus:outline-none focus:ring-2 focus:ring-primary"
                />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs font-medium mb-1">Max Agents</label>
                  <input
                    type="number"
                    min={1}
                    max={50}
                    value={newCivForm.max_agents}
                    onChange={e => setNewCivForm(f => ({ ...f, max_agents: parseInt(e.target.value) || 5 }))}
                    className="w-full px-3 py-2 text-sm border border-input rounded-lg bg-background focus:outline-none focus:ring-2 focus:ring-primary"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium mb-1">Autonomy</label>
                  <select
                    value={newCivForm.autonomy_level}
                    onChange={e => setNewCivForm(f => ({ ...f, autonomy_level: e.target.value }))}
                    className="w-full px-3 py-2 text-sm border border-input rounded-lg bg-background focus:outline-none focus:ring-2 focus:ring-primary"
                  >
                    <option value="manual">Manual</option>
                    <option value="human-in-loop">Human in Loop</option>
                    <option value="bounded-autonomous">Bounded Autonomous</option>
                    <option value="fully-autonomous">Fully Autonomous</option>
                  </select>
                </div>
              </div>
            </div>

            <div className="flex gap-3">
              <button
                onClick={async () => {
                  if (!newCivForm.name.trim()) return;
                  try {
                    await apiFetch('/civilization/civilizations', {
                      method: 'POST',
                      body: JSON.stringify(newCivForm),
                    });
                    toast({ kind: 'success', message: `Civilization "${newCivForm.name}" created!` });
                    setShowNewCivModal(false);
                    setNewCivForm({ name: '', description: '', max_agents: 5, autonomy_level: 'bounded-autonomous' });
                    qc.invalidateQueries({ queryKey: ['civilizations'] });
                  } catch (e) {
                    toast({ kind: 'error', message: `Failed: ${String(e)}` });
                  }
                }}
                disabled={!newCivForm.name.trim()}
                className="flex-1 py-2.5 bg-primary text-[#00D4FF]-foreground font-medium rounded-lg hover:opacity-90 disabled:opacity-50"
              >
                Create Civilization
              </button>
              <button onClick={() => setShowNewCivModal(false)} className="px-4 py-2.5 border border-input rounded-lg hover:bg-muted/50">
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

const PANEL_TABS: {
  key: Panel;
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  shortLabel: string;
}[] = [
  { key: 'overview',      icon: BarChart2,  label: 'Overview',     shortLabel: 'Overview' },
  { key: 'members',       icon: Users,      label: 'Members',      shortLabel: 'Members' },
  { key: 'blackboard',    icon: Clipboard,  label: 'Blackboard',   shortLabel: 'Board' },
  { key: 'learnings',     icon: BookOpen,   label: 'Learnings',    shortLabel: 'Learn' },
  { key: 'spawns',        icon: GitBranch,  label: 'Spawn Audit',  shortLabel: 'Spawns' },
  { key: 'debates',       icon: Scale,      label: 'Debates',      shortLabel: 'Debates' },
  { key: 'constitution',  icon: Settings2,  label: 'Constitution', shortLabel: 'Rules' },
  { key: 'replay',        icon: Radio,      label: 'Live Events',  shortLabel: 'Events' },
];

const EVENT_TYPE_COLOR: Record<string, string> = {
  agent_spawned:   '#22c55e',
  agent_retired:   '#94a3b8',
  goal_assigned:   '#6366f1',
  goal_complete:   '#22c55e',
  goal_failed:     '#ef4444',
  debate_started:  '#a855f7',
  debate_resolved: '#22c55e',
  finding_posted:  '#3b82f6',
  learning_promoted: '#f59e0b',
};

function EventTypeBadge({ type }: { type: string }) {
  const color = EVENT_TYPE_COLOR[type] ?? '#475569';
  const label = type.replace(/_/g, ' ');
  return (
    <span
      className="text-[10px] font-semibold px-1.5 py-0.5 rounded-full border"
      style={{
        background: `${color}15`,
        borderColor: `${color}40`,
        color,
      }}
    >
      {label}
    </span>
  );
}

function CivilizationTheater({ civId }: { civId: string }) {
  const qc = useQueryClient();
  const [activePanel, setActivePanel] = useState<Panel>('overview');
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null);
  const [liveEvents, setLiveEvents] = useState<CivilizationEvent[]>([]);

  const { data: civ } = useQuery({
    queryKey: ['civilization', civId],
    queryFn: () => civilizationApi.get(civId),
    refetchInterval: 6000,
  });

  const { data: graph } = useQuery({
    queryKey: ['civilization-graph', civId],
    queryFn: () => civilizationApi.getGraph(civId),
    refetchInterval: 4000,
  });

  const { data: blackboard } = useQuery({
    queryKey: ['civilization-blackboard', civId],
    queryFn: () => civilizationApi.getBlackboard(civId),
    enabled: activePanel === 'blackboard',
    refetchInterval: 3000,
  });

  const { data: learnings } = useQuery({
    queryKey: ['civilization-learnings', civId],
    queryFn: () => civilizationApi.getLearnings(civId),
    enabled: activePanel === 'learnings',
    refetchInterval: 5000,
  });

  const { data: spawns } = useQuery({
    queryKey: ['civilization-spawns', civId],
    queryFn: () => civilizationApi.getSpawnAudit(civId),
    enabled: activePanel === 'spawns',
    refetchInterval: 5000,
  });

  const { data: debates } = useQuery({
    queryKey: ['civilization-debates', civId],
    queryFn: () => civilizationApi.getDebates(civId),
    enabled: activePanel === 'debates',
    refetchInterval: 5000,
  });

  const handleEvent = useCallback((evt: CivilizationEvent) => {
    setLiveEvents(prev => [...prev.slice(-99), evt]);
    if (['agent_spawned', 'agent_retired'].includes(evt.type)) {
      void qc.invalidateQueries({ queryKey: ['civilization-graph', civId] });
      void qc.invalidateQueries({ queryKey: ['civilization', civId] });
    }
  }, [civId, qc]);

  const { connected } = useCivilizationStream(civId, { onEvent: handleEvent });

  const handleSubmitGoal = async (goal: string) => {
    await civilizationApi.submitGoal(civId, goal);
    void qc.invalidateQueries({ queryKey: ['civilization-graph', civId] });
  };

  const handlePause = async () => {
    await civilizationApi.control(civId, 'pause');
    void qc.invalidateQueries({ queryKey: ['civilization', civId] });
  };

  const handleResume = async () => {
    await civilizationApi.control(civId, 'resume');
    void qc.invalidateQueries({ queryKey: ['civilization', civId] });
  };

  const isPaused = civ?.status === 'paused';
  const activeCount = civ?.metrics?.active_members ?? 0;
  const totalCount = civ?.metrics?.total_members ?? 0;

  return (
    <div
      className="flex flex-col h-screen overflow-hidden"
      style={{ background: '#0b1120' }}
    >
      {/* ── Top Header Bar ─────────────────────────────────────────────────── */}
      <div
        className="flex items-center gap-4 px-4 py-2.5 border-b flex-shrink-0"
        style={{
          background: 'rgba(15,23,42,0.98)',
          borderColor: 'rgba(255,255,255,0.06)',
          backdropFilter: 'blur(8px)',
        }}
      >
        {/* Breadcrumb */}
        <Link
          to="/civilization"
          className="flex items-center gap-1.5 text-xs text-[#5A7494] hover:text-[#CBD5E1] transition-colors flex-shrink-0"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          <span className="hidden sm:inline">Civilizations</span>
        </Link>
        <span className="text-[#A0B4CC] text-xs">/</span>

        {/* Title */}
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <Globe className="h-4 w-4 text-indigo-400 flex-shrink-0" />
            <h1 className="text-sm font-bold text-[#F1F5F9] truncate">
              {civ?.name ?? 'Civilization'}
            </h1>
            {isPaused && (
              <span className="text-[10px] px-1.5 py-px rounded bg-amber-500/15 text-amber-400 border border-amber-500/25 flex-shrink-0">
                PAUSED
              </span>
            )}
          </div>
        </div>

        {/* Right: live KPIs */}
        <div className="flex items-center gap-3 flex-shrink-0">
          {/* Agent counts */}
          {totalCount > 0 && (
            <div className="hidden md:flex items-center gap-2 text-xs">
              <span
                className="flex items-center gap-1.5 px-2 py-0.5 rounded-full"
                style={{ background: 'rgba(59,130,246,0.1)', border: '1px solid rgba(59,130,246,0.2)' }}
              >
                <span className="w-1.5 h-1.5 rounded-full bg-blue-400 animate-pulse" />
                <span className="text-[#00D4FF] font-medium">{activeCount} active</span>
              </span>
              <span className="text-[#374151]">{totalCount} total</span>
            </div>
          )}

          {/* Live indicator */}
          <div className="flex items-center gap-1.5 text-xs">
            {connected
              ? <Wifi className="h-3.5 w-3.5 text-green-400" />
              : <WifiOff className="h-3.5 w-3.5 text-red-400 animate-pulse" />
            }
            <span className={`hidden sm:inline ${connected ? 'text-green-400' : 'text-red-400'}`}>
              {connected ? 'Live' : 'Reconnecting…'}
            </span>
          </div>

          {/* Event count badge */}
          {liveEvents.length > 0 && (
            <div
              className="flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-full"
              style={{ background: 'rgba(99,102,241,0.15)', border: '1px solid rgba(99,102,241,0.3)', color: '#a5b4fc' }}
            >
              <Zap className="h-3 w-3" />
              {liveEvents.length}
            </div>
          )}
        </div>
      </div>

      {/* ── Command / Control Bar ─────────────────────────────────────────── */}
      <ControlBar
        civilizationId={civId}
        status={civ?.status ?? 'active'}
        onPause={handlePause}
        onResume={handleResume}
        onSubmitGoal={handleSubmitGoal}
        onAdjustBudget={async (newBudget) => {
          await civilizationApi.control(civId, 'set_budget', { budget_usd: newBudget });
          void qc.invalidateQueries({ queryKey: ['civilization', civId] });
        }}
        currentBudget={civ?.constitution?.total_budget_usd}
      />

      {/* ── Main Content ──────────────────────────────────────────────────── */}
      <div className="flex flex-1 overflow-hidden">

        {/* ── Canvas (left 65%) ──────────────────────────────────────────── */}
        <div className="flex-1 relative overflow-hidden min-w-0">
          <CivilizationMap
            nodes={graph?.nodes ?? []}
            edges={graph?.edges ?? []}
            onNodeClick={setSelectedAgentId}
            liveEvents={liveEvents}
          />

          {/* Live event ticker overlay */}
          {liveEvents.length > 0 && (
            <div
              className="absolute bottom-4 left-4 right-4 pointer-events-none"
              style={{ maxWidth: '480px' }}
            >
              <div
                className="rounded-xl px-3 py-2 space-y-1"
                style={{
                  background: 'rgba(15,23,42,0.88)',
                  border: '1px solid rgba(255,255,255,0.08)',
                  backdropFilter: 'blur(12px)',
                }}
              >
                {liveEvents.slice(-3).reverse().map((e, i) => (
                  <div
                    key={`${e.id}-${i}`}
                    className="flex items-center gap-2 text-[11px]"
                    style={{ opacity: 1 - i * 0.25 }}
                  >
                    <span className="text-[#374151] font-mono flex-shrink-0">
                      {(e.ts ?? '').slice(11, 19)}
                    </span>
                    <EventTypeBadge type={e.type} />
                    {(e.payload as Record<string, unknown>)?.agent_id != null && (
                      <span className="text-[#5A7494] font-mono truncate">
                        {String((e.payload as Record<string, unknown>).agent_id).slice(0, 10)}
                      </span>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* ── Right Panel (35%) ─────────────────────────────────────────── */}
        <div
          className="w-[340px] xl:w-[380px] flex flex-col flex-shrink-0 border-l overflow-hidden"
          style={{
            background: 'rgba(15,23,42,0.97)',
            borderColor: 'rgba(255,255,255,0.06)',
          }}
        >
          {/* Tab bar — icon + label */}
          <div
            className="flex border-b overflow-x-auto scrollbar-none flex-shrink-0"
            style={{ borderColor: 'rgba(255,255,255,0.06)' }}
          >
            {PANEL_TABS.map(({ key, icon: Icon, label, shortLabel }) => {
              const isActive = activePanel === key;
              return (
                <button
                  key={key}
                  onClick={() => setActivePanel(key)}
                  title={label}
                  className={`
                    flex flex-col items-center justify-center gap-0.5 px-2.5 py-2.5 min-w-[48px] flex-1
                    text-[10px] font-medium transition-colors border-b-2 whitespace-nowrap
                    ${isActive
                      ? 'border-indigo-500 text-indigo-300'
                      : 'border-transparent text-[#374151] hover:text-[#94A3B8] hover:border-[#1E2535]'
                    }
                  `}
                  aria-selected={isActive}
                  role="tab"
                >
                  <Icon className={`h-4 w-4 ${isActive ? 'text-indigo-400' : ''}`} />
                  <span className="hidden lg:inline">{shortLabel}</span>
                </button>
              );
            })}
          </div>

          {/* Panel content */}
          <div className="flex-1 overflow-y-auto p-4 space-y-0">
            {activePanel === 'overview' && (
              civ?.metrics
                ? <CivilizationMetrics metrics={civ.metrics} />
                : <PanelPlaceholder icon={BarChart2} message="Metrics will appear once agents are active." />
            )}

            {activePanel === 'members' && (
              <MembersPanel civId={civId} />
            )}

            {activePanel === 'blackboard' && (
              <BlackboardFeed entries={blackboard ?? []} />
            )}

            {activePanel === 'learnings' && (
              <LearningLedger records={learnings ?? []} />
            )}

            {activePanel === 'spawns' && (
              <SpawnLineageTimeline spawns={spawns ?? []} />
            )}

            {activePanel === 'debates' && (
              // eslint-disable-next-line @typescript-eslint/no-explicit-any
              <DebateViewer debates={(debates ?? []) as any[]} />
            )}

            {activePanel === 'constitution' && civ && (
              <ConstitutionEditor
                constitution={civ.constitution}
                onSave={async (newConst) => {
                  await civilizationApi.updateConstitution(civId, newConst);
                  void qc.invalidateQueries({ queryKey: ['civilization', civId] });
                }}
              />
            )}

            {activePanel === 'replay' && (
              <ReplayPanel events={liveEvents} />
            )}
          </div>
        </div>
      </div>

      {/* Agent Inspector slide-over */}
      {selectedAgentId && (
        <AgentInspectorDrawer
          civilizationId={civId}
          agentId={selectedAgentId}
          onClose={() => setSelectedAgentId(null)}
        />
      )}
    </div>
  );
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function PanelPlaceholder({
  icon: Icon,
  message,
}: {
  icon: React.ComponentType<{ className?: string }>;
  message: string;
}) {
  return (
    <div className="flex flex-col items-center justify-center py-16 text-center space-y-3">
      <div
        className="w-12 h-12 rounded-xl flex items-center justify-center"
        style={{ background: 'rgba(99,102,241,0.08)', border: '1px solid rgba(99,102,241,0.15)' }}
      >
        <Icon className="h-6 w-6 text-[#5A7494]" />
      </div>
      <p className="text-xs text-[#374151] max-w-[200px] leading-relaxed">{message}</p>
    </div>
  );
}

function ReplayPanel({ events }: { events: CivilizationEvent[] }) {
  if (events.length === 0) {
    return (
      <PanelPlaceholder
        icon={Radio}
        message="Live events will stream here during execution."
      />
    );
  }

  return (
    <JARVISPageShell>
    <JARVISStagger className="space-y-1.5">
      <div className="flex items-center justify-between text-[10px] text-[#5A7494] mb-2">
        <span>{events.length} event{events.length !== 1 ? 's' : ''}</span>
        <span className="flex items-center gap-1 text-green-400">
          <StatusOrb status="completed" size={6} />
          Live
        </span>
      </div>
      {[...events].reverse().map((e, i) => (
        <div
          key={`${e.id}-${i}`}
          className="flex items-start gap-2 py-1.5 border-b last:border-0 text-xs"
          style={{ borderColor: 'rgba(255,255,255,0.05)' }}
        >
          <span className="text-[#374151] font-mono text-[10px] flex-shrink-0 pt-0.5">
            {(e.ts ?? '').slice(11, 19)}
          </span>
          <EventTypeBadge type={e.type} />
        </div>
      ))}
    </JARVISStagger>
    </JARVISPageShell>
  );
}

// ── Page router ───────────────────────────────────────────────────────────────

export function CivilizationPage() {
  const { id: civId } = useParams<{ id: string }>();
  if (!civId) return <CivilizationList />;
  return <CivilizationTheater civId={civId} />;
}

export default CivilizationPage;
