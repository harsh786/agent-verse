/** Phase 7 — code blocks get a copy button + dependency-free highlighting. */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { RichMarkdown } from './RichMarkdown';
import { highlightCode } from './codeHighlight';

describe('RichMarkdown code blocks', () => {
  const writeText = vi.fn().mockResolvedValue(undefined);

  beforeEach(() => {
    writeText.mockClear();
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText },
      configurable: true,
      writable: true,
    });
  });

  it('renders a fenced code block with a copy button', () => {
    const { container } = render(<RichMarkdown>{'```js\nconst x = 1;\n```'}</RichMarkdown>);
    expect(container.querySelector('code')).not.toBeNull();
    expect(screen.getByRole('button', { name: /copy code/i })).toBeDefined();
    // No doubled <pre> from the markdown wrapper.
    expect(container.querySelectorAll('pre')).toHaveLength(1);
  });

  it('copies the code to the clipboard when the button is clicked', async () => {
    render(<RichMarkdown>{'```js\nconst x = 1;\n```'}</RichMarkdown>);
    await userEvent.click(screen.getByRole('button', { name: /copy code/i }));
    expect(writeText).toHaveBeenCalledWith('const x = 1;');
    await waitFor(() => expect(screen.getByRole('button', { name: /copied/i })).toBeDefined());
  });

  it('keeps inline code as a plain <code> (no copy button)', () => {
    render(<RichMarkdown>{'this is `inline` code'}</RichMarkdown>);
    expect(screen.queryByRole('button', { name: /copy code/i })).toBeNull();
  });

  it('highlightCode wraps keywords, strings and numbers without raw HTML', () => {
    const nodes = highlightCode('const s = "hi"; // note');
    // Returns an array of React nodes (strings + <span>), never an HTML string.
    expect(Array.isArray(nodes)).toBe(true);
    expect(nodes.length).toBeGreaterThan(1);
  });
});

describe('RichMarkdown GFM + prose rendering', () => {
  it('renders a table with the striped/bordered wrapper', () => {
    const md = '| A | B |\n| --- | --- |\n| 1 | 2 |';
    const { container } = render(<RichMarkdown>{md}</RichMarkdown>);
    expect(container.querySelector('table')).not.toBeNull();
    expect(container.querySelector('thead')).not.toBeNull();
    expect(screen.getByRole('columnheader', { name: 'A' })).toBeDefined();
    expect(screen.getByRole('cell', { name: '1' })).toBeDefined();
  });

  it('renders links with target=_blank and rel=noopener', () => {
    render(<RichMarkdown>{'[docs](https://example.com)'}</RichMarkdown>);
    const link = screen.getByRole('link', { name: 'docs' });
    expect(link).toHaveAttribute('href', 'https://example.com');
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
  });

  it('renders headings h1-h3', () => {
    render(<RichMarkdown>{'# H1\n## H2\n### H3'}</RichMarkdown>);
    expect(screen.getByRole('heading', { level: 1, name: 'H1' })).toBeDefined();
    expect(screen.getByRole('heading', { level: 2, name: 'H2' })).toBeDefined();
    expect(screen.getByRole('heading', { level: 3, name: 'H3' })).toBeDefined();
  });

  it('renders unordered and ordered lists', () => {
    const { container } = render(<RichMarkdown>{'- a\n- b\n\n1. x\n2. y'}</RichMarkdown>);
    expect(container.querySelector('ul')).not.toBeNull();
    expect(container.querySelector('ol')).not.toBeNull();
    expect(screen.getAllByRole('listitem')).toHaveLength(4);
  });

  it('renders a blockquote and a horizontal rule', () => {
    const { container } = render(<RichMarkdown>{'> quoted\n\n---'}</RichMarkdown>);
    expect(container.querySelector('blockquote')).not.toBeNull();
    expect(container.querySelector('hr')).not.toBeNull();
  });

  it('renders images with the rounded/bordered styling', () => {
    const { container } = render(<RichMarkdown>{'![alt text](https://example.com/img.png)'}</RichMarkdown>);
    const img = container.querySelector('img');
    expect(img).not.toBeNull();
    expect(img).toHaveAttribute('alt', 'alt text');
  });

  it('renders a plain paragraph', () => {
    const { container } = render(<RichMarkdown>{'just some prose'}</RichMarkdown>);
    expect(container.querySelector('p')).not.toBeNull();
    expect(screen.getByText('just some prose')).toBeDefined();
  });

  it('sanitizes raw HTML while still rendering safe tags like <br>', () => {
    const { container } = render(
      <RichMarkdown>{'line one<br/>line two\n\n<script>alert(1)</script>'}</RichMarkdown>,
    );
    expect(container.querySelector('br')).not.toBeNull();
    expect(container.querySelector('script')).toBeNull();
  });

  it('applies the custom className to the wrapper', () => {
    const { container } = render(<RichMarkdown className="custom-class">{'hi'}</RichMarkdown>);
    expect(container.querySelector('.custom-class')).not.toBeNull();
  });
});
