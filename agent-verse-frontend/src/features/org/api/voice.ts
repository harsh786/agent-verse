/**
 * Voice OS API client — AgentVerse Voice endpoints.
 *
 * All calls use the same auth pattern as the rest of the app:
 *   X-API-Key header via apiFetch / manual fetch with useAuthStore.
 */
import { apiFetch, API_BASE } from '@/lib/api/client';
import { useAuthStore, getAuthHeader } from '@/stores/auth';
import type {
  PersonaResponse,
  TranscribeResponse,
  VoiceStatusResponse,
} from '../types/voice';

const BASE = '/v1/voice';

export const voiceApi = {

  /** Check STT/TTS provider readiness. */
  status(): Promise<VoiceStatusResponse> {
    return apiFetch<VoiceStatusResponse>(`${BASE}/status`);
  },

  /** Upload audio blob → transcript (native STT). */
  transcribe(audio: Blob, filename = 'audio.wav'): Promise<TranscribeResponse> {
    const form = new FormData();
    form.append('audio', audio, filename);
    return apiFetch<TranscribeResponse>(`${BASE}/transcribe`, {
      method: 'POST',
      body: form,
    });
  },

  /** Text → WAV Blob (native TTS). */
  async speak(
    text: string,
    opts?: { language?: string; speed?: number; org_id?: string; use_org_persona?: boolean },
  ): Promise<Blob> {
    const r = await fetch(`${API_BASE}${BASE}/speak`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getAuthHeader() },
      body: JSON.stringify({ text, language: opts?.language ?? 'en', ...opts }),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      throw new Error(err?.detail ?? 'TTS failed');
    }
    return r.blob();
  },

  /** Spoken login greeting WAV for org (D-2/D-5/D-7). */
  async greeting(orgId: string, opts?: { user_name?: string; language?: string }): Promise<Blob> {
    const qs     = new URLSearchParams();
    if (opts?.user_name) qs.set('user_name', opts.user_name);
    if (opts?.language)  qs.set('language', opts.language);
    const r = await fetch(`${API_BASE}${BASE}/greeting/${orgId}?${qs}`, {
      headers: getAuthHeader(),
    });
    if (!r.ok) throw new Error('Greeting fetch failed');
    return r.blob();
  },

  /** Upload org voice persona reference audio (D-5 voice cloning). */
  uploadPersona(orgId: string, audio: File, refText: string, language = 'en'): Promise<PersonaResponse> {
    const form = new FormData();
    form.append('audio', audio);
    return apiFetch<PersonaResponse>(
      `${BASE}/persona/${orgId}?ref_text=${encodeURIComponent(refText)}&language=${language}`,
      { method: 'POST', body: form },
    );
  },

  /** Remove org voice persona. */
  deletePersona(orgId: string): Promise<void> {
    return apiFetch<void>(`${BASE}/persona/${orgId}`, { method: 'DELETE' });
  },

  /** Build authenticated WebSocket URL for real-time voice stream. */
  streamUrl(orgId: string): string {
    const apiKey = useAuthStore.getState().apiKey ?? '';
    const base   = API_BASE.replace(/^http/, 'ws');
    return `${base}/v1/voice/stream/${orgId}?api_key=${encodeURIComponent(apiKey)}`;
  },
};
