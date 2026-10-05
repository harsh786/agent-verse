import { beforeEach, describe, expect, it } from 'vitest';
import { useEmergencyStore } from './emergency';

beforeEach(() => {
  localStorage.clear();
  useEmergencyStore.setState({
    isActive: false,
    activatedAt: null,
    cancelledGoals: 0,
    rejectedApprovals: 0,
  });
});

describe('useEmergencyStore', () => {
  it('starts inactive with zeroed stats', () => {
    const s = useEmergencyStore.getState();
    expect(s.isActive).toBe(false);
    expect(s.activatedAt).toBeNull();
    expect(s.cancelledGoals).toBe(0);
    expect(s.rejectedApprovals).toBe(0);
  });

  it('setActive marks the store active, timestamps it, and records stats', () => {
    useEmergencyStore.getState().setActive({ cancelledGoals: 4, rejectedApprovals: 2 });
    const s = useEmergencyStore.getState();
    expect(s.isActive).toBe(true);
    expect(s.cancelledGoals).toBe(4);
    expect(s.rejectedApprovals).toBe(2);
    expect(s.activatedAt).not.toBeNull();
    expect(new Date(s.activatedAt as string).toString()).not.toBe('Invalid Date');
  });

  it('setActive with zero stats still marks the store active', () => {
    useEmergencyStore.getState().setActive({ cancelledGoals: 0, rejectedApprovals: 0 });
    const s = useEmergencyStore.getState();
    expect(s.isActive).toBe(true);
    expect(s.cancelledGoals).toBe(0);
    expect(s.rejectedApprovals).toBe(0);
  });

  it('clear resets the store back to its inactive default', () => {
    useEmergencyStore.getState().setActive({ cancelledGoals: 5, rejectedApprovals: 3 });
    useEmergencyStore.getState().clear();
    const s = useEmergencyStore.getState();
    expect(s.isActive).toBe(false);
    expect(s.activatedAt).toBeNull();
    expect(s.cancelledGoals).toBe(0);
    expect(s.rejectedApprovals).toBe(0);
  });

  it('is never persisted to browser storage (the server is the source of truth)', () => {
    useEmergencyStore.getState().setActive({ cancelledGoals: 1, rejectedApprovals: 1 });
    expect(localStorage.getItem('agentverse-emergency')).toBeNull();
  });

  it('syncFromServer takes the counts from the server payload', () => {
    useEmergencyStore.getState().syncFromServer({
      active: true, activatedAt: '2026-09-29T10:00:00Z', cancelledGoals: 5, rejectedApprovals: 2,
    });
    const s = useEmergencyStore.getState();
    expect(s.cancelledGoals).toBe(5);
    expect(s.rejectedApprovals).toBe(2);
  });
});
