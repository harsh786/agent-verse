import { describe, it, expect, vi, beforeEach } from 'vitest';
import { AgentVerseClient } from '../src/client.js';
import { AuthError, GoalFailedError, GoalTimeoutError } from '../src/errors.js';

const mockFetch = vi.fn();
vi.stubGlobal('fetch', mockFetch);

function makeClient() {
  return new AgentVerseClient('test-key', 'http://localhost:8000');
}

function ok(data: unknown, status = 200) {
  return Promise.resolve({
    ok: status < 400, status,
    json: () => Promise.resolve(data),
    text: () => Promise.resolve(JSON.stringify(data)),
  } as Response);
}

describe('AgentVerseClient', () => {
  beforeEach(() => vi.clearAllMocks());

  it('submitGoal returns Goal', async () => {
    mockFetch.mockResolvedValueOnce(ok({ goal_id: 'g1', goal: 'test', status: 'planning' }));
    const client = makeClient();
    const goal = await client.submitGoal('test goal');
    expect(goal.goal_id).toBe('g1');
  });

  it('getGoal returns Goal', async () => {
    mockFetch.mockResolvedValueOnce(ok({ goal_id: 'g1', goal: 'test', status: 'complete' }));
    const client = makeClient();
    const goal = await client.getGoal('g1');
    expect(goal.status).toBe('complete');
  });

  it('throws AuthError on 401', async () => {
    mockFetch.mockResolvedValueOnce({ ok: false, status: 401, text: () => Promise.resolve('Unauthorized') } as Response);
    const client = makeClient();
    await expect(client.listGoals()).rejects.toThrow(AuthError);
  });

  it('waitForGoal resolves on complete', async () => {
    mockFetch
      .mockResolvedValueOnce(ok({ goal_id: 'g1', goal: 'test', status: 'executing' }))
      .mockResolvedValueOnce(ok({ goal_id: 'g1', goal: 'test', status: 'complete' }));
    const client = makeClient();
    const result = await client.waitForGoal('g1', { pollInterval: 0.01 });
    expect(result.status).toBe('complete');
  });

  it('waitForGoal throws GoalFailedError on failed', async () => {
    mockFetch.mockResolvedValue(ok({ goal_id: 'g1', goal: 'test', status: 'failed' }));
    const client = makeClient();
    await expect(client.waitForGoal('g1', { pollInterval: 0.01 })).rejects.toThrow(GoalFailedError);
  });

  it('waitForGoal throws GoalTimeoutError', async () => {
    mockFetch.mockResolvedValue(ok({ goal_id: 'g1', goal: 'test', status: 'executing' }));
    const client = makeClient();
    await expect(client.waitForGoal('g1', { timeout: 0.02, pollInterval: 0.01 }))
      .rejects.toThrow(GoalTimeoutError);
  });

  it('listAgents returns array', async () => {
    mockFetch.mockResolvedValueOnce(ok([{ agent_id: 'a1', name: 'Test', autonomy_mode: 'supervised' }]));
    const client = makeClient();
    const agents = await client.listAgents();
    expect(agents[0].agent_id).toBe('a1');
  });
});
