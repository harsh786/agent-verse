/**
 * useVoiceStream — manages the real-time WebSocket voice session.
 *
 * State machine: idle → connecting → listening → processing → speaking → idle
 *
 * Mic capture: AudioWorklet (DSP thread, avoids main thread jank).
 * TTS playback: Web Audio API PCM16 queue at 24 kHz.
 *
 * D-1: Transcripts are sent to the backend intent router which creates real
 *      missions via GoalRefinementPipeline + OrgService.
 * D-3: Intent classification happens server-side.
 * D-4: Say "approve" → real OrgService.record_decision() called.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { voiceApi } from '@/features/org/api/voice';
import type { VoiceStreamEvent, VoiceStreamState } from '@/features/org/types/voice';

const PCM_OUT_RATE = 24_000;   // TTS output sample rate

export interface UseVoiceStreamCallbacks {
  onTranscript?:    (text: string, isFinal: boolean, confidence: number) => void;
  onAgentThinking?: () => void;
  onAgentResponse?: (text: string) => void;
  onTtsDone?:       () => void;
  onError?:         (msg: string) => void;
  onSessionEnd?:    () => void;
}

export function useVoiceStream(orgId: string, callbacks?: UseVoiceStreamCallbacks) {
  const [state, setState] = useState<VoiceStreamState>('idle');
  const wsRef      = useRef<WebSocket | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const workletRef = useRef<AudioWorkletNode | null>(null);
  const streamRef  = useRef<MediaStream | null>(null);
  const pcmQueue   = useRef<Float32Array[]>([]);

  const connect = useCallback(async () => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;
    setState('connecting');
    try {
      const ws = new WebSocket(voiceApi.streamUrl(orgId));
      wsRef.current = ws;

      ws.onopen = () => setState('listening');
      ws.onerror = () => { setState('error'); callbacks?.onError?.('WebSocket error'); };
      ws.onclose = () => setState('idle');
      ws.onmessage = (ev) => {
        try {
          const msg: VoiceStreamEvent = JSON.parse(ev.data);
          _handleEvent(msg);
        } catch { /* ignore parse errors */ }
      };
    } catch (err) {
      setState('error');
      callbacks?.onError?.(`Connection failed: ${err}`);
    }
  }, [orgId]);   // eslint-disable-line react-hooks/exhaustive-deps

  const _handleEvent = (msg: VoiceStreamEvent) => {
    if (msg.type === 'transcript') {
      callbacks?.onTranscript?.(msg.text ?? '', msg.is_final ?? false, msg.confidence ?? 0);
      if (msg.is_final) setState('processing');
    } else if (msg.type === 'agent_thinking') {
      callbacks?.onAgentThinking?.();
      setState('processing');
    } else if (msg.type === 'agent_response') {
      callbacks?.onAgentResponse?.(msg.text ?? '');
      setState('speaking');
    } else if (msg.type === 'tts_chunk' && msg.data) {
      _enqueuePCM(msg.data);
    } else if (msg.type === 'tts_done') {
      callbacks?.onTtsDone?.();
      _flushPCM().then(() => setState('listening'));
    } else if (msg.type === 'session_end') {
      callbacks?.onSessionEnd?.();
      setState('idle');
    } else if (msg.type === 'error') {
      callbacks?.onError?.(msg.detail ?? 'Unknown error');
      setState('error');
    }
  };

  const startMic = useCallback(async () => {
    if (state !== 'listening' && state !== 'connecting') {
      await connect();
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { sampleRate: 16_000, channelCount: 1, echoCancellation: true, noiseSuppression: true },
      });
      streamRef.current = stream;
      const ctx = new AudioContext({ sampleRate: 16_000 });
      audioCtxRef.current = ctx;
      await ctx.audioWorklet.addModule('/audio-worklet-processor.js');
      const source  = ctx.createMediaStreamSource(stream);
      const worklet = new AudioWorkletNode(ctx, 'pcm-capture-processor');
      workletRef.current = worklet;
      worklet.port.onmessage = ({ data }: MessageEvent<ArrayBuffer>) => {
        const b64 = btoa(String.fromCharCode(...new Uint8Array(data)));
        wsRef.current?.send(JSON.stringify({ type: 'audio_chunk', data: b64 }));
      };
      source.connect(worklet);
      worklet.connect(ctx.destination);
      setState('listening');
    } catch (err) {
      callbacks?.onError?.(`Mic access failed: ${err}`);
      setState('error');
    }
  }, [state, connect]);   // eslint-disable-line react-hooks/exhaustive-deps

  const stopMic = useCallback(() => {
    workletRef.current?.disconnect();
    workletRef.current = null;
    streamRef.current?.getTracks().forEach(t => t.stop());
    streamRef.current = null;
    audioCtxRef.current?.close();
    audioCtxRef.current = null;
    wsRef.current?.send(JSON.stringify({ type: 'end_of_speech' }));
  }, []);

  const disconnect = useCallback(() => {
    stopMic();
    wsRef.current?.close();
    wsRef.current = null;
    setState('idle');
  }, [stopMic]);

  const sendDecisionId = useCallback((decisionId: string) => {
    wsRef.current?.send(JSON.stringify({ type: 'set_pending_decision', decision_id: decisionId }));
  }, []);

  // TTS PCM playback helpers
  const _enqueuePCM = (b64: string) => {
    const raw    = atob(b64);
    const int16  = new Int16Array(raw.length / 2);
    for (let i = 0; i < int16.length; i++) {
      int16[i] = (raw.charCodeAt(i * 2) | (raw.charCodeAt(i * 2 + 1) << 8));
    }
    const f32 = new Float32Array(int16.length);
    for (let i = 0; i < int16.length; i++) f32[i] = int16[i] / 32768;
    pcmQueue.current.push(f32);
  };

  const _flushPCM = async () => {
    if (!pcmQueue.current.length) return;
    const ctx    = new AudioContext({ sampleRate: PCM_OUT_RATE });
    const chunks = pcmQueue.current.splice(0);
    const total  = chunks.reduce((a, c) => a + c.length, 0);
    const buf    = ctx.createBuffer(1, total, PCM_OUT_RATE);
    const data   = buf.getChannelData(0);
    let offset   = 0;
    for (const c of chunks) { data.set(c, offset); offset += c.length; }
    const src = ctx.createBufferSource();
    src.buffer = buf;
    src.connect(ctx.destination);
    await new Promise<void>(resolve => { src.onended = () => { ctx.close(); resolve(); }; });
    src.start();
  };

  useEffect(() => () => disconnect(), [disconnect]);

  return { state, connect, startMic, stopMic, disconnect, sendDecisionId };
}
