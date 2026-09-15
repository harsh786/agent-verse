/** Phase 7 — sidebar delete requires a two-click confirm (safety). */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ChatSidebar } from './ChatSidebar';
import type { ChatSession } from './types/chat.types';

function makeSession(over: Partial<ChatSession> = {}): ChatSession {
  return {
    id: 's1',
    tenant_id: 't1',
    title: 'My chat',
    pinned: false,
    ttl_days: null,
    system_prompt: null,
    agent_id: null,
    folder_id: null,
    show_reasoning: false,
    proactive_suggestions: true,
    preferred_model: null,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    ...over,
  } as ChatSession;
}

function renderSidebar(onDeleteSession = vi.fn()) {
  render(
    <ChatSidebar
      sessions={[makeSession()]}
      folders={[]}
      activeSessionId={undefined}
      onSelectSession={vi.fn()}
      onNewSession={vi.fn()}
      onDeleteSession={onDeleteSession}
      onPinSession={vi.fn()}
    />,
  );
  return onDeleteSession;
}

describe('ChatSidebar delete confirmation', () => {
  it('does not delete on the first click, deletes on the second', () => {
    const onDelete = renderSidebar();
    const btn = screen.getByLabelText('Delete session');
    fireEvent.click(btn);
    expect(onDelete).not.toHaveBeenCalled();
    // Now armed — label switches to confirm.
    const confirmBtn = screen.getByLabelText('Confirm delete session');
    fireEvent.click(confirmBtn);
    expect(onDelete).toHaveBeenCalledWith('s1');
  });
});
