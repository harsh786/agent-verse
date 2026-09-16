/**
 * Tests for VoiceGoalInput — dictate a goal via the Web Speech API.
 *
 * jsdom has no SpeechRecognition, so we install a tiny fake constructor on
 * window that records start()/stop() and lets tests fire onstart/onresult/
 * onend/onerror with the exact `results[0][0].transcript` shape the component
 * reads. This is a transport double, NOT a source change.
 */
import { render, screen, fireEvent, act } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { VoiceGoalInput } from './VoiceGoalInput';

class FakeSpeechRecognition {
  static instances: FakeSpeechRecognition[] = [];
  lang = '';
  interimResults = false;
  maxAlternatives = 1;
  continuous = false;
  onstart: (() => void) | null = null;
  onend: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onresult: ((e: unknown) => void) | null = null;
  start = vi.fn(() => { this.onstart?.(); });
  stop = vi.fn();

  constructor() { FakeSpeechRecognition.instances.push(this); }
  fireResult(transcript: string) {
    this.onresult?.({ results: [[{ transcript, confidence: 1 }]], resultIndex: 0 });
  }
  static latest() { return FakeSpeechRecognition.instances[FakeSpeechRecognition.instances.length - 1]; }
}

const w = window as unknown as Record<string, unknown>;

beforeEach(() => {
  FakeSpeechRecognition.instances = [];
  w.SpeechRecognition = FakeSpeechRecognition;
  w.webkitSpeechRecognition = FakeSpeechRecognition;
});
afterEach(() => {
  delete w.SpeechRecognition;
  delete w.webkitSpeechRecognition;
  vi.restoreAllMocks();
});

describe('VoiceGoalInput', () => {
  test('renders nothing when speech recognition is unsupported', () => {
    delete w.SpeechRecognition;
    delete w.webkitSpeechRecognition;
    const { container } = render(<VoiceGoalInput onTranscript={vi.fn()} />);
    expect(container.firstChild).toBeNull();
  });

  test('renders the mic button in the idle state when supported', () => {
    render(<VoiceGoalInput onTranscript={vi.fn()} />);
    expect(screen.getByRole('button', { name: 'Start voice input' })).toBeInTheDocument();
  });

  test('clicking starts recognition and shows the listening state', () => {
    render(<VoiceGoalInput onTranscript={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Start voice input' }));

    expect(FakeSpeechRecognition.latest().start).toHaveBeenCalledTimes(1);
    expect(FakeSpeechRecognition.latest().lang).toBe('en-US');
    expect(screen.getByRole('button', { name: 'Stop listening' })).toBeInTheDocument();
  });

  test('a recognition result forwards the transcript to onTranscript', () => {
    vi.useFakeTimers();
    try {
      const onTranscript = vi.fn();
      render(<VoiceGoalInput onTranscript={onTranscript} />);
      fireEvent.click(screen.getByRole('button', { name: 'Start voice input' }));

      act(() => FakeSpeechRecognition.latest().fireResult('deploy the release'));
      expect(onTranscript).toHaveBeenCalledWith('deploy the release');

      // After a 300ms settle the control returns to idle.
      act(() => vi.advanceTimersByTime(300));
      expect(screen.getByRole('button', { name: 'Start voice input' })).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  test('an empty transcript does not call onTranscript', () => {
    vi.useFakeTimers();
    try {
      const onTranscript = vi.fn();
      render(<VoiceGoalInput onTranscript={onTranscript} />);
      fireEvent.click(screen.getByRole('button', { name: 'Start voice input' }));
      act(() => FakeSpeechRecognition.latest().fireResult(''));
      expect(onTranscript).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  test('clicking while listening stops recognition and returns to idle', () => {
    render(<VoiceGoalInput onTranscript={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Start voice input' }));
    fireEvent.click(screen.getByRole('button', { name: 'Stop listening' }));

    expect(FakeSpeechRecognition.latest().stop).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('button', { name: 'Start voice input' })).toBeInTheDocument();
  });

  test('onend / onerror reset the control to idle', () => {
    render(<VoiceGoalInput onTranscript={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Start voice input' }));
    act(() => FakeSpeechRecognition.latest().onerror?.());
    expect(screen.getByRole('button', { name: 'Start voice input' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Start voice input' }));
    act(() => FakeSpeechRecognition.latest().onend?.());
    expect(screen.getByRole('button', { name: 'Start voice input' })).toBeInTheDocument();
  });

  test('the disabled prop disables the button', () => {
    render(<VoiceGoalInput onTranscript={vi.fn()} disabled />);
    expect(screen.getByRole('button', { name: 'Start voice input' })).toBeDisabled();
  });
});
