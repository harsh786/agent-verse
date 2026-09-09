import "@testing-library/jest-dom";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";
import "@/lib/i18n";

afterEach(() => cleanup());

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

// CodeMirror measures DOM ranges; jsdom does not implement these layout APIs.
if (typeof Range.prototype.getClientRects === "undefined") {
  Range.prototype.getClientRects = () => [] as unknown as DOMRectList;
}
if (typeof Range.prototype.getBoundingClientRect === "undefined") {
  Range.prototype.getBoundingClientRect = () => new DOMRect();
}

// jsdom in some environments does not expose Web Storage; many hooks/components
// (auth token, theme, drafts) call localStorage/sessionStorage and would crash
// with "Cannot read properties of undefined (reading 'getItem')". Provide a
// deterministic in-memory implementation when one is missing.
function _memoryStorage(): Storage {
  const store = new Map<string, string>();
  return {
    getItem: (k: string) => (store.has(k) ? store.get(k)! : null),
    setItem: (k: string, v: string) => void store.set(k, String(v)),
    removeItem: (k: string) => void store.delete(k),
    clear: () => store.clear(),
    key: (i: number) => Array.from(store.keys())[i] ?? null,
    get length() {
      return store.size;
    },
  } as Storage;
}

for (const name of ["localStorage", "sessionStorage"] as const) {
  const existing = (globalThis as Record<string, unknown>)[name] as Storage | undefined;
  if (!existing || typeof existing.getItem !== "function") {
    Object.defineProperty(globalThis, name, {
      value: _memoryStorage(),
      configurable: true,
      writable: true,
    });
  }
}
