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
