/**
 * OrgComposerWizard — N2: Natural-language org creation wizard.
 *
 * 3-step JARVIS wizard:
 *   Step 1 — Describe your organisation (NL input + industry + goals)
 *   Step 2 — Review AI-composed structure (departments + initial missions)
 *   Step 3 — Confirm & launch
 *
 * Skills:
 *   frontend-design:   JARVIS dark, wizard step indicators, green glow on AI
 *   emil-design-eng:   spring 300/28 step transitions, 600/35 micro-interactions
 *   impeccable-ui:     description dominant, departments secondary, actions tertiary
 *   web-guidelines:    aria-live for AI loading, role=progressbar step, fieldset
 *   ui-ux-pro-max:     44px targets, useReducedMotion, keyboard nav steps
 */
import { useState, useCallback, useId } from 'react';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import { Sparkles, Building2, Plus, Check, ChevronRight, Loader2, Target, X } from 'lucide-react';
import { useMutation } from '@tanstack/react-query';
import { apiRequest } from '@/lib/api/client';

// ── Types ─────────────────────────────────────────────────────────────────────

interface ComposedDept   { id: string; name: string; purpose: string; capability_domains: string[] }
interface ComposedMission { id: string; title: string }

interface ComposeResult {
  org_id:           string;
  name:             string;
  departments:      ComposedDept[];
  initial_missions: ComposedMission[];
  autonomy_level:   number;
  composition_method: 'llm' | 'template';
}

interface OrgComposerWizardProps {
  onComplete?: (orgId: string, orgName: string) => void;
  onClose?:    () => void;
}

// ── Hooks ─────────────────────────────────────────────────────────────────────

function useComposeOrg() {
  return useMutation({
    mutationFn: (body: {
      description: string;
      goals: string[];
      industry: string;
      autonomy_level: number;
      budget_usd: number;
      constraints: string[];
    }) => apiRequest<ComposeResult>('POST', '/v1/org/compose', body),
  });
}

// ── Spring constants ──────────────────────────────────────────────────────────

const SPRING_FAST  = { type: 'spring', stiffness: 600, damping: 35 } as const;
const SPRING_STEP  = { type: 'spring', stiffness: 300, damping: 28 } as const;

// ── Industry options ──────────────────────────────────────────────────────────

const INDUSTRIES = [
  { value: '',           label: 'Select industry…' },
  { value: 'saas',       label: 'SaaS / Software' },
  { value: 'fintech',    label: 'Fintech / Finance' },
  { value: 'healthcare', label: 'Healthcare' },
  { value: 'ecommerce',  label: 'E-commerce' },
  { value: 'edtech',     label: 'EdTech / Education' },
  { value: 'hr',         label: 'HR / People Ops' },
  { value: 'media',      label: 'Media / Content' },
  { value: 'logistics',  label: 'Logistics / Supply Chain' },
];

// ── Step indicator ────────────────────────────────────────────────────────────

function StepIndicator({ step, current }: { step: number; current: number }) {
  const done = current > step;
  const active = current === step;
  return (
    <div className={`w-7 h-7 rounded-full flex items-center justify-center text-[11px] font-bold border-2 transition-colors ${
      done   ? 'bg-emerald-500 border-emerald-500 text-white' :
      active ? 'bg-blue-600 border-blue-600 text-white' :
               'bg-transparent border-[#2D3748] text-[#475569]'
    }`}>
      {done ? <Check className="h-3.5 w-3.5" aria-hidden /> : step}
    </div>
  );
}

// ── Main Wizard ───────────────────────────────────────────────────────────────

