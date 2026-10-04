/**
 * Client-side handling of backend endpoints that now refuse honestly instead of
 * faking success: object-shaped error details, the meta-agent heuristic-draft
 * 502, the PKCE OAuth endpoints, and the 410 on v1 suggestion apply.
 */
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import {
  agentsApi,
  ApiError,
  connectorsApi,
  createdAgentId,
  errorMessageFromBody,
  heuristicDraftFromError,
  selfImprovementApi,
} from '@/lib/api/client';
import { useAuthStore } from '@/stores/auth';
import { useToastStore } from '@/stores/toast';
import { fmtFixed, fmtRatioPct, fmtUsd, isMetric, NO_VALUE } from '@/lib/metrics';

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

beforeEach(() => {
  useToastStore.setState({ toasts: [] });
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('errorMessageFromBody', () => {
  test('prefers the envelope message, then a string detail', () => {
    expect(errorMessageFromBody({ error: { message: 'env' }, detail: 'x' })).toBe('env');
    expect(errorMessageFromBody({ detail: 'plain' })).toBe('plain');
  });

  test('reads RFC-7807-style object details instead of "[object Object]"', () => {
    expect(
      errorMessageFromBody({ detail: { type: 't', title: 'Title', detail: 'The real reason' } }),
    ).toBe('The real reason');
    expect(errorMessageFromBody({ detail: { title: 'Only a title' } })).toBe('Only a title');
  });

  test('joins FastAPI validation error messages', () => {
    expect(errorMessageFromBody({ detail: [{ msg: 'field required' }, { msg: 'bad type' }] })).toBe(
      'field required; bad type',
    );
  });

  test('returns undefined for bodies with nothing usable', () => {
    expect(errorMessageFromBody(null)).toBeUndefined();
    expect(errorMessageFromBody({})).toBeUndefined();
    expect(errorMessageFromBody({ detail: {} })).toBeUndefined();
  });

  test('an object detail from a real request becomes a readable ApiError message', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      json({ detail: { type: 'connector-test-unavailable', detail: 'Not exchanged.', reachable: false } }, 501),
    );
    const err = await connectorsApi.test('c').catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(501);
    expect((err as ApiError).message).toBe('Not exchanged.');
  });
});

describe('meta-agent create (POST /agents/create)', () => {
  const HEURISTIC_BODY = {
    detail: 'The agent designer LLM did not return a usable config; no agent was created.',
    error_code: 'meta_agent_llm_unavailable',
    generated_by: 'heuristic',
    fallback_reason: 'timeout',
    draft_config: {
      name: 'Draft', goal_template: 'do x', connectors: ['github'], trigger_type: 'manual', autonomy_mode: 'supervised',
    },
  };

  test('only sends accept_heuristic when explicitly confirmed', async () => {
    const f = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => json({ agent: { agent_id: 'a1' } }, 201));
    await agentsApi.createNl('make a bot');
    await agentsApi.createNl('make a bot', false, { acceptHeuristic: true });
    expect(JSON.parse(String(f.mock.calls[0][1]?.body))).toEqual({ command: 'make a bot', autorun: false });
    expect(JSON.parse(String(f.mock.calls[1][1]?.body))).toEqual({
      command: 'make a bot', autorun: false, accept_heuristic: true,
    });
  });

  test('a heuristic 502 is recognised, carries the draft, and raises no generic "Server error" toast', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(json(HEURISTIC_BODY, 502));
    const err = await agentsApi.createNl('make a bot').catch((e: unknown) => e);
    const draft = heuristicDraftFromError(err);
    expect(draft).not.toBeNull();
    expect(draft?.draft).toEqual(HEURISTIC_BODY.draft_config);
    expect(draft?.fallbackReason).toBe('timeout');
    expect(draft?.detail).toMatch(/no agent was created/);
    expect(useToastStore.getState().toasts.some((t) => /Server error/.test(t.message))).toBe(false);
  });

  test('other errors are not mistaken for a heuristic draft', () => {
    expect(heuristicDraftFromError(new ApiError(502, 'bad gateway', { detail: 'x' }))).toBeNull();
    expect(heuristicDraftFromError(new ApiError(500, 'x', HEURISTIC_BODY))).toBeNull();
    expect(heuristicDraftFromError(new Error('nope'))).toBeNull();
  });

  test('createdAgentId reads both the {agent: {...}} and the flat response shapes', () => {
    expect(createdAgentId({ agent: { agent_id: 'nested' } as never })).toBe('nested');
    expect(createdAgentId({ agent_id: 'flat' })).toBe('flat');
    expect(createdAgentId(undefined)).toBe('');
  });
});

describe('connector OAuth (PKCE)', () => {
  test('startPkceOAuth GETs /connectors/oauth/start with the server_id', async () => {
    const f = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      json({ server_id: 'srv 1', auth_url: 'https://p/auth', state: 's' }),
    );
    await connectorsApi.startPkceOAuth('srv 1');
    const [url, init] = f.mock.calls[0];
    expect(String(url)).toContain('/connectors/oauth/start?server_id=srv%201');
    expect(init?.method ?? 'GET').toBe('GET');
  });

  test('completePkceOAuth GETs /connectors/oauth/callback with server_id, code and state', async () => {
    const f = vi.spyOn(globalThis, 'fetch').mockResolvedValue(json({ server_id: 'srv-1', status: 'connected' }));
    const r = await connectorsApi.completePkceOAuth('srv-1', 'c&1', 'st');
    const url = new URL(String(f.mock.calls[0][0]));
    expect(url.pathname).toMatch(/\/connectors\/oauth\/callback$/);
    expect(url.searchParams.get('server_id')).toBe('srv-1');
    expect(url.searchParams.get('code')).toBe('c&1');
    expect(url.searchParams.get('state')).toBe('st');
    expect(r.status).toBe('connected');
  });
});

describe('v1 suggestion apply', () => {
  test('the 410 Gone message pointing to the experiments endpoint reaches the caller', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      json({ detail: 'Applying v1 optimization suggestions is no longer supported: it never changed any agent. Use POST /intelligence/experiments/{experiment_id}/apply to apply a self-optimizer v2 candidate config.' }, 410),
    );
    const err = await selfImprovementApi.applySuggestion('sug-1').catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(410);
    expect((err as ApiError).message).toMatch(/\/intelligence\/experiments\/\{experiment_id\}\/apply/);
  });
});

describe('nullable metric formatters', () => {
  test('map null / undefined / NaN to a dash instead of 0 or a crash', () => {
    for (const v of [null, undefined, Number.NaN, Number.POSITIVE_INFINITY]) {
      expect(fmtRatioPct(v)).toBe(NO_VALUE);
      expect(fmtFixed(v)).toBe(NO_VALUE);
      expect(fmtUsd(v)).toBe(NO_VALUE);
      expect(isMetric(v)).toBe(false);
    }
  });

  test('format real values, including a real zero', () => {
    expect(fmtRatioPct(0)).toBe('0.0%');
    expect(fmtRatioPct(0.834)).toBe('83.4%');
    expect(fmtRatioPct(0.834, 0)).toBe('83%');
    expect(fmtFixed(0.8, 2)).toBe('0.80');
    expect(fmtUsd(0.0123)).toBe('$0.0123');
    expect(fmtUsd(12.5)).toBe('$12.50');
  });
});
