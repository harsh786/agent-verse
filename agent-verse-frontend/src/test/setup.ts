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

// jsdom intentionally omits layout and browser preference APIs used by the UI.
// Keep these deterministic so component tests exercise application behaviour
// instead of crashing on missing browser primitives.
if (typeof window.matchMedia === "undefined") {
  window.matchMedia = (query: string): MediaQueryList => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => undefined,
    removeListener: () => undefined,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    dispatchEvent: () => false,
  });
}

if (typeof Element.prototype.scrollIntoView === "undefined") {
  Element.prototype.scrollIntoView = () => undefined;
}
