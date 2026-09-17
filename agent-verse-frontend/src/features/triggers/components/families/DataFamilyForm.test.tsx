import { render, screen, fireEvent } from '@testing-library/react';
import { afterEach, describe, expect, test, vi } from 'vitest';
import { DataFamilyForm } from './DataFamilyForm';

function lastArg(fn: ReturnType<typeof vi.fn>): Record<string, unknown> {
  return fn.mock.calls.at(-1)![0] as Record<string, unknown>;
}

afterEach(() => vi.restoreAllMocks());

describe('DataFamilyForm', () => {
  test('db_row_change renders table, operation and filter fields', () => {
    render(<DataFamilyForm triggerType="db_row_change" value={{}} onChange={vi.fn()} />);
    expect(screen.getByText('Database Table')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('orders')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('{"status": "pending"}')).toBeInTheDocument();
  });

  test('db_row_change operation select fires onChange with the chosen operation', () => {
    const onChange = vi.fn();
    render(<DataFamilyForm triggerType="db_row_change" value={{ db_table: 'orders' }} onChange={onChange} />);
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'UPDATE' } });
    expect(lastArg(onChange)).toEqual({ db_table: 'orders', db_operation: 'UPDATE' });
  });

  test('db_row_change table field reports edits', () => {
    const onChange = vi.fn();
    render(<DataFamilyForm triggerType="db_row_change" value={{}} onChange={onChange} />);
    fireEvent.change(screen.getByPlaceholderText('orders'), { target: { value: 'shipments' } });
    expect(lastArg(onChange)).toEqual({ db_table: 'shipments' });
  });

  test('db_row_change filter field reports edits', () => {
    const onChange = vi.fn();
    render(<DataFamilyForm triggerType="db_row_change" value={{}} onChange={onChange} />);
    fireEvent.change(screen.getByPlaceholderText('{"status": "pending"}'), {
      target: { value: '{"status": "done"}' },
    });
    expect(lastArg(onChange)).toEqual({ db_filter: '{"status": "done"}' });
  });

  test('s3_event renders bucket + prefix fields and reports edits', () => {
    const onChange = vi.fn();
    render(<DataFamilyForm triggerType="s3_event" value={{}} onChange={onChange} />);
    expect(screen.getByText('S3 Bucket')).toBeInTheDocument();
    expect(screen.getByText('Key Prefix (optional)')).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText('my-data-bucket'), { target: { value: 'exports' } });
    expect(lastArg(onChange)).toEqual({ s3_bucket: 'exports' });
    fireEvent.change(screen.getByPlaceholderText('reports/'), { target: { value: 'raw/' } });
    expect(lastArg(onChange)).toEqual({ s3_prefix: 'raw/' });
  });

  test('api_poll renders poll url + interval and reports url edits', () => {
    const onChange = vi.fn();
    render(<DataFamilyForm triggerType="api_poll" value={{}} onChange={onChange} />);
    const urlInput = screen.getByPlaceholderText('https://api.example.com/status');
    fireEvent.change(urlInput, { target: { value: 'https://svc/health' } });
    expect(lastArg(onChange)).toEqual({ poll_url: 'https://svc/health' });
    // Interval field defaults to 300s.
    expect(screen.getByDisplayValue('300')).toBeInTheDocument();
  });

  test('api_poll method select, jsonpath, expected value, and interval all report edits', () => {
    const onChange = vi.fn();
    render(<DataFamilyForm triggerType="api_poll" value={{}} onChange={onChange} />);
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'POST' } });
    expect(lastArg(onChange)).toEqual({ poll_method: 'POST' });
    fireEvent.change(screen.getByPlaceholderText('$.status'), { target: { value: '$.results.count' } });
    expect(lastArg(onChange)).toEqual({ poll_jsonpath: '$.results.count' });
    fireEvent.change(screen.getByPlaceholderText('complete'), { target: { value: 'done' } });
    expect(lastArg(onChange)).toEqual({ poll_expected_value: 'done' });
    fireEvent.change(screen.getByDisplayValue('300'), { target: { value: '120' } });
    expect(lastArg(onChange)).toEqual({ poll_interval_seconds: 120 });
  });

  test('file_drop shows the watched-path field and preserves existing value', () => {
    render(
      <DataFamilyForm
        triggerType="file_drop"
        value={{ file_drop_path: '/inbox/*.csv' }}
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByText('File Drop Path')).toBeInTheDocument();
    expect(screen.getByDisplayValue('/inbox/*.csv')).toBeInTheDocument();
  });

  test('file_drop path field reports edits', () => {
    const onChange = vi.fn();
    render(<DataFamilyForm triggerType="file_drop" value={{}} onChange={onChange} />);
    fireEvent.change(screen.getByPlaceholderText('/inbox/*.csv'), { target: { value: '/incoming/*.json' } });
    expect(lastArg(onChange)).toEqual({ file_drop_path: '/incoming/*.json' });
  });

  test('rss_feed renders only the feed URL field', () => {
    const onChange = vi.fn();
    render(<DataFamilyForm triggerType="rss_feed" value={{}} onChange={onChange} />);
    expect(screen.getByText('RSS/Atom Feed URL')).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText('https://feeds.example.com/rss.xml'), {
      target: { value: 'https://blog/feed.xml' },
    });
    expect(lastArg(onChange)).toEqual({ rss_url: 'https://blog/feed.xml' });
  });

  test('renders no fields for a type outside this family', () => {
    render(<DataFamilyForm triggerType="cron" value={{}} onChange={vi.fn()} />);
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
  });
});
