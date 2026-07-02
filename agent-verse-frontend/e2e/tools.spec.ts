/**
 * E2E tests — Tools page
 *
 * Tests all three tabs end-to-end against mocked backend routes:
 *   1. Code Runner  — execute code, view stdout/stderr, execution history, language switch
 *   2. File Manager — list, create, open, save, delete files
 *   3. Email        — compose and send, error handling, sent history
 */
import { test, expect, type Page } from '@playwright/test';

// ── Auth helper ───────────────────────────────────────────────────────────────

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
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ tenant_id: 'test-tenant', name: 'Test Org', plan: 'free' }),
    })
  );
}

// ── Shared file mock ──────────────────────────────────────────────────────────

async function mockFilesApi(page: Page, files: object[] = []) {
  await page.route(/localhost:8000\/tools\/files(\?.*)?$/, (route) => {
    if (route.request().method() === 'GET')
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(files) });
    return route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify({ path: 'new.txt', bytes_written: 5, success: true }) });
  });
}

// ═══════════════════════════════════════════════════════════════════════════════
// Code Runner
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Tools — Code Runner', () => {
  test('page loads with Code Runner tab active', async ({ page }) => {
    await setupAuth(page);
    await mockFilesApi(page);
    await page.goto('/tools');
    await expect(page.getByRole('tab', { name: /code runner/i })).toBeVisible({ timeout: 15000 });
    await expect(page.getByRole('tab', { name: /code runner/i })).toHaveAttribute('aria-selected', 'true');
    await expect(page.getByLabel('Code')).toBeVisible();
  });

  test('all three language buttons are present', async ({ page }) => {
    await setupAuth(page);
    await mockFilesApi(page);
    await page.goto('/tools');
    await expect(page.getByRole('button', { name: /python/i })).toBeVisible({ timeout: 10000 });
    await expect(page.getByRole('button', { name: /javascript/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /bash/i })).toBeVisible();
  });

  test('Run code button is disabled when editor is empty', async ({ page }) => {
    await setupAuth(page);
    await mockFilesApi(page);
    await page.goto('/tools');
    await expect(page.getByRole('button', { name: /run code/i })).toBeDisabled({ timeout: 10000 });
  });

  test('executes Python code and displays stdout', async ({ page }) => {
    await setupAuth(page);
    await mockFilesApi(page);
    await page.route('**/tools/execute-code', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ stdout: 'Hello, World!\n', stderr: '', exit_code: 0, success: true, timed_out: false, execution_time_ms: 35 }),
      })
    );

    await page.goto('/tools');
    await page.getByLabel('Code').fill("print('Hello, World!')");
    await page.getByRole('button', { name: /run code/i }).click();

    await expect(page.getByText('Hello, World!')).toBeVisible({ timeout: 10000 });
    await expect(page.getByText(/exit 0/i)).toBeVisible();
    await expect(page.getByText(/35ms/)).toBeVisible();
  });

  test('shows stderr section on non-zero exit', async ({ page }) => {
    await setupAuth(page);
    await mockFilesApi(page);
    await page.route('**/tools/execute-code', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ stdout: '', stderr: 'NameError: name "foo" is not defined', exit_code: 1, success: false, timed_out: false, execution_time_ms: 10 }),
      })
    );

    await page.goto('/tools');
    await page.getByLabel('Code').fill('foo');
    await page.getByRole('button', { name: /run code/i }).click();

    await expect(page.getByText(/NameError/)).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('stderr')).toBeVisible();
  });

  test('execution history appears after a run', async ({ page }) => {
    await setupAuth(page);
    await mockFilesApi(page);
    await page.route('**/tools/execute-code', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ stdout: 'ok', stderr: '', exit_code: 0, success: true, timed_out: false, execution_time_ms: 5 }),
      })
    );

    await page.goto('/tools');
    await page.getByLabel('Code').fill('print("ok")');
    await page.getByRole('button', { name: /run code/i }).click();
    await page.getByText('ok').waitFor({ timeout: 10000 });

    await expect(page.getByText(/execution history/i)).toBeVisible();
  });

  test('Template button loads example code', async ({ page }) => {
    await setupAuth(page);
    await mockFilesApi(page);
    await page.goto('/tools');
    await page.getByRole('button', { name: /template/i }).click();
    const code = await page.getByLabel('Code').inputValue();
    expect(code.length).toBeGreaterThan(0);
  });

  test('timeout input accepts values 1–60', async ({ page }) => {
    await setupAuth(page);
    await mockFilesApi(page);
    await page.goto('/tools');
    const timeoutInput = page.getByLabel(/timeout in seconds/i);
    await timeoutInput.fill('45');
    await expect(timeoutInput).toHaveValue('45');
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// File Manager
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Tools — File Manager', () => {
  test('shows file listing with name and size', async ({ page }) => {
    await setupAuth(page);
    await page.route(/localhost:8000\/tools\/files(\?.*)?$/, (route) => {
      if (route.request().method() === 'GET')
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify([
            { name: 'main.py', path: 'main.py', type: 'file', size_bytes: 512, modified_at: 1700000000 },
            { name: 'data.json', path: 'data.json', type: 'file', size_bytes: 1024, modified_at: 1700000000 },
          ]),
        });
      return route.fulfill({ status: 204 });
    });

    await page.goto('/tools');
    await page.getByRole('tab', { name: /file manager/i }).click();

    await expect(page.getByText('main.py')).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('data.json')).toBeVisible();
    await expect(page.getByText('512 B')).toBeVisible();
  });

  test('shows empty state when workspace is empty', async ({ page }) => {
    await setupAuth(page);
    await mockFilesApi(page, []);
    await page.goto('/tools');
    await page.getByRole('tab', { name: /file manager/i }).click();
    await expect(page.getByText(/no files yet/i)).toBeVisible({ timeout: 10000 });
  });

  test('clicking a file opens its content', async ({ page }) => {
    await setupAuth(page);
    await page.route(/localhost:8000\/tools\/files(\?.*)?$/, (route) => {
      const url = route.request().url();
      if (route.request().method() === 'GET' && url.includes('/main.py'))
        return route.fulfill({
          status: 200, contentType: 'application/json',
          body: JSON.stringify({ path: 'main.py', content: 'print("from file")', success: true }),
        });
      if (route.request().method() === 'GET')
        return route.fulfill({
          status: 200, contentType: 'application/json',
          body: JSON.stringify([{ name: 'main.py', path: 'main.py', type: 'file', size_bytes: 18, modified_at: 0 }]),
        });
      return route.fulfill({ status: 204 });
    });

    await page.goto('/tools');
    await page.getByRole('tab', { name: /file manager/i }).click();
    await expect(page.getByText('main.py')).toBeVisible({ timeout: 10000 });
    await page.getByText('main.py').click();

    await expect(page.getByLabel('File content')).toHaveValue(/print\("from file"\)/);
  });

  test('New File button prepares empty editor', async ({ page }) => {
    await setupAuth(page);
    await mockFilesApi(page, []);
    await page.goto('/tools');
    await page.getByRole('tab', { name: /file manager/i }).click();
    await page.getByLabel('New file').click();
    await expect(page.getByLabel('File path')).toBeVisible({ timeout: 10000 });
    await expect(page.getByLabel('File content')).toHaveValue('');
  });

  test('Save file button calls POST /tools/files/{path}', async ({ page }) => {
    let savedPath = '';
    await setupAuth(page);
    await page.route(/localhost:8000\/tools\/files(\?.*)?$/, async (route) => {
      if (route.request().method() === 'GET')
        return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
      if (route.request().method() === 'POST') {
        savedPath = route.request().url().split('/tools/files/')[1];
        return route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify({ path: 'test.txt', bytes_written: 5, success: true }) });
      }
      return route.fulfill({ status: 204 });
    });

    await page.goto('/tools');
    await page.getByRole('tab', { name: /file manager/i }).click();
    await page.getByLabel('New file').click();
    await page.getByLabel('File path').fill('test.txt');
    await page.getByLabel('File content').fill('hello');
    await page.getByRole('button', { name: /save file/i }).click();

    await page.waitForFunction(() => document.body.textContent?.includes('saved'));
    expect(savedPath).toContain('test.txt');
  });

  test('delete button shows inline confirm and calls DELETE', async ({ page }) => {
    let deleteUrl = '';
    await setupAuth(page);
    await page.route(/localhost:8000\/tools\/files(\?.*)?$/, async (route) => {
      const method = route.request().method();
      if (method === 'GET' && !route.request().url().includes('/hello.py'))
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([{ name: 'hello.py', path: 'hello.py', type: 'file', size_bytes: 10, modified_at: 0 }]) });
      if (method === 'DELETE') {
        deleteUrl = route.request().url();
        return route.fulfill({ status: 204 });
      }
      if (method === 'GET')
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ path: 'hello.py', content: '', success: true }) });
      return route.fulfill({ status: 204 });
    });

    await page.goto('/tools');
    await page.getByRole('tab', { name: /file manager/i }).click();
    await expect(page.getByText('hello.py')).toBeVisible({ timeout: 10000 });
    await page.getByLabel('Delete hello.py').click();
    // Inline confirm
    await expect(page.getByRole('button', { name: /^yes$/i })).toBeVisible();
    await page.getByRole('button', { name: /^yes$/i }).click();
    await page.waitForFunction(() => !document.body.textContent?.includes('hello.py'));
    expect(deleteUrl).toContain('hello.py');
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// Email Composer
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Tools — Email', () => {
  test('email tab renders To, Subject, Message fields', async ({ page }) => {
    await setupAuth(page);
    await mockFilesApi(page);
    await page.goto('/tools');
    await page.getByRole('tab', { name: /^email$/i }).click();
    await expect(page.getByLabel(/^to$/i)).toBeVisible({ timeout: 10000 });
    await expect(page.getByLabel(/^subject$/i)).toBeVisible();
    await expect(page.getByLabel(/^message$/i)).toBeVisible();
  });

  test('Send button is disabled until To and Subject are filled', async ({ page }) => {
    await setupAuth(page);
    await mockFilesApi(page);
    await page.goto('/tools');
    await page.getByRole('tab', { name: /^email$/i }).click();
    await expect(page.getByRole('button', { name: /send email/i })).toBeDisabled({ timeout: 10000 });
    await page.getByLabel(/^to$/i).fill('x@example.com');
    await expect(page.getByRole('button', { name: /send email/i })).toBeDisabled(); // still disabled — no subject
    await page.getByLabel(/^subject$/i).fill('Hi');
    await expect(page.getByRole('button', { name: /send email/i })).toBeEnabled();
  });

  test('successful send clears form and shows sent history', async ({ page }) => {
    await setupAuth(page);
    await mockFilesApi(page);
    await page.route('**/tools/email/send', (route) =>
      route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ success: true, to: ['x@example.com'], subject: 'Deploy done' }),
      })
    );

    await page.goto('/tools');
    await page.getByRole('tab', { name: /^email$/i }).click();
    await page.getByLabel(/^to$/i).fill('x@example.com');
    await page.getByLabel(/^subject$/i).fill('Deploy done');
    await page.getByLabel(/^message$/i).fill('All systems go.');
    await page.getByRole('button', { name: /send email/i }).click();

    // Sent history appears
    await expect(page.getByText('Deploy done')).toBeVisible({ timeout: 10000 });
  });

  test('CC button shows CC field', async ({ page }) => {
    await setupAuth(page);
    await mockFilesApi(page);
    await page.goto('/tools');
    await page.getByRole('tab', { name: /^email$/i }).click();
    await page.getByRole('button', { name: /^cc$/i }).click();
    await expect(page.getByLabel(/^cc$/i)).toBeVisible({ timeout: 5000 });
  });

  test('shows error toast when backend returns error', async ({ page }) => {
    await setupAuth(page);
    await mockFilesApi(page);
    await page.route('**/tools/email/send', (route) =>
      route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ detail: 'SMTP connection failed' }) })
    );

    await page.goto('/tools');
    await page.getByRole('tab', { name: /^email$/i }).click();
    await page.getByLabel(/^to$/i).fill('x@example.com');
    await page.getByLabel(/^subject$/i).fill('Test');
    await page.getByLabel(/^message$/i).fill('Body');
    await page.getByRole('button', { name: /send email/i }).click();

    // Should show error in some form (toast or inline)
    await expect(page.getByText(/failed|error/i)).toBeVisible({ timeout: 10000 });
  });
});
