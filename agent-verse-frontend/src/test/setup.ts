import "@testing-library/jest-dom";

// React Flow (and other DOM-measuring libraries) need ResizeObserver in jsdom.
// Provide a no-op stub when the environment does not include a real implementation.
if (typeof globalThis.ResizeObserver === 'undefined') {
  class ResizeObserverStub {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  globalThis.ResizeObserver = ResizeObserverStub as unknown as typeof ResizeObserver;
}
