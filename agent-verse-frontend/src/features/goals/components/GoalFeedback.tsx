/**
 * GoalFeedback — thumbs up/down + optional correction on goal results (RLHF-lite).
 * Appears below goal result. Sends POST /goals/{id}/feedback.
 */
import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { ThumbsUp, ThumbsDown, Check, Loader2 } from 'lucide-react';
import { toast } from '@/stores/toast';
import { API_BASE } from '@/lib/api/client';

interface Props {
  goalId: string;
  status: string;
}

async function submitFeedback(goalId: string, rating: 1 | -1, correction?: string) {
  const { getAuthHeader } = await import('@/stores/auth');
  const resp = await fetch(`${API_BASE}/goals/${goalId}/feedback`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getAuthHeader() },
    body: JSON.stringify({ rating, correction }),
  });
  if (!resp.ok) throw new Error(`Feedback failed: ${resp.status}`);
  return resp.json();
}

export function GoalFeedback({ goalId, status }: Props) {
  const [rating, setRating] = useState<1 | -1 | null>(null);
  const [correction, setCorrection] = useState('');
  const [showCorrection, setShowCorrection] = useState(false);

  const mutation = useMutation({
    mutationFn: (data: { r: 1 | -1; c?: string }) => submitFeedback(goalId, data.r, data.c),
    onSuccess: () => toast({ kind: 'success', message: 'Feedback recorded — thank you!' }),
    onError: () => toast({ kind: 'error', message: 'Failed to submit feedback' }),
  });

  if (!['complete', 'failed'].includes(status)) return null;

  if (mutation.isSuccess) {
    return (
      <div className="flex items-center gap-2 text-xs text-muted-foreground py-2 mt-2 border-t border-border" data-testid="feedback-success">
        <Check className="h-3.5 w-3.5 text-green-500" aria-hidden />
        Feedback recorded
      </div>
    );
  }

  return (
    <div className="space-y-2 pt-3 mt-2 border-t border-border" data-testid="goal-feedback">
      <p className="text-xs text-muted-foreground">Was this result helpful?</p>
      <div className="flex items-center gap-2">
        <button
          onClick={() => { setRating(1); mutation.mutate({ r: 1 }); }}
          disabled={mutation.isPending}
          aria-label="Thumbs up — helpful"
          data-testid="thumbs-up"
          className={`p-2 rounded-lg border transition-colors focus-visible:ring-2 focus-visible:ring-primary ${
            rating === 1 ? 'bg-green-100 border-green-400 text-green-700 dark:bg-green-900/30 dark:border-green-600 dark:text-green-400'
              : 'hover:bg-muted border-border text-muted-foreground'
          }`}
        >
          <ThumbsUp className="h-4 w-4" />
        </button>
        <button
          onClick={() => { setRating(-1); setShowCorrection(true); }}
          disabled={mutation.isPending}
          aria-label="Thumbs down — not helpful"
          data-testid="thumbs-down"
          className={`p-2 rounded-lg border transition-colors focus-visible:ring-2 focus-visible:ring-primary ${
            rating === -1 ? 'bg-red-100 border-red-400 text-red-700 dark:bg-red-900/30 dark:border-red-600 dark:text-red-400'
              : 'hover:bg-muted border-border text-muted-foreground'
          }`}
        >
          <ThumbsDown className="h-4 w-4" />
        </button>
        {mutation.isPending && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />}
      </div>

      {showCorrection && !mutation.isSuccess && (
        <div className="space-y-2 animate-in slide-in-from-top-1 duration-150">
          <label htmlFor="goal-correction" className="text-xs text-muted-foreground">
            What should the correct answer have been? <span className="text-muted-foreground">(optional)</span>
          </label>
          <textarea
            id="goal-correction"
            value={correction}
            onChange={e => setCorrection(e.target.value)}
            placeholder="Describe the correct output…"
            rows={2}
            aria-label="Correction text"
            className="w-full text-xs border border-border rounded-lg px-2.5 py-1.5 bg-background resize-none focus:outline-none focus:ring-1 focus:ring-primary"
          />
          <button
            onClick={() => mutation.mutate({ r: -1, c: correction || undefined })}
            disabled={mutation.isPending}
            aria-label="Submit feedback"
            className="text-xs px-3 py-1.5 rounded-lg bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-50 transition-opacity"
          >
            {mutation.isPending ? 'Submitting…' : 'Submit'}
          </button>
        </div>
      )}
    </div>
  );
}
