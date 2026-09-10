/**
 * voicePrefs — persisted user toggle for "JARVIS speaking" (WS-7 item 4).
 *
 * TTS narration of org events is opt-in: default OFF so a first-time visitor
 * is never surprised by the app suddenly talking. Persisted across sessions
 * (localStorage, same pattern as useThemeStore) so the choice sticks.
 */
import { create } from 'zustand';
import { persist } from 'zustand/middleware';

interface VoicePrefsState {
  /** Whether JARVIS may speak short lines on key org events. Default OFF. */
  jarvisSpeechEnabled: boolean;
  setJarvisSpeechEnabled: (enabled: boolean) => void;
  toggleJarvisSpeech: () => void;
}

export const useVoicePrefsStore = create<VoicePrefsState>()(
  persist(
    (set, get) => ({
      jarvisSpeechEnabled: false,
      setJarvisSpeechEnabled: (enabled) => set({ jarvisSpeechEnabled: enabled }),
      toggleJarvisSpeech: () => set({ jarvisSpeechEnabled: !get().jarvisSpeechEnabled }),
    }),
    { name: 'av-voice-prefs' },
  ),
);
