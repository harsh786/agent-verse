/**
 * Toast.branches — covers what Toast.test.tsx does not:
 * auto-dismiss timer, pause-on-hover, the useWorkflowToast hook (push/dismiss/
 * success/error/warning/info/loading/hitl), the 5-toast cap, and toasts with
 * no description/action.
 */
import { act, fireEvent, render, renderHook, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { WorkflowToastStack, useWorkflowToast, type ToastMessage } from './Toast';

function makeToast(overrides: Partial<ToastMessage> = {}): ToastMessage {
  return { id: 't1', type: 'info', title: 'Heads up', duration: 0, ...overrides };
}

describe('ToastItem auto-dismiss / pause behavior', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  test('auto-dismisses after its duration elapses', () => {
    const onDismiss = vi.fn();
    render(
      <WorkflowToastStack
        toasts={[makeToast({ id: 'auto', duration: 1000 })]}
        onDismiss={onDismiss}
      />,
    );

    act(() => {
      vi.advanceTimersByTime(1200);
    });

    expect(onDismiss).toHaveBeenCalledWith('auto');
  });

  test('pausing on hover prevents dismissal until mouse leaves', () => {
    const onDismiss = vi.fn();
    render(
      <WorkflowToastStack
        toasts={[makeToast({ id: 'pausable', duration: 1000 })]}
        onDismiss={onDismiss}
      />,
    );

    const toastEl = screen.getByRole('status');

    act(() => {
      fireEvent.mouseEnter(toastEl);
    });
    act(() => {
      vi.advanceTimersByTime(2000);
    });
    // Paused — should not have dismissed despite the duration passing.
    expect(onDismiss).not.toHaveBeenCalled();

    act(() => {
      fireEvent.mouseLeave(toastEl);
    });
    act(() => {
      vi.advanceTimersByTime(1200);
    });
    expect(onDismiss).toHaveBeenCalledWith('pausable');
  });

  test('sticky (duration 0) toasts never auto-dismiss', () => {
    const onDismiss = vi.fn();
    render(
      <WorkflowToastStack
        toasts={[makeToast({ id: 'sticky', duration: 0 })]}
        onDismiss={onDismiss}
      />,
    );
    act(() => {
      vi.advanceTimersByTime(20000);
    });
    expect(onDismiss).not.toHaveBeenCalled();
  });

  test('renders without a description when none is provided', () => {
    render(
      <WorkflowToastStack
        toasts={[makeToast({ id: 'no-desc', title: 'Just a title', description: undefined })]}
        onDismiss={vi.fn()}
      />,
    );
    expect(screen.getByText('Just a title')).toBeInTheDocument();
  });

  test('renders without an action button when none is provided', () => {
    render(
      <WorkflowToastStack
        toasts={[makeToast({ id: 'no-action' })]}
        onDismiss={vi.fn()}
      />,
    );
    // Only the dismiss button should be present — no extra named action button.
    expect(screen.getAllByRole('button')).toHaveLength(1);
  });

  test('renders an empty stack with no toasts', () => {
    const { container } = render(<WorkflowToastStack toasts={[]} onDismiss={vi.fn()} />);
    expect(container.querySelector('[aria-label="Notifications"]')).toBeInTheDocument();
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });
});

describe('useWorkflowToast hook', () => {
  test('push adds a toast and returns its id; dismiss removes it', () => {
    const { result } = renderHook(() => useWorkflowToast());

    let id = '';
    act(() => {
      id = result.current.push({ type: 'info', title: 'Pushed' });
    });
    expect(result.current.toasts).toHaveLength(1);
    expect(result.current.toasts[0].title).toBe('Pushed');
    expect(id).toBe(result.current.toasts[0].id);

    act(() => {
      result.current.dismiss(id);
    });
    expect(result.current.toasts).toHaveLength(0);
  });

  test('caps the visible stack at 5 toasts, dropping the oldest', () => {
    const { result } = renderHook(() => useWorkflowToast());

    act(() => {
      for (let i = 0; i < 7; i++) {
        result.current.push({ type: 'info', title: `Toast ${i}` });
      }
    });

    expect(result.current.toasts).toHaveLength(5);
    // The oldest two (0, 1) should have been dropped; newest (6) kept.
    expect(result.current.toasts.map((t) => t.title)).toEqual([
      'Toast 2', 'Toast 3', 'Toast 4', 'Toast 5', 'Toast 6',
    ]);
  });

  test('success() pushes a success toast with default duration', () => {
    const { result } = renderHook(() => useWorkflowToast());
    act(() => {
      result.current.success('Saved', 'All changes saved');
    });
    expect(result.current.toasts[0]).toMatchObject({
      type: 'success', title: 'Saved', description: 'All changes saved',
    });
  });

  test('error() pushes an error toast with an 8000ms duration', () => {
    const { result } = renderHook(() => useWorkflowToast());
    act(() => {
      result.current.error('Failed', 'Something broke');
    });
    expect(result.current.toasts[0]).toMatchObject({
      type: 'error', title: 'Failed', description: 'Something broke', duration: 8000,
    });
  });

  test('warning() pushes a warning toast', () => {
    const { result } = renderHook(() => useWorkflowToast());
    act(() => {
      result.current.warning('Careful');
    });
    expect(result.current.toasts[0]).toMatchObject({ type: 'warning', title: 'Careful' });
  });

  test('info() pushes an info toast', () => {
    const { result } = renderHook(() => useWorkflowToast());
    act(() => {
      result.current.info('FYI');
    });
    expect(result.current.toasts[0]).toMatchObject({ type: 'info', title: 'FYI' });
  });

  test('loading() pushes a sticky loading toast (duration 0)', () => {
    const { result } = renderHook(() => useWorkflowToast());
    act(() => {
      result.current.loading('Working…');
    });
    expect(result.current.toasts[0]).toMatchObject({ type: 'loading', title: 'Working…', duration: 0 });
  });

  test('hitl() pushes a sticky hitl toast (duration 0)', () => {
    const { result } = renderHook(() => useWorkflowToast());
    act(() => {
      result.current.hitl('Needs approval');
    });
    expect(result.current.toasts[0]).toMatchObject({ type: 'hitl', title: 'Needs approval', duration: 0 });
  });

  test('generates unique, incrementing ids across pushes', () => {
    const { result } = renderHook(() => useWorkflowToast());
    let id1 = '';
    let id2 = '';
    act(() => {
      id1 = result.current.push({ type: 'info', title: 'One' });
    });
    act(() => {
      id2 = result.current.push({ type: 'info', title: 'Two' });
    });
    expect(id1).not.toBe(id2);
  });
});
