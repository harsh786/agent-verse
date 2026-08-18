/**
 * ChatGoalFailureCard — shows failure analysis with 3 suggestion chips.
 */

import { type JSX } from 'react';
import { XCircle } from 'lucide-react';

interface Props {
  reason: string;
  suggestions?: string[];
  onRetry?: (suggestion: string) => void;
}

export function ChatGoalFailureCard({ reason, suggestions = [], onRetry }: Props): JSX.Element {
  return (
    <div className="border border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-950 rounded-xl p-4 my-2">
      <div className="flex items-center gap-2 mb-2">
        <XCircle className="w-4 h-4 text-red-600" />
        <span className="text-sm font-medium text-red-700 dark:text-red-300">Goal failed</span>
      </div>
      <p className="text-sm text-[#A0B4CC] dark:text-gray-300 mb-3">{reason}</p>
      {suggestions.length > 0 && (
        <div>
          <p className="text-xs text-[#5A7494] mb-2">Try one of these:</p>
          <div className="flex flex-col gap-1.5">
            {suggestions.slice(0, 3).map((s, i) => (
              <button
                key={i}
                className="text-xs px-3 py-1.5 text-left bg-[#0F1826] dark:bg-gray-800 border border-red-200 dark:border-red-700 rounded-lg text-[#A0B4CC] dark:text-gray-200 hover:border-indigo-400 hover:text-indigo-600 transition-colors"
                onClick={() => onRetry?.(s)}
              >
                {s}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
