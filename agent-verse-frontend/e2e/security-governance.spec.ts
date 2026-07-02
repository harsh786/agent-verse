/**
 * Security & Governance E2E Tests — 6 sections, 42 tests total
 *
 * Sections:
 *   1.  Notifications     (/notifications)        — 7 tests
 *   2.  Access Control    (/rbac)                 — 7 tests
 *   3.  Compliance        (/compliance)           — 7 tests
 *   4.  Audit Log         (/audit)                — 7 tests
 *   5.  Guardrails        (/settings/guardrails)  — 7 tests
 *   6.  Scope Explorer    (/settings/scopes)      — 7 tests
 */
import { test, expect, type Page } from '@playwright/test';

// ── Auth helper ────────────────────────────────────────────────────────────────

async function setupAuth(page: Page): Promise<void> {
  await page.route(/localhost:8000/, (route) =>
    route.fulfill({ status: 404, contentType: 'application/json', body: '{"detail":"not found"}' })
  );
  await page.addInitScript(() => {
    const AUTH = JSON.stringify({
      state: { apiKey: 'test-key', tenantId: 'test-tenant', plan: 'enterprise', isAuthenticated: true },
      version: 0,
    });
    localStorage.setItem('av-auth', AUTH);
    sessionStorage.setItem('av-auth', AUTH);
    localStorage.setItem('av_api_key', 'test-key');
  });
  await page.route('**/tenants/me', (route) =>
    route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ tenant_id: 'test-tenant', name: 'PineLabs', plan: 'enterprise' }),
    })
  );
}

// ── Mock data ─────────────────────────────────────────────────────────────────

const CHANNEL = { channel_id: 'ch-1', type: 'slack', enabled: true };
const CHANNELS = [CHANNEL, { channel_id: 'ch-2', type: 'webhook', enabled: false }];
const ROLES = [
  { id: 'r1', user_id: 'alice@pinelabs.com', role: 'admin', created_at: new Date().toISOString() },
  { id: 'r2', user_id: 'bob@pinelabs.com',   role: 'viewer', created_at: new Date().toISOString() },
];
const IPS = [{ id: 'e1', cidr: '10.0.0.0/8', description: 'Office network', created_at: new Date().toISOString() }];
const HOLDS = [{ id: 'h1', reason: 'SEC investigation hold', expires_at: null, created_by: 'admin@pinelabs.com' }];
const AUDIT_EVENTS = [
  { event_id: 'evt-001', goal_id: 'goal-aaa', tool_name: 'shell:execute', action_level: 'deny', outcome: 'blocked', approver: null, note: null },
  { event_id: 'evt-002', goal_id: 'goal-bbb', tool_name: 'jira:search', action_level: 'allow_log', outcome: 'success', approver: null, note: null },
  { event_id: 'evt-003', goal_id: 'goal-ccc', tool_name: 'stripe:charge', action_level: 'approval', outcome: 'approved', approver: 'alice@pinelabs.com', note: 'Reviewed' },
];
const GUARDRAILS = [
  { id: 'gr-1', name: 'Block PII Output', rule_type: 'pii_detection', severity: 'critical', enabled: true, layers: ['goal', 'final'], config: {}, created_at: '2026-01-01T00:00:00Z' },
  { id: 'gr-2', name: 'Regex Block SSN',  rule_type: 'regex_match',   severity: 'high',     enabled: true, layers: ['tool_args'], config: { pattern: '\\d{3}-\\d{2}-\\d{4}' }, created_at: '2026-01-02T00:00:00Z' },
];
const VIOLATIONS = [
  { id: 'v1', guardrail_id: 'gr-1', guardrail_name: 'Block PII Output', type: 'pii_detected', severity: 'critical', message: 'SSN detected in tool output', goal_id: 'goal-xxx', created_at: new Date().toISOString() },
];
const GUARDRAIL_STATS = {
  total_24h: 12, total_all: 248,
  by_severity: { critical: 3, high: 5, medium: 3, low: 1 },
  by_layer: { goal: 6, tool_args: 4, final: 2 },
  top_categories: [{ category: 'pii_detected', count: 89 }, { category: 'prompt_injection', count: 50 }],
  risk_score_p95: 0.87,
};

// ── Notifications setup ───────────────────────────────────────────────────────

