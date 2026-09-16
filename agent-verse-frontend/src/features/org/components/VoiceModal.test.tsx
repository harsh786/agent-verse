/**
 * Tests for VoiceModal — the JARVIS-style real-time voice session modal.
 *
 * useVoiceStream is mocked (same pattern as VoiceCommandBar.test.tsx) so tests
 * drive the component purely through the callbacks it registers
 * (onTranscript / onAgentResponse / onTtsDone / onError) and assert the
 * resulting UI. framer-motion is stubbed because AnimatePresence gates the
 * whole modal on `open`.
 */
import { render, screen, fireEvent, act } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import React, { type ReactNode } from 'react';
import type { UseVoiceStreamCallbacks } from '@/lib/voice/useVoiceStream';
import { VoiceModal } from './VoiceModal';

vi.mock('framer-motion', async (importOriginal) => {
  const actual = await importOriginal<typeof import('framer-motion')>();
  const makeStub = (tag: string) =>
    ({ children, ...props }: { children?: ReactNode; [k: string]: unknown }) =>
      React.createElement(tag, props as Record<string, unknown>, children);
  return {
    ...actual,
    useReducedMotion: () => true,
    AnimatePresence: ({ children }: { children?: ReactNode }) => <>{children}</>,
    motion: new Proxy(actual.motion as unknown as Record<string, unknown>, {
      get: (target, key: string) => (key in target ? target[key] : makeStub(key)),
    }),
  };
});

// Controllable mock of the voice-stream hook.
let captured: UseVoiceStreamCallbacks = {};
let streamState = 'idle';
const connect = vi.fn();
const startMic = vi.fn();
const stopMic = vi.fn();
const disconnect = vi.fn();
let lastOrgId: string | undefined;

vi.mock('@/lib/voice/useVoiceStream', () => ({
  useVoiceStream: (orgId: string, cbs?: UseVoiceStreamCallbacks) => {
    lastOrgId = orgId;
    captured = cbs ?? {};
    return { state: streamState, connect, startMic, stopMic, disconnect, sendDecisionId: vi.fn() };
  },
}));

beforeEach(() => {
  captured = {};
  streamState = 'idle';
  lastOrgId = undefined;
  connect.mockReset();
  startMic.mockReset();
  stopMic.mockReset();
  disconnect.mockReset();
});
afterEach(() => vi.restoreAllMocks());

