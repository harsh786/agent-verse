/**
 * ChatErrorBanner — visible, retryable error surface for the chat stream.
 *
 * A dropped SSE connection or a stream error used to fail silently; this renders
 * the error inline with an optional Retry.
 */
import { type JSX } from 'react';
import { AlertCircle, RotateCcw } from 'lucide-react';

interface Props {
  message: string;
  onRetry?: () => void;
}

export function ChatErrorBanner({ message, onRetry }: Props): JSX.Element {
  return (
    <div
      role="alert"
      data-testid="chat-error"
      className="mx-4 mb-2 flex items-center gap-2 rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-600 dark:text-red-300"
    >
      <AlertCircle className="w-4 h-4 shrink-0" />
      <span className="flex-1">{message}</span>
      {onRetry && (
        <button
          onClick={onRetry}
          className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium text-red-700 hover:bg-red-500/20 dark:text-red-200"
          aria-label="Retry"
        >
          <RotateCcw className="w-3 h-3" /> Retry
        </button>
      )}
    </div>
  );
}
