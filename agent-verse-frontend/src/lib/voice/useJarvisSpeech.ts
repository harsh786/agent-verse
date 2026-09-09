/**
 * useJarvisSpeech — WS-7 item 4 ("JARVIS speaking").
 *
 * Speaks a short line via the existing TTS endpoint (`voiceApi.speak`, the
 * same REST call used by useGreeting) on key, REAL org SSE events — never a
 * timer or fabricated activity. Feed `handleEvent` the same `OrgEvent`
 * objects the org SSE stream already delivers (wire it into
 * `useOrgRealtimeManager`'s `onEvent`, alongside `neural.applyEvent`).
 *
 * Guardrails (all required by the WS-7 brief):
 *   - opt-in: silent unless `useVoicePrefsStore`'s persisted toggle is on
 *     (default OFF — see src/stores/voicePrefs.ts)
 *   - skipped entirely under prefers-reduced-motion
 *   - debounced: at most one spoken line per SPEECH_DEBOUNCE_MS, so a burst
 *     of events (e.g. a team forming = several agent.activated events) never
 *     spams the speaker
 */
import { useCallback, useRef } from 'react';
import { useReducedMotion } from 'framer-motion';
import { voiceApi } from '@/features/org/api/voice';
import { useVoicePrefsStore } from '@/stores/voicePrefs';
import { ORG_EVENTS, type OrgEvent, type OrgEventType } from '@/features/org/OrgRealtimeManager';

/** The five key events called out by the WS-7 brief. */
const SPEAKABLE_EVENTS: ReadonlySet<OrgEventType> = new Set([
  ORG_EVENTS.MISSION_STARTED,
  ORG_EVENTS.TEAM_FORMED,
  ORG_EVENTS.AGENT_ACTIVATED,
  ORG_EVENTS.APPROVAL_REQUESTED,
  ORG_EVENTS.MISSION_COMPLETED,
]);

/** Minimum time between two spoken lines — keeps JARVIS from spamming. */
export const SPEECH_DEBOUNCE_MS = 4000;

/** Pure: turn one real OrgEvent into a short spoken line, or null to skip it. */
export function lineFor(event: OrgEvent): string | null {
  const p = (event.payload ?? {}) as Record<string, unknown>;
  const str = (key: string) => (typeof p[key] === 'string' ? (p[key] as string) : undefined);

  switch (event.event_type) {
    case ORG_EVENTS.MISSION_STARTED: {
      const title = str('title');
      return title ? `Mission ${title} is now underway.` : 'A mission is now underway.';
    }
    case ORG_EVENTS.TEAM_FORMED:
      return 'A new team has been assembled.';
    case ORG_EVENTS.AGENT_ACTIVATED: {
      const role = str('role');
      return role ? `${role} is now active.` : 'A new agent is now active.';
    }
    case ORG_EVENTS.APPROVAL_REQUESTED: {
      const action = str('action');
      return action ? `Your approval is needed for ${action}.` : 'Your approval is needed.';
    }
    case ORG_EVENTS.MISSION_COMPLETED: {
      const title = str('title');
      return title ? `Mission ${title} is complete.` : 'A mission is complete.';
    }
    default:
      return null;
  }
}

export function useJarvisSpeech() {
  const enabled = useVoicePrefsStore(s => s.jarvisSpeechEnabled);
  const reduce = useReducedMotion();
  const lastSpokenAtRef = useRef(0);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  const speak = useCallback(async (text: string) => {
    try {
      const wav = await voiceApi.speak(text);
      const url = URL.createObjectURL(wav);
      audioRef.current?.pause();
      const audio = new Audio(url);
      audioRef.current = audio;
      audio.onended = () => URL.revokeObjectURL(url);
      audio.onerror = () => URL.revokeObjectURL(url);
      await audio.play();
    } catch {
      // A speech failure must never disrupt the console.
    }
  }, []);

  const handleEvent = useCallback((event: OrgEvent) => {
    if (!enabled || reduce) return;
    if (!SPEAKABLE_EVENTS.has(event.event_type)) return;

    const now = Date.now();
    if (now - lastSpokenAtRef.current < SPEECH_DEBOUNCE_MS) return; // debounce — no spam

    const line = lineFor(event);
    if (!line) return;

    lastSpokenAtRef.current = now;
    void speak(line);
  }, [enabled, reduce, speak]);

  return { handleEvent, enabled };
}
