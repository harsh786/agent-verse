/**
 * Tests for ConstitutionEditor — a form-based governance editor.
 *
 * Presentational: render with a representative constitution + a vi.fn onSave,
 * then assert real rendered fields, dirty-tracking, and the saved draft.
 */
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, test, vi } from 'vitest';
import { ConstitutionEditor } from './ConstitutionEditor';
import type { CivilizationConstitution } from '../../lib/api/civilizationApi';

const CONSTITUTION: CivilizationConstitution = {
  max_depth: 3,
  max_total_agents: 10,
  max_concurrent_agents: 5,
  total_budget_usd: 100,
  per_agent_budget_usd: 10,
  budget_decay: 0.5,
  reputation_floor: 0.2,
  spawn_rate_limit_per_min: 5,
  idle_ttl_seconds: 3600,
  autonomy_ceiling: 'bounded-autonomous',
  high_risk_requires_hitl: true,
};

function renderEditor(overrides: Partial<{
  constitution: CivilizationConstitution;
  readOnly: boolean;
}> = {}) {
  const onSave = vi.fn<(c: CivilizationConstitution) => Promise<void>>().mockResolvedValue(undefined);
  render(
    <ConstitutionEditor
      constitution={overrides.constitution ?? CONSTITUTION}
      onSave={onSave}
      readOnly={overrides.readOnly}
    />,
  );
  return { onSave };
}

describe('ConstitutionEditor', () => {
  test('renders governance field labels and controls', () => {
    renderEditor();
    expect(screen.getByText('Governance Rules')).toBeInTheDocument();
    expect(screen.getByText('Max Spawn Depth')).toBeInTheDocument();
    expect(screen.getByText('Autonomy Ceiling')).toBeInTheDocument();
    expect(screen.getByText('HITL for High-Risk')).toBeInTheDocument();
    expect(screen.getByRole('switch', { name: /HITL for High-Risk/i })).toBeInTheDocument();
  });

  test('formats slider values with their units', () => {
    renderEditor();
    expect(screen.getByText('$100')).toBeInTheDocument();   // total_budget_usd (int)
    expect(screen.getByText('$10.00')).toBeInTheDocument();  // per_agent_budget_usd (step < 1)
    expect(screen.getByText('0.50')).toBeInTheDocument();    // budget_decay
  });

  test('toggling a boolean marks the form dirty', async () => {
    renderEditor();
    const sw = screen.getByRole('switch', { name: /HITL for High-Risk/i });
    expect(sw).toBeChecked();
    expect(screen.queryByText('Unsaved')).not.toBeInTheDocument();
    await userEvent.click(sw);
    expect(sw).not.toBeChecked();
    expect(screen.getByText('Unsaved')).toBeInTheDocument();
  });

  test('dragging a slider updates the displayed value and marks dirty', () => {
    renderEditor();
    const slider = screen.getByRole('slider', { name: 'Max Spawn Depth' });
    fireEvent.change(slider, { target: { value: '7' } });
    expect(screen.getByText('7')).toBeInTheDocument();
    expect(screen.getByText('Unsaved')).toBeInTheDocument();
  });

  test('changing a select marks the form dirty', () => {
    renderEditor();
    const select = screen.getByRole('combobox', { name: 'Autonomy Ceiling' });
    fireEvent.change(select, { target: { value: 'fully-autonomous' } });
    expect(screen.getByText('Unsaved')).toBeInTheDocument();
    expect((select as HTMLSelectElement).value).toBe('fully-autonomous');
  });

  test('Save is disabled until dirty, then calls onSave with the edited draft', async () => {
    const { onSave } = renderEditor();
    const saveBtn = screen.getByRole('button', { name: /Save Constitution/i });
    expect(saveBtn).toBeDisabled();

    await userEvent.click(screen.getByRole('switch', { name: /HITL for High-Risk/i }));
    expect(saveBtn).toBeEnabled();
    await userEvent.click(saveBtn);

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    expect(onSave.mock.calls[0][0]).toEqual(
      expect.objectContaining({ high_risk_requires_hitl: false, max_depth: 3 }),
    );
    expect(await screen.findByText('Saved!')).toBeInTheDocument();
  });

  test('Reset reverts the draft back to the last saved constitution', async () => {
    renderEditor();
    fireEvent.change(screen.getByRole('slider', { name: 'Max Spawn Depth' }), { target: { value: '7' } });
    expect(screen.getByText('Unsaved')).toBeInTheDocument();
    await userEvent.click(screen.getByTitle('Reset to last saved'));
    expect(screen.queryByText('Unsaved')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Save Constitution/i })).toBeDisabled();
  });

  test('raw JSON editor rejects invalid JSON and applies valid JSON', async () => {
    renderEditor();
    await userEvent.click(screen.getByRole('button', { name: /Raw JSON/i }));
    const textarea = screen.getByRole('textbox');

    // Invalid → error shown, panel stays open, draft untouched.
    fireEvent.change(textarea, { target: { value: '@@@ not json' } });
    await userEvent.click(screen.getByRole('button', { name: /Apply JSON/i }));
    expect(screen.getByText(/valid JSON|Unexpected|Expected/i)).toBeInTheDocument();
    expect(screen.queryByText('Unsaved')).not.toBeInTheDocument();

    // Valid → draft updates (max_depth 9) and the form becomes dirty.
    fireEvent.change(textarea, { target: { value: JSON.stringify({ ...CONSTITUTION, max_depth: 9 }) } });
    await userEvent.click(screen.getByRole('button', { name: /Apply JSON/i }));
    expect(screen.getByText('9')).toBeInTheDocument();
    expect(screen.getByText('Unsaved')).toBeInTheDocument();
  });

  test('readOnly hides the save/reset actions and disables the toggle', () => {
    renderEditor({ readOnly: true });
    expect(screen.queryByRole('button', { name: /Save Constitution/i })).not.toBeInTheDocument();
    expect(screen.queryByTitle('Reset to last saved')).not.toBeInTheDocument();
    expect(screen.getByRole('switch', { name: /HITL for High-Risk/i })).toBeDisabled();
  });
});
