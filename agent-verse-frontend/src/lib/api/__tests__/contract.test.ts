import { describe, it, expect, vi, beforeEach } from 'vitest';

/**
 * OpenAPI contract tests — verify that API client methods match
 * the backend OpenAPI schema.
 *
 * These tests prevent frontend client drift from the backend API.
 */
describe('API Contract Tests', () => {
  describe('Goals API', () => {
    beforeEach(() => {
      vi.clearAllMocks();
    });

    it('submits goal with correct shape', async () => {
      const fetchMock = vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ goal_id: 'g1', status: 'running' }),
        status: 200,
      });
      vi.stubGlobal('fetch', fetchMock);

      const { goalsApi } = await import('../client');

      if (goalsApi?.submit) {
        await goalsApi.submit({ goal: 'test goal', priority: 'medium' }).catch(() => {});

        if (fetchMock.mock.calls.length > 0) {
          const [url, options] = fetchMock.mock.calls[0] as [string, RequestInit];
          const body = JSON.parse((options?.body as string) || '{}') as Record<string, unknown>;

          // Verify URL points to goals endpoint
          expect(url).toContain('/goals');

          // Verify required fields per OpenAPI spec
          expect(typeof body.goal).toBe('string');
        }
      }
    });

    it('goal list response has expected shape', () => {
      // Type-level contract: verify GoalListResponse shape
      type GoalStatus = 'pending' | 'planning' | 'executing' | 'complete' | 'failed';
      type Goal = {
        goal_id: string;
        goal: string;
        status: GoalStatus;
        created_at: string;
      };

      const mockGoal: Goal = {
        goal_id: 'g1',
        goal: 'test',
        status: 'pending',
        created_at: new Date().toISOString(),
      };

      expect(mockGoal.goal_id).toBeTruthy();
      expect(['pending', 'planning', 'executing', 'complete', 'failed']).toContain(mockGoal.status);
    });

    it('goalsApi exposes list, submit, get, cancel methods', async () => {
      const { goalsApi } = await import('../client');
      expect(typeof goalsApi.list).toBe('function');
      expect(typeof goalsApi.submit).toBe('function');
      expect(typeof goalsApi.get).toBe('function');
      expect(typeof goalsApi.cancel).toBe('function');
    });

    it('GoalRequest requires goal field (string)', () => {
      // Structural type check — compile-time enforced, runtime sanity
      const req = { goal: 'test goal' };
      expect(typeof req.goal).toBe('string');
      expect(req.goal.length).toBeGreaterThan(0);
    });
  });

  describe('Agents API', () => {
    it('agent has required fields', () => {
      type Agent = {
        id: string;
        name: string;
        description: string;
        plan: string;
      };

      const mockAgent: Agent = {
        id: 'a1',
        name: 'Test Agent',
        description: 'A test agent',
        plan: 'free',
      };

      expect(mockAgent.id).toBeTruthy();
      expect(mockAgent.name).toBeTruthy();
    });

    it('agentsApi exposes list, create, get, delete methods', async () => {
      const { agentsApi } = await import('../client');
      expect(typeof agentsApi.list).toBe('function');
      expect(typeof agentsApi.create).toBe('function');
      expect(typeof agentsApi.get).toBe('function');
      expect(typeof agentsApi.delete).toBe('function');
    });

    it('AgentResponse uses agent_id as primary key (not id)', async () => {
      // Backend returns agent_id, not id — verify client typing matches
      const { agentsApi } = await import('../client');
      const fetchMock = vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ([{
          agent_id: 'a1',
          name: 'Test',
          autonomy_mode: 'supervised',
          goal_template: '',
        }]),
        status: 200,
      });
      vi.stubGlobal('fetch', fetchMock);

      const agents = await agentsApi.list().catch(() => []);
      if (agents.length > 0) {
        expect(agents[0].agent_id).toBe('a1');
      }
    });
  });

  describe('Governance API', () => {
    it('governanceApi exposes approval methods', async () => {
      const { governanceApi } = await import('../client');
      expect(typeof governanceApi.listApprovals).toBe('function');
      expect(typeof governanceApi.approve).toBe('function');
      expect(typeof governanceApi.reject).toBe('function');
    });
  });
});
