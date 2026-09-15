/** Phase 7 — downloadable artifact card from an artifact_created event. */
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ChatArtifactCard } from './ChatArtifactCard';

describe('ChatArtifactCard', () => {
  const artifact = { artifactId: 'art_1', title: 'report.pdf', language: 'pdf' };

  it('renders a download link pointing at the download endpoint', () => {
    render(<ChatArtifactCard artifact={artifact} />);
    const link = screen.getByRole('link', { name: /download report\.pdf/i });
    expect(link.getAttribute('href')).toContain('/chat/artifacts/art_1/download');
    expect(link.getAttribute('download')).toBe('report.pdf');
  });

  it('shows the title and language', () => {
    render(<ChatArtifactCard artifact={artifact} />);
    expect(screen.getByText('report.pdf')).toBeDefined();
    expect(screen.getByText('pdf')).toBeDefined();
  });

  it('fires onOpen with the artifact id when the open button is clicked', async () => {
    const onOpen = vi.fn();
    render(<ChatArtifactCard artifact={artifact} onOpen={onOpen} />);
    await userEvent.click(screen.getByRole('button', { name: /open report\.pdf/i }));
    expect(onOpen).toHaveBeenCalledWith('art_1');
  });

  it('omits the open button when no onOpen handler is given', () => {
    render(<ChatArtifactCard artifact={artifact} />);
    expect(screen.queryByRole('button', { name: /open/i })).toBeNull();
  });
});
