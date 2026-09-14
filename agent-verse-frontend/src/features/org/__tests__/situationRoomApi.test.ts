import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

describe('situationApi', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    sessionStorage.clear();
    localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('collaborationHistory GETs the collaboration-message events and maps payload → CollaborationMessage', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        data: [
          {
            id: 'evt-1',
            org_id: 'org-1',
            event_type: 'org.collaboration.message',
            title: 'Collaboration message',
            description: 'agent-a -> agent-b',
            severity: 'info',
            entity_type: 'agent',
            entity_id: 'agent-a',
            created_at: '2026-09-14T00:00:00Z',
            payload: {
              lead: 'agent-a',
              message: 'Can you review the draft?',
              from_agent: 'agent-a',
              to: 'agent-b',
              kind: 'request',
              latency_ms: 420,
              tokens: 128,
              cost_usd: 0.0032,
              mission_id: 'mission-1',
            },
            source: 'brain',
          },
        ],
        cursor: null,
        hasMore: false,
      }),
    });
    vi.stubGlobal('fetch', fetchMock);

    const { setApiKey } = await import('@/lib/api/client');
    setApiKey('test-key');
    const { situationApi } = await import('../api');

    const result = await situationApi.collaborationHistory('org-1', 25);

    expect(fetchMock.mock.calls.length).toBe(1);
    const [url, options] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain('/v1/org/org-1/events');
    expect(url).toContain('event_type=org.collaboration.message');
    expect(url).toContain('limit=25');
    expect(options?.method ?? 'GET').toBe('GET');

    expect(result).toHaveLength(1);
    expect(result[0]).toEqual({
      id:         'evt-1',
      from_agent: 'agent-a',
      to:         'agent-b',
      kind:       'request',
      message:    'Can you review the draft?',
      latency_ms: 420,
      tokens:     128,
      cost_usd:   0.0032,
      mission_id: 'mission-1',
      at:         '2026-09-14T00:00:00Z',
    });
  });

  it('collaborationHistory falls back to description/entity_id for legacy rows missing payload fields', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ([
        {
          id: 'evt-2',
          org_id: 'org-1',
          event_type: 'org.collaboration.message',
          title: 'Collaboration message',
          description: 'legacy row text',
          severity: 'info',
          entity_type: 'agent',
          entity_id: 'agent-c',
          created_at: '2026-09-14T01:00:00Z',
        },
      ]),
    });
    vi.stubGlobal('fetch', fetchMock);

    const { setApiKey } = await import('@/lib/api/client');
    setApiKey('test-key');
    const { situationApi } = await import('../api');

    const result = await situationApi.collaborationHistory('org-1');

    expect(result[0].from_agent).toBe('agent-c');
    expect(result[0].message).toBe('legacy row text');
    expect(result[0].latency_ms).toBeNull();
    expect(result[0].cost_usd).toBeNull();
  });

  it('agentAudit GETs the agent audit path and returns the bare array', async () => {
    const auditEntries = [
      {
        id: 'a-1',
        kind: 'decision',
        at: '2026-09-14T02:00:00Z',
        title: 'Proposed mission',
        detail: 'Proposed a new mission to fix the pipeline',
        cost_usd: 0.12,
        duration_ms: 340,
        mission_id: 'mission-1',
        ref: { tick_id: 't-1' },
      },
    ];
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => auditEntries,
    });
    vi.stubGlobal('fetch', fetchMock);

    const { setApiKey } = await import('@/lib/api/client');
    setApiKey('test-key');
    const { situationApi } = await import('../api');

    const result = await situationApi.agentAudit('org-1', 'agent-a', 25);

    expect(fetchMock.mock.calls.length).toBe(1);
    const [url, options] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain('/v1/org/org-1/agents/agent-a/audit');
    expect(url).toContain('limit=25');
    expect(options?.method ?? 'GET').toBe('GET');
    expect(result).toEqual(auditEntries);
  });

  it('agentAudit URL-encodes the agent id', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => [],
    });
    vi.stubGlobal('fetch', fetchMock);

    const { setApiKey } = await import('@/lib/api/client');
    setApiKey('test-key');
    const { situationApi } = await import('../api');

    await situationApi.agentAudit('org-1', 'agent/with slash');

    const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain('/agents/agent%2Fwith%20slash/audit');
  });

  it('missionTimeline GETs the mission timeline path and returns the object', async () => {
    const timeline = {
      mission_id: 'mission-1',
      status: 'active',
      phases: [
        { name: 'planning', at: '2026-09-14T00:00:00Z', until: '2026-09-14T00:05:00Z', duration_ms: 300000, agent: 'agent-a' },
      ],
      total_ms: 300000,
    };
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => timeline,
    });
    vi.stubGlobal('fetch', fetchMock);

    const { setApiKey } = await import('@/lib/api/client');
    setApiKey('test-key');
    const { situationApi } = await import('../api');

    const result = await situationApi.missionTimeline('org-1', 'mission-1');

    expect(fetchMock.mock.calls.length).toBe(1);
    const [url, options] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url.endsWith('/v1/org/org-1/missions/mission-1/timeline')).toBe(true);
    expect(options?.method ?? 'GET').toBe('GET');
    expect(result).toEqual(timeline);
  });
});
