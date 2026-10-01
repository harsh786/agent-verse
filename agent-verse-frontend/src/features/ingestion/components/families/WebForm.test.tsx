import { render, screen, fireEvent } from '@testing-library/react';
import { afterEach, describe, expect, test, vi } from 'vitest';
import { WebForm } from './WebForm';

function lastArg(fn: ReturnType<typeof vi.fn>): Record<string, unknown> {
  return fn.mock.calls.at(-1)![0] as Record<string, unknown>;
}

afterEach(() => vi.restoreAllMocks());

describe('WebForm', () => {
  test('an unmapped web type renders the start URL(s) field', () => {
    render(<WebForm sourceType="reddit" value={{}} onChange={vi.fn()} />);
    expect(screen.getByText('Start URL(s)')).toBeInTheDocument();
  });

  test('typing URLs for an unmapped type calls onChange with merged value', () => {
    const onChange = vi.fn();
    render(<WebForm sourceType="reddit" value={{ foo: 'bar' }} onChange={onChange} />);
    fireEvent.change(screen.getByPlaceholderText(/docs.example.com/), { target: { value: 'https://x.com' } });
    expect(lastArg(onChange)).toEqual({ foo: 'bar', urls: 'https://x.com' });
  });

  describe('web_crawl (WEB-FORM-TYPES: the real source type, not "web_crawler")', () => {
    test('renders crawler and sitemap fields', () => {
      render(<WebForm sourceType="web_crawl" value={{}} onChange={vi.fn()} />);
      expect(screen.getByLabelText('Seed URLs')).toBeInTheDocument();
      expect(screen.getByLabelText('Sitemap URL (optional)')).toBeInTheDocument();
      expect(screen.getByLabelText('Max Depth')).toHaveValue(3);
      expect(screen.getByLabelText('Max pages per sync')).toHaveValue(100);
      expect(screen.queryByText('Start URL(s)')).not.toBeInTheDocument();
    });

    test('seed URLs are sent as the seed_urls list the connector reads', () => {
      const onChange = vi.fn();
      render(<WebForm sourceType="web_crawl" value={{}} onChange={onChange} />);
      fireEvent.change(screen.getByLabelText('Seed URLs'), {
        target: { value: 'https://a.example.com\nhttps://b.example.com, https://c.example.com\n' },
      });
      expect(lastArg(onChange)).toEqual({
        seed_urls: ['https://a.example.com', 'https://b.example.com', 'https://c.example.com'],
      });
      // The raw text (with the trailing newline) stays editable.
      expect(screen.getByLabelText('Seed URLs')).toHaveValue('https://a.example.com\nhttps://b.example.com, https://c.example.com\n');
    });

    test('an existing seed_urls list is shown one per line', () => {
      render(<WebForm sourceType="web_crawl" value={{ seed_urls: ['https://a', 'https://b'] }} onChange={vi.fn()} />);
      expect(screen.getByLabelText('Seed URLs')).toHaveValue('https://a\nhttps://b');
    });

    test.each([
      ['Sitemap URL (optional)', 'https://x/sitemap.xml', { sitemap_url: 'https://x/sitemap.xml' }],
      ['Max Depth', '5', { max_depth: 5 }],
      ['Max pages per sync', '20', { max_pages: 20 }],
      ['Include URL pattern (regex, optional)', '^https://docs', { include_url_pattern: '^https://docs' }],
      ['Exclude URL pattern (regex, optional)', '/admin/', { exclude_url_pattern: '/admin/' }],
      ['Delay between requests (seconds)', '0.5', { crawl_delay_seconds: 0.5 }],
    ])('%s maps to the connector key', (label, input, expected) => {
      const onChange = vi.fn();
      render(<WebForm sourceType="web_crawl" value={{}} onChange={onChange} />);
      fireEvent.change(screen.getByLabelText(label), { target: { value: input } });
      expect(lastArg(onChange)).toEqual(expected);
    });
  });

  test.each(['rss', 'atom'])('%s sends the feed as `url`, the key the connector reads', (type) => {
    const onChange = vi.fn();
    render(<WebForm sourceType={type} value={{}} onChange={onChange} />);
    expect(screen.getByText('Feed URL')).toBeInTheDocument();
    expect(screen.queryByText('Start URL(s)')).not.toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText('https://example.com/feed.xml'), { target: { value: 'https://x/feed.xml' } });
    expect(lastArg(onChange)).toEqual({ url: 'https://x/feed.xml' });
  });

  test('rss max entries is sent as a number', () => {
    const onChange = vi.fn();
    render(<WebForm sourceType="rss" value={{}} onChange={onChange} />);
    fireEvent.change(screen.getByDisplayValue('200'), { target: { value: '50' } });
    expect(lastArg(onChange)).toEqual({ max_entries: 50 });
  });

  test('no form branch keys off a source type the catalogue does not offer', () => {
    render(<WebForm sourceType="web_crawler" value={{}} onChange={vi.fn()} />);
    expect(screen.queryByLabelText('Seed URLs')).not.toBeInTheDocument();
  });
});
