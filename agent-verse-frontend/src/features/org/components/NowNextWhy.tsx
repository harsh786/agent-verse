/**
 * NowNextWhy — N12: Three-panel temporal dashboard for the Command Center.
 *
 * NOW:  Active missions, tasks, teams, agents + live cost meter
 * NEXT: Upcoming deadlines, pending approvals (SLA countdown), scheduled ops
 * WHY:  Autonomous action reasoning + decision transparency
 *
 * Skills:
 *   frontend-design:   JARVIS dark tri-panel, pulse on active items
 *   emil-design-eng:   spring 280/26 panel, stagger item entrance
 *   impeccable-ui:     section label dominant, count secondary
 *   web-guidelines:    role=tablist (panels), aria-live NOW counts, time[datetime]
 *   ui-ux-pro-max:     44px targets, useReducedMotion, keyboard nav
 */
import { useState } from 'react';
import { motion, useReducedMotion } from 'framer-motion';
import { Activity, Calendar, HelpCircle, AlertTriangle, Clock, CheckCircle2 } from 'lucide-react';
import type { OrgMission } from '../types';

// ── Types ─────────────────────────────────────────────────────────────────────

interface OrgHealthData {
  health:                  string;
  active_missions:         number;
  active_teams:            number;
  pending_approvals:       number;
  task_counts:             Record<string, number>;
  event_counts_24h:        Record<string, number>;
  items_needing_attention: number;
}

interface UpcomingItem {
  id:      string;
  label:   string;
  dueAt?:  string;
  urgency: 'high' | 'medium' | 'low';
  type:    'approval' | 'deadline' | 'scheduled';
}

interface WhyItem {
  id:       string;
  action:   string;
  reason:   string;
  autonomy: string;
}

interface NowNextWhyProps {
  health?:     OrgHealthData;
  missions?:   OrgMission[];
  upcoming?:   UpcomingItem[];
  decisions?:  WhyItem[];
  isLoading?:  boolean;
}

// ── Spring constants ──────────────────────────────────────────────────────────

const SPRING_FAST  = { type: 'spring', stiffness: 600, damping: 35 } as const;

// ── Panel Tab ─────────────────────────────────────────────────────────────────

type PanelId = 'now' | 'next' | 'why';

const PANELS: { id: PanelId; label: string; Icon: React.ComponentType<{ className?: string }> }[] = [
  { id: 'now',  label: 'NOW',  Icon: Activity    },
  { id: 'next', label: 'NEXT', Icon: Calendar    },
  { id: 'why',  label: 'WHY',  Icon: HelpCircle  },
];

function PanelTab({ id, label, Icon, active, badge, onClick }: {
  id: PanelId; label: string;
  Icon: React.ComponentType<{ className?: string }>;
  active: boolean; badge?: number;
  onClick: () => void;
}) {
  const reduce = useReducedMotion();
  return (
    <motion.button
      role="tab"
      aria-selected={active}
      aria-controls={`panel-${id}`}
      id={`tab-${id}`}
      type="button"
      onClick={onClick}
      whileTap={reduce ? {} : { scale: 0.96 }}
      transition={SPRING_FAST}
      style={{ touchAction: 'manipulation' }}
      className={[
        'relative flex items-center gap-1.5 px-4 py-2.5 rounded-lg text-[12px] font-semibold transition-colors',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70',
        active
          ? 'bg-blue-600/15 text-blue-400'
          : 'text-[#64748B] hover:text-[#94A3B8] hover:bg-[#252B3B]',
      ].join(' ')}
    >
      <Icon className="h-3.5 w-3.5" aria-hidden />
      {label}
      {badge != null && badge > 0 && (
        <span className="absolute -top-1 -right-1 w-4 h-4 rounded-full bg-amber-500 text-[10px] text-black font-bold flex items-center justify-center tabular-nums">{badge}</span>
      )}
    </motion.button>
  );
}

// ── NOW Panel ─────────────────────────────────────────────────────────────────

