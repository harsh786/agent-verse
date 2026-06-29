/**
 * Civilization Theater — the main UI page.
 * Watch a goal get solved by a society, fully auditable.
 */
import { useState, useCallback } from 'react';
import { useParams, Link } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { civilizationApi } from '../../lib/api/civilizationApi';
import { useCivilizationStream } from '../../lib/sse/useCivilizationStream';
import { CivilizationMap } from './CivilizationMap';
import { CivilizationMetrics } from './CivilizationMetrics';
import { BlackboardFeed } from './BlackboardFeed';
import { LearningLedger } from './LearningLedger';
import { ControlBar } from './ControlBar';
import { AgentInspectorDrawer } from './AgentInspectorDrawer';
import { DebateViewer } from './DebateViewer';
import { ConstitutionEditor } from './ConstitutionEditor';
import { SpawnLineageTimeline } from './SpawnLineageTimeline';
import type { CivilizationEvent, Civilization } from '../../lib/api/civilizationApi';

type Panel = 'map' | 'blackboard' | 'learnings' | 'spawns' | 'debates' | 'constitution' | 'replay';

function CivilizationList() {
  const { data: civilizations, isLoading, error } = useQuery({
    queryKey: ['civilizations'],
    queryFn: () => civilizationApi.list(),
    refetchInterval: 5000,
  });

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64 text-muted-foreground">
        <div className="text-center">
          <div className="animate-spin text-3xl mb-2">&#9696;</div>
          <div className="text-sm">Loading civilizations...</div>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-64 text-red-500">
        <div className="text-center">
          <div className="text-3xl mb-2">&#9888;</div>
          <div className="text-sm">Failed to load civilizations. Is the backend running?</div>
        </div>
      </div>
    );
  }

  const civs = (civilizations as Civilization[] | undefined) ?? [];

  return (
    <div className="p-6 max-w-4xl mx-auto">
      <div className="mb-6">
        <h1 className="text-2xl font-bold">&#127760; Agent Civilizations</h1>
        <p className="text-muted-foreground text-sm mt-1">
          Select a civilization to enter the theater, or create a new one.
        </p>
      </div>

      {civs.length === 0 ? (
        <div className="border-2 border-dashed rounded-xl p-12 text-center text-muted-foreground">
          <div className="text-5xl mb-3">&#127759;</div>
          <div className="text-lg font-medium mb-1">No civilizations yet</div>
          <div className="text-sm">Create one from the backend or via the API to get started.</div>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {civs.map((civ: Civilization) => (
            <Link
              key={civ.id}
              to={`/civilization/${civ.id}`}
              className="border rounded-xl p-4 hover:border-blue-400 hover:shadow-md transition-all bg-card group"
            >
              <div className="flex items-start justify-between mb-3">
                <div>
                  <h2 className="font-bold text-foreground group-hover:text-blue-600 transition-colors">
                    {civ.name}
                  </h2>
                  <div className="text-xs text-muted-foreground mt-0.5">
                    {civ.id.slice(0, 16)}...
                  </div>
                </div>
                <span className={`text-xs px-2 py-1 rounded-full font-medium ${
                  civ.status === 'active' ? 'bg-green-100 text-green-700' :
                  civ.status === 'paused' ? 'bg-amber-100 text-amber-700' :
                  'bg-muted text-muted-foreground'
                }`}>
                  {civ.status}
                </span>
              </div>

              {civ.metrics && (
                <div className="grid grid-cols-3 gap-2 text-xs">
                  <div className="bg-blue-50 rounded p-2 text-center">
                    <div className="font-bold text-blue-700">{civ.metrics.active_members}</div>
                    <div className="text-blue-500">Active</div>
                  </div>
                  <div className="bg-purple-50 rounded p-2 text-center">
                    <div className="font-bold text-purple-700">{civ.metrics.total_members}</div>
                    <div className="text-purple-500">Total</div>
                  </div>
                  <div className="bg-amber-50 rounded p-2 text-center">
                    <div className="font-bold text-amber-700">
                      ${civ.metrics.total_budget_spent_usd.toFixed(2)}
                    </div>
                    <div className="text-amber-500">Spent</div>
                  </div>
                </div>
              )}

              <div className="mt-3 text-xs text-muted-foreground">
                Created {new Date(civ.created_at).toLocaleDateString()}
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}

function CivilizationTheater({ civId }: { civId: string }) {
  const qc = useQueryClient();
  const [activePanel, setActivePanel] = useState<Panel>('map');
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null);
  const [liveEvents, setLiveEvents] = useState<CivilizationEvent[]>([]);

  const { data: civ } = useQuery({
    queryKey: ['civilization', civId],
    queryFn: () => civilizationApi.get(civId),
    refetchInterval: 5000,
  });

  const { data: graph } = useQuery({
    queryKey: ['civilization-graph', civId],
    queryFn: () => civilizationApi.getGraph(civId),
    refetchInterval: 3000,
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
    refetchInterval: 4000,
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
    setLiveEvents(prev => [...prev.slice(-50), evt]);
    // Invalidate graph on spawn/retire
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

  const TABS: { key: Panel; label: string }[] = [
    { key: 'map', label: '🌐 Map & Metrics' },
    { key: 'blackboard', label: '📋 Blackboard' },
    { key: 'learnings', label: '🧠 Learning Ledger' },
    { key: 'spawns', label: '🌱 Spawn Audit' },
    { key: 'debates', label: '⚖️ Debates' },
    { key: 'constitution', label: '⚙️ Constitution' },
    { key: 'replay', label: '⏪ Replay' },
  ];

  return (
    <>
      <div className="flex flex-col h-screen bg-background">
        {/* Header */}
        <div className="bg-card border-b border-border px-6 py-3 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Link to="/civilization" className="text-muted-foreground hover:text-foreground/70 text-sm">
              &#8592; Civilizations
            </Link>
            <span className="text-border">/</span>
            <div>
              <h1 className="text-lg font-bold leading-tight">{civ?.name ?? 'Civilization'}</h1>
              <div className="text-xs text-muted-foreground">Agent Civilization Theater</div>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <div className={`w-2 h-2 rounded-full ${connected ? 'bg-green-500 animate-pulse' : 'bg-red-400'}`} />
            <span className="text-xs text-muted-foreground">{connected ? 'Live' : 'Reconnecting...'}</span>
            {civ?.metrics && (
              <div className="text-xs text-muted-foreground bg-muted rounded px-2 py-1">
                {civ.metrics.active_members} active &middot; {civ.metrics.total_members} total
              </div>
            )}
          </div>
        </div>

        {/* Control Bar */}
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

        {/* Main content */}
        <div className="flex flex-1 overflow-hidden">
          {/* Left: Map (always visible) */}
          <div className="flex-1 min-w-0 relative">
            <div className="h-full">
              <CivilizationMap
                nodes={graph?.nodes ?? []}
                edges={graph?.edges ?? []}
                onNodeClick={setSelectedAgentId}
                liveEvents={liveEvents}
              />
            </div>
            {/* Live event ticker */}
            {liveEvents.length > 0 && (
              <div className="absolute bottom-3 left-3 right-3 bg-black/60 text-white text-xs rounded p-2 max-h-16 overflow-hidden pointer-events-none">
                {liveEvents.slice(-3).reverse().map((e, i) => (
                  <div key={`${e.id}-${i}`} className="truncate opacity-80">
                    {e.ts?.slice(11, 19)} &middot; <span className="font-medium">{e.type}</span>
                    {(e.payload as Record<string, unknown>)?.agent_id
                      ? ` \u00b7 ${String((e.payload as Record<string, unknown>).agent_id).slice(0, 8)}`
                      : ''}
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Right: Detail panels */}
          <div className="w-96 border-l bg-card flex flex-col">
            {/* Tabs */}
            <div className="flex border-b overflow-x-auto scrollbar-none">
              {TABS.map(tab => (
                <button
                  key={tab.key}
                  onClick={() => setActivePanel(tab.key)}
                  className={`px-3 py-2 text-xs whitespace-nowrap transition-colors ${
                    activePanel === tab.key
                      ? 'border-b-2 border-blue-600 text-blue-600 font-medium'
                      : 'text-muted-foreground hover:text-foreground'
                  }`}
                >
                  {tab.label}
                </button>
              ))}
            </div>

            {/* Panel content */}
            <div className="flex-1 overflow-y-auto p-3">
              {activePanel === 'map' && civ?.metrics && (
                <CivilizationMetrics metrics={civ.metrics} />
              )}
              {activePanel === 'map' && !civ?.metrics && (
                <div className="text-sm text-muted-foreground text-center py-8">
                  Metrics will appear once agents are running.
                </div>
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
                <div className="space-y-1">
                  {liveEvents.length === 0 && (
                    <div className="text-sm text-muted-foreground text-center py-4">
                      Live events will appear here.
                    </div>
                  )}
                  {liveEvents.map((e, i) => (
                    <div key={`${e.id}-${i}`} className="flex gap-2 text-xs border-b pb-1">
                      <span className="text-muted-foreground whitespace-nowrap">{e.ts?.slice(11, 19)}</span>
                      <span className="text-blue-600 font-medium">{e.type}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
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
    </>
  );
}

export function CivilizationPage() {
  const { id: civId } = useParams<{ id: string }>();

  if (!civId) {
    return <CivilizationList />;
  }

  return <CivilizationTheater civId={civId} />;
}

export default CivilizationPage;
