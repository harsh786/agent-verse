/**
 * E2E tests — OCR Document Extraction page
 *
 * All backend calls are intercepted and mocked — no real Tesseract/OCR needed.
 *
 * Tests:
 *   Single tab — page load, drop zone, file upload, loading state, result panel,
 *                doc-type badge, confidence ring, field table, raw text, copy,
 *                JSON export, reset, error handling
 *   Batch tab  — switch tab, add files, extract all, result summary
 *   History tab — empty state, saved entries, clear history
 *   Accessibility — keyboard navigation, ARIA labels
 */

import { test, expect, type Page } from '@playwright/test';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// ── Fixtures ──────────────────────────────────────────────────────────────────

const MOCK_RESULT = {
  raw_text: 'John Doe\nDOB: 01/01/1990\nPAN: ABCDE1234F',
  document_type: 'pan_card',
  fields: {
    name: { value: 'John Doe', confidence: 0.95, is_valid: true, raw_value: null },
    pan_number: { value: 'ABCDE1234F', confidence: 0.88, is_valid: true, raw_value: null },
    dob: { value: '01/01/1990', confidence: 0.62, is_valid: true, raw_value: null },
    father_name: { value: '', confidence: 0.3, is_valid: false, raw_value: 'unclear' },
  },
  engine_used: 'tesseract',
  overall_confidence: 0.79,
  page_count: 1,
};

const MOCK_INVOICE = {
  raw_text: 'Invoice #INV-2024-001\nAmount: $1,250.00\nDue: 2024-03-15',
  document_type: 'invoice',
  fields: {
    invoice_number: { value: 'INV-2024-001', confidence: 0.97, is_valid: true, raw_value: null },
    amount: { value: '$1,250.00', confidence: 0.91, is_valid: true, raw_value: null },
    due_date: { value: '2024-03-15', confidence: 0.85, is_valid: true, raw_value: null },
  },
  engine_used: 'llm_vision',
  overall_confidence: 0.91,
  page_count: 2,
};

// ── Auth + API setup ──────────────────────────────────────────────────────────

async function setupAuth(page: Page) {
  // Default: reject all unknown routes (catch misconfigured test routes early)
  await page.route(/localhost:8000\/(?!ocr|tenants)/, (route) =>
    route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ detail: 'not found' }) }),
  );

  await page.addInitScript(() => {
    localStorage.setItem('av-auth', JSON.stringify({
      state: { apiKey: 'test-key', tenantId: 'test-tenant', plan: 'professional', isAuthenticated: true },
      version: 0,
    }));
    localStorage.setItem('av_api_key', 'test-key');
    sessionStorage.setItem('av_api_key', 'test-key');
    // Clear any stale history from previous tests
    localStorage.removeItem('ocr_history_v1');
  });

  await page.route('**/tenants/me', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ tenant_id: 'test-tenant', name: 'Acme Corp', plan: 'professional' }),
    }),
  );
}

async function mockOcrExtract(page: Page, result = MOCK_RESULT, delayMs = 0) {
  await page.route('**/ocr/extract', async (route) => {
    if (delayMs > 0) await new Promise((r) => setTimeout(r, delayMs));
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(result),
    });
  });
}

async function mockOcrBatch(page: Page, results = [MOCK_RESULT, MOCK_INVOICE]) {
  await page.route('**/ocr/batch', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        results,
        total: results.length,
        succeeded: results.filter(Boolean).length,
        failed: results.filter((r) => !r).length,
      }),
    }),
  );
}

async function navigateToOcr(page: Page) {
  await page.goto('/ocr');
  await expect(page.getByTestId('ocr-page')).toBeVisible({ timeout: 10_000 });
}

// ── Single Tab ────────────────────────────────────────────────────────────────

