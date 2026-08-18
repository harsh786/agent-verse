/**
 * TypingIndicator — animated "•••" bubble shown while the agent is thinking.
 */

import { type JSX } from 'react';

export function TypingIndicator(): JSX.Element {
  return (
    <div className="flex items-center gap-1 px-4 py-2" aria-label="Agent is typing" role="status">
      <div className="flex items-center gap-1 bg-[#0F1826] dark:bg-gray-800 rounded-2xl px-4 py-3">
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            className="w-2 h-2 bg-indigo-500 rounded-full animate-bounce"
            style={{ animationDelay: `${i * 0.15}s` }}
          />
        ))}
      </div>
    </div>
  );
}
