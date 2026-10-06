/**
 * Tests for BillingPage — plan, usage meters, plan comparison, invoices and the
 * Razorpay upgrade modal.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, fireEvent, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import BillingPage from './BillingPage';

// Limits as the API serves them: the enforced plan limits (QA-17).
const PLANS = [
  { plan_id: 'free', name: 'Free', prices: { monthly_inr: 0, annual_inr: 0, monthly_paise: 0, annual_paise: 0 }, limits: { goals_per_day: 25, agents: 3 }, razorpay_key_id: 'rzp_test' },
  { plan_id: 'starter', name: 'Starter', prices: { monthly_inr: 29, annual_inr: 278, monthly_paise: 2900, annual_paise: 27840 }, limits: { goals_per_day: 100, agents: 10 }, razorpay_key_id: 'rzp_test' },
  { plan_id: 'professional', name: 'Professional', prices: { monthly_inr: 99, annual_inr: 948, monthly_paise: 9900, annual_paise: 94800 }, limits: { goals_per_day: 1000, agents: 50 }, razorpay_key_id: 'rzp_test' },
];

interface Overrides {
  subscriptionStatus?: number;
  usageStatus?: number;
  plansStatus?: number;
  subscription?: unknown;
  usage?: unknown;
  invoices?: unknown;
}

function mockFetch(o: Overrides = {}) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    const json = (payload: unknown, status = 200) =>
      new Response(status === 204 ? null : JSON.stringify(payload), { status, headers: { 'Content-Type': 'application/json' } });

    if (url.includes('/billing/subscription'))
      return json(o.subscription ?? { plan: 'starter', limits: {}, total_cost_usd: 12.5 }, o.subscriptionStatus ?? 200);
    if (url.includes('/billing/usage'))
      return json(o.usage ?? { usage: { goals: 42, llm_tokens: 12000, tool_calls: 7 } }, o.usageStatus ?? 200);
    if (url.includes('/billing/plans'))
      return json(PLANS, o.plansStatus ?? 200);
    if (url.includes('/billing/invoices'))
      return json(o.invoices ?? [], 200);
    return json({});
  });
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><BillingPage /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true, ssoMode: false, accessToken: '' });
});
afterEach(() => vi.restoreAllMocks());

describe('BillingPage', () => {
  test('renders the page header', async () => {
    mockFetch();
    renderPage();
    expect(await screen.findByRole('heading', { name: /Billing & Usage/i })).toBeInTheDocument();
    expect(screen.getByText(/Manage your plan and monitor usage/i)).toBeInTheDocument();
  });

  test('shows the current plan badge and this-month cost', async () => {
    mockFetch({ subscription: { plan: 'starter', limits: {}, total_cost_usd: 12.5 } });
    renderPage();
    const card = (await screen.findByRole('heading', { name: 'Current Plan' })).closest('div.rounded-lg') as HTMLElement;
    expect(await within(card).findByText('Starter')).toBeInTheDocument();
    expect(within(card).getByText('$12.50')).toBeInTheDocument();
    expect(within(card).getByRole('button', { name: /Upgrade Plan/i })).toBeInTheDocument();
  });

  test('renders a billing-not-available message when the subscription request fails', async () => {
    mockFetch({ subscriptionStatus: 500 });
    renderPage();
    expect(await screen.findByText(/Billing information not available/i)).toBeInTheDocument();
  });

  test('renders usage meters from the usage response', async () => {
    mockFetch({ usage: { usage: { goals: 42, llm_tokens: 12000, tool_calls: 7 } } });
    renderPage();
    expect(await screen.findByText('Goals')).toBeInTheDocument();
    expect(screen.getByText('LLM Tokens')).toBeInTheDocument();
    expect(screen.getByText('Tool Calls')).toBeInTheDocument();
    expect(screen.getByText('42')).toBeInTheDocument();
    expect(screen.getByText('12,000')).toBeInTheDocument();
  });

  test('shows the enforced daily goal limit from the subscription, not a made-up number', async () => {
    mockFetch({
      subscription: { plan: 'starter', limits: { goals_per_day: 100, agents: 10 }, total_cost_usd: 0 },
      usage: { usage: { goals: 42, llm_tokens: 12000, tool_calls: 7 } },
    });
    renderPage();
    expect(await screen.findByText(/Plan limit: 100 goals\/day/)).toBeInTheDocument();
    // Tokens and tool calls are not capped by any plan: no invented limit.
    expect(screen.queryByText(/1,000,000/)).not.toBeInTheDocument();
    expect(screen.queryByText(/10,000/)).not.toBeInTheDocument();
    expect(screen.getAllByText(/Not limited by plan/).length).toBe(2);
  });

  test('falls back to the plan list for the daily goal limit', async () => {
    mockFetch({ subscription: { plan: 'professional', total_cost_usd: 0 } });
    renderPage();
    expect(await screen.findByText(/Plan limit: 1,000 goals\/day/)).toBeInTheDocument();
  });

  test('without any API limit no goal limit is invented', async () => {
    mockFetch({ subscription: { plan: 'mystery', total_cost_usd: 0 } });
    renderPage();
    expect(await screen.findByText('Goals')).toBeInTheDocument();
    expect(screen.queryByText(/Plan limit:/)).not.toBeInTheDocument();
  });

  test('renders a usage-not-available message when the usage request fails', async () => {
    mockFetch({ usageStatus: 500 });
    renderPage();
    expect(await screen.findByText(/Usage data not available/i)).toBeInTheDocument();
  });

  test('lists available plans, marking the current one and offering Select on others', async () => {
    mockFetch({ subscription: { plan: 'starter', limits: {}, total_cost_usd: 0 } });
    renderPage();
    const plansCard = (await screen.findByRole('heading', { name: 'Available Plans' })).closest('div.rounded-lg') as HTMLElement;
    expect(await within(plansCard).findByText('Professional')).toBeInTheDocument();
    expect(within(plansCard).getByText('Current Plan')).toBeInTheDocument();
    expect(within(plansCard).getAllByRole('button', { name: /^Select$/i }).length).toBeGreaterThan(0);
  });

  test('the free plan is listed with its limits but cannot be bought', async () => {
    mockFetch({ subscription: { plan: 'starter', limits: {}, total_cost_usd: 0 } });
    renderPage();
    const plansCard = (await screen.findByRole('heading', { name: 'Available Plans' })).closest('div.rounded-lg') as HTMLElement;
    const freeCard = (await within(plansCard).findByRole('heading', { name: 'Free' })).parentElement as HTMLElement;
    expect(within(freeCard).getByText(/goals per day: 25/)).toBeInTheDocument();
    expect(within(freeCard).queryByRole('button', { name: /^Select$/i })).not.toBeInTheDocument();
    // Professional is still purchasable.
    const proCard = within(plansCard).getByRole('heading', { name: 'Professional' }).parentElement as HTMLElement;
    expect(within(proCard).getByRole('button', { name: /^Select$/i })).toBeInTheDocument();
  });

  test('renders a plan-not-available message when the plans request fails', async () => {
    mockFetch({ plansStatus: 500 });
    renderPage();
    expect(await screen.findByText(/Plan information not available/i)).toBeInTheDocument();
  });

  test('shows "No invoices yet" when there are none', async () => {
    mockFetch({ invoices: [] });
    renderPage();
    expect(await screen.findByText(/No invoices yet/i)).toBeInTheDocument();
  });

  test('lists invoices with amount, status and a PDF link when present', async () => {
    mockFetch({ invoices: [{ id: 'inv1', date: '2026-02-01', amount_usd: 29, status: 'paid', pdf_url: 'https://x/inv1.pdf' }] });
    renderPage();
    expect(await screen.findByText('$29.00')).toBeInTheDocument();
    expect(screen.getByText('paid')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /PDF/i })).toHaveAttribute('href', 'https://x/inv1.pdf');
  });

  test('clicking Upgrade opens the Razorpay checkout modal with a billing-cycle choice', async () => {
    mockFetch({ subscription: { plan: 'starter', limits: {}, total_cost_usd: 0 } });
    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: /Upgrade Plan/i }));
    expect(await screen.findByRole('heading', { name: /Upgrade to Starter/i })).toBeInTheDocument();
    expect(screen.getByText(/Choose your billing cycle/i)).toBeInTheDocument();
    expect(screen.getByText(/Pay ₹29 with Razorpay/i)).toBeInTheDocument();
    // Switching to annual updates the pay amount.
    fireEvent.click(screen.getByText('annual'));
    expect(screen.getByText(/Pay ₹278 with Razorpay/i)).toBeInTheDocument();
  });
});
