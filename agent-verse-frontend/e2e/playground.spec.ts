/**
 * Agent Playground — E2E Tests
 *
 * 3-column sandbox: Scenario Library (left), Goal + Tool Picker + Execution
 * Canvas (center, live SSE), Step Inspector + Session Stats (right).
 *
 * Endpoints exercised:
 *   GET  /enterprise/simulation/available-tools  (simulationApi.getAvailableTools)
 *   POST /enterprise/simulation/stream            (raw fetch, SSE)
 *   POST /enterprise/simulation                   (simulationApi.run — fallback)
 *   POST /playground/scenarios                    (best-effort persistence)
 */
import { test, expect, type Page } from '@playwright/test';
import { setupAuth } from './helpers/auth';

const TOOLS = [
  { name: 'jira:search_issues', description: 'Search Jira issues', server_id: 'srv-jira' },
  { name: 'slack:send_message', description: 'Send a Slack message', server_id: 'srv-slack' },
];

async function mockAvailableTools(page: Page, tools: typeof TOOLS = TOOLS): Promise<void> {
  await page.route('**/enterprise/simulation/available-tools', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ tools, total: tools.length }),
    })
  );
}

async function mockScenarioPersistence(page: Page): Promise<void> {
  await page.route('**/playground/scenarios', (route) =>
    route.fulfill({ status: 201, contentType: 'application/json', body: '{}' })
  );
}

function sseBody(events: Record<string, unknown>[]): string {
  return events.map((e) => `data: ${JSON.stringify(e)}`).join('\n\n') + '\n\n';
}

test.describe('Playground — Load & Empty States', () => {
  test('1. Loads and renders key content with empty scenario library and empty canvas', async ({ page }) => {
    await setupAuth(page);
    await mockAvailableTools(page, []);
    await page.goto('/playground');

    await expect(page.getByRole('heading', { name: /agent playground/i })).toBeVisible({ timeout: 10000 });
    await expect(page.getByPlaceholder(/describe what the agent should accomplish/i)).toBeVisible();
    await expect(page.getByText(/no saved scenarios yet/i)).toBeVisible();
    await expect(page.getByText(/run a simulation to see execution here/i)).toBeVisible();
    await expect(page.getByText(/click a step to inspect details/i)).toBeVisible();
  });

  test('2. No backend tools — shows empty tool message; adding a custom tool renders an editable row', async ({
    page,
  }) => {
    await setupAuth(page);
    await mockAvailableTools(page, []);
    await page.goto('/playground');

    await expect(page.getByRole('heading', { name: /agent playground/i })).toBeVisible({ timeout: 10000 });
    await expect(page.getByText(/no tools configured/i)).toBeVisible();

    await page.getByRole('button', { name: /custom/i }).click();

    await expect(page.getByPlaceholder('tool_name')).toBeVisible({ timeout: 5000 });
    await expect(page.getByPlaceholder('{"result": "mock"}')).toBeVisible();
  });
});

test.describe('Playground — Populated Tool List', () => {
  test('3. Backend tools populate as toggleable cards; toggling updates the active count', async ({ page }) => {
    await setupAuth(page);
    await mockAvailableTools(page);
    await page.goto('/playground');

    await expect(page.getByRole('heading', { name: /agent playground/i })).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('jira:search_issues')).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('slack:send_message')).toBeVisible();

    await page.getByLabel('Toggle jira:search_issues').click();
    await expect(page.getByText('1 active')).toBeVisible({ timeout: 5000 });

    await page.getByLabel('Toggle slack:send_message').click();
    await expect(page.getByText('2 active')).toBeVisible({ timeout: 5000 });
  });

  test('4. Applying a Quick Template fills the goal and enables its tool', async ({ page }) => {
    await setupAuth(page);
    await mockAvailableTools(page, []);
    await page.goto('/playground');

    await expect(page.getByRole('heading', { name: /agent playground/i })).toBeVisible({ timeout: 10000 });
    await page.getByRole('button', { name: /jira search/i }).click();

    await expect(page.getByPlaceholder(/describe what the agent should accomplish/i)).toHaveValue(
      /Search Jira for all open bugs/i
    );
    await expect(page.getByPlaceholder('tool_name')).toHaveValue('jira:search_issues');
  });
});

