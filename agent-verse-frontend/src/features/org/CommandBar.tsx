/**
 * CommandBar — Cmd+K universal NL command interface.
 *
 * UX flow:
 *   1. User presses Cmd+K → modal opens with input
 *   2. AI suggests intent (create mission / search / find artifact)
 *   3. User confirms → 3-step wizard:
 *        Step 1: Goal refinement (AI shows proposed mission spec)
 *        Step 2: Honest preview — no pre-submit estimate endpoint exists yet,
 *                so this shows the refined goal only (see TODO(api) below)
 *        Step 3: Autonomy level + confirm → mission starts
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Command, Sparkles, Target, Info,
  ChevronRight, Loader2, X, Zap,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Slider } from '@/components/ui/slider';
import { useCreateMission } from './hooks/useOrg';
import { orgApi } from './api';

// ── Types ──────────────────────────────────────────────────────────────────

// The refined goal is the user's own input; the estimate fields come from the
// backend's fast pre-flight heuristic (POST /v1/org/{id}/missions/preview —
// team size, cost, risk without dispatching). The real team is formed by the
// MetaOrchestrator when the mission actually launches.
interface MissionPreview {
  refined_goal: string;
  estimate?: {
    departments: string[];
    estimated_agents: number;
    estimated_duration_hours: number;
    estimated_cost_usd: number;
    estimated_risk: string;
    success_probability: number;
    potential_blockers: string[];
  };
}

interface CommandBarProps {
  orgId: string;
  onClose: () => void;
}

// ── Autonomy level labels ──────────────────────────────────────────────────

const AUTONOMY_LABELS: Record<number, { label: string; description: string; color: string }> = {
  1: { label: 'L1 Recommend', description: 'Proposes actions, you approve everything', color: 'text-yellow-400' },
  2: { label: 'L2 Supervised', description: 'Executes low-risk, approves writes', color: 'text-yellow-300' },
  3: { label: 'L3 Standard', description: 'Runs autonomously with configurable gates', color: 'text-emerald-400' },
  4: { label: 'L4 Autonomous', description: 'Highly autonomous within policy', color: 'text-emerald-500' },
  5: { label: 'L5 Full', description: 'End-to-end autonomous execution', color: 'text-blue-400' },
};

// ── Suggestion engine (local heuristic) ───────────────────────────────────

function getSuggestions(text: string): string[] {
  if (!text.trim()) return [];
  const lower = text.toLowerCase();
  const suggestions: string[] = [];

  if (lower.includes('launch') || lower.includes('product'))
    suggestions.push(`💼 Create mission: "${text}"`);
  if (lower.includes('research') || lower.includes('analyse') || lower.includes('analyze'))
    suggestions.push(`🔍 Start research mission: "${text}"`);
  if (lower.includes('status') || lower.includes('what'))
    suggestions.push(`📊 Ask org: "${text}"`);
  if (suggestions.length === 0)
    suggestions.push(`💼 Create mission: "${text}"`, `🔍 Search: "${text}"`);

  return suggestions.slice(0, 3);
}

// ── Main component ─────────────────────────────────────────────────────────

export function CommandBar({ orgId, onClose }: CommandBarProps) {
  const [step, setStep] = useState<'input' | 'preview' | 'autonomy'>('input');
  const [query, setQuery] = useState('');
  const [selectedSuggestion, setSelectedSuggestion] = useState(0);
  const [preview, setPreview] = useState<MissionPreview | null>(null);
  const [autonomyLevel, setAutonomyLevel] = useState(3);
  const [isLoading, setIsLoading] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const createMission = useCreateMission(orgId);

  // Focus input on mount
  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  // Global Escape to close
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [onClose]);

  const suggestions = getSuggestions(query);

  // Step 1 → Step 2: fetch a fast pre-flight estimate (team size / cost / risk)
  // from the backend heuristic and show it. Move to the preview step
  // immediately; the estimate fills in when it returns (it's near-instant).
  const handleSubmit = useCallback(() => {
    if (!query.trim()) return;
    const goal = query;
    setPreview({ refined_goal: goal });
    setStep('preview');
    setIsLoading(true);
    orgApi
      .previewMission(orgId, goal)
      .then((estimate) => setPreview({ refined_goal: goal, estimate }))
      .catch(() => { /* keep the goal-only preview if the estimate fails */ })
      .finally(() => setIsLoading(false));
  }, [query, orgId]);

  const handleConfirmMission = useCallback(async () => {
    if (!preview) return;
    setIsLoading(true);
    try {
      await createMission.mutateAsync({
        org_id: orgId,
        title: preview.refined_goal.slice(0, 200),
        priority: 'medium',
        autonomy_level: autonomyLevel,
      });
      onClose();
    } finally {
      setIsLoading(false);
    }
  }, [preview, orgId, autonomyLevel, createMission, onClose]);

  const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowDown') setSelectedSuggestion(s => Math.min(s + 1, suggestions.length - 1));
    if (e.key === 'ArrowUp') setSelectedSuggestion(s => Math.max(s - 1, 0));
    if (e.key === 'Enter') handleSubmit();
    if (e.key === 'Escape') onClose();
  }, [suggestions.length, handleSubmit, onClose]);

  const autonomyInfo = AUTONOMY_LABELS[autonomyLevel] ?? AUTONOMY_LABELS[3];

  return (
    <AnimatePresence>
      {/* Backdrop */}
      <motion.div
        className="fixed inset-0 z-50 flex items-start justify-center pt-[15vh] bg-black/60 backdrop-blur-sm"
        initial={false}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        onClick={onClose}
        aria-modal="true"
        role="dialog"
        aria-label="Universal command"
      >
        <motion.div
          className="w-full max-w-[620px] mx-4 bg-[var(--bg-card)] border border-[var(--border)] rounded-2xl shadow-2xl overflow-hidden"
          initial={false}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          exit={{ opacity: 0, y: -20, scale: 0.97 }}
          transition={{ type: 'spring', damping: 20, stiffness: 300 }}
          onClick={e => e.stopPropagation()}
        >
          {/* ── Step 1: Input ─────────────────────────────────────────── */}
          {step === 'input' && (
            <div>
              <div className="flex items-center gap-3 px-4 py-3.5 border-b border-[var(--border)]">
                <Command className="h-4 w-4 text-[var(--accent-blue)]" aria-hidden="true" />
                <input
                  ref={inputRef}
                  value={query}
                  onChange={e => { setQuery(e.target.value); setSelectedSuggestion(0); }}
                  onKeyDown={handleKeyDown}
                  placeholder="What do you want the organisation to do?"
                  className="flex-1 bg-transparent text-[var(--text-primary)] placeholder:text-[var(--text-muted)] text-sm outline-none"
                  aria-label="Command input"
                />
                {isLoading && <Loader2 className="h-4 w-4 animate-spin text-[var(--accent-blue)]" aria-label="Loading" />}
                {query && !isLoading && (
                  <button onClick={() => setQuery('')} aria-label="Clear input">
                    <X className="h-4 w-4 text-[var(--text-muted)] hover:text-[var(--text-primary)]" />
                  </button>
                )}
              </div>

              {/* Suggestions */}
              {suggestions.length > 0 && (
                <ul className="py-1" role="listbox" aria-label="Suggestions">
                  {suggestions.map((s, i) => (
                    <li
                      key={s}
                      role="option"
                      aria-selected={i === selectedSuggestion}
                      className={`flex items-center gap-3 px-4 py-2.5 cursor-pointer transition-colors
                        ${i === selectedSuggestion ? 'bg-[var(--bg-surface)]' : 'hover:bg-[var(--bg-surface)]'}`}
                      onClick={handleSubmit}
                    >
                      <Sparkles className="h-3.5 w-3.5 text-[var(--accent-blue)] flex-shrink-0" aria-hidden="true" />
                      <span className="text-sm text-[var(--text-primary)]">{s}</span>
                      <ChevronRight className="h-3 w-3 text-[var(--text-muted)] ml-auto" aria-hidden="true" />
                    </li>
                  ))}
                </ul>
              )}

              {/* Footer hint */}
              <div className="flex items-center gap-3 px-4 py-2.5 border-t border-[var(--border)] bg-[var(--bg-surface)]">
                <span className="text-[10px] text-[var(--text-muted)]">
                  Press <kbd className="px-1 py-0.5 rounded bg-[var(--bg-card)] border border-[var(--border)] text-[9px] font-mono">↵ Enter</kbd> to continue
                </span>
                <span className="text-[10px] text-[var(--text-muted)] ml-auto">
                  <kbd className="px-1 py-0.5 rounded bg-[var(--bg-card)] border border-[var(--border)] text-[9px] font-mono">Esc</kbd> to close
                </span>
              </div>
            </div>
          )}

          {/* ── Step 2: Team Preview ───────────────────────────────────── */}
          {step === 'preview' && preview && (
            <div>
              <div className="flex items-center gap-2 px-4 py-3 border-b border-[var(--border)]">
                <Target className="h-4 w-4 text-[var(--accent-blue)]" aria-hidden="true" />
                <span className="text-sm font-medium text-[var(--text-primary)]">Mission Preview</span>
                <Badge variant="outline" className="ml-auto text-[10px]">
                  Step 2 of 3
                </Badge>
              </div>

              <div className="p-4 space-y-4">
                {/* Goal */}
                <div>
                  <div className="text-xs text-[var(--text-muted)] mb-1">Refined Goal</div>
                  <p className="text-sm text-[var(--text-primary)] bg-[var(--bg-surface)] rounded-lg px-3 py-2">
                    {preview.refined_goal}
                  </p>
                </div>

                {/* Fast pre-flight estimate from the backend heuristic. */}
                {isLoading && !preview.estimate ? (
                  <div className="flex items-center gap-2 px-1 py-3 text-xs text-[var(--text-muted)]" role="status">
                    <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                    Estimating team, cost, and risk…
                  </div>
                ) : preview.estimate ? (
                  <div className="space-y-3">
                    <div className="grid grid-cols-3 gap-2">
                      {[
                        { label: 'Agents', value: String(preview.estimate.estimated_agents) },
                        { label: 'Est. cost', value: `$${preview.estimate.estimated_cost_usd.toFixed(2)}` },
                        { label: 'Est. time', value: `${preview.estimate.estimated_duration_hours}h` },
                      ].map((m) => (
                        <div key={m.label} className="rounded-lg bg-[var(--bg-surface)] px-3 py-2 text-center">
                          <div className="text-sm font-semibold text-[var(--text-primary)] tabular-nums">{m.value}</div>
                          <div className="text-[10px] text-[var(--text-muted)]">{m.label}</div>
                        </div>
                      ))}
                    </div>

                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className="text-[10px] text-[var(--text-muted)]">Departments:</span>
                      {preview.estimate.departments.map((d) => (
                        <Badge key={d} variant="outline" className="text-[10px] capitalize">{d}</Badge>
                      ))}
                      <span
                        className={`ml-auto text-[10px] font-medium capitalize ${
                          preview.estimate.estimated_risk === 'high' ? 'text-rose-400'
                          : preview.estimate.estimated_risk === 'medium' ? 'text-amber-400'
                          : 'text-emerald-400'
                        }`}
                      >
                        {preview.estimate.estimated_risk} risk
                      </span>
                    </div>

                    {preview.estimate.potential_blockers.length > 0 && (
                      <ul className="space-y-1">
                        {preview.estimate.potential_blockers.map((b) => (
                          <li key={b} className="flex items-start gap-1.5 text-[11px] text-[var(--text-muted)]">
                            <Info className="h-3 w-3 mt-0.5 flex-shrink-0 text-amber-400/70" aria-hidden="true" />
                            {b}
                          </li>
                        ))}
                      </ul>
                    )}

                    <p className="text-[10px] text-[var(--text-muted)]">
                      Rough estimate — AgentVerse forms the real team when you launch.
                    </p>
                  </div>
                ) : (
                  <div className="flex items-start gap-2.5 rounded-lg border border-dashed border-[var(--border)] bg-[var(--bg-surface)] px-3 py-3" role="status">
                    <Info className="h-4 w-4 mt-0.5 text-[var(--text-muted)] flex-shrink-0" aria-hidden="true" />
                    <p className="text-xs text-[var(--text-muted)] leading-relaxed">
                      AgentVerse will size and dispatch the mission when you launch it.
                    </p>
                  </div>
                )}
              </div>

              <div className="flex gap-2 px-4 pb-4">
                <Button variant="outline" size="sm" className="flex-1" onClick={() => setStep('input')}>
                  Back
                </Button>
                <Button size="sm" className="flex-1" onClick={() => setStep('autonomy')}>
                  Continue
                  <ChevronRight className="h-3 w-3 ml-1" />
                </Button>
              </div>
            </div>
          )}

          {/* ── Step 3: Autonomy + Confirm ─────────────────────────────── */}
          {step === 'autonomy' && (
            <div>
              <div className="flex items-center gap-2 px-4 py-3 border-b border-[var(--border)]">
                <Zap className="h-4 w-4 text-[var(--accent-blue)]" aria-hidden="true" />
                <span className="text-sm font-medium text-[var(--text-primary)]">Autonomy Level</span>
                <Badge variant="outline" className="ml-auto text-[10px]">
                  Step 3 of 3
                </Badge>
              </div>

              <div className="p-4 space-y-5">
                <div>
                  <div className="flex justify-between mb-2">
                    <div>
                      <span className={`text-sm font-medium ${autonomyInfo.color}`}>
                        {autonomyInfo.label}
                      </span>
                      <p className="text-xs text-[var(--text-muted)] mt-0.5">{autonomyInfo.description}</p>
                    </div>
                    <span className="text-2xl font-bold text-[var(--text-primary)]">L{autonomyLevel}</span>
                  </div>
                  <Slider
                    min={1}
                    max={5}
                    step={1}
                    value={[autonomyLevel]}
                    onValueChange={([v]) => setAutonomyLevel(v)}
                    aria-label="Autonomy level"
                    className="mt-2"
                  />
                  <div className="flex justify-between mt-1">
                    {[1, 2, 3, 4, 5].map(l => (
                      <span key={l} className={`text-[10px] ${l === autonomyLevel ? 'text-[var(--text-primary)] font-medium' : 'text-[var(--text-muted)]'}`}>
                        L{l}
                      </span>
                    ))}
                  </div>
                </div>

                <div className="bg-[var(--bg-surface)] rounded-lg px-3 py-2.5 text-xs text-[var(--text-muted)]">
                  <strong className="text-[var(--text-primary)]">Hard limits always apply:</strong>{' '}
                  No infrastructure changes, financial transfers &gt;$10k, legal agreements,
                  or mass data operations without explicit human approval — regardless of level.
                </div>
              </div>

              <div className="flex gap-2 px-4 pb-4">
                <Button variant="outline" size="sm" className="flex-1" onClick={() => setStep('preview')}>
                  Back
                </Button>
                <Button
                  size="sm"
                  className="flex-1 bg-[var(--accent-blue)] hover:bg-blue-600"
                  onClick={handleConfirmMission}
                  disabled={isLoading}
                >
                  {isLoading ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : <Zap className="h-4 w-4 mr-1" />}
                  Launch Mission
                </Button>
              </div>
            </div>
          )}
        </motion.div>
      </motion.div>
    </AnimatePresence>
  );
}

// ── Global Cmd+K hook ──────────────────────────────────────────────────────

export function useCommandBar() {
  const [isOpen, setIsOpen] = useState(false);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        setIsOpen(o => !o);
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, []);

  return { isOpen, open: () => setIsOpen(true), close: () => setIsOpen(false) };
}
