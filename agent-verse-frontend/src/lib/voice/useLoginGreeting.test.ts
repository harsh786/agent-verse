/**
 * Tests for useLoginGreeting — fetches the org login greeting WAV (React Query)
 * and auto-plays it once via an <audio> element after an 800ms delay.
 *
 * jsdom has no real audio pipeline, so we install a tiny fake Audio transport
 * on globalThis that records play()/pause() and lets tests fire onplay/onended/
 * onerror. voiceApi.greeting is mocked so no network is touched. This is a
 * transport double, NOT a source change. Real timers are used throughout
 * (with a generous waitFor timeout) so the hook's own 800ms setTimeout runs
 * unmodified.
 */
import { renderHook, waitFor, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import React from 'react';
import { useLoginGreeting } from './useLoginGreeting';

const { greetingMock } = vi.hoisted(() => ({ greetingMock: vi.fn() }));
vi.mock('@/features/org/api/voice', () => ({ voiceApi: { greeting: greetingMock } }));

class FakeAudio {
  static instances: FakeAudio[] = [];
  src: string;
  onplay: (() => void) | null = null;
  onpause: (() => void) | null = null;
  onended: (() => void) | null = null;
  onerror: (() => void) | null = null;
  paused = false;
  play = vi.fn().mockResolvedValue(undefined);
  pause = vi.fn(() => {
    this.paused = true;
    this.onpause?.();
  });

  constructor(src: string) {
    this.src = src;
    FakeAudio.instances.push(this);
  }
  static latest() {
    return FakeAudio.instances[FakeAudio.instances.length - 1];
  }
}

const OriginalAudio = globalThis.Audio;
const OriginalMatchMedia = window.matchMedia;

function stubReducedMotion(matches: boolean) {
  window.matchMedia = ((query: string) => ({
    matches,
    media: query,
    onchange: null,
    addListener: () => undefined,
    removeListener: () => undefined,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
}

beforeEach(() => {
  FakeAudio.instances = [];
  greetingMock.mockReset();
  greetingMock.mockResolvedValue(new Blob(['wav']));
  (globalThis as unknown as { Audio: unknown }).Audio = FakeAudio;
  vi.stubGlobal('URL', { ...URL, createObjectURL: () => 'blob:greeting', revokeObjectURL: vi.fn() });
  stubReducedMotion(false);
});
afterEach(() => {
  (globalThis as unknown as { Audio: unknown }).Audio = OriginalAudio;
  window.matchMedia = OriginalMatchMedia;
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function wrapper({ children }: { children: React.ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return React.createElement(QueryClientProvider, { client: qc }, children);
}

describe('useLoginGreeting', () => {
  test('fetches the greeting, waits ~800ms, then plays it and marks hasPlayed on end', async () => {
    const { result } = renderHook(
      () => useLoginGreeting({ orgId: 'org-1', userName: 'Ada' }),
      { wrapper },
    );

    await waitFor(() => expect(greetingMock).toHaveBeenCalledWith('org-1', { user_name: 'Ada', language: 'en' }));
    await waitFor(() => expect(FakeAudio.instances).toHaveLength(1));
    const audio = FakeAudio.latest();

    // The hook defers play() by 800ms — give it real time to fire.
    await waitFor(() => expect(audio.play).toHaveBeenCalledTimes(1), { timeout: 2000 });

    act(() => audio.onplay?.());
    expect(result.current.isPlaying).toBe(true);

    act(() => audio.onended?.());
    expect(result.current.isPlaying).toBe(false);
    expect(result.current.hasPlayed).toBe(true);
  });

  test('does not fetch when enabled is false', async () => {
    renderHook(() => useLoginGreeting({ orgId: 'org-1', userName: 'Ada', enabled: false }), { wrapper });
    await act(async () => { await Promise.resolve(); });
    expect(greetingMock).not.toHaveBeenCalled();
  });

  test('does not fetch when prefers-reduced-motion is set', async () => {
    stubReducedMotion(true);
    renderHook(() => useLoginGreeting({ orgId: 'org-1', userName: 'Ada' }), { wrapper });
    await act(async () => { await Promise.resolve(); });
    expect(greetingMock).not.toHaveBeenCalled();
  });

  test('does not fetch when orgId is empty', async () => {
    renderHook(() => useLoginGreeting({ orgId: '', userName: 'Ada' }), { wrapper });
    await act(async () => { await Promise.resolve(); });
    expect(greetingMock).not.toHaveBeenCalled();
  });

  test('passes a custom language through to voiceApi.greeting', async () => {
    renderHook(() => useLoginGreeting({ orgId: 'org-2', userName: 'Ada', language: 'fr' }), { wrapper });
    await waitFor(() => expect(greetingMock).toHaveBeenCalledWith('org-2', { user_name: 'Ada', language: 'fr' }));
  });

  test('onerror clears isPlaying without throwing', async () => {
    const { result } = renderHook(() => useLoginGreeting({ orgId: 'org-3', userName: 'Ada' }), { wrapper });
    await waitFor(() => expect(FakeAudio.instances).toHaveLength(1));
    const audio = FakeAudio.latest();
    await waitFor(() => expect(audio.play).toHaveBeenCalledTimes(1), { timeout: 2000 });
    act(() => audio.onplay?.());
    expect(result.current.isPlaying).toBe(true);

    expect(() => act(() => audio.onerror?.())).not.toThrow();
    expect(result.current.isPlaying).toBe(false);
  });

  test('stop() pauses the audio and clears isPlaying', async () => {
    const { result } = renderHook(() => useLoginGreeting({ orgId: 'org-4', userName: 'Ada' }), { wrapper });
    await waitFor(() => expect(FakeAudio.instances).toHaveLength(1));
    const audio = FakeAudio.latest();
    await waitFor(() => expect(audio.play).toHaveBeenCalledTimes(1), { timeout: 2000 });
    act(() => audio.onplay?.());
    expect(result.current.isPlaying).toBe(true);

    act(() => result.current.stop());
    expect(audio.pause).toHaveBeenCalled();
    expect(result.current.isPlaying).toBe(false);
  });

  test('stop() is safe to call before any audio element exists', () => {
    const { result } = renderHook(() => useLoginGreeting({ orgId: '', userName: 'Ada' }), { wrapper });
    expect(() => act(() => result.current.stop())).not.toThrow();
  });

  test('resetAndReplay clears the session flag and hasPlayed', async () => {
    const { result } = renderHook(() => useLoginGreeting({ orgId: 'org-5', userName: 'Ada' }), { wrapper });
    await waitFor(() => expect(FakeAudio.instances).toHaveLength(1));
    const audio = FakeAudio.latest();
    await waitFor(() => expect(audio.play).toHaveBeenCalledTimes(1), { timeout: 2000 });
    act(() => audio.onended?.());
    expect(result.current.hasPlayed).toBe(true);

    sessionStorage.setItem('av:greeting-played:org-5', '1');
    act(() => result.current.resetAndReplay());
    expect(result.current.hasPlayed).toBe(false);
    expect(sessionStorage.getItem('av:greeting-played:org-5')).toBeNull();
  });
});
