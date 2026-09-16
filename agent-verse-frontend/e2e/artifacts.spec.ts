/**
 * Artifacts Browser Page — E2E Tests
 *
 * Covers src/features/artifacts/ArtifactsBrowserPage.tsx:
 *   - Search / type filter / sort
 *   - 3-column grid of artifact cards
 *   - Detail drawer (preview, copy URI, "Use as Input", "Go to Goal", delete)
 */
import { test, expect, type Page } from '@playwright/test';
import { setupAuth } from './helpers/auth';

// ── Mock data ─────────────────────────────────────────────────────────────────

interface MockArtifact {
  id: string;
  name: string;
  artifact_type: string;
  storage_uri: string;
  content_type: string;
  size_bytes: number;
  goal_id: string;
  created_at: string;
}

const ARTIFACT_REPORT: MockArtifact = {
  id: 'artifact-report-1',
  name: 'quarterly-report.json',
  artifact_type: 'report',
  storage_uri: 'https://storage.example.com/quarterly-report.json',
  content_type: 'application/json',
  size_bytes: 2048,
  goal_id: 'goal-111',
  created_at: new Date(Date.now() - 3600_000).toISOString(),
};

const ARTIFACT_IMAGE: MockArtifact = {
  id: 'artifact-image-1',
  name: 'homepage-screenshot.png',
  artifact_type: 'screenshot',
  storage_uri: 'https://storage.example.com/homepage-screenshot.png',
  content_type: 'image/png',
  size_bytes: 512_000,
  goal_id: 'goal-222',
  created_at: new Date(Date.now() - 7200_000).toISOString(),
};

async function mockArtifactsApi(
  page: Page,
  { artifacts = [] as MockArtifact[] } = {}
): Promise<void> {
  await page.route(/localhost:8000\/artifacts/, (route) => {
    const method = route.request().method();
    const url = route.request().url();

    if (method === 'DELETE') {
      return route.fulfill({ status: 204, body: '' });
    }

    // GET /artifacts/{id}
    if (method === 'GET' && url.match(/\/artifacts\/[^/?]+$/)) {
      const id = url.split('/artifacts/')[1].split('?')[0];
      const found = artifacts.find((a) => a.id === id);
      return route.fulfill({
        status: found ? 200 : 404,
        contentType: 'application/json',
        body: JSON.stringify(found ?? { detail: 'not found' }),
      });
    }

    // GET /artifacts (list) — apply basic type/search filtering server-side
    const u = new URL(url);
    const typeFilter = u.searchParams.get('artifact_type');
    const search = u.searchParams.get('search')?.toLowerCase();
    let filtered = artifacts;
    if (typeFilter) filtered = filtered.filter((a) => a.artifact_type === typeFilter);
    if (search) filtered = filtered.filter((a) => a.name.toLowerCase().includes(search));

    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ items: filtered, total: filtered.length }),
    });
  });
}

