/** Phase 7 — empty thread shows suggestion prompts that send on click. */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ChatThread } from './ChatThread';

describe('ChatThread empty state', () => {
  it('renders suggestions and sends the prompt on click', () => {
    const onSuggestionSelect = vi.fn();
    render(
      <ChatThread
        messages={[]}
        isStreaming={false}
        streamingTokens=""
        currentEvent={null}
        onSuggestionSelect={onSuggestionSelect}
      />,
    );
    const suggestion = screen.getByText('Ask a question');
    fireEvent.click(suggestion);
    expect(onSuggestionSelect).toHaveBeenCalledTimes(1);
    expect(onSuggestionSelect.mock.calls[0][0]).toContain('SQL');
  });

  it('falls back to plain empty text without the handler', () => {
    render(
      <ChatThread messages={[]} isStreaming={false} streamingTokens="" currentEvent={null} />,
    );
    expect(screen.getByText('Start a conversation')).toBeDefined();
  });
});
