import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, expect, test, vi } from 'vitest';
import { useToastStore, toast } from '@/stores/toast';
import type { ToastKind } from '@/stores/toast';
import { Toaster } from './Toaster';

const useReducedMotionMock = vi.fn(() => false);
vi.mock('framer-motion', async (importOriginal) => {
  const actual = await importOriginal<typeof import('framer-motion')>();
  return { ...actual, useReducedMotion: () => useReducedMotionMock() };
});

beforeEach(() => {
  useToastStore.setState({ toasts: [] });
  useReducedMotionMock.mockReturnValue(false);
});

test('respects prefers-reduced-motion by using the simplified fade transition', async () => {
  useReducedMotionMock.mockReturnValue(true);
  render(<Toaster />);
  await act(async () => {
    toast({ kind: 'info', message: 'Reduced motion toast' });
  });
  expect(await screen.findByText('Reduced motion toast')).toBeInTheDocument();
});

test('renders a toast and dismisses on click', async () => {
  render(<Toaster />);
  await act(async () => {
    toast({ kind: 'error', message: 'Network down' });
  });
  expect(await screen.findByText('Network down')).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: /dismiss/i }));
  // Use waitFor to handle AnimatePresence exit animation in framer-motion
  await waitFor(() => {
    expect(screen.queryByText('Network down')).not.toBeInTheDocument();
  });
});

test('clicking an action button fires its onClick and dismisses the toast', async () => {
  const onClick = vi.fn();
  render(<Toaster />);
  await act(async () => {
    toast({ kind: 'success', message: 'Deployed', action: { label: 'Undo', onClick } });
  });
  expect(await screen.findByText('Deployed')).toBeInTheDocument();

  await userEvent.click(screen.getByRole('button', { name: 'Undo' }));
  expect(onClick).toHaveBeenCalledTimes(1);
  await waitFor(() => {
    expect(screen.queryByText('Deployed')).not.toBeInTheDocument();
  });
});

test('an unrecognized kind falls back to the info config', async () => {
  render(<Toaster />);
  await act(async () => {
    toast({ kind: 'mystery' as ToastKind, message: 'Unknown kind' });
  });
  expect(await screen.findByText('Unknown kind')).toBeInTheDocument();
});

test('a warning toast (duration 0) does not auto-dismiss', async () => {
  vi.useFakeTimers();
  render(<Toaster />);
  act(() => {
    toast({ kind: 'warning', message: 'Careful now' });
  });
  expect(screen.getByText('Careful now')).toBeInTheDocument();

  await act(async () => {
    vi.advanceTimersByTime(10000);
  });
  expect(screen.getByText('Careful now')).toBeInTheDocument();
  vi.useRealTimers();
});

test('hovering over the notification region pauses auto-dismiss', async () => {
  render(<Toaster />);
  await act(async () => {
    toast({ kind: 'success', message: 'Hover pause' });
  });
  const region = screen.getByRole('region', { name: /notifications/i });

  await userEvent.hover(region);
  expect(screen.getByText('Hover pause')).toBeInTheDocument();

  await userEvent.unhover(region);
  expect(screen.getByText('Hover pause')).toBeInTheDocument();
});

test('an explicit duration overrides the kind default', async () => {
  vi.useFakeTimers();
  render(<Toaster />);
  act(() => {
    toast({ kind: 'success', message: 'Quick note', duration: 100 });
  });
  expect(screen.getByText('Quick note')).toBeInTheDocument();

  act(() => {
    vi.advanceTimersByTime(150);
  });
  vi.useRealTimers();
  await waitFor(() => {
    expect(screen.queryByText('Quick note')).not.toBeInTheDocument();
  });
});
