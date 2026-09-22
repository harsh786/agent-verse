import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, test, vi } from 'vitest';
import { ConfirmModal } from './ConfirmModal';

describe('ConfirmModal', () => {
  test('renders nothing when closed', () => {
    const { container } = render(
      <ConfirmModal open={false} title="Delete?" onConfirm={vi.fn()} onCancel={vi.fn()} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  test('renders the dialog with title, default labels, and no description when open', () => {
    render(<ConfirmModal open title="Delete agent?" onConfirm={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText('Delete agent?')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Confirm' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument();
    // No description passed → no describedby text and aria-describedby unset
    expect(screen.getByRole('dialog')).not.toHaveAttribute('aria-describedby');
  });

  test('renders a description and wires aria-describedby when provided', () => {
    render(
      <ConfirmModal
        open
        title="Delete agent?"
        description="This cannot be undone."
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.getByText('This cannot be undone.')).toBeInTheDocument();
    expect(screen.getByRole('dialog')).toHaveAttribute('aria-describedby', 'confirm-modal-desc');
  });

  test('custom confirmLabel/cancelLabel override the defaults', () => {
    render(
      <ConfirmModal
        open
        title="Archive?"
        confirmLabel="Archive"
        cancelLabel="Nevermind"
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.getByRole('button', { name: 'Archive' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Nevermind' })).toBeInTheDocument();
  });

  test.each(['danger', 'warning', 'info'] as const)('renders the %s variant without crashing', (variant) => {
    render(<ConfirmModal open title="Confirm" variant={variant} onConfirm={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });

  test('clicking Confirm calls onConfirm', async () => {
    const onConfirm = vi.fn();
    render(<ConfirmModal open title="Confirm" onConfirm={onConfirm} onCancel={vi.fn()} />);
    await userEvent.click(screen.getByRole('button', { name: 'Confirm' }));
    expect(onConfirm).toHaveBeenCalledOnce();
  });

  test('clicking Cancel calls onCancel', async () => {
    const onCancel = vi.fn();
    render(<ConfirmModal open title="Confirm" onConfirm={vi.fn()} onCancel={onCancel} />);
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(onCancel).toHaveBeenCalledOnce();
  });

  test('clicking the close (X) button calls onCancel', async () => {
    const onCancel = vi.fn();
    render(<ConfirmModal open title="Confirm" onConfirm={vi.fn()} onCancel={onCancel} />);
    await userEvent.click(screen.getByRole('button', { name: 'Close dialog' }));
    expect(onCancel).toHaveBeenCalledOnce();
  });

  test('clicking the backdrop calls onCancel', async () => {
    const onCancel = vi.fn();
    const { container } = render(<ConfirmModal open title="Confirm" onConfirm={vi.fn()} onCancel={onCancel} />);
    const backdrop = container.querySelector('[aria-hidden="true"]');
    expect(backdrop).toBeTruthy();
    await userEvent.click(backdrop as Element);
    expect(onCancel).toHaveBeenCalledOnce();
  });

  test('pressing Escape calls onCancel', async () => {
    const onCancel = vi.fn();
    render(<ConfirmModal open title="Confirm" onConfirm={vi.fn()} onCancel={onCancel} />);
    await userEvent.keyboard('{Escape}');
    expect(onCancel).toHaveBeenCalledOnce();
  });

  test('pressing Escape while closed does nothing (listener not attached)', async () => {
    const onCancel = vi.fn();
    render(<ConfirmModal open={false} title="Confirm" onConfirm={vi.fn()} onCancel={onCancel} />);
    await userEvent.keyboard('{Escape}');
    expect(onCancel).not.toHaveBeenCalled();
  });

  test('isLoading disables both buttons and shows a processing label on Confirm', () => {
    render(<ConfirmModal open title="Confirm" isLoading onConfirm={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.getByRole('button', { name: /processing/i })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled();
  });

  test('focuses the Cancel button shortly after opening', async () => {
    render(<ConfirmModal open title="Confirm" onConfirm={vi.fn()} onCancel={vi.fn()} />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Cancel' })).toHaveFocus());
  });
});
