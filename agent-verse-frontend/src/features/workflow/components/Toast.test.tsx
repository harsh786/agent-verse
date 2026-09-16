import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, test, vi } from 'vitest';
import { WorkflowToastStack, type ToastMessage } from './Toast';

afterEach(() => vi.restoreAllMocks());

function makeToast(overrides: Partial<ToastMessage> = {}): ToastMessage {
  // duration 0 = sticky, so the RAF progress timer never auto-dismisses during a test.
  return { id: 't1', type: 'info', title: 'Heads up', duration: 0, ...overrides };
}

describe('WorkflowToastStack', () => {
  test('renders the toast title and description', () => {
    render(
      <WorkflowToastStack
        toasts={[makeToast({ title: 'Deploy started', description: 'Rolling out v2' })]}
        onDismiss={vi.fn()}
      />,
    );
    expect(screen.getByText('Deploy started')).toBeInTheDocument();
    expect(screen.getByText('Rolling out v2')).toBeInTheDocument();
  });

  test('error toasts use role="alert", others use role="status"', () => {
    render(
      <WorkflowToastStack
        toasts={[
          makeToast({ id: 'e', type: 'error', title: 'Boom', duration: 0 }),
          makeToast({ id: 's', type: 'success', title: 'All good', duration: 0 }),
        ]}
        onDismiss={vi.fn()}
      />,
    );
    expect(screen.getByRole('alert')).toHaveTextContent('Boom');
    expect(screen.getByRole('status')).toHaveTextContent('All good');
  });

  test('dismiss button invokes onDismiss with the toast id', async () => {
    const onDismiss = vi.fn();
    render(
      <WorkflowToastStack toasts={[makeToast({ id: 'abc', duration: 0 })]} onDismiss={onDismiss} />,
    );
    await userEvent.click(screen.getByRole('button', { name: /dismiss notification/i }));
    expect(onDismiss).toHaveBeenCalledWith('abc');
  });

  test('action button fires the provided callback', async () => {
    const onClick = vi.fn();
    render(
      <WorkflowToastStack
        toasts={[makeToast({ title: 'Undo?', action: { label: 'Undo', onClick } })]}
        onDismiss={vi.fn()}
      />,
    );
    await userEvent.click(screen.getByRole('button', { name: 'Undo' }));
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  test('renders every toast in the stack', () => {
    render(
      <WorkflowToastStack
        toasts={[
          makeToast({ id: '1', title: 'First' }),
          makeToast({ id: '2', title: 'Second' }),
          makeToast({ id: '3', title: 'Third' }),
        ]}
        onDismiss={vi.fn()}
      />,
    );
    expect(screen.getByText('First')).toBeInTheDocument();
    expect(screen.getByText('Second')).toBeInTheDocument();
    expect(screen.getByText('Third')).toBeInTheDocument();
  });

  test('loading toast renders its title and never shows a progress track', () => {
    const { container } = render(
      <WorkflowToastStack
        toasts={[makeToast({ id: 'l', type: 'loading', title: 'Working…', duration: 5000 })]}
        onDismiss={vi.fn()}
      />,
    );
    expect(screen.getByText('Working…')).toBeInTheDocument();
    // The progress bar is suppressed for loading toasts (inline width style).
    expect(container.querySelector('[style*="width"]')).toBeNull();
  });
});
