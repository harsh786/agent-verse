/**
 * useVoiceAlerts — D-6 Proactive Voice Alerts frontend consumer.
 *
 * Subscribes to GET /v1/voice/alerts/stream (SSE) and plays
 * incoming TTS audio chunks automatically, even when no VoiceModal is open.
 *
 * Alert types: mission_failed, mission_blocked, approval_urgent,
 *              budget_exceeded, agent_error, mission_completed, goal_failed
 *
 * Usage in OrgPage or a persistent app-level component:
 *   useVoiceAlerts({ orgId, enabled: true });
 */
import { useEffect, useRef, useCallback } from 'react';
import { API_BASE } from '@/lib/api/client';
import { useAuthStore } from '@/stores/auth';

export interface VoiceAlertEvent {
  event_type: string;
  text:       string;
  chunks:     string[];   // base64-encoded PCM16 at 24 kHz
}

interface UseVoiceAlertsOpts {
  enabled?:     boolean;
  onAlert?:     (event: VoiceAlertEvent) => void;
}

const PCM_RATE = 24_000;

export function useVoiceAlerts({ enabled = true, onAlert }: UseVoiceAlertsOpts = {}) {
  const esRef   = useRef<EventSource | null>(null);
  const apiKey  = useAuthStore(s => s.apiKey);

  const playChunks = useCallback(async (chunks: string[]) => {
    if (!chunks.length) return;
    const ctx = new AudioContext({ sampleRate: PCM_RATE });
    // Decode all base64 PCM16 chunks into a single Float32 array
    const floats: number[] = [];
    for (const b64 of chunks) {
      const raw = atob(b64);
      for (let i = 0; i < raw.length - 1; i += 2) {
        const s16 = raw.charCodeAt(i) | (raw.charCodeAt(i + 1) << 8);
        floats.push((s16 > 32767 ? s16 - 65536 : s16) / 32768);
      }
    }
    const buf  = ctx.createBuffer(1, floats.length, PCM_RATE);
    buf.getChannelData(0).set(floats);
    const src  = ctx.createBufferSource();
    src.buffer = buf;
    src.connect(ctx.destination);
    src.onended = () => ctx.close();
    src.start();
  }, []);

  useEffect(() => {
    if (!enabled || !apiKey) return;

    // SSE requires URL-based auth (EventSource doesn't support headers)
    const url = `${API_BASE}/v1/voice/alerts/stream?api_key=${encodeURIComponent(apiKey)}`;
    const es  = new EventSource(url);
    esRef.current = es;

    es.onmessage = async (e) => {
      try {
        const event: VoiceAlertEvent = JSON.parse(e.data);
        onAlert?.(event);
        await playChunks(event.chunks ?? []);
      } catch { /* ignore malformed events */ }
    };

    es.onerror = () => {
      // SSE auto-reconnects on error — no manual retry needed
    };

    return () => {
      es.close();
      esRef.current = null;
    };
  }, [enabled, apiKey, onAlert, playChunks]);

  const dismiss = useCallback(() => {
    esRef.current?.close();
    esRef.current = null;
  }, []);

  return { dismiss };
}
