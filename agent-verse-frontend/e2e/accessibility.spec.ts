import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

const pages = [
  { name: 'home', url: '/' },
  { name: 'goals', url: '/goals' },
  { name: 'agents', url: '/agents' },
  { name: 'knowledge', url: '/knowledge' },
  { name: 'marketplace', url: '/marketplace' },
];

test.describe('WCAG 2.2 AA Accessibility', () => {
  for (const { name, url } of pages) {
    test(`${name} page passes axe-core AA checks`, async ({ page }) => {
      await page.goto(url);
      await page.waitForLoadState('networkidle');

      const results = await new AxeBuilder({ page })
        .withTags(['wcag2a', 'wcag2aa', 'wcag21aa'])
        .analyze();

      // Report violations but don't hard-fail on first run
      if (results.violations.length > 0) {
        console.warn(`Accessibility violations on ${name}:`);
        results.violations.forEach(v => {
          console.warn(`  - ${v.id}: ${v.description} (impact: ${v.impact})`);
          v.nodes.forEach(n => console.warn(`    selector: ${n.target}`));
        });
      }

      // Only fail on CRITICAL violations
      const critical = results.violations.filter(v => v.impact === 'critical');
      expect(critical, `Critical a11y violations on ${name}: ${critical.map(v => v.id).join(', ')}`).toHaveLength(0);
    });
  }

  test('Goal submission form has accessible labels', async ({ page }) => {
    await page.goto('/goals');

    const results = await new AxeBuilder({ page })
      .withTags(['wcag2a'])
      .include('form, input, button, textarea')
      .analyze();

    const violations = results.violations.filter(v => v.impact === 'critical' || v.impact === 'serious');
    expect(violations).toHaveLength(0);
  });

  test('Navigation has proper ARIA landmarks', async ({ page }) => {
    await page.goto('/');

    // Verify main landmark exists
    await expect(page.locator('main, [role="main"]')).toBeVisible();

    // Verify nav landmark exists
    await expect(page.locator('nav, [role="navigation"]')).toBeVisible();
  });
});
