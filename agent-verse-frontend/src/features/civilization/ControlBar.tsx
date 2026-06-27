import { useState } from 'react';

interface Props {
  civilizationId: string;
  status: string;
  onPause: () => Promise<void>;
  onResume: () => Promise<void>;
  onSubmitGoal: (goal: string) => Promise<void>;
}

export function ControlBar({ status, onPause, onResume, onSubmitGoal }: Props) {
  const [goal, setGoal] = useState('');
  const [submitting, setSubmitting] = useState(false);

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
    <div className="flex items-center gap-3 p-3 bg-gray-50 border-b">
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
        {submitting ? 'Submitting...' : '\u25b6 Run'}
      </button>

      {/* Pause / Resume */}
      {status === 'paused' ? (
        <button
          onClick={() => void onResume()}
          className="px-3 py-1.5 bg-green-600 text-white text-sm rounded hover:bg-green-700 font-medium"
        >
          &#9654; Resume
        </button>
      ) : (
        <button
          onClick={() => void onPause()}
          className="px-3 py-1.5 bg-red-600 text-white text-sm rounded hover:bg-red-700 font-medium"
        >
          &#9646;&#9646; Pause Civilization
        </button>
      )}

      {/* Status indicator */}
      <div className="flex items-center gap-1.5">
        <div className={`w-2 h-2 rounded-full ${status === 'active' ? 'bg-green-500 animate-pulse' : 'bg-amber-400'}`} />
        <span className="text-xs text-gray-500 capitalize">{status}</span>
      </div>
    </div>
  );
}
