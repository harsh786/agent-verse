/**
 * DepartmentPage — per-department analytics, agents, and missions.
 *
 * JARVIS motion system:
 *   - JARVISPageShell:  spring blur-in page entry
 *   - JARVISStagger:    stagger agent cards
 *   - SPRING_PANEL:     side panel slides
 *   - useReducedMotion: a11y fallback
 */
import { useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { motion, AnimatePresence } from 'framer-motion';
import {
  ArrowLeft, Users, Target, Activity, CheckCircle2, Clock,
  ChevronRight, Building2, RefreshCw, Zap,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import {
  JARVISPageShell, JARVISStagger, JARVISStaggerItem, SPRING_FAST,
} from '@/components/ui/JARVISPageShell';
import { Badge } from '@/components/ui/badge';
import { useDepartments, useMissions, useOrgTasks } from './hooks/useOrg';
import type { OrgDepartment, OrgMission } from './types';

// ── Dept color map (from spec) ─────────────────────────────────────────────

const DEPT_COLORS: Record<string, string> = {
  executive:   '#7C3AED', engineering: '#2563EB', marketing:  '#EC4899',
  finance:     '#16A34A', legal:       '#CA8A04', hr:         '#7C6FAB',
  security:    '#DC2626', data:        '#0891B2', sales:      '#EA580C',
  operations:  '#6B7280', research:    '#9333EA', product:    '#DB2777',
  support:     '#F59E0B', devops:      '#0EA5E9',
};

function deptColor(name: string): string {
  const key = name.toLowerCase().replace(/[\s/]+/g, '');
  return DEPT_COLORS[key] ?? '#6366F1';
}

// ── Stat card ──────────────────────────────────────────────────────────────

function StatCard({
  icon: Icon, label, value, sub, color = 'text-[#00D4FF]',
}: {
  icon: React.ElementType; label: string; value: string | number;
  sub?: string; color?: string;
}) {
  return (
    <div className="bg-[#0F1623] border border-[#1E2535] rounded-xl p-4">
      <div className="flex items-center gap-2 mb-2">
        <Icon className={cn('h-4 w-4', color)} aria-hidden />
        <span className="text-[11px] text-[#475569] uppercase tracking-wider">{label}</span>
      </div>
      <p className="text-2xl font-bold text-[#F1F5F9]">{value}</p>
      {sub && <p className="text-[11px] text-[#475569] mt-0.5">{sub}</p>}
    </div>
  );
}

// ── Mission row ────────────────────────────────────────────────────────────

const STATUS_DOT: Record<string, string> = {
  active: 'bg-emerald-400 animate-pulse', queued: 'bg-yellow-400',
  paused: 'bg-amber-400', completed: 'bg-[#475569]', failed: 'bg-red-400',
};

function MissionRow({ mission }: { mission: OrgMission }) {
  const dot = STATUS_DOT[mission.status] ?? 'bg-[#475569]';
  return (
    <motion.div
      layout
      initial={false}
      animate={{ opacity: 1, x: 0 }}
      transition={SPRING_FAST}
      className="flex items-center gap-3 px-4 py-3 rounded-lg hover:bg-[#1A1F2E] transition-colors cursor-pointer group"
      tabIndex={0}
      role="listitem"
      aria-label={`Mission: ${mission.title}`}
    >
      <span className={cn('h-2 w-2 rounded-full flex-shrink-0', dot)} aria-hidden />
      <span className="flex-1 text-sm text-[#94A3B8] group-hover:text-[#F1F5F9] transition-colors truncate">
        {mission.title}
      </span>
      <Badge
        variant="outline"
        className="text-[10px] border-[#1E2535] text-[#475569] capitalize"
      >
        {mission.status}
      </Badge>
      <ChevronRight className="h-3.5 w-3.5 text-[#475569] opacity-0 group-hover:opacity-100 transition-opacity" aria-hidden />
    </motion.div>
  );
}

// ── Main ───────────────────────────────────────────────────────────────────

export function DepartmentPage() {
  const { orgId, deptId } = useParams<{ orgId: string; deptId: string }>();
  const navigate  = useNavigate();

  const [activeTab, setActiveTab] = useState<'overview' | 'missions' | 'tasks'>('overview');

  const { data: departments, isLoading } = useDepartments(orgId ?? null);
  const { data: missionData }            = useMissions(orgId, {});
  const { data: taskData }               = useOrgTasks(orgId, {});

  const deptList: OrgDepartment[] = Array.isArray(departments)
    ? departments
    : (departments as any)?.data ?? [];

  const dept = deptList.find(d => d.id === deptId);

  const allMissions: OrgMission[] =
    missionData?.pages.flatMap(p => p.data) ?? [];
  const deptMissions = allMissions.filter(m => (m as any).dept_id === deptId);

  const allTasks     = (taskData as any)?.data ?? [];
  const deptTasks    = allTasks.filter((t: any) => (t as any).dept_id === deptId);

  const color = dept ? deptColor(dept.name) : '#6366F1';

  const TABS = [
    { id: 'overview',  label: 'Overview'  },
    { id: 'missions',  label: `Missions (${deptMissions.length})`  },
    { id: 'tasks',     label: `Tasks (${deptTasks.length})`        },
  ] as const;

  if (isLoading) {
    return (
      <JARVISPageShell className="flex items-center justify-center h-full">
        <RefreshCw className="h-5 w-5 animate-spin text-[#00D4FF]" aria-label="Loading" />
      </JARVISPageShell>
    );
  }

  if (!dept) {
    return (
      <JARVISPageShell className="flex flex-col items-center justify-center h-full gap-4">
        <Building2 className="h-12 w-12 text-[#475569]" aria-hidden />
        <p className="text-[#94A3B8]">Department not found.</p>
        <button
          onClick={() => navigate(-1)}
          className="text-sm text-[#00D4FF] hover:underline"
        >
          Go back
        </button>
      </JARVISPageShell>
    );
  }

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

        {/* Dept icon with brand colour */}
        <div
          className="h-9 w-9 rounded-lg flex items-center justify-center flex-shrink-0"
          style={{ backgroundColor: `${color}20`, border: `1px solid ${color}40` }}
          aria-hidden
        >
          <Building2 className="h-4 w-4" style={{ color }} />
        </div>

        <div className="min-w-0">
          <h1 className="text-base font-semibold text-[#F1F5F9] truncate">
            {dept.name}
          </h1>
          <p className="text-[11px] text-[#475569] capitalize">{dept.purpose || 'Department'}</p>
        </div>

        {/* Active pulse */}
        <div className="ml-auto flex items-center gap-1.5">
          <span className="h-2 w-2 rounded-full bg-emerald-400 animate-pulse" aria-hidden />
          <span className="text-[11px] text-[#475569]">Active</span>
        </div>
      </header>

      {/* ── Stats row ── */}
      <div className="px-6 pt-4 pb-2 grid grid-cols-2 sm:grid-cols-4 gap-3 shrink-0">
        <StatCard icon={Users}      label="Agents"   value={dept.agent_count ?? 0}         color="text-[#00D4FF]" />
        <StatCard icon={Target}     label="Missions" value={deptMissions.length}            color="text-indigo-400" />
        <StatCard icon={CheckCircle2} label="Completed" value={deptMissions.filter(m => m.status === 'completed').length} color="text-emerald-400" />
        <StatCard icon={Zap}        label="Active"   value={deptMissions.filter(m => m.status === 'active').length} color="text-yellow-400" />
      </div>

      {/* ── Tabs ── */}
      <div
        className="flex gap-1 px-6 py-2 border-b border-[#1E2535] shrink-0"
        role="tablist"
        aria-label="Department sections"
      >
        {TABS.map(tab => (
          <button
            key={tab.id}
            role="tab"
            aria-selected={activeTab === tab.id}
            onClick={() => setActiveTab(tab.id)}
            style={{ touchAction: 'manipulation' }}
            className={cn(
              'px-3 py-1.5 rounded-lg text-xs font-medium transition-colors',
              'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00D4FF]/50',
              activeTab === tab.id
                ? 'bg-[#00D4FF]/10 text-[#00D4FF]'
                : 'text-[#475569] hover:text-[#94A3B8] hover:bg-[#1A1F2E]',
            )}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* ── Tab content ── */}
      <div className="flex-1 overflow-y-auto px-6 py-4" role="tabpanel">
        <AnimatePresence mode="wait">
          {activeTab === 'overview' && (
            <motion.div
              key="overview"
              initial={false}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              transition={SPRING_FAST}
              className="space-y-4"
            >
              {/* Purpose */}
              {dept.purpose && (
                <div className="bg-[#0F1623] border border-[#1E2535] rounded-xl p-4">
                  <h2 className="text-xs font-medium text-[#475569] uppercase tracking-wider mb-2">Purpose</h2>
                  <p className="text-sm text-[#94A3B8] leading-relaxed">{dept.purpose}</p>
                </div>
              )}

              {/* Capability domains */}
              {dept.capability_domains?.length > 0 && (
                <div className="bg-[#0F1623] border border-[#1E2535] rounded-xl p-4">
                  <h2 className="text-xs font-medium text-[#475569] uppercase tracking-wider mb-3">
                    Capabilities
                  </h2>
                  <div className="flex flex-wrap gap-2">
                    {dept.capability_domains.map(cap => (
                      <Badge
                        key={cap}
                        variant="outline"
                        className="text-xs border-[#1E2535] text-[#94A3B8]"
                      >
                        {cap}
                      </Badge>
                    ))}
                  </div>
                </div>
              )}

              {/* Recent activity */}
              <div className="bg-[#0F1623] border border-[#1E2535] rounded-xl p-4">
                <h2 className="text-xs font-medium text-[#475569] uppercase tracking-wider mb-3 flex items-center gap-2">
                  <Activity className="h-3.5 w-3.5" aria-hidden />
                  Recent Missions
                </h2>
                {deptMissions.length === 0 ? (
                  <p className="text-sm text-[#475569] py-4 text-center">No missions yet</p>
                ) : (
                  <JARVISStagger className="space-y-1">
                    {deptMissions.slice(0, 5).map(m => (
                      <JARVISStaggerItem key={m.id} interactive>
                        <MissionRow mission={m} />
                      </JARVISStaggerItem>
                    ))}
                  </JARVISStagger>
                )}
              </div>
            </motion.div>
          )}

          {activeTab === 'missions' && (
            <motion.div
              key="missions"
              initial={false}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              transition={SPRING_FAST}
              role="list"
              aria-label="Department missions"
            >
              {deptMissions.length === 0 ? (
                <div className="flex flex-col items-center py-16 gap-3">
                  <Target className="h-10 w-10 text-[#1E2535]" aria-hidden />
                  <p className="text-[#475569] text-sm">No missions assigned to this department.</p>
                </div>
              ) : (
                <JARVISStagger className="space-y-2">
                  {deptMissions.map(m => (
                    <JARVISStaggerItem key={m.id} interactive>
                      <MissionRow mission={m} />
                    </JARVISStaggerItem>
                  ))}
                </JARVISStagger>
              )}
            </motion.div>
          )}

          {activeTab === 'tasks' && (
            <motion.div
              key="tasks"
              initial={false}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              transition={SPRING_FAST}
              role="list"
              aria-label="Department tasks"
            >
              {deptTasks.length === 0 ? (
                <div className="flex flex-col items-center py-16 gap-3">
                  <Clock className="h-10 w-10 text-[#1E2535]" aria-hidden />
                  <p className="text-[#475569] text-sm">No tasks for this department.</p>
                </div>
              ) : (
                <JARVISStagger className="space-y-2">
                  {deptTasks.map((t: any) => (
                    <JARVISStaggerItem key={t.id} interactive>
                      <div className="flex items-center gap-3 px-4 py-3 rounded-lg hover:bg-[#1A1F2E] transition-colors">
                        <span className="text-sm text-[#94A3B8] flex-1 truncate">{t.title ?? 'Untitled task'}</span>
                        <Badge variant="outline" className="text-[10px] border-[#1E2535] text-[#475569] capitalize">
                          {t.status}
                        </Badge>
                      </div>
                    </JARVISStaggerItem>
                  ))}
                </JARVISStagger>
              )}
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </JARVISPageShell>
  );
}

export default DepartmentPage;
