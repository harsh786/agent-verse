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

  test('api_poll renders poll url + interval and reports url edits', () => {
    const onChange = vi.fn();
    render(<DataFamilyForm triggerType="api_poll" value={{}} onChange={onChange} />);
    const urlInput = screen.getByPlaceholderText('https://api.example.com/status');
    fireEvent.change(urlInput, { target: { value: 'https://svc/health' } });
    expect(lastArg(onChange)).toEqual({ poll_url: 'https://svc/health' });
    // Interval field defaults to 300s.
    expect(screen.getByDisplayValue('300')).toBeInTheDocument();
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
