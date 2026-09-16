/**
 * Tests for useGreeting — fetches the org greeting WAV (React Query) and
 * auto-plays it once via an <audio> element.
 *
 * jsdom has no real audio pipeline, so we install a tiny fake Audio transport
 * on globalThis that records play()/pause() and lets tests fire onended/onerror.
 * voiceApi.greeting is mocked so no network is touched. This is a transport
 * double, NOT a source change.
 */
import { renderHook, waitFor, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import React from 'react';
import { useGreeting } from './useGreeting';

const { greetingMock } = vi.hoisted(() => ({ greetingMock: vi.fn() }));
vi.mock('@/features/org/api/voice', () => ({ voiceApi: { greeting: greetingMock } }));

class FakeAudio {
  static instances: FakeAudio[] = [];
  src: string;
  onended: (() => void) | null = null;
  onerror: (() => void) | null = null;
  paused = false;
  play = vi.fn().mockResolvedValue(undefined);
  pause = vi.fn(() => { this.paused = true; });

  constructor(src: string) {
    this.src = src;
    FakeAudio.instances.push(this);
  }
  static latest() { return FakeAudio.instances[FakeAudio.instances.length - 1]; }
}

const OriginalAudio = globalThis.Audio;

beforeEach(() => {
  FakeAudio.instances = [];
  greetingMock.mockReset();
  greetingMock.mockResolvedValue(new Blob(['wav']));
  (globalThis as unknown as { Audio: unknown }).Audio = FakeAudio;
  vi.stubGlobal('URL', { ...URL, createObjectURL: () => 'blob:greeting', revokeObjectURL: vi.fn() });
});
afterEach(() => {
  (globalThis as unknown as { Audio: unknown }).Audio = OriginalAudio;
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function wrapper({ children }: { children: React.ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return React.createElement(QueryClientProvider, { client: qc }, children);
}

describe('useGreeting', () => {
  test('fetches the greeting and auto-plays it once when the WAV arrives', async () => {
    const { result } = renderHook(() => useGreeting('org-1', 'Ada'), { wrapper });

    await waitFor(() => expect(result.current.isPlaying).toBe(true));
    expect(greetingMock).toHaveBeenCalledWith('org-1', { user_name: 'Ada' });
    const audio = FakeAudio.latest();
    expect(audio.play).toHaveBeenCalledTimes(1);
    expect(result.current.hasPlayed).toBe(true);
  });

  test('does not fetch or play when orgId is null (query disabled)', async () => {
    const { result } = renderHook(() => useGreeting(null), { wrapper });
    // Give React Query a tick to (not) run.
    await act(async () => { await Promise.resolve(); });
    expect(greetingMock).not.toHaveBeenCalled();
    expect(FakeAudio.instances).toHaveLength(0);
    expect(result.current.isPlaying).toBe(false);
  });

  test('clears isPlaying when playback ends', async () => {
    const { result } = renderHook(() => useGreeting('org-2', 'Ada'), { wrapper });
    await waitFor(() => expect(result.current.isPlaying).toBe(true));

    act(() => FakeAudio.latest().onended?.());
    expect(result.current.isPlaying).toBe(false);
  });

  test('onerror clears the playing state without throwing', async () => {
    const { result } = renderHook(() => useGreeting('org-3', 'Ada'), { wrapper });
    await waitFor(() => expect(result.current.isPlaying).toBe(true));

    act(() => FakeAudio.latest().onerror?.());
    expect(result.current.isPlaying).toBe(false);
  });

  test('stop() pauses the audio and clears isPlaying', async () => {
    const { result } = renderHook(() => useGreeting('org-4', 'Ada'), { wrapper });
    await waitFor(() => expect(result.current.isPlaying).toBe(true));
    const audio = FakeAudio.latest();

    act(() => result.current.stop());
    expect(audio.pause).toHaveBeenCalled();
    expect(result.current.isPlaying).toBe(false);
  });

  test('play() is a no-op after the greeting has already played', async () => {
    const { result } = renderHook(() => useGreeting('org-5', 'Ada'), { wrapper });
    await waitFor(() => expect(result.current.hasPlayed).toBe(true));
    const countAfterFirst = FakeAudio.instances.length;

    await act(async () => { await result.current.play(); });
    // hasPlayed guard prevents a second <audio> from being created.
    expect(FakeAudio.instances.length).toBe(countAfterFirst);
  });
});
