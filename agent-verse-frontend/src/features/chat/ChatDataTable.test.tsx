/** ChatDataTable — sortable/filterable data table component. */
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, test } from 'vitest';
import { ChatDataTable } from './ChatDataTable';

const ROWS = [
  { name: 'Charlie', age: 30, city: 'Berlin' },
  { name: 'Alice', age: 25, city: 'Austin' },
  { name: 'Bob', age: 40, city: 'Boston' },
];

describe('ChatDataTable', () => {
  test('renders "No data to display" for empty array', () => {
    render(<ChatDataTable data={[]} />);
    expect(screen.getByText('No data to display')).toBeInTheDocument();
  });

  test('renders "No data to display" when data is undefined-like (falsy)', () => {
    // @ts-expect-error deliberately testing the falsy guard
    render(<ChatDataTable data={null} />);
    expect(screen.getByText('No data to display')).toBeInTheDocument();
  });

  test('renders column headers from the first row keys', () => {
    render(<ChatDataTable data={ROWS} />);
    expect(screen.getByText('name')).toBeInTheDocument();
    expect(screen.getByText('age')).toBeInTheDocument();
    expect(screen.getByText('city')).toBeInTheDocument();
  });

  test('renders all rows and the row count footer', () => {
    render(<ChatDataTable data={ROWS} />);
    expect(screen.getByText('Charlie')).toBeInTheDocument();
    expect(screen.getByText('Alice')).toBeInTheDocument();
    expect(screen.getByText('Bob')).toBeInTheDocument();
    expect(screen.getByText('3 of 3 rows')).toBeInTheDocument();
  });

  test('does not render title bar or filter input when no title is given', () => {
    render(<ChatDataTable data={ROWS} />);
    expect(screen.queryByPlaceholderText('Filter…')).not.toBeInTheDocument();
  });

  test('renders title and filter input when title is given', () => {
    render(<ChatDataTable data={ROWS} title="People" />);
    expect(screen.getByText('People')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('Filter…')).toBeInTheDocument();
  });

  test('filters rows by any column matching the filter text (case-insensitive)', async () => {
    render(<ChatDataTable data={ROWS} title="People" />);
    const filterInput = screen.getByPlaceholderText('Filter…');
    await userEvent.type(filterInput, 'berlin');
    expect(screen.getByText('Charlie')).toBeInTheDocument();
    expect(screen.queryByText('Alice')).not.toBeInTheDocument();
    expect(screen.queryByText('Bob')).not.toBeInTheDocument();
    expect(screen.getByText('1 of 3 rows')).toBeInTheDocument();
  });

  test('filter matching no rows shows an empty table with 0 of N rows', async () => {
    render(<ChatDataTable data={ROWS} title="People" />);
    await userEvent.type(screen.getByPlaceholderText('Filter…'), 'zzz-no-match');
    expect(screen.getByText('0 of 3 rows')).toBeInTheDocument();
  });

  test('clicking a column header sorts ascending, then descending on second click', async () => {
    render(<ChatDataTable data={ROWS} />);
    const nameHeader = screen.getByText('name').closest('th')!;

    await userEvent.click(nameHeader);
    let rows = screen.getAllByRole('row').slice(1); // skip header row
    expect(within(rows[0]).getByText('Alice')).toBeInTheDocument();
    expect(within(rows[1]).getByText('Bob')).toBeInTheDocument();
    expect(within(rows[2]).getByText('Charlie')).toBeInTheDocument();

    await userEvent.click(nameHeader);
    rows = screen.getAllByRole('row').slice(1);
    expect(within(rows[0]).getByText('Charlie')).toBeInTheDocument();
    expect(within(rows[1]).getByText('Bob')).toBeInTheDocument();
    expect(within(rows[2]).getByText('Alice')).toBeInTheDocument();
  });

  test('clicking a different column header switches sort key and resets to ascending', async () => {
    render(<ChatDataTable data={ROWS} />);
    const nameHeader = screen.getByText('name').closest('th')!;
    const ageHeader = screen.getByText('age').closest('th')!;

    await userEvent.click(nameHeader); // sort by name asc
    await userEvent.click(ageHeader); // switch to age asc

    const rows = screen.getAllByRole('row').slice(1);
    expect(within(rows[0]).getByText('25')).toBeInTheDocument();
    expect(within(rows[1]).getByText('30')).toBeInTheDocument();
    expect(within(rows[2]).getByText('40')).toBeInTheDocument();
  });

  test('renders empty-string for missing/nullish cell values', () => {
    const sparse = [{ a: 'x', b: undefined }, { a: null, b: 'y' }] as unknown as Record<string, unknown>[];
    render(<ChatDataTable data={sparse} />);
    // Column "a" header exists; cells with nullish values render as empty strings without throwing.
    expect(screen.getByText('a')).toBeInTheDocument();
    expect(screen.getByText('x')).toBeInTheDocument();
    expect(screen.getByText('y')).toBeInTheDocument();
  });
});
