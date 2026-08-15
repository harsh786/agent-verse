/**
 * ChatMessage — renders a single chat message bubble.
 *
 * Supports: user / assistant / system roles.
 * Shows intent badge, goal steps, clarification cards, and token count.
 */

import { type JSX } from 'react';
import type { ChatMessage as ChatMessageType } from './types/chat.types';

interface Props {
  message: ChatMessageType;
  isStreaming?: boolean;
  streamingTokens?: string;
  onEdit?: (messageId: string, currentContent: string) => void;
}

const INTENT_BADGE: Record<string, { label: string; className: string }> = {
  QA: { label: 'Q&A', className: 'bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300' },
  GOAL: { label: 'Goal', className: 'bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300' },
  CLARIFY: { label: 'Clarify', className: 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900 dark:text-yellow-300' },
  SCHEDULE: { label: 'Schedule', className: 'bg-purple-100 text-purple-700 dark:bg-purple-900 dark:text-purple-300' },
};

export function ChatMessage({ message, isStreaming, streamingTokens, onEdit }: Props): JSX.Element {
  const isUser = message.role === 'user';

  const displayContent = isStreaming && streamingTokens !== undefined
    ? streamingTokens
    : message.content;

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
        {/* Intent badge */}
        {isUser && message.intent && INTENT_BADGE[message.intent] && (
          <span
            className={`text-xs px-2 py-0.5 rounded-full mb-1 font-medium ${INTENT_BADGE[message.intent].className}`}
          >
            {INTENT_BADGE[message.intent].label}
          </span>
        )}

        {/* Bubble */}
        <div
          className={[
            'px-4 py-3 rounded-2xl text-sm leading-relaxed whitespace-pre-wrap break-words',
            isUser
              ? 'bg-indigo-600 text-white rounded-br-sm'
              : 'bg-gray-100 dark:bg-gray-800 text-gray-900 dark:text-gray-100 rounded-bl-sm',
          ].join(' ')}
        >
          {displayContent || (isStreaming ? '\u00a0' : '\u00a0')}
          {isStreaming && (
            <span className="inline-block w-1 h-4 bg-current ml-0.5 animate-pulse" />
          )}
        </div>

        {/* Edit button for user messages */}
        {isUser && onEdit && !isStreaming && (
          <button
            className="mt-1 text-xs text-gray-400 hover:text-indigo-500 transition-colors"
            onClick={() => onEdit(message.id, message.content)}
            aria-label="Edit message"
          >
            Edit
          </button>
        )}

        <time
          className="text-xs text-gray-400 mt-1 px-1"
          dateTime={message.created_at}
          aria-label={`Sent at ${new Date(message.created_at).toLocaleTimeString()}`}
        >
          {new Date(message.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
        </time>
      </div>

      {isUser && (
        <div className="w-8 h-8 rounded-full bg-gray-300 dark:bg-gray-600 flex items-center justify-center text-gray-700 dark:text-gray-200 text-sm font-bold ml-3 mt-1 shrink-0">
          U
        </div>
      )}
    </div>
  );
}
