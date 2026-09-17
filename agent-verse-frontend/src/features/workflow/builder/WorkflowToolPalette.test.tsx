import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, test, vi } from 'vitest';
import React, { type ReactNode } from 'react';

import { WorkflowToolPalette } from './WorkflowToolPalette';

// Stub framer-motion so AnimatePresence's exit animation doesn't leave
// collapsed category tiles lingering in the DOM (jsdom never fires the
// animation-complete callback that would normally unmount them). The stub is
// memoized per tag so React sees a stable component identity across renders
// — a fresh function per render would force-remount the whole subtree.
vi.mock('framer-motion', async (importOriginal) => {
  const actual = await importOriginal<typeof import('framer-motion')>();
  const stubCache = new Map<string, (props: { children?: ReactNode; [k: string]: unknown }) => ReactNode>();
  const makeStub = (tag: string) => {
    if (!stubCache.has(tag)) {
      stubCache.set(tag, ({ children, ...props }) =>
        React.createElement(tag, props as Record<string, unknown>, children));
    }
    return stubCache.get(tag)!;
  };
  return {
    ...actual,
    useReducedMotion: () => true,
    AnimatePresence: ({ children }: { children?: ReactNode }) => <>{children}</>,
    motion: new Proxy(actual.motion as unknown as Record<string, unknown>, {
      get: (target, key: string) => (key in target ? target[key] : makeStub(key)),
    }),
  };
});

describe('WorkflowToolPalette', () => {
  test('renders the search input and every category', () => {
    render(<WorkflowToolPalette />);
    expect(screen.getByLabelText('Search step types')).toBeInTheDocument();
    for (const cat of ['Entry', 'AI', 'Perception', 'Integration', 'Control', 'Human', 'Data', 'Dev']) {
      expect(screen.getByText(cat)).toBeInTheDocument();
    }
    // A representative tile from each of a couple categories is visible by default.
    expect(screen.getByLabelText('Drag Trigger step to canvas')).toBeInTheDocument();
    expect(screen.getByLabelText('Drag LLM Prompt step to canvas')).toBeInTheDocument();
  });

  test('typing a search term filters tiles by label and hides non-matching categories', () => {
    render(<WorkflowToolPalette />);
    const search = screen.getByLabelText('Search step types');
    fireEvent.change(search, { target: { value: 'trigger' } });

    // Only the Entry category (which contains "trigger") should remain.
    expect(screen.getByText('Entry')).toBeInTheDocument();
    expect(screen.queryByText('AI')).not.toBeInTheDocument();
    expect(screen.getByLabelText('Drag Trigger step to canvas')).toBeInTheDocument();
    expect(screen.queryByLabelText('Drag LLM Prompt step to canvas')).not.toBeInTheDocument();
  });

  test('search matches on the human-readable label, not just the raw type', () => {
    render(<WorkflowToolPalette />);
    const search = screen.getByLabelText('Search step types');
    // "Human Review" is the label for the "hitl" type — the raw type doesn't contain "review".
    fireEvent.change(search, { target: { value: 'review' } });
    expect(screen.getByLabelText('Drag Human Review step to canvas')).toBeInTheDocument();
    expect(screen.queryByLabelText('Drag Trigger step to canvas')).not.toBeInTheDocument();
  });

  test('a search with no matches renders no categories', () => {
    render(<WorkflowToolPalette />);
    const search = screen.getByLabelText('Search step types');
    fireEvent.change(search, { target: { value: 'zzz-no-such-step' } });
    expect(screen.queryByText('Entry')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /step to canvas/ })).not.toBeInTheDocument();
  });

  test('clicking a category header collapses and then re-expands its tiles', () => {
    render(<WorkflowToolPalette />);
    const entryHeader = screen.getByText('Entry').closest('button') as HTMLElement;
    expect(entryHeader).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByLabelText('Drag Trigger step to canvas')).toBeInTheDocument();

    fireEvent.click(entryHeader);
    expect(entryHeader).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByLabelText('Drag Trigger step to canvas')).not.toBeInTheDocument();

    fireEvent.click(entryHeader);
    expect(entryHeader).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByLabelText('Drag Trigger step to canvas')).toBeInTheDocument();
  });

  test('dragging a tile sets the dataTransfer payload and clears on drag end', () => {
    render(<WorkflowToolPalette />);
    const tile = screen.getByLabelText('Drag Trigger step to canvas');
    const setData = vi.fn();
    const dataTransfer = { setData, effectAllowed: '' };

    fireEvent.dragStart(tile, { dataTransfer });
    expect(setData).toHaveBeenCalledWith('application/workflow-step-type', 'trigger');
    expect(dataTransfer.effectAllowed).toBe('move');

    fireEvent.dragEnd(tile);
    // No throw and the tile remains in the document after drag end resets state.
    expect(tile).toBeInTheDocument();
  });

  test('search is case-insensitive', () => {
    render(<WorkflowToolPalette />);
    const search = screen.getByLabelText('Search step types');
    fireEvent.change(search, { target: { value: 'TRIGGER' } });
    expect(screen.getByLabelText('Drag Trigger step to canvas')).toBeInTheDocument();
  });

  test('each rendered tile shows both its icon and its label', () => {
    render(<WorkflowToolPalette />);
    const trigger = screen.getByLabelText('Drag Trigger step to canvas');
    expect(within(trigger).getByText('Trigger')).toBeInTheDocument();
  });
});
