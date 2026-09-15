/** Phase 7 — inline edit-and-rerun replaces window.prompt in the message row. */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ChatMessage } from './ChatMessage';
import type { ChatMessage as ChatMessageType } from './types/chat.types';

const userMsg: ChatMessageType = {
  id: 'u1',
  session_id: 's1',
  role: 'user',
  content: 'original text',
  metadata: {},
  intent: null,
  goal_id: null,
  branch_id: null,
  parent_message_id: null,
  created_at: new Date().toISOString(),
};

describe('ChatMessage inline edit', () => {
  it('opens an inline textarea and submits the edited content (no window.prompt)', () => {
    const promptSpy = vi.spyOn(window, 'prompt');
    const onEdit = vi.fn();
    render(<ChatMessage message={userMsg} onEdit={onEdit} />);

    fireEvent.click(screen.getByLabelText('Edit message'));
    const ta = screen.getByTestId('edit-textarea-u1') as HTMLTextAreaElement;
    expect(ta.value).toBe('original text');

    fireEvent.change(ta, { target: { value: 'edited text' } });
    fireEvent.click(screen.getByTestId('edit-save-u1'));

    expect(onEdit).toHaveBeenCalledWith('u1', 'edited text');
    expect(promptSpy).not.toHaveBeenCalled();
  });

  it('cancels without calling onEdit', () => {
    const onEdit = vi.fn();
    render(<ChatMessage message={userMsg} onEdit={onEdit} />);
    fireEvent.click(screen.getByLabelText('Edit message'));
    fireEvent.click(screen.getByText('Cancel'));
    expect(onEdit).not.toHaveBeenCalled();
    expect(screen.queryByTestId('edit-textarea-u1')).toBeNull();
  });
});
