import { test, expect, type Page } from '@playwright/test';

async function setupAuth(page: Page) {
  await page.addInitScript(() => {
    localStorage.setItem(
      'av-auth',
      JSON.stringify({
        state: {
          apiKey: 'test-key',
          tenantId: 'test-tenant',
          plan: 'free',
          isAuthenticated: true,
        },
        version: 0,
      })
    );
    localStorage.setItem('av_api_key', 'test-key');
  });
  await page.route('**/tenants/me', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ tenant_id: 'test-tenant', name: 'Test Org', plan: 'free' }),
    })
  );
}

const MOCK_TEMPLATES = [
  {
    id: 'tmpl-001',
    name: 'Deploy to Kubernetes',
    description: 'Automates a rolling deployment to a Kubernetes cluster',
    domain: 'devops',
    goal_text: 'Deploy {{image}} to cluster {{cluster}} in namespace {{namespace}}',
    parameters: [
      { name: 'image', required: true, description: 'Docker image tag', default: '' },
      { name: 'cluster', required: true, description: 'Cluster name', default: 'prod' },
      { name: 'namespace', required: false, description: 'K8s namespace', default: 'default' },
    ],
    use_count: 12,
    created_at: new Date().toISOString(),
  },
  {
    id: 'tmpl-002',
    name: 'Code Review Summary',
    description: 'Summarizes open pull requests for a repository',
    domain: 'engineering',
    goal_text: 'Review all open PRs in {{repo}} and post a summary',
    parameters: [
      { name: 'repo', required: true, description: 'Repository name', default: '' },
    ],
    use_count: 4,
    created_at: new Date().toISOString(),
  },
];

async function mockTemplatesApi(page: Page, templates = MOCK_TEMPLATES) {
  await page.route(/localhost:8000\/templates/, async (route) => {
    const method = route.request().method();
    if (method === 'GET') {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(templates),
      });
    }
    if (method === 'POST') {
      return route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify({
          id: 'tmpl-new',
          name: 'New Template',
          description: 'A newly created template',
          domain: 'general',
          goal_text: 'Do something useful',
          parameters: [],
          use_count: 0,
          created_at: new Date().toISOString(),
        }),
      });
    }
    return route.continue();
  });
}

test.describe('Template Library', () => {
  test('page renders with "Template Library" heading', async ({ page }) => {
    await setupAuth(page);
    await mockTemplatesApi(page);
    await page.goto('/templates');

    await expect(
      page.locator('h1').filter({ hasText: /template library/i })
    ).toBeVisible({ timeout: 15000 });
  });

  test('can browse domain filters', async ({ page }) => {
    await setupAuth(page);
    await mockTemplatesApi(page);
    await page.goto('/templates');

    await expect(page.getByRole('button', { name: /^all$/i })).toBeVisible({ timeout: 15000 });
    await expect(page.getByRole('button', { name: /devops/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /engineering/i })).toBeVisible();
  });

  test('search filters templates by name', async ({ page }) => {
    await setupAuth(page);
    await mockTemplatesApi(page);
    await page.goto('/templates');

    await expect(page.getByText('Deploy to Kubernetes')).toBeVisible({ timeout: 15000 });
    await expect(page.getByText('Code Review Summary')).toBeVisible();

    await page.locator('input[aria-label="Search templates"]').fill('kubernetes');
    await page.waitForTimeout(300);

    await expect(page.getByText('Deploy to Kubernetes')).toBeVisible();
    await expect(page.getByText('Code Review Summary')).not.toBeVisible();
  });

  test('Create template button opens modal', async ({ page }) => {
    await setupAuth(page);
    await mockTemplatesApi(page);
    await page.goto('/templates');

    await expect(page.getByRole('button', { name: /new template/i })).toBeVisible({ timeout: 15000 });
    await page.getByRole('button', { name: /new template/i }).click();

    // The create modal should appear
    await expect(
      page.locator('input[placeholder]').or(page.locator('textarea[placeholder]')).first()
    ).toBeVisible({ timeout: 5000 });
  });

  test('Use template button is visible on template cards', async ({ page }) => {
    await setupAuth(page);
    await mockTemplatesApi(page);
    await page.goto('/templates');

    await expect(page.getByText('Deploy to Kubernetes')).toBeVisible({ timeout: 15000 });

    // Each template card should have a "Use template" button
    const useButtons = page.getByRole('button', { name: /use template/i });
    await expect(useButtons.first()).toBeVisible();
  });

  test('can fill template parameters in instantiator modal', async ({ page }) => {
    await setupAuth(page);
    await mockTemplatesApi(page);
    await page.goto('/templates');

    await expect(page.getByText('Deploy to Kubernetes')).toBeVisible({ timeout: 15000 });

    // Click "Use template" on the first card
    await page.getByRole('button', { name: /use template: deploy to kubernetes/i }).click();

    // Instantiator modal should open with parameter inputs
    await expect(page.getByText('Deploy to Kubernetes').first()).toBeVisible({ timeout: 5000 });
    // Expect a parameter input for "image" or its label
    // placeholder may be empty ('') so use id or label text
    await expect(
      page.locator('input[id="param-image"]').or(page.locator('label').filter({ hasText: /image/i }))
    ).toBeVisible({ timeout: 10000 });
  });
});
