import { afterEach, expect, test, vi } from 'vitest';
import {
  ApiError, LLM_BUDGET_EXHAUSTED_MESSAGE, apiFetch, isLlmBudgetExhausted, llmErrorMessage,
} from '@/lib/api/client';

afterEach(() => vi.restoreAllMocks());

function mockStatus(status: number, body: unknown) {
  return vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }),
  );
}

test('a 429 llm_budget_exhausted response is recognised and explained', async () => {
  mockStatus(429, { detail: 'LLM budget exhausted: tenant daily LLM budget exhausted', code: 'llm_budget_exhausted' });
  const err = await apiFetch('/insights/query', { method: 'POST' }).catch((e: unknown) => e);
  expect(err).toBeInstanceOf(ApiError);
  expect(isLlmBudgetExhausted(err)).toBe(true);
  expect(llmErrorMessage(err, 'Query failed')).toBe(LLM_BUDGET_EXHAUSTED_MESSAGE);
});

test('a plain rate-limit 429 is not mistaken for budget exhaustion', async () => {
  mockStatus(429, { detail: 'Too many requests' });
  const err = await apiFetch('/knowledge/search?q=x').catch((e: unknown) => e);
  expect(isLlmBudgetExhausted(err)).toBe(false);
  expect(llmErrorMessage(err, 'Search failed')).toBe('Too many requests');
});

test('other errors fall back to their message, then the default', () => {
  expect(llmErrorMessage(new ApiError(502, ''), 'Search failed')).toBe('Search failed (502)');
  expect(llmErrorMessage(new Error('boom'), 'Search failed')).toBe('boom');
});
