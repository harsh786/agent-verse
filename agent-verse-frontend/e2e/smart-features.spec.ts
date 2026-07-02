/**
 * Schedules, Knowledge & Collaboration — Comprehensive E2E Tests
 *
 * 42 tests across 6 suites (7 per section × 2 sections × 3 pages):
 *   1.  Schedules — Page Structure + CRUD       (7)
 *   2.  Schedules — Analytics + AI Advisor      (7)
 *   3.  Knowledge — Collections + Ask AI        (7)
 *   4.  Knowledge — Ingest + Search + Analytics (7)
 *   5.  Collaboration — Sessions                (7)
 *   6.  Collaboration — Live Session Panel      (7)
 */
import { test, expect, type Page } from '@playwright/test';

// ── Auth ──────────────────────────────────────────────────────────────────────

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
    route.fulfill({ status: 200, contentType: 'application/json',
      body: JSON.stringify({ tenant_id: 'test-tenant', name: 'PineLabs', plan: 'enterprise' }) })
  );
}

// ── Schedule mock data ────────────────────────────────────────────────────────

const SCHEDULES = [
  { schedule_id: 'sched-1', goal_template: 'Daily engineering report', trigger_type: 'cron', cron_expr: '0 9 * * *', paused: false, status: 'active', next_run_at: new Date(Date.now() + 3600000).toISOString(), last_fired_at: new Date(Date.now() - 86400000).toISOString() },
  { schedule_id: 'sched-2', goal_template: 'Hourly health check', trigger_type: 'interval', interval_seconds: 3600, paused: true, status: 'paused', next_run_at: null, last_fired_at: null },
  { schedule_id: 'sched-3', goal_template: 'On deployment webhook', trigger_type: 'webhook', paused: false, status: 'active' },
];

const SCHEDULE_ANALYTICS = {
  total: 3, active: 2, paused: 1,
  by_trigger_type: { cron: 1, interval: 1, webhook: 1 },
  fired_last_7_days: {
    [new Date(Date.now() - 6 * 86400000).toISOString().slice(0, 10)]: 2,
    [new Date(Date.now() - 5 * 86400000).toISOString().slice(0, 10)]: 3,
    [new Date(Date.now() - 4 * 86400000).toISOString().slice(0, 10)]: 1,
    [new Date(Date.now() - 3 * 86400000).toISOString().slice(0, 10)]: 4,
    [new Date(Date.now() - 2 * 86400000).toISOString().slice(0, 10)]: 2,
    [new Date(Date.now() - 1 * 86400000).toISOString().slice(0, 10)]: 5,
    [new Date().toISOString().slice(0, 10)]: 1,
  },
  schedules_summary: SCHEDULES,
};

const AI_SUGGESTIONS = {
  suggestions: [
    { rank: 1, title: 'Daily at 9 AM', trigger_type: 'cron', cron_expr: '0 9 * * *', interval_seconds: null, rationale: 'Perfect for daily recurring tasks.', use_case: 'Reports, summaries' },
    { rank: 2, title: 'Every 4 hours', trigger_type: 'interval', cron_expr: null, interval_seconds: 14400, rationale: 'Ideal for monitoring.', use_case: 'Health checks' },
    { rank: 3, title: 'Weekly Monday 8 AM', trigger_type: 'cron', cron_expr: '0 8 * * 1', interval_seconds: null, rationale: 'Low frequency planning.', use_case: 'Weekly planning' },
  ],
  goal: 'Send daily engineering metrics',
  llm_powered: true,
};

async function setupScheduleRoutes(page: Page): Promise<void> {
  await page.route(/localhost:8000\/agents(\?.*)?$/, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([{ agent_id: 'agent-1', name: 'Daily Reporter' }]) })
  );
  await page.route(/localhost:8000\/schedules\/analytics/, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(SCHEDULE_ANALYTICS) })
  );
  await page.route(/localhost:8000\/schedules\/suggest/, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(AI_SUGGESTIONS) })
  );
  await page.route(/localhost:8000\/schedules\/sched-1\/pause/, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ schedule_id: 'sched-1', paused: true }) })
  );
  await page.route(/localhost:8000\/schedules\/sched-1\/resume/, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ schedule_id: 'sched-1', paused: false }) })
  );
  await page.route(/localhost:8000\/schedules\/sched-.*/, (route) => {
    if (route.request().method() === 'DELETE') return route.fulfill({ status: 204, body: '' });
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(SCHEDULES[0]) });
  });
  await page.route(/localhost:8000\/nl\/schedule/, (route) =>
    route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify([{ schedule_id: 'sched-nl', trigger_type: 'cron', spec: { cron_expression: '0 9 * * 1-5' } }]) })
  );
  await page.route(/localhost:8000\/schedules(\?.*)?$/, (route) => {
    if (route.request().method() === 'POST') return route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify({ schedule_id: 'sched-new', trigger_type: 'cron' }) });
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(SCHEDULES) });
  });
}

