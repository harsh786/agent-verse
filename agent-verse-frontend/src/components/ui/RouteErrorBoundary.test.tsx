import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { RouteErrorBoundary } from './RouteErrorBoundary';

/** A child that throws on demand so the boundary catches it. */
function Boom({ explode }: { explode: boolean }): JSX.Element {
  if (explode) throw new Error('kaboom in child');
  return <div>child is fine</div>;
}

function renderBoundary(ui: React.ReactNode, routeName?: string) {
  return render(
    <MemoryRouter>
      <RouteErrorBoundary routeName={routeName}>{ui}</RouteErrorBoundary>
    </MemoryRouter>,
  );
}

let errorSpy: ReturnType<typeof vi.spyOn>;
beforeEach(() => {
  // The boundary logs to console.error in componentDidCatch — silence it so the
  // expected error does not pollute test output.
  errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
});
afterEach(() => vi.restoreAllMocks());

describe('RouteErrorBoundary', () => {
  test('renders children unchanged when nothing throws', () => {
    renderBoundary(<div>all good</div>);
    expect(screen.getByText('all good')).toBeInTheDocument();
    expect(screen.queryByText('Something went wrong')).not.toBeInTheDocument();
  });

  test('renders the fallback UI with the thrown error message', () => {
    renderBoundary(<Boom explode />);
    expect(screen.getByText('Something went wrong')).toBeInTheDocument();
    expect(screen.getByText('kaboom in child')).toBeInTheDocument();
  });

  test('shows the route name in the fallback when provided', () => {
    renderBoundary(<Boom explode />, 'CheckoutPage');
    expect(screen.getByText('CheckoutPage')).toBeInTheDocument();
    // componentDidCatch logs with the route name tag (React also logs its own
    // error, so scan every arg of every call for the tag).
    expect(errorSpy).toHaveBeenCalled();
    const logged = errorSpy.mock.calls.some((call) =>
      call.some((arg) => String(arg).includes('[RouteErrorBoundary:CheckoutPage]')),
    );
    expect(logged).toBe(true);
  });

  test('offers Try Again and Go Home controls in the fallback', () => {
    renderBoundary(<Boom explode />);
    expect(screen.getByRole('button', { name: /Try Again/i })).toBeInTheDocument();
    const home = screen.getByRole('link', { name: /Go Home/i });
    expect(home).toHaveAttribute('href', '/');
  });

  test('Try Again resets error state and re-renders children', () => {
    // The child reads the throw flag at its OWN render time (not via a frozen
    // prop), so resetting the boundary re-renders it in a non-throwing state.
    function FlakyChild(): JSX.Element {
      if ((globalThis as { __explode?: boolean }).__explode) throw new Error('kaboom in child');
      return <div>child is fine</div>;
    }
    (globalThis as { __explode?: boolean }).__explode = true;
    renderBoundary(<FlakyChild />);
    expect(screen.getByText('Something went wrong')).toBeInTheDocument();

    (globalThis as { __explode?: boolean }).__explode = false;
    fireEvent.click(screen.getByRole('button', { name: /Try Again/i }));
    expect(screen.getByText('child is fine')).toBeInTheDocument();
    expect(screen.queryByText('Something went wrong')).not.toBeInTheDocument();
    delete (globalThis as { __explode?: boolean }).__explode;
  });

  test('falls back to "Unknown error" when the thrown value has no message', () => {
    function ThrowString(): JSX.Element {
      // Throwing a non-Error value → caught error has no `.message`, so the
      // `?? 'Unknown error'` fallback branch renders.
      throw 'plain string failure';
    }
    renderBoundary(<ThrowString />);
    expect(screen.getByText('Unknown error')).toBeInTheDocument();
  });
});
