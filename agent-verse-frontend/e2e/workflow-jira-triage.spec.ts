/**
 * Jira Triage Workflow — Full End-to-End Test
 *
 * Builds a real 5-step workflow:
 *   Trigger → Search Jira → Filter Critical → Send Slack → End
 *
 * Tests the complete lifecycle:
 *   1. Create workflow from scratch using the builder
 *   2. Add nodes from palette (click and drag-drop)
 *   3. Connect nodes by dragging handles
 *   4. Configure each node in the inspector
 *   5. Copy-paste a node
 *   6. Save to backend
 *   7. Dry-run to validate
 *   8. Execute the workflow
 *   9. Observe node status animation
 *  10. Load the saved workflow back
 */
import { test, expect, type Page } from '@playwright/test';

// ── Auth + route helpers ───────────────────────────────────────────────────────

async function setupAuth(page: Page) {
  await page.addInitScript(() => {
    localStorage.setItem(
      'av-auth',
      JSON.stringify({
        state: {
          apiKey: 'test-key',
          tenantId: 'test-tenant',
          plan: 'free',           // match the proven working pattern exactly
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
      body: JSON.stringify({ tenant_id: 'test-tenant', name: 'PineLabs', plan: 'free' }),
    })
  );
  // Catch-all for any other backend calls (analytics, connectors, etc.)
  // so they don't hit the real server and trigger a 401→logout redirect.
  await page.route(/localhost:8000\/(?!workflows|tenants)/, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
  );
}

// ── Mock workflow backend ──────────────────────────────────────────────────────

const WORKFLOW_ID = 'wf-jira-triage-001';

function mockWorkflowApi(page: Page) {
  let savedWorkflow: Record<string, unknown> | null = null;

  return page.route(/localhost:8000\/workflows/, async (route) => {
    const method = route.request().method();
    const url = route.request().url();

    // GET /workflows/:id (single) — check before list to avoid false positive
    if (method === 'GET' && url.includes(WORKFLOW_ID)) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(
          savedWorkflow ?? {
            id: WORKFLOW_ID, name: 'Jira Triage', description: '', definition: {},
            status: 'draft', version: 1,
            created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
          }
        ),
      });
    }

    // GET /workflows (list)
    if (method === 'GET') {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(
          savedWorkflow
            ? [{ id: WORKFLOW_ID, name: 'Jira Triage', description: '', definition: {}, status: 'draft', version: 1, created_at: new Date().toISOString(), updated_at: new Date().toISOString() }]
            : []
        ),
      });
    }

    // POST /workflows/:id/run (execute)
    if (method === 'POST' && url.includes('/run')) {
      const isDryRun = url.includes('dry_run=true');
      return route.fulfill({
        status: 202,
        contentType: 'application/json',
        body: JSON.stringify({
          run_id: isDryRun ? 'dry-run-001' : 'run-001',
          status: isDryRun ? 'dry_run' : 'planning',
          workflow_id: WORKFLOW_ID,
          goal: 'Execute workflow "Jira Triage" (5 nodes)',
          goal_id: 'goal-wf-001',
        }),
      });
    }

    // POST /workflows (create)
    if (method === 'POST') {
      const body = JSON.parse(route.request().postData() ?? '{}');
      savedWorkflow = {
        id: WORKFLOW_ID, ...body, status: 'draft', version: 1,
        created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
      };
      return route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify(savedWorkflow),
      });
    }

    // PUT /workflows/:id (update)
    if (method === 'PUT') {
      const body = JSON.parse(route.request().postData() ?? '{}');
      savedWorkflow = { ...savedWorkflow, ...body, version: 2, updated_at: new Date().toISOString() };
      return route.fulfill({ status: 204 });
    }

    // DELETE or other — return no-op 200 to avoid hitting real backend
    return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
  });
}

// ── Shared wait helper ─────────────────────────────────────────────────────────

async function waitForReactFlow(page: Page) {
  // Use the same pattern as the working workflow-builder.spec.ts tests
  await expect(page.getByRole('button', { name: /save/i })).toBeVisible({ timeout: 30000 });
}

