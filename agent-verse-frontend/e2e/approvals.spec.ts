/**
 * E2E tests — Approvals page (world-class rebuild)
 *
 * Covers:
 *   - Page structure: heading, live indicator, tabs, filter pills
 *   - Empty state
 *   - Approval cards: content, risk badge, time-ago, note textarea
 *   - Single approve / reject
 *   - Bulk selection: checkboxes, Select All, bulk toolbar, batch API call
 *   - Risk filter: pills filter displayed cards
 *   - Sort: risk / time toggle
 *   - Keyboard shortcuts dialog
 *   - History tab: loads resolved, empty state
 *   - SLA stats bar renders when backend returns stats
 *   - SSE live indicator display
 */
import { test, expect, type Page } from '@playwright/test';

// ── Auth + shared setup ───────────────────────────────────────────────────────

async function setupAuth(page: Page) {
  await page.route(/localhost:8000/, (route) =>
    route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ detail: 'not found' }) })
  );
  await page.addInitScript(() => {
    localStorage.setItem('av-auth', JSON.stringify({
      state: { apiKey: 'test-key', tenantId: 'test-tenant', plan: 'free', isAuthenticated: true },
      version: 0,
    }));
    localStorage.setItem('av_api_key', 'test-key');
    sessionStorage.setItem('av_api_key', 'test-key');
  });
  await page.route('**/tenants/me', (route) =>
    route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ tenant_id: 'test-tenant', name: 'Test Org', plan: 'free' }),
    })
  );
}

function makeApproval(overrides: Partial<{
  request_id: string; goal_id: string; action: string;
  risk_level: string; status: string; created_at: string;
}> = {}) {
  return {
    request_id: 'req-001',
    goal_id:    'goal-abc123',
    action:     'deploy_to_production --env=prod',
    risk_level: 'high',
    status:     'pending',
    created_at: new Date().toISOString(),
    ...overrides,
  };
}

async function mockApprovalsApi(
  page: Page,
  approvals: ReturnType<typeof makeApproval>[] = [],
  opts: { history?: object[]; sla?: object } = {}
) {
  await page.route(/localhost:8000\/governance\/approvals\/sla-stats/, (route) =>
    route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify(opts.sla ?? {}),
    })
  );
  await page.route(/localhost:8000\/governance\/approvals\/history/, (route) =>
    route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify(opts.history ?? []),
    })
  );
  await page.route(/localhost:8000\/governance\/approvals\/stream/, (route) =>
    route.fulfill({ status: 200, contentType: 'text/event-stream', body: '' })
  );
  await page.route(/localhost:8000\/governance\/approvals$/, async (route) => {
    const method = route.request().method();
    if (method === 'GET')
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(approvals) });
    return route.fulfill({ status: 404, contentType: 'application/json', body: '{}' });
  });
  await page.route(/localhost:8000\/governance\/approvals\/[^/]+\/approve/, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'approved' }) })
  );
  await page.route(/localhost:8000\/governance\/approvals\/[^/]+\/reject/, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'rejected' }) })
  );
  await page.route(/localhost:8000\/governance\/hitl\/batch-approve/, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ approved: 2, rejected: 0, not_found: 0, results: [] }) })
  );
}

