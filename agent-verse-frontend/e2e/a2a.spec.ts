/**
 * A2A Network Page — E2E Tests
 *
 * Covers the 3 tabs of src/features/a2a/A2APage.tsx:
 *   1. Tasks         — dispatch form + live-polling task list
 *   2. Agent Card    — this platform's published capability card
 *   3. Remote Agents — localStorage-backed registry of external agents
 */
import { test, expect, type Page } from '@playwright/test';
import { setupAuth } from './helpers/auth';

// ── Mock data ─────────────────────────────────────────────────────────────────

const AGENT_CARD = {
  agent_id: 'agentverse-platform',
  name: 'AgentVerse Platform Agent',
  version: '1.0.0',
  description: 'A vendor-agnostic autonomous agent operating system.',
  endpoint: 'https://api.agentverse.example.com',
  authentication: { scheme: 'Bearer', header: 'Authorization', note: 'API key' },
  capabilities: ['web_browsing', 'code_execution', 'file_management'],
  supported_task_types: ['research', 'automation', 'data_processing'],
};

const TASK_RUNNING = {
  task_id: 'task-abc123def456',
  goal: 'Summarize the latest quarterly report',
  status: 'running',
  created_at: new Date().toISOString(),
};

const TASK_COMPLETE = {
  task_id: 'task-xyz789complete',
  goal: 'Fetch weather for NYC',
  status: 'complete',
  result: 'It is sunny, 72F in New York City.',
  created_at: new Date(Date.now() - 60_000).toISOString(),
};

async function mockA2AApi(
  page: Page,
  opts: { tasks?: unknown[]; card?: unknown; submitResult?: unknown } = {}
): Promise<void> {
  const tasks = opts.tasks ?? [];
  const card = opts.card ?? AGENT_CARD;

  await page.route('**/.well-known/agent.json', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(card) })
  );

  await page.route(/localhost:8000\/a2a\/tasks/, (route) => {
    const method = route.request().method();
    if (method === 'POST') {
      return route.fulfill({
        status: 202,
        contentType: 'application/json',
        body: JSON.stringify(
          opts.submitResult ?? { task_id: 'task-new-001', status: 'accepted', message: 'Task dispatched' }
        ),
      });
    }
    // GET /a2a/tasks (list)
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(tasks) });
  });
}

