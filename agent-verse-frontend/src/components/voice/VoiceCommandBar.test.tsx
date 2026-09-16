/**
 * Tests for VoiceCommandBar — the org command-center voice UI.
 *
 * useVoiceStream is mocked so the tests drive the component's own state via the
 * callbacks it registers (onTranscript / onAgentResponse / onError) and assert
 * the resulting UI (transcript line, intent badges, agent response) plus the
 * mic-toggle wiring. framer-motion is stubbed because AnimatePresence gates the
 * transcript / badges / response.
 */
import { render, screen, fireEvent, act } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import React, { type ReactNode } from 'react';
import type { UseVoiceStreamCallbacks } from '@/lib/voice/useVoiceStream';
import { VoiceCommandBar } from './VoiceCommandBar';

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

// Controllable mock of the voice-stream hook.
let captured: UseVoiceStreamCallbacks = {};
let streamState = 'idle';
const startMic = vi.fn();
const stopMic = vi.fn();
const disconnect = vi.fn();

vi.mock('@/lib/voice/useVoiceStream', () => ({
  useVoiceStream: (_orgId: string, cbs?: UseVoiceStreamCallbacks) => {
    captured = cbs ?? {};
    return { state: streamState, connect: vi.fn(), startMic, stopMic, disconnect, sendDecisionId: vi.fn() };
  },
}));

beforeEach(() => {
  captured = {};
  streamState = 'idle';
  startMic.mockReset();
  stopMic.mockReset();
  disconnect.mockReset();
});
afterEach(() => vi.restoreAllMocks());

describe('VoiceCommandBar', () => {
  test('shows the idle prompt and starts the mic on click', async () => {
    render(<VoiceCommandBar orgId="org-1" />);
    expect(screen.getByText('Click mic to start')).toBeInTheDocument();

    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Start voice input' })); });
    expect(startMic).toHaveBeenCalledTimes(1);
  });

  test('a final transcript is rendered with the confirmed marker', () => {
    render(<VoiceCommandBar orgId="org-1" />);
    act(() => captured.onTranscript?.('deploy the app', true, 0.9));
    expect(screen.getByText(/✓\s*deploy the app/)).toBeInTheDocument();
  });

  test('a low-confidence final transcript is annotated', () => {
    render(<VoiceCommandBar orgId="org-1" />);
    act(() => captured.onTranscript?.('mumble', true, 0.2));
    expect(screen.getByText(/mumble \(low confidence\)/)).toBeInTheDocument();
  });

  test('a "Mission created" response shows the MISSION badge and fires onMissionCreated', () => {
    const onMissionCreated = vi.fn();
    render(<VoiceCommandBar orgId="org-1" onMissionCreated={onMissionCreated} />);
    act(() => captured.onAgentResponse?.('Mission created: launch campaign'));

    expect(screen.getByText('MISSION')).toBeInTheDocument();
    expect(screen.getByText('Mission created: launch campaign')).toBeInTheDocument();
    expect(onMissionCreated).toHaveBeenCalledWith('Mission created: launch campaign');
  });

  test('an "Approved" response shows the APPROVED badge', () => {
    render(<VoiceCommandBar orgId="org-1" />);
    act(() => captured.onAgentResponse?.('Approved the pending decision'));
    expect(screen.getByText('APPROVED')).toBeInTheDocument();
  });

  test('an error is surfaced in the response area', () => {
    render(<VoiceCommandBar orgId="org-1" />);
    act(() => captured.onError?.('mic blocked'));
    expect(screen.getByText('Error: mic blocked')).toBeInTheDocument();
  });

  test('onSessionEnd clears the transcript and response', () => {
    render(<VoiceCommandBar orgId="org-1" />);
    act(() => captured.onTranscript?.('hello', true, 0.9));
    expect(screen.getByText(/hello/)).toBeInTheDocument();

    act(() => captured.onSessionEnd?.());
    expect(screen.queryByText(/hello/)).not.toBeInTheDocument();
  });

  test('while listening, clicking the mic stops it', async () => {
    streamState = 'listening';
    render(<VoiceCommandBar orgId="org-1" />);
    expect(screen.getByText('Listening...')).toBeInTheDocument();

    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Stop voice input' })); });
    expect(stopMic).toHaveBeenCalledTimes(1);
  });
});
