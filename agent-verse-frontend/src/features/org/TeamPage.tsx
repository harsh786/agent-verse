/**
 * TeamPage — active team view with kanban + member feed.
 *
 * JARVIS motion system:
 *   - JARVISPageShell:  page entry blur-in
 *   - JARVISStagger:    member card stagger
 *   - SPRING_PANEL:     detail panel slide
 *   - AnimatePresence:  tab transition
 */
import { useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { motion, AnimatePresence } from 'framer-motion';
import {
  ArrowLeft, Users,
  User, Clock, Zap,
  ChevronRight,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import {
  JARVISPageShell, JARVISStagger, JARVISStaggerItem,
  SPRING_FAST,
} from '@/components/ui/JARVISPageShell';
import { KanbanBoard } from './KanbanBoard';
import { ActivityFeed } from './components/ActivityFeed';
import { useOrgTasks, useOrgEvents, useTeamMembers } from './hooks/useOrg';

// ── Member status ──────────────────────────────────────────────────────────

const AGENT_STATUS: Record<string, { dot: string; label: string; text: string }> = {
  idle:      { dot: 'bg-[#475569]',                     label: 'Idle',      text: 'text-[#475569]'  },
  executing: { dot: 'bg-emerald-400 animate-pulse-glow', label: 'Working',   text: 'text-emerald-400' },
  planning:  { dot: 'bg-indigo-400',                    label: 'Planning',  text: 'text-indigo-400' },
  waiting:   { dot: 'bg-yellow-400',                    label: 'Waiting',   text: 'text-yellow-400' },
  blocked:   { dot: 'bg-red-400 animate-pulse',         label: 'Blocked',   text: 'text-red-400'    },
  completed: { dot: 'bg-emerald-500',                   label: 'Done',      text: 'text-emerald-500' },
};

interface TeamMember {
  id: string;
  name: string;
  role?: string;
  status?: string;
  current_task?: string;
}

function MemberCard({ member, onClick }: { member: TeamMember; onClick?: () => void }) {
  const conf = AGENT_STATUS[member.status ?? 'idle'] ?? AGENT_STATUS.idle;
  return (
    <motion.button
      layout
      initial={false}
      animate={{ opacity: 1, y: 0 }}
      transition={SPRING_FAST}
      onClick={onClick}
      style={{ touchAction: 'manipulation' }}
      className="w-full flex items-center gap-3 px-4 py-3 rounded-xl bg-[#0F1623] border border-[#1E2535] hover:border-[#00D4FF]/20 hover:shadow-glow-electric transition-all group text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00D4FF]/50"
      aria-label={`Agent ${member.name}: ${conf.label}`}
    >
      <div className="relative flex-shrink-0">
        <div className="h-8 w-8 rounded-lg bg-[#1A1F2E] border border-[#1E2535] flex items-center justify-center">
          <User className="h-4 w-4 text-[#475569] group-hover:text-[#00D4FF] transition-colors" aria-hidden />
        </div>
        <span className={cn('absolute -bottom-0.5 -right-0.5 h-2.5 w-2.5 rounded-full border-2 border-[#0F1623]', conf.dot)} aria-hidden />
      </div>

      <div className="flex-1 min-w-0">
        <div className="flex items-center justify-between">
          <span className="text-sm font-medium text-[#F1F5F9] truncate">{member.name}</span>
          <span className={cn('text-[10px] font-medium ml-2 flex-shrink-0', conf.text)}>{conf.label}</span>
        </div>
        {member.role && (
          <p className="text-[11px] text-[#475569] truncate">{member.role}</p>
        )}
        {member.current_task && (
          <p className="text-[11px] text-[#94A3B8] truncate mt-0.5 italic">"{member.current_task}"</p>
        )}
      </div>

      <ChevronRight className="h-3.5 w-3.5 text-[#475569] opacity-0 group-hover:opacity-100 transition-opacity flex-shrink-0" aria-hidden />
    </motion.button>
  );
}

// ── Main ───────────────────────────────────────────────────────────────────

export function TeamPage() {
  const { orgId, teamId } = useParams<{ orgId: string; teamId: string }>();
  const navigate = useNavigate();

  const [tab, setTab] = useState<'members' | 'kanban' | 'feed'>('members');

  // For a real team, we'd fetch team-specific data. Using org tasks as approximation.
  useOrgTasks(orgId, {});
  useOrgEvents(orgId);
  const { data: teamMembersResponse } = useTeamMembers(orgId, teamId);

  const members: TeamMember[] = teamMembersResponse?.members ?? [];

  const TABS = [
    { id: 'members', label: `Members (${members.length})` },
    { id: 'kanban',  label: 'Task Board'                  },
    { id: 'feed',    label: 'Activity'                    },
  ] as const;

  return (
    <JARVISPageShell className="flex flex-col h-full bg-[#0A0D14] overflow-hidden">
      {/* ── Header ── */}
      <header className="flex items-center gap-3 px-6 py-4 border-b border-[#1E2535] shrink-0">
        <button
          onClick={() => navigate(-1)}
          aria-label="Back"
          style={{ touchAction: 'manipulation' }}
          className="p-2 rounded-lg text-[#475569] hover:text-[#94A3B8] hover:bg-[#1A1F2E] transition-colors min-w-[44px] min-h-[44px] flex items-center justify-center focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00D4FF]/50"
        >
          <ArrowLeft className="h-4 w-4" aria-hidden />
        </button>

        <div className="h-9 w-9 rounded-xl bg-indigo-500/10 border border-indigo-500/20 flex items-center justify-center flex-shrink-0" aria-hidden>
          <Users className="h-4 w-4 text-indigo-400" />
        </div>

        <div className="min-w-0 flex-1">
          <h1 className="text-base font-semibold text-[#F1F5F9] truncate">Mission Team</h1>
          <p className="text-[11px] text-[#475569]">{members.length} agents</p>
        </div>

        <div className="flex items-center gap-1.5">
          <span className="h-2 w-2 rounded-full bg-emerald-400 animate-pulse" aria-hidden />
          <span className="text-[11px] text-[#475569]">Active</span>
        </div>
      </header>

      {/* ── Stats ── */}
      <div className="grid grid-cols-3 gap-px bg-[#1E2535] shrink-0">
        {[
          { icon: Users, value: members.length, label: 'Agents' },
          { icon: Zap,   value: members.filter(m => m.status === 'executing').length, label: 'Working' },
          { icon: Clock, value: members.filter(m => m.status === 'waiting' || m.status === 'blocked').length, label: 'Blocked' },
        ].map(({ icon: Icon, value, label }) => (
          <div key={label} className="bg-[#0A0D14] px-4 py-3 flex items-center gap-3">
            <Icon className="h-4 w-4 text-[#475569]" aria-hidden />
            <div>
              <p className="text-lg font-bold text-[#F1F5F9]">{value}</p>
              <p className="text-[10px] text-[#475569]">{label}</p>
            </div>
          </div>
        ))}
      </div>

      {/* ── Tabs ── */}
      <div
        className="flex gap-1 px-6 py-2 border-b border-[#1E2535] shrink-0"
        role="tablist"
        aria-label="Team sections"
      >
        {TABS.map(t => (
          <button
            key={t.id}
            role="tab"
            aria-selected={tab === t.id}
            onClick={() => setTab(t.id)}
            style={{ touchAction: 'manipulation' }}
            className={cn(
              'px-3 py-1.5 rounded-lg text-xs font-medium transition-colors',
              'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00D4FF]/50',
              tab === t.id
                ? 'bg-[#00D4FF]/10 text-[#00D4FF]'
                : 'text-[#475569] hover:text-[#94A3B8] hover:bg-[#1A1F2E]',
            )}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* ── Content ── */}
      <div className="flex-1 overflow-hidden" role="tabpanel">
        <AnimatePresence mode="wait">
          {tab === 'members' && (
            <motion.div
              key="members"
              initial={false}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              transition={SPRING_FAST}
              className="h-full overflow-y-auto px-6 py-4"
            >
              {members.length === 0 ? (
                <div className="text-sm text-[#94A3B8]">No team members available yet.</div>
              ) : (
                <JARVISStagger className="space-y-2">
                  {members.map(m => (
                    <JARVISStaggerItem key={m.id} interactive>
                      <MemberCard member={m} />
                    </JARVISStaggerItem>
                  ))}
                </JARVISStagger>
              )}
            </motion.div>
          )}

          {tab === 'kanban' && orgId && (
            <motion.div
              key="kanban"
              initial={false}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              transition={SPRING_FAST}
              className="h-full"
            >
              <KanbanBoard orgId={orgId} />
            </motion.div>
          )}

          {tab === 'feed' && orgId && (
            <motion.div
              key="feed"
              initial={false}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              transition={SPRING_FAST}
              className="h-full px-6 py-4"
            >
              <ActivityFeed orgId={orgId} className="h-full" />
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </JARVISPageShell>
  );
}

export default TeamPage;
