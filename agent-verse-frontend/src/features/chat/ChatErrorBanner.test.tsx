/** Phase 7 — visible, retryable stream error. */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ChatErrorBanner } from './ChatErrorBanner';

describe('ChatErrorBanner', () => {
  it('shows the message as an alert', () => {
    render(<ChatErrorBanner message="Connection error" />);
    expect(screen.getByRole('alert').textContent).toContain('Connection error');
  });

  it('calls onRetry when Retry is clicked', () => {
    const onRetry = vi.fn();
    render(<ChatErrorBanner message="Stream error" onRetry={onRetry} />);
    fireEvent.click(screen.getByLabelText('Retry'));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it('omits Retry when no handler given', () => {
    render(<ChatErrorBanner message="x" />);
    expect(screen.queryByLabelText('Retry')).toBeNull();
  });
});
