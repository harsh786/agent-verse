/**
 * ChatSessionSettingsModal — system prompt, reasoning toggle, proactive suggestions.
 */

import { useState, type JSX } from 'react';
import { X, Settings } from 'lucide-react';
import type { ChatSession, UpdateSessionPayload } from './types/chat.types';

interface Props {
  session: ChatSession | null;
  onClose: () => void;
  onSave: (updates: UpdateSessionPayload) => void;
}

export function ChatSessionSettingsModal({ session, onClose, onSave }: Props): JSX.Element | null {
  const [systemPrompt, setSystemPrompt] = useState(session?.system_prompt ?? '');
  const [showReasoning, setShowReasoning] = useState(session?.show_reasoning ?? false);
  const [proactive, setProactive] = useState(session?.proactive_suggestions ?? true);

  if (!session) return null;

  const handleSave = () => {
    onSave({
      system_prompt: systemPrompt || undefined,
      show_reasoning: showReasoning,
      proactive_suggestions: proactive,
    });
    onClose();
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      role="dialog"
      aria-modal="true"
      aria-label="Session settings"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="bg-white dark:bg-gray-900 rounded-2xl shadow-2xl w-[480px] p-6">
        <div className="flex items-center justify-between mb-5">
          <div className="flex items-center gap-2">
            <Settings className="w-4 h-4 text-indigo-500" />
            <h2 className="text-base font-semibold text-gray-800 dark:text-gray-200">Session Settings</h2>
          </div>
          <button onClick={onClose} className="p-1 rounded hover:bg-gray-100 dark:hover:bg-gray-800" aria-label="Close">
            <X className="w-4 h-4 text-gray-400" />
          </button>
        </div>

        <div className="space-y-4">
          {/* System Prompt */}
          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1" htmlFor="system-prompt">
              System Prompt
            </label>
            <textarea
              id="system-prompt"
              rows={4}
              className="w-full text-sm border border-gray-200 dark:border-gray-700 rounded-xl px-3 py-2 bg-gray-50 dark:bg-gray-800 text-gray-800 dark:text-gray-200 focus:outline-none focus:ring-2 focus:ring-indigo-500 resize-none"
              placeholder="Give the assistant a persona or instructions…"
              value={systemPrompt}
              onChange={(e) => setSystemPrompt(e.target.value)}
            />
          </div>

          {/* Show Reasoning */}
          <div className="flex items-center justify-between py-2">
            <div>
              <p className="text-sm font-medium text-gray-700 dark:text-gray-300">Show reasoning</p>
              <p className="text-xs text-gray-400">Display the agent's thinking process</p>
            </div>
            <button
              role="switch"
              aria-checked={showReasoning}
              className={`relative inline-flex h-5 w-9 rounded-full transition-colors ${showReasoning ? 'bg-indigo-600' : 'bg-gray-200 dark:bg-gray-700'}`}
              onClick={() => setShowReasoning((r) => !r)}
            >
              <span
                className={`inline-block h-4 w-4 mt-0.5 rounded-full bg-white shadow transition-transform ${showReasoning ? 'translate-x-4' : 'translate-x-0.5'}`}
              />
            </button>
          </div>

          {/* Proactive Suggestions */}
          <div className="flex items-center justify-between py-2">
            <div>
              <p className="text-sm font-medium text-gray-700 dark:text-gray-300">Proactive suggestions</p>
              <p className="text-xs text-gray-400">Show follow-up ideas after goal completion</p>
            </div>
            <button
              role="switch"
              aria-checked={proactive}
              className={`relative inline-flex h-5 w-9 rounded-full transition-colors ${proactive ? 'bg-indigo-600' : 'bg-gray-200 dark:bg-gray-700'}`}
              onClick={() => setProactive((p) => !p)}
            >
              <span
                className={`inline-block h-4 w-4 mt-0.5 rounded-full bg-white shadow transition-transform ${proactive ? 'translate-x-4' : 'translate-x-0.5'}`}
              />
            </button>
          </div>
        </div>

        <div className="flex gap-3 mt-6">
          <button
            className="flex-1 py-2 border border-gray-200 dark:border-gray-700 text-sm rounded-xl text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors"
            onClick={onClose}
          >
            Cancel
          </button>
          <button
            className="flex-1 py-2 bg-indigo-600 hover:bg-indigo-700 text-white text-sm rounded-xl font-medium transition-colors"
            onClick={handleSave}
          >
            Save
          </button>
        </div>
      </div>
    </div>
  );
}
