/** Phase 7 — inline session rename. */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ChatSidebar } from './ChatSidebar';
import type { ChatSession } from './types/chat.types';

function makeSession(over: Partial<ChatSession> = {}): ChatSession {
  return {
    id: 's1', tenant_id: 't1', title: 'Old title', pinned: false, ttl_days: null,
    system_prompt: null, agent_id: null, folder_id: null, show_reasoning: false,
    proactive_suggestions: true, preferred_model: null,
    created_at: new Date().toISOString(), updated_at: new Date().toISOString(), ...over,
  } as ChatSession;
}

function renderSidebar(onRenameSession = vi.fn()) {
  render(
    <ChatSidebar
      sessions={[makeSession()]} folders={[]} activeSessionId={undefined}
      onSelectSession={vi.fn()} onNewSession={vi.fn()} onDeleteSession={vi.fn()}
      onPinSession={vi.fn()} onRenameSession={onRenameSession}
    />,
  );
  return onRenameSession;
}

describe('ChatSidebar rename', () => {
  it('renames on Enter with the new title', () => {
    const onRename = renderSidebar();
    fireEvent.click(screen.getByLabelText('Rename session'));
    const input = screen.getByLabelText('Rename session') as HTMLInputElement;
    fireEvent.change(input, { target: { value: 'New title' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(onRename).toHaveBeenCalledWith('s1', 'New title');
  });

  it('does not rename when unchanged', () => {
    const onRename = renderSidebar();
    fireEvent.click(screen.getByLabelText('Rename session'));
    const input = screen.getByLabelText('Rename session') as HTMLInputElement;
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(onRename).not.toHaveBeenCalled();
  });
});
