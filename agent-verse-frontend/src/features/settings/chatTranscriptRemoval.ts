import type { ChatTranscriptRemoval } from '@/lib/api/client';

/** "Removed 3 indexed transcripts; 1 kept under legal hold." — or nothing. */
export function removalSummary(r: ChatTranscriptRemoval | undefined): string | null {
  if (!r || r.removed_documents === undefined) return null;
  const parts = [`Removed ${r.removed_documents} indexed transcript${r.removed_documents === 1 ? '' : 's'}`];
  if (r.held_documents) parts.push(`${r.held_documents} kept under legal hold`);
  if (r.pending) parts.push('the rest is being removed in the background');
  return `${parts.join('; ')}.`;
}
