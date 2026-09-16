import { renderHook, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { useTypingIndicator } from './useTypingIndicator';

describe('useTypingIndicator', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('shows typing immediately when isStreaming is true', () => {
    const { result } = renderHook(() => useTypingIndicator(true));
    expect(result.current).toBe(true);
  });

  it('starts hidden when isStreaming is false from the outset', () => {
    const { result } = renderHook(() => useTypingIndicator(false));
    expect(result.current).toBe(false);
  });

  it('keeps showing typing for 300ms after streaming ends, then hides it', () => {
    const { result, rerender } = renderHook(({ isStreaming }) => useTypingIndicator(isStreaming), {
      initialProps: { isStreaming: true },
    });
    expect(result.current).toBe(true);

    rerender({ isStreaming: false });
    // Still true immediately after streaming ends — the hide is debounced.
    expect(result.current).toBe(true);

    act(() => {
      vi.advanceTimersByTime(299);
    });
    expect(result.current).toBe(true);

    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(result.current).toBe(false);
  });

  it('clears the pending hide timeout if streaming resumes before it fires', () => {
    const { result, rerender } = renderHook(({ isStreaming }) => useTypingIndicator(isStreaming), {
      initialProps: { isStreaming: true },
    });

    rerender({ isStreaming: false });
    act(() => {
      vi.advanceTimersByTime(150);
    });
    rerender({ isStreaming: true });
    // Streaming resumed: indicator stays visible and the old timeout shouldn't fire.
    act(() => {
      vi.advanceTimersByTime(300);
    });
    expect(result.current).toBe(true);
  });

  it('clears the timeout on unmount without throwing', () => {
    const { rerender, unmount } = renderHook(({ isStreaming }) => useTypingIndicator(isStreaming), {
      initialProps: { isStreaming: true },
    });
    rerender({ isStreaming: false });
    expect(() => unmount()).not.toThrow();
  });
});
