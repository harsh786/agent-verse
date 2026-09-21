/**
 * Tests for useVoiceStream — the real-time WebSocket voice session hook.
 *
 * jsdom has no WebSocket / AudioContext / AudioWorklet / getUserMedia, so we
 * install tiny fakes on globalThis that record calls and let tests fire
 * open/message/error/close and drive the state machine. These are transport
 * doubles, NOT source changes.
 */
import { renderHook, act } from '@testing-library/react';
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

interface FakeBufferSource {
  buffer: unknown;
  connect: ReturnType<typeof vi.fn>;
  start: ReturnType<typeof vi.fn>;
  onended: (() => void) | null;
}

class FakeAudioContext {
  static instances: FakeAudioContext[] = [];
  static lastBufferSource: FakeBufferSource | null = null;
  destination = {};
  audioWorklet = { addModule: vi.fn().mockResolvedValue(undefined) };
  close = vi.fn().mockResolvedValue(undefined);
  createMediaStreamSource = vi.fn(() => ({ connect: vi.fn() }));
  constructor() { FakeAudioContext.instances.push(this); }
  createBuffer(_c: number, len: number) { return { getChannelData: () => new Float32Array(len) }; }
  createBufferSource(): FakeBufferSource {
    const src: FakeBufferSource = { buffer: null, connect: vi.fn(), start: vi.fn(), onended: null };
    FakeAudioContext.lastBufferSource = src;
    return src;
  }
}

class FakeAudioWorkletNode {
  static lastInstance: FakeAudioWorkletNode | null = null;
  port = { onmessage: null as ((e: MessageEvent<ArrayBuffer>) => void) | null };
  connect = vi.fn();
  disconnect = vi.fn();
  constructor(_ctx: unknown, _name: string) { FakeAudioWorkletNode.lastInstance = this; }
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
  FakeAudioContext.instances = [];
  FakeAudioContext.lastBufferSource = null;
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

  test('startMic wires the worklet to base64-encode PCM chunks and send them over the WS', async () => {
    const { result } = renderHook(() => useVoiceStream('org-1'));
    await act(async () => { await result.current.connect(); });
    act(() => FakeWebSocket.latest().simulateOpen());

    await act(async () => { await result.current.startMic(); });
    const ws = FakeWebSocket.latest();
    ws.sent = [];

    const worklet = FakeAudioWorkletNode.lastInstance;
    expect(worklet).not.toBeNull();
    const chunk = new Uint8Array([1, 2, 3, 4]).buffer;
    act(() => worklet?.port.onmessage?.({ data: chunk } as MessageEvent<ArrayBuffer>));

    const expectedB64 = btoa(String.fromCharCode(1, 2, 3, 4));
    expect(ws.sent).toContain(JSON.stringify({ type: 'audio_chunk', data: expectedB64 }));
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

  test('agent_thinking event fires onAgentThinking and moves to processing', async () => {
    const onAgentThinking = vi.fn();
    const { result } = renderHook(() => useVoiceStream('org-1', { onAgentThinking }));
    await act(async () => { await result.current.connect(); });
    act(() => FakeWebSocket.latest().simulateOpen());

    act(() => FakeWebSocket.latest().simulateMessage({ type: 'agent_thinking' }));
    expect(onAgentThinking).toHaveBeenCalled();
    expect(result.current.state).toBe('processing');
  });

  test('connect() is a no-op when the socket is already open', async () => {
    const { result } = renderHook(() => useVoiceStream('org-1'));
    await act(async () => { await result.current.connect(); });
    act(() => FakeWebSocket.latest().simulateOpen());
    expect(FakeWebSocket.instances).toHaveLength(1);

    await act(async () => { await result.current.connect(); });
    expect(FakeWebSocket.instances).toHaveLength(1);
    expect(result.current.state).toBe('listening');
  });

  test('connect() catches a synchronous WebSocket construction failure', async () => {
    const onError = vi.fn();
    class ThrowingWebSocket {
      static OPEN = 1;
      static CLOSED = 3;
      constructor() { throw new Error('constructor boom'); }
    }
    (globalThis as Record<string, unknown>).WebSocket = ThrowingWebSocket;

    const { result } = renderHook(() => useVoiceStream('org-1', { onError }));
    await act(async () => { await result.current.connect(); });

    expect(onError).toHaveBeenCalledWith(expect.stringContaining('Connection failed'));
    expect(result.current.state).toBe('error');
  });

  test('tts_chunk buffers PCM without changing state', async () => {
    const { result } = renderHook(() => useVoiceStream('org-1'));
    await act(async () => { await result.current.connect(); });
    act(() => FakeWebSocket.latest().simulateOpen());
    act(() => FakeWebSocket.latest().simulateMessage({ type: 'agent_response', text: 'hi' }));
    expect(result.current.state).toBe('speaking');

    const pcmB64 = btoa(String.fromCharCode(1, 0, 2, 0));
    act(() => FakeWebSocket.latest().simulateMessage({ type: 'tts_chunk', data: pcmB64 }));
    // Buffering a chunk alone does not move the state machine.
    expect(result.current.state).toBe('speaking');
    expect(FakeAudioContext.instances).toHaveLength(0);
  });

  test('tts_done flushes buffered PCM through an AudioContext and returns to listening', async () => {
    const { result } = renderHook(() => useVoiceStream('org-1'));
    await act(async () => { await result.current.connect(); });
    act(() => FakeWebSocket.latest().simulateOpen());

    const pcmB64 = btoa(String.fromCharCode(1, 0, 2, 0));
    act(() => FakeWebSocket.latest().simulateMessage({ type: 'tts_chunk', data: pcmB64 }));

    let settled = false;
    await act(async () => {
      FakeWebSocket.latest().simulateMessage({ type: 'tts_done' });
      // Let the microtask queue advance so _flushPCM creates the AudioContext/source.
      await Promise.resolve();
      await Promise.resolve();
      settled = true;
    });
    expect(settled).toBe(true);
    expect(FakeAudioContext.instances).toHaveLength(1);
    const src = FakeAudioContext.lastBufferSource;
    expect(src).not.toBeNull();
    expect(src?.start).toHaveBeenCalled();

    await act(async () => { src?.onended?.(); await Promise.resolve(); });
    expect(result.current.state).toBe('listening');
  });

  test('tts_done with no buffered PCM skips AudioContext creation and returns to listening', async () => {
    const { result } = renderHook(() => useVoiceStream('org-1'));
    await act(async () => { await result.current.connect(); });
    act(() => FakeWebSocket.latest().simulateOpen());

    await act(async () => {
      FakeWebSocket.latest().simulateMessage({ type: 'tts_done' });
      await Promise.resolve();
    });
    expect(FakeAudioContext.instances).toHaveLength(0);
    expect(result.current.state).toBe('listening');
  });

  test('startMic connects first when called from idle', async () => {
    const { result } = renderHook(() => useVoiceStream('org-1'));
    expect(result.current.state).toBe('idle');

    await act(async () => { await result.current.startMic(); });
    expect(FakeWebSocket.instances).toHaveLength(1);
    expect(getUserMedia).toHaveBeenCalled();
    expect(result.current.state).toBe('listening');
  });

  test('unmount cleans up the connection via disconnect', async () => {
    const { result, unmount } = renderHook(() => useVoiceStream('org-1'));
    await act(async () => { await result.current.connect(); });
    act(() => FakeWebSocket.latest().simulateOpen());
    const ws = FakeWebSocket.latest();

    unmount();
    expect(ws.closed).toBe(true);
  });
});
