import { render, screen } from '@testing-library/react';
import { describe, expect, test } from 'vitest';
import { StatusBadge } from './StatusBadge';

describe('StatusBadge', () => {
  test('labels a parked fan-out parent as waiting on its sub-goals', () => {
    render(<StatusBadge status="waiting_children" />);
    const badge = screen.getByLabelText('Status: Waiting on sub-goals');
    expect(badge).toHaveTextContent('Waiting on sub-goals');
    // Parked (no worker slot) — it is waiting, not actively running: no live pulse.
    expect(badge.querySelector('.animate-ping')).toBeNull();
  });

  test('normalises hyphenated status values', () => {
    render(<StatusBadge status="waiting-children" />);
    expect(screen.getByLabelText('Status: Waiting on sub-goals')).toBeInTheDocument();
  });

  test('keeps waiting_human distinct from waiting_children', () => {
    render(<StatusBadge status="waiting_human" />);
    expect(screen.getByLabelText('Status: Awaiting OK')).toBeInTheDocument();
  });

  test('falls back to the raw status for unknown values', () => {
    render(<StatusBadge status="mystery" />);
    expect(screen.getByLabelText('Status: mystery')).toHaveTextContent('mystery');
  });
});