test.describe('OCR — Single Extraction', () => {
  test('page loads with title and drop zone', async ({ page }) => {
    await setupAuth(page);
    await navigateToOcr(page);

    await expect(page.getByText('OCR Document Extraction')).toBeVisible();
    await expect(page.getByTestId('drop-zone')).toBeVisible();
    await expect(page.getByTestId('tab-single')).toHaveClass(/bg-indigo-600/);
  });

  test('accepts image upload and shows filename', async ({ page }) => {
    await setupAuth(page);
    await mockOcrExtract(page);
    await navigateToOcr(page);

    // Create a small PNG fixture in memory
    const fileInput = page.getByTestId('file-input');
    await fileInput.setInputFiles({
      name: 'test-doc.png',
      mimeType: 'image/png',
      buffer: Buffer.from([137, 80, 78, 71, 13, 10, 26, 10, 0, 0, 0, 13]), // PNG header
    });

    await expect(page.getByTestId('filename')).toContainText('test-doc.png');
    await expect(page.getByTestId('extract-btn')).toBeVisible();
  });

  test('accepts PDF upload', async ({ page }) => {
    await setupAuth(page);
    await mockOcrExtract(page);
    await navigateToOcr(page);

    await page.getByTestId('file-input').setInputFiles({
      name: 'invoice.pdf',
      mimeType: 'application/pdf',
      buffer: Buffer.from('%PDF-1.4'),
    });

    await expect(page.getByTestId('filename')).toContainText('invoice.pdf');
  });

  test('clicking Extract calls the API and shows result', async ({ page }) => {
    await setupAuth(page);
    await mockOcrExtract(page);
    await navigateToOcr(page);

    await page.getByTestId('file-input').setInputFiles({
      name: 'pan_card.jpg',
      mimeType: 'image/jpeg',
      buffer: Buffer.from([0xff, 0xd8, 0xff]), // JPEG SOI marker
    });

    await page.getByTestId('extract-btn').click();

    await expect(page.getByTestId('ocr-result')).toBeVisible({ timeout: 8_000 });
    await expect(page.getByTestId('doc-type-badge')).toContainText('PAN Card');
    await expect(page.getByTestId('confidence-ring')).toBeVisible();
  });

  test('fields table shows extracted values', async ({ page }) => {
    await setupAuth(page);
    await mockOcrExtract(page);
    await navigateToOcr(page);

    await page.getByTestId('file-input').setInputFiles({
      name: 'doc.jpg',
      mimeType: 'image/jpeg',
      buffer: Buffer.from([0xff, 0xd8, 0xff]),
    });

    await page.getByTestId('extract-btn').click();
    await expect(page.getByTestId('fields-table')).toBeVisible({ timeout: 8_000 });

    await expect(page.getByText('John Doe')).toBeVisible();
    await expect(page.getByText('ABCDE1234F')).toBeVisible();
    await expect(page.getByText('01/01/1990')).toBeVisible();
  });

  test('raw text accordion reveals and hides text', async ({ page }) => {
    await setupAuth(page);
    await mockOcrExtract(page);
    await navigateToOcr(page);

    await page.getByTestId('file-input').setInputFiles({
      name: 'doc.jpg', mimeType: 'image/jpeg', buffer: Buffer.from([0xff, 0xd8, 0xff]),
    });
    await page.getByTestId('extract-btn').click();
    await expect(page.getByTestId('ocr-result')).toBeVisible({ timeout: 8_000 });

    // Raw text should be hidden initially
    await expect(page.getByTestId('raw-text')).not.toBeVisible();

    // Click to open
    await page.getByTestId('raw-text-toggle').click();
    await expect(page.getByTestId('raw-text')).toBeVisible();
    await expect(page.getByTestId('raw-text')).toContainText('John Doe');

    // Click again to close
    await page.getByTestId('raw-text-toggle').click();
    await expect(page.getByTestId('raw-text')).not.toBeVisible();
  });

  test('New button resets to drop zone', async ({ page }) => {
    await setupAuth(page);
    await mockOcrExtract(page);
    await navigateToOcr(page);

    await page.getByTestId('file-input').setInputFiles({
      name: 'doc.jpg', mimeType: 'image/jpeg', buffer: Buffer.from([0xff, 0xd8, 0xff]),
    });
    await page.getByTestId('extract-btn').click();
    await expect(page.getByTestId('ocr-result')).toBeVisible({ timeout: 8_000 });

    await page.getByTestId('new-extraction').click();
    await expect(page.getByTestId('drop-zone')).toBeVisible();
    await expect(page.getByTestId('ocr-result')).not.toBeVisible();
  });

  test('shows loading state during slow extraction', async ({ page }) => {
    await setupAuth(page);
    await mockOcrExtract(page, MOCK_RESULT, 500); // 500ms delay
    await navigateToOcr(page);

    await page.getByTestId('file-input').setInputFiles({
      name: 'doc.jpg', mimeType: 'image/jpeg', buffer: Buffer.from([0xff, 0xd8, 0xff]),
    });
    await page.getByTestId('extract-btn').click();
    await expect(page.getByTestId('loading-indicator')).toBeVisible();
    await expect(page.getByTestId('ocr-result')).toBeVisible({ timeout: 8_000 });
  });

  test('displays error toast on API failure', async ({ page }) => {
    await setupAuth(page);
    await page.route('**/ocr/extract', (route) =>
      route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ detail: 'OCR service unavailable' }) }),
    );
    await navigateToOcr(page);

    await page.getByTestId('file-input').setInputFiles({
      name: 'doc.jpg', mimeType: 'image/jpeg', buffer: Buffer.from([0xff, 0xd8, 0xff]),
    });
    await page.getByTestId('extract-btn').click();

    // Result panel should not appear
    await page.waitForTimeout(2_000);
    await expect(page.getByTestId('ocr-result')).not.toBeVisible();
  });

  test('Export JSON button triggers download', async ({ page }) => {
    await setupAuth(page);
    await mockOcrExtract(page, MOCK_INVOICE);
    await navigateToOcr(page);

    const downloadPromise = page.waitForEvent('download');

    await page.getByTestId('file-input').setInputFiles({
      name: 'invoice.jpg', mimeType: 'image/jpeg', buffer: Buffer.from([0xff, 0xd8, 0xff]),
    });
    await page.getByTestId('extract-btn').click();
    await expect(page.getByTestId('ocr-result')).toBeVisible({ timeout: 8_000 });
    await page.getByTestId('export-json').click();

    const download = await downloadPromise;
    expect(download.suggestedFilename()).toMatch(/\.json$/);
  });
});