function NowPanel({ health, missions }: { health?: OrgHealthData; missions?: OrgMission[] }) {
  const reduce = useReducedMotion();
  const stats = [
    { label: 'Missions', value: health?.active_missions ?? 0, warn: false },
    { label: 'Tasks Running', value: health?.task_counts?.running ?? 0, warn: false },
    { label: 'Teams', value: health?.active_teams ?? 0, warn: false },
    { label: 'Blocked', value: health?.task_counts?.blocked ?? 0, warn: (health?.task_counts?.blocked ?? 0) > 0 },
  ];

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-2">
        {stats.map((s, i) => (
          <div
            key={s.label}
            style={{ animationDelay: `${Math.min(i, 8) * 0.04}s` }}
            className="jarvis-pop-in bg-[#252B3B] rounded-lg p-3"
          >
            <p className={`text-[20px] font-bold tabular-nums ${s.warn ? 'text-amber-400' : 'text-[#F1F5F9]'}`}>{s.value}</p>
            <p className="text-[11px] text-[#64748B]">{s.label}</p>
            {s.warn && s.value > 0 && (
              <motion.span
                animate={reduce ? {} : { opacity: [1, 0.4, 1] }}
                transition={{ duration: 1.5, repeat: Infinity }}
                className="text-[10px] text-amber-400"
              >
                needs attention
              </motion.span>
            )}
          </div>
        ))}
      </div>

      {/* Live mission list */}
      {missions && missions.length > 0 && (
        <div>
          <p className="text-[11px] font-semibold text-[#64748B] uppercase tracking-wider mb-2">Active Missions</p>
          <div className="space-y-1.5">
            {missions.filter(m => m.status === 'active').slice(0, 4).map((m, i) => (
              <div
                key={m.id}
                style={{ animationDelay: `${Math.min(i, 8) * 0.04}s` }}
                className="jarvis-rise-in flex items-center gap-2.5 p-2.5 bg-[#252B3B] rounded-lg"
              >
                <motion.span
                  animate={reduce ? {} : { scale: [1, 1.3, 1] }}
                  transition={{ duration: 2, repeat: Infinity, delay: i * 0.3 }}
                  className="w-1.5 h-1.5 rounded-full bg-emerald-400 flex-shrink-0"
                />
                <p className="text-[12px] text-[#E2E8F0] truncate">{m.title}</p>
                <span className={`ml-auto text-[10px] flex-shrink-0 ${m.priority === 'critical' ? 'text-red-400' : 'text-[#475569]'}`}>{m.priority}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {(!missions || missions.every(m => m.status !== 'active')) && (
        <div className="flex items-center gap-2 text-[#475569] text-[13px] py-4 justify-center">
          <CheckCircle2 className="h-4 w-4 text-emerald-500/50" aria-hidden />
          No missions running right now
        </div>
      )}
    </div>
  );
}

// ── NEXT Panel ────────────────────────────────────────────────────────────────

function NextPanel({ upcoming, pendingApprovals }: { upcoming?: UpcomingItem[]; pendingApprovals?: number }) {
  const items = upcoming ?? [];

  return (
    <div className="space-y-4" aria-live="polite">
      {pendingApprovals != null && pendingApprovals > 0 && (
        <div className="flex items-center gap-2.5 p-3 bg-amber-500/10 border border-amber-500/20 rounded-lg">
          <AlertTriangle className="h-4 w-4 text-amber-400 flex-shrink-0" aria-hidden />
          <div>
            <p className="text-[13px] font-semibold text-amber-400">{pendingApprovals} approvals awaiting</p>
            <p className="text-[11px] text-[#94A3B8]">Review before SLA expires</p>
          </div>
        </div>
      )}

      {items.length > 0 ? (
        <div className="space-y-1.5">
          {items.map((item, i) => (
            <div
              key={item.id}
              style={{ animationDelay: `${Math.min(i, 8) * 0.04}s` }}
              className="jarvis-rise-in flex items-start gap-2.5 p-2.5 bg-[#252B3B] rounded-lg"
            >
              <Clock className={`h-3.5 w-3.5 flex-shrink-0 mt-0.5 ${item.urgency === 'high' ? 'text-red-400' : item.urgency === 'medium' ? 'text-amber-400' : 'text-[#475569]'}`} aria-hidden />
              <div className="flex-1 min-w-0">
                <p className="text-[12px] text-[#E2E8F0] truncate">{item.label}</p>
                {item.dueAt && (
                  <time dateTime={item.dueAt} className="text-[11px] text-[#64748B] tabular-nums">
                    {new Intl.DateTimeFormat('en', { dateStyle: 'short', timeStyle: 'short' }).format(new Date(item.dueAt))}
                  </time>
                )}
              </div>
              <span className="text-[10px] text-[#475569] capitalize flex-shrink-0">{item.type}</span>
            </div>
          ))}
        </div>
      ) : (
        <div className="flex items-center gap-2 text-[#475569] text-[13px] py-4 justify-center">
          <Calendar className="h-4 w-4" aria-hidden />
          Nothing urgent coming up
        </div>
      )}
    </div>
  );
}

// ── WHY Panel ─────────────────────────────────────────────────────────────────

function WhyPanel({ decisions }: { decisions?: WhyItem[] }) {
  const items  = decisions ?? [];

  return (
    <div className="space-y-2" aria-label="Decision transparency — why autonomous actions occurred">
      {items.length === 0 ? (
        <div className="flex items-center gap-2 text-[#475569] text-[13px] py-4 justify-center">
          <HelpCircle className="h-4 w-4" aria-hidden />
          No autonomous decisions in the last 24h
        </div>
      ) : (
        items.map((item, i) => (
          <div
            key={item.id}
            style={{ animationDelay: `${Math.min(i, 8) * 0.04}s` }}
            className="jarvis-rise-in p-3 bg-[#252B3B] rounded-lg"
          >
            <p className="text-[12px] font-semibold text-[#F1F5F9] mb-1">{item.action}</p>
            <p className="text-[11px] text-[#94A3B8] leading-snug mb-1.5">{item.reason}</p>
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-purple-500/10 text-purple-400 font-medium">{item.autonomy}</span>
          </div>
        ))
      )}
    </div>
  );
}

// ── Main Component ────────────────────────────────────────────────────────────

export function NowNextWhy({ health, missions, upcoming, decisions, isLoading }: NowNextWhyProps) {
  const [active, setActive] = useState<PanelId>('now');
  const pendingApprovals = health?.pending_approvals;

  return (
    <div className="bg-[#1A1F2E] border border-[#2D3748] rounded-xl overflow-hidden">
      {/* Tab bar */}
      <div role="tablist" aria-label="Dashboard panels" className="flex items-center gap-1 p-2 border-b border-[#252B3B]">
        {PANELS.map(({ id, label, Icon }) => (
          <PanelTab
            key={id}
            id={id}
            label={label}
            Icon={Icon}
            active={active === id}
            badge={id === 'next' ? pendingApprovals : undefined}
            onClick={() => setActive(id)}
          />
        ))}
      </div>

      {/* Panel content */}
      <div className="p-4 min-h-[200px]">
        {isLoading ? (
          <div className="flex items-center justify-center h-32 text-[#475569] text-[13px]">
            <Activity className="h-4 w-4 animate-pulse mr-2" aria-hidden />
            Loading…
          </div>
        ) : (
          <>
            <div
              id="panel-now"
              role="tabpanel"
              aria-labelledby="tab-now"
              hidden={active !== 'now'}
            >
              {active === 'now' && <NowPanel health={health} missions={missions} />}
            </div>
            <div
              id="panel-next"
              role="tabpanel"
              aria-labelledby="tab-next"
              hidden={active !== 'next'}
            >
              {active === 'next' && <NextPanel upcoming={upcoming} pendingApprovals={pendingApprovals} />}
            </div>
            <div
              id="panel-why"
              role="tabpanel"
              aria-labelledby="tab-why"
              hidden={active !== 'why'}
            >
              {active === 'why' && <WhyPanel decisions={decisions} />}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

export default NowNextWhy;
