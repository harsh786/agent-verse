/** Phase 7 — DB-vs-local message de-duplication. */
import { describe, it, expect } from 'vitest';
import { mergeChatMessages } from './mergeMessages';
import type { ChatMessage } from './types/chat.types';

function msg(partial: Partial<ChatMessage>): ChatMessage {
  return {
    id: 'x',
    session_id: 's1',
    role: 'assistant',
    content: '',
    metadata: {},
    intent: null,
    goal_id: null,
    branch_id: null,
    parent_message_id: null,
    created_at: new Date().toISOString(),
    ...partial,
  };
}

describe('mergeChatMessages', () => {
  it('drops a local message whose id already exists in the DB copy', () => {
    const db = [msg({ id: 'a', role: 'user', content: 'hi' })];
    const local = [msg({ id: 'a', role: 'user', content: 'hi' })];
    expect(mergeChatMessages(db, local)).toHaveLength(1);
  });

  it('shows a streamed reply once when the persisted copy has the same role+content', () => {
    const db = [
      msg({ id: 'u1', role: 'user', content: 'question' }),
      msg({ id: 'srv_a1', role: 'assistant', content: 'the answer' }),
    ];
    // local optimistic copy has a different (local_) id but identical content.
    const local = [msg({ id: 'local_123', role: 'assistant', content: 'the answer' })];
    const merged = mergeChatMessages(db, local);
    const answers = merged.filter((m) => m.role === 'assistant' && m.content === 'the answer');
    expect(answers).toHaveLength(1);
    expect(merged).toHaveLength(2);
  });

  it('keeps the empty pending assistant placeholder while streaming', () => {
    const db = [msg({ id: 'u1', role: 'user', content: 'q' })];
    const local = [msg({ id: '__pending_assistant__', role: 'assistant', content: '' })];
    const merged = mergeChatMessages(db, local);
    expect(merged.some((m) => m.id === '__pending_assistant__')).toBe(true);
  });

  it('keeps distinct local messages that are not in the DB', () => {
    const db = [msg({ id: 'u1', role: 'user', content: 'q' })];
    const local = [msg({ id: 'local_u', role: 'user', content: 'a brand new turn' })];
    expect(mergeChatMessages(db, local)).toHaveLength(2);
  });
});
