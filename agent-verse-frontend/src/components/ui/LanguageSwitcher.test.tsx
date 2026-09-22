import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, test } from 'vitest';
import i18n from '@/lib/i18n';
import { LanguageSwitcher } from './LanguageSwitcher';

afterEach(async () => {
  await i18n.changeLanguage('en');
});

describe('LanguageSwitcher', () => {
  test('renders a button per configured language with accessible labels', () => {
    render(<LanguageSwitcher />);
    expect(screen.getByRole('group', { name: /language selection/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Switch to English' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Switch to हिन्दी' })).toBeInTheDocument();
  });

  test('marks the active language as pressed and the rest as not pressed', async () => {
    await i18n.changeLanguage('en');
    render(<LanguageSwitcher />);
    expect(screen.getByRole('button', { name: 'Switch to English' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: 'Switch to हिन्दी' })).toHaveAttribute('aria-pressed', 'false');
  });

  test('clicking a language button switches i18n.language and updates pressed state', async () => {
    render(<LanguageSwitcher />);
    await userEvent.click(screen.getByRole('button', { name: 'Switch to हिन्दी' }));
    expect(i18n.language).toBe('hi');
    expect(screen.getByRole('button', { name: 'Switch to हिन्दी' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: 'Switch to English' })).toHaveAttribute('aria-pressed', 'false');
  });
});
