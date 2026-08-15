/**
 * ChatGoalSummary — shown after a goal completes with follow-up suggestion chips.
 */

import { type JSX } from 'react';
import { CheckCircle } from 'lucide-react';

interface Props {
  summary: string;
  suggestions?: string[];
  onSuggestionClick?: (text: string) => void;
}

export function ChatGoalSummary({ summary, suggestions = [], onSuggestionClick }: Props): JSX.Element {
  return (
    <div className="border border-green-200 dark:border-green-800 bg-green-50 dark:bg-green-950 rounded-xl p-4 my-2">
      <div className="flex items-center gap-2 mb-2">
        <CheckCircle className="w-4 h-4 text-green-600" />
        <span className="text-sm font-medium text-green-700 dark:text-green-300">Goal completed</span>
      </div>
      <p className="text-sm text-gray-700 dark:text-gray-300 mb-3">{summary}</p>
      {suggestions.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {suggestions.map((s, i) => (
            <button
              key={i}
              className="text-xs px-3 py-1 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-full text-gray-600 dark:text-gray-300 hover:border-indigo-400 hover:text-indigo-600 transition-colors"
              onClick={() => onSuggestionClick?.(s)}
            >
              {s}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