test.describe('Playground — Scenario Library', () => {
  test('5. Saving the current goal as a scenario adds it to the Saved Scenarios list', async ({ page }) => {
    await setupAuth(page);
    await mockAvailableTools(page, []);
    await mockScenarioPersistence(page);
    await page.goto('/playground');

    await expect(page.getByRole('heading', { name: /agent playground/i })).toBeVisible({ timeout: 10000 });
    await page.getByPlaceholder(/describe what the agent should accomplish/i).fill('Summarize open incidents');

    await page.getByTitle('Save current').click();
    await page.getByPlaceholder(/scenario name/i).fill('Incident Summary');
    await page.getByRole('button', { name: /^save$/i }).click();

    await expect(page.getByText('Incident Summary')).toBeVisible({ timeout: 5000 });
    await expect(page.getByText(/no saved scenarios yet/i)).not.toBeVisible();
  });
});

test.describe('Playground — Run Simulation (primary interaction)', () => {
  test('6. Running a simulation streams steps via SSE and shows the final result + stats', async ({ page }) => {
    await setupAuth(page);
    await mockAvailableTools(page, []);
    await page.route('**/enterprise/simulation/stream', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: sseBody([
          { type: 'step_started', description: 'Planning approach' },
          { type: 'step_completed', cost_increment: 0.001, output: 'Plan created' },
          {
            type: 'step_started',
            description: 'Calling jira:search_issues',
            tool_called: 'jira:search_issues',
          },
          {
            type: 'step_completed',
            cost_increment: 0.002,
            tool_called: 'jira:search_issues',
            output: '{"issues":[]}',
          },
          { type: 'simulation_complete', total_cost: 0.003, total_steps: 2, final_status: 'complete' },
        ]),
      })
    );
    await page.goto('/playground');

    await expect(page.getByRole('heading', { name: /agent playground/i })).toBeVisible({ timeout: 10000 });
    await page.getByPlaceholder(/describe what the agent should accomplish/i).fill('Search Jira for open bugs');
    await page.getByRole('button', { name: /run simulation/i }).click();

    await expect(page.getByText('Planning approach')).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('Calling jira:search_issues')).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('→ jira:search_issues')).toBeVisible();

    await expect(page.getByText('complete').first()).toBeVisible({ timeout: 5000 });
    await expect(page.getByText(/2 steps · \$0\.0030 simulated/i)).toBeVisible();

    // Session stats panel
    await expect(page.getByText('Tool Calls')).toBeVisible();
    await expect(page.getByText('$0.0030')).toBeVisible();
  });

  test('7. Clicking a step opens the Step Inspector with its details', async ({ page }) => {
    await setupAuth(page);
    await mockAvailableTools(page, []);
    await page.route('**/enterprise/simulation/stream', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: sseBody([
          {
            type: 'step_started',
            description: 'Calling jira:search_issues',
            tool_called: 'jira:search_issues',
          },
          {
            type: 'step_completed',
            cost_increment: 0.0025,
            tool_called: 'jira:search_issues',
            output: '{"issues":[]}',
          },
          { type: 'simulation_complete', total_cost: 0.0025, total_steps: 1, final_status: 'complete' },
        ]),
      })
    );
    await page.goto('/playground');

    await expect(page.getByRole('heading', { name: /agent playground/i })).toBeVisible({ timeout: 10000 });
    await page.getByPlaceholder(/describe what the agent should accomplish/i).fill('Search Jira for open bugs');
    await page.getByRole('button', { name: /run simulation/i }).click();

    await expect(page.getByText('Calling jira:search_issues')).toBeVisible({ timeout: 10000 });
    await page.getByText('Calling jira:search_issues').click();

    await expect(page.getByText('Step 1')).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('$0.0025 estimated')).toBeVisible();
  });

  test('8. When the SSE stream is unavailable, falls back to batch simulation results', async ({ page }) => {
    await setupAuth(page);
    await mockAvailableTools(page, []);
    await page.route('**/enterprise/simulation/stream', (route) =>
      route.fulfill({ status: 500, contentType: 'application/json', body: '{"detail":"stream unavailable"}' })
    );
    await page.route('**/enterprise/simulation', (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          run_id: 'run-1',
          status: 'complete',
          steps: [{ step: 1, tool: 'jira:search_issues', output: '{"ok":true}', cost_usd: 0.001 }],
          cost_usd: 0.001,
          iterations: 1,
          used_real_llm: false,
        }),
      });
    });
    await page.goto('/playground');

    await expect(page.getByRole('heading', { name: /agent playground/i })).toBeVisible({ timeout: 10000 });
    await page.getByPlaceholder(/describe what the agent should accomplish/i).fill('Search Jira for open bugs');
    await page.getByRole('button', { name: /run simulation/i }).click();

    await expect(page.getByText(/1 steps · \$0\.0010 simulated/i)).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('complete').first()).toBeVisible();
  });
});
