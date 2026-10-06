/** CHAT-D-1 — folders: create, rename, delete, and move a chat into one. */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import { ChatSidebar } from './ChatSidebar';
import type { ChatFolder, ChatSession } from './types/chat.types';

function makeSession(over: Partial<ChatSession> = {}): ChatSession {
  return {
    id: 's1', tenant_id: 't1', title: 'Chat one', pinned: false, ttl_days: null,
    system_prompt: null, agent_id: null, folder_id: null, show_reasoning: false,
    proactive_suggestions: true, preferred_model: null,
    created_at: new Date().toISOString(), updated_at: new Date().toISOString(), ...over,
  } as ChatSession;
}

const WORK: ChatFolder = { id: 'f-work', name: 'Work', color: '#112233' };
const EMPTY: ChatFolder = { id: 'f-empty', name: 'Empty', color: '#445566' };

function renderSidebar(
  sessions: ChatSession[] = [makeSession()],
  folders: ChatFolder[] = [WORK, EMPTY],
) {
  const handlers = {
    onCreateFolder: vi.fn(),
    onRenameFolder: vi.fn(),
    onDeleteFolder: vi.fn(),
    onMoveSession: vi.fn(),
  };
  render(
    <ChatSidebar
      sessions={sessions} folders={folders} activeSessionId={undefined}
      onSelectSession={vi.fn()} onNewSession={vi.fn()} onDeleteSession={vi.fn()}
      onPinSession={vi.fn()} {...handlers}
    />,
  );
  return handlers;
}

describe('ChatSidebar folders', () => {
  it('creates a folder from the inline name input', () => {
    const h = renderSidebar();
    fireEvent.click(screen.getByLabelText('New folder'));
    const input = screen.getByLabelText('New folder name');
    fireEvent.change(input, { target: { value: '  Clients  ' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(h.onCreateFolder).toHaveBeenCalledWith('Clients');
  });

  it('does not create a folder with an empty name', () => {
    const h = renderSidebar();
    fireEvent.click(screen.getByLabelText('New folder'));
    fireEvent.keyDown(screen.getByLabelText('New folder name'), { key: 'Enter' });
    expect(h.onCreateFolder).not.toHaveBeenCalled();
  });

  it('shows an empty folder so a new folder is visible', () => {
    renderSidebar();
    expect(screen.getByTestId('folder-f-empty')).toHaveTextContent('Empty');
  });

  it('renames a folder', () => {
    const h = renderSidebar();
    fireEvent.click(screen.getByLabelText('Rename folder Work'));
    const input = screen.getByLabelText('Folder name');
    fireEvent.change(input, { target: { value: 'Clients' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(h.onRenameFolder).toHaveBeenCalledWith('f-work', 'Clients');
  });

  it('deletes a folder only on the confirming second click', () => {
    const h = renderSidebar();
    fireEvent.click(screen.getByLabelText('Delete folder Work'));
    expect(h.onDeleteFolder).not.toHaveBeenCalled();
    fireEvent.click(screen.getByLabelText('Confirm delete folder Work'));
    expect(h.onDeleteFolder).toHaveBeenCalledWith('f-work');
  });

  it('moves a chat into a folder and back out', () => {
    const h = renderSidebar([makeSession(), makeSession({ id: 's2', folder_id: 'f-work' })]);
    const row = screen.getByTestId('session-s1');
    fireEvent.change(within(row).getByLabelText('Move to folder'), {
      target: { value: 'f-work' },
    });
    expect(h.onMoveSession).toHaveBeenCalledWith('s1', 'f-work');

    const filed = screen.getByTestId('session-s2');
    expect(within(screen.getByTestId('folder-f-work')).getByTestId('session-s2')).toBe(filed);
    fireEvent.change(within(filed).getByLabelText('Move to folder'), { target: { value: '' } });
    expect(h.onMoveSession).toHaveBeenCalledWith('s2', null);
  });

  it('lists a chat whose folder is unknown as unfiled instead of hiding it', () => {
    renderSidebar([makeSession({ id: 's9', title: 'Orphan', folder_id: 'gone' })]);
    expect(screen.getByTestId('session-s9')).toHaveTextContent('Orphan');
  });
});
