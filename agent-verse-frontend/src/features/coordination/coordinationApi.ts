import { apiFetch } from '@/lib/api/client';
import type {
  CoordinationMessage,
  CoordinationPage,
  CoordinationRun,
  CoordinationSession,
  PatternName,
  PatternRunResult,
} from './types';

export type { PatternName, PatternRunResult } from './types';

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
      apiFetch<CoordinationRun['swarm']>(`${prefix}/swarm`),
      apiFetch<CoordinationPage & { sealed_bid_count: number }>(`${prefix}/auction`),
    ]);
    return { session, messages, ledger, moa, camel, generative, swarm, auction };
  },
  /** Run a coordination pattern; retrying with the same key resumes / replays it. */
  runPattern(sessionId: string, pattern: PatternName, objective: string, idempotencyKey: string) {
    return apiFetch<PatternRunResult>(`${base(sessionId)}/patterns/${pattern}/runs`, {
      method: 'POST',
      headers: { 'Idempotency-Key': idempotencyKey },
      body: JSON.stringify({ objective }),
    });
  },
  submitMagenticReview(sessionId: string, token: string, approved: boolean) {
    return apiFetch<{ approved: boolean; run: PatternRunResult | null }>(
      `${base(sessionId)}/magentic/human-review`,
      { method: 'POST', body: JSON.stringify({ token, approved }) },
    );
  },
  transition(sessionId: string, command: 'cancel' | 'resume', version: number) {
    return apiFetch(`${base(sessionId)}/${command}`, {
      method: 'POST',
      headers: { 'Idempotency-Key': crypto.randomUUID() },
      body: JSON.stringify({ expected_version: version }),
    });
  },
};
