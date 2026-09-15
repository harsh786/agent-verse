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

  it('renders assistant markdown as rich HTML (bold, code, tables)', () => {
    const { container } = render(
      <ChatMessage
        message={makeMsg({
          role: 'assistant',
          content: 'Here is **bold** and `code`.\n\n```js\nconst x = 1;\n```\n\n| A | B |\n|---|---|\n| 1 | 2 |',
        })}
      />,
    );
    // Markdown became real elements, not raw asterisks/backticks.
    expect(container.querySelector('strong')).not.toBeNull();
    expect(container.querySelector('code')).not.toBeNull();
    expect(container.querySelector('table')).not.toBeNull();
    expect(container.textContent).not.toContain('**bold**');
  });

  it('keeps assistant content plain (with cursor) while streaming', () => {
    const { container } = render(
      <ChatMessage
        message={makeMsg({ role: 'assistant', content: '' })}
        isStreaming
        streamingTokens={'**not yet parsed**'}
      />,
    );
    // While streaming we show raw tokens (no markdown parse) + a cursor.
    expect(container.textContent).toContain('**not yet parsed**');
    expect(container.querySelector('.animate-pulse')).not.toBeNull();
  });

  it('does not markdown-render user messages', () => {
    const { container } = render(
      <ChatMessage message={makeMsg({ role: 'user', content: 'send **raw** to me' })} />,
    );
    expect(container.querySelector('strong')).toBeNull();
    expect(container.textContent).toContain('**raw**');
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
