import { act, render } from '@testing-library/react';
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

// A variant that fires "isIntersecting: true" synchronously on observe(), so
// components using useReveal() flip `on` to true and any interval/animation
// gated behind it (e.g. PipelineDemo's step cycler) actually starts.
class TriggeringIntersectionObserver {
  private callback: IntersectionObserverCallback;
  constructor(callback: IntersectionObserverCallback) {
    this.callback = callback;
  }
  observe(target: Element) {
    this.callback(
      [{ isIntersecting: true, target } as IntersectionObserverEntry],
      this as unknown as IntersectionObserver
    );
  }
  unobserve() {}
  disconnect() {}
  takeRecords(): IntersectionObserverEntry[] { return []; }
}

const originalIntersectionObserver = IntersectionObserverStub;

function resetIntersectionObserverStub() {
  Object.defineProperty(window, 'IntersectionObserver', {
    writable: true, configurable: true,
    value: originalIntersectionObserver,
  });
}

resetIntersectionObserverStub();

beforeEach(() => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('{}', { status: 200 }));
});
afterEach(() => {
  vi.restoreAllMocks();
  resetIntersectionObserverStub();
});

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

  test('cycles the pipeline demo through active/completed/pending step states', () => {
    Object.defineProperty(window, 'IntersectionObserver', {
      writable: true, configurable: true,
      value: TriggeringIntersectionObserver,
    });
    vi.useFakeTimers({ toFake: ['setTimeout', 'setInterval', 'clearInterval', 'clearTimeout'] });

    try {
      render(<MemoryRouter><LandingPage /></MemoryRouter>);

      // Goal line renders (PipelineDemo header content, present regardless of active step).
      expect(document.body.textContent).toContain('agent execution pipeline');

      // First tick: active = 0 -> the "Plan" step is the current (active === i) step,
      // which renders its detail tooltip ("LLM decomposes goal → ordered steps").
      act(() => {
        vi.advanceTimersByTime(900);
      });
      expect(document.body.textContent).toContain('LLM decomposes goal');

      // Second tick: active = 1 -> step 0 ("Plan") is now in the "completed" (active > i)
      // state (checkmark + emerald styling), and step 1 ("Memory recall") becomes the
      // current active step, showing its own detail text.
      act(() => {
        vi.advanceTimersByTime(900);
      });
      expect(document.body.textContent).toContain('Past plans & failures injected');
      // The "Plan" step's own detail tooltip should no longer be shown once it is
      // no longer the active step (active > i, not active === i).
      expect(document.body.textContent).not.toContain('LLM decomposes goal');
    } finally {
      vi.useRealTimers();
    }
  });
});
