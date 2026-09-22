/**
 * useJarvisSpeech — WS-7 item 4. Verifies TTS fires exactly once per
 * speakable event when enabled, and never fires when muted or when
 * prefers-reduced-motion is set — plus the required debounce.
 */
import { renderHook, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { ORG_EVENTS, type OrgEvent } from '@/features/org/OrgRealtimeManager';
import { useVoicePrefsStore } from '@/stores/voicePrefs';

const { speakMock } = vi.hoisted(() => ({ speakMock: vi.fn() }));
vi.mock('@/features/org/api/voice', () => ({
  voiceApi: { speak: speakMock },
}));

let reduceMotion = false;
vi.mock('framer-motion', async (importOriginal) => {
  const actual = await importOriginal<typeof import('framer-motion')>();
  return { ...actual, useReducedMotion: () => reduceMotion };
});

// Import after mocks are registered.
const { useJarvisSpeech, lineFor } = await import('./useJarvisSpeech');

function missionStarted(overrides?: Partial<OrgEvent>): OrgEvent {
  return {
    event_type: ORG_EVENTS.MISSION_STARTED,
    org_id: 'org-1',
    tenant_id: 't-1',
    payload: { title: 'Launch campaign' },
    timestamp: new Date().toISOString(),
    version: '1',
    ...overrides,
  };
}

describe('useJarvisSpeech', () => {
  beforeEach(() => {
    speakMock.mockReset();
    speakMock.mockResolvedValue(new Blob());
    reduceMotion = false;
    useVoicePrefsStore.setState({ jarvisSpeechEnabled: true });
    // jsdom has no real audio decoding/playback — stub it out.
    vi.stubGlobal('URL', { ...URL, createObjectURL: () => 'blob:mock', revokeObjectURL: () => {} });
    HTMLMediaElement.prototype.play = vi.fn().mockResolvedValue(undefined);
    HTMLMediaElement.prototype.pause = vi.fn();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('speaks once for a speakable event when enabled and motion is not reduced', async () => {
    const { result } = renderHook(() => useJarvisSpeech());
    await act(async () => {
      result.current.handleEvent(missionStarted());
    });
    expect(speakMock).toHaveBeenCalledTimes(1);
    expect(speakMock).toHaveBeenCalledWith('Mission Launch campaign is now underway.');
  });

  it('does NOT speak when the persisted mute toggle is off (default)', async () => {
    useVoicePrefsStore.setState({ jarvisSpeechEnabled: false });
    const { result } = renderHook(() => useJarvisSpeech());
    await act(async () => {
      result.current.handleEvent(missionStarted());
    });
    expect(speakMock).not.toHaveBeenCalled();
  });

  it('does NOT speak under prefers-reduced-motion, even when enabled', async () => {
    reduceMotion = true;
    const { result } = renderHook(() => useJarvisSpeech());
    await act(async () => {
      result.current.handleEvent(missionStarted());
    });
    expect(speakMock).not.toHaveBeenCalled();
  });

  it('ignores event types outside the speakable set', async () => {
    const { result } = renderHook(() => useJarvisSpeech());
    await act(async () => {
      result.current.handleEvent(missionStarted({ event_type: ORG_EVENTS.HEALTH_DEGRADED }));
    });
    expect(speakMock).not.toHaveBeenCalled();
  });

  it('debounces a burst of speakable events into a single spoken line', async () => {
    vi.useFakeTimers({ toFake: ['Date'] });
    const { result } = renderHook(() => useJarvisSpeech());

    await act(async () => {
      result.current.handleEvent(missionStarted());
      result.current.handleEvent(missionStarted({ event_type: ORG_EVENTS.TEAM_FORMED, payload: {} }));
      result.current.handleEvent(missionStarted({ event_type: ORG_EVENTS.AGENT_ACTIVATED, payload: { role: 'Researcher' } }));
    });
    expect(speakMock).toHaveBeenCalledTimes(1);

    vi.setSystemTime(Date.now() + 5000);
    await act(async () => {
      result.current.handleEvent(missionStarted({ event_type: ORG_EVENTS.MISSION_COMPLETED, payload: { title: 'Launch campaign' } }));
    });
    expect(speakMock).toHaveBeenCalledTimes(2);
  });

  it('swallows a speak() failure so the console is never disrupted', async () => {
    speakMock.mockRejectedValueOnce(new Error('tts down'));
    const { result } = renderHook(() => useJarvisSpeech());
    await act(async () => {
      result.current.handleEvent(missionStarted());
    });
    expect(speakMock).toHaveBeenCalledTimes(1);
  });
});

describe('lineFor', () => {
  const base = {
    org_id: 'org-1',
    tenant_id: 't-1',
    timestamp: new Date().toISOString(),
    version: '1',
  };

  it('falls back to a generic line when MISSION_STARTED has no title', () => {
    expect(lineFor({ ...base, event_type: ORG_EVENTS.MISSION_STARTED, payload: {} }))
      .toBe('A mission is now underway.');
  });

  it('announces a team being formed', () => {
    expect(lineFor({ ...base, event_type: ORG_EVENTS.TEAM_FORMED, payload: {} }))
      .toBe('A new team has been assembled.');
  });

  it('falls back to a generic line when AGENT_ACTIVATED has no role', () => {
    expect(lineFor({ ...base, event_type: ORG_EVENTS.AGENT_ACTIVATED, payload: {} }))
      .toBe('A new agent is now active.');
  });

  it('includes the role when AGENT_ACTIVATED has one', () => {
    expect(lineFor({ ...base, event_type: ORG_EVENTS.AGENT_ACTIVATED, payload: { role: 'Researcher' } }))
      .toBe('Researcher is now active.');
  });

  it('falls back to a generic line when APPROVAL_REQUESTED has no action', () => {
    expect(lineFor({ ...base, event_type: ORG_EVENTS.APPROVAL_REQUESTED, payload: {} }))
      .toBe('Your approval is needed.');
  });

  it('includes the action when APPROVAL_REQUESTED has one', () => {
    expect(lineFor({ ...base, event_type: ORG_EVENTS.APPROVAL_REQUESTED, payload: { action: 'deploy prod' } }))
      .toBe('Your approval is needed for deploy prod.');
  });

  it('falls back to a generic line when MISSION_COMPLETED has no title', () => {
    expect(lineFor({ ...base, event_type: ORG_EVENTS.MISSION_COMPLETED, payload: {} }))
      .toBe('A mission is complete.');
  });

  it('returns null for a non-speakable event type', () => {
    expect(lineFor({ ...base, event_type: ORG_EVENTS.HEALTH_DEGRADED, payload: {} })).toBeNull();
  });
});
