import { describe, it, expect } from 'vitest';

describe('AuditExplorerPage', () => {
  it('module exists', async () => {
    const mod = await import('../AuditExplorerPage');
    expect(mod.AuditExplorerPage).toBeDefined();
  });
});
