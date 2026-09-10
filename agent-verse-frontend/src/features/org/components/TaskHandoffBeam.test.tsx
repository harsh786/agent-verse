import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { TaskHandoffLabel } from './TaskHandoffBeam';

describe('TaskHandoffLabel', () => {
  it('renders the traveling packet label', () => {
    render(<TaskHandoffLabel from={{ x: 0, y: 0 }} to={{ x: 100, y: 100 }} label="Write draft" />);
    expect(screen.getByText('Write draft')).toBeInTheDocument();
  });

  it('renders nothing under prefers-reduced-motion — no fabricated travel animation', () => {
    const { container } = render(
      <TaskHandoffLabel from={{ x: 0, y: 0 }} to={{ x: 100, y: 100 }} label="Write draft" reduce />
    );
    expect(container).toBeEmptyDOMElement();
  });
});
