/**
 * Unit tests for ChatMessage component.
 */

import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ChatMessage } from './ChatMessage';
import type { ChatMessage as ChatMessageType } from './types/chat.types';

function makeMsg(overrides: Partial<ChatMessageType> = {}): ChatMessageType {
  return {
    id: 'm1',
    session_id: 's1',
    role: 'user',
    content: 'Hello world',
    metadata: {},
    intent: null,
    goal_id: null,
    branch_id: null,
    parent_message_id: null,
    created_at: new Date().toISOString(),
    ...overrides,
  };
}

describe('ChatMessage', () => {
  it('renders user message content', () => {
    render(<ChatMessage message={makeMsg({ content: 'Test message' })} />);
    expect(screen.getByText('Test message')).toBeDefined();
  });

  it('renders assistant message content', () => {
    render(<ChatMessage message={makeMsg({ role: 'assistant', content: 'I am an assistant.' })} />);
    expect(screen.getByText('I am an assistant.')).toBeDefined();
  });

  it('shows intent badge for QA messages', () => {
    render(<ChatMessage message={makeMsg({ intent: 'QA' })} />);
    expect(screen.getByText('Q&A')).toBeDefined();
  });

  it('shows intent badge for GOAL messages', () => {
    render(<ChatMessage message={makeMsg({ intent: 'GOAL' })} />);
    expect(screen.getByText('Goal')).toBeDefined();
  });

  it('shows intent badge for SCHEDULE messages', () => {
    render(<ChatMessage message={makeMsg({ intent: 'SCHEDULE' })} />);
    expect(screen.getByText('Schedule')).toBeDefined();
  });

  it('shows cursor when streaming', () => {
    const { container } = render(
      <ChatMessage message={makeMsg({ role: 'assistant', content: '' })} isStreaming streamingTokens="Hello" />,
    );
    // Streaming cursor is a blinking span
    const cursor = container.querySelector('.animate-pulse');
    expect(cursor).toBeTruthy();
  });

  it('shows Edit button for user messages when onEdit provided', () => {
    const onEdit = () => {};
    render(<ChatMessage message={makeMsg({ role: 'user' })} onEdit={onEdit} />);
    expect(screen.getByText('Edit')).toBeDefined();
  });

  it('does not show Edit button for assistant messages', () => {
    const onEdit = () => {};
    render(<ChatMessage message={makeMsg({ role: 'assistant' })} onEdit={onEdit} />);
    expect(screen.queryByText('Edit')).toBeNull();
  });
});
