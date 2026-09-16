import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, test, vi } from 'vitest';
import React, { type ReactNode } from 'react';
import { CanvasViewer } from './CanvasViewer';

vi.mock('framer-motion', async (importOriginal) => {
  const actual = await importOriginal<typeof import('framer-motion')>();
  const makeStub = (tag: string) =>
    ({ children, ...props }: { children?: ReactNode; [k: string]: unknown }) =>
      React.createElement(tag, props as Record<string, unknown>, children);
  return {
    ...actual,
    motion: new Proxy(actual.motion as unknown as Record<string, unknown>, {
      get: (target, key: string) => (key in target ? target[key] : makeStub(key)),
    }),
  };
});

afterEach(() => vi.restoreAllMocks());

describe('CanvasViewer', () => {
  test('auto-detects JSON content and renders it in a code block', () => {
    render(<CanvasViewer content={'{\n  "hello": "world"\n}'} />);
    // header label from detected type
    expect(screen.getByText('JSON')).toBeInTheDocument();
    // content rendered verbatim inside <pre><code>
    expect(screen.getByText(/"hello": "world"/)).toBeInTheDocument();
  });

  test('auto-detects markdown vs code vs plain text', () => {
    const { rerender } = render(<CanvasViewer content={'# A heading'} />);
    expect(screen.getByText('Markdown')).toBeInTheDocument();

    rerender(<CanvasViewer content={'function add(a, b) { return a + b; }'} />);
    expect(screen.getByText('Code')).toBeInTheDocument();

    rerender(<CanvasViewer content={'just some prose text'} />);
    expect(screen.getByText('Text')).toBeInTheDocument();
  });

  test('renders an image when the content type is image', () => {
    render(<CanvasViewer content="https://x/pic.png" contentType="image" title="My Pic" />);
    const img = screen.getByAltText('My Pic') as HTMLImageElement;
    expect(img.tagName).toBe('IMG');
    expect(img.src).toContain('https://x/pic.png');
  });

  test('copy button writes the content to the clipboard', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    render(<CanvasViewer content="copy me" contentType="text" />);
    await userEvent.click(screen.getByRole('button', { name: 'Copy content' }));
    expect(writeText).toHaveBeenCalledWith('copy me');
  });

  test('maximize toggles the button between Maximize and Minimize', async () => {
    render(<CanvasViewer content="body" contentType="text" />);
    expect(screen.getByRole('button', { name: 'Maximize' })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Maximize' }));
    expect(screen.getByRole('button', { name: 'Minimize' })).toBeInTheDocument();
  });

  test('uses an explicit title and language in the header', () => {
    render(<CanvasViewer content="print('hi')" contentType="code" language="python" title="snippet.py" />);
    expect(screen.getByText(/snippet\.py/)).toBeInTheDocument();
    expect(screen.getByText(/python/)).toBeInTheDocument();
  });
});
