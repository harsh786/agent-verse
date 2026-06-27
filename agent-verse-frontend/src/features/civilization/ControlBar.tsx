import { useState } from 'react';

interface Props {
  civilizationId: string;
  status: string;
  onPause: () => Promise<void>;
  onResume: () => Promise<void>;
  onSubmitGoal: (goal: string) => Promise<void>;
  onAdjustBudget?: (newBudget: number) => Promise<void>;
  currentBudget?: number;
}

export function ControlBar({ status, onPause, onResume, onSubmitGoal, onAdjustBudget }: Props) {
  const [goal, setGoal] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [showBudget, setShowBudget] = useState(false);
  const [budgetValue, setBudgetValue] = useState('');

  const handleSubmit = async () => {
    if (!goal.trim()) return;
    setSubmitting(true);
    try {
      await onSubmitGoal(goal);
      setGoal('');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="flex items-center gap-3 p-3 bg-gray-50 border-b flex-wrap">
      {/* Goal input */}
      <input
        value={goal}
        onChange={e => setGoal(e.target.value)}
        onKeyDown={e => e.key === 'Enter' && void handleSubmit()}
        placeholder="Submit a goal to the civilization..."
        className="flex-1 px-3 py-1.5 text-sm border rounded"
        disabled={status === 'paused'}
      />
      <button
        onClick={() => void handleSubmit()}
        disabled={submitting || !goal.trim() || status === 'paused'}
        className="px-3 py-1.5 bg-blue-600 text-white text-sm rounded hover:bg-blue-700 disabled:opacity-40"
      >
        {submitting ? 'Submitting...' : '▶ Run'}
      </button>

      {/* Pause / Resume */}
      {status === 'paused' ? (
        <button
          onClick={() => void onResume()}
          className="px-3 py-1.5 bg-green-600 text-white text-sm rounded hover:bg-green-700 font-medium"
        >
          ▶ Resume
        </button>
      ) : (
        <button
          onClick={() => void onPause()}
          className="px-3 py-1.5 bg-red-600 text-white text-sm rounded hover:bg-red-700 font-medium"
        >
          ⏸ Pause Civilization
        </button>
      )}

      {/* Budget adjustment */}
      {onAdjustBudget && (
        <>
          <button
            onClick={() => setShowBudget(!showBudget)}
            className="px-2 py-1.5 bg-gray-100 text-gray-600 text-xs rounded hover:bg-gray-200"
            title="Adjust budget"
          >
            💰
          </button>
          {showBudget && (
            <div className="flex items-center gap-1">
              <input
                type="number"
                value={budgetValue}
                onChange={e => setBudgetValue(e.target.value)}
                placeholder="New budget $"
                className="w-24 px-2 py-1 text-xs border rounded"
              />
              <button
                onClick={async () => {
                  if (onAdjustBudget && budgetValue) {
                    await onAdjustBudget(parseFloat(budgetValue));
                    setShowBudget(false);
                    setBudgetValue('');
                  }
                }}
                className="px-2 py-1 bg-blue-600 text-white text-xs rounded"
              >
                Set
              </button>
            </div>
          )}
        </>
      )}

      {/* Status indicator */}
      <div className="flex items-center gap-1.5">
        <div className={`w-2 h-2 rounded-full ${status === 'active' ? 'bg-green-500 animate-pulse' : 'bg-amber-400'}`} />
        <span className="text-xs text-gray-500 capitalize">{status}</span>
      </div>
    </div>
  );
}
