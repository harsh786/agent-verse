/**
 * useChatHistory — loads and paginates chat message history.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { chatApi } from '@/lib/api/chat';
import { toast } from '@/stores/toast';
import type { ChatMessage } from '../types/chat.types';

export const MESSAGE_KEYS = {
  list: (sessionId: string) => ['chat', 'messages', sessionId] as const,
};

export function useChatHistory(sessionId: string | undefined, limit = 100) {
  return useQuery({
    queryKey: MESSAGE_KEYS.list(sessionId ?? ''),
    queryFn: (): Promise<ChatMessage[]> =>
      chatApi.listMessages(sessionId!, limit).then((r) => r.messages),
    enabled: Boolean(sessionId),
    staleTime: 0, // always fresh
  });
}

export function useInvalidateHistory(sessionId: string) {
  const qc = useQueryClient();
  return () => qc.invalidateQueries({ queryKey: MESSAGE_KEYS.list(sessionId) });
}

/**
 * Save / clear the caller's feedback on a reply (CHAT-D-2). The cached message
 * list is updated with the server's saved state, then refetched.
 */
export function useMessageFeedback(sessionId: string) {
  const qc = useQueryClient();
  const key = MESSAGE_KEYS.list(sessionId);
  const setFeedback = (messageId: string, feedback: ChatMessage['feedback']) =>
    qc.setQueryData<ChatMessage[]>(key, (msgs) =>
      msgs?.map((m) => (m.id === messageId ? { ...m, feedback } : m)),
    );
  const onError = (err: unknown) =>
    toast({
      kind: 'error',
      message: `Feedback failed: ${err instanceof Error ? err.message : 'unknown error'}`,
    });
  const submit = useMutation({
    mutationFn: ({
      messageId,
      rating,
      comment,
    }: {
      messageId: string;
      rating: -1 | 1;
      comment: string | null;
    }) => chatApi.submitFeedback(sessionId, messageId, rating, comment),
    onSuccess: (saved, { messageId }) => {
      setFeedback(messageId, {
        rating: saved.rating,
        comment: saved.comment,
        updated_at: saved.updated_at,
      });
      void qc.invalidateQueries({ queryKey: key });
    },
    onError,
  });
  const clear = useMutation({
    mutationFn: (messageId: string) => chatApi.clearFeedback(sessionId, messageId),
    onSuccess: (_v, messageId) => {
      setFeedback(messageId, null);
      void qc.invalidateQueries({ queryKey: key });
    },
    onError,
  });
  return { submit, clear };
}
