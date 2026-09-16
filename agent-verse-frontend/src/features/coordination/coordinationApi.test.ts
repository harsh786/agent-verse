import { describe, it, expect, vi, beforeEach } from 'vitest';
import { coordinationApi } from './coordinationApi';
import { apiFetch, ApiError } from '@/lib/api/client';

vi.mock('@/lib/api/client', () => ({
  apiFetch: vi.fn(),
  ApiError: class ApiError extends Error {
    status: number;
    body: unknown;
    constructor(status: number, message: string, body?: unknown) {
      super(message);
      this.name = 'ApiError';
      this.status = status;
      this.body = body;
    }
  },
}));

const mockApiFetch = vi.mocked(apiFetch);

describe('coordinationApi.getRun', () => {
  beforeEach(() => {
    mockApiFetch.mockReset();
  });

  it('fetches session, messages, ledger, moa, camel, generative, swarm and auction in parallel', async () => {
    const session = { session_id: 's1' };
    const messages = { items: [{ message_id: 'm1' }] };
    const ledger = { version: 1 };
    const moa = { items: [] };
    const camel = { items: [] };
    const generative = { items: [] };
    const swarm = { nodes: [], edges: [] };
    const auction = { items: [], sealed_bid_count: 0 };

    mockApiFetch.mockImplementation(async (path: string) => {
      if (path === '/api/v1/coordination/sessions/s1') return session;
      if (path.includes('/messages')) return messages;
      if (path.includes('/ledger')) return ledger;
      if (path.includes('/moa/layers')) return moa;
      if (path.includes('/camel')) return camel;
      if (path.includes('/generative')) return generative;
      if (path.includes('/swarm')) return swarm;
      if (path.includes('/auction')) return auction;
      throw new Error(`unexpected path ${path}`);
    });

    const result = await coordinationApi.getRun('s1');

    expect(result).toEqual({ session, messages, ledger, moa, camel, generative, swarm, auction });
    expect(mockApiFetch).toHaveBeenCalledWith('/api/v1/coordination/sessions/s1');
    expect(mockApiFetch).toHaveBeenCalledWith(
      '/api/v1/coordination/sessions/s1/messages?after_sequence=0&limit=500',
    );
    expect(mockApiFetch).toHaveBeenCalledWith('/api/v1/coordination/sessions/s1/ledger');
    expect(mockApiFetch).toHaveBeenCalledWith(
      '/api/v1/coordination/sessions/s1/moa/layers?after_layer=-1&limit=100',
    );
    expect(mockApiFetch).toHaveBeenCalledWith('/api/v1/coordination/sessions/s1/camel');
    expect(mockApiFetch).toHaveBeenCalledWith('/api/v1/coordination/sessions/s1/generative');
    expect(mockApiFetch).toHaveBeenCalledWith('/api/v1/coordination/sessions/s1/swarm');
    expect(mockApiFetch).toHaveBeenCalledWith('/api/v1/coordination/sessions/s1/auction');
  });

  it('encodes the sessionId into the URL path', async () => {
    mockApiFetch.mockResolvedValue({});
    await coordinationApi.getRun('needs encoding/x');
    expect(mockApiFetch).toHaveBeenCalledWith(
      expect.stringContaining('/api/v1/coordination/sessions/needs%20encoding%2Fx'),
    );
  });

  it('falls back to null for the ledger on a 404 (feature not yet enabled for this run)', async () => {
    mockApiFetch.mockImplementation(async (path: string) => {
      if (path.includes('/ledger')) throw new ApiError(404, 'Not Found');
      return {};
    });
    const result = await coordinationApi.getRun('s1');
    expect(result.ledger).toBeNull();
  });

  it('re-throws a non-404 error from the ledger fetch', async () => {
    mockApiFetch.mockImplementation(async (path: string) => {
      if (path.includes('/ledger')) throw new ApiError(500, 'Server error');
      return {};
    });
    await expect(coordinationApi.getRun('s1')).rejects.toThrow('Server error');
  });

  it('re-throws a non-ApiError thrown from the ledger fetch (no status field)', async () => {
    mockApiFetch.mockImplementation(async (path: string) => {
      if (path.includes('/ledger')) throw new Error('boom');
      return {};
    });
    await expect(coordinationApi.getRun('s1')).rejects.toThrow('boom');
  });
});

describe('coordinationApi.transition', () => {
  beforeEach(() => {
    mockApiFetch.mockReset();
    mockApiFetch.mockResolvedValue({ ok: true });
  });

  it('POSTs to the cancel endpoint with the expected version and an idempotency key', async () => {
    await coordinationApi.transition('s1', 'cancel', 3);
    expect(mockApiFetch).toHaveBeenCalledWith(
      '/api/v1/coordination/sessions/s1/cancel',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ expected_version: 3 }),
        headers: expect.objectContaining({ 'Idempotency-Key': expect.any(String) }),
      }),
    );
  });

  it('POSTs to the resume endpoint', async () => {
    await coordinationApi.transition('s1', 'resume', 5);
    const [path, init] = mockApiFetch.mock.calls[0];
    expect(path).toBe('/api/v1/coordination/sessions/s1/resume');
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({ expected_version: 5 });
  });
});
