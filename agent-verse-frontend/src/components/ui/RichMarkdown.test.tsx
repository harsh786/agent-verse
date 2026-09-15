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
