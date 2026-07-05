import { describe, it, expect } from 'vitest';

const EXPECTED_COMPONENT_TRANSLATIONS = [
  { label: 'src/features/goals/GoalsListPage.tsx', importPath: '../../features/goals/GoalsListPage' },
  { label: 'src/features/agents/AgentsListPage.tsx', importPath: '../../features/agents/AgentsListPage' },
  { label: 'src/features/agents/AgentDetailPage.tsx', importPath: '../../features/agents/AgentDetailPage' },
  { label: 'src/components/ui/Sidebar.tsx', importPath: '../../components/ui/Sidebar' },
];

describe('i18n translation coverage', () => {
  EXPECTED_COMPONENT_TRANSLATIONS.forEach(({ label, importPath }) => {
    it(`${label} uses useTranslation`, async () => {
      const mod = await import(`${importPath}?raw`);
      expect(mod.default, `${label} must import useTranslation`).toContain('useTranslation');
    });
  });

  it('en.json and hi.json have matching key structure', async () => {
    const en = await import('./locales/en.json');
    const hi = await import('./locales/hi.json');

    function getLeafKeys(obj: any, prefix = ''): string[] {
      return Object.entries(obj).flatMap(([k, v]) =>
        typeof v === 'object' ? getLeafKeys(v, `${prefix}${k}.`) : [`${prefix}${k}`]
      );
    }

    const enKeys = getLeafKeys(en).sort();
    const hiKeys = getLeafKeys(hi).sort();
    expect(hiKeys).toEqual(enKeys);
  });

  it('all hi.json translations are non-empty', async () => {
    const hi = await import('./locales/hi.json');
    function checkNonEmpty(obj: any, path = '') {
      for (const [k, v] of Object.entries(obj)) {
        if (typeof v === 'string') {
          expect(v.length, `hi.json key "${path}${k}" must not be empty`).toBeGreaterThan(0);
        } else if (typeof v === 'object') {
          checkNonEmpty(v, `${path}${k}.`);
        }
      }
    }
    checkNonEmpty(hi);
  });
});
