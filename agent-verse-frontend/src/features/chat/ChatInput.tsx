/**
 * ChatInput — multi-line textarea with send button, model selector, and
 * keyboard shortcut (Enter to send, Shift+Enter for newline).
 */

import { useRef, useState, type KeyboardEvent } from 'react';
import { Send, Loader2 } from 'lucide-react';

interface Props {
  onSend: (content: string, model?: string) => void;
  isLoading: boolean;
  availableModels?: string[];
  selectedModel?: string;
  onModelChange?: (model: string) => void;
  disabled?: boolean;
}

export function ChatInput({
  onSend,
  isLoading,
  availableModels = [],
  selectedModel,
  onModelChange,
  disabled,
}: Props) {
  const [value, setValue] = useState('');
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const handleSend = () => {
    const trimmed = value.trim();
    if (!trimmed || isLoading || disabled) return;
    onSend(trimmed, selectedModel);
    setValue('');
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
    }
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleInput = () => {
    const ta = textareaRef.current;
    if (!ta) return;
    ta.style.height = 'auto';
    ta.style.height = `${Math.min(ta.scrollHeight, 200)}px`;
  };

  return (
    <div className="border-t border-white/[0.08] dark:border-gray-700 bg-[#0F1826] dark:bg-gray-900 px-4 py-3">
      {availableModels.length > 0 && onModelChange && (
        <div className="mb-2 flex items-center gap-2">
          <label htmlFor="model-selector" className="text-xs text-[#5A7494]">
            Model:
          </label>
          <select
            id="model-selector"
            className="text-xs border border-white/[0.08] dark:border-gray-700 rounded-lg px-2 py-1 bg-[#0F1826] dark:bg-gray-800 text-[#A0B4CC] dark:text-gray-300"
            value={selectedModel ?? ''}
            onChange={(e) => onModelChange(e.target.value)}
            aria-label="Select LLM model"
          >
            {availableModels.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </div>
      )}

      <div className="flex items-end gap-3">
        <textarea
          ref={textareaRef}
          rows={1}
          className="flex-1 resize-none rounded-xl border border-white/[0.08] dark:border-gray-700 bg-[#0A0F1A] dark:bg-gray-800 px-4 py-3 text-sm text-[#F0F6FF] dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-indigo-500 min-h-[44px] max-h-[200px]"
          placeholder="Ask a question or describe a goal… (Enter to send, Shift+Enter for newline)"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
          onInput={handleInput}
          disabled={isLoading || disabled}
          aria-label="Chat message input"
          aria-multiline="true"
        />

        <button
          className="shrink-0 w-11 h-11 rounded-xl bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center text-white transition-colors"
          onClick={handleSend}
          disabled={!value.trim() || isLoading || disabled}
          aria-label="Send message"
          data-testid="send-button"
        >
          {isLoading ? (
            <Loader2 className="w-5 h-5 animate-spin" />
          ) : (
            <Send className="w-5 h-5" />
          )}
        </button>
      </div>

      <p className="mt-1 text-xs text-[#A0B4CC]">
        Enter to send · Shift+Enter for new line
      </p>
    </div>
  );
}
