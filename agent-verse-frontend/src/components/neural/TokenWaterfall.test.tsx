import { render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';

// Keep real motion (it renders children synchronously in jsdom) but make
// useReducedMotion deterministic per-test.
const reduce = vi.fn(() => false);
vi.mock('framer-motion', async (importOriginal) => {
  const actual = await importOriginal<typeof import('framer-motion')>();
  return { ...actual, useReducedMotion: () => reduce() };
});

import { TokenWaterfall } from './TokenWaterfall';

beforeEach(() => reduce.mockReturnValue(false));
afterEach(() => {
  vi.restoreAllMocks();
  reduce.mockReturnValue(false);
});

describe('TokenWaterfall', () => {
  test('renders nothing when inactive and no tokens', () => {
    const { container } = render(
      <TokenWaterfall stepName="plan" tokens="" isActive={false} />
    );
    expect(container).toBeEmptyDOMElement();
  });

  test('renders when inactive but tokens already exist', () => {
    render(<TokenWaterfall stepName="plan" tokens="hello" isActive={false} />);
    expect(screen.getByText('hello')).toBeInTheDocument();
  });

  test('renders when active even without tokens yet', () => {
    const { container } = render(
      <TokenWaterfall stepName="plan" tokens="" isActive />
    );
    expect(container).not.toBeEmptyDOMElement();
  });

  test('shows the pulsing indicator dot when active and motion is enabled', () => {
    const { container } = render(
      <TokenWaterfall stepName="plan" tokens="" isActive />
    );
    expect(container.querySelector('.animate-pulse')).toBeInTheDocument();
  });

  test('hides the pulsing indicator dot under reduced motion', () => {
    reduce.mockReturnValue(true);
    const { container } = render(
      <TokenWaterfall stepName="plan" tokens="" isActive />
    );
    expect(container.querySelector('.animate-pulse')).not.toBeInTheDocument();
  });

  test('hides the pulsing indicator dot when not active', () => {
    const { container } = render(
      <TokenWaterfall stepName="plan" tokens="abc" isActive={false} />
    );
    expect(container.querySelector('.animate-pulse')).not.toBeInTheDocument();
  });

  test('truncates a long step name to 50 characters', () => {
    const long = 'x'.repeat(80);
    render(<TokenWaterfall stepName={long} tokens="" isActive />);
    expect(screen.getByText('x'.repeat(50))).toBeInTheDocument();
  });

  test('omits the model badge when no model is given', () => {
    render(<TokenWaterfall stepName="plan" tokens="abc" isActive={false} />);
    expect(screen.queryByText(/claude|gpt|gemini/i)).not.toBeInTheDocument();
  });

  test('renders the model badge, truncated to 20 characters, when a model is given', () => {
    const longModel = 'claude-opus-4-1-super-long-name-tail';
    render(
      <TokenWaterfall stepName="plan" tokens="abc" isActive={false} model={longModel} />
    );
    expect(screen.getByText(longModel.slice(0, 20))).toBeInTheDocument();
  });

  test('omits the token count badge when tokens are empty', () => {
    render(<TokenWaterfall stepName="plan" tokens="" isActive />);
    expect(screen.queryByText(/tok$/)).not.toBeInTheDocument();
  });

  test('shows the token count badge when tokens are present', () => {
    render(<TokenWaterfall stepName="plan" tokens="hello" isActive={false} />);
    expect(screen.getByText('5 tok')).toBeInTheDocument();
  });

  test('renders the blinking cursor while active', () => {
    const { container } = render(
      <TokenWaterfall stepName="plan" tokens="partial" isActive />
    );
    const pre = container.querySelector('pre') as HTMLElement;
    expect(within(pre).getByText('partial')).toBeInTheDocument();
    expect(pre.querySelector('span[aria-hidden]')).toBeInTheDocument();
  });

  test('omits the blinking cursor when not active', () => {
    const { container } = render(
      <TokenWaterfall stepName="plan" tokens="done" isActive={false} />
    );
    const pre = container.querySelector('pre') as HTMLElement;
    expect(pre.querySelector('span[aria-hidden]')).not.toBeInTheDocument();
  });

  test('applies a custom className alongside the default classes', () => {
    const { container } = render(
      <TokenWaterfall stepName="plan" tokens="abc" isActive={false} className="extra-class" />
    );
    expect(container.firstChild).toHaveClass('extra-class');
    expect(container.firstChild).toHaveClass('rounded-xl');
  });

  describe('model color resolution', () => {
    test('falls back to the default color when no model is given', () => {
      const { container } = render(
        <TokenWaterfall stepName="plan" tokens="" isActive />
      );
      // No model badge is rendered, so no color-styled span exists either.
      expect(container.querySelector('span[style*="color"]')).not.toBeInTheDocument();
    });

    test('matches a known model key case-insensitively (gpt)', () => {
      const { container } = render(
        <TokenWaterfall stepName="plan" tokens="abc" isActive={false} model="GPT-4o" />
      );
      const badge = screen.getByText('GPT-4o');
      expect(badge).toHaveStyle({ color: '#10A37F' });
      expect(container).toBeTruthy();
    });

    test('matches a different known model key (gemini)', () => {
      render(<TokenWaterfall stepName="plan" tokens="abc" isActive={false} model="gemini-2.5-pro" />);
      expect(screen.getByText('gemini-2.5-pro')).toHaveStyle({ color: '#4285F4' });
    });

    test('falls back to the default color for an unrecognized model name', () => {
      render(<TokenWaterfall stepName="plan" tokens="abc" isActive={false} model="mystery-model" />);
      expect(screen.getByText('mystery-model')).toHaveStyle({ color: '#6366F1' });
    });
  });
});
