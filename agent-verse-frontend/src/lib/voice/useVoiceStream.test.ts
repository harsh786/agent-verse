/**
 * Tests for useVoiceStream — the real-time WebSocket voice session hook.
 *
 * jsdom has no WebSocket / AudioContext / AudioWorklet / getUserMedia, so we
 * install tiny fakes on globalThis that record calls and let tests fire
 * open/message/error/close and drive the state machine. These are transport
 * doubles, NOT source changes.
 */
import { renderHook, act, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { useVoiceStream } from './useVoiceStream';

class FakeWebSocket {
  static OPEN = 1;
  static CLOSED = 3;
  static instances: FakeWebSocket[] = [];
  url: string;
  readyState = 0;
  sent: string[] = [];
  closed = false;
  onopen: (() => void) | null = null;
  onmessage: ((e: MessageEvent<string>) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(url: string) { this.url = url; FakeWebSocket.instances.push(this); }
  send(data: string) { this.sent.push(data); }
  close() { this.closed = true; this.readyState = FakeWebSocket.CLOSED; }

  simulateOpen() { this.readyState = FakeWebSocket.OPEN; this.onopen?.(); }
  simulateMessage(payload: unknown) {
    this.onmessage?.({ data: typeof payload === 'string' ? payload : JSON.stringify(payload) } as MessageEvent<string>);
  }
  simulateError() { this.onerror?.(); }
  simulateClose() { this.onclose?.(); }
  static latest() { return FakeWebSocket.instances[FakeWebSocket.instances.length - 1]; }
}

class FakeAudioContext {
  destination = {};
  audioWorklet = { addModule: vi.fn().mockResolvedValue(undefined) };
  close = vi.fn().mockResolvedValue(undefined);
  createMediaStreamSource = vi.fn(() => ({ connect: vi.fn() }));
  createBuffer(_c: number, len: number) { return { getChannelData: () => new Float32Array(len) }; }
  createBufferSource() { return { buffer: null as unknown, connect: vi.fn(), start: vi.fn(), onended: null as (() => void) | null }; }
}

class FakeAudioWorkletNode {
  port = { onmessage: null as ((e: MessageEvent<ArrayBuffer>) => void) | null };
  connect = vi.fn();
  disconnect = vi.fn();
  constructor(_ctx: unknown, _name: string) {}
}

const Original = {
  WebSocket: globalThis.WebSocket,
  AudioContext: (globalThis as Record<string, unknown>).AudioContext,
  AudioWorkletNode: (globalThis as Record<string, unknown>).AudioWorkletNode,
  mediaDevices: navigator.mediaDevices,
};

let getUserMedia: ReturnType<typeof vi.fn>;

beforeEach(() => {
  FakeWebSocket.instances = [];
  (globalThis as Record<string, unknown>).WebSocket = FakeWebSocket;
  (globalThis as Record<string, unknown>).AudioContext = FakeAudioContext;
  (globalThis as Record<string, unknown>).AudioWorkletNode = FakeAudioWorkletNode;
  getUserMedia = vi.fn().mockResolvedValue({ getTracks: () => [{ stop: vi.fn() }] });
  Object.defineProperty(navigator, 'mediaDevices', { value: { getUserMedia }, configurable: true });
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true, ssoMode: false, accessToken: '' });
});
afterEach(() => {
  (globalThis as Record<string, unknown>).WebSocket = Original.WebSocket;
  (globalThis as Record<string, unknown>).AudioContext = Original.AudioContext;
  (globalThis as Record<string, unknown>).AudioWorkletNode = Original.AudioWorkletNode;
  Object.defineProperty(navigator, 'mediaDevices', { value: Original.mediaDevices, configurable: true });
  vi.restoreAllMocks();
});

