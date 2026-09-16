import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { CMODashboard } from './CMODashboard';
import type { Organization } from '../types';

const { useOrgHealthMock } = vi.hoisted(() => ({ useOrgHealthMock: vi.fn() }));
vi.mock('../hooks/useOrg', () => ({ useOrgHealth: useOrgHealthMock }));

const org = { id: 'org-1', name: 'Acme Corp' } as Organization;

describe('CMODashboard', () => {
  it('renders the marketing overview heading with the org name', () => {
    useOrgHealthMock.mockReturnValue({ data: undefined });
    render(<CMODashboard org={org} />);
    expect(screen.getByText('Marketing Overview')).toBeInTheDocument();
    expect(screen.getByText(/Acme Corp · CMO view/)).toBeInTheDocument();
  });

  it('renders all six KPI tiles', () => {
    useOrgHealthMock.mockReturnValue({ data: undefined });
    render(<CMODashboard org={org} />);
    expect(screen.getByText('Campaigns')).toBeInTheDocument();
    expect(screen.getByText('Growth Rate')).toBeInTheDocument();
    expect(screen.getByText('Leads Generated')).toBeInTheDocument();
    expect(screen.getByText('Conversion')).toBeInTheDocument();
    expect(screen.getByText('Content Pieces')).toBeInTheDocument();
    expect(screen.getByText('CAC')).toBeInTheDocument();
  });

  it('shows the active_missions count from health data for Campaigns', () => {
    useOrgHealthMock.mockReturnValue({ data: { active_missions: 7 } });
    render(<CMODashboard org={org} />);
    expect(screen.getByText('7')).toBeInTheDocument();
  });

  it('falls back to 0 campaigns when health data is missing', () => {
    useOrgHealthMock.mockReturnValue({ data: undefined });
    render(<CMODashboard org={org} />);
    expect(screen.getByText('0')).toBeInTheDocument();
  });
});
