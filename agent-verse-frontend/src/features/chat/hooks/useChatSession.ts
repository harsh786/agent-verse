/**
 * useChatSession — TanStack Query hooks for session CRUD.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { chatApi } from '@/lib/api/chat';
import type { CreateSessionPayload, UpdateSessionPayload } from '../types/chat.types';

export const SESSION_KEYS = {
  list: ['chat', 'sessions'] as const,
  detail: (id: string) => ['chat', 'sessions', id] as const,
};

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
  });
}

export function useDeleteSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (sessionId: string) => chatApi.deleteSession(sessionId),
    onSuccess: () => qc.invalidateQueries({ queryKey: SESSION_KEYS.list }),
  });
}

export function usePinSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ sessionId, pinned }: { sessionId: string; pinned: boolean }) =>
      chatApi.pinSession(sessionId, pinned),
    onSuccess: () => qc.invalidateQueries({ queryKey: SESSION_KEYS.list }),
  });
}

export function useRenameSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ sessionId, title }: { sessionId: string; title: string }) =>
      chatApi.updateSession(sessionId, { title }),
    onSuccess: () => qc.invalidateQueries({ queryKey: SESSION_KEYS.list }),
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
