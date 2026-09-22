import { render, screen, fireEvent } from '@testing-library/react';
import { afterEach, describe, expect, test, vi } from 'vitest';
import { MonitoringFamilyForm } from './MonitoringFamilyForm';

/** Grab the object the component most recently handed to onChange. */
function lastArg(fn: ReturnType<typeof vi.fn>): Record<string, unknown> {
  return fn.mock.calls.at(-1)![0] as Record<string, unknown>;
}

afterEach(() => vi.restoreAllMocks());

describe('MonitoringFamilyForm', () => {
  test('always renders the severity filter and the CEL condition field', () => {
    render(<MonitoringFamilyForm triggerType="datadog" value={{}} onChange={vi.fn()} />);
    expect(screen.getByText('Alert Severity Filter')).toBeInTheDocument();
    expect(screen.getByText('CEL Condition (optional)')).toBeInTheDocument();
    // Severity dropdown offers the canonical levels.
    expect(screen.getByRole('option', { name: 'Critical' })).toBeInTheDocument();
    // Only the two always-on fields exist for a plain alert type — no CloudWatch inputs.
    expect(screen.queryByText('CloudWatch Namespace')).not.toBeInTheDocument();
  });

  test('choosing a severity calls onChange with the merged spec', () => {
    const onChange = vi.fn();
    render(<MonitoringFamilyForm triggerType="datadog" value={{ description: 'x' }} onChange={onChange} />);
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'critical' } });
    expect(lastArg(onChange)).toEqual({ description: 'x', alert_severity_filter: 'critical' });
  });

  test('cloudwatch type reveals namespace + metric fields and merges edits', () => {
    const onChange = vi.fn();
    render(<MonitoringFamilyForm triggerType="cloudwatch" value={{}} onChange={onChange} />);
    expect(screen.getByText('CloudWatch Namespace')).toBeInTheDocument();
    expect(screen.getByText('CloudWatch Metric')).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText('AWS/Lambda'), { target: { value: 'AWS/EC2' } });
    expect(lastArg(onChange)).toEqual({ cloudwatch_namespace: 'AWS/EC2' });
    fireEvent.change(screen.getByPlaceholderText('Errors'), { target: { value: 'Duration' } });
    expect(lastArg(onChange)).toEqual({ cloudwatch_metric: 'Duration' });
  });

  test('log_pattern type renders the regex + stream fields', () => {
    const onChange = vi.fn();
    render(<MonitoringFamilyForm triggerType="log_pattern" value={{}} onChange={onChange} />);
    expect(screen.getByText('Log Pattern (regex)')).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText(/ERROR\.\*database/), {
      target: { value: 'FATAL.*oom' },
    });
    expect(lastArg(onChange)).toEqual({ log_pattern_regex: 'FATAL.*oom' });
  });

  test('sentry_issue type renders project + environment filters', () => {
    render(<MonitoringFamilyForm triggerType="sentry_issue" value={{}} onChange={vi.fn()} />);
    expect(screen.getByText('Sentry Project (optional)')).toBeInTheDocument();
    expect(screen.getByText('Sentry Environment (optional)')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('my-app-backend')).toBeInTheDocument();
  });

  test('reflects the passed-in values on controlled inputs', () => {
    render(
      <MonitoringFamilyForm
        triggerType="sentry_issue"
        value={{ sentry_project: 'checkout-svc', condition_expression: 'payload.level == "fatal"' }}
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByDisplayValue('checkout-svc')).toBeInTheDocument();
    expect(screen.getByDisplayValue('payload.level == "fatal"')).toBeInTheDocument();
  });

  test('log_pattern type merges edits to the optional log stream field', () => {
    const onChange = vi.fn();
    render(<MonitoringFamilyForm triggerType="log_pattern" value={{}} onChange={onChange} />);
    fireEvent.change(screen.getByPlaceholderText('app.error'), { target: { value: 'app.warn' } });
    expect(lastArg(onChange)).toEqual({ log_stream: 'app.warn' });
  });

  test('sentry_issue type merges edits to project and environment fields', () => {
    const onChange = vi.fn();
    render(<MonitoringFamilyForm triggerType="sentry_issue" value={{}} onChange={onChange} />);
    fireEvent.change(screen.getByPlaceholderText('my-app-backend'), { target: { value: 'checkout-svc' } });
    expect(lastArg(onChange)).toEqual({ sentry_project: 'checkout-svc' });
    fireEvent.change(screen.getByPlaceholderText('production'), { target: { value: 'staging' } });
    expect(lastArg(onChange)).toEqual({ sentry_environment: 'staging' });
  });

  test('pagerduty type renders the check URL field and merges edits', () => {
    const onChange = vi.fn();
    render(<MonitoringFamilyForm triggerType="pagerduty" value={{}} onChange={onChange} />);
    expect(screen.getByText('Check URL')).toBeInTheDocument();
    const urlInput = screen.getByPlaceholderText('https://api.example.com/health');
    expect(urlInput).toHaveAttribute('type', 'url');
    fireEvent.change(urlInput, { target: { value: 'https://api.example.com/status' } });
    expect(lastArg(onChange)).toEqual({ poll_url: 'https://api.example.com/status' });
  });

  test('the CEL condition field merges edits regardless of trigger type', () => {
    const onChange = vi.fn();
    render(<MonitoringFamilyForm triggerType="datadog" value={{}} onChange={onChange} />);
    fireEvent.change(screen.getByPlaceholderText(/payload.severity/), {
      target: { value: 'payload.severity == "warning"' },
    });
    expect(lastArg(onChange)).toEqual({ condition_expression: 'payload.severity == "warning"' });
  });
});
