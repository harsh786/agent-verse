import { describe, it, expect } from 'vitest';

describe('RpaLivePage', () => {
  it('module exists', async () => {
    const mod = await import('../RpaLivePage');
    expect(mod.RpaLivePage).toBeDefined();
  });
});
