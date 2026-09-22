import { render, screen, fireEvent } from '@testing-library/react';
import { afterEach, describe, expect, test, vi } from 'vitest';
import { DatabaseForm } from './DatabaseForm';

function lastArg(fn: ReturnType<typeof vi.fn>): Record<string, unknown> {
  return fn.mock.calls.at(-1)![0] as Record<string, unknown>;
}

afterEach(() => vi.restoreAllMocks());

describe('DatabaseForm', () => {
  describe('generic (postgresql/mysql) branch', () => {
    test('renders host, port, database, username, password, tables fields', () => {
      render(<DatabaseForm sourceType="postgresql" value={{}} onChange={vi.fn()} />);
      expect(screen.getByText('Host')).toBeInTheDocument();
      expect(screen.getByText('Port')).toBeInTheDocument();
      expect(screen.getByText('Database')).toBeInTheDocument();
      expect(screen.getByText('Username')).toBeInTheDocument();
      expect(screen.getByText('Password')).toBeInTheDocument();
      expect(screen.getByText('Tables (comma-separated)')).toBeInTheDocument();
    });

    test('defaults port to 5432 for postgresql and 3306 for mysql', () => {
      const { rerender } = render(<DatabaseForm sourceType="postgresql" value={{}} onChange={vi.fn()} />);
      expect(screen.getByPlaceholderText('db.example.com').parentElement).toBeTruthy();
      expect(screen.getByDisplayValue('5432')).toBeInTheDocument();
      rerender(<DatabaseForm sourceType="mysql" value={{}} onChange={vi.fn()} />);
      expect(screen.getByDisplayValue('3306')).toBeInTheDocument();
    });

    test('shows CDC Mode select only for postgresql', () => {
      const { rerender } = render(<DatabaseForm sourceType="postgresql" value={{}} onChange={vi.fn()} />);
      expect(screen.getByText('CDC Mode')).toBeInTheDocument();
      rerender(<DatabaseForm sourceType="mysql" value={{}} onChange={vi.fn()} />);
      expect(screen.queryByText('CDC Mode')).not.toBeInTheDocument();
    });

    test('typing in host merges into value via onChange', () => {
      const onChange = vi.fn();
      render(<DatabaseForm sourceType="mysql" value={{ database: 'd1' }} onChange={onChange} />);
      fireEvent.change(screen.getByPlaceholderText('db.example.com'), { target: { value: 'db.acme.com' } });
      expect(lastArg(onChange)).toEqual({ database: 'd1', host: 'db.acme.com' });
    });

    test('changing CDC mode updates value', () => {
      const onChange = vi.fn();
      render(<DatabaseForm sourceType="postgresql" value={{}} onChange={onChange} />);
      fireEvent.change(screen.getByDisplayValue('Query (incremental)'), { target: { value: 'logical_replication' } });
      expect(lastArg(onChange)).toEqual({ cdc_mode: 'logical_replication' });
    });

    test('renders existing values', () => {
      render(<DatabaseForm sourceType="postgresql" value={{ host: 'h1', username: 'u1' }} onChange={vi.fn()} />);
      expect(screen.getByDisplayValue('h1')).toBeInTheDocument();
      expect(screen.getByDisplayValue('u1')).toBeInTheDocument();
    });

    test('typing in port, database, username, password, and tables fields all merge into value', () => {
      const onChange = vi.fn();
      render(<DatabaseForm sourceType="postgresql" value={{}} onChange={onChange} />);

      fireEvent.change(screen.getByDisplayValue('5432'), { target: { value: '5433' } });
      expect(lastArg(onChange)).toEqual({ port: 5433 });

      // Textbox order: host(0), database(1), username(2); tables_csv has its own placeholder.
      const textboxes = screen.getAllByRole('textbox');
      fireEvent.change(textboxes[1], { target: { value: 'mydb' } });
      expect(lastArg(onChange)).toEqual({ database: 'mydb' });

      fireEvent.change(textboxes[2], { target: { value: 'admin' } });
      expect(lastArg(onChange)).toEqual({ username: 'admin' });

      const passwordInput = document.querySelector('input[type="password"]')!;
      fireEvent.change(passwordInput, { target: { value: 'secret' } });
      expect(lastArg(onChange)).toEqual({ password: 'secret' });

      fireEvent.change(screen.getByPlaceholderText('orders, customers'), { target: { value: 'orders' } });
      expect(lastArg(onChange)).toEqual({ tables_csv: 'orders' });
    });
  });

  describe('snowflake branch', () => {
    test('renders account, warehouse, database, schema, username, password, tables fields', () => {
      render(<DatabaseForm sourceType="snowflake" value={{}} onChange={vi.fn()} />);
      expect(screen.getByText('Account')).toBeInTheDocument();
      expect(screen.getByText('Warehouse')).toBeInTheDocument();
      expect(screen.getByText('Schema')).toBeInTheDocument();
      expect(screen.getByDisplayValue('PUBLIC')).toBeInTheDocument();
      // host/port fields from the generic branch should not appear
      expect(screen.queryByText('Host')).not.toBeInTheDocument();
      expect(screen.queryByText('CDC Mode')).not.toBeInTheDocument();
    });

    test('typing account calls onChange with merged value', () => {
      const onChange = vi.fn();
      render(<DatabaseForm sourceType="snowflake" value={{}} onChange={onChange} />);
      fireEvent.change(screen.getByPlaceholderText('org-account'), { target: { value: 'my-org' } });
      expect(lastArg(onChange)).toEqual({ account: 'my-org' });
    });

    test('typing in warehouse, database, schema, username, password, and tables fields all merge into value', () => {
      const onChange = vi.fn();
      render(<DatabaseForm sourceType="snowflake" value={{}} onChange={onChange} />);

      // Textbox order: account(0), warehouse(1), database(2), schema(3), username(4).
      const textboxes = screen.getAllByRole('textbox');
      fireEvent.change(textboxes[1], { target: { value: 'wh1' } });
      expect(lastArg(onChange)).toEqual({ warehouse: 'wh1' });

      fireEvent.change(textboxes[2], { target: { value: 'db1' } });
      expect(lastArg(onChange)).toEqual({ database: 'db1' });

      fireEvent.change(textboxes[3], { target: { value: 'CUSTOM_SCHEMA' } });
      expect(lastArg(onChange)).toEqual({ schema: 'CUSTOM_SCHEMA' });

      fireEvent.change(textboxes[4], { target: { value: 'admin' } });
      expect(lastArg(onChange)).toEqual({ username: 'admin' });

      const passwordInput = document.querySelector('input[type="password"]')!;
      fireEvent.change(passwordInput, { target: { value: 'secret' } });
      expect(lastArg(onChange)).toEqual({ password: 'secret' });

      fireEvent.change(textboxes[5], { target: { value: 'orders' } });
      expect(lastArg(onChange)).toEqual({ tables_csv: 'orders' });
    });
  });

  describe('mongodb branch', () => {
    test('renders connection URI, database, collections fields only', () => {
      render(<DatabaseForm sourceType="mongodb" value={{}} onChange={vi.fn()} />);
      expect(screen.getByText('Connection URI')).toBeInTheDocument();
      expect(screen.getByText('Collections (comma-separated)')).toBeInTheDocument();
      expect(screen.queryByText('Host')).not.toBeInTheDocument();
      expect(screen.queryByText('Account')).not.toBeInTheDocument();
    });

    test('typing the URI calls onChange with merged value', () => {
      const onChange = vi.fn();
      render(<DatabaseForm sourceType="mongodb" value={{ database: 'd1' }} onChange={onChange} />);
      fireEvent.change(screen.getByPlaceholderText('mongodb+srv://...'), { target: { value: 'mongodb+srv://x' } });
      expect(lastArg(onChange)).toEqual({ database: 'd1', uri: 'mongodb+srv://x' });
    });

    test('typing in database and collections fields merges into value', () => {
      const onChange = vi.fn();
      render(<DatabaseForm sourceType="mongodb" value={{}} onChange={onChange} />);
      const textboxes = screen.getAllByRole('textbox');
      fireEvent.change(textboxes[1], { target: { value: 'mydb' } });
      expect(lastArg(onChange)).toEqual({ database: 'mydb' });
      fireEvent.change(textboxes[2], { target: { value: 'users, orders' } });
      expect(lastArg(onChange)).toEqual({ collections_csv: 'users, orders' });
    });
  });
});