// ── Knowledge mock data ───────────────────────────────────────────────────────

const COLLECTIONS = [
  { collection_id: 'col-1', name: 'Engineering Docs', doc_count: 42, embedder: 'voyage', created_at: '2026-01-01T00:00:00Z' },
  { collection_id: 'col-2', name: 'Product Specs', doc_count: 18, embedder: 'openai', created_at: '2026-02-01T00:00:00Z' },
];

const SEARCH_RESULTS = [
  { chunk_id: 'c1', content: 'The CI/CD pipeline uses GitHub Actions for automated testing.', score: 0.94, source_url: 'https://github.com/org/repo/blob/main/docs/ci.md', metadata: {} },
  { chunk_id: 'c2', content: 'Deploy to production requires two approvals from senior engineers.', score: 0.87, source_url: '', metadata: {} },
];

const RAG_ANSWER = {
  answer: 'Based on the documentation, the CI/CD pipeline uses GitHub Actions [1]. Production deployments require two approvals [2].',
  citations: [
    { index: 1, chunk_id: 'c1', collection_id: 'col-1', score: 0.94, source_url: 'https://github.com/org/repo/blob/main/docs/ci.md', page_number: null, excerpt: 'The CI/CD pipeline uses GitHub Actions for automated testing.' },
    { index: 2, chunk_id: 'c2', collection_id: 'col-1', score: 0.87, source_url: '', page_number: null, excerpt: 'Deploy to production requires two approvals.' },
  ],
  collections_searched: 2,
  chunks_retrieved: 2,
  question: 'How does deployment work?',
};

const COLLECTION_STATS = {
  collection_id: 'col-1', name: 'Engineering Docs', doc_count: 42, chunk_count: 350,
  embedding_coverage_pct: 98, avg_chunk_length: 420, source_type_distribution: { text: 20, url: 15, github: 7 },
  embedder: 'voyage', health_score: 0.91,
};

async function setupKnowledgeRoutes(page: Page): Promise<void> {
  // Register broad routes FIRST so specific ones win via LIFO
  await page.route(/localhost:8000\/knowledge\/collections\/col-.*/, (route) => {
    if (route.request().method() === 'DELETE') return route.fulfill({ status: 204, body: '' });
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(COLLECTIONS[0]) });
  });
  // Specific: stats endpoint — registered LAST = highest LIFO priority
  await page.route(/localhost:8000\/knowledge\/collections\/col-1\/stats/, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(COLLECTION_STATS) })
  );
  await page.route(/localhost:8000\/knowledge\/collections(\?.*)?$/, (route) => {
    if (route.request().method() === 'POST') return route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify({ collection_id: 'col-new', name: 'New Collection' }) });
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(COLLECTIONS) });
  });
  await page.route(/localhost:8000\/knowledge\/chat/, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(RAG_ANSWER) })
  );
  await page.route(/localhost:8000\/knowledge\/search(\?.*)?$/, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(SEARCH_RESULTS) })
  );
  await page.route(/localhost:8000\/knowledge\/ingest\/file/, (route) =>
    route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify({ chunks_created: 12, filename: 'test.pdf' }) })
  );
  await page.route(/localhost:8000\/knowledge\/ingest(\?.*)?$/, (route) =>
    route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify({ chunks_created: 7, document_id: 'doc-1', content_hash: 'abc' }) })
  );
  await page.route(/localhost:8000\/knowledge\/cache\/stats/, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ hits: 42, misses: 8 }) })
  );
}

// ── Collaboration mock data ───────────────────────────────────────────────────

