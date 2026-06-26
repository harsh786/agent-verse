import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { AuthPage } from './AuthPage';

function renderAuthPage() {
  render(
    <MemoryRouter initialEntries={['/auth']}>
      <Routes>
        <Route path="/auth" element={<AuthPage />} />
        <Route path="/dashboard" element={<div>Dashboard</div>} />
      </Routes>
    </MemoryRouter>
  );
}

describe('AuthPage', () => {
  beforeEach(() => {
    localStorage.clear();
    useAuthStore.setState({ apiKey: '', tenantId: '', plan: '', isAuthenticated: false });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  test('does not login when the API key is rejected by the backend', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ error: { message: 'Missing or invalid API key.' } }), {
        status: 401,
        headers: { 'Content-Type': 'application/json' },
      })
    );

    renderAuthPage();

    await userEvent.type(screen.getByLabelText(/tenant id/i), 'tenant-1');
    await userEvent.type(screen.getByLabelText(/api key/i), 'bad-key');
    await userEvent.click(screen.getByRole('button', { name: /sign in/i }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Invalid tenant ID or API key.');
    expect(screen.queryByText('Dashboard')).not.toBeInTheDocument();
    expect(localStorage.getItem('av_api_key')).toBeNull();
    expect(useAuthStore.getState().isAuthenticated).toBe(false);
  });
});
