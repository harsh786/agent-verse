import { render, screen } from '@testing-library/react';
import { describe, expect, test } from 'vitest';
import { ReflexionEvidenceView } from './ReflexionEvidenceView';

describe('ReflexionEvidenceView', () => {
  test('renders nothing under the heading when evidence is empty', () => {
    render(<ReflexionEvidenceView evidence={[]} />);
    expect(screen.getByText('Reflexion evidence')).toBeInTheDocument();
    expect(screen.queryByRole('listitem')).not.toBeInTheDocument();
  });

  test('renders a confidence percentage rounded from a numeric confidence', () => {
    render(<ReflexionEvidenceView evidence={[{ memory_id: 'm1', confidence: 0.876 }]} />);
    expect(screen.getByText('88%')).toBeInTheDocument();
  });

  test('renders "unscored" when confidence is not a number', () => {
    render(<ReflexionEvidenceView evidence={[{ memory_id: 'm2', confidence: 'n/a' }]} />);
    expect(screen.getByText('unscored')).toBeInTheDocument();
  });

  test('renders "unscored" when confidence is entirely absent', () => {
    render(<ReflexionEvidenceView evidence={[{ memory_id: 'm3' }]} />);
    expect(screen.getByText('unscored')).toBeInTheDocument();
  });

  test('falls back to "General lesson" and "No provenance" when applicability/provenance missing', () => {
    render(<ReflexionEvidenceView evidence={[{ memory_id: 'm4' }]} />);
    expect(screen.getByText('General lesson')).toBeInTheDocument();
    expect(screen.getByText(/No provenance/)).toBeInTheDocument();
  });

  test('shows "Quarantined" when quarantined is truthy', () => {
    render(<ReflexionEvidenceView evidence={[{ memory_id: 'm5', quarantined: true }]} />);
    expect(screen.getByText(/Quarantined/)).toBeInTheDocument();
    expect(screen.queryByText(/Eligible/)).not.toBeInTheDocument();
  });

  test('shows "Eligible" when quarantined is falsy or absent', () => {
    render(<ReflexionEvidenceView evidence={[{ memory_id: 'm6', quarantined: false }]} />);
    expect(screen.getByText(/Eligible/)).toBeInTheDocument();
  });

  test('renders multiple evidence entries keyed by index when memory_id is missing', () => {
    render(
      <ReflexionEvidenceView
        evidence={[{ applicability: 'lesson-a' }, { applicability: 'lesson-b' }]}
      />,
    );
    expect(screen.getByText('lesson-a')).toBeInTheDocument();
    expect(screen.getByText('lesson-b')).toBeInTheDocument();
  });

  test('treats confidence of 0 as a scored numeric value (0%)', () => {
    render(<ReflexionEvidenceView evidence={[{ memory_id: 'm7', confidence: 0 }]} />);
    expect(screen.getByText('0%')).toBeInTheDocument();
  });
});
