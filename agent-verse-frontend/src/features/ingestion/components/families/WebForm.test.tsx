import { render, screen, fireEvent } from '@testing-library/react';
import { afterEach, describe, expect, test, vi } from 'vitest';
import { WebForm } from './WebForm';

function lastArg(fn: ReturnType<typeof vi.fn>): Record<string, unknown> {
  return fn.mock.calls.at(-1)![0] as Record<string, unknown>;
}

afterEach(() => vi.restoreAllMocks());

describe('WebForm', () => {
  test('always renders the start URL(s) field', () => {
    render(<WebForm sourceType="unknown" value={{}} onChange={vi.fn()} />);
    expect(screen.getByText('Start URL(s)')).toBeInTheDocument();
  });

  test('typing URLs calls onChange with merged value', () => {
    const onChange = vi.fn();
    render(<WebForm sourceType="unknown" value={{ foo: 'bar' }} onChange={onChange} />);
    fireEvent.change(screen.getByPlaceholderText(/docs.example.com/), { target: { value: 'https://x.com' } });
    expect(lastArg(onChange)).toEqual({ foo: 'bar', urls: 'https://x.com' });
  });

  test('renders max depth and URL pattern fields for web_crawler', () => {
    render(<WebForm sourceType="web_crawler" value={{}} onChange={vi.fn()} />);
    expect(screen.getByText('Max Depth')).toBeInTheDocument();
    expect(screen.getByText('URL Pattern (optional)')).toBeInTheDocument();
    expect(screen.getByDisplayValue('3')).toBeInTheDocument();
  });

  test('changing max depth calls onChange with a number', () => {
    const onChange = vi.fn();
    render(<WebForm sourceType="web_crawler" value={{}} onChange={onChange} />);
    fireEvent.change(screen.getByDisplayValue('3'), { target: { value: '5' } });
    expect(lastArg(onChange)).toEqual({ max_depth: 5 });
  });

  test('renders feed URL field for rss_feed', () => {
    const onChange = vi.fn();
    render(<WebForm sourceType="rss_feed" value={{}} onChange={onChange} />);
    expect(screen.getByText('Feed URL')).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText('https://example.com/feed.xml'), { target: { value: 'https://x/feed.xml' } });
    expect(lastArg(onChange)).toEqual({ feed_url: 'https://x/feed.xml' });
  });

  test('renders sitemap URL field for sitemap', () => {
    const onChange = vi.fn();
    render(<WebForm sourceType="sitemap" value={{}} onChange={onChange} />);
    expect(screen.getByText('Sitemap URL')).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText('https://example.com/sitemap.xml'), { target: { value: 'https://x/sitemap.xml' } });
    expect(lastArg(onChange)).toEqual({ sitemap_url: 'https://x/sitemap.xml' });
  });

  test('does not render web_crawler/rss/sitemap-specific fields for a plain type', () => {
    render(<WebForm sourceType="plain" value={{}} onChange={vi.fn()} />);
    expect(screen.queryByText('Max Depth')).not.toBeInTheDocument();
    expect(screen.queryByText('Feed URL')).not.toBeInTheDocument();
    expect(screen.queryByText('Sitemap URL')).not.toBeInTheDocument();
  });
});