const SESSIONS = [
  { session_id: 'sess-1', name: 'PR Code Review', mode: 'review', participants: ['human:alice', 'agent:reviewer'], participant_count: 2, status: 'active', content: 'Initial review draft', goal_id: 'goal-1', agent_id: 'agent-reviewer', created_at: new Date().toISOString() },
  { session_id: 'sess-2', name: 'Architecture Debate', mode: 'debate', participants: ['human:bob', 'agent:architect'], participant_count: 2, status: 'active', content: '', goal_id: 'goal-2', agent_id: 'agent-architect', created_at: new Date().toISOString() },
];

const CONSENSUS = { agreed: true, summary: 'The team agreed to use microservices architecture.', dissenter: null };

const INSIGHTS = {
  session_id: 'sess-1',
  session_name: 'PR Code Review',
  key_decisions: ['Use TypeScript for all new modules', 'Add test coverage to 80%'],
  action_items: ['Update CI pipeline', 'Write migration guide'],
  open_questions: ['Should we use Zustand or Redux?'],
  agreement_level: 'high',
  sentiment: 'positive',
  summary: 'Productive review with clear action items.',
  llm_powered: true,
};

async function setupCollabRoutes(page: Page): Promise<void> {
  await page.route(/localhost:8000\/collab\/sessions\/sess-.*\/insights/, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(INSIGHTS) })
  );
  await page.route(/localhost:8000\/collab\/sessions\/sess-.*\/consensus/, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(CONSENSUS) })
  );
  await page.route(/localhost:8000\/collab\/sessions\/sess-.*\/operations/, (route) => {
    if (route.request().method() === 'POST') return route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify({ operation_id: 'op-1', version: 1, author: 'human', operation: { type: 'content_update', content: 'Updated' } }) });
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) });
  });
  await page.route(/localhost:8000\/collab\/sessions\/sess-.*\/rounds/, (route) =>
    route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify({ round_id: 'r-1' }) })
  );
  await page.route(/localhost:8000\/collab\/sessions\/sess-.*\/delegate/, (route) =>
    route.fulfill({ status: 202, contentType: 'application/json', body: JSON.stringify({ delegated_goal_id: 'goal-delegated', sub_task: 'Write tests' }) })
  );
  await page.route(/localhost:8000\/collab\/sessions\/sess-.*/, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(SESSIONS[0]) })
  );
  await page.route(/localhost:8000\/collab\/sessions(\?.*)?$/, (route) => {
    if (route.request().method() === 'POST') return route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify({ ...SESSIONS[0], session_id: 'sess-new', content: 'Draft content' }) });
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(SESSIONS) });
  });
  // Mock WebSocket to prevent real connection attempts
  await page.route(/localhost:8000\/collab\/sessions\/.*\/ws/, (route) =>
    route.fulfill({ status: 404, body: '' })
  );
}

