/**
 * Tests for ChatArtifactPanel — the artifact side-panel editor.
 */
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { ChatArtifactPanel } from './ChatArtifactPanel';
import type { ChatArtifact } from './types/chat.types';

const ARTIFACT: ChatArtifact = {
  id: 'a1',
  title: 'hello.py',
  language: 'python',
  content: 'print("hi")',
};

beforeEach(() => {
  Object.defineProperty(navigator, 'clipboard', {
    value: { writeText: vi.fn().mockResolvedValue(undefined) },
    configurable: true,
  });
});
afterEach(() => vi.restoreAllMocks());

describe('ChatArtifactPanel', () => {
  test('renders nothing when there is no artifact', () => {
    const { container } = render(<ChatArtifactPanel artifact={null} onClose={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });

  test('renders the title, language and content in the editor', () => {
    render(<ChatArtifactPanel artifact={ARTIFACT} onClose={vi.fn()} />);
    expect(screen.getByText('hello.py')).toBeInTheDocument();
    expect(screen.getByText('python')).toBeInTheDocument();
    expect(screen.getByLabelText('Artifact editor')).toHaveValue('print("hi")');
  });

  test('editing the textarea and clicking Save calls onSave with the new content', () => {
    const onSave = vi.fn();
    render(<ChatArtifactPanel artifact={ARTIFACT} onClose={vi.fn()} onSave={onSave} />);
    fireEvent.change(screen.getByLabelText('Artifact editor'), { target: { value: 'print("bye")' } });
    fireEvent.click(screen.getByRole('button', { name: /Save changes/i }));
    expect(onSave).toHaveBeenCalledWith('print("bye")');
  });

  test('hides the Save button when no onSave handler is provided', () => {
    render(<ChatArtifactPanel artifact={ARTIFACT} onClose={vi.fn()} />);
    expect(screen.queryByRole('button', { name: /Save changes/i })).not.toBeInTheDocument();
  });

  test('the copy button writes the current content to the clipboard', async () => {
    render(<ChatArtifactPanel artifact={ARTIFACT} onClose={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Copy artifact content' }));
    await waitFor(() => expect(navigator.clipboard.writeText).toHaveBeenCalledWith('print("hi")'));
  });

  test('the download button builds a blob download named after the artifact title', () => {
    const createObjectURL = vi.fn(() => 'blob:xyz');
    const revokeObjectURL = vi.fn();
    Object.defineProperty(URL, 'createObjectURL', { value: createObjectURL, configurable: true });
    Object.defineProperty(URL, 'revokeObjectURL', { value: revokeObjectURL, configurable: true });
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});

    render(<ChatArtifactPanel artifact={ARTIFACT} onClose={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Download artifact' }));

    expect(createObjectURL).toHaveBeenCalledTimes(1);
    expect(clickSpy).toHaveBeenCalledTimes(1);
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:xyz');
  });

  test('the close button calls onClose', () => {
    const onClose = vi.fn();
    render(<ChatArtifactPanel artifact={ARTIFACT} onClose={onClose} />);
    fireEvent.click(screen.getByRole('button', { name: 'Close artifact panel' }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
