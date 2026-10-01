/**
 * REAL-WORLD UI scenarios against the LIVE local Docker stack (no mocking).
 *
 * Skipped unless REAL_WORLD=1. Run via scripts/run_real_world.sh or:
 *   REAL_WORLD=1 AGENTVERSE_TENANT_FILE=/path/tenant.json \
 *     npx playwright test --config=playwright.real-world.config.ts
 *
 * Signs in the way the existing real-e2e specs do (API key injected into the
 * persisted auth store). The key comes from AGENTVERSE_API_KEY or the JSON file
 * in AGENTVERSE_TENANT_FILE and is never printed.
 */
import { readFileSync } from 'node:fs';

import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

const API = process.env.API_BASE_URL ?? 'http://localhost:8000';
const ENABLED = process.env.REAL_WORLD === '1';

function apiKey(): string {
  if (process.env.AGENTVERSE_API_KEY) return process.env.AGENTVERSE_API_KEY;
  const file = process.env.AGENTVERSE_TENANT_FILE;
  if (file) return String(JSON.parse(readFileSync(file, 'utf-8')).api_key ?? '');
  return '';
}

const KEY = ENABLED ? apiKey() : '';
const H = { 'X-API-Key': KEY };
const tag = () => Math.random().toString(36).slice(2, 10);

test.skip(!ENABLED, 'real-world suite: set REAL_WORLD=1 (live stack)');

function workflowYaml(name: string): string {
  return `name: ${name}
description: Draft the weekly engineering report, get manager sign-off, then publish a summary.
trigger:
  type: api
inputs:
  team: {type: string, required: false, default: Payments Platform}
steps:
  - id: draft_report
    name: Draft weekly report
    type: llm
    json_output: true
    max_tokens: 500
    timeout: 180s
    on_failure: abort
    prompt: >-
      Write the weekly status report for the {{inputs.team}} team. Return ONLY a JSON object
      with keys "title" and "summary".
  - id: manager_approval
    name: Engineering manager sign-off
    type: hitl
    depends_on: [draft_report]
    timeout: 24h
    context:
      - {label: Report title, value: "{{steps.draft_report.output.title}}", display_type: text}
    actions:
      - {id: approve, label: Publish, style: success}
      - {id: reject, label: Send back, style: danger}
  - id: publish_summary
    name: Publish summary
    type: code
    runtime: python
    depends_on: [manager_approval]
    input: {title: "{{steps.draft_report.output.title}}"}
    code: |
      output = {"published": True, "headline": str(inputs.get("title") or "")[:120]}
`;
}

async function login(page: Page, tenantId: string): Promise<void> {
  await page.addInitScript(
    ({ key, tid }) => {
      const auth = JSON.stringify({
        state: { apiKey: key, tenantId: tid, plan: 'free', isAuthenticated: true },
        version: 0,
      });
      for (const store of [localStorage, sessionStorage]) {
        store.setItem('av-auth', auth);
        store.setItem('av_api_key', key);
        store.setItem('av_tenant_id', tid);
      }
    },
    { key: KEY, tid: tenantId },
  );
}

async function json<T = Record<string, unknown>>(
  req: APIRequestContext, method: 'get' | 'post' | 'delete', path: string,
  opts: Record<string, unknown> = {},
): Promise<T> {
  // The free test tenant has a per-minute request budget; wait out a 429.
  let resp = await req[method](`${API}${path}`, { headers: H, ...opts });
  for (let attempt = 1; resp.status() === 429 && attempt <= 8; attempt++) {
    await new Promise((r) => setTimeout(r, Math.min(5_000 * attempt, 30_000)));
    resp = await req[method](`${API}${path}`, { headers: H, ...opts });
  }
  expect(resp.status(), `${method.toUpperCase()} ${path}`).toBeLessThan(300);
  const text = await resp.text();
  return (text ? JSON.parse(text) : {}) as T;
}

async function waitFor<T>(fn: () => Promise<T | null | undefined>, ms: number, what: string): Promise<T> {
  const until = Date.now() + ms;
  for (;;) {
    const v = await fn();
    if (v) return v;
    if (Date.now() > until) throw new Error(`timed out waiting for ${what}`);
    await new Promise((r) => setTimeout(r, 3000));
  }
}