// ═══════════════════════════════════════════════════════════════════════════════
// Page structure
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Approvals — Page structure', () => {
  test('shows Approval Inbox heading', async ({ page }) => {
    await setupAuth(page);
    await mockApprovalsApi(page);
    await page.goto('/approvals');
    await expect(page.getByRole('heading', { name: /approval inbox/i })).toBeVisible({ timeout: 15000 });
  });

  test('shows subtitle text', async ({ page }) => {
    await setupAuth(page);
    await mockApprovalsApi(page);
    await page.goto('/approvals');
    await expect(page.getByText(/human-in-the-loop/i)).toBeVisible({ timeout: 15000 });
  });

  test('shows Live SSE indicator', async ({ page }) => {
    await setupAuth(page);
    await mockApprovalsApi(page);
    await page.goto('/approvals');
    // The SSE indicator might say "Live" (when mock responds) or "Reconnecting…"
    await expect(page.getByText(/live|reconnecting/i)).toBeVisible({ timeout: 15000 });
  });

  test('shows Inbox and History tabs', async ({ page }) => {
    await setupAuth(page);
    await mockApprovalsApi(page);
    await page.goto('/approvals');
    await expect(page.getByRole('tab', { name: /inbox/i })).toBeVisible({ timeout: 10000 });
    await expect(page.getByRole('tab', { name: /history/i })).toBeVisible();
  });

  test('shows keyboard shortcuts button', async ({ page }) => {
    await setupAuth(page);
    await mockApprovalsApi(page);
    await page.goto('/approvals');
    await expect(page.getByRole('button', { name: /keyboard shortcuts/i })).toBeVisible({ timeout: 10000 });
  });

  test('shows risk filter pills', async ({ page }) => {
    await setupAuth(page);
    await mockApprovalsApi(page);
    await page.goto('/approvals');
    await expect(page.getByRole('button', { name: /^all$/i })).toBeVisible({ timeout: 10000 });
    await expect(page.getByRole('button', { name: /^critical$/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /^high$/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /^medium$/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /^low$/i })).toBeVisible();
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// Empty state
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Approvals — Empty state', () => {
  test('shows All clear empty state when no requests', async ({ page }) => {
    await setupAuth(page);
    await mockApprovalsApi(page, []);
    await page.goto('/approvals');
    await expect(page.getByTestId('empty-state')).toBeVisible({ timeout: 15000 });
    await expect(page.getByText(/all clear/i)).toBeVisible();
    await expect(page.getByText(/no pending approval requests/i)).toBeVisible();
  });

  test('empty state has helpful sub-message', async ({ page }) => {
    await setupAuth(page);
    await mockApprovalsApi(page, []);
    await page.goto('/approvals');
    await expect(page.getByText(/agents are running autonomously/i)).toBeVisible({ timeout: 10000 });
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// Approval cards
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Approvals — Cards', () => {
  test('shows action text and goal ID', async ({ page }) => {
    await setupAuth(page);
    await mockApprovalsApi(page, [makeApproval()]);
    await page.goto('/approvals');
    await expect(page.getByText('deploy_to_production --env=prod')).toBeVisible({ timeout: 15000 });
    await expect(page.getByText(/goal-abc123/)).toBeVisible();
  });

  test('shows risk level badge', async ({ page }) => {
    await setupAuth(page);
    await mockApprovalsApi(page, [makeApproval({ risk_level: 'critical' })]);
    await page.goto('/approvals');
    await expect(page.getByTestId('approval-card')).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('critical').first()).toBeVisible();
  });

  test('shows pending badge on card', async ({ page }) => {
    await setupAuth(page);
    await mockApprovalsApi(page, [makeApproval()]);
    await page.goto('/approvals');
    await expect(page.getByText('pending').first()).toBeVisible({ timeout: 10000 });
  });

  test('shows Approve and Reject buttons', async ({ page }) => {
    await setupAuth(page);
    await mockApprovalsApi(page, [makeApproval()]);
    await page.goto('/approvals');
    await expect(page.getByRole('button', { name: /approve request/i })).toBeVisible({ timeout: 10000 });
    await expect(page.getByRole('button', { name: /reject request/i })).toBeVisible();
  });

  test('Add note button expands note textarea', async ({ page }) => {
    await setupAuth(page);
    await mockApprovalsApi(page, [makeApproval()]);
    await page.goto('/approvals');
    await expect(page.getByTestId('approval-card')).toBeVisible({ timeout: 10000 });
    await page.getByRole('button', { name: /add note/i }).click();
    await expect(page.getByLabel(/approval note/i)).toBeVisible({ timeout: 5000 });
  });

  test('pending count badge shows in heading', async ({ page }) => {
    await setupAuth(page);
    await mockApprovalsApi(page, [makeApproval(), makeApproval({ request_id: 'req-002' })]);
    await page.goto('/approvals');
    // Badge with count 2 in the heading area
    await expect(page.locator('h1').getByText('2')).toBeVisible({ timeout: 10000 });
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// Single approve / reject
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Approvals — Approve / Reject', () => {
  test('clicking Approve calls POST /governance/approvals/{id}/approve', async ({ page }) => {
    let approveUrl = '';
    await setupAuth(page);
    await mockApprovalsApi(page, [makeApproval()]);
    await page.route(/localhost:8000\/governance\/approvals\/.*\/approve/, (route) => {
      approveUrl = route.request().url();
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'approved' }) });
    });
    await page.goto('/approvals');
    await page.getByRole('button', { name: /approve request/i }).click();
    await page.waitForTimeout(2000);
    expect(approveUrl).toContain('req-001');
    expect(approveUrl).toContain('/approve');
  });

  test('clicking Reject calls POST /governance/approvals/{id}/reject', async ({ page }) => {
    let rejectUrl = '';
    await setupAuth(page);
    await mockApprovalsApi(page, [makeApproval()]);
    await page.route(/localhost:8000\/governance\/approvals\/.*\/reject/, (route) => {
      rejectUrl = route.request().url();
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'rejected' }) });
    });
    await page.goto('/approvals');
    await page.getByRole('button', { name: /reject request/i }).click();
    await page.waitForTimeout(2000);
    expect(rejectUrl).toContain('req-001');
    expect(rejectUrl).toContain('/reject');
  });

  test('approve request body includes approver and note', async ({ page }) => {
    let requestBody = '';
    await setupAuth(page);
    await mockApprovalsApi(page, [makeApproval()]);
    await page.route(/localhost:8000\/governance\/approvals\/.*\/approve/, async (route) => {
      requestBody = route.request().postData() ?? '';
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'approved' }) });
    });
    await page.goto('/approvals');
    // Open note and add a note first
    await page.getByRole('button', { name: /add note/i }).click();
    await page.getByLabel(/approval note/i).fill('LGTM');
    await page.getByRole('button', { name: /approve request/i }).click();
    await page.waitForTimeout(2000);
    const body = JSON.parse(requestBody);
    expect(body).toHaveProperty('approver');
    expect(body.note).toBe('LGTM');
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// Risk filter
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Approvals — Risk filter', () => {
  test('filtering by "critical" hides non-critical cards', async ({ page }) => {
    await setupAuth(page);
    await mockApprovalsApi(page, [
      makeApproval({ request_id: 'r1', risk_level: 'critical', action: 'Critical action' }),
      makeApproval({ request_id: 'r2', risk_level: 'low',      action: 'Low action' }),
    ]);
    await page.goto('/approvals');
    await expect(page.getByText('Critical action')).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('Low action')).toBeVisible();

    await page.getByRole('button', { name: /^critical$/i }).click();
    await expect(page.getByText('Critical action')).toBeVisible();
    await expect(page.getByText('Low action')).not.toBeVisible({ timeout: 3000 });
  });

  test('filtering by "medium" shows correct empty state', async ({ page }) => {
    await setupAuth(page);
    await mockApprovalsApi(page, [makeApproval({ risk_level: 'high' })]);
    await page.goto('/approvals');
    await page.getByRole('button', { name: /^medium$/i }).click();
    await expect(page.getByText(/no pending medium risk requests/i)).toBeVisible({ timeout: 5000 });
    await expect(page.getByRole('button', { name: /show all requests/i })).toBeVisible();
  });

  test('"Show all requests" link clears risk filter', async ({ page }) => {
    await setupAuth(page);
    await mockApprovalsApi(page, [makeApproval({ risk_level: 'high', action: 'High risk action' })]);
    await page.goto('/approvals');
    await page.getByRole('button', { name: /^medium$/i }).click();
    await expect(page.getByRole('button', { name: /show all requests/i })).toBeVisible({ timeout: 5000 });
    await page.getByRole('button', { name: /show all requests/i }).click();
    await expect(page.getByText('High risk action')).toBeVisible({ timeout: 5000 });
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// Sort
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Approvals — Sort', () => {
  test('sort dropdown shows Risk and Time options', async ({ page }) => {
    await setupAuth(page);
    await mockApprovalsApi(page, [makeApproval()]);
    await page.goto('/approvals');
    await expect(page.getByTestId('approval-card')).toBeVisible({ timeout: 10000 });
    const sortSelect = page.getByLabel('Sort by');
    await expect(sortSelect).toBeVisible();
    const options = await sortSelect.locator('option').allTextContents();
    expect(options).toContain('Sort: Risk');
    expect(options).toContain('Sort: Time');
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// Bulk selection
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Approvals — Bulk actions', () => {
  test('checkboxes appear on each card', async ({ page }) => {
    await setupAuth(page);
    await mockApprovalsApi(page, [
      makeApproval(),
      makeApproval({ request_id: 'req-002', action: 'second action' }),
    ]);
    await page.goto('/approvals');
    await expect(page.getByTestId('approval-card').first()).toBeVisible({ timeout: 10000 });
    const checkboxes = page.getByRole('checkbox', { name: /select request/i });
    await expect(checkboxes).toHaveCount(2);
  });

  test('Select All checkbox appears with multiple requests', async ({ page }) => {
    await setupAuth(page);
    await mockApprovalsApi(page, [
      makeApproval(),
      makeApproval({ request_id: 'req-002', action: 'action 2' }),
    ]);
    await page.goto('/approvals');
    await expect(page.getByRole('checkbox', { name: /select all requests/i })).toBeVisible({ timeout: 10000 });
  });

  test('checking Select All shows bulk toolbar with Approve/Reject all', async ({ page }) => {
    await setupAuth(page);
    await mockApprovalsApi(page, [
      makeApproval(),
      makeApproval({ request_id: 'req-002', action: 'action 2' }),
    ]);
    await page.goto('/approvals');
    await page.getByRole('checkbox', { name: /select all requests/i }).check();
    await expect(page.getByRole('button', { name: /approve all/i })).toBeVisible({ timeout: 5000 });
    await expect(page.getByRole('button', { name: /reject all/i })).toBeVisible();
    await expect(page.getByText(/2 selected/i)).toBeVisible();
  });

  test('bulk Approve All opens confirm dialog', async ({ page }) => {
    await setupAuth(page);
    await mockApprovalsApi(page, [
      makeApproval(),
      makeApproval({ request_id: 'req-002', action: 'action 2' }),
    ]);
    await page.goto('/approvals');
    await page.getByRole('checkbox', { name: /select all requests/i }).check();
    await page.getByRole('button', { name: /approve all/i }).click();
    await expect(page.getByText(/approve 2 request/i)).toBeVisible({ timeout: 5000 });
  });

  test('bulk Reject All calls POST /governance/hitl/batch-approve', async ({ page }) => {
    let batchUrl = '';
    let batchBody = '';
    await setupAuth(page);
    await mockApprovalsApi(page, [
      makeApproval(),
      makeApproval({ request_id: 'req-002', action: 'action 2' }),
    ]);
    await page.route(/localhost:8000\/governance\/hitl\/batch-approve/, async (route) => {
      batchUrl = route.request().url();
      batchBody = route.request().postData() ?? '';
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ approved: 0, rejected: 2, not_found: 0, results: [] }) });
    });
    await page.goto('/approvals');
    await page.getByRole('checkbox', { name: /select all requests/i }).check();
    await page.getByRole('button', { name: /reject all/i }).click();
    // Confirm in dialog
    await page.getByRole('button', { name: /reject all/i }).last().click();
    await page.waitForTimeout(2000);
    expect(batchUrl).toContain('/hitl/batch-approve');
    const parsed = JSON.parse(batchBody);
    expect(parsed.action).toBe('reject');
    expect(parsed.request_ids).toContain('req-001');
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// Keyboard shortcuts
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Approvals — Keyboard shortcuts', () => {
  test('clicking keyboard shortcuts button opens help dialog', async ({ page }) => {
    await setupAuth(page);
    await mockApprovalsApi(page, []);
    await page.goto('/approvals');
    await page.getByRole('button', { name: /keyboard shortcuts/i }).click();
    await expect(page.getByText(/keyboard shortcuts/i)).toBeVisible({ timeout: 5000 });
    await expect(page.getByText(/navigate between requests/i)).toBeVisible();
    await expect(page.getByText(/approve focused request/i)).toBeVisible();
    await expect(page.getByText(/reject focused request/i)).toBeVisible();
  });

  test('help dialog can be closed', async ({ page }) => {
    await setupAuth(page);
    await mockApprovalsApi(page, []);
    await page.goto('/approvals');
    await page.getByRole('button', { name: /keyboard shortcuts/i }).click();
    await page.getByRole('button', { name: /close/i }).click();
    await expect(page.getByText(/navigate between requests/i)).not.toBeVisible({ timeout: 5000 });
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// History tab
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Approvals — History tab', () => {
  test('switching to History tab loads resolved requests', async ({ page }) => {
    await setupAuth(page);
    await mockApprovalsApi(page, [], {
      history: [{
        request_id: 'h1', goal_id: 'g1', action: 'Approved deployment',
        risk_level: 'high', status: 'approved',
        approver: 'user:admin', note: 'All good',
        created_at: new Date().toISOString(), resolved_at: new Date().toISOString(),
      }],
    });
    await page.goto('/approvals');
    await page.getByRole('tab', { name: /history/i }).click();
    await expect(page.getByText('Approved deployment')).toBeVisible({ timeout: 10000 });
    await expect(page.getByTestId('history-row')).toBeVisible();
  });

  test('history row shows status badge (approved)', async ({ page }) => {
    await setupAuth(page);
    await mockApprovalsApi(page, [], {
      history: [{
        request_id: 'h1', goal_id: 'g1', action: 'Deploy action',
        risk_level: 'medium', status: 'approved',
        created_at: new Date().toISOString(), resolved_at: new Date().toISOString(),
      }],
    });
    await page.goto('/approvals');
    await page.getByRole('tab', { name: /history/i }).click();
    await expect(page.getByText('approved')).toBeVisible({ timeout: 10000 });
  });

  test('history row shows rejected status', async ({ page }) => {
    await setupAuth(page);
    await mockApprovalsApi(page, [], {
      history: [{
        request_id: 'h1', goal_id: 'g1', action: 'Rejected action',
        risk_level: 'critical', status: 'rejected',
        created_at: new Date().toISOString(), resolved_at: new Date().toISOString(),
      }],
    });
    await page.goto('/approvals');
    await page.getByRole('tab', { name: /history/i }).click();
    await expect(page.getByText('rejected')).toBeVisible({ timeout: 10000 });
  });

  test('history shows empty state when no resolved requests', async ({ page }) => {
    await setupAuth(page);
    await mockApprovalsApi(page, [], { history: [] });
    await page.goto('/approvals');
    await page.getByRole('tab', { name: /history/i }).click();
    await expect(page.getByText(/no history yet/i)).toBeVisible({ timeout: 10000 });
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// Stats bar
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Approvals — Stats bar', () => {
  test('shows per-risk stats with counts', async ({ page }) => {
    await setupAuth(page);
    await mockApprovalsApi(page, [
      makeApproval({ risk_level: 'critical' }),
      makeApproval({ request_id: 'r2', risk_level: 'high' }),
      makeApproval({ request_id: 'r3', risk_level: 'high' }),
    ]);
    await page.goto('/approvals');
    await expect(page.getByTestId('approval-card').first()).toBeVisible({ timeout: 10000 });
    // Stats bar shows count per risk level
    await expect(page.getByText('critical')).toBeVisible();
    await expect(page.getByText('high')).toBeVisible();
  });

  test('shows avg resolution time from sla-stats', async ({ page }) => {
    await setupAuth(page);
    await mockApprovalsApi(page, [], {
      sla: { pending: 0, approved: 10, denied: 2, timed_out: 1, within_sla: 9, avg_resolution_seconds: 240 },
    });
    await page.goto('/approvals');
    await expect(page.getByText(/avg resolution/i)).toBeVisible({ timeout: 10000 });
    await expect(page.getByText(/4m/)).toBeVisible(); // 240 seconds = 4 minutes
  });
});
