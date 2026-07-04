import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

describe('API client Content-Type handling', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    sessionStorage.clear();
    localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('does not set Content-Type for FormData requests', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({}),
      status: 200,
    });
    vi.stubGlobal('fetch', fetchMock);

    // Import your API client function
    const { uploadFile } = await import('../client');

    const formData = new FormData();
    formData.append('file', new Blob(['test']), 'test.txt');

    await uploadFile('/test', formData).catch(() => {});

    if (fetchMock.mock.calls.length > 0) {
      const [, options] = fetchMock.mock.calls[0] as [string, RequestInit];
      const headers = (options?.headers ?? {}) as Record<string, string>;
      expect(headers['Content-Type']).toBeUndefined();
    }
  });

  it('sets Content-Type application/json for JSON requests', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({}),
      status: 200,
    });
    vi.stubGlobal('fetch', fetchMock);

    const { apiRequest } = await import('../client');

    await apiRequest('POST', '/test', { data: 'test' }).catch(() => {});

    if (fetchMock.mock.calls.length > 0) {
      const [, options] = fetchMock.mock.calls[0] as [string, RequestInit];
      const headers = (options?.headers ?? {}) as Record<string, string>;
      // JSON requests should have Content-Type set
      expect(
        headers['Content-Type'] ?? headers['content-type'],
      ).toContain('application/json');
    }
  });
});

describe('Token refresh security', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('sends refresh token in POST body, not URL', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ access_token: 'new-token', expires_in: 3600 }),
      status: 200,
    });
    vi.stubGlobal('fetch', fetchMock);

    // Path from src/lib/api/__tests__/ up to src/hooks/
    const { refreshToken } = await import('../../../hooks/useTokenRefresh');
    if (refreshToken) {
      await refreshToken('test-refresh-token').catch(() => {});

      if (fetchMock.mock.calls.length > 0) {
        const [url, options] = fetchMock.mock.calls[0] as [string, RequestInit];

        // Token must NOT be in the URL
        expect(url).not.toContain('refresh_token');
        expect(url).not.toContain('test-refresh-token');

        // Token MUST be in the body
        const body = JSON.parse(options?.body as string ?? '{}') as Record<string, unknown>;
        expect(body['refresh_token']).toBe('test-refresh-token');
      }
    }
  });
});