test.describe('real-world UI', () => {
  let tenantId = '';

  test.beforeAll(async ({ request }) => {
    expect(KEY, 'AGENTVERSE_API_KEY / AGENTVERSE_TENANT_FILE').not.toBe('');
    tenantId = (await json<{ tenant_id: string }>(request, 'get', '/tenants/me')).tenant_id;
  });

  test('UI-APPROVALS-LIVE: pending workflow approval appears live, approve, run finishes', async ({ page, request }) => {
    test.setTimeout(420_000);
    const name = `rw-ui-weekly-${tag()}`;
    const wf = await json<{ id: string }>(request, 'post', '/api/v1/workflows/import-yaml', {
      data: workflowYaml(name), headers: { ...H, 'Content-Type': 'application/x-yaml' },
    });
    try {
      await login(page, tenantId);
      // Open the Approvals page BEFORE the run reaches the gate: it must show up live.
      await page.goto('/approvals', { waitUntil: 'domcontentloaded' });
      await expect(page.getByRole('heading', { name: /approval/i }).first()).toBeVisible();

      const { run_id: runId } = await json<{ run_id: string }>(
        request, 'post', `/api/v1/workflows/${wf.id}/trigger`, { data: { inputs: {} } });
      await waitFor(async () => {
        const run = await json<{ status: string }>(request, 'get', `/api/v1/runs/${runId}`);
        if (['failed', 'cancelled', 'complete'].includes(run.status)) {
          throw new Error(`run ended ${run.status} before the gate`);
        }
        return run.status === 'waiting_hitl' ? run : null;
      }, 300_000, 'run to park at the approval gate');

      // /approvals is the product's approvals inbox: the workflow gate must appear
      // there without a manual reload (SSE / polling).
      await expect.soft(
        page.getByText(new RegExp(`${name}|Engineering manager sign-off`)).first(),
        '/approvals shows the pending WORKFLOW approval live',
      ).toBeVisible({ timeout: 45_000 });

      // The workflow approval inbox: approve from the card for this run.
      await page.goto('/workflows/approvals', { waitUntil: 'domcontentloaded' });
      const card = page.getByRole('article', { name: `Approval request for run ${runId}` });
      await expect(card, 'workflow approval inbox lists the run').toBeVisible({ timeout: 45_000 });
      await card.getByRole('button', { name: /^(Publish|Approve) request$/ }).click();

      // Open the run view while the approved run is still finishing.
      await page.goto(`/workflows/${wf.id}/runs/${runId}`, { waitUntil: 'domcontentloaded' });
      const finished = await waitFor(async () => {
        const run = await json<{ status: string }>(request, 'get', `/api/v1/runs/${runId}`);
        return ['complete', 'failed', 'cancelled'].includes(run.status) ? run : null;
      }, 180_000, 'run to finish after the UI approval');
      expect(finished.status, 'approved run completes').toBe('complete');
      // The run header (first status badge) must show the run finishing.
      await expect(page.getByText('Run Detail').locator('xpath=..').getByText(/^complete$/),
        'run view header shows complete').toBeVisible({ timeout: 30_000 });
      // The step timeline must catch up without a manual reload...
      await expect.soft(
        page.getByLabel(/Step publish_summary — complete/),
        'run view step timeline updates to publish_summary complete (no reload)',
      ).toBeVisible({ timeout: 20_000 });
      await expect.soft(
        page.getByLabel(/Step manager_approval — waiting_hitl/),
        'run view shows no stale waiting_hitl row for the decided gate',
      ).toHaveCount(0);
      // The run really finished (source of truth: the API).
      const steps = await json<Array<{ step_id: string; status: string }>>(
        request, 'get', `/api/v1/runs/${runId}/steps`);
      expect(steps.filter((s) => s.step_id === 'publish_summary').map((s) => s.status))
        .toContain('complete');
    } finally {
      await request.delete(`${API}/api/v1/workflows/${wf.id}`, { headers: H });
    }
  });

  test('UI-KB-DOCS: knowledge page lists uploaded documents as indexed', async ({ page, request }) => {
    test.setTimeout(180_000);
    const col = await json<{ collection_id: string }>(request, 'post', '/knowledge/collections', {
      data: { name: `rw-ui-kb-${tag()}`, embedder_type: 'default' },
    });
    const files = [
      { name: 'rw-travel-policy.md', mimeType: 'text/markdown',
        buffer: Buffer.from('# Travel\n\nPune meal per diem is INR 1,850 (policy Nightjar-4).\n') },
      { name: 'rw-service-owners.csv', mimeType: 'text/csv',
        buffer: Buffer.from('service,owner\nledger-sync,Team Ibex\n') },
      { name: 'rw-handbook.html', mimeType: 'text/html',
        buffer: Buffer.from('<h1>Wi-Fi</h1><p>Guest network Tamarind-Guest.</p>') },
    ];
    try {
      for (const f of files) {
        const up = await json<{ chunks_created: number }>(request, 'post', '/knowledge/ingest/file', {
          multipart: { collection_id: col.collection_id, file: f },
        });
        expect(up.chunks_created, `${f.name} chunks`).toBeGreaterThan(0);
      }
      await login(page, tenantId);
      await page.goto('/knowledge', { waitUntil: 'domcontentloaded' });
      await page.getByTestId('tab-documents').click();
      await page.locator('select').first().selectOption(col.collection_id);
      for (const f of files) {
        const row = page.getByText(f.name, { exact: true });
        await expect(row, `KB documents tab lists ${f.name}`).toBeVisible({ timeout: 30_000 });
      }
      // "ready": every row reports a non-zero chunk count (the page has no status badge).
      await expect(page.getByText(/^[1-9]\d* chunks$/)).toHaveCount(files.length);
    } finally {
      await request.delete(`${API}/knowledge/collections/${col.collection_id}`, { headers: H });
    }
  });
});
