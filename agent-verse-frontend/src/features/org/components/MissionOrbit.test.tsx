import { render, screen } from '@testing-library/react';
import React, { type ReactNode } from 'react';
import { describe, expect, test, vi } from 'vitest';
import type { OrgMission } from '../types';

vi.mock('framer-motion', async (importOriginal) => {
  const actual = await importOriginal<typeof import('framer-motion')>();
  const makeStub = (tag: string) =>
    ({ children, ...props }: { children?: ReactNode; [k: string]: unknown }) =>
      React.createElement(tag, props as Record<string, unknown>, children);
  return {
    ...actual,
    useReducedMotion: () => true,
    AnimatePresence: ({ children }: { children?: ReactNode }) => <>{children}</>,
    motion: new Proxy(actual.motion as unknown as Record<string, unknown>, {
      get: (target, key: string) => (key in target ? target[key] : makeStub(key)),
    }),
  };
});

import { MissionOrbit } from './MissionOrbit';

function mission(partial: Partial<OrgMission> & { id: string; title: string }): OrgMission {
  return {
    tenant_id: 't', org_id: 'org-1', dept_id: null, assigned_team_id: null,
    objective: '', why: '', expected_outcome: '', status: 'active', priority: 'high',
    source: 'user', autonomy_level: null, budget_usd: null, deadline: null, tags: [],
    created_by: null, outputs: [], evidence: [], started_at: null, completed_at: null,
    created_at: '', updated_at: '', ...partial,
  } as OrgMission;
}

describe('MissionOrbit', () => {
  test('renders nothing when there are no active missions', () => {
    const { container } = render(<MissionOrbit missions={[]} />);
    expect(container.firstChild).toBeNull();
  });

  test('renders active missions with accessible labels and titles', () => {
    render(
      <MissionOrbit
        missions={[
          mission({ id: 'm1', title: 'Alpha', priority: 'high' }),
          mission({ id: 'm2', title: 'Beta', priority: 'low' }),
        ]}
      />,
    );
    expect(screen.getByLabelText('Mission: Alpha')).toBeInTheDocument();
    expect(screen.getByLabelText('Mission: Beta')).toBeInTheDocument();
    // Container announces the active count (plural).
    expect(screen.getByLabelText('2 active missions orbiting')).toBeInTheDocument();
  });

  test('excludes non-active missions from the orbit', () => {
    render(
      <MissionOrbit
        missions={[
          mission({ id: 'm1', title: 'Alpha', status: 'active' }),
          mission({ id: 'm2', title: 'Draft', status: 'draft' }),
        ]}
      />,
    );
    expect(screen.getByLabelText('Mission: Alpha')).toBeInTheDocument();
    expect(screen.queryByLabelText('Mission: Draft')).not.toBeInTheDocument();
    expect(screen.getByLabelText('1 active mission orbiting')).toBeInTheDocument();
  });

  test('caps the orbit at eight mission nodes', () => {
    const many = Array.from({ length: 12 }, (_, i) =>
      mission({ id: `m${i}`, title: `Mission${i}`, status: 'active' }),
    );
    render(<MissionOrbit missions={many} />);
    expect(screen.getAllByLabelText(/^Mission: /)).toHaveLength(8);
  });

  test('shows the truncated priority tag for a mission', () => {
    render(<MissionOrbit missions={[mission({ id: 'm1', title: 'Alpha', priority: 'critical' })]} />);
    // priority is upper-cased and sliced to 3 chars: "critical" → "CRI".
    expect(screen.getByText('CRI')).toBeInTheDocument();
  });
});
