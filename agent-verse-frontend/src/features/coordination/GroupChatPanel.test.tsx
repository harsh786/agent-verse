import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, test } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { GroupChatPanel } from './GroupChatPanel';
import { mergeMessages } from './useGroupChat';

class FakeWebSocket {
  static OPEN = 1;
  static instances: FakeWebSocket[] = [];
  readyState = 0;
  sent: string[] = [];
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onclose: (() => void) | null = null;
  constructor(public url: string, public protocols?: string[]) {
    FakeWebSocket.instances.push(this);
  }
  send(data: string) {
    this.sent.push(data);
  }
  close() {
    this.readyState = 3;
  }
  emit(frame: unknown) {
    this.readyState = FakeWebSocket.OPEN;
    act(() => this.onmessage?.({ data: JSON.stringify(frame) } as MessageEvent<string>));
  }
}

const Original = globalThis.WebSocket;

beforeEach(() => {
  FakeWebSocket.instances = [];
  (globalThis as unknown as { WebSocket: unknown }).WebSocket = FakeWebSocket;
  useAuthStore.setState({ apiKey: 'key-a', isAuthenticated: true });
});

afterEach(() => {
  (globalThis as unknown as { WebSocket: unknown }).WebSocket = Original;
});

describe('GroupChatPanel', () => {
  test('connects with the coordination subprotocol and key token', () => {
    render(<GroupChatPanel sessionId="s1" />);
    const socket = FakeWebSocket.instances[0];
    expect(socket.url).toContain('/api/v1/coordination/sessions/s1/group-chat/ws?after_sequence=0');
    expect(socket.protocols?.[0]).toBe('agentverse.coordination.v1');
    expect(socket.protocols?.[1]).toMatch(/^av\.v1\./);
  });

  test('renders messages from other participants live and de-duplicates by id', async () => {
    render(<GroupChatPanel sessionId="s1" />);
    const socket = FakeWebSocket.instances[0];
    socket.emit({ type: 'replay_complete', last_sequence: 0 });
    socket.emit({ type: 'message', message: { message_id: 'm1', sequence: 1, sender_agent_id: 'human:b', safe_content: 'from B' } });
    socket.emit({ type: 'message', message: { message_id: 'm1', sequence: 1, sender_agent_id: 'human:b', safe_content: 'from B' } });
    expect(screen.getAllByText('from B')).toHaveLength(1);

    await userEvent.type(screen.getByLabelText('Message'), 'hello');
    await userEvent.click(screen.getByRole('button', { name: /send/i }));
    const sent = JSON.parse(socket.sent[0]) as { content: string; client_message_id: string };
    expect(sent.content).toBe('hello');
    expect(sent.client_message_id).toBeTruthy();
    socket.emit({ type: 'ack', message: { message_id: 'm2', sequence: 2, sender_agent_id: 'human:a', safe_content: 'hello' } });
    expect(screen.getByText('hello')).toBeInTheDocument();
  });

  test('mergeMessages keeps causal order', () => {
    const merged = mergeMessages(
      [{ message_id: 'b', sequence: 2 }],
      [{ message_id: 'a', sequence: 1 }, { message_id: 'b', sequence: 2 }],
    );
    expect(merged.map((m) => m.message_id)).toEqual(['a', 'b']);
  });
});
