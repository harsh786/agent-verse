import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { MissionDeliverable } from './MissionDeliverable';

describe('MissionDeliverable', () => {
  it('renders a string output as a deliverable card with its text', () => {
    render(
      <MissionDeliverable
        outputs={['Q3 revenue summary drafted']}
        evidence={[]}
      />,
    );
    expect(screen.getByText(/deliverable/i)).toBeInTheDocument();
    expect(screen.getByText(/q3 revenue summary drafted/i)).toBeInTheDocument();
  });

  it('unwraps a structured deliverable (summary) from an output entry', () => {
    render(
      <MissionDeliverable
        outputs={[{ deliverable: { title: 'Weekly digest', summary: 'Three bullets here' } }]}
        evidence={[]}
      />,
    );
    expect(screen.getByText('Weekly digest')).toBeInTheDocument();
    expect(screen.getByText(/three bullets here/i)).toBeInTheDocument();
  });

  it('renders typed artifacts — files, images and links — distinctly', () => {
    render(
      <MissionDeliverable
        outputs={[
          {
            deliverable: {
              summary: 'Report ready',
              files: [{ filename: 'report.pdf', url: 'https://x.test/report.pdf', content_type: 'application/pdf' }],
              images: [{ url: 'https://x.test/chart.png', caption: 'Revenue chart' }],
              links: [{ url: 'https://x.test/dashboard', title: 'Live dashboard' }],
            },
          },
        ]}
        evidence={[]}
      />,
    );
    expect(screen.getByText('report.pdf')).toBeInTheDocument();
    expect(screen.getByAltText('Revenue chart')).toBeInTheDocument();
    expect(screen.getByText('Live dashboard')).toBeInTheDocument();
  });

  it('renders evidence items with links rather than a bare count', () => {
    render(
      <MissionDeliverable
        outputs={['done']}
        evidence={['https://src.test/proof', { label: 'Source', url: 'https://src.test/doc' }]}
      />,
    );
    expect(screen.getByText(/evidence/i)).toBeInTheDocument();
    expect(screen.getByText('https://src.test/proof')).toBeInTheDocument();
    expect(screen.getByText(/source/i)).toBeInTheDocument();
  });

  it('shows a "published to" receipt with a live link when the deliverable was published', () => {
    render(
      <MissionDeliverable
        outputs={['posted']}
        evidence={[]}
        published={{
          server_id: 'builtin-utility',
          tool_name: 'http_request',
          success: true,
          output: { url: 'https://social.test/post/42' },
          published_at: '2026-09-11T10:07:47Z',
        }}
      />,
    );
    expect(screen.getByText(/published/i)).toBeInTheDocument();
    expect(screen.getByText('https://social.test/post/42')).toBeInTheDocument();
  });

  it('shows an approval-pending banner when publishing awaits approval', () => {
    render(<MissionDeliverable outputs={[]} evidence={[]} publishPending />);
    expect(screen.getByText(/awaiting your approval/i)).toBeInTheDocument();
  });

  it('renders nothing when there is no deliverable, receipt, or pending publish', () => {
    const { container } = render(<MissionDeliverable outputs={[]} evidence={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});
