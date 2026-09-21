import { render, screen } from '@testing-library/react';
import { describe, expect, test, vi } from 'vitest';
import { FamilyFormRouter } from './FamilyFormRouter';
import type { TriggerFamily } from '../../types';

/** Each case below asserts a label that only the routed-to form renders for
 * that triggerType, so a wrong branch (or the wrong form entirely) fails the
 * assertion rather than just failing to find *anything*. */
describe('FamilyFormRouter', () => {
  test('time family routes to TimeFamilyForm', () => {
    render(
      <FamilyFormRouter family="time" triggerType="cron" value={{}} onChange={vi.fn()} />
    );
    expect(screen.getByText('Cron Expression')).toBeInTheDocument();
  });

  test('goal_chain family routes to GoalChainFamilyForm', () => {
    render(
      <FamilyFormRouter family="goal_chain" triggerType="goal_completed" value={{}} onChange={vi.fn()} />
    );
    expect(screen.getByText('Watch Goal ID (optional)')).toBeInTheDocument();
  });

  test('webhook family routes to WebhookFamilyForm', () => {
    render(
      <FamilyFormRouter family="webhook" triggerType="webhook" value={{}} onChange={vi.fn()} />
    );
    expect(screen.getByText('Endpoint Name')).toBeInTheDocument();
    expect(screen.getByText('Webhook Secret')).toBeInTheDocument();
  });

  test('conversational family routes to ConversationalFamilyForm', () => {
    render(
      <FamilyFormRouter family="conversational" triggerType="chat_command" value={{}} onChange={vi.fn()} />
    );
    expect(screen.getByText('Command Pattern')).toBeInTheDocument();
  });

  test('state_condition family routes to ConditionFamilyForm', () => {
    render(
      <FamilyFormRouter family="state_condition" triggerType="condition" value={{}} onChange={vi.fn()} />
    );
    expect(screen.getByText('CEL Condition Expression')).toBeInTheDocument();
  });

  test('data family routes to DataFamilyForm', () => {
    render(
      <FamilyFormRouter family="data" triggerType="db_row_change" value={{}} onChange={vi.fn()} />
    );
    expect(screen.getByText('Database Table')).toBeInTheDocument();
  });

  test('monitoring family routes to MonitoringFamilyForm', () => {
    render(
      <FamilyFormRouter family="monitoring" triggerType="cloudwatch" value={{}} onChange={vi.fn()} />
    );
    expect(screen.getByText('Alert Severity Filter')).toBeInTheDocument();
    expect(screen.getByText('CloudWatch Namespace')).toBeInTheDocument();
  });

  test('iot family routes to IoTFamilyForm', () => {
    render(
      <FamilyFormRouter family="iot" triggerType="mqtt" value={{}} onChange={vi.fn()} />
    );
    expect(screen.getByText('MQTT Broker URL')).toBeInTheDocument();
  });

  test('ml_signal family routes to PollingFamilyForm', () => {
    render(
      <FamilyFormRouter family="ml_signal" triggerType="price_threshold" value={{}} onChange={vi.fn()} />
    );
    expect(screen.getByText('Threshold Price')).toBeInTheDocument();
  });

  test('unknown family falls back to GenericFamilyForm', () => {
    render(
      <FamilyFormRouter
        family={'unknown_family' as TriggerFamily}
        triggerType="cron"
        value={{}}
        onChange={vi.fn()}
      />
    );
    expect(screen.getByText('Extra configuration (JSON)')).toBeInTheDocument();
    // Confirm it's the generic form, not one of the specialised ones.
    expect(screen.queryByText('Cron Expression')).not.toBeInTheDocument();
  });

  test('passes value and onChange through to the routed form unchanged', () => {
    const onChange = vi.fn();
    render(
      <FamilyFormRouter
        family="time"
        triggerType="interval"
        value={{ interval_seconds: 42 }}
        onChange={onChange}
      />
    );
    expect(screen.getByDisplayValue('42')).toBeInTheDocument();
  });
});
