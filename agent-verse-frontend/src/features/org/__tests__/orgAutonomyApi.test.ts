import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

describe('orgAutonomyApi', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    sessionStorage.clear();
    localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('get issues GET to the autonomy path', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        autonomy_level: 3,
        settings: {
          paused: false,
          cadence_seconds: 300,
          min_interval_seconds: 600,
          max_concurrent: 2,
          max_missions_per_day: 8,
          daily_budget_usd: 5,
          per_mission_cost_ceiling_usd: 1,
          blocked_threshold: 5,
          failed_threshold: 2,
          idle_threshold: 1,
          collaboration_enabled: false,
          collaboration_daily_budget_usd: 1,
          collab_messages_per_tick: 4,
        },
      }),
    });
    vi.stubGlobal('fetch', fetchMock);

    const { setApiKey } = await import('@/lib/api/client');
    setApiKey('test-key');
    const { orgAutonomyApi } = await import('../api');

    const result = await orgAutonomyApi.get('org-1');

    expect(fetchMock.mock.calls.length).toBe(1);
    const [url, options] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url.endsWith('/v1/org/org-1/autonomy')).toBe(true);
    expect(options?.method ?? 'GET').toBe('GET');
    expect(result.autonomy_level).toBe(3);
    expect(result.settings.max_concurrent).toBe(2);
  });

  it('patch issues PATCH to the autonomy path with the JSON body', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        autonomy_level: 4,
        settings: {
          paused: true,
          cadence_seconds: 300,
          min_interval_seconds: 600,
          max_concurrent: 2,
          max_missions_per_day: 8,
          daily_budget_usd: 5,
          per_mission_cost_ceiling_usd: 1,
          blocked_threshold: 5,
          failed_threshold: 2,
          idle_threshold: 1,
          collaboration_enabled: false,
          collaboration_daily_budget_usd: 1,
          collab_messages_per_tick: 4,
        },
      }),
    });
    vi.stubGlobal('fetch', fetchMock);

    const { setApiKey } = await import('@/lib/api/client');
    setApiKey('test-key');
    const { orgAutonomyApi } = await import('../api');

    const body = { autonomy_level: 4, settings: { paused: true } };
    const result = await orgAutonomyApi.patch('org-1', body);

    expect(fetchMock.mock.calls.length).toBe(1);
    const [url, options] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url.endsWith('/v1/org/org-1/autonomy')).toBe(true);
    expect(options.method).toBe('PATCH');
    expect(JSON.parse(options.body as string)).toEqual(body);
    expect(result.autonomy_level).toBe(4);
    expect(result.settings.paused).toBe(true);
  });

  it('decisions issues GET to the decisions path with limit', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ([
        {
          id: 'd-1',
          tick_id: 't-1',
          kind: 'reactive',
          rationale: 'because',
          target_goal: 'goal text',
          action: 'propose',
          guardrail_verdict: 'allow',
          reason: null,
          est_cost_usd: 0.5,
          mission_id: null,
          created_at: '2026-09-14T00:00:00Z',
        },
      ]),
    });
    vi.stubGlobal('fetch', fetchMock);

    const { setApiKey } = await import('@/lib/api/client');
    setApiKey('test-key');
    const { orgAutonomyApi } = await import('../api');

    const result = await orgAutonomyApi.decisions('org-1', 25);

    expect(fetchMock.mock.calls.length).toBe(1);
    const [url, options] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url.endsWith('/v1/org/org-1/brain/decisions?limit=25')).toBe(true);
    expect(options?.method ?? 'GET').toBe('GET');
    expect(Array.isArray(result)).toBe(true);
    expect(result[0].id).toBe('d-1');
  });

  it('decisions defaults limit to 50 when omitted', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ([]),
    });
    vi.stubGlobal('fetch', fetchMock);

    const { setApiKey } = await import('@/lib/api/client');
    setApiKey('test-key');
    const { orgAutonomyApi } = await import('../api');

    await orgAutonomyApi.decisions('org-1');

    const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url.endsWith('/v1/org/org-1/brain/decisions?limit=50')).toBe(true);
  });

  it('approveProposal issues POST to the approve path', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ goal_id: 'g-1', dispatched: true }),
    });
    vi.stubGlobal('fetch', fetchMock);

    const { setApiKey } = await import('@/lib/api/client');
    setApiKey('test-key');
    const { orgAutonomyApi } = await import('../api');

    const result = await orgAutonomyApi.approveProposal('org-1', 'mission-1');

    const [url, options] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url.endsWith('/v1/org/org-1/brain/proposals/mission-1/approve')).toBe(true);
    expect(options.method).toBe('POST');
    expect(result.goal_id).toBe('g-1');
    expect(result.dispatched).toBe(true);
  });

  it('rejectProposal issues POST to the reject path', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ id: 'mission-1', status: 'cancelled' }),
    });
    vi.stubGlobal('fetch', fetchMock);

    const { setApiKey } = await import('@/lib/api/client');
    setApiKey('test-key');
    const { orgAutonomyApi } = await import('../api');

    const result = await orgAutonomyApi.rejectProposal('org-1', 'mission-1');

    const [url, options] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url.endsWith('/v1/org/org-1/brain/proposals/mission-1/reject')).toBe(true);
    expect(options.method).toBe('POST');
    expect(result.status).toBe('cancelled');
  });
});
