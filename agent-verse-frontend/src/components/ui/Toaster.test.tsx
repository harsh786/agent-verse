import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, expect, test } from 'vitest';
import { useToastStore, toast } from '@/stores/toast';
import { Toaster } from './Toaster';

beforeEach(() => useToastStore.setState({ toasts: [] }));

test('renders a toast and dismisses on click', async () => {
  render(<Toaster />);
  toast({ kind: 'error', message: 'Network down' });
  expect(await screen.findByText('Network down')).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: /dismiss/i }));
  expect(screen.queryByText('Network down')).not.toBeInTheDocument();
});
