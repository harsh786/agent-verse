/**
 * Onboarding Wizard — E2E Tests
 *
 * 4-step wizard: Configure LLM → Add Connector → Create Agent → Run First Goal.
 * Each step's "primary" action calls a distinct backend endpoint:
 *   1. PUT  /tenants/me/llm       (settingsApi.setLLM)
 *   2. POST /connectors           (connectorsApi.register)
 *   3. POST /agents/create        (agentsApi.createNl)
 *   4. POST /goals                (goalsApi.submit)
 */
import { test, expect, type Page } from '@playwright/test';
import { setupAuth } from './helpers/auth';

async function mockOnboardingApis(page: Page): Promise<void> {
  await page.route('**/tenants/me/llm', (route) => {
    if (route.request().method() === 'PUT') {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ provider: 'openai', default_model: 'gpt-4o' }),
      });
    }
    return route.continue();
  });

  await page.route(/localhost:8000\/connectors$/, (route) => {
    if (route.request().method() === 'POST') {
      return route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify({ server_id: 'conn-1', name: 'GitHub', status: 'active' }),
      });
    }
    return route.continue();
  });

  await page.route('**/agents/create', (route) =>
    route.fulfill({
      status: 201,
      contentType: 'application/json',
      body: JSON.stringify({ agent_id: 'agent-onboard-1', name: 'New Agent' }),
    })
  );

  await page.route(/localhost:8000\/goals$/, (route) => {
    if (route.request().method() === 'POST') {
      return route.fulfill({
        status: 202,
        contentType: 'application/json',
        body: JSON.stringify({ goal_id: 'goal-onboard-1', id: 'goal-onboard-1', status: 'planning' }),
      });
    }
    return route.continue();
  });
}