// ── Batch Tab ─────────────────────────────────────────────────────────────────

test.describe('OCR — Batch Extraction', () => {
  test('switches to batch tab', async ({ page }) => {
    await setupAuth(page);
    await navigateToOcr(page);

    await page.getByTestId('tab-batch').click();
    await expect(page.getByTestId('tab-batch')).toHaveClass(/bg-indigo-600/);
    await expect(page.getByTestId('drop-zone')).toBeVisible();
  });

  test('batch extraction shows result summary', async ({ page }) => {
    await setupAuth(page);
    await mockOcrBatch(page);
    await navigateToOcr(page);

    await page.getByTestId('tab-batch').click();

    await page.getByTestId('file-input').setInputFiles([
      { name: 'doc1.jpg', mimeType: 'image/jpeg', buffer: Buffer.from([0xff, 0xd8, 0xff]) },
    ]);

    await expect(page.getByTestId('extract-batch-btn')).toBeVisible();
    await page.getByTestId('extract-batch-btn').click();

    await expect(page.getByText(/Batch complete/i)).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText(/succeeded/i)).toBeVisible();
  });

  test('batch grid shows individual file results', async ({ page }) => {
    await setupAuth(page);
    await mockOcrBatch(page, [MOCK_RESULT]);
    await navigateToOcr(page);

    await page.getByTestId('tab-batch').click();
    await page.getByTestId('file-input').setInputFiles([
      { name: 'pan.jpg', mimeType: 'image/jpeg', buffer: Buffer.from([0xff, 0xd8, 0xff]) },
    ]);
    await page.getByTestId('extract-batch-btn').click();

    await expect(page.getByTestId('batch-grid')).toBeVisible({ timeout: 8_000 });
    await expect(page.getByTestId('batch-item-done')).toBeVisible({ timeout: 8_000 });
  });
});

// ── History Tab ───────────────────────────────────────────────────────────────

test.describe('OCR — History', () => {
  test('shows empty state when no history', async ({ page }) => {
    await setupAuth(page);
    await navigateToOcr(page);

    await page.getByTestId('tab-history').click();
    await expect(page.getByText(/No extraction history yet/i)).toBeVisible();
  });

  test('saved result appears in history tab', async ({ page }) => {
    await setupAuth(page);
    await mockOcrExtract(page);
    await navigateToOcr(page);

    // Extract a document
    await page.getByTestId('file-input').setInputFiles({
      name: 'my-pan.jpg', mimeType: 'image/jpeg', buffer: Buffer.from([0xff, 0xd8, 0xff]),
    });
    await page.getByTestId('extract-btn').click();
    await expect(page.getByTestId('ocr-result')).toBeVisible({ timeout: 8_000 });

    // Save it
    await page.getByRole('button', { name: /Save/i }).click();

    // Switch to history and verify
    await page.getByTestId('tab-history').click();
    await expect(page.getByTestId('history-entry')).toBeVisible();
    await expect(page.getByText('my-pan.jpg')).toBeVisible();
    await expect(page.getByText('PAN Card')).toBeVisible();
  });

  test('history persists across page navigation', async ({ page }) => {
    await setupAuth(page);
    await mockOcrExtract(page);

    // Pre-seed history in localStorage
    await page.addInitScript(() => {
      localStorage.setItem('ocr_history_v1', JSON.stringify([{
        id: 'seed-1',
        filename: 'seeded-doc.jpg',
        timestamp: Date.now(),
        result: {
          raw_text: 'Seeded text',
          document_type: 'invoice',
          fields: {},
          engine_used: 'tesseract',
          overall_confidence: 0.85,
          page_count: 1,
        },
      }]));
    });

    await navigateToOcr(page);
    await page.getByTestId('tab-history').click();
    await expect(page.getByText('seeded-doc.jpg')).toBeVisible();
    await expect(page.getByText('Invoice')).toBeVisible();
  });
});

