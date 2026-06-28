import { render, screen } from '@testing-library/react';
import { expect, test, vi } from 'vitest';
import { ErrorBoundary } from './ErrorBoundary';

function Boom(): never { throw new Error('kaboom'); }

test('renders fallback when a child throws', () => {
  vi.spyOn(console, 'error').mockImplementation(() => {});
  render(<ErrorBoundary><Boom /></ErrorBoundary>);
  expect(screen.getByText(/something went wrong/i)).toBeInTheDocument();
  expect(screen.getByText(/kaboom/i)).toBeInTheDocument();
});
