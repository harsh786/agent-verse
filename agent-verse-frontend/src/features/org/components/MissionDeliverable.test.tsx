import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { MissionDeliverable } from './MissionDeliverable';

describe('MissionDeliverable', () => {
  it('renders string outputs and an evidence count when present', () => {
    render(
      <MissionDeliverable
        outputs={['Q3 revenue summary drafted', 'Sent to #finance']}
        evidence={[{ url: 'a' }, { url: 'b' }, { url: 'c' }]}
      />,
    );
    expect(screen.getByText(/deliverable/i)).toBeInTheDocument();
    expect(screen.getByText('2 outputs')).toBeInTheDocument();
    expect(screen.getByText(/q3 revenue summary drafted/i)).toBeInTheDocument();
    expect(screen.getByText(/3 evidence items attached/i)).toBeInTheDocument();
  });

  it('serialises non-string outputs as JSON and singularises counts', () => {
    render(<MissionDeliverable outputs={[{ metric: 'arr', value: 42 }]} evidence={[{ url: 'a' }]} />);
    expect(screen.getByText('1 output')).toBeInTheDocument();
    expect(screen.getByText(/"metric": "arr"/)).toBeInTheDocument();
    expect(screen.getByText(/1 evidence item attached/i)).toBeInTheDocument();
  });

  it('caps the rendered outputs at 8', () => {
    const outputs = Array.from({ length: 12 }, (_, i) => `line ${i}`);
    render(<MissionDeliverable outputs={outputs} evidence={[]} />);
    expect(screen.getByText('12 outputs')).toBeInTheDocument();
    expect(screen.getByText('line 7')).toBeInTheDocument();
    expect(screen.queryByText('line 8')).not.toBeInTheDocument();
    // No evidence line when there is no evidence.
    expect(screen.queryByText(/evidence item/i)).not.toBeInTheDocument();
  });

  it('renders nothing when there is no deliverable yet', () => {
    const { container } = render(<MissionDeliverable outputs={[]} evidence={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});
