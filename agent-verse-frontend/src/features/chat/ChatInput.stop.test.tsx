/** Phase 7 — Stop button surfaces while streaming and cancels the stream. */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ChatInput } from './ChatInput';

describe('ChatInput stop button', () => {
  it('shows Stop while streaming and calls onStop, not onSend', () => {
    const onSend = vi.fn();
    const onStop = vi.fn();
    render(<ChatInput onSend={onSend} isLoading onStop={onStop} />);
    const stop = screen.getByTestId('stop-button');
    expect(screen.queryByTestId('send-button')).toBeNull();
    fireEvent.click(stop);
    expect(onStop).toHaveBeenCalledTimes(1);
    expect(onSend).not.toHaveBeenCalled();
  });

  it('shows Send (not Stop) when idle', () => {
    render(<ChatInput onSend={vi.fn()} isLoading={false} onStop={vi.fn()} />);
    expect(screen.getByTestId('send-button')).toBeDefined();
    expect(screen.queryByTestId('stop-button')).toBeNull();
  });
});
