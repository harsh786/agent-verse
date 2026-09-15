/**
 * useChatSession — TanStack Query hooks for session CRUD.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { chatApi } from '@/lib/api/chat';
import { toast } from '@/stores/toast';
import type { CreateSessionPayload, UpdateSessionPayload } from '../types/chat.types';

export const SESSION_KEYS = {
  list: ['chat', 'sessions'] as const,
  detail: (id: string) => ['chat', 'sessions', id] as const,
};

// Surface mutation failures instead of letting a control silently do nothing
// (a failed request used to make "New Chat" appear dead).
const onMutationError = (action: string) => (err: unknown) =>
  toast({
    kind: 'error',
    message: `${action} failed: ${err instanceof Error ? err.message : 'unknown error'}`,
  });

export function useSessions() {
  return useQuery({
    queryKey: SESSION_KEYS.list,
    queryFn: () => chatApi.listSessions().then((r) => r.sessions),
    staleTime: 30_000,
  });
}

export function useSession(sessionId: string | undefined) {
  return useQuery({
    queryKey: SESSION_KEYS.detail(sessionId ?? ''),
    queryFn: () => chatApi.getSession(sessionId!),
    enabled: Boolean(sessionId),
    staleTime: 30_000,
  });
}

export function useCreateSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: CreateSessionPayload = {}) => chatApi.createSession(payload),
    onSuccess: () => qc.invalidateQueries({ queryKey: SESSION_KEYS.list }),
    onError: onMutationError('Create chat'),
  });
}

export function useUpdateSession(sessionId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: UpdateSessionPayload) => chatApi.updateSession(sessionId, payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: SESSION_KEYS.list });
      qc.invalidateQueries({ queryKey: SESSION_KEYS.detail(sessionId) });
    },
    onError: onMutationError('Update chat'),
  });
}

export function useDeleteSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (sessionId: string) => chatApi.deleteSession(sessionId),
    onSuccess: () => qc.invalidateQueries({ queryKey: SESSION_KEYS.list }),
    onError: onMutationError('Delete chat'),
  });
}

export function usePinSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ sessionId, pinned }: { sessionId: string; pinned: boolean }) =>
      chatApi.pinSession(sessionId, pinned),
    onSuccess: () => qc.invalidateQueries({ queryKey: SESSION_KEYS.list }),
    onError: onMutationError('Pin chat'),
  });
}

export function useRenameSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ sessionId, title }: { sessionId: string; title: string }) =>
      chatApi.updateSession(sessionId, { title }),
    onSuccess: () => qc.invalidateQueries({ queryKey: SESSION_KEYS.list }),
    onError: onMutationError('Rename chat'),
  });
}

export function useFolders() {
  return useQuery({
    queryKey: ['chat', 'folders'],
    queryFn: () => chatApi.listFolders().then((r) => r.folders),
    staleTime: 60_000,
  });
}

export function useCreateFolder() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ name, color }: { name: string; color?: string }) =>
      chatApi.createFolder(name, color),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['chat', 'folders'] }),
  });
}
