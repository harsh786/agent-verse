import { describe, test, expect, beforeEach } from 'vitest';
import { useVoicePrefsStore } from './voicePrefs';

beforeEach(() => {
  useVoicePrefsStore.setState({ jarvisSpeechEnabled: false });
});

describe('useVoicePrefsStore', () => {
  test('defaults to disabled', () => {
    expect(useVoicePrefsStore.getState().jarvisSpeechEnabled).toBe(false);
  });

  test('setJarvisSpeechEnabled sets the flag explicitly', () => {
    useVoicePrefsStore.getState().setJarvisSpeechEnabled(true);
    expect(useVoicePrefsStore.getState().jarvisSpeechEnabled).toBe(true);
    useVoicePrefsStore.getState().setJarvisSpeechEnabled(false);
    expect(useVoicePrefsStore.getState().jarvisSpeechEnabled).toBe(false);
  });

  test('toggleJarvisSpeech flips the current value', () => {
    expect(useVoicePrefsStore.getState().jarvisSpeechEnabled).toBe(false);
    useVoicePrefsStore.getState().toggleJarvisSpeech();
    expect(useVoicePrefsStore.getState().jarvisSpeechEnabled).toBe(true);
    useVoicePrefsStore.getState().toggleJarvisSpeech();
    expect(useVoicePrefsStore.getState().jarvisSpeechEnabled).toBe(false);
  });
});