export function OrgComposerWizard({ onComplete, onClose }: OrgComposerWizardProps) {
  const titleId   = useId();
  const reduce    = useReducedMotion();
  const compose   = useComposeOrg();
  const [step, setStep]         = useState(1);
  const [result, setResult]     = useState<ComposeResult | null>(null);

  // Step 1 state
  const [description, setDescription] = useState('');
  const [industry, setIndustry]       = useState('');
  const [goalInput, setGoalInput]     = useState('');
  const [goals, setGoals]             = useState<string[]>([]);
  const [autonomy, setAutonomy]       = useState(2);
  const [budget]                      = useState('');  // reserved for budget input field

  const addGoal = useCallback(() => {
    const g = goalInput.trim();
    if (g && goals.length < 5) { setGoals(gs => [...gs, g]); setGoalInput(''); }
  }, [goalInput, goals]);

  const handleCompose = useCallback(async () => {
    if (!description.trim()) return;
    const r = await compose.mutateAsync({
      description,
      goals,
      industry,
      autonomy_level: autonomy,
      budget_usd: parseFloat(budget) || 0,
      constraints: [],
    });
    setResult(r);
    setStep(2);
  }, [compose, description, goals, industry, autonomy, budget]);

  const handleLaunch = useCallback(() => {
    if (result) onComplete?.(result.org_id, result.name);
  }, [result, onComplete]);

  return (
    <div role="dialog" aria-modal="true" aria-labelledby={titleId}
      className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
        className="absolute inset-0 bg-black/60" onClick={onClose} />

      <motion.div
        initial={false}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={reduce ? { opacity: 0 } : { opacity: 0, scale: 0.94 }}
        transition={SPRING_STEP}
        className="relative bg-[#0F1117] border border-[#2D3748] rounded-2xl w-full max-w-xl shadow-2xl overflow-hidden"
      >
        {/* Header */}
        <div className="px-6 pt-6 pb-4 border-b border-[#1E2535]">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-2.5">
              <div className="w-9 h-9 rounded-xl bg-blue-500/10 flex items-center justify-center">
                <Sparkles className="h-4.5 w-4.5 text-blue-400" aria-hidden />
              </div>
              <div>
                <h2 id={titleId} className="text-[16px] font-bold text-[#F1F5F9]">Organisation Composer</h2>
                <p className="text-[11px] text-[#64748B]">AI-powered org creation</p>
              </div>
            </div>
            {onClose && (
              <motion.button
                whileTap={reduce ? {} : { scale: 0.9 }} transition={SPRING_FAST}
                onClick={onClose} aria-label="Close"
                style={{ touchAction: 'manipulation' }}
                className="w-8 h-8 rounded-lg flex items-center justify-center text-[#475569] hover:text-[#94A3B8] hover:bg-[#252B3B] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70"
              >
                <X className="h-4 w-4" aria-hidden />
              </motion.button>
            )}
          </div>

          {/* Step indicators */}
          <div role="progressbar" aria-valuenow={step} aria-valuemin={1} aria-valuemax={3}
            aria-label={`Step ${step} of 3`} className="flex items-center gap-2">
            {[1, 2, 3].map(s => (
              <>
                <StepIndicator key={s} step={s} current={step} />
                {s < 3 && (
                  <div className={`flex-1 h-0.5 rounded-full transition-colors ${
                    step > s ? 'bg-emerald-500' : 'bg-[#2D3748]'
                  }`} />
                )}
              </>
            ))}
          </div>
          <div className="flex justify-between text-[10px] text-[#475569] mt-1.5">
            <span>Describe</span><span>Review</span><span>Launch</span>
          </div>
        </div>

        {/* Step content */}
        <div className="max-h-[60vh] overflow-y-auto">
          <AnimatePresence mode="wait">
            {/* ── STEP 1: Describe ── */}
            {step === 1 && (
              <motion.div key="step1"
                initial={false}
                animate={{ opacity: 1, x: 0 }}
                exit={reduce ? { opacity: 0 } : { opacity: 0, x: -30 }}
                transition={SPRING_STEP}
                className="p-6 space-y-5"
              >
                <div>
                  <label htmlFor="org-desc" className="block text-[12px] font-medium text-[#94A3B8] mb-1.5">
                    Describe your organisation <span className="text-red-400">*</span>
                  </label>
                  <textarea
                    id="org-desc"
                    value={description}
                    onChange={e => setDescription(e.target.value)}
                    placeholder="e.g. A fintech startup that helps small businesses manage cash flow using AI-powered forecasting and automated reconciliation."
                    aria-required="true"
                    rows={4}
                    className="w-full px-3 py-2 rounded-lg bg-[#1A1F2E] border border-[#2D3748] text-[14px] text-[#F1F5F9] placeholder:text-[#374151] focus:outline-none focus:ring-2 focus:ring-blue-500/60 resize-none"
                  />
                </div>

                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label htmlFor="org-industry" className="block text-[12px] font-medium text-[#94A3B8] mb-1.5">Industry</label>
                    <select
                      id="org-industry"
                      value={industry}
                      onChange={e => setIndustry(e.target.value)}
                      className="w-full px-3 py-2 rounded-lg bg-[#1A1F2E] border border-[#2D3748] text-[14px] text-[#F1F5F9] focus:outline-none focus:ring-2 focus:ring-blue-500/60"
                    >
                      {INDUSTRIES.map(i => <option key={i.value} value={i.value}>{i.label}</option>)}
                    </select>
                  </div>
                  <div>
                    <label htmlFor="org-autonomy" className="block text-[12px] font-medium text-[#94A3B8] mb-1.5">
                      Autonomy Level: <span className="text-blue-400">L{autonomy}</span>
                    </label>
                    <input
                      id="org-autonomy"
                      type="range" min={0} max={5} step={1}
                      value={autonomy} onChange={e => setAutonomy(Number(e.target.value))}
                      className="w-full accent-blue-500"
                      aria-label={`Autonomy level ${autonomy}`}
                    />
                    <div className="flex justify-between text-[10px] text-[#475569] mt-0.5">
                      <span>Manual</span><span>Full Auto</span>
                    </div>
                  </div>
                </div>

                {/* Goals */}
                <div>
                  <label className="block text-[12px] font-medium text-[#94A3B8] mb-1.5">Goals (up to 5)</label>
                  <div className="flex gap-2 mb-2">
                    <input
                      type="text"
                      value={goalInput}
                      onChange={e => setGoalInput(e.target.value)}
                      onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); addGoal(); } }}
                      placeholder="Add a goal…"
                      className="flex-1 px-3 py-2 rounded-lg bg-[#1A1F2E] border border-[#2D3748] text-[13px] text-[#F1F5F9] placeholder:text-[#374151] focus:outline-none focus:ring-2 focus:ring-blue-500/60"
                      aria-label="Add a goal"
                    />
                    <motion.button
                      whileTap={reduce ? {} : { scale: 0.93 }} transition={SPRING_FAST}
                      onClick={addGoal} aria-label="Add goal"
                      style={{ touchAction: 'manipulation' }}
                      className="w-9 h-9 rounded-lg bg-blue-600 hover:bg-blue-500 text-white flex items-center justify-center focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400"
                    >
                      <Plus className="h-4 w-4" aria-hidden />
                    </motion.button>
                  </div>
                  <AnimatePresence>
                    {goals.map((g, i) => (
                      <motion.div key={i}
                        initial={false} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
                        transition={SPRING_FAST}
                        className="flex items-center gap-2 mb-1.5 px-3 py-1.5 bg-[#1A1F2E] rounded-lg"
                      >
                        <Target className="h-3.5 w-3.5 text-blue-400 flex-shrink-0" aria-hidden />
                        <p className="flex-1 text-[12px] text-[#E2E8F0] truncate">{g}</p>
                        <button onClick={() => setGoals(gs => gs.filter((_, j) => j !== i))} aria-label={`Remove goal: ${g}`}
                          className="text-[#475569] hover:text-red-400 text-[10px]">✕</button>
                      </motion.div>
                    ))}
                  </AnimatePresence>
                </div>
              </motion.div>
            )}

            {/* ── STEP 2: Review AI structure ── */}
            {step === 2 && (
              <motion.div key="step2"
                initial={false}
                animate={{ opacity: 1, x: 0 }}
                exit={reduce ? { opacity: 0 } : { opacity: 0, x: -30 }}
                transition={SPRING_STEP}
                className="p-6 space-y-4"
              >
                {compose.isPending ? (
                  <div className="flex flex-col items-center py-12 gap-4" aria-live="polite">
                    <motion.div animate={{ rotate: 360 }} transition={{ duration: 1.5, repeat: Infinity, ease: 'linear' }}>
                      <Sparkles className="h-8 w-8 text-blue-400" aria-hidden />
                    </motion.div>
                    <p className="text-[14px] text-[#94A3B8]">Composing your organisation…</p>
                    <p className="text-[12px] text-[#475569]">{compose.data ? 'Complete' : 'AI is designing your structure'}</p>
                  </div>
                ) : result ? (
                  <>
                    <div className="flex items-center gap-2 p-3 bg-emerald-500/5 border border-emerald-500/10 rounded-xl">
                      <Check className="h-4 w-4 text-emerald-400" aria-hidden />
                      <div>
                        <p className="text-[13px] font-semibold text-[#F1F5F9]">{result.name}</p>
                        <p className="text-[11px] text-[#64748B]">
                          {result.composition_method === 'llm' ? 'AI-composed structure' : 'Template-based structure'}
                          {' · '}Autonomy L{result.autonomy_level}
                        </p>
                      </div>
                    </div>

                    <div>
                      <div className="flex items-center gap-1.5 mb-2">
                        <Building2 className="h-3.5 w-3.5 text-[#64748B]" aria-hidden />
                        <p className="text-[11px] font-semibold text-[#64748B] uppercase tracking-wider">
                          {result.departments.length} Departments
                        </p>
                      </div>
                      <div className="space-y-1.5">
                        {result.departments.map((d, i) => (
                          <div key={d.id}
                            style={{ animationDelay: `${Math.min(i, 8) * 0.04}s` }}
                            className="jarvis-rise-in p-2.5 bg-[#1A1F2E] border border-[#2D3748] rounded-lg"
                          >
                            <p className="text-[13px] font-medium text-[#F1F5F9]">{d.name}</p>
                            <p className="text-[11px] text-[#64748B] mt-0.5">{d.purpose}</p>
                            {d.capability_domains.length > 0 && (
                              <div className="flex flex-wrap gap-1 mt-1.5">
                                {d.capability_domains.slice(0, 4).map(cap => (
                                  <span key={cap} className="px-1.5 py-0.5 rounded bg-blue-500/10 text-blue-400 text-[10px]">{cap}</span>
                                ))}
                              </div>
                            )}
                          </div>
                        ))}
                      </div>
                    </div>

                    {result.initial_missions.length > 0 && (
                      <div>
                        <div className="flex items-center gap-1.5 mb-2">
                          <Target className="h-3.5 w-3.5 text-[#64748B]" aria-hidden />
                          <p className="text-[11px] font-semibold text-[#64748B] uppercase tracking-wider">
                            {result.initial_missions.length} Initial Missions
                          </p>
                        </div>
                        {result.initial_missions.map(m => (
                          <div key={m.id} className="flex items-center gap-2 p-2.5 bg-[#1A1F2E] rounded-lg mb-1.5">
                            <Target className="h-3.5 w-3.5 text-amber-400 flex-shrink-0" aria-hidden />
                            <p className="text-[12px] text-[#E2E8F0] truncate">{m.title}</p>
                          </div>
                        ))}
                      </div>
                    )}
                  </>
                ) : null}
              </motion.div>
            )}

            {/* ── STEP 3: Confirm ── */}
            {step === 3 && result && (
              <motion.div key="step3"
                initial={false}
                animate={{ opacity: 1, x: 0 }}
                transition={SPRING_STEP}
                className="p-6 text-center"
              >
                <div className="w-16 h-16 rounded-full bg-emerald-500/10 flex items-center justify-center mx-auto mb-4">
                  <motion.div animate={reduce ? {} : { scale: [1, 1.1, 1] }} transition={{ duration: 1.5, repeat: Infinity }}>
                    <Building2 className="h-8 w-8 text-emerald-400" aria-hidden />
                  </motion.div>
                </div>
                <h3 className="text-[20px] font-bold text-[#F1F5F9] mb-2 [text-wrap:balance]">
                  Ready to launch!
                </h3>
                <p className="text-[13px] text-[#94A3B8] mb-6 [text-wrap:balance]">
                  <strong className="text-[#F1F5F9]">{result.name}</strong> will be created with {result.departments.length} departments
                  {result.initial_missions.length > 0 && ` and ${result.initial_missions.length} initial missions`}.
                </p>
                <div className="grid grid-cols-3 gap-2 mb-6 text-center">
                  {[
                    { label: 'Departments', value: result.departments.length },
                    { label: 'Missions',    value: result.initial_missions.length },
                    { label: 'Autonomy',    value: `L${result.autonomy_level}` },
                  ].map(({ label, value }) => (
                    <div key={label} className="bg-[#1A1F2E] rounded-lg p-3">
                      <p className="text-[18px] font-bold text-[#F1F5F9] tabular-nums">{value}</p>
                      <p className="text-[11px] text-[#64748B]">{label}</p>
                    </div>
                  ))}
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </div>

        {/* Footer actions */}
        <div className="px-6 py-4 border-t border-[#1E2535] flex gap-3">
          {step > 1 && step < 3 && !compose.isPending && (
            <motion.button
              aria-label="Go back to previous step"
              whileTap={reduce ? {} : { scale: 0.97 }} transition={SPRING_FAST}
              onClick={() => setStep(s => s - 1 as 1 | 2)}
              style={{ touchAction: 'manipulation' }}
              className="px-4 py-2.5 rounded-xl border border-[#2D3748] text-[#94A3B8] text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400 min-h-[44px]"
            >
              Back
            </motion.button>
          )}

          <motion.button
            aria-label={step === 1 ? 'Compose organisation with AI' : step === 2 ? 'Continue to confirm' : 'Launch organisation'}
            whileTap={reduce ? {} : { scale: 0.97 }} transition={SPRING_FAST}
            onClick={step === 1 ? handleCompose : step === 2 ? () => setStep(3) : handleLaunch}
            disabled={
              (step === 1 && (!description.trim() || compose.isPending)) ||
              (step === 2 && (compose.isPending || !result))
            }
            style={{ touchAction: 'manipulation' }}
            className="flex-1 flex items-center justify-center gap-2 py-2.5 rounded-xl bg-blue-600 hover:bg-blue-500 text-white text-sm font-semibold disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400 min-h-[44px]"
          >
            {compose.isPending ? (
              <><Loader2 className="h-4 w-4 animate-spin" aria-hidden />Composing…</>
            ) : step === 1 ? (
              <><Sparkles className="h-4 w-4" aria-hidden />Compose with AI</>
            ) : step === 2 ? (
              <><ChevronRight className="h-4 w-4" aria-hidden />Looks good, continue</>
            ) : (
              <><Check className="h-4 w-4" aria-hidden />Launch Organisation</>
            )}
          </motion.button>
        </div>

        <div aria-live="polite" aria-atomic="true" className="sr-only">
          {compose.isPending && 'Composing organisation with AI…'}
          {result && `Organisation ${result.name} composed with ${result.departments.length} departments.`}
        </div>
      </motion.div>
    </div>
  );
}

export default OrgComposerWizard;
