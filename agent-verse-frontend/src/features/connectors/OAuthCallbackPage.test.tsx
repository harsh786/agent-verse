/**
 * Tests for OAuthCallbackPage — forwards the OAuth result to window.opener via
 * postMessage and renders a status.
 */
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import OAuthCallbackPage from './OAuthCallbackPage';

function renderAt(search: string) {
  return render(
    <MemoryRouter initialEntries={[`/connectors/oauth/callback${search}`]}>
      <OAuthCallbackPage />
    </MemoryRouter>,
  );
}

function setOpener(opener: unknown) {
  Object.defineProperty(window, 'opener', { value: opener, configurable: true, writable: true });
}

beforeEach(() => {
  vi.spyOn(window, 'close').mockImplementation(() => {});
});
afterEach(() => {
  setOpener(null);
  vi.restoreAllMocks();
});

describe('OAuthCallbackPage', () => {
  test('posts the code + state to the opener and shows a success state', async () => {
    const postMessage = vi.fn();
    setOpener({ postMessage });
    renderAt('?code=code-1&state=state-1');

    expect(await screen.findByText('Connected!')).toBeInTheDocument();
    expect(postMessage).toHaveBeenCalledWith(
      { type: 'oauth_callback', code: 'code-1', state: 'state-1' },
      window.location.origin,
    );
    expect(screen.getByText(/Authorization successful/i)).toBeInTheDocument();
  });

  test('forwards a provider error (using error_description) and shows the failure state', async () => {
    const postMessage = vi.fn();
    setOpener({ postMessage });
    renderAt('?error=access_denied&error_description=User%20said%20no');

    expect(await screen.findByText('Authorization Failed')).toBeInTheDocument();
    expect(postMessage).toHaveBeenCalledWith(
      { type: 'oauth_callback', error: 'User said no' },
      window.location.origin,
    );
    expect(screen.getByText('User said no')).toBeInTheDocument();
  });

  test('reports a missing code/state as an error to the opener', async () => {
    const postMessage = vi.fn();
    setOpener({ postMessage });
    renderAt('?foo=bar');

    expect(await screen.findByText('Authorization Failed')).toBeInTheDocument();
    expect(postMessage).toHaveBeenCalledWith(
      { type: 'oauth_callback', error: 'Missing code or state parameter' },
      window.location.origin,
    );
    expect(screen.getByText(/Invalid OAuth response/i)).toBeInTheDocument();
  });

  test('when opened directly (no opener) it shows an informational done state', async () => {
    setOpener(null);
    renderAt('?code=code-1&state=state-1');
    expect(await screen.findByText(/OAuth callback received/i)).toBeInTheDocument();
  });
});
