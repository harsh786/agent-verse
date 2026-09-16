import { render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import type { UseYjsCollabReturn } from '@/hooks/useYjsCollab';
import { useYjsCollab } from '@/hooks/useYjsCollab';
import { CRDTEditor } from './CRDTEditor';

vi.mock('@/hooks/useYjsCollab', () => ({
  useYjsCollab: vi.fn(),
}));

const mockedUseYjsCollab = vi.mocked(useYjsCollab);

function makeReturn(overrides: Partial<UseYjsCollabReturn> = {}): UseYjsCollabReturn {
  return {
    text: '',
    setText: vi.fn(),
    updateCursorPosition: vi.fn(),
    cursors: [],
    connected: false,
    synced: false,
    awareness: null,
    undoManager: null,
    undo: vi.fn(),
    redo: vi.fn(),
    canUndo: false,
    canRedo: false,
    ...overrides,
  };
}

describe('CRDTEditor', () => {
  beforeEach(() => {
    mockedUseYjsCollab.mockReset();
  });
  afterEach(() => vi.restoreAllMocks());

  test('renders textarea with placeholder', () => {
    mockedUseYjsCollab.mockReturnValue(makeReturn());
    render(<CRDTEditor roomId="room-1" placeholder="Type here…" />);
    expect(screen.getByPlaceholderText('Type here…')).toBeInTheDocument();
  });

  test('shows Connecting… status when not connected and not synced', () => {
    mockedUseYjsCollab.mockReturnValue(makeReturn({ connected: false, synced: false }));
    render(<CRDTEditor roomId="room-1" />);
    expect(screen.getByText('Connecting…')).toBeInTheDocument();
  });

  test('shows Live status when connected', () => {
    mockedUseYjsCollab.mockReturnValue(makeReturn({ connected: true, synced: true }));
    render(<CRDTEditor roomId="room-1" />);
    expect(screen.getByText('Live')).toBeInTheDocument();
  });

  test('shows Offline status when synced but not connected', () => {
    mockedUseYjsCollab.mockReturnValue(makeReturn({ connected: false, synced: true }));
    render(<CRDTEditor roomId="room-1" />);
    expect(screen.getByText('Offline')).toBeInTheDocument();
  });

  test('calls setText with local origin on user typing', async () => {
    const setText = vi.fn();
    mockedUseYjsCollab.mockReturnValue(makeReturn({ text: 'hi', setText }));
    render(<CRDTEditor roomId="room-1" />);
    const textarea = screen.getByLabelText('Collaborative editor');
    await userEvent.type(textarea, '!');
    expect(setText).toHaveBeenCalledWith(expect.any(String), 'local');
  });

  test('does not call setText when readOnly', () => {
    const setText = vi.fn();
    mockedUseYjsCollab.mockReturnValue(makeReturn({ text: 'hi', setText }));
    render(<CRDTEditor roomId="room-1" readOnly />);
    const textarea = screen.getByLabelText('Collaborative editor') as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: 'hi!' } });
    expect(setText).not.toHaveBeenCalled();
    expect(textarea).toHaveAttribute('readonly');
  });

  test('hides undo/redo buttons when readOnly', () => {
    mockedUseYjsCollab.mockReturnValue(makeReturn());
    render(<CRDTEditor roomId="room-1" readOnly />);
    expect(screen.queryByTitle('Undo (Ctrl+Z)')).not.toBeInTheDocument();
    expect(screen.queryByTitle('Redo (Ctrl+Y)')).not.toBeInTheDocument();
  });

  test('applies initialContent once when synced and text is empty', () => {
    const setText = vi.fn();
    mockedUseYjsCollab.mockReturnValue(makeReturn({ text: '', setText, synced: true, connected: true }));
    render(<CRDTEditor roomId="room-1" initialContent="hello world" />);
    expect(setText).toHaveBeenCalledWith('hello world', 'init');
  });

  test('applies initialContent when not connected (offline/test mode)', () => {
    const setText = vi.fn();
    mockedUseYjsCollab.mockReturnValue(makeReturn({ text: '', setText, synced: false, connected: false }));
    render(<CRDTEditor roomId="room-1" initialContent="offline content" />);
    expect(setText).toHaveBeenCalledWith('offline content', 'init');
  });

  test('does not apply initialContent when connected but not yet synced', () => {
    const setText = vi.fn();
    mockedUseYjsCollab.mockReturnValue(makeReturn({ text: '', setText, synced: false, connected: true }));
    render(<CRDTEditor roomId="room-1" initialContent="should not apply" />);
    expect(setText).not.toHaveBeenCalled();
  });

  test('does not apply initialContent when text already has content', () => {
    const setText = vi.fn();
    mockedUseYjsCollab.mockReturnValue(makeReturn({ text: 'existing', setText, synced: true, connected: true }));
    render(<CRDTEditor roomId="room-1" initialContent="should not apply" />);
    expect(setText).not.toHaveBeenCalled();
  });

  test('calls onChange with the latest text', () => {
    const onChange = vi.fn();
    mockedUseYjsCollab.mockReturnValue(makeReturn({ text: 'abc' }));
    render(<CRDTEditor roomId="room-1" onChange={onChange} />);
    expect(onChange).toHaveBeenCalledWith('abc');
  });

  test('Ctrl+Z triggers undo', () => {
    const undo = vi.fn();
    mockedUseYjsCollab.mockReturnValue(makeReturn({ undo, canUndo: true }));
    render(<CRDTEditor roomId="room-1" />);
    const textarea = screen.getByLabelText('Collaborative editor');
    fireEvent.keyDown(textarea, { key: 'z', ctrlKey: true });
    expect(undo).toHaveBeenCalled();
  });

  test('Ctrl+Shift+Z triggers redo', () => {
    const redo = vi.fn();
    mockedUseYjsCollab.mockReturnValue(makeReturn({ redo, canRedo: true }));
    render(<CRDTEditor roomId="room-1" />);
    const textarea = screen.getByLabelText('Collaborative editor');
    fireEvent.keyDown(textarea, { key: 'z', ctrlKey: true, shiftKey: true });
    expect(redo).toHaveBeenCalled();
  });

  test('Ctrl+Y triggers redo', () => {
    const redo = vi.fn();
    mockedUseYjsCollab.mockReturnValue(makeReturn({ redo, canRedo: true }));
    render(<CRDTEditor roomId="room-1" />);
    const textarea = screen.getByLabelText('Collaborative editor');
    fireEvent.keyDown(textarea, { key: 'y', ctrlKey: true });
    expect(redo).toHaveBeenCalled();
  });

  test('clicking undo button calls undo when enabled', async () => {
    const undo = vi.fn();
    mockedUseYjsCollab.mockReturnValue(makeReturn({ undo, canUndo: true }));
    render(<CRDTEditor roomId="room-1" />);
    await userEvent.click(screen.getByTitle('Undo (Ctrl+Z)'));
    expect(undo).toHaveBeenCalled();
  });

  test('clicking redo button calls redo when enabled', async () => {
    const redo = vi.fn();
    mockedUseYjsCollab.mockReturnValue(makeReturn({ redo, canRedo: true }));
    render(<CRDTEditor roomId="room-1" />);
    await userEvent.click(screen.getByTitle('Redo (Ctrl+Y)'));
    expect(redo).toHaveBeenCalled();
  });

  test('undo/redo buttons disabled when canUndo/canRedo are false', () => {
    mockedUseYjsCollab.mockReturnValue(makeReturn({ canUndo: false, canRedo: false }));
    render(<CRDTEditor roomId="room-1" />);
    expect(screen.getByTitle('Undo (Ctrl+Z)')).toBeDisabled();
    expect(screen.getByTitle('Redo (Ctrl+Y)')).toBeDisabled();
  });

  test('shows "Only you here" when no remote cursors', () => {
    mockedUseYjsCollab.mockReturnValue(makeReturn({ cursors: [] }));
    render(<CRDTEditor roomId="room-1" />);
    expect(screen.getByText('Only you here')).toBeInTheDocument();
  });

  test('renders an awareness pill per remote cursor', () => {
    mockedUseYjsCollab.mockReturnValue(
      makeReturn({
        cursors: [
          { clientId: 1, name: 'Alice', color: '#FF0000', position: 0 },
          { clientId: 2, name: 'Bob', color: '#00FF00', position: 3 },
        ],
      }),
    );
    render(<CRDTEditor roomId="room-1" />);
    expect(screen.getByText('Alice')).toBeInTheDocument();
    expect(screen.getByText('Bob')).toBeInTheDocument();
    expect(screen.queryByText('Only you here')).not.toBeInTheDocument();
  });

  test('shows character count', () => {
    mockedUseYjsCollab.mockReturnValue(makeReturn({ text: 'hello' }));
    render(<CRDTEditor roomId="room-1" />);
    expect(screen.getByText('5 chars')).toBeInTheDocument();
  });

  test('shows offline footer when disconnected with content', () => {
    mockedUseYjsCollab.mockReturnValue(makeReturn({ connected: false, text: 'some content' }));
    render(<CRDTEditor roomId="room-1" />);
    expect(screen.getByText(/Working offline/)).toBeInTheDocument();
  });

  test('hides offline footer when connected', () => {
    mockedUseYjsCollab.mockReturnValue(makeReturn({ connected: true, text: 'some content' }));
    render(<CRDTEditor roomId="room-1" />);
    expect(screen.queryByText(/Working offline/)).not.toBeInTheDocument();
  });

  test('hides offline footer when disconnected but no text', () => {
    mockedUseYjsCollab.mockReturnValue(makeReturn({ connected: false, text: '' }));
    render(<CRDTEditor roomId="room-1" />);
    expect(screen.queryByText(/Working offline/)).not.toBeInTheDocument();
  });

  test('updateCursorPosition called on select and key up', () => {
    const updateCursorPosition = vi.fn();
    mockedUseYjsCollab.mockReturnValue(makeReturn({ text: 'hello', updateCursorPosition }));
    render(<CRDTEditor roomId="room-1" />);
    const textarea = screen.getByLabelText('Collaborative editor') as HTMLTextAreaElement;
    fireEvent.select(textarea);
    expect(updateCursorPosition).toHaveBeenCalled();
    const callsAfterSelect = updateCursorPosition.mock.calls.length;
    fireEvent.keyUp(textarea);
    expect(updateCursorPosition.mock.calls.length).toBeGreaterThan(callsAfterSelect);
  });

  test('passes roomId, userName and userColor through to useYjsCollab', () => {
    mockedUseYjsCollab.mockReturnValue(makeReturn());
    render(<CRDTEditor roomId="room-42" userName="Alice" userColor="#123456" />);
    expect(mockedUseYjsCollab).toHaveBeenCalledWith({
      roomId: 'room-42',
      userName: 'Alice',
      color: '#123456',
    });
  });

  test('applies custom className', () => {
    mockedUseYjsCollab.mockReturnValue(makeReturn());
    const { container } = render(<CRDTEditor roomId="room-1" className="my-class" />);
    expect(container.querySelector('.my-class')).toBeInTheDocument();
  });
});
