/**
 * Tests for the civilizationApi client — asserts each wrapper issues the correct
 * URL + method (+ body) and returns the parsed JSON body from apiFetch.
 */
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { civilizationApi } from './civilizationApi';
import { API_BASE } from './client';
import { useAuthStore } from '@/stores/auth';

// The client prefixes every path with this (VITE_API_BASE_URL, or the default).
const BASE = API_BASE;

interface Captured {
  url: string;
  method: string;
  headers: Record<string, string>;
  body: unknown;
}

function mockFetch(payload: unknown, status = 200) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async () => {
    const body = status === 204 ? null : JSON.stringify(payload);
    return new Response(body, {
      status,
      headers: { 'Content-Type': 'application/json' },
    });
  });
}

function lastCall(spy: ReturnType<typeof mockFetch>): Captured {
  const call = spy.mock.calls[spy.mock.calls.length - 1];
  const [url, init] = call as [string, RequestInit | undefined];
  return {
    url: String(url),
    method: (init?.method ?? 'GET').toUpperCase(),
    headers: (init?.headers ?? {}) as Record<string, string>,
    body: init?.body ? JSON.parse(init.body as string) : undefined,
  };
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  useAuthStore.setState({ apiKey: 'k-123', tenantId: 't', plan: 'free', isAuthenticated: true, ssoMode: false, accessToken: '' });
});
afterEach(() => vi.restoreAllMocks());

describe('civilizationApi', () => {
  test('list() GETs /civilizations, sends the tenant key, and returns the parsed array', async () => {
    const rows = [{ id: 'c1', name: 'Alpha', status: 'active', constitution: {}, created_at: '2026-01-01' }];
    const spy = mockFetch(rows);

    const result = await civilizationApi.list();

    const c = lastCall(spy);
    expect(c.url).toBe(`${BASE}/civilizations`);
    expect(c.method).toBe('GET');
    expect(c.headers['X-API-Key']).toBe('k-123');
    expect(result).toEqual(rows);
  });

  test('create() POSTs name + constitution and returns the created civilization', async () => {
    const created = { id: 'c2', name: 'Beta', status: 'active', constitution: { max_depth: 3 }, created_at: '2026-02-02' };
    const spy = mockFetch(created);

    const result = await civilizationApi.create('Beta', { max_depth: 3 });

    const c = lastCall(spy);
    expect(c.url).toBe(`${BASE}/civilizations`);
    expect(c.method).toBe('POST');
    expect(c.body).toEqual({ name: 'Beta', constitution: { max_depth: 3 } });
    expect(result).toEqual(created);
  });

  test('create() defaults constitution to {} when omitted', async () => {
    const spy = mockFetch({ id: 'c3', name: 'Gamma', status: 'active', constitution: {}, created_at: 'x' });
    await civilizationApi.create('Gamma');
    expect(lastCall(spy).body).toEqual({ name: 'Gamma', constitution: {} });
  });

  test('get() GETs /civilizations/:id with the id in the path', async () => {
    const spy = mockFetch({ id: 'c9', name: 'Nine', status: 'paused', constitution: {}, created_at: 'x' });
    const result = await civilizationApi.get('c9');
    expect(lastCall(spy).url).toBe(`${BASE}/civilizations/c9`);
    expect(result.id).toBe('c9');
  });

  test('submitGoal() POSTs the goal with default priority "normal"', async () => {
    const spy = mockFetch({ status: 'queued', goal_id: 'g1' });
    const result = await civilizationApi.submitGoal('c1', 'do the thing');
    const c = lastCall(spy);
    expect(c.url).toBe(`${BASE}/civilizations/c1/goals`);
    expect(c.method).toBe('POST');
    expect(c.body).toEqual({ goal: 'do the thing', priority: 'normal' });
    expect(result).toEqual({ status: 'queued', goal_id: 'g1' });
  });

  test('getBlackboard() URL-encodes the optional topic query param', async () => {
    const spy = mockFetch([]);
    await civilizationApi.getBlackboard('c1', 'a b&c');
    expect(lastCall(spy).url).toBe(`${BASE}/civilizations/c1/blackboard?topic=a%20b%26c`);
  });

  test('getGraph() returns the parsed {nodes, edges} shape', async () => {
    const graph = { nodes: [{ id: 'n1', label: 'root', status: 'active', reputation: 1, depth: 0, budget_spent_usd: 0 }], edges: [] };
    mockFetch(graph);
    const result = await civilizationApi.getGraph('c1');
    expect(result.nodes).toHaveLength(1);
    expect(result.nodes[0].id).toBe('n1');
  });

  test('removeMember() issues a DELETE to the member path (204 → undefined)', async () => {
    const spy = mockFetch(undefined, 204);
    const result = await civilizationApi.removeMember('c1', 'agent-7');
    const c = lastCall(spy);
    expect(c.url).toBe(`${BASE}/civilizations/c1/members/agent-7`);
    expect(c.method).toBe('DELETE');
    expect(result).toBeUndefined();
  });
});
