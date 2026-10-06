/** CHAT-D-2 — the feedback bar shows the saved state and saves changes. */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ChatFeedbackBar } from './ChatFeedbackBar';
import { ChatMessage } from './ChatMessage';
import type { ChatMessage as ChatMessageType } from './types/chat.types';

const saved = (rating: -1 | 1, comment: string | null = null) => ({
  rating, comment, updated_at: '2026-10-06T00:00:00Z',
});

describe('ChatFeedbackBar', () => {
  it('shows the saved thumbs and comment', () => {
    render(<ChatFeedbackBar feedback={saved(-1, 'wrong total')} onSubmit={vi.fn()} onClear={vi.fn()} />);
    expect(screen.getByLabelText('Bad response')).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByLabelText('Good response')).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByTestId('feedback-comment')).toHaveTextContent('wrong total');
  });

  it('rates, switches keeping the comment, and clears on the active thumb', () => {
    const onSubmit = vi.fn();
    const onClear = vi.fn();
    const { rerender } = render(
      <ChatFeedbackBar feedback={null} onSubmit={onSubmit} onClear={onClear} />,
    );
    fireEvent.click(screen.getByLabelText('Good response'));
    expect(onSubmit).toHaveBeenLastCalledWith(1, null);

    rerender(<ChatFeedbackBar feedback={saved(1, 'nice')} onSubmit={onSubmit} onClear={onClear} />);
    fireEvent.click(screen.getByLabelText('Bad response'));
    expect(onSubmit).toHaveBeenLastCalledWith(-1, 'nice');
    fireEvent.click(screen.getByLabelText('Good response'));
    expect(onClear).toHaveBeenCalledTimes(1);
  });

  it('adds a comment to a rating', () => {
    const onSubmit = vi.fn();
    render(<ChatFeedbackBar feedback={saved(-1)} onSubmit={onSubmit} onClear={vi.fn()} />);
    fireEvent.click(screen.getByLabelText('Add feedback comment'));
    fireEvent.change(screen.getByLabelText('Feedback comment'), {
      target: { value: '  missed the tax line  ' },
    });
    fireEvent.click(screen.getByText('Save comment'));
    expect(onSubmit).toHaveBeenCalledWith(-1, 'missed the tax line');
  });

  it('offers no comment box before a rating is chosen', () => {
    render(<ChatFeedbackBar feedback={null} onSubmit={vi.fn()} onClear={vi.fn()} />);
    expect(screen.queryByLabelText('Add feedback comment')).toBeNull();
  });
});

function msg(over: Partial<ChatMessageType> = {}): ChatMessageType {
  return {
    id: 'm1', session_id: 's1', role: 'assistant', content: 'All green.', metadata: {},
    intent: null, goal_id: null, branch_id: null, parent_message_id: null,
    created_at: new Date().toISOString(), feedback: null, ...over,
  };
}

describe('ChatMessage feedback', () => {
  it('shows the bar on a saved assistant reply and reports the message id', () => {
    const onFeedback = vi.fn();
    render(<ChatMessage message={msg({ feedback: saved(1) })} onFeedback={onFeedback} />);
    expect(screen.getByLabelText('Good response')).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(screen.getByLabelText('Bad response'));
    expect(onFeedback).toHaveBeenCalledWith('m1', -1, null);
  });

  it('hides the bar on user messages, local unsaved replies and while streaming', () => {
    const onFeedback = vi.fn();
    const { rerender } = render(
      <ChatMessage message={msg({ role: 'user' })} onFeedback={onFeedback} />,
    );
    expect(screen.queryByTestId('feedback-bar')).toBeNull();
    const local = msg();
    delete local.feedback;
    rerender(<ChatMessage message={local} onFeedback={onFeedback} />);
    expect(screen.queryByTestId('feedback-bar')).toBeNull();
    rerender(<ChatMessage message={msg()} isStreaming onFeedback={onFeedback} />);
    expect(screen.queryByTestId('feedback-bar')).toBeNull();
  });
});
