import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { SalesDashboard } from './SalesDashboard';
import type { Organization } from '../types';

const { useOrgHealthMock } = vi.hoisted(() => ({ useOrgHealthMock: vi.fn() }));
vi.mock('../hooks/useOrg', () => ({ useOrgHealth: useOrgHealthMock }));

const org = { id: 'org-3', name: 'Gamma LLC' } as Organization;

describe('SalesDashboard', () => {
  it('renders the sales overview heading with the org name', () => {
    useOrgHealthMock.mockReturnValue({ data: undefined });
    render(<SalesDashboard org={org} />);
    expect(screen.getByText('Sales Overview')).toBeInTheDocument();
    expect(screen.getByText(/Gamma LLC · Sales view/)).toBeInTheDocument();
  });

  it('renders all six KPI tiles', () => {
    useOrgHealthMock.mockReturnValue({ data: undefined });
    render(<SalesDashboard org={org} />);
    expect(screen.getByText('Pipeline Value')).toBeInTheDocument();
    expect(screen.getByText('Active Deals')).toBeInTheDocument();
    expect(screen.getByText('Conversion Rate')).toBeInTheDocument();
    expect(screen.getByText('Revenue (MTD)')).toBeInTheDocument();
    expect(screen.getByText('Accounts')).toBeInTheDocument();
    expect(screen.getByText('Sales Missions')).toBeInTheDocument();
  });

  it('shows the active_missions count from health data for Sales Missions', () => {
    useOrgHealthMock.mockReturnValue({ data: { active_missions: 4 } });
    render(<SalesDashboard org={org} />);
    expect(screen.getByText('4')).toBeInTheDocument();
  });

  it('falls back to 0 sales missions when health data is missing', () => {
    useOrgHealthMock.mockReturnValue({ data: undefined });
    render(<SalesDashboard org={org} />);
    expect(screen.getByText('0')).toBeInTheDocument();
  });
});
