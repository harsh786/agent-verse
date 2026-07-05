import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { CitationList } from './CitationList';

describe('CitationList', () => {
  it('renders citations', () => {
    render(
      <CitationList
        citations={[{ text: 'Found JIRA-123', source: 'step_1_jira', step: 1 }]}
      />
    );
    expect(screen.getByText('Sources & Citations')).toBeTruthy();
  });

  it('renders nothing when no citations', () => {
    const { container } = render(<CitationList citations={[]} />);
    expect(container.firstChild).toBeNull();
  });

  it('renders cited answer when provided', () => {
    render(
      <CitationList
        citations={[{ text: 'Some finding', source: 'source_1' }]}
        citedAnswer="This is the cited answer"
      />
    );
    expect(screen.getByText('This is the cited answer')).toBeTruthy();
  });

  it('renders multiple citations', () => {
    render(
      <CitationList
        citations={[
          { text: 'First finding', source: 'src_1', step: 1 },
          { text: 'Second finding', source: 'src_2', step: 2 },
        ]}
      />
    );
    expect(screen.getByText('First finding')).toBeTruthy();
    expect(screen.getByText('Second finding')).toBeTruthy();
  });

  it('uses index+1 when step is not provided', () => {
    render(
      <CitationList
        citations={[{ text: 'No step', source: 'src' }]}
      />
    );
    expect(screen.getByText('1')).toBeTruthy();
  });
});