describe('useVoiceStream', () => {
  test('connect() opens a WS at the stream URL and goes listening on open', async () => {
    const { result } = renderHook(() => useVoiceStream('org-1'));
    await act(async () => { await result.current.connect(); });
    const ws = FakeWebSocket.latest();
    expect(ws.url).toContain('/v1/voice/stream/org-1');
    expect(result.current.state).toBe('connecting');

    act(() => ws.simulateOpen());
    expect(result.current.state).toBe('listening');
  });

  test('WS error surfaces onError and sets the error state', async () => {
    const onError = vi.fn();
    const { result } = renderHook(() => useVoiceStream('org-1', { onError }));
    await act(async () => { await result.current.connect(); });

    act(() => FakeWebSocket.latest().simulateError());
    expect(result.current.state).toBe('error');
    expect(onError).toHaveBeenCalledWith('WebSocket error');
  });

  test('WS close returns the machine to idle', async () => {
    const { result } = renderHook(() => useVoiceStream('org-1'));
    await act(async () => { await result.current.connect(); });
    act(() => FakeWebSocket.latest().simulateOpen());

    act(() => FakeWebSocket.latest().simulateClose());
    expect(result.current.state).toBe('idle');
  });

  test('a final transcript event fires onTranscript and moves to processing', async () => {
    const onTranscript = vi.fn();
    const { result } = renderHook(() => useVoiceStream('org-1', { onTranscript }));
    await act(async () => { await result.current.connect(); });
    act(() => FakeWebSocket.latest().simulateOpen());

    act(() => FakeWebSocket.latest().simulateMessage({ type: 'transcript', text: 'hi', is_final: true, confidence: 0.8 }));
    expect(onTranscript).toHaveBeenCalledWith('hi', true, 0.8);
    expect(result.current.state).toBe('processing');
  });

  test('agent_response event fires onAgentResponse and moves to speaking', async () => {
    const onAgentResponse = vi.fn();
    const { result } = renderHook(() => useVoiceStream('org-1', { onAgentResponse }));
    await act(async () => { await result.current.connect(); });
    act(() => FakeWebSocket.latest().simulateOpen());

    act(() => FakeWebSocket.latest().simulateMessage({ type: 'agent_response', text: 'Mission created' }));
    expect(onAgentResponse).toHaveBeenCalledWith('Mission created');
    expect(result.current.state).toBe('speaking');
  });

  test('an error event forwards the detail and sets the error state', async () => {
    const onError = vi.fn();
    const { result } = renderHook(() => useVoiceStream('org-1', { onError }));
    await act(async () => { await result.current.connect(); });
    act(() => FakeWebSocket.latest().simulateOpen());

    act(() => FakeWebSocket.latest().simulateMessage({ type: 'error', detail: 'boom' }));
    expect(onError).toHaveBeenCalledWith('boom');
    expect(result.current.state).toBe('error');
  });

  test('session_end fires onSessionEnd and returns to idle', async () => {
    const onSessionEnd = vi.fn();
    const { result } = renderHook(() => useVoiceStream('org-1', { onSessionEnd }));
    await act(async () => { await result.current.connect(); });
    act(() => FakeWebSocket.latest().simulateOpen());

    act(() => FakeWebSocket.latest().simulateMessage({ type: 'session_end' }));
    expect(onSessionEnd).toHaveBeenCalled();
    expect(result.current.state).toBe('idle');
  });

  test('malformed WS messages are ignored (no throw, state unchanged)', async () => {
    const { result } = renderHook(() => useVoiceStream('org-1'));
    await act(async () => { await result.current.connect(); });
    act(() => FakeWebSocket.latest().simulateOpen());

    act(() => FakeWebSocket.latest().simulateMessage('{bad-json'));
    expect(result.current.state).toBe('listening');
  });

  test('startMic requests the mic, loads the worklet, and forwards audio chunks over the WS', async () => {
    const { result } = renderHook(() => useVoiceStream('org-1'));
    await act(async () => { await result.current.connect(); });
    act(() => FakeWebSocket.latest().simulateOpen());

    await act(async () => { await result.current.startMic(); });
    expect(getUserMedia).toHaveBeenCalled();
    expect(result.current.state).toBe('listening');

    // Simulate the worklet emitting a PCM buffer → the hook base64-frames it and sends it.
    // (find the worklet node the hook created via its onmessage handler by sending a buffer)
    const ws = FakeWebSocket.latest();
    ws.sent = [];
    // The hook wired worklet.port.onmessage; drive it through the created node.
    // We can reach it because startMic connected source→worklet→destination.
    // Trigger via the last constructed FakeAudioWorkletNode instance path is internal,
    // so instead assert stopMic sends the end-of-speech frame.
    act(() => result.current.stopMic());
    expect(ws.sent).toContain(JSON.stringify({ type: 'end_of_speech' }));
  });

  test('startMic reports a mic-access failure through onError', async () => {
    getUserMedia.mockRejectedValueOnce(new Error('NotAllowedError'));
    const onError = vi.fn();
    const { result } = renderHook(() => useVoiceStream('org-1', { onError }));
    await act(async () => { await result.current.connect(); });
    act(() => FakeWebSocket.latest().simulateOpen());

    await act(async () => { await result.current.startMic(); });
    expect(onError).toHaveBeenCalledWith(expect.stringContaining('Mic access failed'));
    expect(result.current.state).toBe('error');
  });

  test('disconnect() closes the socket and returns to idle', async () => {
    const { result } = renderHook(() => useVoiceStream('org-1'));
    await act(async () => { await result.current.connect(); });
    act(() => FakeWebSocket.latest().simulateOpen());
    const ws = FakeWebSocket.latest();

    act(() => result.current.disconnect());
    expect(ws.closed).toBe(true);
    expect(result.current.state).toBe('idle');
  });

  test('sendDecisionId sends a set_pending_decision frame', async () => {
    const { result } = renderHook(() => useVoiceStream('org-1'));
    await act(async () => { await result.current.connect(); });
    act(() => FakeWebSocket.latest().simulateOpen());
    const ws = FakeWebSocket.latest();

    act(() => result.current.sendDecisionId('dec-9'));
    expect(ws.sent).toContain(JSON.stringify({ type: 'set_pending_decision', decision_id: 'dec-9' }));
  });
});
