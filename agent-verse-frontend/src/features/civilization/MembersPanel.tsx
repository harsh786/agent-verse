/**
 * MembersPanel — world-class member management for the civilization theater.
 *
 * Features:
 * - List current society members with stats (reputation ring, status, cost)
 * - "Add Agent" button → modal: pick from existing agents or create new
 * - Remove / retire a member
 * - Create new agent inline (name, autonomy mode, connectors)
 */
import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Users, Plus, Trash2, X, ChevronRight,
  Loader2, Check, AlertTriangle, Bot,
} from 'lucide-react';
import { civilizationApi } from '../../lib/api/civilizationApi';
import { agentsApi } from '../../lib/api/client';
import { toast } from '../../stores/toast';

const ROLE_OPTIONS = ['worker', 'analyst', 'coordinator', 'researcher', 'supervisor'];

const STATUS_DOT: Record<string, string> = {
  active: 'bg-blue-400 animate-pulse',
  idle: 'bg-slate-500',
  debating: 'bg-purple-400 animate-pulse',
  retired: 'bg-gray-600',
  failed: 'bg-red-400',
};

function repColor(rep: number) {
  return rep > 0.6 ? '#22c55e' : rep > 0.3 ? '#f59e0b' : '#ef4444';
}

// ── Inline reputation mini-ring ──────────────────────────────────────────────
function MiniRepRing({ rep }: { rep: number }) {
  const r = 10;
  const circ = 2 * Math.PI * r;
  const dash = (rep / 1) * circ;
  const color = repColor(rep);
  return (
    <svg width="26" height="26" className="-rotate-90 flex-shrink-0">
      <circle cx="13" cy="13" r={r} fill="none" stroke="rgba(255,255,255,0.06)" strokeWidth="2.5" />
      <circle
        cx="13" cy="13" r={r} fill="none"
        stroke={color} strokeWidth="2.5" strokeLinecap="round"
        strokeDasharray={`${dash} ${circ}`}
        style={{ filter: `drop-shadow(0 0 3px ${color})` }}
      />
    </svg>
  );
}