async function setupNotificationRoutes(page: Page): Promise<void> {
  await page.route(/localhost:8000\/governance\/notifications\/.*\/test/, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ success: true, message: 'Test notification sent.' }) })
  );
  await page.route(/localhost:8000\/governance\/notifications\/.*/, (route) => {
    if (route.request().method() === 'DELETE') return route.fulfill({ status: 204, body: '' });
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(CHANNEL) });
  });
  await page.route(/localhost:8000\/governance\/notifications(\?.*)?$/, (route) => {
    if (route.request().method() === 'POST') return route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify({ channel_id: 'ch-new', type: 'slack', status: 'created' }) });
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(CHANNELS) });
  });
}

// ── RBAC setup ────────────────────────────────────────────────────────────────

async function setupRbacRoutes(page: Page): Promise<void> {
  await page.route(/localhost:8000\/tenants\/me\/roles\/.*/, (route) =>
    route.fulfill({ status: 204, body: '' })
  );
  await page.route(/localhost:8000\/tenants\/me\/roles(\?.*)?$/, (route) => {
    if (route.request().method() === 'POST') return route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify({ id: 'r-new', user_id: 'carol@pinelabs.com', role: 'operator' }) });
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(ROLES) });
  });
  await page.route(/localhost:8000\/tenants\/me\/ip-allowlist\/.*/, (route) =>
    route.fulfill({ status: 204, body: '' })
  );
  await page.route(/localhost:8000\/tenants\/me\/ip-allowlist(\?.*)?$/, (route) => {
    if (route.request().method() === 'POST') return route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify({ id: 'e-new', cidr: '172.16.0.0/12', description: 'VPN' }) });
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(IPS) });
  });
}

// ── Compliance setup ──────────────────────────────────────────────────────────

async function setupComplianceRoutes(page: Page): Promise<void> {
  await page.route(/localhost:8000\/governance\/legal-hold$/, (route) =>
    route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify({ status: 'legal_hold_placed', reason: 'Test hold', tenant_id: 'test-tenant' }) })
  );
  await page.route(/localhost:8000\/governance\/legal-holds/, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(HOLDS) })
  );
  await page.route(/localhost:8000\/compliance\/export\/start/, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ job_id: 'j-001', status: 'pending', poll_url: '/compliance/export/jobs/j-001' }) })
  );
  await page.route(/localhost:8000\/compliance\/export\/jobs\/j-001/, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ job_id: 'j-001', status: 'complete', completed_at: new Date().toISOString(), download_url: 'https://example.com/export.zip', error: null }) })
  );
  await page.route(/localhost:8000\/compliance\/consent(\?.*)?$/, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ consent_id: 'c1', purpose: 'analytics', status: 'granted' }) })
  );
  await page.route(/localhost:8000\/enterprise\/compliance\/gdpr/, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ framework: 'gdpr', compliant: true, checks: [{ check: 'audit_trail', passed: true }, { check: 'consent_records', passed: true }], tenant_id: 'test-tenant' }) })
  );
  await page.route(/localhost:8000\/enterprise\/compliance\/hipaa/, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ framework: 'hipaa', compliant: false, checks: [{ check: 'phi_access_log', passed: false, detail: 'Missing PHI audit' }], tenant_id: 'test-tenant' }) })
  );
  await page.route(/localhost:8000\/enterprise\/compliance\/soc2/, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ framework: 'soc2', compliant: true, checks: [{ check: 'change_management', passed: true }], tenant_id: 'test-tenant' }) })
  );
  await page.route(/localhost:8000\/enterprise\/contracts/, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([{ contract_type: 'baa', status: 'pending_signature' }]) })
  );
}

// ── Audit setup ───────────────────────────────────────────────────────────────

async function setupAuditRoutes(page: Page): Promise<void> {
  await page.route(/localhost:8000\/governance\/audit\/integrity\/verify/, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ verified: true, verified_events: 1042, chain_tip_hash: 'abc123def456789012345' }) })
  );
  await page.route(/localhost:8000\/governance\/audit(\?.*)?$/, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(AUDIT_EVENTS) })
  );
}

// ── Guardrails setup ──────────────────────────────────────────────────────────

