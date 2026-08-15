/**
 * Vitest unit tests for trigger feature components.
 * Tests: types, filters store, form components, status badge.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import {
  TRIGGER_FAMILY_LABELS,
  TRIGGER_TYPE_FAMILY,
  type TriggerType,
  type TriggerFamily,
} from './types';
import { useTriggerFilterStore } from './triggerFilters';
import { TriggerStatusBadge } from './components/TriggerStatusBadge';
import { TimeFamilyForm } from './components/families/TimeFamilyForm';
import { GoalChainFamilyForm } from './components/families/GoalChainFamilyForm';
import { WebhookFamilyForm } from './components/families/WebhookFamilyForm';
import { IoTFamilyForm } from './components/families/IoTFamilyForm';


// ── Types module ──────────────────────────────────────────────────────────────

describe('TriggerType system', () => {
  it('has exactly 9 families', () => {
    const families = Object.keys(TRIGGER_FAMILY_LABELS) as TriggerFamily[];
    expect(families).toHaveLength(9);
  });

  it('covers all trigger type family mappings', () => {
    const mappedTypes = Object.keys(TRIGGER_TYPE_FAMILY) as TriggerType[];
    // All mapped types should have a valid family
    const validFamilies = new Set<TriggerFamily>(Object.keys(TRIGGER_FAMILY_LABELS) as TriggerFamily[]);
    for (const type of mappedTypes) {
      expect(validFamilies.has(TRIGGER_TYPE_FAMILY[type])).toBe(true);
    }
  });

  it('time family includes cron and interval', () => {
    expect(TRIGGER_TYPE_FAMILY['cron']).toBe('time');
    expect(TRIGGER_TYPE_FAMILY['interval']).toBe('time');
  });

  it('goal_chain family includes goal_completed and hitl_approved', () => {
    expect(TRIGGER_TYPE_FAMILY['goal_completed']).toBe('goal_chain');
    expect(TRIGGER_TYPE_FAMILY['hitl_approved']).toBe('goal_chain');
  });

  it('iot family includes mqtt and geofence', () => {
    expect(TRIGGER_TYPE_FAMILY['mqtt']).toBe('iot');
    expect(TRIGGER_TYPE_FAMILY['geofence']).toBe('iot');
  });

  it('webhook family includes github_webhook and stripe_webhook', () => {
    expect(TRIGGER_TYPE_FAMILY['github_webhook']).toBe('webhook');
    expect(TRIGGER_TYPE_FAMILY['stripe_webhook']).toBe('webhook');
  });

  it('all family labels are non-empty strings', () => {
    for (const [, label] of Object.entries(TRIGGER_FAMILY_LABELS)) {
      expect(typeof label).toBe('string');
      expect(label.length).toBeGreaterThan(0);
    }
  });
});


// ── Zustand filter store ──────────────────────────────────────────────────────

describe('useTriggerFilterStore', () => {
  beforeEach(() => {
    useTriggerFilterStore.setState({
      familyFilter: 'all',
      statusFilter: 'all',
      searchQuery: '',
    });
  });

  it('initial state is all/all/empty', () => {
    const state = useTriggerFilterStore.getState();
    expect(state.familyFilter).toBe('all');
    expect(state.statusFilter).toBe('all');
    expect(state.searchQuery).toBe('');
  });

  it('setFamilyFilter updates family', () => {
    useTriggerFilterStore.getState().setFamilyFilter('time');
    expect(useTriggerFilterStore.getState().familyFilter).toBe('time');
  });

  it('setStatusFilter updates status', () => {
    useTriggerFilterStore.getState().setStatusFilter('paused');
    expect(useTriggerFilterStore.getState().statusFilter).toBe('paused');
  });

  it('setSearchQuery updates query', () => {
    useTriggerFilterStore.getState().setSearchQuery('cron job');
    expect(useTriggerFilterStore.getState().searchQuery).toBe('cron job');
  });

  it('resetFilters restores defaults', () => {
    useTriggerFilterStore.getState().setFamilyFilter('iot');
    useTriggerFilterStore.getState().setStatusFilter('active');
    useTriggerFilterStore.getState().resetFilters();
    const state = useTriggerFilterStore.getState();
    expect(state.familyFilter).toBe('all');
    expect(state.statusFilter).toBe('all');
  });
});


// ── TriggerStatusBadge ────────────────────────────────────────────────────────

describe('TriggerStatusBadge', () => {
  it('renders "Active" when not paused', () => {
    render(<TriggerStatusBadge paused={false} />);
    expect(screen.getByText('Active')).toBeTruthy();
  });

  it('renders "Paused" when paused', () => {
    render(<TriggerStatusBadge paused={true} />);
    expect(screen.getByText('Paused')).toBeTruthy();
  });

  it('renders "Circuit Open" when circuitOpen', () => {
    render(<TriggerStatusBadge paused={false} circuitOpen={true} />);
    expect(screen.getByText('Circuit Open')).toBeTruthy();
  });

  it('has correct aria-label for active', () => {
    const { container } = render(<TriggerStatusBadge paused={false} />);
    const badge = container.querySelector('[aria-label]');
    expect(badge?.getAttribute('aria-label')).toBe('Trigger status: active');
  });

  it('has correct aria-label for paused', () => {
    const { container } = render(<TriggerStatusBadge paused={true} />);
    const badge = container.querySelector('[aria-label]');
    expect(badge?.getAttribute('aria-label')).toBe('Trigger status: paused');
  });

  it('has correct aria-label for circuit open', () => {
    const { container } = render(<TriggerStatusBadge paused={false} circuitOpen={true} />);
    const badge = container.querySelector('[aria-label]');
    expect(badge?.getAttribute('aria-label')).toBe('Trigger status: circuit open');
  });

  it('circuit open takes priority over paused', () => {
    render(<TriggerStatusBadge paused={true} circuitOpen={true} />);
    expect(screen.getByText('Circuit Open')).toBeTruthy();
  });
});


// ── TimeFamilyForm ─────────────────────────────────────────────────────────────

describe('TimeFamilyForm', () => {
  it('renders cron expression field for cron type', () => {
    const onChange = vi.fn();
    render(<TimeFamilyForm triggerType="cron" value={{}} onChange={onChange} />);
    expect(screen.getByPlaceholderText('0 * * * *')).toBeTruthy();
  });

  it('renders interval field for interval type', () => {
    const onChange = vi.fn();
    render(<TimeFamilyForm triggerType="interval" value={{}} onChange={onChange} />);
    const spinbuttons = screen.getAllByRole('spinbutton');
    expect(spinbuttons.length).toBeGreaterThan(0);
  });

  it('calls onChange when cron expression changes', () => {
    const onChange = vi.fn();
    render(<TimeFamilyForm triggerType="cron" value={{}} onChange={onChange} />);
    const input = screen.getByPlaceholderText('0 * * * *');
    fireEvent.change(input, { target: { value: '0 9 * * 1-5' } });
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ cron_expression: '0 9 * * 1-5' }));
  });
});


// ── GoalChainFamilyForm ───────────────────────────────────────────────────────

describe('GoalChainFamilyForm', () => {
  it('renders watch_goal_id field', () => {
    const onChange = vi.fn();
    render(<GoalChainFamilyForm triggerType="goal_completed" value={{}} onChange={onChange} />);
    expect(screen.getByPlaceholderText('goal-uuid')).toBeTruthy();
  });

  it('renders score threshold for goal_score_below', () => {
    const onChange = vi.fn();
    render(<GoalChainFamilyForm triggerType="goal_score_below" value={{}} onChange={onChange} />);
    const inputs = screen.getAllByRole('spinbutton');
    expect(inputs.length).toBeGreaterThan(0);
  });
});


// ── WebhookFamilyForm ──────────────────────────────────────────────────────────

describe('WebhookFamilyForm', () => {
  it('renders webhook secret field', () => {
    const onChange = vi.fn();
    render(<WebhookFamilyForm triggerType="github_webhook" value={{}} onChange={onChange} />);
    expect(screen.getByPlaceholderText('whsec_…')).toBeTruthy();
  });

  it('renders github event filter for github_webhook', () => {
    const onChange = vi.fn();
    render(<WebhookFamilyForm triggerType="github_webhook" value={{}} onChange={onChange} />);
    expect(screen.getByPlaceholderText('push')).toBeTruthy();
  });
});


// ── IoTFamilyForm ─────────────────────────────────────────────────────────────

describe('IoTFamilyForm', () => {
  it('renders mqtt fields for mqtt type', () => {
    const onChange = vi.fn();
    render(<IoTFamilyForm triggerType="mqtt" value={{}} onChange={onChange} />);
    expect(screen.getByPlaceholderText('mqtt://broker.example.com:1883')).toBeTruthy();
    expect(screen.getByPlaceholderText('sensors/+/temperature')).toBeTruthy();
  });

  it('renders geofence action dropdown', () => {
    const onChange = vi.fn();
    render(<IoTFamilyForm triggerType="geofence" value={{}} onChange={onChange} />);
    expect(screen.getByDisplayValue('On Enter')).toBeTruthy();
  });

  it('renders sensor threshold fields', () => {
    const onChange = vi.fn();
    render(<IoTFamilyForm triggerType="sensor_threshold" value={{}} onChange={onChange} />);
    expect(screen.getByPlaceholderText('device-001')).toBeTruthy();
    expect(screen.getByPlaceholderText('temperature')).toBeTruthy();
  });
});
