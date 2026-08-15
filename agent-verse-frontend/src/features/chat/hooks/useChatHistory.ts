/**
 * useChatHistory — loads and paginates chat message history.
 */

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { chatApi } from '@/lib/api/chat';
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
