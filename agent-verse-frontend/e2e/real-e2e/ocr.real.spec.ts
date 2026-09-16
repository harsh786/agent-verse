/**
 * Real E2E tests for the OCR feature — NO HTTP mocking.
 *
 * Route:   /ocr                    (src/features/ocr/OcrPage.tsx)
 * Backend: POST /ocr/extract       (single doc, multipart or base64 JSON)
 *          POST /ocr/batch         (up to 10 docs, base64 JSON)
 *          (src/app/api/ocr.py, prefix "/ocr")
 *
 * Every HTTP request goes through:
 *   browser → localhost:5173 (Vite) → localhost:8000 (FastAPI) → Postgres+Redis
 *
 * Tenant signup is capped at 10/IP/hour on the backend (app/api/tenants.py) and
 * that IP is shared by every real-e2e worker/file running concurrently, so this
 * file creates exactly ONE tenant (in beforeAll) and reuses it for every test.
 */

import { expect, test as base } from '@playwright/test';
import { createE2ETenant, loginFrontend, apiClient, FRONTEND_BASE, type E2ETenant } from './fixtures';

// A minimal valid 1x1 transparent PNG, base64-encoded.
const TINY_PNG_BASE64 =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=';

let tenant: E2ETenant;
let api: ReturnType<typeof apiClient>;

base.beforeAll(async ({ playwright }) => {
  const ctx = await playwright.request.newContext();
  tenant = await createE2ETenant(ctx, `-ocr-${Math.random().toString(36).slice(2, 7)}`);
  api = apiClient(ctx, tenant);
});

base.describe('OCR — real extraction', () => {
  base('POST /ocr/extract returns a real extraction result for a real image', async () => {
    const resp = await api.post('/ocr/extract', {
      image_base64: TINY_PNG_BASE64,
      filename: 'test.png',
    });
    expect(resp.status()).toBe(200);
    const body = await resp.json();
    expect(body).toHaveProperty('raw_text');
    expect(body).toHaveProperty('document_type');
    expect(body).toHaveProperty('fields');
    expect(body).toHaveProperty('engine_used');
    expect(body).toHaveProperty('overall_confidence');
    expect(body).toHaveProperty('page_count');
    expect(typeof body.overall_confidence).toBe('number');
  });

  base('POST /ocr/extract without an image or PDF returns 422', async () => {
    const resp = await api.post('/ocr/extract', {});
    expect(resp.status()).toBe(422);
  });

  base('POST /ocr/batch processes multiple documents concurrently', async () => {
    const resp = await api.post('/ocr/batch', {
      documents: [
        { image_base64: TINY_PNG_BASE64, filename: 'a.png' },
        { image_base64: TINY_PNG_BASE64, filename: 'b.png' },
      ],
    });
    expect(resp.status()).toBe(200);
    const body = await resp.json();
    expect(body.total).toBe(2);
    expect(body.succeeded + body.failed).toBe(2);
    expect(Array.isArray(body.results)).toBe(true);
    expect(body.results).toHaveLength(2);
  });

  base('ocr page renders the extraction UI without a critical error', async ({ page }) => {
    await loginFrontend(page, tenant);
    await page.goto(`${FRONTEND_BASE}/ocr`, { waitUntil: 'networkidle' });
    await expect(page.locator('body')).toBeVisible();
    const text = await page.locator('body').textContent();
    expect(text!.length).toBeGreaterThan(0);
    expect(text!.toLowerCase()).not.toContain('internal server error');
    await expect(page.getByText(/OCR/i).first()).toBeVisible();
  });
});
