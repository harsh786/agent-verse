import { render, screen } from '@testing-library/react';
import { describe, expect, test } from 'vitest';
import { GuardrailsPanel } from './GuardrailsPanel';

describe('GuardrailsPanel', () => {
  test('renders every active guardrail layer', () => {
    render(<GuardrailsPanel />);
    expect(screen.getByText('Active Guardrail Layers')).toBeInTheDocument();
    for (const layer of [
      'Encoding Attack Decoder',
      'Prompt Injection Scanner',
      'Indirect Injection Scanner',
      'PII & PHI Detector',
      'Data Exfiltration Guard',
      'Output Anomaly Detector',
      'LLM Judge (Cloud Destruction)',
    ]) {
      expect(screen.getByText(layer)).toBeInTheDocument();
    }
  });

  test('shows severity and status badges for the layers', () => {
    render(<GuardrailsPanel />);
    // Three layers are critical, four are high.
    expect(screen.getAllByText('critical')).toHaveLength(3);
    expect(screen.getAllByText('high')).toHaveLength(4);
    // Every layer is active — 7 "active" status pills.
    expect(screen.getAllByText('active')).toHaveLength(7);
  });

  test('renders the red-team coverage categories with counts', () => {
    render(<GuardrailsPanel />);
    expect(screen.getByText('Red-Team Test Coverage')).toBeInTheDocument();
    expect(screen.getByText('Direct Injection')).toBeInTheDocument();
    expect(screen.getByText('Jailbreak')).toBeInTheDocument();
    expect(screen.getByText('Legitimate (Pass)')).toBeInTheDocument();
  });

  test('summarises the total red-team case count', () => {
    render(<GuardrailsPanel />);
    expect(screen.getByText(/51 total red-team cases/i)).toBeInTheDocument();
  });
});
