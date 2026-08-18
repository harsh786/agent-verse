/**
 * ChatClarifyCard — clarification question with quick-reply chips and countdown.
 */

import { type JSX } from 'react';
import { HelpCircle } from 'lucide-react';

interface Props {
  question: string;
  options?: string[];
  round?: number;
  onAnswer?: (answer: string) => void;
}

export function ChatClarifyCard({ question, options = [], round = 1, onAnswer }: Props): JSX.Element {
  return (
    <div className="border border-yellow-200 dark:border-yellow-800 bg-yellow-50 dark:bg-yellow-950 rounded-xl p-4 my-2">
      <div className="flex items-center gap-2 mb-2">
        <HelpCircle className="w-4 h-4 text-yellow-600" />
        <span className="text-xs font-medium text-yellow-700 dark:text-yellow-300">
          Clarification needed (round {round}/3)
        </span>
      </div>
      <p className="text-sm font-medium text-[#F0F6FF] dark:text-gray-200 mb-3">{question}</p>
      {options.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {options.map((opt, i) => (
            <button
              key={i}
              className="text-xs px-3 py-1.5 bg-[#0F1826] dark:bg-gray-800 border border-yellow-300 dark:border-yellow-700 rounded-full text-[#A0B4CC] dark:text-gray-200 hover:bg-yellow-100 dark:hover:bg-yellow-900 transition-colors"
              onClick={() => onAnswer?.(opt)}
            >
              {opt}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
