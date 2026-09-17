import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import SecurityCenterPage, { SecurityScore } from '../SecurityCenterPage';

vi.mock('react-router-dom', async () => ({
  ...(await vi.importActual('react-router-dom')),
  useNavigate: () => vi.fn(),
}));

const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });

function renderPage() {
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <SecurityCenterPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe('SecurityCenterPage', () => {
  it('renders all 6 tabs', () => {
    renderPage();
    expect(screen.getByText('Security Center')).toBeTruthy();
    expect(screen.getByText('Agent Identity')).toBeTruthy();
    expect(screen.getByText('Governance')).toBeTruthy();
    expect(screen.getByText('Guardrails')).toBeTruthy();
    expect(screen.getByText('Audit Trail')).toBeTruthy();
    expect(screen.getByText('Scopes & Roles')).toBeTruthy();
    expect(screen.getByText('Limits')).toBeTruthy();
  });

  it('shows security score', () => {
    renderPage();
    expect(screen.getByText('Security Score')).toBeTruthy();
    expect(screen.getByText('78')).toBeTruthy();
  });

  it('renders the Agent Identity panel by default and highlights the identity tab', () => {
    renderPage();
    const tabContent = screen.getByTestId('tab-content');
    expect(within(tabContent).getByText('Per-Agent API Keys')).toBeTruthy();

    const identityTab = screen.getByTestId('tab-identity');
    expect(identityTab.className).toContain('bg-neural-violet');

    const governanceTab = screen.getByTestId('tab-governance');
    expect(governanceTab.className).not.toContain('bg-neural-violet text-white');
  });

  it('switches panels and active-tab styling when each tab is clicked', () => {
    renderPage();

    const sequence: Array<{ id: string; heading: string }> = [
      { id: 'governance', heading: 'Pending Approvals' },
      { id: 'guardrails', heading: 'Active Guardrail Layers' },
      { id: 'audit', heading: 'Hash Chain Integrity' },
      { id: 'scopes', heading: 'Built-in Role Hierarchy' },
      { id: 'limits', heading: 'Plan Limits Comparison' },
      { id: 'identity', heading: 'Per-Agent API Keys' },
    ];

    let previousHeading = 'Per-Agent API Keys';

    for (const { id, heading } of sequence) {
      const tabButton = screen.getByTestId(`tab-${id}`);
      fireEvent.click(tabButton);

      const tabContent = screen.getByTestId('tab-content');
      expect(within(tabContent).getByText(heading)).toBeTruthy();
      expect(tabButton.className).toContain('bg-neural-violet');

      if (heading !== previousHeading) {
        expect(within(tabContent).queryByText(previousHeading)).not.toBeInTheDocument();
      }
      previousHeading = heading;
    }
  });

  it('clicking the already-active tab keeps the same panel rendered', () => {
    renderPage();
    const identityTab = screen.getByTestId('tab-identity');
    fireEvent.click(identityTab);
    const tabContent = screen.getByTestId('tab-content');
    expect(within(tabContent).getByText('Per-Agent API Keys')).toBeTruthy();
  });
});

describe('SecurityScore', () => {
  it('renders the "good" (green) styling for a score >= 80', () => {
    const { container } = render(<SecurityScore score={92} />);
    expect(screen.getByText('92')).toBeTruthy();
    expect(container.querySelector('.text-verified-green')).toBeTruthy();
    expect(container.querySelector('.bg-verified-green\\/15')).toBeTruthy();
  });

  it('renders the "medium" (amber) styling for 60 <= score < 80', () => {
    const { container } = render(<SecurityScore score={78} />);
    expect(screen.getByText('78')).toBeTruthy();
    expect(container.querySelector('.text-risk-amber')).toBeTruthy();
    expect(container.querySelector('.bg-risk-amber\\/15')).toBeTruthy();
  });

  it('renders the "poor" (red) styling for a score < 60', () => {
    const { container } = render(<SecurityScore score={41} />);
    expect(screen.getByText('41')).toBeTruthy();
    expect(container.querySelector('.text-mission-red')).toBeTruthy();
    expect(container.querySelector('.bg-mission-red\\/15')).toBeTruthy();
  });
});