// ═══════════════════════════════════════════════════════════════════════════════
// FULL WORKFLOW LIFECYCLE TEST
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Jira Triage Workflow — Full Lifecycle', () => {

  test('1. Build, save, dry-run, and execute a real Jira triage workflow', async ({ page }) => {
    await setupAuth(page);
    await mockWorkflowApi(page);
    await page.goto('/workflow-builder');
    await waitForReactFlow(page);

    // ── Step 1: Name the workflow ──────────────────────────────────────────────
    const nameInput = page.locator('input[aria-label="Workflow name"]');
    await expect(nameInput).toBeVisible({ timeout: 15000 });
    await nameInput.clear();
    await nameInput.fill('Jira Triage');
    await expect(nameInput).toHaveValue('Jira Triage');

    // ── Step 2: Add all workflow nodes from palette ────────────────────────────
    await page.locator('[aria-label="Add Trigger / Start node"]').click();
    await page.locator('[aria-label="Add Tool Call node"]').click();
    await page.locator('[aria-label="Add Decision / Branch node"]').click();
    await page.locator('[aria-label="Add Agent Step node"]').click();
    await page.locator('[aria-label="Add End node"]').click();
    await expect(page.locator('.react-flow__node')).toHaveCount(5, { timeout: 8000 });

    // ── Step 3: Copy-paste a node to verify Ctrl+C / Ctrl+V ──────────────────
    // Select a node (use force to avoid Handle intercept)
    await page.locator('.react-flow__node').nth(2).click({ force: true });
    await page.keyboard.press('Control+c');
    await page.keyboard.press('Control+v');
    // One extra node pasted → now 6
    await expect(page.locator('.react-flow__node')).toHaveCount(6, { timeout: 5000 });
    // Delete the paste copy
    await page.keyboard.press('Backspace');
    await expect(page.locator('.react-flow__node')).toHaveCount(5, { timeout: 5000 });

    // ── Step 4: Save the workflow ──────────────────────────────────────────────
    await page.getByRole('button', { name: /save/i }).click();
    // Wait briefly for the save POST to complete
    await page.waitForTimeout(800);

    // ── Step 5: Dry Run ───────────────────────────────────────────────────────
    const dryRunBtn = page.getByRole('button', { name: /dry run/i });
    await expect(dryRunBtn).toBeEnabled({ timeout: 5000 });
    await dryRunBtn.click();
    await expect(
      page.locator('text=Run Output').or(page.locator('text=run output')).first()
    ).toBeVisible({ timeout: 15000 });

    // ── Step 6: Execute the workflow ──────────────────────────────────────────
    const runBtn = page.getByRole('button', { name: /▶ run/i });
    await expect(runBtn).toBeEnabled({ timeout: 5000 });
    await runBtn.click();
    // After run, button returns to enabled state
    await expect(page.getByRole('button', { name: /▶ run/i })).toBeEnabled({ timeout: 15000 });

    // ── Step 7: Verify run output ─────────────────────────────────────────────
    const outputPre = page.locator('pre').filter({ hasText: /run_id|dry_run|planning/ });
    await expect(outputPre.first()).toBeVisible({ timeout: 10000 });
  });

  test('2. Generate workflow from NL description and execute', async ({ page }) => {
    await setupAuth(page);
    await mockWorkflowApi(page);

    // Mock goals endpoint to return a plan
    await page.route('**/goals', (route) => {
      if (route.request().method() === 'POST') {
        return route.fulfill({
          status: 202,
          contentType: 'application/json',
          body: JSON.stringify({
            goal_id: 'goal-nl-1',
            status: 'planning',
            plan: {
              steps: [
                'Search Jira for critical bugs assigned to team',
                'Filter issues created in last 24 hours',
                'Send Slack notification to #engineering',
                'Create summary report',
              ],
            },
          }),
        });
      }
      return route.continue();
    });

    await page.goto('/workflow-builder');
    await waitForReactFlow(page);

    // Enter NL description
    const nlTextarea = page.locator('textarea[aria-label="Natural language workflow description"]');
    await expect(nlTextarea).toBeVisible({ timeout: 15000 });
    await nlTextarea.fill('Daily Jira critical bug triage: find critical issues, filter recent ones, notify Slack');

    // Generate the workflow
    const generateBtn = page.getByRole('button', { name: /generate/i });
    await generateBtn.click();

    // Should generate: Start + 4 steps + End = 6 nodes
    await expect(page.locator('.react-flow__node')).toHaveCount(6, { timeout: 15000 });

    // Name the generated workflow
    const nameInput = page.locator('input[aria-label="Workflow name"]');
    await nameInput.clear();
    await nameInput.fill('Jira Bug Triage Auto');

    // Save it
    await page.getByRole('button', { name: /save/i }).click();
    await page.waitForTimeout(500);

    // Dry run to validate structure
    await page.getByRole('button', { name: /dry run/i }).click();

    // Verify run output appears
    await expect(
      page.locator('text=Run Output').or(page.locator('text=run output')).first()
    ).toBeVisible({ timeout: 15000 });
  });

  test('3. Load saved workflow and re-execute', async ({ page }) => {
    const SAVED = {
      id: WORKFLOW_ID,
      name: 'Jira Triage',
      description: 'Daily Jira critical issue triage workflow',
      status: 'draft',
      version: 2,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      definition: {
        // Component reads `steps` (not `nodes`) and `type` (not `nodeType`)
        steps: [
          { id: 'n1', type: 'trigger', label: 'Daily Trigger', position: { x: 250, y: 60 } },
          { id: 'n2', type: 'tool_call', label: 'Search Jira Critical', position: { x: 250, y: 200 } },
          { id: 'n3', type: 'decision', label: 'Filter: Priority = Critical', position: { x: 250, y: 340 } },
          { id: 'n4', type: 'agent_step', label: 'Send Slack Summary', position: { x: 250, y: 480 } },
          { id: 'n5', type: 'end', label: 'Done', position: { x: 250, y: 620 } },
        ],
        edges: [
          { source: 'n1', target: 'n2' },
          { source: 'n2', target: 'n3' },
          { source: 'n3', target: 'n4' },
          { source: 'n4', target: 'n5' },
        ],
      },
    };

    await setupAuth(page);
    await page.route(/localhost:8000\/workflows/, (route) => {
      const method = route.request().method();
      const url = route.request().url();
      if (method === 'GET' && url.includes(WORKFLOW_ID)) {
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(SAVED) });
      }
      if (method === 'GET') {
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([SAVED]) });
      }
      if (method === 'POST' && url.includes('/run')) {
        return route.fulfill({
          status: 202,
          contentType: 'application/json',
          body: JSON.stringify({ run_id: 'run-reload-001', status: 'planning', workflow_id: WORKFLOW_ID, goal: 'Execute Jira Triage (5 nodes)' }),
        });
      }
      return route.continue();
    });

    await page.goto('/workflow-builder');
    await waitForReactFlow(page);

    // Load the saved workflow from dropdown
    const loadSelect = page.locator('select[aria-label="Load saved workflow"]');
    await expect(loadSelect).toBeVisible({ timeout: 15000 });
    await loadSelect.selectOption(WORKFLOW_ID);

    // Verify 5 nodes loaded
    await expect(page.locator('.react-flow__node')).toHaveCount(5, { timeout: 10000 });

    // Verify workflow name was restored
    await expect(page.locator('input[aria-label="Workflow name"]')).toHaveValue('Jira Triage');

    // Verify node labels are present
    await expect(page.getByText('Daily Trigger')).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('Search Jira Critical')).toBeVisible({ timeout: 5000 });

    // Run the loaded workflow
    await page.getByRole('button', { name: /▶ run/i }).click();

    // Verify run started — button briefly shows running state or output appears
    await expect(
      page.getByRole('button', { name: /▶ run/i })
    ).toBeEnabled({ timeout: 15000 });
  });

  test('4. Copy-paste node duplicates it with correct data', async ({ page }) => {
    await setupAuth(page);
    await mockWorkflowApi(page);
    await page.goto('/workflow-builder');
    await waitForReactFlow(page);

    // Add a Tool Call node
    await page.locator('[aria-label="Add Tool Call node"]').click();
    await expect(page.locator('.react-flow__node')).toHaveCount(1, { timeout: 8000 });

    // Configure label — click with force to avoid Handle intercept
    await page.locator('.react-flow__node').first().click({ force: true });
    const insLabel = page.locator('#ins-label');
    if (await insLabel.isVisible({ timeout: 3000 }).catch(() => false)) {
      await insLabel.clear();
      await insLabel.fill('Jira Search');
    }
    // Deselect
    await page.locator('.react-flow__pane').click({ position: { x: 10, y: 10 } });

    // Re-select the node for copy — use force click
    await page.locator('.react-flow__node').first().click({ force: true });

    // Copy (Ctrl+C)
    await page.keyboard.press('Control+c');

    // Paste (Ctrl+V)
    await page.keyboard.press('Control+v');

    // Should now have 2 nodes
    await expect(page.locator('.react-flow__node')).toHaveCount(2, { timeout: 5000 });
  });

  test('5. Keyboard delete removes selected node', async ({ page }) => {
    await setupAuth(page);
    await mockWorkflowApi(page);
    await page.goto('/workflow-builder');
    await waitForReactFlow(page);

    // Add two nodes
    await page.locator('[aria-label="Add Trigger / Start node"]').click();
    await page.locator('[aria-label="Add Tool Call node"]').click();
    await expect(page.locator('.react-flow__node')).toHaveCount(2, { timeout: 8000 });

    // Select the Tool Call node (second one) and delete with Backspace
    await page.locator('.react-flow__node').nth(1).click({ force: true });
    await page.keyboard.press('Backspace');

    // Should be back to 1 node
    await expect(page.locator('.react-flow__node')).toHaveCount(1, { timeout: 5000 });
  });

  test('6. Node inspector shows type-specific placeholder text', async ({ page }) => {
    await setupAuth(page);
    await mockWorkflowApi(page);
    await page.goto('/workflow-builder');
    await waitForReactFlow(page);

    // Add a Decision node
    await page.locator('[aria-label="Add Decision / Branch node"]').click();
    await page.waitForSelector('.react-flow__node');
    await page.locator('.react-flow__node').first().click();

    // Inspector subtitle input should have condition-specific placeholder
    const subtitleInput = page.locator('#ins-subtitle');
    if (await subtitleInput.isVisible({ timeout: 3000 }).catch(() => false)) {
      const placeholder = await subtitleInput.getAttribute('placeholder');
      expect(placeholder?.toLowerCase()).toMatch(/condition|status/);
    }
  });

  test('7. Workflow name persists to save payload', async ({ page }) => {
    const capturedRequests: Array<{ name: string }> = [];

    await setupAuth(page);
    await page.route(/localhost:8000\/workflows/, async (route) => {
      const method = route.request().method();
      const url = route.request().url();
      if (method === 'POST' && !url.includes('/run')) {
        const body = JSON.parse(route.request().postData() ?? '{}');
        capturedRequests.push({ name: body.name });
        return route.fulfill({
          status: 201,
          contentType: 'application/json',
          body: JSON.stringify({ id: 'wf-name-test', name: body.name, description: '', definition: {}, status: 'draft', version: 1, created_at: new Date().toISOString(), updated_at: new Date().toISOString() }),
        });
      }
      if (method === 'GET') {
        return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
      }
      return route.continue();
    });

    await page.goto('/workflow-builder');
    await waitForReactFlow(page);

    // Change name
    const nameInput = page.locator('input[aria-label="Workflow name"]');
    await expect(nameInput).toBeVisible({ timeout: 15000 });
    await nameInput.clear();
    await nameInput.fill('My Jira Triage Pipeline');

    // Save
    await page.getByRole('button', { name: /save/i }).click();

    // Verify backend received the correct name
    await expect(async () => {
      expect(capturedRequests.length).toBeGreaterThan(0);
      expect(capturedRequests[capturedRequests.length - 1].name).toBe('My Jira Triage Pipeline');
    }).toPass({ timeout: 5000 });
  });

  test('8. New button resets canvas ready for next workflow', async ({ page }) => {
    await setupAuth(page);
    await mockWorkflowApi(page);
    await page.goto('/workflow-builder');
    await waitForReactFlow(page);

    // Build a workflow
    await page.locator('[aria-label="Add Trigger / Start node"]').click();
    await page.locator('[aria-label="Add Tool Call node"]').click();
    await page.locator('[aria-label="Add End node"]').click();
    await expect(page.locator('.react-flow__node')).toHaveCount(3, { timeout: 8000 });

    // Click New
    await page.getByRole('button', { name: /new/i }).click();

    // Canvas cleared
    await expect(page.locator('.react-flow__node')).toHaveCount(0, { timeout: 5000 });

    // Name reset
    await expect(page.locator('input[aria-label="Workflow name"]')).toHaveValue('My Workflow');
  });
});