// ── Member card ───────────────────────────────────────────────────────────────
function MemberCard({
  member,
  civId,
  onRemove,
}: {
  member: ReturnType<typeof civilizationApi.listMembers> extends Promise<infer T> ? T extends (infer U)[] ? U : never : never;
  civId: string;
  onRemove: () => void;
}) {
  const qc = useQueryClient();
  const killMutation = useMutation({
    mutationFn: () => civilizationApi.killAgent(civId, member.agent_id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['civ-members', civId] });
      qc.invalidateQueries({ queryKey: ['civilization-graph', civId] });
      toast({ kind: 'success', message: `${member.agent_name} removed` });
      onRemove();
    },
    onError: () => toast({ kind: 'error', message: 'Failed to remove agent' }),
  });

  const dotClass = STATUS_DOT[member.status] ?? 'bg-slate-500';
  const repPct = Math.round(member.reputation * 100);

  return (
    <div
      className="rounded-xl p-3 flex items-center gap-3 group"
      style={{
        background: 'rgba(255,255,255,0.04)',
        border: '1px solid rgba(255,255,255,0.07)',
      }}
    >
      {/* Rep ring */}
      <div className="relative flex-shrink-0">
        <MiniRepRing rep={member.reputation} />
        <div className="absolute inset-0 flex items-center justify-center">
          <Bot className="h-3 w-3 text-slate-400" />
        </div>
      </div>

      {/* Info */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold text-slate-100 truncate">
            {member.agent_name}
          </span>
          <span
            className="text-[10px] px-1.5 py-px rounded-full border flex-shrink-0"
            style={{
              background: 'rgba(99,102,241,0.1)',
              borderColor: 'rgba(99,102,241,0.3)',
              color: '#a5b4fc',
            }}
          >
            {member.role}
          </span>
        </div>
        <div className="flex items-center gap-2 mt-0.5">
          <span className={`w-1.5 h-1.5 rounded-full ${dotClass}`} />
          <span className="text-[10px] text-[#5A7494] capitalize">{member.status}</span>
          <span className="text-[10px] text-slate-600">·</span>
          <span
            className="text-[10px] font-semibold tabular-nums"
            style={{ color: repColor(member.reputation) }}
          >
            {repPct}% rep
          </span>
          <span className="text-[10px] text-slate-600">·</span>
          <span className="text-[10px] text-[#5A7494] font-mono">
            ${member.budget_spent_usd.toFixed(2)} / ${member.budget_usd.toFixed(0)}
          </span>
        </div>
        <div className="text-[10px] text-slate-600 mt-0.5 capitalize">
          {member.autonomy_mode.replace(/-/g, ' ')}
        </div>
      </div>

      {/* Remove button */}
      <button
        onClick={() => killMutation.mutate()}
        disabled={killMutation.isPending}
        aria-label={`Remove ${member.agent_name}`}
        className="
          opacity-0 group-hover:opacity-100
          w-7 h-7 rounded-lg flex items-center justify-center flex-shrink-0
          text-[#5A7494] hover:text-red-400 hover:bg-red-500/10
          transition-[color,background-color,border-color,opacity,box-shadow,transform] duration-150 disabled:opacity-30
        "
      >
        {killMutation.isPending
          ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
          : <Trash2 className="h-3.5 w-3.5" />
        }
      </button>
    </div>
  );
}

// ── Add Agent Modal ───────────────────────────────────────────────────────────
function AddAgentModal({
  civId,
  onClose,
}: {
  civId: string;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const [mode, setMode] = useState<'pick' | 'create'>('pick');
  const [selectedId, setSelectedId] = useState('');
  const [role, setRole] = useState('worker');
  const [budget, setBudget] = useState('10');

  // Create new agent state
  const [newName, setNewName] = useState('');
  const [newMode, setNewMode] = useState('bounded-autonomous');
  const [newGoalTemplate, setNewGoalTemplate] = useState('');

  // Fetch all existing agents
  const { data: agentsData, isLoading: agentsLoading } = useQuery({
    queryKey: ['agents-for-civ'],
    queryFn: () => agentsApi.list(),
  });
  const existingAgents = (agentsData as any)?.agents ?? (Array.isArray(agentsData) ? agentsData : []);

  // Fetch current members to exclude them
  const { data: currentMembers = [] } = useQuery({
    queryKey: ['civ-members', civId],
    queryFn: () => civilizationApi.listMembers(civId),
  });
  const memberIds = new Set(currentMembers.map((m) => m.agent_id));

  // Add existing agent mutation
  const addMutation = useMutation({
    mutationFn: () =>
      civilizationApi.addMember(civId, selectedId, role, parseFloat(budget) || 10),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['civ-members', civId] });
      qc.invalidateQueries({ queryKey: ['civilization-graph', civId] });
      qc.invalidateQueries({ queryKey: ['civilization', civId] });
      toast({ kind: 'success', message: 'Agent added to civilization' });
      onClose();
    },
    onError: (e) => toast({ kind: 'error', message: `Failed: ${e}` }),
  });

  // Create + add new agent mutation
  const createMutation = useMutation({
    mutationFn: async () => {
      if (!newName.trim()) throw new Error('Agent name required');
      const created = await agentsApi.create({
        name: newName.trim(),
        goal_template: newGoalTemplate.trim() || undefined,
        autonomy_mode: newMode,
      } as any);
      const agentId = (created as any).agent_id ?? (created as any).id;
      if (!agentId) throw new Error('Failed to get agent ID');
      return civilizationApi.addMember(civId, agentId, role, parseFloat(budget) || 10);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['civ-members', civId] });
      qc.invalidateQueries({ queryKey: ['civilization-graph', civId] });
      qc.invalidateQueries({ queryKey: ['civilization', civId] });
      qc.invalidateQueries({ queryKey: ['agents'] });
      toast({ kind: 'success', message: 'Agent created and added' });
      onClose();
    },
    onError: (e) => toast({ kind: 'error', message: `Failed: ${e}` }),
  });

  const isPending = addMutation.isPending || createMutation.isPending;

  const availableAgents = existingAgents.filter(
    (a: any) => !memberIds.has(a.agent_id ?? a.id)
  );

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: 'rgba(0,0,0,0.7)', backdropFilter: 'blur(8px)' }}
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div
        className="w-full max-w-md rounded-2xl overflow-hidden shadow-2xl"
        style={{
          background: 'linear-gradient(180deg, #131e30 0%, #0f1a28 100%)',
          border: '1px solid rgba(255,255,255,0.1)',
        }}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b" style={{ borderColor: 'rgba(255,255,255,0.07)' }}>
          <div>
            <h2 className="text-base font-bold text-white">Add Agent to Society</h2>
            <p className="text-xs text-[#5A7494] mt-0.5">
              Pick an existing agent or create a new one
            </p>
          </div>
          <button
            onClick={onClose}
            className="w-7 h-7 rounded-lg flex items-center justify-center text-[#5A7494] hover:text-slate-200 hover:bg-white/10 transition-[color,background-color,border-color,opacity,box-shadow,transform]"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Mode switch */}
        <div className="flex gap-1 p-4 pb-0">
          {(['pick', 'create'] as const).map(m => (
            <button
              key={m}
              onClick={() => setMode(m)}
              className={`flex-1 py-2 text-xs font-semibold rounded-lg transition-colors ${
                mode === m
                  ? 'bg-indigo-600 text-white'
                  : 'bg-[#0F1826]/5 text-slate-400 hover:bg-white/10 hover:text-slate-200'
              }`}
            >
              {m === 'pick' ? '🔍 Pick Existing' : '✨ Create New'}
            </button>
          ))}
        </div>

        <div className="p-4 space-y-4">
          {/* ── Pick existing ── */}
          {mode === 'pick' && (
            <div className="space-y-3">
              {agentsLoading ? (
                <div className="flex items-center gap-2 text-[#5A7494] py-4 justify-center">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  <span className="text-sm">Loading agents…</span>
                </div>
              ) : availableAgents.length === 0 ? (
                <div className="text-center py-6">
                  <Bot className="h-8 w-8 text-[#A0B4CC] mx-auto mb-2" />
                  <p className="text-sm text-[#5A7494]">
                    {existingAgents.length === 0
                      ? 'No agents created yet. Use "Create New" tab.'
                      : 'All agents are already in this civilization.'}
                  </p>
                </div>
              ) : (
                <div
                  className="space-y-1.5 max-h-48 overflow-y-auto"
                  style={{ scrollbarWidth: 'thin' }}
                >
                  {availableAgents.map((a: any) => {
                    const aid = a.agent_id ?? a.id;
                    const isSelected = selectedId === aid;
                    return (
                      <button
                        key={aid}
                        onClick={() => setSelectedId(aid)}
                        className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-xl text-left transition-[color,background-color,border-color,opacity,box-shadow,transform] ${
                          isSelected
                            ? 'bg-indigo-600/20 border border-indigo-500/40'
                            : 'bg-[#0F1826]/3 border border-white/6 hover:bg-white/8'
                        }`}
                        style={{ border: isSelected ? undefined : '1px solid rgba(255,255,255,0.06)' }}
                      >
                        <div
                          className="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0"
                          style={{ background: isSelected ? 'rgba(99,102,241,0.2)' : 'rgba(255,255,255,0.05)' }}
                        >
                          <Bot className={`h-4 w-4 ${isSelected ? 'text-indigo-400' : 'text-[#5A7494]'}`} />
                        </div>
                        <div className="flex-1 min-w-0">
                          <p className="text-sm font-medium text-slate-200 truncate">{a.name}</p>
                          <p className="text-[10px] text-[#5A7494] capitalize">
                            {(a.autonomy_mode ?? 'supervised').replace(/-/g, ' ')}
                          </p>
                        </div>
                        {isSelected && <Check className="h-4 w-4 text-indigo-400 flex-shrink-0" />}
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          )}

          {/* ── Create new ── */}
          {mode === 'create' && (
            <div className="space-y-3">
              <div>
                <label className="text-xs font-medium text-slate-400 block mb-1">Agent Name *</label>
                <input
                  value={newName}
                  onChange={e => setNewName(e.target.value)}
                  placeholder="e.g. Jira Analyst"
                  className="w-full bg-[#0F1826]/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-slate-200 placeholder-slate-600 focus:outline-none focus:border-indigo-500/50"
                  autoFocus
                />
              </div>
              <div>
                <label className="text-xs font-medium text-slate-400 block mb-1">Autonomy Mode</label>
                <select
                  value={newMode}
                  onChange={e => setNewMode(e.target.value)}
                  className="w-full bg-[#0F1826]/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500/50 appearance-none"
                  style={{ background: 'rgba(15,23,42,0.9)' }}
                >
                  <option value="supervised">Supervised — approve all writes</option>
                  <option value="bounded-autonomous">Bounded Autonomous — approve high-risk only</option>
                  <option value="fully-autonomous">Fully Autonomous — no approvals</option>
                </select>
              </div>
              <div>
                <label className="text-xs font-medium text-slate-400 block mb-1">Goal Template (optional)</label>
                <textarea
                  value={newGoalTemplate}
                  onChange={e => setNewGoalTemplate(e.target.value)}
                  placeholder="Describe what this agent specialises in…"
                  rows={2}
                  className="w-full bg-[#0F1826]/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-slate-200 placeholder-slate-600 focus:outline-none focus:border-indigo-500/50 resize-none"
                />
              </div>
            </div>
          )}

          {/* Shared: role + budget */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-xs font-medium text-slate-400 block mb-1">Role</label>
              <select
                value={role}
                onChange={e => setRole(e.target.value)}
                className="w-full bg-[#0F1826]/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500/50 appearance-none"
                style={{ background: 'rgba(15,23,42,0.9)' }}
              >
                {ROLE_OPTIONS.map(r => (
                  <option key={r} value={r}>{r.charAt(0).toUpperCase() + r.slice(1)}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="text-xs font-medium text-slate-400 block mb-1">Budget (USD)</label>
              <input
                type="number"
                value={budget}
                onChange={e => setBudget(e.target.value)}
                min="0.1"
                step="1"
                className="w-full bg-[#0F1826]/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500/50"
              />
            </div>
          </div>

          {/* Error */}
          {(addMutation.isError || createMutation.isError) && (
            <div className="flex items-start gap-2 text-xs text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2">
              <AlertTriangle className="h-3.5 w-3.5 mt-0.5 flex-shrink-0" />
              {String(addMutation.error ?? createMutation.error)}
            </div>
          )}

          {/* Submit */}
          <button
            onClick={() => mode === 'pick' ? addMutation.mutate() : createMutation.mutate()}
            disabled={isPending || (mode === 'pick' && !selectedId) || (mode === 'create' && !newName.trim())}
            className="
              w-full flex items-center justify-center gap-2 py-2.5 rounded-xl text-sm font-semibold
              bg-indigo-600 text-white hover:bg-indigo-500
              disabled:opacity-30 disabled:cursor-not-allowed
              transition-[color,background-color,border-color,opacity,box-shadow,transform] duration-150 active:scale-[0.99]
            "
          >
            {isPending
              ? <><Loader2 className="h-4 w-4 animate-spin" /> Adding…</>
              : mode === 'pick'
                ? <><Plus className="h-4 w-4" /> Add to Society</>
                : <><Plus className="h-4 w-4" /> Create &amp; Add</>
            }
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Main MembersPanel ─────────────────────────────────────────────────────────
interface Props {
  civId: string;
}

export function MembersPanel({ civId }: Props) {
  const [modalOpen, setModalOpen] = useState(false);
  const qc = useQueryClient();

  const { data: members = [], isLoading } = useQuery({
    queryKey: ['civ-members', civId],
    queryFn: () => civilizationApi.listMembers(civId),
    refetchInterval: 6000,
  });

  return (
    <div className="space-y-3">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold text-slate-200 flex items-center gap-2">
            <Users className="h-4 w-4 text-indigo-400" />
            Society Members
          </h3>
          <p className="text-[10px] text-[#5A7494] mt-0.5">
            {members.length} member{members.length !== 1 ? 's' : ''} in this civilization
          </p>
        </div>
        <button
          onClick={() => setModalOpen(true)}
          className="
            flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold
            bg-indigo-600/80 text-white border border-indigo-500/40
            hover:bg-indigo-500 transition-[color,background-color,border-color,opacity,box-shadow,transform] duration-150 active:scale-95
          "
          aria-label="Add agent to civilization"
        >
          <Plus className="h-3.5 w-3.5" />
          Add Agent
        </button>
      </div>

      {/* Member list */}
      {isLoading ? (
        <div className="flex items-center justify-center py-8 gap-2 text-[#5A7494]">
          <Loader2 className="h-4 w-4 animate-spin" />
          <span className="text-sm">Loading members…</span>
        </div>
      ) : members.length === 0 ? (
        <div className="rounded-xl border-2 border-dashed py-10 text-center space-y-3"
          style={{ borderColor: 'rgba(255,255,255,0.07)' }}
        >
          <Users className="h-8 w-8 text-[#A0B4CC] mx-auto" />
          <div>
            <p className="text-sm font-medium text-slate-400">No members yet</p>
            <p className="text-xs text-slate-600 mt-1">Add an agent to start the civilization</p>
          </div>
          <button
            onClick={() => setModalOpen(true)}
            className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-semibold bg-indigo-600/80 text-white hover:bg-indigo-500 transition-colors"
          >
            <Plus className="h-3.5 w-3.5" />
            Add First Agent
          </button>
        </div>
      ) : (
        <div className="space-y-2">
          {members.map(member => (
            <MemberCard
              key={member.member_id}
              member={member}
              civId={civId}
              onRemove={() => qc.invalidateQueries({ queryKey: ['civ-members', civId] })}
            />
          ))}
        </div>
      )}

      {/* Quick stats */}
      {members.length > 0 && (
        <div className="grid grid-cols-3 gap-2 pt-1">
          {[
            { label: 'Active', value: members.filter(m => m.status === 'active').length, color: '#3b82f6' },
            { label: 'Avg Rep', value: `${Math.round((members.reduce((s, m) => s + m.reputation, 0) / members.length) * 100)}%`, color: '#22c55e' },
            { label: 'Total Spent', value: `$${members.reduce((s, m) => s + m.budget_spent_usd, 0).toFixed(2)}`, color: '#f59e0b' },
          ].map(stat => (
            <div
              key={stat.label}
              className="rounded-lg p-2 text-center"
              style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.06)' }}
            >
              <p className="text-sm font-bold tabular-nums" style={{ color: stat.color }}>{stat.value}</p>
              <p className="text-[9px] text-slate-600 mt-px">{stat.label}</p>
            </div>
          ))}
        </div>
      )}

      {/* Role legend */}
      {members.length > 0 && (
        <div className="flex flex-wrap gap-2 text-[10px] text-slate-600 pt-1">
          {[...new Set(members.map(m => m.role))].map(r => (
            <span key={r} className="flex items-center gap-1">
              <ChevronRight className="h-3 w-3" />
              {r}
            </span>
          ))}
        </div>
      )}

      {/* Add Agent Modal */}
      {modalOpen && (
        <AddAgentModal
          civId={civId}
          onClose={() => setModalOpen(false)}
        />
      )}
    </div>
  );
}
