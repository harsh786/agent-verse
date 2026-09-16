/**
 * Landing Page E2E Tests
 *
 * Covers / (src/features/landing/LandingPage.tsx) — the public marketing page,
 * eagerly imported (not lazy) and rendered with no auth. Navigated to fresh,
 * no setupAuth/mocking needed since nothing here calls the backend.
 *
 *   1. Renders hero headline, tagline, and proof stats
 *   2. Renders nav bar brand and links
 *   3. Renders capability/footer content
 *   4. "Get started" nav CTA navigates to /auth
 *   5. Hero "Launch your first agent" CTA navigates to /auth
 *   6. "Sign in to dashboard" CTA navigates to /auth
 */
import { test, expect } from '@playwright/test';

test.describe('Landing Page — Content', () => {
  test('1. Renders hero headline, typewriter tagline, and proof stats', async ({ page }) => {
    await page.goto('/');

    await expect(page.getByRole('heading', { name: /your agents/i })).toBeVisible({ timeout: 10000 });
    await expect(page.getByText(/every tool\. zero code\./i)).toBeVisible();
    await expect(page.getByText(/227 real-world connectors/i)).toBeVisible();
    await expect(page.getByText('227+')).toBeVisible();
    await expect(page.getByText('connectors')).toBeVisible();
  });

  test('2. Renders nav bar with brand and section links', async ({ page }) => {
    await page.goto('/');

    await expect(page.getByText('AgentVerse').first()).toBeVisible({ timeout: 10000 });
    await expect(page.getByRole('link', { name: 'Platform' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'Connectors' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'Governance' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'Use Cases' })).toBeVisible();
  });

  test('3. Renders footer with platform/enterprise/developer link groups', async ({ page }) => {
    await page.goto('/');

    await expect(page.getByText(/the autonomous ai operating system/i).first()).toBeVisible({ timeout: 10000 });
    await page.getByText(/© 2026 AgentVerse/i).scrollIntoViewIfNeeded();
    await expect(page.getByText(/© 2026 AgentVerse/i)).toBeVisible();
    await expect(page.getByText('Autonomous Agents')).toBeVisible();
    await expect(page.getByText('HITL Approval')).toBeVisible();
    await expect(page.getByText('Python SDK')).toBeVisible();
  });
});

test.describe('Landing Page — CTA navigation', () => {
  test('4. Nav bar "Get started" navigates to /auth', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByRole('heading', { name: /your agents/i })).toBeVisible({ timeout: 10000 });

    await page.getByRole('button', { name: /get started/i }).click();
    await expect(page).toHaveURL(/\/auth$/);
  });

  test('5. Nav bar "Sign in" navigates to /auth', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByRole('heading', { name: /your agents/i })).toBeVisible({ timeout: 10000 });

    await page.getByRole('button', { name: /^sign in$/i }).click();
    await expect(page).toHaveURL(/\/auth$/);
  });

  test('6. Hero "Launch your first agent" CTA navigates to /auth', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByRole('heading', { name: /your agents/i })).toBeVisible({ timeout: 10000 });

    await page.getByRole('button', { name: /launch your first agent/i }).first().click();
    await expect(page).toHaveURL(/\/auth$/);
  });

  test('7. Bottom CTA "Sign in to dashboard" navigates to /auth', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByRole('heading', { name: /your agents/i })).toBeVisible({ timeout: 10000 });

    await page.getByRole('button', { name: /sign in to dashboard/i }).scrollIntoViewIfNeeded();
    await page.getByRole('button', { name: /sign in to dashboard/i }).click();
    await expect(page).toHaveURL(/\/auth$/);
  });
});
