/** CHAT-D-2 — useMessageFeedback writes the saved feedback into the message cache. */
import { describe, it, expect, vi } from 'vitest';
import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createElement, type ReactNode } from 'react';
import type { ChatMessage } from '../types/chat.types';

const submitFeedback = vi.fn();
const clearFeedback = vi.fn();
const listMessages = vi.fn();
vi.mock('@/lib/api/chat', () => ({
  chatApi: {
    submitFeedback: (...a: unknown[]) => submitFeedback(...a),
    clearFeedback: (...a: unknown[]) => clearFeedback(...a),
    listMessages: (...a: unknown[]) => listMessages(...a),
  },
}));

import { MESSAGE_KEYS, useMessageFeedback } from './useChatHistory';

const reply: ChatMessage = {
  id: 'm1', session_id: 's1', role: 'assistant', content: 'hi', metadata: {}, intent: null,
  goal_id: null, branch_id: null, parent_message_id: null, created_at: 'x', feedback: null,
};

function setup() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  qc.setQueryData(MESSAGE_KEYS.list('s1'), [reply]);
  const wrapper = ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client: qc }, children);
  return { qc, ...renderHook(() => useMessageFeedback('s1'), { wrapper }) };
}

describe('useMessageFeedback', () => {
  it('saves feedback and shows the saved state in the cached messages', async () => {
    submitFeedback.mockResolvedValue({
      message_id: 'm1', rating: -1, comment: 'off', updated_at: 'u1',
    });
    listMessages.mockReturnValue(new Promise(() => {})); // keep the refetch pending
    const { qc, result } = setup();
    act(() => result.current.submit.mutate({ messageId: 'm1', rating: -1, comment: 'off' }));
    await waitFor(() => expect(result.current.submit.isSuccess).toBe(true));
    expect(submitFeedback).toHaveBeenCalledWith('s1', 'm1', -1, 'off');
    const cached = qc.getQueryData<ChatMessage[]>(MESSAGE_KEYS.list('s1'));
    expect(cached?.[0].feedback).toEqual({ rating: -1, comment: 'off', updated_at: 'u1' });
  });

  it('clears feedback in the cache', async () => {
    clearFeedback.mockResolvedValue(undefined);
    listMessages.mockReturnValue(new Promise(() => {}));
    const { qc, result } = setup();
    qc.setQueryData(MESSAGE_KEYS.list('s1'), [
      { ...reply, feedback: { rating: 1, comment: null, updated_at: 'u' } },
    ]);
    act(() => result.current.clear.mutate('m1'));
    await waitFor(() => expect(result.current.clear.isSuccess).toBe(true));
    expect(qc.getQueryData<ChatMessage[]>(MESSAGE_KEYS.list('s1'))?.[0].feedback).toBeNull();
  });
});
