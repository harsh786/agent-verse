import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useUiStore } from '@/stores/ui';
import { CommandPalette } from './CommandPalette';

// Capture navigate() calls while keeping the real MemoryRouter.
const navigateMock = vi.fn();
vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>();
  return { ...actual, useNavigate: () => navigateMock };
});

function renderPalette() {
  return render(
    <MemoryRouter>
      <CommandPalette />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  navigateMock.mockClear();
  useUiStore.setState({ commandPaletteOpen: false });
});
afterEach(() => vi.restoreAllMocks());

describe('CommandPalette', () => {
  test('renders nothing while the palette is closed', () => {
    renderPalette();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  test('renders the command list when opened', () => {
    useUiStore.setState({ commandPaletteOpen: true });
    renderPalette();
    expect(screen.getByRole('dialog', { name: 'Command palette' })).toBeInTheDocument();
    expect(screen.getByText('Go to Dashboard')).toBeInTheDocument();
    expect(screen.getByText('Submit a goal')).toBeInTheDocument();
    expect(screen.getByText('Open marketplace')).toBeInTheDocument();
  });

  test('typing in the search box filters the command list', async () => {
    useUiStore.setState({ commandPaletteOpen: true });
    renderPalette();
    await userEvent.type(screen.getByLabelText('Command search'), 'agent');
    expect(screen.getByText('Create agent (meta-agent)')).toBeInTheDocument();
    expect(screen.queryByText('Go to Dashboard')).not.toBeInTheDocument();
  });

  test('shows an empty state when nothing matches', async () => {
    useUiStore.setState({ commandPaletteOpen: true });
    renderPalette();
    await userEvent.type(screen.getByLabelText('Command search'), 'zzzznope');
    expect(screen.getByText('No results found.')).toBeInTheDocument();
  });

  test('clicking a command navigates and closes the palette', async () => {
    useUiStore.setState({ commandPaletteOpen: true });
    renderPalette();
    await userEvent.click(screen.getByText('Go to Dashboard'));
    expect(navigateMock).toHaveBeenCalledWith('/dashboard');
    expect(useUiStore.getState().commandPaletteOpen).toBe(false);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  test('the close button dismisses the palette', async () => {
    useUiStore.setState({ commandPaletteOpen: true });
    renderPalette();
    await userEvent.click(screen.getByRole('button', { name: 'Close command palette' }));
    expect(useUiStore.getState().commandPaletteOpen).toBe(false);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});
