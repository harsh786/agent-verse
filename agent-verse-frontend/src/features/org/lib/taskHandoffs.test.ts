import { describe, it, expect } from 'vitest';
import { computeTaskHandoffs } from './taskHandoffs';
import type { OrgTask } from '../types';

function task(overrides: Partial<OrgTask>): OrgTask {
  return {
    id:          't1',
    tenant_id:   'tn-1',
    org_id:      'org-1',
    mission_id:  'm-1',
    title:       'Untitled task',
    objective:   '',
    status:      'running',
    priority:    'medium',
    assigned_to: 'agent-a',
    created_at:  '2026-09-01T00:00:00Z',
    updated_at:  '2026-09-01T00:00:00Z',
    ...overrides,
  };
}

describe('computeTaskHandoffs', () => {
  it('computes a handoff when a task completes and another task in the same mission is in flight for a different agent', () => {
    const prev = [
      task({ id: 't1', assigned_to: 'agent-a', status: 'running' }),
      task({ id: 't2', assigned_to: 'agent-b', status: 'queued', title: 'Draft the summary' }),
    ];
    const next = [
      task({ id: 't1', assigned_to: 'agent-a', status: 'completed' }),
      task({ id: 't2', assigned_to: 'agent-b', status: 'queued', title: 'Draft the summary' }),
    ];

    const handoffs = computeTaskHandoffs(prev, next);

    expect(handoffs).toEqual([
      {
        id:          't1->t2',
        missionId:   'm-1',
        fromAgentId: 'agent-a',
        toAgentId:   'agent-b',
        fromTaskId:  't1',
        toTaskId:    't2',
        label:       'Draft the summary',
      },
    ]);
  });

  it('computes the right path — picks the in-flight task, not a queued one belonging to a different mission', () => {
    const prev = [task({ id: 't1', status: 'running', assigned_to: 'agent-a' })];
    const next = [
      task({ id: 't1', status: 'completed', assigned_to: 'agent-a' }),
      task({ id: 't2', status: 'queued', assigned_to: 'agent-c', mission_id: 'm-OTHER', title: 'Wrong mission' }),
      task({ id: 't3', status: 'running', assigned_to: 'agent-b', mission_id: 'm-1', title: 'Correct next step' }),
    ];

    const handoffs = computeTaskHandoffs(prev, next);

    expect(handoffs).toHaveLength(1);
    expect(handoffs[0]).toMatchObject({ toTaskId: 't3', toAgentId: 'agent-b', label: 'Correct next step' });
  });

  it('does not emit a handoff for a task that was already completed last snapshot', () => {
    const prev = [task({ id: 't1', status: 'completed', assigned_to: 'agent-a' })];
    const next = [
      task({ id: 't1', status: 'completed', assigned_to: 'agent-a' }),
      task({ id: 't2', status: 'running', assigned_to: 'agent-b' }),
    ];

    expect(computeTaskHandoffs(prev, next)).toEqual([]);
  });

  it('does not emit a handoff when no other agent has in-flight work in the mission', () => {
    const prev = [task({ id: 't1', status: 'running', assigned_to: 'agent-a' })];
    const next = [
      task({ id: 't1', status: 'completed', assigned_to: 'agent-a' }),
      task({ id: 't2', status: 'completed', assigned_to: 'agent-b' }),
    ];

    expect(computeTaskHandoffs(prev, next)).toEqual([]);
  });

  it('ignores tasks with no mission or no assignee', () => {
    const prev = [task({ id: 't1', status: 'running', mission_id: null })];
    const next = [
      task({ id: 't1', status: 'completed', mission_id: null }),
      task({ id: 't2', status: 'running', assigned_to: 'agent-b' }),
    ];

    expect(computeTaskHandoffs(prev, next)).toEqual([]);
  });

  it('returns an empty list for two empty snapshots', () => {
    expect(computeTaskHandoffs([], [])).toEqual([]);
  });
});
