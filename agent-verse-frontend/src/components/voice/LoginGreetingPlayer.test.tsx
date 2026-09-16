/**
 * Tests for LoginGreetingPlayer — the ambient "brief is speaking" indicator.
 *
 * The audio pipeline lives in useLoginGreeting, which is mocked here so the
 * tests focus on this component's own rendering: it shows the waveform + mute
 * control only while playing, wires the mute button to stop(), and forwards its
 * props to the hook. framer-motion is stubbed (AnimatePresence gates render).
 */
import { render, screen, fireEvent } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import React, { type ReactNode } from 'react';
import { LoginGreetingPlayer } from './LoginGreetingPlayer';
import { useLoginGreeting } from '@/lib/voice/useLoginGreeting';

vi.mock('framer-motion', async (importOriginal) => {
  const actual = await importOriginal<typeof import('framer-motion')>();
  const makeStub = (tag: string) =>
    ({ children, ...props }: { children?: ReactNode; [k: string]: unknown }) =>
      React.createElement(tag, props as Record<string, unknown>, children);
  return {
    ...actual,
    AnimatePresence: ({ children }: { children?: ReactNode }) => <>{children}</>,
    motion: new Proxy(actual.motion as unknown as Record<string, unknown>, {
      get: (target, key: string) => (key in target ? target[key] : makeStub(key)),
    }),
  };
});

vi.mock('@/lib/voice/useLoginGreeting', () => ({ useLoginGreeting: vi.fn() }));
const mockedHook = vi.mocked(useLoginGreeting);

const stop = vi.fn();
function setHook(isPlaying: boolean) {
  mockedHook.mockReturnValue({ isPlaying, hasPlayed: false, stop, resetAndReplay: vi.fn() });
}

beforeEach(() => { stop.mockReset(); mockedHook.mockReset(); });
afterEach(() => vi.restoreAllMocks());

describe('LoginGreetingPlayer', () => {
  test('renders the speaking indicator with a mute control while playing', () => {
    setHook(true);
    render(<LoginGreetingPlayer orgId="org-1" />);
    expect(screen.getByRole('status')).toBeInTheDocument();
    expect(screen.getByText('BRIEF')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Mute daily brief' })).toBeInTheDocument();
  });

  test('renders nothing when not playing', () => {
    setHook(false);
    const { container } = render(<LoginGreetingPlayer orgId="org-1" />);
    expect(container.firstChild).toBeNull();
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  test('clicking the mute button calls stop()', () => {
    setHook(true);
    render(<LoginGreetingPlayer orgId="org-1" />);
    fireEvent.click(screen.getByRole('button', { name: 'Mute daily brief' }));
    expect(stop).toHaveBeenCalledTimes(1);
  });

  test('forwards orgId, userName and language to useLoginGreeting', () => {
    setHook(false);
    render(<LoginGreetingPlayer orgId="org-42" userName="Ada" language="es" />);
    expect(mockedHook).toHaveBeenCalledWith({ orgId: 'org-42', userName: 'Ada', language: 'es' });
  });

  test('defaults userName to "there" and language to "en" when omitted', () => {
    setHook(false);
    render(<LoginGreetingPlayer orgId="org-7" />);
    expect(mockedHook).toHaveBeenCalledWith({ orgId: 'org-7', userName: 'there', language: 'en' });
  });
});
