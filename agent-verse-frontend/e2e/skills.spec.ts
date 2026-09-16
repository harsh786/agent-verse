/**
 * Skills — E2E Tests
 *
 * Covers /skills (src/features/skills/SkillsPage.tsx).
 *
 * Endpoints mocked:
 *   GET    /skills
 *   POST   /skills
 *   PUT    /skills/{id}
 *   PATCH  /skills/{id}
 *   DELETE /skills/{id}
 *   POST   /skills/{id}/test
 */
import { test, expect, type Page } from '@playwright/test';
import { setupAuth } from './helpers/auth';

// ── Mock data ─────────────────────────────────────────────────────────────────

interface MockSkill {
  id: string;
  name: string;
  description: string;
  trigger_hints: string[];
  instructions: string;
  allowed_tools: string[];
  token_estimate: number;
  visibility: string;
  is_platform: boolean;
  enabled?: boolean;
  use_count?: number;
}

const PLATFORM_SKILL: MockSkill = {
  id: 'skill-platform-1',
  name: 'web-research',
  description: 'Improves web research quality',
  trigger_hints: ['research', 'search', 'investigate'],
  instructions: 'Cross-reference at least 3 sources.',
  allowed_tools: ['web_search', 'document_reader'],
  token_estimate: 320,
  visibility: 'public',
  is_platform: true,
  use_count: 12,
};

const CUSTOM_SKILL: MockSkill = {
  id: 'skill-custom-1',
  name: 'my-research-skill',
  description: 'Custom deep-dive research helper',
  trigger_hints: ['deep-dive'],
  instructions: 'Always cite sources with URLs.',
  allowed_tools: ['web_search'],
  token_estimate: 150,
  visibility: 'private',
  is_platform: false,
  enabled: true,
  use_count: 3,
};

async function mockSkillsApi(
  page: Page,
  { skills = [] as MockSkill[] } = {}
): Promise<void> {
  let current = [...skills];

  await page.route(/\/skills\/[^/]+\/test$/, (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ output: 'Test run completed', matched: true }),
    })
  );

  await page.route(/\/skills\/[^/?]+$/, (route) => {
    const method = route.request().method();
    const id = route.request().url().split('/skills/')[1].split('?')[0];

    if (method === 'PUT' || method === 'PATCH') {
      const body = JSON.parse(route.request().postData() ?? '{}');
      current = current.map((s) => (s.id === id ? { ...s, ...body } : s));
      const updated = current.find((s) => s.id === id);
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(updated) });
    }
    if (method === 'DELETE') {
      current = current.filter((s) => s.id !== id);
      return route.fulfill({ status: 204, body: '' });
    }
    return route.continue();
  });

  await page.route('**/skills', (route) => {
    const method = route.request().method();
    if (method === 'POST') {
      const body = JSON.parse(route.request().postData() ?? '{}');
      const created: MockSkill = {
        id: `skill-new-${current.length + 1}`,
        description: '',
        trigger_hints: [],
        instructions: '',
        allowed_tools: [],
        token_estimate: 10,
        visibility: 'private',
        is_platform: false,
        enabled: true,
        ...body,
      };
      current = [...current, created];
      return route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify(created) });
    }
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ skills: current }) });
  });
}

test.describe('Skills — empty state', () => {
  test('1. Shows empty custom-skills message when no skills exist', async ({ page }) => {
    await setupAuth(page);
    await mockSkillsApi(page, { skills: [] });
    await page.goto('/skills');

    await expect(page.getByRole('heading', { name: 'Skills' })).toBeVisible({ timeout: 10000 });
    await expect(page.getByText(/platform skills \(0\)/i)).toBeVisible();
    await expect(page.getByText(/no platform skills available/i)).toBeVisible();
    await expect(page.getByText(/no custom skills yet/i)).toBeVisible();
  });
});

test.describe('Skills — populated state', () => {
  test('2. Renders platform and custom skill cards with their metadata', async ({ page }) => {
    await setupAuth(page);
    await mockSkillsApi(page, { skills: [PLATFORM_SKILL, CUSTOM_SKILL] });
    await page.goto('/skills');

    await expect(page.getByRole('heading', { name: 'Skills' })).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('web-research')).toBeVisible();
    await expect(page.getByText('Platform').first()).toBeVisible();
    await expect(page.getByText('my-research-skill')).toBeVisible();
    await expect(page.getByText(/~320 tokens/i)).toBeVisible();
    await expect(page.getByText(/used 12×/i)).toBeVisible();
  });

  test('3. Search filters skills by name', async ({ page }) => {
    await setupAuth(page);
    await mockSkillsApi(page, { skills: [PLATFORM_SKILL, CUSTOM_SKILL] });
    await page.goto('/skills');

    await expect(page.getByText('web-research')).toBeVisible({ timeout: 10000 });
    await page.getByPlaceholder(/search skills by name/i).fill('my-research');

    await expect(page.getByText('my-research-skill')).toBeVisible();
    await expect(page.getByText('web-research')).not.toBeVisible();
  });
});

test.describe('Skills — primary interaction', () => {
  test('4. Create Skill form adds a new custom skill to the list', async ({ page }) => {
    await setupAuth(page);
    await mockSkillsApi(page, { skills: [] });
    await page.goto('/skills');

    await expect(page.getByRole('heading', { name: 'Skills' })).toBeVisible({ timeout: 10000 });
    await page.getByRole('button', { name: /create skill/i }).click();

    await page.getByPlaceholder('my-research-skill').fill('summarizer');
    await page.getByPlaceholder(/improves web research quality/i).fill('Summarizes long documents');
    await page
      .getByPlaceholder(/always cross-reference at least 3 sources/i)
      .fill('Summarize in under 200 words.');

    await page.getByRole('button', { name: 'Create', exact: true }).click();

    await expect(page.getByText('summarizer')).toBeVisible({ timeout: 5000 });
    await expect(page.getByText(/custom skills \(1\)/i)).toBeVisible();
  });

  test('5. Toggling a custom skill off updates its status badge', async ({ page }) => {
    await setupAuth(page);
    await mockSkillsApi(page, { skills: [CUSTOM_SKILL] });
    await page.goto('/skills');

    await expect(page.getByText('my-research-skill')).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('Active')).toBeVisible();

    await page.getByText('● On').click();

    await expect(page.getByText('Inactive')).toBeVisible({ timeout: 5000 });
  });

  test('6. Test modal runs a skill test and shows the JSON result', async ({ page }) => {
    await setupAuth(page);
    await mockSkillsApi(page, { skills: [CUSTOM_SKILL] });
    await page.goto('/skills');

    await expect(page.getByText('my-research-skill')).toBeVisible({ timeout: 10000 });
    await page.getByRole('button', { name: /^test$/i }).first().click();

    await expect(page.getByRole('heading', { name: /test: my-research-skill/i })).toBeVisible();
    await page.getByPlaceholder(/enter test input for this skill/i).fill('Find the latest news on AI.');
    await page.getByRole('button', { name: /run test/i }).click();

    await expect(page.getByText(/test run completed/i)).toBeVisible({ timeout: 5000 });
  });

  test('7. Deleting a custom skill removes it from the list', async ({ page }) => {
    await setupAuth(page);
    await mockSkillsApi(page, { skills: [CUSTOM_SKILL] });
    await page.goto('/skills');

    await expect(page.getByText('my-research-skill')).toBeVisible({ timeout: 10000 });
    await page.getByRole('button', { name: /delete/i }).click();

    await expect(page.getByText(/no custom skills yet/i)).toBeVisible({ timeout: 5000 });
  });
});
