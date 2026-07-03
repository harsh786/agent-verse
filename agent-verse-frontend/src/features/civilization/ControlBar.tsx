/**
 * ControlBar — world-class command palette for civilization control.
 *
 * Features:
 * - Full-width goal input with ⌘+Enter shortcut hint
 * - Keyboard shortcut: Enter to submit
 * - Status pill with animated dot
 * - Icon action buttons with tooltips
 * - Budget adjustment inline
 */
import { useState, useRef } from 'react';
import { Play, Pause, DollarSign, Check, Globe } from 'lucide-react';

interface Props {
  civilizationId: string;
  status: string;
  onPause: () => Promise<void>;
  onResume: () => Promise<void>;
  onSubmitGoal: (goal: string) => Promise<void>;
  onAdjustBudget?: (newBudget: number) => Promise<void>;
  currentBudget?: number;
}

export function ControlBar({
  status,
  onPause,
  onResume,
  onSubmitGoal,
  onAdjustBudget,
  currentBudget,
}: Props) {
  const [goal, setGoal] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [pausing, setPausing] = useState(false);
  const [budgetOpen, setBudgetOpen] = useState(false);
  const [budgetValue, setBudgetValue] = useState(currentBudget?.toString() ?? '');
  const [budgetSaving, setBudgetSaving] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const isPaused = status === 'paused';
  const isRunning = status === 'active';

  const handleSubmit = async () => {
    const trimmed = goal.trim();
    if (!trimmed || submitting || isPaused) return;
    setSubmitting(true);
    try {
      await onSubmitGoal(trimmed);
      setGoal('');
      inputRef.current?.focus();
    } finally {
      setSubmitting(false);
    }
  };

  const handleTogglePause = async () => {
    setPausing(true);
    try {
      if (isPaused) await onResume();
      else await onPause();
    } finally {
      setPausing(false);
    }
  };

  const handleBudgetSave = async () => {
    if (!onAdjustBudget || !budgetValue) return;
    const val = parseFloat(budgetValue);
    if (isNaN(val) || val <= 0) return;
    setBudgetSaving(true);
    try {
      await onAdjustBudget(val);
      setBudgetOpen(false);
    } finally {
      setBudgetSaving(false);
    }
  };

  return (
    <div
      className="flex items-center gap-2 px-4 py-2.5 border-b"
      style={{
        background: 'linear-gradient(180deg, rgba(15,23,42,0.98) 0%, rgba(15,23,42,0.95) 100%)',
        borderColor: 'rgba(255,255,255,0.06)',
      }}
    >
      {/* Globe icon */}
      <Globe className="h-4 w-4 text-slate-500 flex-shrink-0" aria-hidden />

      {/* Goal input */}
      <div className="relative flex-1 min-w-0">
        <input
          ref={inputRef}
          value={goal}
          onChange={e => setGoal(e.target.value)}
          onKeyDown={e => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              void handleSubmit();
            }
          }}
          placeholder={isPaused ? 'Civilization paused…' : 'Submit a goal to the civilization…'}
          disabled={submitting || isPaused}
          className="
            w-full bg-transparent text-sm text-slate-200 placeholder-slate-600
            focus:outline-none focus:placeholder-slate-500
            disabled:opacity-40 disabled:cursor-not-allowed
            transition-colors duration-150
          "
          aria-label="Goal input"
        />
      </div>

      {/* ⌘↵ hint */}
      <kbd className="hidden md:flex items-center gap-0.5 text-[10px] font-mono text-slate-600 border border-slate-700 rounded px-1 py-0.5 flex-shrink-0">
        ↵
      </kbd>

      {/* Submit button */}
      <button
        onClick={() => void handleSubmit()}
        disabled={!goal.trim() || submitting || isPaused}
        aria-label="Run goal"
        className="
          flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold
          bg-indigo-600 text-white hover:bg-indigo-500
          disabled:opacity-30 disabled:cursor-not-allowed
          transition-all duration-150 active:scale-95 flex-shrink-0
        "
      >
        {submitting
          ? <span className="w-3.5 h-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
          : <Play className="h-3.5 w-3.5" fill="currentColor" />
        }
        <span className="hidden sm:inline">{submitting ? 'Sending…' : 'Run'}</span>
      </button>

      {/* Divider */}
      <div className="w-px h-5 bg-white/10 flex-shrink-0" />

      {/* Pause / Resume */}
      <button
        onClick={() => void handleTogglePause()}
        disabled={pausing}
        aria-label={isPaused ? 'Resume civilization' : 'Pause civilization'}
        title={isPaused ? 'Resume' : 'Pause'}
        className={`
          flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold
          transition-all duration-150 active:scale-95 flex-shrink-0
          ${isPaused
            ? 'bg-green-600/90 text-white hover:bg-green-500'
            : 'bg-amber-600/80 text-white hover:bg-amber-500'
          }
          ${pausing ? 'opacity-50 cursor-wait' : ''}
        `}
      >
        {pausing
          ? <span className="w-3.5 h-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
          : isPaused
            ? <Play className="h-3.5 w-3.5" fill="currentColor" />
            : <Pause className="h-3.5 w-3.5" fill="currentColor" />
        }
        <span className="hidden sm:inline">{isPaused ? 'Resume' : 'Pause'}</span>
      </button>

      {/* Budget */}
      {onAdjustBudget && (
        <div className="relative flex-shrink-0">
          <button
            onClick={() => setBudgetOpen(o => !o)}
            aria-label="Adjust budget"
            title={`Budget: $${currentBudget ?? '—'}`}
            className="
              flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-xs
              bg-white/5 border border-white/10 text-slate-400
              hover:bg-white/10 hover:text-slate-200 hover:border-white/20
              transition-all duration-150 active:scale-95
            "
          >
            <DollarSign className="h-3.5 w-3.5" />
            {currentBudget !== undefined && (
              <span className="font-mono">${currentBudget}</span>
            )}
          </button>

          {budgetOpen && (
            <div
              className="absolute right-0 top-full mt-2 w-48 rounded-xl border p-3 z-50 shadow-2xl"
              style={{
                background: 'rgba(15,23,42,0.98)',
                border: '1px solid rgba(255,255,255,0.12)',
                backdropFilter: 'blur(16px)',
              }}
            >
              <p className="text-xs font-semibold text-slate-300 mb-2">Adjust Total Budget</p>
              <div className="flex gap-2">
                <input
                  type="number"
                  value={budgetValue}
                  onChange={e => setBudgetValue(e.target.value)}
                  onKeyDown={e => e.key === 'Enter' && void handleBudgetSave()}
                  placeholder="USD"
                  className="
                    flex-1 bg-white/5 border border-white/10 rounded-lg px-2 py-1.5
                    text-xs text-slate-200 placeholder-slate-600
                    focus:outline-none focus:border-indigo-500/50
                  "
                  autoFocus
                />
                <button
                  onClick={() => void handleBudgetSave()}
                  disabled={budgetSaving}
                  className="p-1.5 rounded-lg bg-indigo-600 text-white hover:bg-indigo-500 disabled:opacity-40"
                >
                  <Check className="h-3.5 w-3.5" />
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Status pill */}
      <div className="flex items-center gap-1.5 flex-shrink-0 ml-1">
        <span
          className={`w-2 h-2 rounded-full flex-shrink-0 ${
            isRunning ? 'bg-green-400 animate-pulse' :
            isPaused ? 'bg-amber-400' :
            'bg-slate-500'
          }`}
        />
        <span className="text-xs text-slate-500 capitalize hidden sm:inline">{status}</span>
      </div>
    </div>
  );
}
