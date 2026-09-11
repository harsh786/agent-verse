/**
 * AgentProfile — full agent detail panel.
 *
 * JARVIS motion system:
 *   - SPRING_PANEL: slides in from right as overlay
 *   - JARVISStagger: stagger capability/memory items
 *   - SPRING_FAST: stat counters
 *
 * Shows: live stream, reputation, capabilities, memory, tools, cost
 */
import { useState } from 'react';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import {
  X, User, Cpu, DollarSign,
  CheckCircle2, Clock, Star, Brain, Wrench, TrendingUp,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import {
  JARVISStagger, JARVISStaggerItem,
  SPRING_PANEL, SPRING_FAST,
} from '@/components/ui/JARVISPageShell';
import { Badge } from '@/components/ui/badge';

// ── Types ──────────────────────────────────────────────────────────────────

interface AgentProfileData {
  id: string;
  name: string;
  role?: string;
  department?: string;
  status?: string;
  autonomy_level?: number;
  reputation?: number;
  success_rate?: number;
  quality_score?: number;
  avg_cost_per_task?: number;
  avg_duration_minutes?: number;
  capabilities?: string[];
  allowed_tools?: string[];
  primary_model?: string;
  tokens_in?: number;
  tokens_out?: number;
  cost_usd?: number;
  current_task?: string;
  memory_items?: { content: string; confidence: number }[];
}

interface AgentProfileProps {
  agent: AgentProfileData | null;
  onClose: () => void;
}

// ── Status pill ────────────────────────────────────────────────────────────

const STATUS_CONFIG: Record<string, { class: string; dot: string; label: string }> = {
  idle:      { class: 'text-[#94A3B8]',  dot: 'bg-[#475569]',                     label: 'Idle'      },
  planning:  { class: 'text-indigo-400', dot: 'bg-indigo-400',                    label: 'Planning'  },
  executing: { class: 'text-emerald-400',dot: 'bg-emerald-400 animate-pulse-glow',label: 'Executing' },
  waiting:   { class: 'text-yellow-400', dot: 'bg-yellow-400',                    label: 'Waiting'   },
  blocked:   { class: 'text-red-400',    dot: 'bg-red-400 animate-pulse',         label: 'Blocked'   },
  escalated: { class: 'text-orange-400', dot: 'bg-orange-400 animate-pulse',      label: 'Escalated' },
  completed: { class: 'text-emerald-500',dot: 'bg-emerald-500',                   label: 'Completed' },
};

// ── Metric row ─────────────────────────────────────────────────────────────

function MetricRow({ label, value, icon: Icon }: { label: string; value: string | number; icon: React.ElementType }) {
  return (
    <div className="flex items-center justify-between py-2 border-b border-[#1E2535] last:border-0">
      <div className="flex items-center gap-2 text-[#475569]">
        <Icon className="h-3.5 w-3.5" aria-hidden />
        <span className="text-xs">{label}</span>
      </div>
      <span className="text-sm font-medium text-[#F1F5F9]">{value}</span>
    </div>
  );
}

// ── Main ───────────────────────────────────────────────────────────────────

export function AgentProfile({ agent, onClose }: AgentProfileProps) {
  const reduce   = useReducedMotion();
  const [tab, setTab] = useState<'overview' | 'memory' | 'tools'>('overview');

  const statusConf = STATUS_CONFIG[agent?.status ?? 'idle'] ?? STATUS_CONFIG.idle;

  const TABS = [
    { id: 'overview', label: 'Overview' },
    { id: 'memory',   label: 'Memory'   },
    { id: 'tools',    label: 'Tools'    },
  ] as const;

  return (
    <AnimatePresence>
      {agent && (
        <>
          {/* Backdrop */}
          <motion.div
            key="backdrop"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={SPRING_FAST}
            className="fixed inset-0 bg-black/40 z-40 backdrop-blur-sm"
            onClick={onClose}
            aria-hidden
          />

          {/* Panel */}
          <motion.aside
            key="panel"
            initial={reduce ? false : { x: '100%' }}
            animate={{ x: 0, opacity: 1 }}
            exit={reduce ? { opacity: 0 } : { x: '100%', opacity: 0 }}
            transition={SPRING_PANEL}
            role="dialog"
            aria-modal
            aria-label={`Agent profile: ${agent.name}`}
            className="fixed right-0 top-0 h-full w-full max-w-md bg-[#0A0D14] border-l border-[#1E2535] z-50 flex flex-col shadow-2xl"
          >
            {/* Header */}
            <div className="flex items-start gap-3 px-5 py-4 border-b border-[#1E2535] shrink-0">
              <div className="relative flex-shrink-0">
                <div className="h-10 w-10 rounded-xl bg-[#1A1F2E] border border-[#1E2535] flex items-center justify-center">
                  <User className="h-5 w-5 text-[#00D4FF]" aria-hidden />
                </div>
                <span
                  className={cn('absolute -bottom-0.5 -right-0.5 h-3 w-3 rounded-full border-2 border-[#0A0D14]', statusConf.dot)}
                  aria-hidden
                />
              </div>

              <div className="flex-1 min-w-0">
                <h2 className="text-base font-semibold text-[#F1F5F9] truncate">{agent.name}</h2>
                <div className="flex items-center gap-2 mt-0.5">
                  {agent.role && (
                    <span className="text-[11px] text-[#475569]">{agent.role}</span>
                  )}
                  {agent.department && (
                    <>
                      <span className="text-[#1E2535]">·</span>
                      <span className="text-[11px] text-[#475569]">{agent.department}</span>
                    </>
                  )}
                </div>
              </div>

              <div className="flex items-center gap-1.5 flex-shrink-0">
                <span className={cn('text-xs font-medium', statusConf.class)}>
                  {statusConf.label}
                </span>
                <button
                  onClick={onClose}
                  aria-label="Close agent profile"
                  style={{ touchAction: 'manipulation' }}
                  className="p-2 rounded-lg text-[#475569] hover:text-[#94A3B8] hover:bg-[#1A1F2E] transition-colors min-w-[44px] min-h-[44px] flex items-center justify-center focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00D4FF]/50"
                >
                  <X className="h-4 w-4" aria-hidden />
                </button>
              </div>
            </div>

            {/* Live current task */}
            {agent.current_task && (
              <div className="mx-5 mt-3 px-3 py-2 rounded-lg bg-emerald-500/5 border border-emerald-500/20 flex items-center gap-2">
                <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse flex-shrink-0" aria-hidden />
                <span className="text-xs text-emerald-400 truncate">{agent.current_task}</span>
              </div>
            )}

            {/* Tabs */}
            <div
              className="flex gap-1 px-5 py-2 shrink-0"
              role="tablist"
              aria-label="Agent profile sections"
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

            {/* Content */}
            <div className="flex-1 overflow-y-auto px-5 pb-6" role="tabpanel">
              <AnimatePresence mode="wait">
                {tab === 'overview' && (
                  <motion.div
                    key="overview"
                    initial={false}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0 }}
                    transition={SPRING_FAST}
                    className="space-y-4 pt-2"
                  >
                    {/* Performance metrics */}
                    <div className="bg-[#0F1623] border border-[#1E2535] rounded-xl p-4">
                      <h3 className="text-[11px] text-[#475569] uppercase tracking-wider mb-3">Performance (30d)</h3>
                      <MetricRow label="Success rate"   value={`${Math.round((agent.success_rate ?? 0) * 100)}%`} icon={TrendingUp} />
                      <MetricRow label="Quality score"  value={(agent.quality_score ?? 0).toFixed(2)}             icon={Star} />
                      <MetricRow label="Avg cost/task"  value={`$${(agent.avg_cost_per_task ?? 0).toFixed(2)}`}   icon={DollarSign} />
                      <MetricRow label="Avg duration"   value={`${(agent.avg_duration_minutes ?? 0).toFixed(1)} min`} icon={Clock} />
                    </div>

                    {/* Reputation */}
                    <div className="bg-[#0F1623] border border-[#1E2535] rounded-xl p-4">
                      <div className="flex items-center justify-between mb-2">
                        <span className="text-[11px] text-[#475569] uppercase tracking-wider">Reputation</span>
                        <span className="text-lg font-bold text-[#F1F5F9]">
                          {((agent.reputation ?? 0) * 100).toFixed(0)}
                        </span>
                      </div>
                      <div className="h-1.5 rounded-full bg-[#1E2535] overflow-hidden">
                        <motion.div
                          className="h-full rounded-full bg-gradient-to-r from-[#00D4FF] to-indigo-400"
                          initial={{ width: 0 }}
                          animate={{ width: `${(agent.reputation ?? 0) * 100}%` }}
                          transition={SPRING_PANEL}
                        />
                      </div>
                    </div>

                    {/* Model info */}
                    {agent.primary_model && (
                      <div className="bg-[#0F1623] border border-[#1E2535] rounded-xl p-4">
                        <h3 className="text-[11px] text-[#475569] uppercase tracking-wider mb-3 flex items-center gap-2">
                          <Cpu className="h-3.5 w-3.5" aria-hidden /> Current Session
                        </h3>
                        <div className="flex items-center justify-between mb-2">
                          <span className="text-sm text-[#94A3B8]">Model</span>
                          <Badge variant="outline" className="text-xs border-[#1E2535] text-[#94A3B8]">
                            {agent.primary_model}
                          </Badge>
                        </div>
                        {(agent.tokens_in || agent.tokens_out) && (
                          <p className="text-xs text-[#475569]">
                            {agent.tokens_in?.toLocaleString()} in / {agent.tokens_out?.toLocaleString()} out
                            {agent.cost_usd !== undefined && ` · $${agent.cost_usd.toFixed(3)}`}
                          </p>
                        )}
                      </div>
                    )}

                    {/* Capabilities */}
                    {agent.capabilities && agent.capabilities.length > 0 && (
                      <div className="bg-[#0F1623] border border-[#1E2535] rounded-xl p-4">
                        <h3 className="text-[11px] text-[#475569] uppercase tracking-wider mb-3">Capabilities</h3>
                        <div className="flex flex-wrap gap-1.5">
                          {agent.capabilities.map(cap => (
                            <Badge key={cap} variant="outline" className="text-xs border-[#1E2535] text-[#94A3B8]">
                              {cap}
                            </Badge>
                          ))}
                        </div>
                      </div>
                    )}
                  </motion.div>
                )}

                {tab === 'memory' && (
                  <motion.div
                    key="memory"
                    initial={false}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0 }}
                    transition={SPRING_FAST}
                    className="pt-2"
                  >
                    {(!agent.memory_items || agent.memory_items.length === 0) ? (
                      <div className="flex flex-col items-center py-12 gap-3">
                        <Brain className="h-10 w-10 text-[#1E2535]" aria-hidden />
                        <p className="text-[#475569] text-sm">No memory items yet.</p>
                      </div>
                    ) : (
                      <JARVISStagger className="space-y-2">
                        {agent.memory_items.map((item, i) => (
                          <JARVISStaggerItem key={i}>
                            <div className="flex items-start gap-2 px-3 py-2.5 rounded-lg bg-[#0F1623] border border-[#1E2535]">
                              <Brain className="h-3.5 w-3.5 text-[#475569] mt-0.5 flex-shrink-0" aria-hidden />
                              <p className="text-xs text-[#94A3B8] leading-relaxed flex-1">{item.content}</p>
                              <Badge
                                variant="outline"
                                className="text-[10px] border-[#1E2535] text-[#475569] flex-shrink-0"
                              >
                                {(item.confidence * 100).toFixed(0)}%
                              </Badge>
                            </div>
                          </JARVISStaggerItem>
                        ))}
                      </JARVISStagger>
                    )}
                  </motion.div>
                )}

                {tab === 'tools' && (
                  <motion.div
                    key="tools"
                    initial={false}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0 }}
                    transition={SPRING_FAST}
                    className="pt-2"
                  >
                    {(!agent.allowed_tools || agent.allowed_tools.length === 0) ? (
                      <div className="flex flex-col items-center py-12 gap-3">
                        <Wrench className="h-10 w-10 text-[#1E2535]" aria-hidden />
                        <p className="text-[#475569] text-sm">No tools configured.</p>
                      </div>
                    ) : (
                      <JARVISStagger className="space-y-2">
                        {agent.allowed_tools.map(tool => (
                          <JARVISStaggerItem key={tool}>
                            <div className="flex items-center gap-3 px-3 py-2.5 rounded-lg bg-[#0F1623] border border-[#1E2535]">
                              <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400 flex-shrink-0" aria-hidden />
                              <span className="text-sm text-[#94A3B8] font-mono">{tool}</span>
                            </div>
                          </JARVISStaggerItem>
                        ))}
                      </JARVISStagger>
                    )}
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
          </motion.aside>
        </>
      )}
    </AnimatePresence>
  );
}

export default AgentProfile;