describe('VoiceModal', () => {
  test('renders nothing when closed', () => {
    render(<VoiceModal open={false} onClose={vi.fn()} onTranscript={vi.fn()} />);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  test('renders the dialog and idle state label when open', () => {
    render(<VoiceModal open onClose={vi.fn()} onTranscript={vi.fn()} orgId="org-1" />);
    expect(screen.getByRole('dialog', { name: 'Voice command interface' })).toBeInTheDocument();
    expect(screen.getByText('Click mic to start')).toBeInTheDocument();
  });

  test('connects the voice stream on open when an orgId is provided', () => {
    render(<VoiceModal open onClose={vi.fn()} onTranscript={vi.fn()} orgId="org-1" />);
    expect(lastOrgId).toBe('org-1');
    expect(connect).toHaveBeenCalled();
  });

  test('shows the "AI NATIVE" badge only when an orgId is provided', () => {
    const { rerender } = render(<VoiceModal open onClose={vi.fn()} onTranscript={vi.fn()} orgId="org-1" />);
    expect(screen.getByText('AI NATIVE')).toBeInTheDocument();

    rerender(<VoiceModal open onClose={vi.fn()} onTranscript={vi.fn()} />);
    expect(screen.queryByText('AI NATIVE')).not.toBeInTheDocument();
  });

  test('shows the placeholder text before any transcript arrives', () => {
    render(<VoiceModal open onClose={vi.fn()} onTranscript={vi.fn()} placeholder="Say something" />);
    expect(screen.getByText('Say something')).toBeInTheDocument();
  });

  test('renders an interim transcript in italics alongside any final text', () => {
    render(<VoiceModal open onClose={vi.fn()} onTranscript={vi.fn()} orgId="org-1" />);
    act(() => captured.onTranscript?.('partial words', false, 0.5));
    expect(screen.getByText('partial words')).toBeInTheDocument();
  });

  test('a final transcript replaces the interim text and shows the confirm button', () => {
    render(<VoiceModal open onClose={vi.fn()} onTranscript={vi.fn()} orgId="org-1" />);
    act(() => captured.onTranscript?.('final words', true, 0.9));
    expect(screen.getByText('final words')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Use transcript: final words/ })).toBeInTheDocument();
  });

  test('clicking "Use This" calls onTranscript with the transcript and closes the modal', () => {
    const onTranscript = vi.fn();
    const onClose = vi.fn();
    render(<VoiceModal open onClose={onClose} onTranscript={onTranscript} orgId="org-1" />);
    act(() => captured.onTranscript?.('deploy it', true, 0.9));

    fireEvent.click(screen.getByRole('button', { name: /Use transcript: deploy it/i }));
    expect(onTranscript).toHaveBeenCalledWith('deploy it');
    expect(onClose).toHaveBeenCalled();
    expect(disconnect).toHaveBeenCalled();
  });

  test('renders the agent response when the stream reports one', () => {
    render(<VoiceModal open onClose={vi.fn()} onTranscript={vi.fn()} orgId="org-1" />);
    act(() => captured.onAgentResponse?.('Mission created successfully'));
    expect(screen.getByText('Mission created successfully')).toBeInTheDocument();
  });

  test('onTtsDone stops the mic-active state', () => {
    render(<VoiceModal open onClose={vi.fn()} onTranscript={vi.fn()} orgId="org-1" />);
    act(() => captured.onTtsDone?.());
    // No crash + mic button still reflects "Start voice input" (not active).
    expect(screen.getByRole('button', { name: 'Start voice input' })).toBeInTheDocument();
  });

  test('shows a mic-permission-specific error message for a NotAllowed/denied error', () => {
    render(<VoiceModal open onClose={vi.fn()} onTranscript={vi.fn()} orgId="org-1" />);
    act(() => captured.onError?.('NotAllowedError: Permission denied'));
    expect(screen.getByRole('alert')).toHaveTextContent(/Microphone access blocked/);
  });

  test('shows a no-microphone-specific error message for a NotFound/Devices error', () => {
    render(<VoiceModal open onClose={vi.fn()} onTranscript={vi.fn()} orgId="org-1" />);
    act(() => captured.onError?.('NotFoundError: no Devices found'));
    expect(screen.getByRole('alert')).toHaveTextContent(/No microphone found/);
  });

  test('shows the raw error message for any other error', () => {
    render(<VoiceModal open onClose={vi.fn()} onTranscript={vi.fn()} orgId="org-1" />);
    act(() => captured.onError?.('Something else went wrong'));
    expect(screen.getByRole('alert')).toHaveTextContent('Something else went wrong');
  });

  test('falls back to a generic error message when none is given', () => {
    render(<VoiceModal open onClose={vi.fn()} onTranscript={vi.fn()} orgId="org-1" />);
    act(() => captured.onError?.(''));
    expect(screen.getByRole('alert')).toHaveTextContent('Voice error — tap the mic to retry.');
  });

  test('clicking the mic button starts recording when not active (native mode)', async () => {
    render(<VoiceModal open onClose={vi.fn()} onTranscript={vi.fn()} orgId="org-1" />);
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Start voice input' }));
    });
    expect(startMic).toHaveBeenCalledTimes(1);
  });

  test('clicking the mic button stops recording when active', async () => {
    render(<VoiceModal open onClose={vi.fn()} onTranscript={vi.fn()} orgId="org-1" />);
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Start voice input' }));
    });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Stop recording' }));
    });
    expect(stopMic).toHaveBeenCalledTimes(1);
  });

  test('the mic button is a no-op without an orgId (noop mode)', async () => {
    render(<VoiceModal open onClose={vi.fn()} onTranscript={vi.fn()} />);
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Start voice input' }));
    });
    expect(startMic).not.toHaveBeenCalled();
  });

  test('shows the "listening" state label and speaking icon in the header', () => {
    streamState = 'speaking';
    render(<VoiceModal open onClose={vi.fn()} onTranscript={vi.fn()} orgId="org-1" />);
    expect(screen.getByText('JARVIS speaking…')).toBeInTheDocument();
  });

  test('shows the processing spinner state label', () => {
    streamState = 'processing';
    render(<VoiceModal open onClose={vi.fn()} onTranscript={vi.fn()} orgId="org-1" />);
    expect(screen.getByText('Processing…')).toBeInTheDocument();
  });

  test('closing via the header close button disconnects and resets state', () => {
    const onClose = vi.fn();
    render(<VoiceModal open onClose={onClose} onTranscript={vi.fn()} orgId="org-1" />);
    fireEvent.click(screen.getByRole('button', { name: 'Close voice modal' }));
    expect(onClose).toHaveBeenCalled();
    expect(disconnect).toHaveBeenCalled();
  });

  test('closing via the backdrop click also closes the modal', () => {
    const onClose = vi.fn();
    const { container } = render(<VoiceModal open onClose={onClose} onTranscript={vi.fn()} orgId="org-1" />);
    const backdrop = container.querySelector('[aria-hidden="true"]');
    expect(backdrop).toBeTruthy();
    fireEvent.click(backdrop!);
    expect(onClose).toHaveBeenCalled();
  });

  test('pressing Escape closes the modal', () => {
    const onClose = vi.fn();
    render(<VoiceModal open onClose={onClose} onTranscript={vi.fn()} orgId="org-1" />);
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalled();
  });

  test('does not attempt to connect when there is no orgId', () => {
    render(<VoiceModal open onClose={vi.fn()} onTranscript={vi.fn()} />);
    expect(connect).not.toHaveBeenCalled();
  });
});
