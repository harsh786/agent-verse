import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, test, vi } from 'vitest';
import { KanbanBoard, type KanbanCard } from './KanbanBoard';

const CARDS: KanbanCard[] = [
  { id: 'c1', title: 'Draft the spec', status: 'backlog', priority: 'high' },
  { id: 'c2', title: 'Wire the API', status: 'running', agentName: 'Ada' },
  { id: 'c3', title: 'Ship it', status: 'done' },
];

afterEach(() => vi.restoreAllMocks());

describe('KanbanBoard', () => {
  test('renders all five columns and places each card in its column', () => {
    render(<KanbanBoard cards={CARDS} />);
    for (const label of ['Backlog', 'Planning', 'Running', 'Review', 'Done']) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    expect(screen.getByText('Draft the spec')).toBeInTheDocument();
    expect(screen.getByText('Wire the API')).toBeInTheDocument();
    expect(screen.getByText('Ship it')).toBeInTheDocument();
    // The running card surfaces its assigned agent.
    expect(screen.getByText('Ada')).toBeInTheDocument();
  });

  test('the Running column reports a card count of one', () => {
    render(<KanbanBoard cards={CARDS} />);
    const running = screen.getByRole('list', { name: 'Running' });
    // Only c2 is running.
    expect(running).toHaveTextContent('Wire the API');
    expect(running).toHaveTextContent('1');
  });

  test('clicking a column add button calls onAdd with that column status', async () => {
    const onAdd = vi.fn();
    render(<KanbanBoard cards={CARDS} onAdd={onAdd} />);
    await userEvent.click(screen.getByRole('button', { name: /Add card to Backlog/i }));
    expect(onAdd).toHaveBeenCalledWith('backlog');
  });

  test('dropping a card on a column calls onMove with the card id and target status', () => {
    const onMove = vi.fn();
    render(<KanbanBoard cards={CARDS} onMove={onMove} />);
    const doneColumn = screen.getByRole('list', { name: 'Done' });
    fireEvent.drop(doneColumn, { dataTransfer: { getData: () => 'c1' } });
    expect(onMove).toHaveBeenCalledWith('c1', 'done');
  });
});
