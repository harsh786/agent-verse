import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { HRODashboard } from './HRODashboard';
import type { Organization } from '../types';

const { useOrgHealthMock } = vi.hoisted(() => ({ useOrgHealthMock: vi.fn() }));
vi.mock('../hooks/useOrg', () => ({ useOrgHealth: useOrgHealthMock }));

const org = { id: 'org-2', name: 'Beta Inc' } as Organization;

describe('HRODashboard', () => {
  it('renders the people overview heading with the org name', () => {
    useOrgHealthMock.mockReturnValue({ data: undefined });
    render(<HRODashboard org={org} />);
    expect(screen.getByText('People Overview')).toBeInTheDocument();
    expect(screen.getByText(/Beta Inc · CHRO view/)).toBeInTheDocument();
  });

  it('renders all six KPI tiles', () => {
    useOrgHealthMock.mockReturnValue({ data: undefined });
    render(<HRODashboard org={org} />);
    expect(screen.getByText('Total Agents')).toBeInTheDocument();
    expect(screen.getByText('Hiring')).toBeInTheDocument();
    expect(screen.getByText('Avg Reputation')).toBeInTheDocument();
    expect(screen.getByText('Performance Trend')).toBeInTheDocument();
    expect(screen.getByText('Culture Score')).toBeInTheDocument();
    expect(screen.getByText('Training Goals')).toBeInTheDocument();
  });

  it('shows the active_teams count from health data for Total Agents', () => {
    useOrgHealthMock.mockReturnValue({ data: { active_teams: 12 } });
    render(<HRODashboard org={org} />);
    expect(screen.getByText('12')).toBeInTheDocument();
  });

  it('falls back to 0 total agents when health data is missing', () => {
    useOrgHealthMock.mockReturnValue({ data: undefined });
    render(<HRODashboard org={org} />);
    expect(screen.getByText('0')).toBeInTheDocument();
  });
});
