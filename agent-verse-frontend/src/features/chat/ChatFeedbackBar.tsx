/**
 * ChatFeedbackBar — thumbs up/down and an optional comment on an assistant reply.
 *
 * CHAT-D-2: the feedback is saved on the server (one per person per reply), so
 * the bar shows the saved state from the message list. Clicking the active
 * thumb clears it; clicking the other one changes it (keeping the comment).
 */

import { useState, type JSX } from 'react';
import { ThumbsDown, ThumbsUp, MessageSquareText } from 'lucide-react';
import type { ChatMessageFeedback } from './types/chat.types';

const MAX_COMMENT = 4000;

interface Props {
  feedback: ChatMessageFeedback | null;
  onSubmit: (rating: -1 | 1, comment: string | null) => void;
  onClear: () => void;
  disabled?: boolean;
}

export function ChatFeedbackBar({ feedback, onSubmit, onClear, disabled }: Props): JSX.Element {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(feedback?.comment ?? '');
  const rating = feedback?.rating ?? 0;

  const choose = (value: -1 | 1) => {
    if (rating === value) onClear();
    else onSubmit(value, feedback?.comment ?? null);
  };

  const saveComment = () => {
    if (rating !== 1 && rating !== -1) return;
    setEditing(false);
    onSubmit(rating, draft.trim() || null);
  };

  const thumb = (value: -1 | 1) => {
    const active = rating === value;
    const Icon = value === 1 ? ThumbsUp : ThumbsDown;
    const label = value === 1 ? 'Good response' : 'Bad response';
    return (
      <button
        type="button"
        className={[
          'p-1 rounded transition-colors',
          active
            ? 'text-indigo-600 dark:text-indigo-400 bg-indigo-50 dark:bg-indigo-950'
            : 'text-muted-foreground hover:text-foreground',
        ].join(' ')}
        onClick={() => choose(value)}
        disabled={disabled}
        aria-pressed={active}
        aria-label={label}
        title={active ? `${label} (click to clear)` : label}
      >
        <Icon className="w-3.5 h-3.5" />
      </button>
    );
  };

  return (
    <div className="mt-1 flex flex-col gap-1" data-testid="feedback-bar">
      <div className="flex items-center gap-1">
        {thumb(1)}
        {thumb(-1)}
        {(rating === 1 || rating === -1) && !editing && (
          <button
            type="button"
            className="p-1 rounded text-muted-foreground hover:text-foreground"
            onClick={() => {
              setDraft(feedback?.comment ?? '');
              setEditing(true);
            }}
            disabled={disabled}
            aria-label={feedback?.comment ? 'Edit feedback comment' : 'Add feedback comment'}
          >
            <MessageSquareText className="w-3.5 h-3.5" />
          </button>
        )}
      </div>
      {feedback?.comment && !editing && (
        <p className="text-xs text-muted-foreground italic px-1" data-testid="feedback-comment">
          {feedback.comment}
        </p>
      )}
      {editing && (
        <div className="flex flex-col gap-1 min-w-[240px]">
          <textarea
            className="w-full resize-none rounded-lg border border-border bg-background px-2 py-1 text-xs text-foreground focus:outline-none focus:ring-2 focus:ring-indigo-500"
            rows={2}
            maxLength={MAX_COMMENT}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            aria-label="Feedback comment"
            autoFocus
          />
          <div className="flex justify-end gap-2">
            <button
              type="button"
              className="text-xs px-2 py-0.5 text-muted-foreground hover:text-foreground"
              onClick={() => setEditing(false)}
            >
              Cancel
            </button>
            <button
              type="button"
              className="text-xs px-2 py-0.5 rounded bg-indigo-600 hover:bg-indigo-700 text-white"
              onClick={saveComment}
            >
              Save comment
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