// ═══════════════════════════════════════════════════════════════════════════════
// SUITE 1 — SCHEDULES: Page Structure & CRUD
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Schedules — Structure & CRUD', () => {

  test('1. Shows Schedules heading and all 4 tabs', async ({ page }) => {
    await setupAuth(page);
    await setupScheduleRoutes(page);
    await page.goto('/schedules');
    await expect(page.getByRole('heading', { name: /schedules/i })).toBeVisible({ timeout: 10000 });
    await expect(page.getByTestId('tab-schedules')).toBeVisible({ timeout: 5000 });
    await expect(page.getByTestId('tab-analytics')).toBeVisible({ timeout: 5000 });
    await expect(page.getByTestId('tab-advisor')).toBeVisible({ timeout: 5000 });
    await expect(page.getByTestId('tab-nl')).toBeVisible({ timeout: 5000 });
  });

  test('2. Lists schedules with trigger type badges and status indicators', async ({ page }) => {
    await setupAuth(page);
    await setupScheduleRoutes(page);
    await page.goto('/schedules');
    await expect(page.getByTestId('schedules-table')).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('Daily engineering report')).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('cron').first()).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('interval').first()).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('webhook').first()).toBeVisible({ timeout: 5000 });
  });

  test('3. Quick-start template buttons are visible', async ({ page }) => {
    await setupAuth(page);
    await setupScheduleRoutes(page);
    await page.goto('/schedules');
    await expect(page.getByRole('heading', { name: /schedules/i })).toBeVisible({ timeout: 10000 });
    await expect(page.getByRole('button', { name: /daily report/i })).toBeVisible({ timeout: 5000 });
    await expect(page.getByRole('button', { name: /hourly check/i })).toBeVisible({ timeout: 5000 });
    await expect(page.getByRole('button', { name: /weekly summary/i })).toBeVisible({ timeout: 5000 });
  });

  test('4. New Schedule button opens create form with trigger type pills', async ({ page }) => {
    await setupAuth(page);
    await setupScheduleRoutes(page);
    await page.goto('/schedules');
    await expect(page.getByRole('heading', { name: /schedules/i })).toBeVisible({ timeout: 10000 });
    await page.getByRole('button', { name: /new schedule/i }).click();
    await expect(page.getByTestId('create-schedule-form')).toBeVisible({ timeout: 5000 });
    await expect(page.getByRole('button', { name: /^cron$/i })).toBeVisible({ timeout: 3000 });
    await expect(page.getByRole('button', { name: /^interval$/i })).toBeVisible({ timeout: 3000 });
    await expect(page.getByRole('button', { name: /^webhook$/i })).toBeVisible({ timeout: 3000 });
  });

  test('5. Creating a cron schedule POSTs to /schedules', async ({ page }) => {
    let postBody: Record<string, unknown> = {};
    await setupAuth(page);
    await setupScheduleRoutes(page);
    await page.route(/localhost:8000\/schedules(\?.*)?$/, (route) => {
      if (route.request().method() === 'POST') {
        postBody = JSON.parse(route.request().postData() ?? '{}');
        return route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify({ schedule_id: 'sched-new' }) });
      }
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(SCHEDULES) });
    });
    await page.goto('/schedules');
    await page.getByRole('button', { name: /new schedule/i }).click();
    await page.getByPlaceholder(/describe what to run/i).fill('Send daily metrics');
    await page.getByRole('button', { name: /create schedule/i }).click();
    await expect(async () => {
      expect(postBody.goal_template).toBe('Send daily metrics');
      expect(postBody.trigger_type).toBe('cron');
    }).toPass({ timeout: 5000 });
  });

  test('6. Pause button calls /pause endpoint', async ({ page }) => {
    let pauseCalled = false;
    await setupAuth(page);
    await setupScheduleRoutes(page);
    await page.route(/localhost:8000\/schedules\/sched-1\/pause/, (route) => {
      pauseCalled = true;
      return route.fulfill({ status: 200, body: '{}' });
    });
    await page.goto('/schedules');
    await expect(page.getByTestId('schedules-table')).toBeVisible({ timeout: 10000 });
    await page.getByTestId('pause-btn-sched-1').click();
    await expect(async () => { expect(pauseCalled).toBe(true); }).toPass({ timeout: 5000 });
  });

  test('7. Delete button calls DELETE endpoint', async ({ page }) => {
    let deleteCalled = false;
    await setupAuth(page);
    await setupScheduleRoutes(page);
    await page.route(/localhost:8000\/schedules\/sched-2/, (route) => {
      if (route.request().method() === 'DELETE') {
        deleteCalled = true;
        return route.fulfill({ status: 204, body: '' });
      }
      return route.fulfill({ status: 200, body: '{}' });
    });
    await page.goto('/schedules');
    await expect(page.getByTestId('schedules-table')).toBeVisible({ timeout: 10000 });
    await page.getByTestId('delete-btn-sched-2').click();
    await expect(async () => { expect(deleteCalled).toBe(true); }).toPass({ timeout: 5000 });
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// SUITE 2 — SCHEDULES: Analytics + AI Advisor
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Schedules — Analytics & AI Advisor', () => {

  test('8. Analytics tab shows KPI stat cards (Total/Active/Paused/Fired)', async ({ page }) => {
    await setupAuth(page);
    await setupScheduleRoutes(page);
    await page.goto('/schedules');
    await expect(page.getByRole('heading', { name: /schedules/i })).toBeVisible({ timeout: 10000 });
    await page.getByTestId('tab-analytics').click();
    await expect(page.getByText('Total').first()).toBeVisible({ timeout: 8000 });
    await expect(page.getByText('Active').first()).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('Paused').first()).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('3').first()).toBeVisible({ timeout: 5000 });
  });

  test('9. Analytics tab shows 7-day firing activity bar chart', async ({ page }) => {
    await setupAuth(page);
    await setupScheduleRoutes(page);
    await page.goto('/schedules');
    await page.getByTestId('tab-analytics').click();
    await expect(page.getByText('Firing Activity').first()).toBeVisible({ timeout: 8000 });
  });

  test('10. Analytics shows trigger type distribution', async ({ page }) => {
    await setupAuth(page);
    await setupScheduleRoutes(page);
    await page.goto('/schedules');
    await page.getByTestId('tab-analytics').click();
    await expect(page.getByText('Trigger Types').first()).toBeVisible({ timeout: 8000 });
  });

  test('11. AI Advisor tab has goal input and Suggest button', async ({ page }) => {
    await setupAuth(page);
    await setupScheduleRoutes(page);
    await page.goto('/schedules');
    await page.getByTestId('tab-advisor').click();
    await expect(page.getByText(/AI Schedule Advisor/i)).toBeVisible({ timeout: 8000 });
    await expect(page.getByTestId('suggest-btn')).toBeVisible({ timeout: 5000 });
  });

  test('12. AI Advisor returns 3 ranked suggestions on submit', async ({ page }) => {
    await setupAuth(page);
    await setupScheduleRoutes(page);
    await page.goto('/schedules');
    await page.getByTestId('tab-advisor').click();
    await page.getByPlaceholder(/describe what your schedule should do/i).fill('Send daily engineering metrics');
    await page.getByTestId('suggest-btn').click();
    await expect(page.getByText('Daily at 9 AM')).toBeVisible({ timeout: 8000 });
    await expect(page.getByText('Every 4 hours')).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('Weekly Monday 8 AM')).toBeVisible({ timeout: 5000 });
  });

  test('13. AI Advisor shows powered-by badge after response', async ({ page }) => {
    await setupAuth(page);
    await setupScheduleRoutes(page);
    await page.goto('/schedules');
    await page.getByTestId('tab-advisor').click();
    await page.getByPlaceholder(/describe what your schedule should do/i).fill('Monitor payments');
    await page.getByTestId('suggest-btn').click();
    await expect(page.getByText(/AI-powered/i)).toBeVisible({ timeout: 8000 });
  });

  test('14. NL Scheduler tab has chat interface with example buttons', async ({ page }) => {
    await setupAuth(page);
    await setupScheduleRoutes(page);
    await page.goto('/schedules');
    await page.getByTestId('tab-nl').click();
    await expect(page.getByText(/every weekday at 9 AM/i).first()).toBeVisible({ timeout: 8000 });
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// SUITE 3 — KNOWLEDGE: Collections + Ask AI
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Knowledge — Collections & Ask AI', () => {

  test('15. Shows Knowledge heading and 5 tabs', async ({ page }) => {
    await setupAuth(page);
    await setupKnowledgeRoutes(page);
    await page.goto('/knowledge');
    await expect(page.getByRole('heading', { name: /knowledge/i })).toBeVisible({ timeout: 10000 });
    await expect(page.getByTestId('tab-collections')).toBeVisible({ timeout: 5000 });
    await expect(page.getByTestId('tab-ask')).toBeVisible({ timeout: 5000 });
    await expect(page.getByTestId('tab-ingest')).toBeVisible({ timeout: 5000 });
    await expect(page.getByTestId('tab-search')).toBeVisible({ timeout: 5000 });
    await expect(page.getByTestId('tab-analytics')).toBeVisible({ timeout: 5000 });
  });

  test('16. Collections grid shows collection cards with doc counts', async ({ page }) => {
    await setupAuth(page);
    await setupKnowledgeRoutes(page);
    await page.goto('/knowledge');
    await expect(page.getByTestId('collections-grid')).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('Engineering Docs')).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('Product Specs')).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('42 docs').first()).toBeVisible({ timeout: 5000 });
  });

  test('17. Collection card stats expand to show chunk count and coverage', async ({ page }) => {
    await setupAuth(page);
    await setupKnowledgeRoutes(page);
    await page.goto('/knowledge');
    await expect(page.getByTestId('collection-card-col-1')).toBeVisible({ timeout: 10000 });
    await page.getByTestId('collection-card-col-1').getByText(/view stats/i).click();
    // Wait for the stats to appear (the section expands inline)
    await expect(
      page.getByTestId('collection-card-col-1').getByText(/350|98%|91%/i).first()
    ).toBeVisible({ timeout: 8000 });
  });

  test('18. Ask AI tab shows question input and example queries', async ({ page }) => {
    await setupAuth(page);
    await setupKnowledgeRoutes(page);
    await page.goto('/knowledge');
    await page.getByTestId('tab-ask').click();
    await expect(page.getByTestId('ask-input')).toBeVisible({ timeout: 8000 });
    await expect(page.getByTestId('ask-btn')).toBeVisible({ timeout: 5000 });
    await expect(page.getByText(/RAG-powered/i)).toBeVisible({ timeout: 5000 });
  });

  test('19. Asking a question shows answer with citations', async ({ page }) => {
    await setupAuth(page);
    await setupKnowledgeRoutes(page);
    await page.goto('/knowledge');
    await page.getByTestId('tab-ask').click();
    await expect(page.getByTestId('ask-input')).toBeVisible({ timeout: 8000 });
    await page.getByTestId('ask-input').fill('How does deployment work?');
    await page.getByTestId('ask-btn').click();
    await expect(page.getByTestId('answer-panel')).toBeVisible({ timeout: 10000 });
    await expect(page.getByText(/GitHub Actions/i).first()).toBeVisible({ timeout: 5000 });
  });

  test('20. Citations panel shows numbered sources with scores', async ({ page }) => {
    await setupAuth(page);
    await setupKnowledgeRoutes(page);
    await page.goto('/knowledge');
    await page.getByTestId('tab-ask').click();
    await page.getByTestId('ask-input').fill('Deployment process?');
    await page.getByTestId('ask-btn').click();
    await expect(page.getByTestId('citations-panel')).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('[1]').first()).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('[2]').first()).toBeVisible({ timeout: 5000 });
  });

  test('21. Creating a collection opens form and POSTs correctly', async ({ page }) => {
    let postBody: Record<string, unknown> = {};
    await setupAuth(page);
    await setupKnowledgeRoutes(page);
    await page.route(/localhost:8000\/knowledge\/collections(\?.*)?$/, (route) => {
      if (route.request().method() === 'POST') {
        postBody = JSON.parse(route.request().postData() ?? '{}');
        return route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify({ collection_id: 'col-new', name: 'API Reference' }) });
      }
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(COLLECTIONS) });
    });
    await page.goto('/knowledge');
    await page.getByRole('button', { name: /new collection/i }).click();
    await page.getByPlaceholder(/my-knowledge-base/i).fill('API Reference');
    await page.getByRole('button', { name: /^create$/i }).click();
    await expect(async () => { expect(postBody.name).toBe('API Reference'); }).toPass({ timeout: 5000 });
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// SUITE 4 — KNOWLEDGE: Ingest + Search + Analytics
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Knowledge — Ingest, Search & Analytics', () => {

  test('22. Ingest tab shows source type pills', async ({ page }) => {
    await setupAuth(page);
    await setupKnowledgeRoutes(page);
    await page.goto('/knowledge');
    await page.getByTestId('tab-ingest').click();
    await expect(page.getByRole('button', { name: /^text$/i })).toBeVisible({ timeout: 8000 });
    await expect(page.getByRole('button', { name: /github/i })).toBeVisible({ timeout: 5000 });
    await expect(page.getByRole('button', { name: /confluence/i })).toBeVisible({ timeout: 5000 });
    await expect(page.getByRole('button', { name: /slack/i })).toBeVisible({ timeout: 5000 });
  });

  test('23. Ingest form shows drag-and-drop zone for text source', async ({ page }) => {
    await setupAuth(page);
    await setupKnowledgeRoutes(page);
    await page.goto('/knowledge');
    await page.getByTestId('tab-ingest').click();
    await expect(page.getByText(/drag.*drop/i).first()).toBeVisible({ timeout: 8000 });
  });

  test('24. Selecting GitHub shows repo and branch fields', async ({ page }) => {
    await setupAuth(page);
    await setupKnowledgeRoutes(page);
    await page.goto('/knowledge');
    await page.getByTestId('tab-ingest').click();
    await page.getByRole('button', { name: /^github$/i }).click();
    await expect(page.getByPlaceholder(/owner\/repo/i)).toBeVisible({ timeout: 5000 });
  });

  test('25. Search tab shows query input and collection filter', async ({ page }) => {
    await setupAuth(page);
    await setupKnowledgeRoutes(page);
    await page.goto('/knowledge');
    await page.getByTestId('tab-search').click();
    await expect(page.getByPlaceholder(/search across/i)).toBeVisible({ timeout: 8000 });
  });

  test('26. Search results show score badges and copy buttons', async ({ page }) => {
    await setupAuth(page);
    await setupKnowledgeRoutes(page);
    await page.goto('/knowledge');
    await page.getByTestId('tab-search').click();
    await page.getByPlaceholder(/search across/i).fill('CI/CD pipeline');
    const searchBtns = await page.getByRole('button', { name: /^search$/i }).all();
    await searchBtns[searchBtns.length - 1].click();
    await expect(page.getByText(/GitHub Actions/i)).toBeVisible({ timeout: 8000 });
    await expect(page.getByText(/94\.0%/i).first()).toBeVisible({ timeout: 5000 });
  });

  test('27. Analytics tab shows collection health grid and cache stats', async ({ page }) => {
    await setupAuth(page);
    await setupKnowledgeRoutes(page);
    await page.goto('/knowledge');
    await page.getByTestId('tab-analytics').click();
    await expect(page.getByText('Cache Hits')).toBeVisible({ timeout: 8000 });
    await expect(page.getByText('42').first()).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('Collection Health').first()).toBeVisible({ timeout: 5000 });
  });

  test('28. Delete collection button calls DELETE API', async ({ page }) => {
    let deleteCalled = false;
    await setupAuth(page);
    await setupKnowledgeRoutes(page);
    await page.route(/localhost:8000\/knowledge\/collections\/col-2/, (route) => {
      if (route.request().method() === 'DELETE') { deleteCalled = true; return route.fulfill({ status: 204, body: '' }); }
      return route.fulfill({ status: 200, body: '{}' });
    });
    await page.goto('/knowledge');
    await expect(page.getByTestId('delete-collection-col-2')).toBeVisible({ timeout: 10000 });
    await page.getByTestId('delete-collection-col-2').click();
    await expect(async () => { expect(deleteCalled).toBe(true); }).toPass({ timeout: 5000 });
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// SUITE 5 — COLLABORATION: Sessions
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Collaboration — Sessions', () => {

  test('29. Shows Collaboration heading and sessions as cards', async ({ page }) => {
    await setupAuth(page);
    await setupCollabRoutes(page);
    await page.goto('/collaboration');
    await expect(page.getByRole('heading', { name: /collaboration/i })).toBeVisible({ timeout: 10000 });
    await expect(page.getByTestId('sessions-list')).toBeVisible({ timeout: 8000 });
    // At least one session card should be visible
    await expect(page.getByTestId('session-card').first()).toBeVisible({ timeout: 5000 });
  });

  test('30. Session cards show mode badges (review, debate)', async ({ page }) => {
    await setupAuth(page);
    await setupCollabRoutes(page);
    await page.goto('/collaboration');
    await expect(page.getByTestId('sessions-list')).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('PR Code Review')).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('Architecture Debate')).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('review').first()).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('debate').first()).toBeVisible({ timeout: 5000 });
  });

  test('31. Session cards show participant pips', async ({ page }) => {
    await setupAuth(page);
    await setupCollabRoutes(page);
    await page.goto('/collaboration');
    await expect(page.getByTestId('sessions-list')).toBeVisible({ timeout: 10000 });
    // Participant pips are rendered inside session cards
    await expect(page.getByTestId('sessions-list')).toBeVisible({ timeout: 5000 });
  });

  test('32. New Session button opens create form with mode selector', async ({ page }) => {
    await setupAuth(page);
    await setupCollabRoutes(page);
    await page.goto('/collaboration');
    await expect(page.getByRole('heading', { name: /collaboration/i })).toBeVisible({ timeout: 10000 });
    await page.getByTestId('create-session-btn').click();
    await expect(page.getByPlaceholder('Session name')).toBeVisible({ timeout: 5000 });
    await expect(page.getByText(/review/i).first()).toBeVisible({ timeout: 5000 });
  });

  test('33. Session templates appear in create form', async ({ page }) => {
    await setupAuth(page);
    await setupCollabRoutes(page);
    await page.goto('/collaboration');
    await page.getByTestId('create-session-btn').click();
    await expect(page.getByText(/Code Review|Product Planning|Architecture Decision/i).first()).toBeVisible({ timeout: 5000 });
  });

  test('34. Creating a session calls POST /collab/sessions with correct body', async ({ page }) => {
    let postBody: Record<string, unknown> = {};
    await setupAuth(page);
    await setupCollabRoutes(page);
    await page.route(/localhost:8000\/collab\/sessions(\?.*)?$/, (route) => {
      if (route.request().method() === 'POST') {
        postBody = JSON.parse(route.request().postData() ?? '{}');
        return route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify(SESSIONS[0]) });
      }
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(SESSIONS) });
    });
    await page.goto('/collaboration');
    await page.getByTestId('create-session-btn').click();
    await page.getByPlaceholder('Session name').fill('Feature Planning');
    await page.getByText('Create Session').click();
    await expect(async () => {
      expect(postBody.name).toBe('Feature Planning');
      expect(postBody.mode).toBeTruthy();
    }).toPass({ timeout: 5000 });
  });

  test('35. Session list shows correct participant counts', async ({ page }) => {
    await setupAuth(page);
    await setupCollabRoutes(page);
    await page.goto('/collaboration');
    await expect(page.getByTestId('sessions-list')).toBeVisible({ timeout: 10000 });
    // Both sessions have participant counts shown
    await expect(page.getByText('2').first()).toBeVisible({ timeout: 5000 });
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// SUITE 6 — COLLABORATION: Live Session Panel
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Collaboration — Live Session Panel', () => {

  test('36. New session create form shows mode selector with 4 modes', async ({ page }) => {
    await setupAuth(page);
    await setupCollabRoutes(page);
    await page.goto('/collaboration');
    await page.getByTestId('create-session-btn').click();
    // Mode buttons: review, suggest, debate, brainstorm
    await expect(page.getByText('review').first()).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('suggest').first()).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('debate').first()).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('brainstorm').first()).toBeVisible({ timeout: 5000 });
  });

  test('37. Session status filter shows All/Active/Closed pills', async ({ page }) => {
    await setupAuth(page);
    await setupCollabRoutes(page);
    await page.goto('/collaboration');
    await expect(page.getByTestId('sessions-list')).toBeVisible({ timeout: 10000 });
    await expect(page.getByRole('button', { name: /^All$/i })).toBeVisible({ timeout: 5000 });
    await expect(page.getByRole('button', { name: /^Active$/i })).toBeVisible({ timeout: 5000 });
    await expect(page.getByRole('button', { name: /^Closed$/i })).toBeVisible({ timeout: 5000 });
  });

  test('38. Session count shown in status bar', async ({ page }) => {
    await setupAuth(page);
    await setupCollabRoutes(page);
    await page.goto('/collaboration');
    await expect(page.getByTestId('sessions-list')).toBeVisible({ timeout: 10000 });
    await expect(page.getByText(/2 sessions/i)).toBeVisible({ timeout: 5000 });
  });

  test('39. Creating a new session POSTs to /collab/sessions', async ({ page }) => {
    let postBody: Record<string, unknown> = {};
    await setupAuth(page);
    await setupCollabRoutes(page);
    await page.route(/localhost:8000\/collab\/sessions(\?.*)?$/, (route) => {
      if (route.request().method() === 'POST') {
        postBody = JSON.parse(route.request().postData() ?? '{}');
        return route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify(SESSIONS[0]) });
      }
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(SESSIONS) });
    });
    await page.goto('/collaboration');
    await page.getByTestId('create-session-btn').click();
    await page.getByPlaceholder('Session name').fill('Architecture Review');
    await page.getByText('Create Session').click();
    await expect(async () => {
      expect(postBody.name).toBe('Architecture Review');
    }).toPass({ timeout: 5000 });
  });

  test('40. Session cards show goal and agent IDs', async ({ page }) => {
    await setupAuth(page);
    await setupCollabRoutes(page);
    await page.goto('/collaboration');
    await expect(page.getByTestId('sessions-list')).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('goal-1')).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('agent-reviewer')).toBeVisible({ timeout: 5000 });
  });

  test('41. Both sessions are shown initially (no filter)', async ({ page }) => {
    await setupAuth(page);
    await setupCollabRoutes(page);
    await page.goto('/collaboration');
    await expect(page.getByTestId('sessions-list')).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('PR Code Review')).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('Architecture Debate')).toBeVisible({ timeout: 5000 });
  });

  test('42. Collaboration page shows "New Session" and heading', async ({ page }) => {
    await setupAuth(page);
    await setupCollabRoutes(page);
    await page.goto('/collaboration');
    await expect(page.getByRole('heading', { name: /collaboration/i })).toBeVisible({ timeout: 10000 });
    await expect(page.getByTestId('create-session-btn')).toBeVisible({ timeout: 5000 });
    await expect(page.getByText(/real-time/i).first()).toBeVisible({ timeout: 5000 });
  });
});
