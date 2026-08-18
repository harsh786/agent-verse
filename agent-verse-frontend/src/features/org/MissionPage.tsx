/**
 * MissionPage — full mission detail view with live execution.
 *
 * Sections:
 *  - Header: title, status, progress, cost, agents
 *  - Timeline: planning → research → execution → review → done
 *  - Team graph: agent nodes with live status
 *  - KanbanBoard: task board (TODO | IN_PROGRESS | REVIEW | DONE | APPROVED)
 *  - ArtifactGallery: versioned outputs
 *  - ApprovalQueue: pending approvals for this mission
 *  - Activity feed: real-time event stream
 */
import React, { useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import {
  ArrowLeft, Users, DollarSign, Clock, Target, CheckCircle2,
  AlertTriangle, Pause, Play, StopCircle, BarChart3,
} from 'lucide-react';
// JARVIS palette — electric #00D4FF surfaces #0F1826 #0A0F1A
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Progress } from '@/components/ui/progress';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useMission, useOrgTasks } from './hooks/useOrg';
import { KanbanBoard } from './KanbanBoard';
import { ArtifactGallery } from './ArtifactGallery';
import { ActivityFeed } from './components/ActivityFeed';
import { JARVISPageShell, JARVISStagger, JARVISStaggerItem } from '@/components/ui/JARVISPageShell';
import type { MissionStatus } from './types';

// ── Status config ──────────────────────────────────────────────────────────

const STATUS_CONFIG: Record<MissionStatus, { label: string; color: string; icon: React.ElementType }> = {
  draft:      { label: 'Draft',       color: 'text-[var(--text-muted)]',  icon: Clock         },
  queued:     { label: 'Queued',      color: 'text-yellow-400',           icon: Clock         },
  planned:    { label: 'Planned',     color: 'text-blue-400',             icon: BarChart3     },
  active:     { label: 'Active',      color: 'text-emerald-400',          icon: Play          },
  paused:     { label: 'Paused',      color: 'text-yellow-400',           icon: Pause         },
  review:     { label: 'In Review',   color: 'text-indigo-400',           icon: BarChart3     },
  completed:  { label: 'Completed',   color: 'text-emerald-500',          icon: CheckCircle2  },
  failed:     { label: 'Failed',      color: 'text-red-400',              icon: AlertTriangle },
  cancelled:  { label: 'Cancelled',   color: 'text-[var(--text-muted)]',  icon: StopCircle    },
  archived:   { label: 'Archived',    color: 'text-[var(--text-muted)]',  icon: Clock         },
};

const RISK_COLOR: Record<string, string> = {
  low: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20',
  medium: 'bg-yellow-500/10 text-yellow-400 border-yellow-500/20',
  high: 'bg-orange-500/10 text-orange-400 border-orange-500/20',
  critical: 'bg-red-500/10 text-red-400 border-red-500/20',
};

// ── Timeline ───────────────────────────────────────────────────────────────

const TIMELINE_PHASES = ['Planning', 'Research', 'Execution', 'Review', 'Complete'];