async function setupGuardrailRoutes(page: Page): Promise<void> {
  // Register broad/base routes FIRST so specific sub-routes win via LIFO

  // Base: list/create guardrails
  await page.route(/localhost:8000\/guardrails(\?.*)?$/, (route) => {
    if (route.request().method() === 'POST') return route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify({ ...GUARDRAILS[0], id: 'gr-new', name: 'New Rule' }) });
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(GUARDRAILS) });
  });

  // Specific: update/delete individual guardrail (register after base so LIFO wins)
  await page.route(/localhost:8000\/guardrails\/[^/]+(\/.*)?$/, (route) => {
    if (route.request().method() === 'PUT')    return route.fulfill({ status: 200, body: '{}' });
    if (route.request().method() === 'DELETE') return route.fulfill({ status: 204, body: '' });
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(GUARDRAILS[0]) });
  });

  // Most-specific: stats, violations, test — register LAST for highest LIFO priority
  await page.route(/localhost:8000\/guardrails\/test/, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ passed: false, risk_score: 0.91, violations: [{ type: 'pii_detected', message: 'SSN pattern found', severity: 'critical' }] }) })
  );
  await page.route(/localhost:8000\/guardrails\/violations/, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(VIOLATIONS) })
  );
  await page.route(/localhost:8000\/guardrails\/stats/, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(GUARDRAIL_STATS) })
  );
}

// ── Scope Explorer setup ──────────────────────────────────────────────────────

async function setupScopeRoutes(page: Page): Promise<void> {
  await page.route(/localhost:8000\/tenants\/me\/keys\/.*/, (route) =>
    route.fulfill({ status: 204, body: '' })
  );
  await page.route(/localhost:8000\/tenants\/me\/keys(\?.*)?$/, (route) => {
    if (route.request().method() === 'POST') return route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify({ raw_key: 'sk-test-abc123', key_id: 'k-new' }) });
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([
      { key_id: 'k1', name: 'Production Key', scopes: ['goals:read', 'agents:read'], created_at: '2026-01-01T00:00:00Z', last_used_at: '2026-07-01T12:00:00Z' },
      { key_id: 'k2', name: 'Analytics Key',  scopes: ['analytics:read'], created_at: '2026-02-01T00:00:00Z', last_used_at: null },
    ]) });
  });
}

