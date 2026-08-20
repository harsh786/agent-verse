/**
 * OrgMissionControlPage — JARVIS Mission Control Center (FLAGSHIP).
 * Spec §3.1: Three-panel layout — Agent Constellation + Command Log + Inspector.
 * Replaces the existing OrgPage split for a full neural visualization experience.
 */
import { useState, useCallback } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Building2, RefreshCw } from 'lucide-react';
import { JARVISPageShell } from '@/components/ui/JARVISPageShell';
import { AgentConstellation } from './components/AgentConstellation';
import { MissionCommandLog } from './components/MissionCommandLog';
import { MissionInspectorPanel } from './components/MissionInspectorPanel';
import { OrgHealthWidget } from './components/OrgHealthWidget';
import { useOrganization, useOrgHealth, useMissions } from './hooks/useOrg';
import { useOrgNeuralState } from './hooks/useOrgNeuralState';
import type { OrgMission } from './types';
import { cn } from '@/lib/utils';

interface OrgMissionControlPageProps {
  orgId: string;
  className?: string;
}

export function OrgMissionControlPage({ orgId, className }: OrgMissionControlPageProps) {
  const [selectedAgent,   setSelectedAgent]   = useState<string | null>(null);
  const [selectedMission, setSelectedMission] = useState<string | null>(null);

  const { data: org,    refetch }  = useOrganization(orgId);
  const { data: health }           = useOrgHealth(orgId);
  const { data: missionInf }       = useMissions(orgId, { status: 'active' });
  const neuralState                = useOrgNeuralState(orgId);

  const activeMissions: OrgMission[] = (missionInf?.pages?.flatMap(p => p.data ?? []) ?? [])
    .filter((m: OrgMission) => m.status === 'active');

  const handleAgentSelect   = useCallback((id: string | null) => { setSelectedAgent(id); setSelectedMission(null); }, []);
  const handleMissionSelect = useCallback((id: string | null) => { setSelectedMission(id); setSelectedAgent(null); }, []);
  const closeInspector      = useCallback(() => { setSelectedAgent(null); setSelectedMission(null); }, []);

  const hasInspector = !!(selectedAgent || selectedMission);

  return (
    <JARVISPageShell className={cn('flex flex-col h-full bg-[#020408] overflow-hidden', className)}>
      {/* Skip nav */}
      <a href="#mission-control-main"
        className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-[9999] focus:px-3 focus:py-1.5 focus:bg-[#00D4FF] focus:text-[#020408] focus:rounded-lg text-xs font-medium">
        Skip to main content
      </a>

      {/* Top bar */}
      <header className="flex items-center justify-between px-4 py-3 border-b border-white/[0.06] shrink-0">
        <div className="flex items-center gap-3 min-w-0">
          <div className="relative">
            <div className="h-8 w-8 rounded-lg bg-[#00D4FF]/10 border border-[#00D4FF]/20 flex items-center justify-center"
              style={{ boxShadow: '0 0 12px rgba(0,212,255,0.20)' }}>
              <Building2 className="h-4 w-4 text-[#00D4FF]" aria-hidden />
            </div>
            <span className="absolute -top-0.5 -right-0.5 h-2 w-2 rounded-full bg-emerald-400 animate-pulse" aria-hidden />
          </div>
          <div className="min-w-0">
            <h1 className="text-[14px] font-semibold text-[#F0F6FF] truncate tracking-tight">
              {org?.name ?? 'Mission Control'}
            </h1>
            <p className="text-[10px] text-[#5A7494]">JARVIS Neural Operations</p>
          </div>
        </div>

        <div className="flex items-center gap-2 shrink-0">
          {/* Health summary chips */}
          {(health as any)?.active_missions > 0 && (
            <span className="text-[10px] px-2 py-0.5 rounded-full bg-[#00D4FF]/10 text-[#00D4FF] border border-[#00D4FF]/20 font-mono">
              {(health as any).active_missions} active
            </span>
          )}
          {(health as any)?.pending_approvals > 0 && (
            <motion.span
              className="text-[10px] px-2 py-0.5 rounded-full bg-[#FFB300]/10 text-[#FFB300] border border-[#FFB300]/20 font-mono"
              animate={{ opacity: [1, 0.5, 1] }}
              transition={{ duration: 1.5, repeat: Infinity }}
            >
              ⏳ {(health as any).pending_approvals}
            </motion.span>
          )}
          <button
            onClick={() => refetch()}
            className="p-1.5 rounded-lg text-[#5A7494] hover:text-[#F0F6FF] hover:bg-white/[0.06] transition-colors"
            aria-label="Refresh"
          >
            <RefreshCw className="h-3.5 w-3.5" />
          </button>
        </div>
      </header>

      {/* Health metrics */}
      <div className="px-4 py-2 border-b border-white/[0.04] shrink-0">
        <OrgHealthWidget orgId={orgId} />
      </div>

      {/* Three-panel body */}
      <div id="mission-control-main" className="flex flex-1 min-h-0 overflow-hidden">

        {/* LEFT: Agent Constellation (55%) */}
        <div className={cn(
          'flex-1 min-w-0 flex items-center justify-center p-4 transition-all',
          hasInspector ? 'basis-[50%]' : 'basis-[60%]',
        )}>
          <AgentConstellation
            orgId={orgId}
            missions={activeMissions}
            agents={neuralState.agents.map(a => ({
              id: a.id, label: a.label, role: a.role,
              status: a.state === 'executing' || a.state === 'communicating' ? 'active' :
                      a.state === 'error' || a.state === 'blocked' ? 'error' : 'idle',
              goalCount: a.goalCount,
            }))}
            communicatingPairs={neuralState.communicatingPairs}
            onAgentSelect={handleAgentSelect}
            onMissionSelect={handleMissionSelect}
            selectedAgentId={selectedAgent}
            className="max-w-full max-h-full"
          />
        </div>

        {/* MIDDLE: Mission Command Log (25%) */}
        <div className={cn(
          'border-l border-white/[0.06] flex flex-col transition-all overflow-hidden',
          hasInspector ? 'basis-[25%]' : 'basis-[40%]',
        )}>
          <MissionCommandLog
            orgId={orgId}
            missionId={selectedMission ?? undefined}
            className="flex-1 rounded-none border-none"
          />
        </div>

        {/* RIGHT: Inspector Drawer (20%) */}
        <AnimatePresence>
          {hasInspector && (
            <MissionInspectorPanel
              key="inspector"
              orgId={orgId}
              missionId={selectedMission}
              agentId={selectedAgent}
              onClose={closeInspector}
              className="basis-[25%] shrink-0"
            />
          )}
        </AnimatePresence>
      </div>
    </JARVISPageShell>
  );
}