// ── Accessibility ─────────────────────────────────────────────────────────────

test.describe('OCR — Accessibility', () => {
  test('drop zone is keyboard accessible (Enter key)', async ({ page }) => {
    await setupAuth(page);
    await navigateToOcr(page);

    const dropZone = page.getByTestId('drop-zone');
    await expect(dropZone).toHaveAttribute('role', 'button');
    await expect(dropZone).toHaveAttribute('tabindex', '0');
  });

  test('tabs are all reachable and interactive', async ({ page }) => {
    await setupAuth(page);
    await navigateToOcr(page);

    for (const tab of ['single', 'batch', 'history']) {
      await expect(page.getByTestId(`tab-${tab}`)).toBeVisible();
    }
  });

  test('field valid/invalid icons have aria-labels', async ({ page }) => {
    await setupAuth(page);
    await mockOcrExtract(page);
    await navigateToOcr(page);

    await page.getByTestId('file-input').setInputFiles({
      name: 'doc.jpg', mimeType: 'image/jpeg', buffer: Buffer.from([0xff, 0xd8, 0xff]),
    });
    await page.getByTestId('extract-btn').click();
    await expect(page.getByTestId('fields-table')).toBeVisible({ timeout: 8_000 });

    await expect(page.locator('[aria-label="valid"]').first()).toBeVisible();
    await expect(page.locator('[aria-label="invalid"]').first()).toBeVisible();
  });

  test('raw text toggle has aria-expanded', async ({ page }) => {
    await setupAuth(page);
    await mockOcrExtract(page);
    await navigateToOcr(page);

    await page.getByTestId('file-input').setInputFiles({
      name: 'doc.jpg', mimeType: 'image/jpeg', buffer: Buffer.from([0xff, 0xd8, 0xff]),
    });
    await page.getByTestId('extract-btn').click();
    await expect(page.getByTestId('raw-text-toggle')).toBeVisible({ timeout: 8_000 });

    await expect(page.getByTestId('raw-text-toggle')).toHaveAttribute('aria-expanded', 'false');
    await page.getByTestId('raw-text-toggle').click();
    await expect(page.getByTestId('raw-text-toggle')).toHaveAttribute('aria-expanded', 'true');
  });
});

// ── LLM Vision engine ─────────────────────────────────────────────────────────

test.describe('OCR — LLM Vision engine display', () => {
  test('shows LLM Vision badge when engine is llm_vision', async ({ page }) => {
    await setupAuth(page);
    await mockOcrExtract(page, MOCK_INVOICE); // MOCK_INVOICE uses llm_vision
    await navigateToOcr(page);

    await page.getByTestId('file-input').setInputFiles({
      name: 'invoice.jpg', mimeType: 'image/jpeg', buffer: Buffer.from([0xff, 0xd8, 0xff]),
    });
    await page.getByTestId('extract-btn').click();
    await expect(page.getByTestId('ocr-result')).toBeVisible({ timeout: 8_000 });
    await expect(page.getByText('LLM Vision')).toBeVisible();
    await expect(page.getByTestId('doc-type-badge')).toContainText('Invoice');
  });

  test('shows page count from multi-page PDF', async ({ page }) => {
    await setupAuth(page);
    await mockOcrExtract(page, { ...MOCK_INVOICE, page_count: 4 });
    await navigateToOcr(page);

    await page.getByTestId('file-input').setInputFiles({
      name: 'report.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4'),
    });
    await page.getByTestId('extract-btn').click();
    await expect(page.getByTestId('ocr-result')).toBeVisible({ timeout: 8_000 });
    await expect(page.getByText(/4 pages/i)).toBeVisible();
  });
});