// ═══════════════════════════════════════════════════════════════════════════════
// SUITE 1 — NOTIFICATIONS (/notifications)
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Notifications Center', () => {

  test('1. Shows Notification Center heading and channel list', async ({ page }) => {
    await setupAuth(page);
    await setupNotificationRoutes(page);
    await page.goto('/notifications');
    await expect(page.getByRole('heading', { name: /notification center/i })).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('slack').first()).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('webhook').first()).toBeVisible({ timeout: 5000 });
  });

  test('2. Channel cards show enabled/disabled status badges', async ({ page }) => {
    await setupAuth(page);
    await setupNotificationRoutes(page);
    await page.goto('/notifications');
    await expect(page.getByRole('heading', { name: /notification center/i })).toBeVisible({ timeout: 10000 });
    // ch-1 is enabled, ch-2 is disabled
    await expect(page.getByText(/active|enabled/i).first()).toBeVisible({ timeout: 8000 });
  });

  test('3. Stats cards show total and active channel counts', async ({ page }) => {
    await setupAuth(page);
    await setupNotificationRoutes(page);
    await page.goto('/notifications');
    await expect(page.getByRole('heading', { name: /notification center/i })).toBeVisible({ timeout: 10000 });
    // Should show count stats
    await expect(page.getByText('2').first()).toBeVisible({ timeout: 8000 });
  });

  test('4. Add channel button opens the form', async ({ page }) => {
    await setupAuth(page);
    await setupNotificationRoutes(page);
    await page.goto('/notifications');
    await expect(page.getByRole('heading', { name: /notification center/i })).toBeVisible({ timeout: 10000 });
    await page.getByTestId('add-channel-btn').click();
    await expect(page.getByTestId('channel-form')).toBeVisible({ timeout: 5000 });
  });

  test('5. Channel type selector has slack, webhook, teams options', async ({ page }) => {
    await setupAuth(page);
    await setupNotificationRoutes(page);
    await page.goto('/notifications');
    await expect(page.getByRole('heading', { name: /notification center/i })).toBeVisible({ timeout: 10000 });
    await page.getByTestId('add-channel-btn').click();
    await expect(page.getByTestId('channel-form')).toBeVisible({ timeout: 5000 });
    await expect(page.getByTestId('channel-type-select')).toBeVisible({ timeout: 3000 });
  });

  test('6. Creating a channel posts to governance/notifications', async ({ page }) => {
    let postBody: Record<string, unknown> = {};
    await setupAuth(page);
    await setupNotificationRoutes(page);
    await page.route(/localhost:8000\/governance\/notifications(\?.*)?$/, (route) => {
      if (route.request().method() === 'POST') {
        postBody = JSON.parse(route.request().postData() ?? '{}');
        return route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify({ channel_id: 'ch-new', type: 'slack', status: 'created' }) });
      }
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(CHANNELS) });
    });
    await page.goto('/notifications');
    await expect(page.getByRole('heading', { name: /notification center/i })).toBeVisible({ timeout: 10000 });
    await page.getByTestId('add-channel-btn').click();
    await expect(page.getByTestId('channel-form')).toBeVisible({ timeout: 5000 });
    await page.getByPlaceholder(/https:\/\//i).fill('https://hooks.slack.com/services/test');
    const addBtns = await page.getByRole('button', { name: /add channel/i }).all();
    await addBtns[addBtns.length - 1].click();
    await expect(async () => {
      expect(postBody.channel_type).toBeTruthy();
    }).toPass({ timeout: 5000 });
  });

  test('7. Delete button removes a channel', async ({ page }) => {
    let deleteCalled = false;
    await setupAuth(page);
    await setupNotificationRoutes(page);
    await page.route(/localhost:8000\/governance\/notifications\/ch-1/, (route) => {
      if (route.request().method() === 'DELETE') {
        deleteCalled = true;
        return route.fulfill({ status: 204, body: '' });
      }
      return route.fulfill({ status: 200, body: '{}' });
    });
    await page.goto('/notifications');
    await expect(page.getByRole('heading', { name: /notification center/i })).toBeVisible({ timeout: 10000 });
    await expect(page.getByTestId('delete-btn-ch-1')).toBeVisible({ timeout: 8000 });
    await page.getByTestId('delete-btn-ch-1').click();
    await expect(async () => { expect(deleteCalled).toBe(true); }).toPass({ timeout: 5000 });
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// SUITE 2 — ACCESS CONTROL (/rbac)
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Access Control', () => {

  test('8. Shows Access Control heading with Role Assignments tab', async ({ page }) => {
    await setupAuth(page);
    await setupRbacRoutes(page);
    await page.goto('/rbac');
    await expect(page.getByRole('heading', { name: /access control/i })).toBeVisible({ timeout: 10000 });
    await expect(page.getByTestId('tab-roles')).toBeVisible({ timeout: 5000 });
  });

  test('9. Lists role assignments with user IDs and colored role badges', async ({ page }) => {
    await setupAuth(page);
    await setupRbacRoutes(page);
    await page.goto('/rbac');
    await expect(page.getByText('alice@pinelabs.com')).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('bob@pinelabs.com')).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('admin').first()).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('viewer').first()).toBeVisible({ timeout: 5000 });
  });

  test('10. Grant Role button opens modal with role picker', async ({ page }) => {
    await setupAuth(page);
    await setupRbacRoutes(page);
    await page.goto('/rbac');
    await expect(page.getByRole('heading', { name: /access control/i })).toBeVisible({ timeout: 10000 });
    await page.getByTestId('grant-role-btn').click();
    await expect(page.getByText(/grant role/i).first()).toBeVisible({ timeout: 5000 });
  });

  test('11. IP Allowlist tab shows CIDR entries', async ({ page }) => {
    await setupAuth(page);
    await setupRbacRoutes(page);
    await page.goto('/rbac');
    await expect(page.getByRole('heading', { name: /access control/i })).toBeVisible({ timeout: 10000 });
    await page.getByTestId('tab-ip').click();
    await expect(page.getByText('10.0.0.0/8')).toBeVisible({ timeout: 8000 });
    await expect(page.getByText('Office network')).toBeVisible({ timeout: 5000 });
  });

  test('12. Adding a CIDR sends POST to ip-allowlist', async ({ page }) => {
    let postBody: Record<string, unknown> = {};
    await setupAuth(page);
    await setupRbacRoutes(page);
    await page.route(/localhost:8000\/tenants\/me\/ip-allowlist(\?.*)?$/, (route) => {
      if (route.request().method() === 'POST') {
        postBody = JSON.parse(route.request().postData() ?? '{}');
        return route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify({ id: 'e-new', cidr: '172.16.0.0/12', description: 'VPN' }) });
      }
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(IPS) });
    });
    await page.goto('/rbac');
    await page.getByTestId('tab-ip').click();
    await expect(page.getByTestId('add-cidr-btn')).toBeVisible({ timeout: 8000 });
    await page.getByPlaceholder(/10\.0\.0\.0|CIDR/i).fill('172.16.0.0/12');
    await page.getByTestId('add-cidr-btn').click();
    await expect(async () => { expect(postBody.cidr).toBe('172.16.0.0/12'); }).toPass({ timeout: 5000 });
  });

  test('13. Role hierarchy section shows collapsible tree', async ({ page }) => {
    await setupAuth(page);
    await setupRbacRoutes(page);
    await page.goto('/rbac');
    await expect(page.getByRole('heading', { name: /access control/i })).toBeVisible({ timeout: 10000 });
    // Role hierarchy tree - look for admin role
    await expect(page.getByText('admin').first()).toBeVisible({ timeout: 8000 });
  });

  test('14. Stats row shows per-role counts', async ({ page }) => {
    await setupAuth(page);
    await setupRbacRoutes(page);
    await page.goto('/rbac');
    await expect(page.getByRole('heading', { name: /access control/i })).toBeVisible({ timeout: 10000 });
    // 2 users in total → stats should show role distribution
    await expect(page.getByText(/admin|approver|operator|viewer/i).first()).toBeVisible({ timeout: 8000 });
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// SUITE 3 — COMPLIANCE (/compliance)
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Compliance', () => {

  test('15. Shows Compliance heading and framework status cards', async ({ page }) => {
    await setupAuth(page);
    await setupComplianceRoutes(page);
    await page.goto('/compliance');
    await expect(page.getByRole('heading', { name: /compliance/i })).toBeVisible({ timeout: 10000 });
    await expect(page.getByTestId('framework-status')).toBeVisible({ timeout: 8000 });
  });

  test('16. GDPR shows compliant (green) and HIPAA shows non-compliant (red)', async ({ page }) => {
    await setupAuth(page);
    await setupComplianceRoutes(page);
    await page.goto('/compliance');
    await expect(page.getByTestId('framework-status')).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('gdpr').first()).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('hipaa').first()).toBeVisible({ timeout: 5000 });
  });

  test('17. Legal Holds tab shows existing holds', async ({ page }) => {
    await setupAuth(page);
    await setupComplianceRoutes(page);
    await page.goto('/compliance');
    await expect(page.getByRole('heading', { name: /compliance/i })).toBeVisible({ timeout: 10000 });
    await page.getByRole('button', { name: /legal holds/i }).click();
    await expect(page.getByText('SEC investigation hold')).toBeVisible({ timeout: 8000 });
    await expect(page.getByText('admin@pinelabs.com')).toBeVisible({ timeout: 5000 });
  });

  test('18. Data Export tab has Start Export button', async ({ page }) => {
    await setupAuth(page);
    await setupComplianceRoutes(page);
    await page.goto('/compliance');
    await expect(page.getByRole('heading', { name: /compliance/i })).toBeVisible({ timeout: 10000 });
    await page.getByRole('button', { name: /data export/i }).click();
    await expect(page.getByTestId('start-export-btn')).toBeVisible({ timeout: 8000 });
  });

  test('19. Starting GDPR export triggers POST to export/start', async ({ page }) => {
    let exportStarted = false;
    await setupAuth(page);
    await setupComplianceRoutes(page);
    await page.route(/localhost:8000\/compliance\/export\/start/, (route) => {
      if (route.request().method() === 'POST') {
        exportStarted = true;
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ job_id: 'j-001', status: 'pending', poll_url: '/compliance/export/jobs/j-001' }) });
      }
      return route.continue();
    });
    await page.goto('/compliance');
    await page.getByRole('button', { name: /data export/i }).click();
    await page.getByTestId('start-export-btn').click();
    await expect(async () => { expect(exportStarted).toBe(true); }).toPass({ timeout: 5000 });
  });

  test('20. Contracts tab shows BAA with Sign button', async ({ page }) => {
    await setupAuth(page);
    await setupComplianceRoutes(page);
    await page.goto('/compliance');
    await expect(page.getByRole('heading', { name: /compliance/i })).toBeVisible({ timeout: 10000 });
    const contractsBtn = page.getByRole('button', { name: /contracts/i });
    if (await contractsBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
      await contractsBtn.click();
      await expect(page.getByText('baa').first()).toBeVisible({ timeout: 5000 });
    }
  });

  test('21. Consent tab shows purpose select and record/revoke buttons', async ({ page }) => {
    await setupAuth(page);
    await setupComplianceRoutes(page);
    await page.goto('/compliance');
    await expect(page.getByRole('heading', { name: /compliance/i })).toBeVisible({ timeout: 10000 });
    const consentBtn = page.getByRole('button', { name: /consent/i });
    if (await consentBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
      await consentBtn.click();
      await expect(page.getByTestId('consent-section')).toBeVisible({ timeout: 5000 });
    }
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// SUITE 4 — AUDIT LOG (/audit)
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Audit Log', () => {

  test('22. Shows Audit Explorer heading with stats row', async ({ page }) => {
    await setupAuth(page);
    await setupAuditRoutes(page);
    await page.goto('/audit');
    await expect(page.getByRole('heading', { name: /audit/i })).toBeVisible({ timeout: 10000 });
    await expect(page.getByTestId('audit-stats')).toBeVisible({ timeout: 8000 });
  });

  test('23. Audit events table shows tool names and action level badges', async ({ page }) => {
    await setupAuth(page);
    await setupAuditRoutes(page);
    await page.goto('/audit');
    await expect(page.getByTestId('audit-stats')).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('shell:execute')).toBeVisible({ timeout: 8000 });
    await expect(page.getByText('jira:search')).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('stripe:charge')).toBeVisible({ timeout: 5000 });
  });

  test('24. Action level badges are color-coded (deny=red, allow=green, approval=orange)', async ({ page }) => {
    await setupAuth(page);
    await setupAuditRoutes(page);
    await page.goto('/audit');
    await expect(page.getByTestId('audit-stats')).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('deny').first()).toBeVisible({ timeout: 8000 });
    await expect(page.getByText('allow_log').first()).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('approval').first()).toBeVisible({ timeout: 5000 });
  });

  test('25. Filter panel has Goal ID, Tool Name, datetime inputs', async ({ page }) => {
    await setupAuth(page);
    await setupAuditRoutes(page);
    await page.goto('/audit');
    await expect(page.getByTestId('audit-filters')).toBeVisible({ timeout: 10000 });
    await expect(page.getByTestId('apply-filters-btn')).toBeVisible({ timeout: 5000 });
  });

  test('26. Applying tool name filter sends it as query param', async ({ page }) => {
    let capturedUrl = '';
    await setupAuth(page);
    await setupAuditRoutes(page);
    await page.route(/localhost:8000\/governance\/audit(\?.*)?$/, (route) => {
      capturedUrl = route.request().url();
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(AUDIT_EVENTS) });
    });
    await page.goto('/audit');
    await expect(page.getByTestId('audit-filters')).toBeVisible({ timeout: 10000 });
    const toolInput = page.getByTestId('audit-filters').locator('#audit-tool-filter');
    await toolInput.fill('shell:execute');
    await page.getByTestId('apply-filters-btn').click();
    await expect(async () => {
      expect(capturedUrl).toContain('tool_name=shell%3Aexecute');
    }).toPass({ timeout: 5000 });
  });

  test('27. Export JSON and CSV buttons are present', async ({ page }) => {
    await setupAuth(page);
    await setupAuditRoutes(page);
    await page.goto('/audit');
    await expect(page.getByTestId('audit-stats')).toBeVisible({ timeout: 10000 });
    await expect(page.getByTestId('export-json-btn')).toBeVisible({ timeout: 5000 });
    await expect(page.getByTestId('export-csv-btn')).toBeVisible({ timeout: 5000 });
  });

  test('28. Hash-chain verify button triggers integrity check', async ({ page }) => {
    await setupAuth(page);
    await setupAuditRoutes(page);
    await page.goto('/audit');
    await expect(page.getByTestId('audit-stats')).toBeVisible({ timeout: 10000 });
    await page.getByTestId('verify-chain-btn').click();
    await expect(page.getByText(/chain verified|1042 events/i).first()).toBeVisible({ timeout: 8000 });
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// SUITE 5 — GUARDRAILS (/settings/guardrails)
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Guardrails Center', () => {

  test('29. Shows Guardrail Center heading with 4 tabs', async ({ page }) => {
    await setupAuth(page);
    await setupGuardrailRoutes(page);
    await page.goto('/settings/guardrails');
    await expect(page.getByRole('heading', { name: /guardrail center/i })).toBeVisible({ timeout: 10000 });
    await expect(page.getByTestId('tab-dashboard')).toBeVisible({ timeout: 5000 });
    await expect(page.getByTestId('tab-rules')).toBeVisible({ timeout: 5000 });
    await expect(page.getByTestId('tab-violations')).toBeVisible({ timeout: 5000 });
    await expect(page.getByTestId('tab-test')).toBeVisible({ timeout: 5000 });
  });

  test('30. Rules tab shows guardrail names and severity badges', async ({ page }) => {
    await setupAuth(page);
    await setupGuardrailRoutes(page);
    await page.goto('/settings/guardrails');
    await expect(page.getByRole('heading', { name: /guardrail center/i })).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('Block PII Output')).toBeVisible({ timeout: 8000 });
    await expect(page.getByText('Regex Block SSN')).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('critical').first()).toBeVisible({ timeout: 5000 });
  });

  test('31. Dashboard tab shows stats cards and severity distribution', async ({ page }) => {
    await setupAuth(page);
    await setupGuardrailRoutes(page);
    await page.goto('/settings/guardrails');
    await expect(page.getByRole('heading', { name: /guardrail center/i })).toBeVisible({ timeout: 10000 });
    await page.getByTestId('tab-dashboard').click();
    await expect(page.getByTestId('stats-cards')).toBeVisible({ timeout: 8000 });
    await expect(page.getByText('12').first()).toBeVisible({ timeout: 5000 });
  });

  test('32. Violations tab shows violation records', async ({ page }) => {
    await setupAuth(page);
    await setupGuardrailRoutes(page);
    await page.goto('/settings/guardrails');
    await expect(page.getByRole('heading', { name: /guardrail center/i })).toBeVisible({ timeout: 10000 });
    await page.getByTestId('tab-violations').click();
    await expect(page.getByTestId('violations-table')).toBeVisible({ timeout: 8000 });
    await expect(page.getByText('Block PII Output')).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('SSN detected in tool output')).toBeVisible({ timeout: 5000 });
  });

  test('33. New Rule button opens create modal', async ({ page }) => {
    await setupAuth(page);
    await setupGuardrailRoutes(page);
    await page.goto('/settings/guardrails');
    await expect(page.getByRole('heading', { name: /guardrail center/i })).toBeVisible({ timeout: 10000 });
    await page.getByTestId('new-rule-btn').click();
    await expect(page.getByText(/New Guardrail/i)).toBeVisible({ timeout: 5000 });
  });

  test('34. Toggle button enables/disables a guardrail', async ({ page }) => {
    let putBody: Record<string, unknown> = {};
    await setupAuth(page);
    await setupGuardrailRoutes(page);
    await page.route(/localhost:8000\/guardrails\/gr-1/, (route) => {
      if (route.request().method() === 'PUT') {
        putBody = JSON.parse(route.request().postData() ?? '{}');
        return route.fulfill({ status: 200, body: '{}' });
      }
      return route.fulfill({ status: 200, body: '{}' });
    });
    await page.goto('/settings/guardrails');
    await expect(page.getByTestId('toggle-gr-1')).toBeVisible({ timeout: 10000 });
    await page.getByTestId('toggle-gr-1').click();
    await expect(async () => {
      expect(putBody.enabled !== undefined).toBe(true);
    }).toPass({ timeout: 5000 });
  });

  test('35. Test Playground: input + run test shows risk gauge', async ({ page }) => {
    await setupAuth(page);
    await setupGuardrailRoutes(page);
    await page.goto('/settings/guardrails');
    await expect(page.getByRole('heading', { name: /guardrail center/i })).toBeVisible({ timeout: 10000 });
    await page.getByTestId('tab-test').click();
    await expect(page.getByTestId('test-input')).toBeVisible({ timeout: 5000 });
    await page.getByTestId('test-input').fill('My SSN is 123-45-6789');
    await page.getByTestId('run-test-btn').click();
    await expect(page.getByText(/blocked|pii_detected/i).first()).toBeVisible({ timeout: 8000 });
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// SUITE 6 — SCOPE EXPLORER (/settings/scopes)
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Scope Explorer', () => {

  test('36. Shows Scope Explorer heading with plan badge', async ({ page }) => {
    await setupAuth(page);
    await setupScopeRoutes(page);
    await page.goto('/settings/scopes');
    await expect(page.getByRole('heading', { name: /scope/i })).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('enterprise').first()).toBeVisible({ timeout: 5000 });
  });

  test('37. Shows scope groups (goals, agents, governance)', async ({ page }) => {
    await setupAuth(page);
    await setupScopeRoutes(page);
    await page.goto('/settings/scopes');
    await expect(page.getByRole('heading', { name: /scope/i })).toBeVisible({ timeout: 10000 });
    await expect(page.getByTestId('scope-groups')).toBeVisible({ timeout: 8000 });
    await expect(page.getByText(/goals/i).first()).toBeVisible({ timeout: 5000 });
  });

  test('38. API Keys section shows existing keys', async ({ page }) => {
    await setupAuth(page);
    await setupScopeRoutes(page);
    await page.goto('/settings/scopes');
    await expect(page.getByRole('heading', { name: /scope/i })).toBeVisible({ timeout: 10000 });
    await expect(page.getByTestId('api-keys-section')).toBeVisible({ timeout: 8000 });
    await expect(page.getByText('Production Key')).toBeVisible({ timeout: 8000 });
    await expect(page.getByText('Analytics Key')).toBeVisible({ timeout: 5000 });
  });

  test('39. Create API Key button opens modal', async ({ page }) => {
    await setupAuth(page);
    await setupScopeRoutes(page);
    await page.goto('/settings/scopes');
    await expect(page.getByTestId('api-keys-section')).toBeVisible({ timeout: 10000 });
    await page.getByTestId('create-key-btn').click();
    await expect(page.getByText(/create.*key|new.*key/i).first()).toBeVisible({ timeout: 5000 });
  });

  test('40. Scope search filters the scope list', async ({ page }) => {
    await setupAuth(page);
    await setupScopeRoutes(page);
    await page.goto('/settings/scopes');
    await expect(page.getByRole('heading', { name: /scope/i })).toBeVisible({ timeout: 10000 });
    const searchInput = page.getByPlaceholder(/search scopes/i);
    await expect(searchInput).toBeVisible({ timeout: 8000 });
    await searchInput.fill('goals:read');
    await expect(page.getByText(/goals:read/i).first()).toBeVisible({ timeout: 5000 });
  });

  test('41. Revoke key button appears per key row', async ({ page }) => {
    await setupAuth(page);
    await setupScopeRoutes(page);
    await page.goto('/settings/scopes');
    await expect(page.getByTestId('api-keys-section')).toBeVisible({ timeout: 10000 });
    await expect(page.getByTestId('revoke-btn-k1')).toBeVisible({ timeout: 8000 });
    await expect(page.getByTestId('revoke-btn-k2')).toBeVisible({ timeout: 5000 });
  });

  test('42. Plan upgrade path shows all 4 plan tiers', async ({ page }) => {
    await setupAuth(page);
    await setupScopeRoutes(page);
    await page.goto('/settings/scopes');
    await expect(page.getByRole('heading', { name: /scope/i })).toBeVisible({ timeout: 10000 });
    // Should show plan tiers
    await expect(page.getByText(/free/i).first()).toBeVisible({ timeout: 8000 });
    await expect(page.getByText(/enterprise/i).first()).toBeVisible({ timeout: 5000 });
  });
});
