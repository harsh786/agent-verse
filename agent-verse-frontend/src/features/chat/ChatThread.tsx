/**
 * ChatThread — virtualized (via scroll container) message list.
 *
 * Auto-scrolls to bottom on new messages.
 * Shows TypingIndicator when streaming.
 */

import { useEffect, useRef, type JSX } from 'react';
import { ChatMessage } from './ChatMessage';
import { TypingIndicator } from './TypingIndicator';
import type { ChatMessage as ChatMessageType, SSEEvent } from './types/chat.types';

interface Props {
  messages: ChatMessageType[];
  isStreaming: boolean;
  streamingTokens: string;
  currentEvent: SSEEvent | null;
  onEditMessage?: (messageId: string, currentContent: string) => void;
}

export function ChatThread({
  messages,
  isStreaming,
  streamingTokens,
  currentEvent,
  onEditMessage,
}: Props): JSX.Element {
  const bottomRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages.length, isStreaming, streamingTokens]);

  const isTyping = isStreaming;
  const lastMsg = messages[messages.length - 1];
  const showStreaming =
    isStreaming &&
    lastMsg?.role === 'assistant' &&
    streamingTokens.length > 0;

  return (
    <div
      className="flex-1 overflow-y-auto px-4 py-6 space-y-2"
      role="log"
      aria-label="Chat messages"
      aria-live="polite"
    >
      {messages.length === 0 && !isStreaming && (
        <div className="flex flex-col items-center justify-center h-full text-center text-[#A0B4CC] gap-4">
          <div className="w-16 h-16 rounded-2xl bg-indigo-100 dark:bg-indigo-900 flex items-center justify-center text-3xl">
            💬
          </div>
          <div>
            <p className="text-lg font-medium text-[#5A7494] dark:text-gray-300">
              Start a conversation
            </p>
            <p className="text-sm mt-1">
              Ask a question, describe a goal, or schedule a task.
            </p>
          </div>
        </div>
      )}

      {messages.map((msg, idx) => {
        const isLastAssistant =
          idx === messages.length - 1 && msg.role === 'assistant';
        return (
          <ChatMessage
            key={msg.id}
            message={msg}
            isStreaming={isLastAssistant && isStreaming}
            streamingTokens={isLastAssistant && isStreaming ? streamingTokens : undefined}
            onEdit={onEditMessage}
          />
        );
      })}

      {/* SSE event badges for goal steps */}
      {isStreaming && currentEvent && currentEvent.type === 'step_started' && (
        <div className="flex justify-start px-4 py-1">
          <span className="text-xs bg-indigo-50 dark:bg-indigo-950 text-indigo-600 dark:text-indigo-300 px-3 py-1 rounded-full border border-indigo-200 dark:border-indigo-800">
            ⚙️ Running step: {String(currentEvent.step ?? '')}
          </span>
        </div>
      )}

      {/* Typing indicator */}
      {isTyping && !showStreaming && <TypingIndicator />}

      <div ref={bottomRef} />
    </div>
  );
}
