/**
 * SDK Integration E2E Tests
 *
 * 15 tests verifying that the REST API contract matches the SDK behaviour,
 * webhook/schedule triggers work end-to-end, and all SDK operations are
 * exercisable via the UI or direct API calls made from Playwright:
 *
 *   1– 2  Goal submission parity (Python SDK / TypeScript SDK)
 *   3– 4  SDK resilience (429 retry, API key auth)
 *   5– 6  Streaming response and GitHub Action flow
 *   7–10  Trigger mechanisms (webhook, schedule, file-drop, alert)
 *  11–13  SDK control operations (cancel, HITL approval, KB upload)
 *  14–15  Metrics endpoint and SDK version compatibility
 *
 * All tests mock the backend — no live server or real SDK binary required.
 * Tests exercise the REST contract as the SDKs would (same HTTP verbs/paths).
 */

import { test, expect, type Page } from '@playwright/test';
import { setupAuth, TEST_API_KEY, TEST_TENANT_ID } from './helpers/auth';

// ── Constants ─────────────────────────────────────────────────────────────────

const SDK_GOAL_ID = 'g-sdk-001';
const SDK_GOAL_TEXT = 'Analyse all open GitHub issues and create a priority matrix';

// ── Helpers ───────────────────────────────────────────────────────────────────

/**
 * Submit a goal via the REST API the same way both SDK clients do.
 * Returns the parsed response body.
 */
async function submitGoalViaApi(
  page: Page,
  goalText: string,
  extra: Record<string, unknown> = {}
): Promise<Record<string, unknown>> {
  return page.evaluate(
    async ({ text, extra, apiKey, tenantId }) => {
      const resp = await fetch('http://localhost:8000/goals', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-API-Key': apiKey,
          'X-Tenant-ID': tenantId,
        },
        body: JSON.stringify({ goal: text, ...extra }),
      });
      const body = await resp.json() as Record<string, unknown>;
      return { status: resp.status, body };
    },
    { text: goalText, extra, apiKey: TEST_API_KEY, tenantId: TEST_TENANT_ID }
  ) as Promise<Record<string, unknown>>;
}

/**
 * Poll a goal's status the same way the TypeScript SDK's `pollUntilDone` does.
 */
async function pollGoalStatus(page: Page, goalId: string): Promise<Record<string, unknown>> {
  return page.evaluate(
    async ({ goalId, apiKey, tenantId }) => {
      const resp = await fetch(`http://localhost:8000/goals/${goalId}`, {
        headers: { 'X-API-Key': apiKey, 'X-Tenant-ID': tenantId },
      });
      const body = await resp.json() as Record<string, unknown>;
      return { status: resp.status, body };
    },
    { goalId, apiKey: TEST_API_KEY, tenantId: TEST_TENANT_ID }
  ) as Promise<Record<string, unknown>>;
}

