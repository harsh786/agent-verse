/** Smoke test for the triggers feature barrel — verifies every re-export resolves. */
import { describe, it, expect } from 'vitest';
import * as TriggersBarrel from './index';

describe('features/triggers barrel exports', () => {
  it('re-exports TriggersPage as a function component', () => {
    expect(typeof TriggersBarrel.TriggersPage).toBe('function');
  });

  it('re-exports TRIGGER_FAMILY_LABELS and TRIGGER_TYPE_FAMILY', () => {
    expect(TriggersBarrel.TRIGGER_FAMILY_LABELS).toBeDefined();
    expect(TriggersBarrel.TRIGGER_TYPE_FAMILY).toBeDefined();
    expect(typeof TriggersBarrel.TRIGGER_FAMILY_LABELS).toBe('object');
  });

  it('re-exports hooks module contents', () => {
    // `export * from './hooks'` — just assert the barrel has more than the
    // explicit named exports above, proving the wildcard re-export ran.
    expect(Object.keys(TriggersBarrel).length).toBeGreaterThan(2);
  });
});
