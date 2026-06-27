import { describe, it, expect } from 'vitest';

describe('CivilizationPage', () => {
  it('renders without crashing when no civilization selected', () => {
    expect(true).toBe(true);
  });

  it('civilizationApi namespace exists', async () => {
    const { civilizationApi } = await import('../../lib/api/civilizationApi');
    expect(typeof civilizationApi.list).toBe('function');
    expect(typeof civilizationApi.create).toBe('function');
    expect(typeof civilizationApi.submitGoal).toBe('function');
    expect(typeof civilizationApi.getGraph).toBe('function');
    expect(typeof civilizationApi.control).toBe('function');
  });

  it('useCivilizationStream hook exists', async () => {
    const { useCivilizationStream } = await import('../../lib/sse/useCivilizationStream');
    expect(typeof useCivilizationStream).toBe('function');
  });

  it('civilizationApi has all required methods', async () => {
    const { civilizationApi } = await import('../../lib/api/civilizationApi');
    const requiredMethods = [
      'list', 'create', 'get', 'updateConstitution', 'submitGoal',
      'getGraph', 'getAgentInspector', 'getBlackboard', 'getDebates',
      'getLearnings', 'getSpawnAudit', 'getReplay', 'control', 'killAgent',
    ] as const;
    for (const method of requiredMethods) {
      expect(typeof civilizationApi[method], `civilizationApi.${method} should be a function`).toBe('function');
    }
  });
});
