/**
 * ChatEmptyState — new chat empty state with category suggestion prompts.
 */

import { type JSX } from 'react';

interface Suggestion {
  icon: string;
  label: string;
  prompt: string;
  category: 'qa' | 'goal' | 'schedule';
}

const SUGGESTIONS: Suggestion[] = [
  { icon: '❓', label: 'Ask a question', prompt: 'What is the difference between SQL and NoSQL?', category: 'qa' },
  { icon: '🚀', label: 'Execute a goal', prompt: 'Deploy the backend service to the staging environment', category: 'goal' },
  { icon: '📅', label: 'Schedule a task', prompt: 'Run the database backup every day at 2 AM', category: 'schedule' },
  { icon: '🔍', label: 'Debug an issue', prompt: 'Analyse the application logs and find the root cause of the 503 errors', category: 'goal' },
  { icon: '📊', label: 'Analyse data', prompt: 'What are the key trends in the last 30 days of usage data?', category: 'qa' },
  { icon: '⚙️', label: 'Configure something', prompt: 'Set up GitHub Actions for the Node.js project with lint, test, and deploy', category: 'goal' },
];

interface Props {
  onSelect: (prompt: string) => void;
}

export function ChatEmptyState({ onSelect }: Props): JSX.Element {
  return (
    <div className="flex flex-col items-center justify-center h-full px-8 py-12 text-center">
      <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center text-3xl mb-5 shadow-lg">
        💬
      </div>
      <h2 className="text-xl font-semibold text-gray-800 dark:text-gray-200 mb-2">
        How can I help you today?
      </h2>
      <p className="text-sm text-gray-500 mb-8 max-w-sm">
        Ask questions, execute goals, schedule tasks — powered by intelligent agents.
      </p>

      <div className="grid grid-cols-2 gap-2 w-full max-w-lg">
        {SUGGESTIONS.map((s, i) => (
          <button
            key={i}
            className="flex items-start gap-3 p-3 text-left bg-white dark:bg-gray-800 border border-gray-100 dark:border-gray-700 rounded-xl hover:border-indigo-300 hover:bg-indigo-50/50 dark:hover:bg-indigo-950/50 transition-colors group"
            onClick={() => onSelect(s.prompt)}
          >
            <span className="text-xl shrink-0">{s.icon}</span>
            <div>
              <p className="text-xs font-medium text-gray-600 dark:text-gray-300 group-hover:text-indigo-700 dark:group-hover:text-indigo-300">
                {s.label}
              </p>
              <p className="text-xs text-gray-400 mt-0.5 line-clamp-2">{s.prompt}</p>
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}
