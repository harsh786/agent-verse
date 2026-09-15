/**
 * ChatMessage — renders a single chat message bubble.
 *
 * Supports: user / assistant / system roles.
 * Shows intent badge, goal steps, clarification cards, and token count.
 */

import { useState, type JSX } from 'react';
import { RichOutputRenderer } from './RichOutputRenderer';
import { ChatChannelBadge } from './ChatChannelBadge';
import type { ChatMessage as ChatMessageType } from './types/chat.types';

interface Props {
  message: ChatMessageType;
  isStreaming?: boolean;
  streamingTokens?: string;
  /** Called with the edited content when an inline edit is submitted. */
  onEdit?: (messageId: string, newContent: string) => void;
}

const INTENT_BADGE: Record<string, { label: string; className: string }> = {
  QA: { label: 'Q&A', className: 'bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300' },
  GOAL: { label: 'Goal', className: 'bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300' },
  CLARIFY: { label: 'Clarify', className: 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900 dark:text-yellow-300' },
  SCHEDULE: { label: 'Schedule', className: 'bg-purple-100 text-purple-700 dark:bg-purple-900 dark:text-purple-300' },
};

export function ChatMessage({ message, isStreaming, streamingTokens, onEdit }: Props): JSX.Element {
  const isUser = message.role === 'user';
  const [isEditing, setIsEditing] = useState(false);
  const [draft, setDraft] = useState(message.content);

  const displayContent = isStreaming && streamingTokens !== undefined
    ? streamingTokens
    : message.content;

  // Channel origin/continuation (e.g. "via WhatsApp") when metadata carries it.
  const channel =
    typeof message.metadata?.channel === 'string' && message.metadata.channel
      ? message.metadata.channel
      : null;

  const beginEdit = () => {
    setDraft(message.content);
    setIsEditing(true);
  };

  const submitEdit = () => {
    const trimmed = draft.trim();
    setIsEditing(false);
    if (trimmed && trimmed !== message.content) {
      onEdit?.(message.id, trimmed);
    }
  };

  return (
    <div
      className={`flex w-full mb-4 ${isUser ? 'justify-end' : 'justify-start'}`}
      data-testid={`message-${message.id}`}
    >
      {!isUser && (
        <div className="w-8 h-8 rounded-full bg-indigo-600 flex items-center justify-center text-white text-sm font-bold mr-3 mt-1 shrink-0">
          A
        </div>
      )}

      <div className={`max-w-[80%] flex flex-col ${isUser ? 'items-end' : 'items-start'}`}>
        {/* Intent + channel-origin badges */}
        {((isUser && message.intent && INTENT_BADGE[message.intent]) || channel) && (
          <div className={`flex items-center gap-1.5 mb-1 ${isUser ? 'justify-end' : ''}`}>
            {isUser && message.intent && INTENT_BADGE[message.intent] && (
              <span
                className={`text-xs px-2 py-0.5 rounded-full font-medium ${INTENT_BADGE[message.intent].className}`}
              >
                {INTENT_BADGE[message.intent].label}
              </span>
            )}
            {channel && <ChatChannelBadge channel={channel} />}
          </div>
        )}

        {/* Inline edit-and-rerun textarea (replaces window.prompt) */}
        {isUser && isEditing ? (
          <div className="w-full min-w-[240px] flex flex-col items-end gap-2">
            <textarea
              className="w-full resize-none rounded-2xl border border-white/[0.08] bg-[#0A0F1A] px-4 py-3 text-sm text-[#F0F6FF] focus:outline-none focus:ring-2 focus:ring-indigo-500"
              value={draft}
              rows={Math.min(6, draft.split('\n').length + 1)}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  submitEdit();
                } else if (e.key === 'Escape') {
                  e.preventDefault();
                  setIsEditing(false);
                }
              }}
              aria-label="Edit message"
              data-testid={`edit-textarea-${message.id}`}
              autoFocus
            />
            <div className="flex gap-2">
              <button
                className="text-xs px-3 py-1 rounded-lg text-[#A0B4CC] hover:text-[#F0F6FF] transition-colors"
                onClick={() => setIsEditing(false)}
              >
                Cancel
              </button>
              <button
                className="text-xs px-3 py-1 rounded-lg bg-indigo-600 hover:bg-indigo-700 text-white transition-colors"
                onClick={submitEdit}
                data-testid={`edit-save-${message.id}`}
              >
                Save &amp; rerun
              </button>
            </div>
          </div>
        ) : (
          <>
            {/* Bubble */}
            <div
              className={[
                'px-4 py-3 rounded-2xl text-sm leading-relaxed break-words',
                // Plain whitespace handling only for the non-markdown (user / streaming)
                // path; RichMarkdown renders its own block elements.
                isUser || isStreaming ? 'whitespace-pre-wrap' : '',
                isUser
                  ? 'bg-indigo-600 text-white rounded-br-sm'
                  : 'bg-[#0F1826] dark:bg-gray-800 text-[#F0F6FF] dark:text-gray-100 rounded-bl-sm',
              ].join(' ')}
            >
              {!isUser && !isStreaming && displayContent ? (
                // Assistant output renders richly once streaming completes: tabular,
                // chart and image blocks are promoted to interactive components, the
                // rest stays as markdown (tables, code, lists\u2026).
                <RichOutputRenderer content={displayContent} />
              ) : (
                <>
                  {displayContent || '\u00a0'}
                  {isStreaming && (
                    <span className="inline-block w-1 h-4 bg-current ml-0.5 animate-pulse" />
                  )}
                </>
              )}
            </div>

            {/* Edit button for user messages */}
            {isUser && onEdit && !isStreaming && (
              <button
                className="mt-1 text-xs text-[#A0B4CC] hover:text-indigo-500 transition-colors"
                onClick={beginEdit}
                aria-label="Edit message"
              >
                Edit
              </button>
            )}
          </>
        )}

        <time
          className="text-xs text-[#A0B4CC] mt-1 px-1"
          dateTime={message.created_at}
          aria-label={`Sent at ${new Date(message.created_at).toLocaleTimeString()}`}
        >
          {new Date(message.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
        </time>
      </div>

      {isUser && (
        <div className="w-8 h-8 rounded-full bg-[#1E2C4A] dark:bg-gray-600 flex items-center justify-center text-[#A0B4CC] dark:text-gray-200 text-sm font-bold ml-3 mt-1 shrink-0">
          U
        </div>
      )}
    </div>
  );
}
