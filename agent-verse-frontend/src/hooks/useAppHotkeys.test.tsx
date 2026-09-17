import { renderHook, act } from '@testing-library/react';
import { describe, test, expect, vi, beforeEach } from 'vitest';
import type { ReactNode } from 'react';
import { MemoryRouter } from 'react-router-dom';
import { useAppHotkeys } from './useAppHotkeys';

const handlers: Record<string, () => void> = {};

vi.mock('react-hotkeys-hook', () => ({
  useHotkeys: (key: string, cb: () => void) => {
    handlers[key] = cb;
  },
}));

const navigateMock = vi.fn();
vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>();
  return { ...actual, useNavigate: () => navigateMock };
});

function wrapper({ children }: { children: ReactNode }) {
  return <MemoryRouter>{children}</MemoryRouter>;
}

beforeEach(() => {
  navigateMock.mockClear();
  for (const k of Object.keys(handlers)) delete handlers[k];
});

describe('useAppHotkeys', () => {
  test('registers a handler for every documented shortcut', () => {
    renderHook(() => useAppHotkeys(), { wrapper });
    for (const key of ['g+d', 'g+g', 'g+a', 'g+t', 'g+k', 'g+r', 'g+o', 'shift+/', 'escape']) {
      expect(handlers[key]).toBeTypeOf('function');
    }
  });

  test.each([
    ['g+d', '/dashboard'],
    ['g+g', '/goals'],
    ['g+a', '/agents'],
    ['g+t', '/templates'],
    ['g+k', '/knowledge'],
    ['g+r', '/analytics'],
    ['g+o', '/observability'],
  ])('%s navigates to %s', (key, path) => {
    renderHook(() => useAppHotkeys(), { wrapper });
    handlers[key]();
    expect(navigateMock).toHaveBeenCalledWith(path);
  });

  test('shift+/ toggles showHelp and escape closes it', () => {
    const { result } = renderHook(() => useAppHotkeys(), { wrapper });
    expect(result.current.showHelp).toBe(false);

    act(() => handlers['shift+/']());
    expect(result.current.showHelp).toBe(true);

    act(() => handlers['shift+/']());
    expect(result.current.showHelp).toBe(false);

    act(() => handlers['shift+/']());
    expect(result.current.showHelp).toBe(true);
    act(() => handlers.escape());
    expect(result.current.showHelp).toBe(false);
  });

  test('setShowHelp is exposed and settable directly', () => {
    const { result } = renderHook(() => useAppHotkeys(), { wrapper });
    act(() => result.current.setShowHelp(true));
    expect(result.current.showHelp).toBe(true);
  });
});
