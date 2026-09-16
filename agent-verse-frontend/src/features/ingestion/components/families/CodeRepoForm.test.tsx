import { render, screen, fireEvent } from '@testing-library/react';
import { afterEach, describe, expect, test, vi } from 'vitest';
import { CodeRepoForm } from './CodeRepoForm';

function lastArg(fn: ReturnType<typeof vi.fn>): Record<string, unknown> {
  return fn.mock.calls.at(-1)![0] as Record<string, unknown>;
}

afterEach(() => vi.restoreAllMocks());

describe('CodeRepoForm', () => {
  test('always renders repository URL, branch, and file pattern fields', () => {
    render(<CodeRepoForm sourceType="gitea" value={{}} onChange={vi.fn()} />);
    expect(screen.getByText('Repository URL')).toBeInTheDocument();
    expect(screen.getByText('Branch')).toBeInTheDocument();
    expect(screen.getByText('File Pattern (glob)')).toBeInTheDocument();
    expect(screen.getByDisplayValue('main')).toBeInTheDocument();
    expect(screen.getByDisplayValue('**/*.{md,py,ts,js,txt}')).toBeInTheDocument();
  });

  test('renders access token field for github and gitlab, not other types', () => {
    const { rerender } = render(<CodeRepoForm sourceType="github" value={{}} onChange={vi.fn()} />);
    expect(screen.getByText('Access Token')).toBeInTheDocument();
    rerender(<CodeRepoForm sourceType="gitlab" value={{}} onChange={vi.fn()} />);
    expect(screen.getByText('Access Token')).toBeInTheDocument();
    rerender(<CodeRepoForm sourceType="gitea" value={{}} onChange={vi.fn()} />);
    expect(screen.queryByText('Access Token')).not.toBeInTheDocument();
  });

  test('renders app password field only for bitbucket', () => {
    const { rerender } = render(<CodeRepoForm sourceType="bitbucket" value={{}} onChange={vi.fn()} />);
    expect(screen.getByText('App Password')).toBeInTheDocument();
    rerender(<CodeRepoForm sourceType="github" value={{}} onChange={vi.fn()} />);
    expect(screen.queryByText('App Password')).not.toBeInTheDocument();
  });

  test('typing repo URL calls onChange with merged value', () => {
    const onChange = vi.fn();
    render(<CodeRepoForm sourceType="github" value={{ branch: 'dev' }} onChange={onChange} />);
    fireEvent.change(screen.getByPlaceholderText('https://github.com/org/repo'), {
      target: { value: 'https://github.com/acme/repo' },
    });
    expect(lastArg(onChange)).toEqual({ branch: 'dev', repo_url: 'https://github.com/acme/repo' });
  });

  test('typing the access token calls onChange', () => {
    const onChange = vi.fn();
    render(<CodeRepoForm sourceType="github" value={{}} onChange={onChange} />);
    const tokenInput = screen.getByText('Access Token').parentElement?.querySelector('input');
    fireEvent.change(tokenInput as HTMLInputElement, { target: { value: 'ghp_123' } });
    expect(lastArg(onChange)).toEqual({ access_token: 'ghp_123' });
  });

  test('typing the app password for bitbucket calls onChange', () => {
    const onChange = vi.fn();
    render(<CodeRepoForm sourceType="bitbucket" value={{}} onChange={onChange} />);
    const pwInput = screen.getByText('App Password').parentElement?.querySelector('input');
    fireEvent.change(pwInput as HTMLInputElement, { target: { value: 'app-pw' } });
    expect(lastArg(onChange)).toEqual({ app_password: 'app-pw' });
  });
});