test.describe('Onboarding — Wizard Navigation', () => {
  test('1. Loads and renders step 1 (Configure LLM) with progress indicators', async ({ page }) => {
    await setupAuth(page);
    await mockOnboardingApis(page);
    await page.goto('/onboarding');

    await expect(page.getByRole('heading', { name: /configure your llm provider/i })).toBeVisible({
      timeout: 10000,
    });
    await expect(page.getByText('AgentVerse')).toBeVisible();
    await expect(page.getByText('Configure LLM')).toBeVisible();
    await expect(page.getByText('Add Connector')).toBeVisible();
    await expect(page.getByText('Create Agent')).toBeVisible();
    await expect(page.getByText('Run First Goal')).toBeVisible();
  });

  test('2. Step 1 — empty API key shows validation error and does not advance', async ({ page }) => {
    await setupAuth(page);
    await mockOnboardingApis(page);
    await page.goto('/onboarding');

    await expect(page.getByRole('heading', { name: /configure your llm provider/i })).toBeVisible({
      timeout: 10000,
    });
    await page.getByRole('button', { name: /save & continue/i }).click();

    await expect(page.getByText(/api key is required/i)).toBeVisible({ timeout: 5000 });
    await expect(page.getByRole('heading', { name: /configure your llm provider/i })).toBeVisible();
  });

  test('3. Full wizard flow — completing all 4 steps reaches the goal-submitted state', async ({ page }) => {
    await setupAuth(page);
    await mockOnboardingApis(page);
    await page.goto('/onboarding');

    // Step 1: Configure LLM
    await expect(page.getByRole('heading', { name: /configure your llm provider/i })).toBeVisible({
      timeout: 10000,
    });
    await page.getByPlaceholder(/your openai api key/i).fill('sk-test-key-123');
    await page.getByRole('button', { name: /save & continue/i }).click();
    await expect(page.getByText(/llm configured!/i)).toBeVisible({ timeout: 5000 });

    // Step 2: Register Connector
    await expect(page.getByRole('heading', { name: /register your first connector/i })).toBeVisible({
      timeout: 5000,
    });
    await page.getByRole('button', { name: /register & continue/i }).click();
    await expect(page.getByText(/connector registered!/i)).toBeVisible({ timeout: 5000 });

    // Step 3: Create Agent
    await expect(page.getByRole('heading', { name: /create your first agent/i })).toBeVisible({
      timeout: 5000,
    });
    await page.getByRole('button', { name: /create agent/i }).click();
    await expect(page.getByText(/agent created!/i)).toBeVisible({ timeout: 5000 });

    // Step 4: Run First Goal
    await expect(page.getByRole('heading', { name: /run your first goal/i })).toBeVisible({ timeout: 5000 });
    await page.getByRole('button', { name: /run goal/i }).click();

    await expect(page.getByText(/goal submitted!/i)).toBeVisible({ timeout: 5000 });
    await expect(page.getByText(/goal-onboard-1/i)).toBeVisible();
    await expect(page.getByRole('button', { name: /go to dashboard/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /watch goal/i })).toBeVisible();
  });

  test('4. Step 2 — "Skip for now" advances to Create Agent without calling the connector API', async ({
    page,
  }) => {
    let connectorPostCalled = false;
    await setupAuth(page);
    await mockOnboardingApis(page);
    await page.route(/localhost:8000\/connectors$/, (route) => {
      if (route.request().method() === 'POST') connectorPostCalled = true;
      return route.continue();
    });
    await page.goto('/onboarding');

    await expect(page.getByRole('heading', { name: /configure your llm provider/i })).toBeVisible({
      timeout: 10000,
    });
    await page.getByPlaceholder(/your openai api key/i).fill('sk-test-key-123');
    await page.getByRole('button', { name: /save & continue/i }).click();
    await expect(page.getByRole('heading', { name: /register your first connector/i })).toBeVisible({
      timeout: 5000,
    });

    await page.getByRole('button', { name: /skip for now/i }).click();

    await expect(page.getByRole('heading', { name: /create your first agent/i })).toBeVisible({
      timeout: 5000,
    });
    expect(connectorPostCalled).toBe(false);
  });

  test('5. "Skip setup and go to dashboard" link navigates to /dashboard', async ({ page }) => {
    await setupAuth(page);
    await mockOnboardingApis(page);
    await page.goto('/onboarding');

    await expect(page.getByRole('heading', { name: /configure your llm provider/i })).toBeVisible({
      timeout: 10000,
    });
    await page.getByRole('button', { name: /skip setup and go to dashboard/i }).click();

    await expect(page).toHaveURL(/\/dashboard/, { timeout: 5000 });
  });

  test('6. Goal step error state shows the error and Run Goal stays clickable', async ({ page }) => {
    await setupAuth(page);
    await mockOnboardingApis(page);
    await page.route(/localhost:8000\/goals$/, (route) => {
      if (route.request().method() === 'POST') {
        return route.fulfill({
          status: 500,
          contentType: 'application/json',
          body: JSON.stringify({ detail: 'Internal error' }),
        });
      }
      return route.continue();
    });
    await page.goto('/onboarding');

    // Fast-forward through steps 1-3
    await expect(page.getByRole('heading', { name: /configure your llm provider/i })).toBeVisible({
      timeout: 10000,
    });
    await page.getByPlaceholder(/your openai api key/i).fill('sk-test-key-123');
    await page.getByRole('button', { name: /save & continue/i }).click();
    await expect(page.getByRole('heading', { name: /register your first connector/i })).toBeVisible({
      timeout: 5000,
    });
    await page.getByRole('button', { name: /skip for now/i }).click();
    await expect(page.getByRole('heading', { name: /create your first agent/i })).toBeVisible({
      timeout: 5000,
    });
    await page.getByRole('button', { name: /create agent/i }).click();
    await expect(page.getByRole('heading', { name: /run your first goal/i })).toBeVisible({ timeout: 5000 });

    await page.getByRole('button', { name: /run goal/i }).click();

    await expect(page.getByText(/error/i).first()).toBeVisible({ timeout: 5000 });
    await expect(page.getByRole('button', { name: /run goal/i })).toBeVisible();
  });
});