// ═══════════════════════════════════════════════════════════════════════════════
// SUITE 1 — Loading / empty / populated
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Artifacts — page states', () => {
  test('1. Page loads and shows header', async ({ page }) => {
    await setupAuth(page);
    await mockArtifactsApi(page, { artifacts: [] });
    await page.goto('/artifacts');

    await expect(page.getByRole('heading', { name: 'Artifacts' })).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('Files and outputs produced by agent runs')).toBeVisible();
    await expect(page.getByPlaceholder('Search by name or type…')).toBeVisible();
  });

  test('2. Empty state shown when there are no artifacts', async ({ page }) => {
    await setupAuth(page);
    await mockArtifactsApi(page, { artifacts: [] });
    await page.goto('/artifacts');

    await expect(page.getByText('No artifacts yet')).toBeVisible({ timeout: 10000 });
    await expect(
      page.getByText('Artifacts are created when agents produce files during goal execution.')
    ).toBeVisible();
  });

  test('3. Populated grid renders artifact cards with metadata', async ({ page }) => {
    await setupAuth(page);
    await mockArtifactsApi(page, { artifacts: [ARTIFACT_REPORT, ARTIFACT_IMAGE] });
    await page.goto('/artifacts');

    await expect(page.getByText('quarterly-report.json')).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('homepage-screenshot.png')).toBeVisible();
    const cards = page.getByTestId('artifact-card');
    await expect(cards).toHaveCount(2);
    await expect(page.getByText('2 artifacts')).toBeVisible();
  });

  test('4. Error state shown when the artifacts request fails', async ({ page }) => {
    await setupAuth(page);
    await page.route(/localhost:8000\/artifacts/, (route) =>
      route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ detail: 'boom' }) })
    );
    await page.goto('/artifacts');

    await expect(page.getByText(/failed to load artifacts/i)).toBeVisible({ timeout: 10000 });
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// SUITE 2 — Filtering, search, sort
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Artifacts — filters', () => {
  test('5. Type filter pills narrow the visible artifacts', async ({ page }) => {
    await setupAuth(page);
    await mockArtifactsApi(page, { artifacts: [ARTIFACT_REPORT, ARTIFACT_IMAGE] });
    await page.goto('/artifacts');

    await expect(page.getByText('quarterly-report.json')).toBeVisible({ timeout: 10000 });
    await page.getByRole('button', { name: 'screenshot', exact: true }).click();

    await expect(page.getByText('homepage-screenshot.png')).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('quarterly-report.json')).not.toBeVisible();
  });

  test('6. Searching by name filters the grid', async ({ page }) => {
    await setupAuth(page);
    await mockArtifactsApi(page, { artifacts: [ARTIFACT_REPORT, ARTIFACT_IMAGE] });
    await page.goto('/artifacts');

    await expect(page.getByText('quarterly-report.json')).toBeVisible({ timeout: 10000 });
    await page.getByPlaceholder('Search by name or type…').fill('homepage');

    await expect(page.getByText('homepage-screenshot.png')).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('quarterly-report.json')).not.toBeVisible();
  });

  test('7. Sort dropdown changes order (smallest first)', async ({ page }) => {
    await setupAuth(page);
    await mockArtifactsApi(page, { artifacts: [ARTIFACT_REPORT, ARTIFACT_IMAGE] });
    await page.goto('/artifacts');

    await expect(page.getByText('quarterly-report.json')).toBeVisible({ timeout: 10000 });
    await page.getByLabel('Sort artifacts').selectOption('smallest');

    const cards = page.getByTestId('artifact-card');
    await expect(cards.first()).toContainText('quarterly-report.json', { timeout: 5000 });
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// SUITE 3 — Detail drawer interactions
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Artifacts — detail drawer', () => {
  test('8. Clicking a card opens the detail drawer with metadata', async ({ page }) => {
    await setupAuth(page);
    await mockArtifactsApi(page, { artifacts: [ARTIFACT_REPORT] });
    await page.goto('/artifacts');

    await page.getByTestId('artifact-card').click();

    await expect(page.getByLabel(/details for quarterly-report\.json/i)).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('Storage URI')).toBeVisible();
    await expect(page.getByText(ARTIFACT_REPORT.storage_uri)).toBeVisible();
  });

  test('9. Deleting an artifact from the drawer removes it after confirmation', async ({ page }) => {
    await setupAuth(page);
    await mockArtifactsApi(page, { artifacts: [ARTIFACT_REPORT] });
    await page.goto('/artifacts');

    await page.getByTestId('artifact-card').click();
    await expect(page.getByLabel(/details for quarterly-report\.json/i)).toBeVisible({ timeout: 5000 });

    await page.getByRole('button', { name: /delete/i }).first().click();
    await expect(page.getByText('Delete artifact?')).toBeVisible({ timeout: 5000 });

    // After the DELETE call, the refetch of the list should return empty.
    await page.route(/localhost:8000\/artifacts/, (route) => {
      if (route.request().method() === 'DELETE') {
        return route.fulfill({ status: 204, body: '' });
      }
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ items: [], total: 0 }),
      });
    });

    await page.getByRole('dialog').getByRole('button', { name: 'Delete' }).click();

    await expect(page.getByText('Artifact deleted.')).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('No artifacts yet')).toBeVisible({ timeout: 5000 });
  });

  test('10. "Use as Input" navigates to the goals page with a prefilled goal', async ({ page }) => {
    await setupAuth(page);
    await mockArtifactsApi(page, { artifacts: [ARTIFACT_REPORT] });
    await page.route(/localhost:8000\/goals/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ goals: [] }) })
    );
    await page.goto('/artifacts');

    await page.getByTestId('artifact-card').click();
    await expect(page.getByLabel(/details for quarterly-report\.json/i)).toBeVisible({ timeout: 5000 });

    await page.getByRole('button', { name: /use as input/i }).click();
    await expect(page.getByText('URI copied! Opening goal form…')).toBeVisible({ timeout: 5000 });
    await expect(page).toHaveURL(/\/goals/, { timeout: 5000 });
  });
});