// ═══════════════════════════════════════════════════════════════════════════════
// SUITE 1 — Tasks tab
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('A2A — Tasks tab', () => {
  test('1. Page loads showing header, tabs, and dispatch form', async ({ page }) => {
    await setupAuth(page);
    await mockA2AApi(page, { tasks: [] });
    await page.goto('/a2a');

    await expect(page.getByRole('heading', { name: 'A2A Network' })).toBeVisible({ timeout: 10000 });
    await expect(page.getByRole('tab', { name: 'Tasks' })).toBeVisible();
    await expect(page.getByRole('tab', { name: 'Agent Card' })).toBeVisible();
    await expect(page.getByRole('tab', { name: 'Remote Agents' })).toBeVisible();
    await expect(page.getByText('Dispatch Task')).toBeVisible();
    await expect(page.getByLabel('Goal')).toBeVisible();
  });

  test('2. Empty state shown when there are no tasks', async ({ page }) => {
    await setupAuth(page);
    await mockA2AApi(page, { tasks: [] });
    await page.goto('/a2a');

    await expect(page.getByText('No tasks yet')).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('Dispatch your first task using the form.')).toBeVisible();
  });

  test('3. Populated task list renders rows with status and goal text', async ({ page }) => {
    await setupAuth(page);
    await mockA2AApi(page, { tasks: [TASK_RUNNING, TASK_COMPLETE] });
    await page.goto('/a2a');

    await expect(page.getByText('Recent Tasks')).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('(2)')).toBeVisible();
    const rows = page.getByTestId('task-row');
    await expect(rows).toHaveCount(2);
    await expect(page.getByText('Summarize the latest quarterly report')).toBeVisible();
    await expect(page.getByText('Fetch weather for NYC')).toBeVisible();
    await expect(page.getByText('running')).toBeVisible();
    await expect(page.getByText('complete')).toBeVisible();
    // Live indicator shows because one task is running
    await expect(page.getByText('Live')).toBeVisible();
  });

  test('4. Expanding a completed task row reveals its result', async ({ page }) => {
    await setupAuth(page);
    await mockA2AApi(page, { tasks: [TASK_COMPLETE] });
    await page.goto('/a2a');

    const row = page.getByTestId('task-row');
    await expect(row).toBeVisible({ timeout: 10000 });
    await row.getByLabel(/copy/i).waitFor({ state: 'attached' }).catch(() => {});
    // The chevron toggle button (only rendered because task.result is set)
    const toggle = row.locator('button').first();
    await toggle.click();
    await expect(page.getByText('It is sunny, 72F in New York City.')).toBeVisible({ timeout: 5000 });
  });

  test('5. Dispatching a task submits the form and shows confirmation', async ({ page }) => {
    await setupAuth(page);
    await mockA2AApi(page, {
      tasks: [],
      submitResult: { task_id: 'task-dispatched-999', status: 'accepted', message: 'ok' },
    });
    await page.goto('/a2a');

    await expect(page.getByLabel('Goal')).toBeVisible({ timeout: 10000 });
    await page.getByLabel('Goal').fill('Analyze competitor pricing pages');
    await page.getByRole('button', { name: /dispatch task/i }).click();

    await expect(page.getByText('Task dispatched!')).toBeVisible({ timeout: 5000 });
    await expect(page.getByText(/task-dispatched-999/)).toBeVisible();
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// SUITE 2 — Agent Card tab
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('A2A — Agent Card tab', () => {
  test('6. Shows platform agent card details', async ({ page }) => {
    await setupAuth(page);
    await mockA2AApi(page, { tasks: [] });
    await page.goto('/a2a');

    await page.getByRole('tab', { name: 'Agent Card' }).click();

    await expect(page.getByText('AgentVerse Platform Agent')).toBeVisible({ timeout: 10000 });
    await expect(page.getByText(/agentverse-platform · v1\.0\.0/)).toBeVisible();
    await expect(page.getByText('A vendor-agnostic autonomous agent operating system.')).toBeVisible();
    await expect(page.getByText('https://api.agentverse.example.com')).toBeVisible();
    await expect(page.getByText('web browsing')).toBeVisible();
    await expect(page.getByText('research')).toBeVisible();
  });

  test('7. Copy JSON button copies the agent card', async ({ page }) => {
    await setupAuth(page);
    await mockA2AApi(page, { tasks: [] });
    await page.context().grantPermissions(['clipboard-read', 'clipboard-write']);
    await page.goto('/a2a');

    await page.getByRole('tab', { name: 'Agent Card' }).click();
    await expect(page.getByText('AgentVerse Platform Agent')).toBeVisible({ timeout: 10000 });

    await page.getByRole('button', { name: /copy json/i }).click();
    await expect(page.getByText('Agent card JSON copied.')).toBeVisible({ timeout: 5000 });
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// SUITE 3 — Remote Agents tab
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('A2A — Remote Agents tab', () => {
  test('8. Empty state shown when no remote agents registered', async ({ page }) => {
    await setupAuth(page);
    await mockA2AApi(page, { tasks: [] });
    await page.addInitScript(() => localStorage.removeItem('a2a_remote_agents'));
    await page.goto('/a2a');

    await page.getByRole('tab', { name: 'Remote Agents' }).click();

    await expect(page.getByText('No remote agents registered')).toBeVisible({ timeout: 10000 });
    await expect(page.getByRole('button', { name: /register agent/i })).toBeVisible();
  });

  test('9. Registering a remote agent adds it to the list', async ({ page }) => {
    await setupAuth(page);
    await mockA2AApi(page, { tasks: [] });
    await page.addInitScript(() => localStorage.removeItem('a2a_remote_agents'));

    const REMOTE_CARD = {
      ...AGENT_CARD,
      agent_id: 'remote-agent-1',
      name: 'Remote Research Agent',
      version: '2.1.0',
    };
    await page.route('https://remote.example.com/.well-known/agent.json', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(REMOTE_CARD) })
    );

    await page.goto('/a2a');
    await page.getByRole('tab', { name: 'Remote Agents' }).click();
    await page.getByRole('button', { name: /register agent/i }).click();

    await expect(page.getByText('Register Remote Agent')).toBeVisible({ timeout: 5000 });
    await page.getByLabel('Agent Card URL').fill('https://remote.example.com/.well-known/agent.json');
    await page.getByLabel('Display Name (optional)').fill('My Remote Agent');
    await page.getByRole('button', { name: 'Register' }).click();

    await expect(page.getByText('My Remote Agent')).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('v2.1.0')).toBeVisible();
  });

  test('10. Removing a registered remote agent clears it from the list', async ({ page }) => {
    await setupAuth(page);
    await mockA2AApi(page, { tasks: [] });
    await page.addInitScript(() => {
      localStorage.setItem(
        'a2a_remote_agents',
        JSON.stringify([{ name: 'Old Agent', url: 'https://old.example.com/agent.json' }])
      );
    });
    await page.goto('/a2a');
    await page.getByRole('tab', { name: 'Remote Agents' }).click();

    await expect(page.getByText('Old Agent')).toBeVisible({ timeout: 10000 });
    await page.getByTitle('Remove').click();

    await expect(page.getByText('No remote agents registered')).toBeVisible({ timeout: 5000 });
  });
});
