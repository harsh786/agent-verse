import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { LandingPage } from './LandingPage';

// Patch IntersectionObserver before module-level code runs
class IntersectionObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
  takeRecords(): IntersectionObserverEntry[] { return []; }
  readonly root: Element | Document | null = null;
  readonly rootMargin: string = '';
  readonly thresholds: ReadonlyArray<number> = [];
  constructor(_callback: IntersectionObserverCallback, _options?: IntersectionObserverInit) {}
}
Object.defineProperty(window, 'IntersectionObserver', {
  writable: true, configurable: true,
  value: IntersectionObserverStub,
});

beforeEach(() => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('{}', { status: 200 }));
});
afterEach(() => vi.restoreAllMocks());

describe('LandingPage', () => {
  test('exports LandingPage function', () => {
    expect(LandingPage).toBeTypeOf('function');
  });

  test('renders without throwing', () => {
    expect(() => render(<MemoryRouter><LandingPage /></MemoryRouter>)).not.toThrow();
  });

  test('renders some visible content', () => {
    render(<MemoryRouter><LandingPage /></MemoryRouter>);
    // The page renders text content
    expect(document.body.textContent?.length).toBeGreaterThan(0);
  });
});
