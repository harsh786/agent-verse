import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, test, vi } from 'vitest';
import { OrgChart, type OrgNode } from './OrgChart';

const ROOT: OrgNode = {
  id: 'org', name: 'Acme', role: 'org',
  children: [
    {
      id: 'team-a', name: 'Team A', role: 'team',
      children: [{ id: 'bot-1', name: 'Bot One', role: 'agent', status: 'running' }],
    },
  ],
};

afterEach(() => vi.restoreAllMocks());

describe('OrgChart', () => {
  test('renders the root node with its name and role inside a tree', () => {
    render(<OrgChart root={ROOT} />);
    expect(screen.getByRole('tree', { name: /organization chart/i })).toBeInTheDocument();
    expect(screen.getByText('Acme')).toBeInTheDocument();
    // Root button exposes name + role as its accessible label.
    expect(screen.getByRole('button', { name: 'Acme (org)' })).toBeInTheDocument();
  });

  test('expands the first two levels so children are visible by default', () => {
    render(<OrgChart root={ROOT} />);
    expect(screen.getByText('Team A')).toBeInTheDocument();
    expect(screen.getByText('Bot One')).toBeInTheDocument();
  });

  test('invokes onNodeClick with the clicked node', async () => {
    const onNodeClick = vi.fn();
    render(<OrgChart root={ROOT} onNodeClick={onNodeClick} />);
    await userEvent.click(screen.getByRole('button', { name: 'Bot One (agent)' }));
    expect(onNodeClick).toHaveBeenCalledTimes(1);
    expect(onNodeClick.mock.calls[0][0]).toMatchObject({ id: 'bot-1', name: 'Bot One' });
  });

  test('collapsing a parent node hides its descendants', async () => {
    render(<OrgChart root={ROOT} />);
    expect(screen.getByText('Bot One')).toBeInTheDocument();
    // Clicking a node that has children toggles it collapsed.
    await userEvent.click(screen.getByRole('button', { name: 'Team A (team)' }));
    expect(screen.queryByText('Bot One')).not.toBeInTheDocument();
  });
});
