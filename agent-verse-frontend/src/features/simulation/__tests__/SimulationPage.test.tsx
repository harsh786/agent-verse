import { describe, it, expect } from 'vitest';

describe('SimulationPage', () => {
  it('module exists', async () => {
    const mod = await import('../SimulationPage');
    expect(mod.SimulationPage).toBeDefined();
  });
});
