/** DatabaseForm — MongoDB advanced options and X.509 (mongo re-audit B4, B5). */
import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { describe, expect, test, vi } from 'vitest';
import { DatabaseForm } from './DatabaseForm';

/** A controlled harness so typing accumulates like it does in the wizard. */
function Harness({ initial = {}, spy }: { initial?: Record<string, unknown>; spy: (v: Record<string, unknown>) => void }) {
  const [value, setValue] = useState<Record<string, unknown>>(initial);
  return <DatabaseForm sourceType="mongodb" value={value} onChange={(v) => { setValue(v); spy(v); }} />;
}
const last = (spy: ReturnType<typeof vi.fn>) => spy.mock.calls.at(-1)![0] as Record<string, unknown>;

describe('B4 MongoDB advanced options', () => {
  test('are collapsed under Advanced and reveal host/port, replica set, batch size, max docs, timeout', async () => {
    render(<Harness spy={vi.fn()} />);
    expect(screen.queryByLabelText(/^host/i)).not.toBeInTheDocument();
    const toggle = screen.getByRole('button', { name: /advanced/i });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    await userEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    for (const label of [/^host/i, /^port/i, /replica set/i, /batch size/i, /max documents per sync/i, /timeout \(ms\)/i]) {
      expect(screen.getByLabelText(label)).toBeInTheDocument();
    }
  });

  test('values are sent with the backend keys; numbers as numbers; cleared numbers are dropped', async () => {
    const spy = vi.fn();
    render(<Harness spy={spy} />);
    await userEvent.click(screen.getByRole('button', { name: /advanced/i }));
    await userEvent.type(screen.getByLabelText(/^host/i), 'h1.example.com,h2.example.com');
    fireEvent.change(screen.getByLabelText(/^port/i), { target: { value: '27018' } });
    await userEvent.type(screen.getByLabelText(/replica set/i), 'rs0');
    fireEvent.change(screen.getByLabelText(/batch size/i), { target: { value: '500' } });
    fireEvent.change(screen.getByLabelText(/max documents per sync/i), { target: { value: '20000' } });
    fireEvent.change(screen.getByLabelText(/timeout \(ms\)/i), { target: { value: '10000' } });
    expect(last(spy)).toMatchObject({
      host: 'h1.example.com,h2.example.com', port: 27018, replica_set: 'rs0',
      batch_size: 500, max_documents_per_sync: 20000, timeout_ms: 10000,
    });
    fireEvent.change(screen.getByLabelText(/batch size/i), { target: { value: '' } });
    expect(last(spy)).not.toHaveProperty('batch_size');
  });

  test('opens by itself when an advanced option is already set (edit)', () => {
    render(<Harness initial={{ uri: '********', replica_set: 'rs0' }} spy={vi.fn()} />);
    expect(screen.getByRole('button', { name: /advanced/i })).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByLabelText(/replica set/i)).toHaveValue('rs0');
  });
});
