/** Phase 7 — collapsible reasoning/transparency panel. */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ChatReasoningPanel } from './ChatReasoningPanel';

describe('ChatReasoningPanel', () => {
  it('renders nothing when reasoning is empty', () => {
    const { container } = render(<ChatReasoningPanel reasoning="   " />);
    expect(container.firstChild).toBeNull();
  });

  it('is collapsed by default (toggle shows aria-expanded=false)', () => {
    render(<ChatReasoningPanel reasoning="step one then step two" />);
    const toggle = screen.getByRole('button', { name: /reasoning/i });
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
  });

  it('reveals the reasoning text when expanded', async () => {
    render(<ChatReasoningPanel reasoning="step one then step two" />);
    const toggle = screen.getByRole('button', { name: /reasoning/i });
    await userEvent.click(toggle);
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    expect(screen.getByText(/step one then step two/)).toBeDefined();
  });

  it('honours defaultOpen', () => {
    render(<ChatReasoningPanel reasoning="visible now" defaultOpen />);
    expect(screen.getByText('visible now')).toBeDefined();
  });
});
