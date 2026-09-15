/** Phase 7 — rich output components are mounted for matching assistant output. */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { RichOutputRenderer } from './RichOutputRenderer';

describe('RichOutputRenderer', () => {
  it('renders a sortable ChatDataTable for a markdown table', () => {
    const { container } = render(
      <RichOutputRenderer content={'| Name | Age |\n|------|-----|\n| Ada | 36 |'} />,
    );
    // ChatDataTable shows a row-count footer and sortable headers.
    expect(container.querySelector('table')).not.toBeNull();
    expect(screen.getByText(/of 1 rows/)).toBeDefined();
  });

  it('renders a ChatChart for a ```chart block', () => {
    render(
      <RichOutputRenderer
        content={'```chart\n{"title":"T","data":[{"label":"Jan","value":10}]}\n```'}
      />,
    );
    expect(screen.getByRole('img', { name: 'T' })).toBeDefined();
    expect(screen.getByText('Jan')).toBeDefined();
  });

  it('renders a ChatImageOutput for a lone markdown image', () => {
    const { container } = render(
      <RichOutputRenderer content={'![diagram](https://example.com/x.png)'} />,
    );
    const img = container.querySelector('img');
    expect(img).not.toBeNull();
    expect(img?.getAttribute('src')).toBe('https://example.com/x.png');
  });

  it('renders markdown prose as rich markdown', () => {
    const { container } = render(<RichOutputRenderer content={'Here is **bold** text.'} />);
    expect(container.querySelector('strong')).not.toBeNull();
    expect(container.textContent).not.toContain('**bold**');
  });
});
