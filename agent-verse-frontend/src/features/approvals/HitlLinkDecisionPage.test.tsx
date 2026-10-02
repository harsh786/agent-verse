/**
 * HITL-05: emailed approve/reject links land on a real page that decides only
 * after the signed-in approver confirms (POST, never on page load).
 */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, test, vi } from 'vitest';
import { HitlLinkDecisionPage } from './HitlLinkDecisionPage';

function renderAt(url: string) {
  return render(
    <MemoryRouter initialEntries={[url]}>
      <Routes>
        <Route path="/hitl/:requestId/:decision" element={<HitlLinkDecisionPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

afterEach(() => vi.restoreAllMocks());

describe('HitlLinkDecisionPage', () => {
  test('opening the link decides nothing; confirming POSTs the signed decision', async () => {
    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(jsonResponse(200, { request_id: 'req-1', status: 'approved', approver: 'kid-alice' }));
    renderAt('/hitl/req-1/approve?sig=abc&exp=123');

    expect(screen.getByRole('heading', { name: /approve agent action/i })).toBeInTheDocument();
    expect(fetchSpy).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole('button', { name: /confirm approve/i }));

    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent(/approved by kid-alice/i));
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const [url, init] = fetchSpy.mock.calls[0];
    expect(String(url)).toContain('/governance/hitl/req-1/approve?sig=abc&exp=123');
    expect((init as RequestInit).method).toBe('POST');
  });

  test('an already-decided request is reported, not shown as success', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse(409, { detail: 'not pending' }));
    renderAt('/hitl/req-2/reject?sig=abc&exp=123');
    await userEvent.click(screen.getByRole('button', { name: /confirm reject/i }));
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/no longer pending/i));
  });

  test('an incomplete link is rejected without calling the API', () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch');
    renderAt('/hitl/req-3/approve');
    expect(screen.getByRole('alert')).toHaveTextContent(/invalid approval link/i);
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});
