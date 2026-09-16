import { render, screen, fireEvent } from '@testing-library/react';
import { describe, expect, test, vi } from 'vitest';
import { Pagination } from './Pagination';

describe('Pagination', () => {
  test('renders the "from–to of total" summary', () => {
    render(<Pagination page={1} pageSize={10} total={42} onPageChange={vi.fn()} />);
    // en-dash between from and to, per the component.
    expect(screen.getByText('1–10 of 42')).toBeInTheDocument();
    expect(screen.getByRole('navigation', { name: 'Pagination' })).toBeInTheDocument();
  });

  test('shows "No results" when total is 0', () => {
    render(<Pagination page={1} pageSize={10} total={0} onPageChange={vi.fn()} />);
    expect(screen.getByText('No results')).toBeInTheDocument();
    expect(screen.queryByText(/of 0/)).not.toBeInTheDocument();
  });

  test('First/Previous are disabled on page 1 while Next/Last stay enabled', () => {
    render(<Pagination page={1} pageSize={10} total={42} onPageChange={vi.fn()} />);
    expect(screen.getByRole('button', { name: 'First page' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Previous page' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Next page' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Last page' })).toBeEnabled();
  });

  test('navigation buttons call onPageChange with the right target page', () => {
    const onPageChange = vi.fn();
    // total=42, pageSize=10 -> totalPages = ceil(42/10) = 5
    render(<Pagination page={1} pageSize={10} total={42} onPageChange={onPageChange} />);

    fireEvent.click(screen.getByRole('button', { name: 'Next page' }));
    expect(onPageChange).toHaveBeenLastCalledWith(2);

    fireEvent.click(screen.getByRole('button', { name: 'Page 3' }));
    expect(onPageChange).toHaveBeenLastCalledWith(3);

    fireEvent.click(screen.getByRole('button', { name: 'Last page' }));
    expect(onPageChange).toHaveBeenLastCalledWith(5); // totalPages
  });

  test('renders an ellipsis plus first/last page buttons when there are many pages', () => {
    // total=200, pageSize=10 -> 20 pages, current page 10 -> windowed with ellipses.
    render(<Pagination page={10} pageSize={10} total={200} onPageChange={vi.fn()} />);
    expect(screen.getAllByText('…').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByRole('button', { name: 'Page 1' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Page 20' })).toBeInTheDocument();
    // The active page is marked with aria-current.
    expect(screen.getByRole('button', { name: 'Page 10' })).toHaveAttribute('aria-current', 'page');
  });

  test('omits the rows-per-page select unless onPageSizeChange is supplied', () => {
    const { rerender } = render(
      <Pagination page={1} pageSize={10} total={42} onPageChange={vi.fn()} />,
    );
    expect(screen.queryByRole('combobox', { name: 'Rows per page' })).not.toBeInTheDocument();

    const onPageSizeChange = vi.fn();
    rerender(
      <Pagination
        page={1}
        pageSize={10}
        total={42}
        onPageChange={vi.fn()}
        onPageSizeChange={onPageSizeChange}
      />,
    );
    const select = screen.getByRole('combobox', { name: 'Rows per page' });
    fireEvent.change(select, { target: { value: '25' } });
    expect(onPageSizeChange).toHaveBeenCalledWith(25); // numeric, not "25"
  });

  test('marks the active page button with aria-current="page"', () => {
    render(<Pagination page={3} pageSize={10} total={42} onPageChange={vi.fn()} />);
    expect(screen.getByRole('button', { name: 'Page 3' })).toHaveAttribute('aria-current', 'page');
    expect(screen.getByRole('button', { name: 'Page 2' })).not.toHaveAttribute('aria-current');
  });
});