function MissionTimeline({ progress }: { progress: number }) {
  const activeIndex = Math.floor((progress / 100) * (TIMELINE_PHASES.length - 1));
  return (
    <div className="flex items-center gap-1" role="list" aria-label="Mission phases">
      {TIMELINE_PHASES.map((phase, idx) => (
        <div key={phase} className="flex items-center gap-1">
          <div
            role="listitem"
            aria-current={idx === activeIndex ? 'step' : undefined}
            className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs transition-colors
              ${idx < activeIndex ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/20'
              : idx === activeIndex ? 'bg-[var(--accent-blue)]/20 text-[var(--accent-blue)] border border-[var(--accent-blue)]/30'
              : 'bg-[var(--bg-surface)] text-[var(--text-muted)] border border-[var(--border)]'}`}
          >
            {idx < activeIndex && <CheckCircle2 className="h-3 w-3" aria-hidden="true" />}
            {idx === activeIndex && <span className="w-2 h-2 rounded-full bg-current animate-pulse" aria-hidden="true" />}
            {phase}
          </div>
          {idx < TIMELINE_PHASES.length - 1 && (
            <div className={`h-px w-4 ${idx < activeIndex ? 'bg-emerald-500' : 'bg-[var(--border)]'}`} aria-hidden="true" />
          )}
        </div>
      ))}
    </div>
  );
}

// ── Main component ─────────────────────────────────────────────────────────

interface MissionPageProps {
  orgId?: string;
  missionId?: string;
}

export function MissionPage({ orgId: orgIdProp, missionId: missionIdProp }: MissionPageProps = {}) {
  const params = useParams<{ orgId: string; missionId: string }>();
  const orgId = orgIdProp ?? params.orgId ?? '';
  const missionId = missionIdProp ?? params.missionId ?? '';
  const navigate = useNavigate();
  const { data: mission, isLoading } = useMission(orgId, missionId);
  const { data: tasksPage } = useOrgTasks(orgId, { mission_id: missionId });
  const [activeTab, setActiveTab] = useState('tasks');

  if (isLoading) {
    return (
      <JARVISPageShell className="flex items-center justify-center min-h-screen bg-[#0A0F1A]">
        <div aria-live="polite" className="text-[#5A7494] text-sm">Loading mission…</div>
      </JARVISPageShell>
    );
  }

  if (!mission) {
    return (
      <div className="flex items-center justify-center min-h-screen bg-[var(--bg-base)]">
        <div className="text-[var(--text-muted)] text-sm">Mission not found.</div>
      </div>
    );
  }

  const statusConfig = STATUS_CONFIG[mission.status] ?? STATUS_CONFIG.draft;
  const StatusIcon = statusConfig.icon;
  const tasks = (tasksPage as any)?.data ?? tasksPage ?? [];
  const completedTasks = tasks.filter((t: any) => t.status === 'completed').length;
  const totalTasks = tasks.length;
  const progress = totalTasks > 0 ? Math.round((completedTasks / totalTasks) * 100) : 0;
  const riskClass = RISK_COLOR[(mission as any).risk_level ?? 'medium'] ?? RISK_COLOR.medium;

  return (
    <JARVISPageShell className="min-h-screen bg-[#0A0F1A] text-[#F0F6FF]">
      <div aria-live="polite" aria-atomic="true" className="sr-only">{isLoading ? 'Loading…' : ''}</div>
      <div className="max-w-6xl mx-auto px-6 py-6 space-y-6">
        {/* ── Header ───────────────────────────────────────────────────── */}
        <div className="flex items-start gap-4">
          <Button variant="ghost" size="icon" onClick={() => navigate(-1)} aria-label="Go back">
            <ArrowLeft className="h-4 w-4" />
          </Button>

          <div className="flex-1 min-w-0">
            <div className="flex items-start justify-between gap-4 flex-wrap">
              <div className="min-w-0">
                <h1 className="text-xl font-bold text-[var(--text-primary)] truncate">
                  {(mission as any).title ?? 'Untitled Mission'}
                </h1>
                <p className="text-sm text-[var(--text-muted)] mt-0.5 line-clamp-2">
                  {(mission as any).goal_text ?? ''}
                </p>
              </div>

              <div className="flex items-center gap-2 flex-shrink-0">
                <Badge variant="outline" className={`border text-xs ${riskClass}`}>
                  {((mission as any).risk_level ?? 'medium').toUpperCase()}
                </Badge>
                <Badge
                  variant="outline"
                  className={`flex items-center gap-1 text-xs ${statusConfig.color}`}
                >
                  <StatusIcon className="h-3 w-3" aria-hidden="true" />
                  {statusConfig.label}
                </Badge>
              </div>
            </div>

            {/* Metrics row */}
            <div className="flex items-center gap-5 mt-3 text-sm flex-wrap">
              <div className="flex items-center gap-1.5 text-[var(--text-muted)]">
                <Users className="h-3.5 w-3.5" aria-hidden="true" />
                <span>{(mission as any).agent_count ?? 0} agents</span>
              </div>
              <div className="flex items-center gap-1.5 text-[var(--text-muted)]">
                <DollarSign className="h-3.5 w-3.5 text-emerald-400" aria-hidden="true" />
                <span className="text-emerald-400">
                  ${parseFloat(String((mission as any).spent_usd ?? 0)).toFixed(2)}
                  {(mission as any).budget_usd ? ` / $${(mission as any).budget_usd}` : ''}
                </span>
              </div>
              <div className="flex items-center gap-1.5 text-[var(--text-muted)]">
                <Target className="h-3.5 w-3.5" aria-hidden="true" />
                <span>{completedTasks}/{totalTasks} tasks</span>
              </div>
            </div>

            {/* Progress */}
            <div className="mt-3 space-y-1.5">
              <div className="flex justify-between text-xs text-[var(--text-muted)]">
                <span>Progress</span>
                <span>{progress}%</span>
              </div>
              <Progress value={progress} className="h-1.5" aria-label={`Mission progress: ${progress}%`} />
            </div>

            {/* Timeline */}
            <div className="mt-3 overflow-x-auto">
              <MissionTimeline progress={progress} />
            </div>
          </div>
        </div>

        {/* ── Tabs ─────────────────────────────────────────────────────── */}
        <Tabs value={activeTab} onValueChange={setActiveTab}>
          <TabsList className="bg-[var(--bg-surface)]">
            <TabsTrigger value="tasks">Tasks ({totalTasks})</TabsTrigger>
            <TabsTrigger value="artifacts">Artifacts</TabsTrigger>
            <TabsTrigger value="activity">Activity</TabsTrigger>
          </TabsList>

          <TabsContent value="tasks" className="mt-4">
            <KanbanBoard orgId={orgId} missionId={missionId} />
          </TabsContent>

          <TabsContent value="artifacts" className="mt-4">
            <ArtifactGallery orgId={orgId} missionId={missionId} />
          </TabsContent>

          <TabsContent value="activity" className="mt-4">
            <div className="h-[500px] bg-[var(--bg-card)] rounded-xl border border-[var(--border)] overflow-hidden">
              <ActivityFeed orgId={orgId} />
            </div>
          </TabsContent>
        </Tabs>
      </div>
    </JARVISPageShell>
  );
}
