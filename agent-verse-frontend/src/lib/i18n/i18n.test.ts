import { describe, it, expect } from 'vitest';
import en from './locales/en.json';
import hi from './locales/hi.json';

describe('i18n locales', () => {
  it('Hindi has same top-level keys as English', () => {
    expect(Object.keys(hi).sort()).toEqual(Object.keys(en).sort());
  });
  it('Hindi goal submit is non-empty', () => {
    expect(hi.goals.submit.length).toBeGreaterThan(0);
  });
  it('English nav.goals is a string', () => {
    expect(typeof en.nav.goals).toBe('string');
  });
});

describe('i18n usage in components', () => {
  it('GoalsListPage imports useTranslation', async () => {
    const src = await import('../../features/goals/GoalsListPage?raw');
    expect(src.default).toContain('useTranslation');
  });

  it('locale files have no empty strings', async () => {
    function checkNoEmpty(obj: Record<string, unknown>, path = ''): void {
      for (const [k, v] of Object.entries(obj)) {
        if (typeof v === 'string') {
          expect(v.length, `${path}.${k} must not be empty`).toBeGreaterThan(0);
        } else if (typeof v === 'object' && v !== null) {
          checkNoEmpty(v as Record<string, unknown>, `${path}.${k}`);
        }
      }
    }
    checkNoEmpty(hi as unknown as Record<string, unknown>);
    checkNoEmpty(en as unknown as Record<string, unknown>);
  });
});
