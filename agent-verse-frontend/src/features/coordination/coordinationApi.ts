import { apiFetch } from '@/lib/api/client';
import type {
  CoordinationMessage,
  CoordinationPage,
  CoordinationRun,
  CoordinationSession,
} from './types';

const base = (sessionId: string) =>
  `/api/v1/coordination/sessions/${encodeURIComponent(sessionId)}`;

async function optional<T>(request: Promise<T>, fallback: T): Promise<T> {
  try {
    return await request;
  } catch (error) {
    if (error instanceof Error && 'status' in error && error.status === 404) return fallback;
    throw error;
  }
}

export const coordinationApi = {
  async getRun(sessionId: string): Promise<CoordinationRun> {
    const prefix = base(sessionId);
    const [session, messages, ledger, moa, camel, generative, swarm, auction] = await Promise.all([
      apiFetch<CoordinationSession>(prefix),
      apiFetch<CoordinationPage<CoordinationMessage>>(
        `${prefix}/messages?after_sequence=0&limit=500`,
      ),
      optional(apiFetch<Record<string, unknown>>(`${prefix}/ledger`), null),
      apiFetch<CoordinationPage>(`${prefix}/moa/layers?after_layer=-1&limit=100`),
      apiFetch<CoordinationPage>(`${prefix}/camel`),
      apiFetch<CoordinationPage>(`${prefix}/generative`),
      apiFetch<{ nodes: Array<Record<string, unknown>>; edges: Array<Record<string, unknown>> }>(`${prefix}/swarm`),
      apiFetch<CoordinationPage & { sealed_bid_count: number }>(`${prefix}/auction`),
    ]);
    return { session, messages, ledger, moa, camel, generative, swarm, auction };
  },
  transition(sessionId: string, command: 'cancel' | 'resume', version: number) {
    return apiFetch(`${base(sessionId)}/${command}`, {
      method: 'POST',
      headers: { 'Idempotency-Key': crypto.randomUUID() },
      body: JSON.stringify({ expected_version: version }),
    });
  },
};