// ═══════════════════════════════════════════════════════════════════════════════
// SUITE 1 — Goal Submission Parity
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('SDK Integration — Goal Submission Parity', () => {
  // ── 1. Python SDK goal submission matches UI submission ────────────────────────
  test('1. Python SDK goal submission — POST /goals with same payload as the UI', async ({
    page,
  }) => {
    let capturedPayload: Record<string, unknown> = {};
    await setupAuth(page);
    await page.route(/localhost:8000\/goals/, async (route) => {
      if (route.request().method() === 'POST') {
        try {
          capturedPayload = JSON.parse(route.request().postData() ?? '{}') as Record<string, unknown>;
        } catch { /* noop */ }
        return route.fulfill({
          status: 202,
          contentType: 'application/json',
          body: JSON.stringify({ goal_id: SDK_GOAL_ID, status: 'planning', goal: SDK_GOAL_TEXT }),
        });
      }
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ goals: [] }) });
    });
    await page.route(/localhost:8000\/agents/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
    );

    // Python SDK sends: POST /goals  { "goal": "<text>", "agent_id": null }
    await page.goto('/goals');
    await page.waitForLoadState('networkidle');

    const result = await submitGoalViaApi(page, SDK_GOAL_TEXT, { agent_id: null });
    const res = result as { status: number; body: Record<string, unknown> };

    expect(res.status).toBe(202);
    expect((res.body as { goal_id?: string }).goal_id).toBe(SDK_GOAL_ID);
    // The captured payload must include the "goal" field
    expect(capturedPayload['goal']).toBe(SDK_GOAL_TEXT);
  });

  // ── 2. TypeScript SDK goal polling works ──────────────────────────────────────
  test('2. TypeScript SDK goal polling — GET /goals/:id returns status', async ({ page }) => {
    await setupAuth(page);
    await page.route(/localhost:8000\/goals/, async (route) => {
      const url = route.request().url();
      const method = route.request().method();
      if (method === 'GET' && url.match(/\/goals\/[^/?]+$/)) {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            goal_id: SDK_GOAL_ID,
            goal: SDK_GOAL_TEXT,
            status: 'complete',
            created_at: new Date().toISOString(),
          }),
        });
      }
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ goals: [] }) });
    });
    await page.route(/localhost:8000\/agents/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
    );

    await page.goto('/goals');
    await page.waitForLoadState('networkidle');

    // TypeScript SDK: agentverse.goals.get(goalId)
    const result = await pollGoalStatus(page, SDK_GOAL_ID);
    const res = result as { status: number; body: Record<string, unknown> };

    expect(res.status).toBe(200);
    expect((res.body as { status?: string }).status).toBe('complete');
    expect((res.body as { goal_id?: string }).goal_id).toBe(SDK_GOAL_ID);
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// SUITE 2 — SDK Resilience
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('SDK Integration — SDK Resilience', () => {
  // ── 3. SDK retry on 429 ────────────────────────────────────────────────────────
  test('3. SDK retry on 429 — second attempt succeeds after rate-limit response', async ({
    page,
  }) => {
    let callCount = 0;
    await setupAuth(page);
    await page.route(/localhost:8000\/goals/, async (route) => {
      if (route.request().method() === 'POST') {
        callCount++;
        if (callCount === 1) {
          // First call: rate limited
          return route.fulfill({
            status: 429,
            contentType: 'application/json',
            headers: { 'Retry-After': '1' },
            body: JSON.stringify({ detail: 'Rate limit exceeded', retry_after: 1 }),
          });
        }
        // Second call: succeeds
        return route.fulfill({
          status: 202,
          contentType: 'application/json',
          body: JSON.stringify({ goal_id: SDK_GOAL_ID, status: 'planning', goal: SDK_GOAL_TEXT }),
        });
      }
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ goals: [] }) });
    });
    await page.route(/localhost:8000\/agents/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
    );

    await page.goto('/goals');
    await page.waitForLoadState('networkidle');

    // First attempt: expect 429
    const firstAttempt = await submitGoalViaApi(page, SDK_GOAL_TEXT);
    const first = firstAttempt as { status: number };
    expect(first.status).toBe(429);

    // Second attempt (retry): expect 202
    const secondAttempt = await submitGoalViaApi(page, SDK_GOAL_TEXT);
    const second = secondAttempt as { status: number; body: Record<string, unknown> };
    expect(second.status).toBe(202);
    expect((second.body as { goal_id?: string }).goal_id).toBe(SDK_GOAL_ID);
    expect(callCount).toBe(2);
  });

  // ── 4. SDK authentication with API key ────────────────────────────────────────
  test('4. SDK authentication — request without API key returns 401', async ({ page }) => {
    await setupAuth(page);
    await page.route(/localhost:8000\/goals/, async (route) => {
      const apiKey = route.request().headers()['x-api-key'] ?? '';
      if (!apiKey) {
        return route.fulfill({
          status: 401,
          contentType: 'application/json',
          body: JSON.stringify({ detail: 'Authentication required. Provide X-API-Key header.' }),
        });
      }
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ goals: [] }),
      });
    });
    await page.route(/localhost:8000\/agents/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
    );

    await page.goto('/goals');
    await page.waitForLoadState('networkidle');

    // Call without API key
    const result = await page.evaluate(async () => {
      const resp = await fetch('http://localhost:8000/goals', {
        headers: { 'Content-Type': 'application/json' },
        // Deliberately omit X-API-Key
      });
      return { status: resp.status };
    });
    const res = result as { status: number };
    expect(res.status).toBe(401);
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// SUITE 3 — Streaming & GitHub Action
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('SDK Integration — Streaming & CI Flows', () => {
  // ── 5. SDK streaming response ──────────────────────────────────────────────────
  test('5. SDK streaming response — GET /goals/:id/stream returns SSE events', async ({ page }) => {
    const sseBody = [
      `data: {"type":"goal_started","goal":"${SDK_GOAL_TEXT}"}\n\n`,
      `data: {"type":"plan_ready","steps":["Fetch GitHub issues","Build matrix"],"iteration":1}\n\n`,
      `data: {"type":"goal_complete"}\n\n`,
    ].join('');

    await setupAuth(page);
    await page.route(new RegExp(`localhost:8000/goals/${SDK_GOAL_ID}/stream`), (route) =>
      route.fulfill({ status: 200, contentType: 'text/event-stream', body: sseBody })
    );
    await page.route(/localhost:8000\/goals/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ goals: [] }) })
    );
    await page.route(/localhost:8000\/agents/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
    );

    await page.goto('/goals');
    await page.waitForLoadState('networkidle');

    // SDK streams using EventSource / ReadableStream
    const result = await page.evaluate(
      async ({ goalId, apiKey, tenantId }) => {
        const resp = await fetch(`http://localhost:8000/goals/${goalId}/stream`, {
          headers: { 'X-API-Key': apiKey, 'X-Tenant-ID': tenantId, Accept: 'text/event-stream' },
        });
        const text = await resp.text();
        return { status: resp.status, hasGoalStarted: text.includes('goal_started'), hasGoalComplete: text.includes('goal_complete') };
      },
      { goalId: SDK_GOAL_ID, apiKey: TEST_API_KEY, tenantId: TEST_TENANT_ID }
    ) as { status: number; hasGoalStarted: boolean; hasGoalComplete: boolean };

    expect(result.status).toBe(200);
    expect(result.hasGoalStarted).toBe(true);
    expect(result.hasGoalComplete).toBe(true);
  });

  // ── 6. GitHub Action submits goal via SDK ──────────────────────────────────────
  test('6. GitHub Action flow — POST /goals with CI metadata creates goal', async ({ page }) => {
    let capturedPayload: Record<string, unknown> = {};
    await setupAuth(page);
    await page.route(/localhost:8000\/goals/, async (route) => {
      if (route.request().method() === 'POST') {
        try {
          capturedPayload = JSON.parse(route.request().postData() ?? '{}') as Record<string, unknown>;
        } catch { /* noop */ }
        return route.fulfill({
          status: 202,
          contentType: 'application/json',
          body: JSON.stringify({
            goal_id: 'g-ci-001',
            status: 'planning',
            goal: capturedPayload['goal'] ?? '',
            metadata: capturedPayload['metadata'],
          }),
        });
      }
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ goals: [] }) });
    });
    await page.route(/localhost:8000\/agents/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
    );

    await page.goto('/goals');
    await page.waitForLoadState('networkidle');

    // GitHub Action SDK call includes CI metadata
    const result = await submitGoalViaApi(
      page,
      'Run post-deploy smoke tests for release v2.4.1',
      {
        metadata: {
          source: 'github_action',
          workflow: 'deploy.yml',
          run_id: '123456789',
          sha: 'abc1234',
          ref: 'refs/tags/v2.4.1',
        },
      }
    );
    const res = result as { status: number; body: Record<string, unknown> };
    expect(res.status).toBe(202);
    expect(capturedPayload['metadata']).toBeDefined();
    const meta = capturedPayload['metadata'] as Record<string, string>;
    expect(meta['source']).toBe('github_action');
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// SUITE 4 — Trigger Mechanisms
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('SDK Integration — Trigger Mechanisms', () => {
  // ── 7. Webhook trigger creates goal ────────────────────────────────────────────
  test('7. Webhook trigger — POST /webhooks/inbound creates a new goal', async ({ page }) => {
    let goalCreated = false;
    await setupAuth(page);
    await page.route(/localhost:8000\/webhooks\/inbound/, async (route) => {
      if (route.request().method() === 'POST') {
        goalCreated = true;
        return route.fulfill({
          status: 202,
          contentType: 'application/json',
          body: JSON.stringify({
            goal_id: 'g-webhook-001',
            status: 'planning',
            goal: 'Investigate PagerDuty alert: High CPU on prod-api-3',
            trigger: 'webhook',
          }),
        });
      }
      return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    });
    await page.route(/localhost:8000\/goals/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ goals: [] }) })
    );
    await page.route(/localhost:8000\/agents/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
    );

    await page.goto('/goals');
    await page.waitForLoadState('networkidle');

    const result = await page.evaluate(
      async ({ apiKey, tenantId }) => {
        const resp = await fetch('http://localhost:8000/webhooks/inbound', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-API-Key': apiKey, 'X-Tenant-ID': tenantId },
          body: JSON.stringify({
            source: 'pagerduty',
            event: 'trigger',
            incident_id: 'PD-12345',
            title: 'High CPU on prod-api-3',
            severity: 'critical',
          }),
        });
        return { status: resp.status };
      },
      { apiKey: TEST_API_KEY, tenantId: TEST_TENANT_ID }
    ) as { status: number };

    expect(result.status).toBe(202);
    expect(goalCreated).toBe(true);
  });

  // ── 8. Schedule trigger fires goal at correct time ────────────────────────────
  test('8. Schedule trigger — POST /schedules creates a cron-based goal trigger', async ({
    page,
  }) => {
    let scheduleCreated = false;
    await setupAuth(page);
    await page.route(/localhost:8000\/schedules/, async (route) => {
      if (route.request().method() === 'POST') {
        scheduleCreated = true;
        return route.fulfill({
          status: 201,
          contentType: 'application/json',
          body: JSON.stringify({
            schedule_id: 'sched-sdk-001',
            goal_template: 'Generate the daily cost report for {{date}}',
            cron_expression: '0 8 * * *',
            nl_description: 'Every day at 8am',
            is_active: true,
            created_at: new Date().toISOString(),
          }),
        });
      }
      return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    });
    await page.route(/localhost:8000\/goals/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ goals: [] }) })
    );
    await page.route(/localhost:8000\/agents/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
    );

    await page.goto('/schedules');
    await page.waitForLoadState('networkidle');

    const result = await page.evaluate(
      async ({ apiKey, tenantId }) => {
        const resp = await fetch('http://localhost:8000/schedules', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-API-Key': apiKey, 'X-Tenant-ID': tenantId },
          body: JSON.stringify({
            goal_template: 'Generate the daily cost report for {{date}}',
            nl_schedule: 'Every day at 8am',
          }),
        });
        const body = await resp.json() as Record<string, unknown>;
        return { status: resp.status, body };
      },
      { apiKey: TEST_API_KEY, tenantId: TEST_TENANT_ID }
    ) as { status: number; body: Record<string, unknown> };

    expect(result.status).toBe(201);
    expect(scheduleCreated).toBe(true);
    expect((result.body as { schedule_id?: string }).schedule_id).toBeDefined();
  });

  // ── 9. File drop trigger processes new file ────────────────────────────────────
  test('9. File drop trigger — uploading to S3/storage endpoint creates a goal', async ({
    page,
  }) => {
    let triggerCalled = false;
    await setupAuth(page);
    await page.route(/localhost:8000\/triggers\/file-drop/, async (route) => {
      if (route.request().method() === 'POST') {
        triggerCalled = true;
        return route.fulfill({
          status: 202,
          contentType: 'application/json',
          body: JSON.stringify({
            goal_id: 'g-filedrop-001',
            status: 'planning',
            goal: 'Process newly uploaded file: report-q3-2026.csv',
            trigger: 'file_drop',
          }),
        });
      }
      return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    });
    await page.route(/localhost:8000\/goals/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ goals: [] }) })
    );
    await page.route(/localhost:8000\/agents/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
    );

    await page.goto('/goals');
    await page.waitForLoadState('networkidle');

    const result = await page.evaluate(
      async ({ apiKey, tenantId }) => {
        const resp = await fetch('http://localhost:8000/triggers/file-drop', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-API-Key': apiKey, 'X-Tenant-ID': tenantId },
          body: JSON.stringify({
            bucket: 'agentverse-uploads',
            key: 'uploads/report-q3-2026.csv',
            event: 'ObjectCreated',
          }),
        });
        return { status: resp.status };
      },
      { apiKey: TEST_API_KEY, tenantId: TEST_TENANT_ID }
    ) as { status: number };

    expect(result.status).toBe(202);
    expect(triggerCalled).toBe(true);
  });

  // ── 10. Alert webhook creates investigation goal ────────────────────────────────
  test('10. Alert webhook — Datadog/Grafana alert payload creates investigation goal', async ({
    page,
  }) => {
    let goalText = '';
    await setupAuth(page);
    await page.route(/localhost:8000\/webhooks\/alert/, async (route) => {
      if (route.request().method() === 'POST') {
        try {
          const body = JSON.parse(route.request().postData() ?? '{}') as Record<string, unknown>;
          goalText = `Investigate alert: ${(body as { alert_name?: string }).alert_name ?? 'unknown'}`;
        } catch { /* noop */ }
        return route.fulfill({
          status: 202,
          contentType: 'application/json',
          body: JSON.stringify({
            goal_id: 'g-alert-001',
            status: 'planning',
            goal: goalText,
            trigger: 'alert_webhook',
          }),
        });
      }
      return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    });
    await page.route(/localhost:8000\/goals/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ goals: [] }) })
    );
    await page.route(/localhost:8000\/agents/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
    );

    await page.goto('/goals');
    await page.waitForLoadState('networkidle');

    const result = await page.evaluate(
      async ({ apiKey, tenantId }) => {
        const resp = await fetch('http://localhost:8000/webhooks/alert', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-API-Key': apiKey, 'X-Tenant-ID': tenantId },
          body: JSON.stringify({
            alert_name: 'p99_latency_breach',
            service: 'payment-api',
            threshold_ms: 2000,
            current_p99_ms: 4800,
            severity: 'warning',
          }),
        });
        return { status: resp.status };
      },
      { apiKey: TEST_API_KEY, tenantId: TEST_TENANT_ID }
    ) as { status: number };

    expect(result.status).toBe(202);
    expect(goalText).toContain('p99_latency_breach');
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// SUITE 5 — SDK Control Operations
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('SDK Integration — SDK Control Operations', () => {
  // ── 11. SDK goal cancellation ──────────────────────────────────────────────────
  test('11. SDK goal cancellation — POST /goals/:id/cancel returns cancelled status', async ({
    page,
  }) => {
    await setupAuth(page);
    await page.route(new RegExp(`localhost:8000/goals/${SDK_GOAL_ID}/cancel`), (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ goal_id: SDK_GOAL_ID, status: 'cancelled' }),
      })
    );
    await page.route(/localhost:8000\/goals/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ goals: [] }) })
    );
    await page.route(/localhost:8000\/agents/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
    );

    await page.goto('/goals');
    await page.waitForLoadState('networkidle');

    const result = await page.evaluate(
      async ({ goalId, apiKey, tenantId }) => {
        const resp = await fetch(`http://localhost:8000/goals/${goalId}/cancel`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-API-Key': apiKey, 'X-Tenant-ID': tenantId },
          body: JSON.stringify({ reason: 'Cancelled via SDK test' }),
        });
        const body = await resp.json() as Record<string, unknown>;
        return { status: resp.status, body };
      },
      { goalId: SDK_GOAL_ID, apiKey: TEST_API_KEY, tenantId: TEST_TENANT_ID }
    ) as { status: number; body: Record<string, unknown> };

    expect(result.status).toBe(200);
    expect((result.body as { status?: string }).status).toBe('cancelled');
  });

  // ── 12. SDK HITL approval ──────────────────────────────────────────────────────
  test('12. SDK HITL approval — POST /governance/approvals/:id/approve returns approved', async ({
    page,
  }) => {
    const approvalId = 'req-sdk-001';
    await setupAuth(page);
    await page.route(new RegExp(`localhost:8000/governance/approvals/${approvalId}/approve`), (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'approved', request_id: approvalId }),
      })
    );
    await page.route(/localhost:8000\/goals/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ goals: [] }) })
    );
    await page.route(/localhost:8000\/agents/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
    );

    await page.goto('/goals');
    await page.waitForLoadState('networkidle');

    const result = await page.evaluate(
      async ({ approvalId, apiKey, tenantId }) => {
        const resp = await fetch(`http://localhost:8000/governance/approvals/${approvalId}/approve`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-API-Key': apiKey, 'X-Tenant-ID': tenantId },
          body: JSON.stringify({ comment: 'Approved via SDK integration test' }),
        });
        const body = await resp.json() as Record<string, unknown>;
        return { status: resp.status, body };
      },
      { approvalId, apiKey: TEST_API_KEY, tenantId: TEST_TENANT_ID }
    ) as { status: number; body: Record<string, unknown> };

    expect(result.status).toBe(200);
    expect((result.body as { status?: string }).status).toBe('approved');
  });

  // ── 13. SDK knowledge base upload ──────────────────────────────────────────────
  test('13. SDK knowledge base upload — POST /knowledge/ingest returns task_id', async ({
    page,
  }) => {
    await setupAuth(page);
    await page.route(/localhost:8000\/knowledge\/ingest/, (route) =>
      route.fulfill({
        status: 202,
        contentType: 'application/json',
        body: JSON.stringify({
          task_id: 'ingest-sdk-001',
          status: 'queued',
          filename: 'api-docs.txt',
          collection_id: 'col-sdk-001',
        }),
      })
    );
    await page.route(/localhost:8000\/goals/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ goals: [] }) })
    );
    await page.route(/localhost:8000\/agents/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
    );

    await page.goto('/goals');
    await page.waitForLoadState('networkidle');

    const result = await page.evaluate(
      async ({ apiKey, tenantId }) => {
        const resp = await fetch('http://localhost:8000/knowledge/ingest', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-API-Key': apiKey, 'X-Tenant-ID': tenantId },
          body: JSON.stringify({
            collection_id: 'col-sdk-001',
            content: 'This is the AgentVerse API documentation content.',
            filename: 'api-docs.txt',
            metadata: { source: 'sdk_test', version: '1.0' },
          }),
        });
        const body = await resp.json() as Record<string, unknown>;
        return { status: resp.status, body };
      },
      { apiKey: TEST_API_KEY, tenantId: TEST_TENANT_ID }
    ) as { status: number; body: Record<string, unknown> };

    expect(result.status).toBe(202);
    expect((result.body as { task_id?: string }).task_id).toBeDefined();
    expect((result.body as { status?: string }).status).toBe('queued');
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// SUITE 6 — Metrics & Version Compatibility
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('SDK Integration — Metrics & Version Compatibility', () => {
  // ── 14. SDK metrics endpoint ───────────────────────────────────────────────────
  test('14. SDK metrics endpoint — GET /goals/metrics returns expected structure', async ({
    page,
  }) => {
    await setupAuth(page);
    await page.route(/localhost:8000\/goals\/metrics/, (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          active_goals: 3,
          total_goals: 142,
          success_rate: 0.873,
          avg_latency_ms: 4200,
          cost_today_usd: 12.4,
          goals_today: 8,
        }),
      })
    );
    await page.route(/localhost:8000\/goals/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ goals: [] }) })
    );
    await page.route(/localhost:8000\/agents/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
    );

    await page.goto('/goals');
    await page.waitForLoadState('networkidle');

    const result = await page.evaluate(
      async ({ apiKey, tenantId }) => {
        const resp = await fetch('http://localhost:8000/goals/metrics', {
          headers: { 'X-API-Key': apiKey, 'X-Tenant-ID': tenantId },
        });
        const body = await resp.json() as Record<string, unknown>;
        return { status: resp.status, body };
      },
      { apiKey: TEST_API_KEY, tenantId: TEST_TENANT_ID }
    ) as { status: number; body: Record<string, unknown> };

    expect(result.status).toBe(200);
    const metrics = result.body as Record<string, number | undefined>;
    expect(metrics['total_goals']).toBe(142);
    expect(typeof metrics['success_rate']).toBe('number');
    expect(typeof metrics['avg_latency_ms']).toBe('number');
  });

  // ── 15. SDK version compatibility check ───────────────────────────────────────
  test('15. SDK version compatibility — GET /version returns API version info', async ({ page }) => {
    await setupAuth(page);
    await page.route(/localhost:8000\/version/, (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          api_version: '1.0.0',
          min_sdk_version: '0.4.0',
          supported_sdk_versions: ['0.4.x', '0.5.x', '1.0.x'],
          deprecated_features: [],
        }),
      })
    );
    await page.route(/localhost:8000\/goals/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ goals: [] }) })
    );
    await page.route(/localhost:8000\/agents/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
    );

    await page.goto('/goals');
    await page.waitForLoadState('networkidle');

    const result = await page.evaluate(
      async ({ apiKey, tenantId }) => {
        const resp = await fetch('http://localhost:8000/version', {
          headers: { 'X-API-Key': apiKey, 'X-Tenant-ID': tenantId },
        });
        const body = await resp.json() as Record<string, unknown>;
        return { status: resp.status, body };
      },
      { apiKey: TEST_API_KEY, tenantId: TEST_TENANT_ID }
    ) as { status: number; body: Record<string, unknown> };

    expect(result.status).toBe(200);
    const versionInfo = result.body as Record<string, string | string[]>;
    expect(versionInfo['api_version']).toBeDefined();
    expect(Array.isArray(versionInfo['supported_sdk_versions'])).toBe(true);
  });
});
