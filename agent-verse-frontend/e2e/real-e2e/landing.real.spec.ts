/**
 * Real E2E tests for the public Landing page — NO HTTP mocking.
 *
 * Route: '/' (public, no auth required), page src/features/landing/LandingPage.tsx.
 *
 * This page needs no tenant/auth fixtures — it uses the plain Playwright `page`
 * fixture and hits the live frontend directly. No backend API calls are made
 * by the page itself; these tests just verify the marketing page renders for
 * an anonymous visitor and that its primary CTAs route to /auth.
 */
import { test, expect } from '@playwright/test';

const FRONTEND_BASE = process.env.BASE_URL ?? 'http://localhost:5173';

test.describe('Landing — public page renders', () => {
  test('renders hero content for an anonymous visitor', async ({ page }) => {
    await page.goto(`${FRONTEND_BASE}/`, { waitUntil: 'networkidle' });

    await expect(page.locator('body')).toBeVisible();
    await expect(page.getByRole('heading', { name: /Your agents\./i })).toBeVisible({ timeout: 10000 });

    const text = await page.locator('body').textContent();
    expect(text!.length).toBeGreaterThan(0);
    expect(text!.toLowerCase()).not.toContain('internal server error');
  });

  test('shows the AgentVerse brand in the nav', async ({ page }) => {
    await page.goto(`${FRONTEND_BASE}/`, { waitUntil: 'networkidle' });
    await expect(page.getByRole('navigation').getByText('AgentVerse')).toBeVisible({ timeout: 10000 });
  });

  test('"Get started" CTA in the nav routes to /auth', async ({ page }) => {
    await page.goto(`${FRONTEND_BASE}/`, { waitUntil: 'networkidle' });

    await page.getByRole('navigation').getByRole('button', { name: 'Get started' }).click();
    await expect(page).toHaveURL(/\/auth/, { timeout: 10000 });
  });

  test('hero "Launch your first agent" CTA routes to /auth', async ({ page }) => {
    await page.goto(`${FRONTEND_BASE}/`, { waitUntil: 'networkidle' });

    await page.getByRole('button', { name: /Launch your first agent/i }).first().click();
    await expect(page).toHaveURL(/\/auth/, { timeout: 10000 });
  });

  test('capability sections and connector marquee render real content', async ({ page }) => {
    await page.goto(`${FRONTEND_BASE}/`, { waitUntil: 'networkidle' });

    await expect(page.getByRole('heading', { name: 'Autonomous Intelligence' })).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('227 production-certified connectors')).toBeVisible();
  });
});
