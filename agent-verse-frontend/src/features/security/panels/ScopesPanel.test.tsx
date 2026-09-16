import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, test } from 'vitest';
import { ScopesPanel } from './ScopesPanel';

describe('ScopesPanel', () => {
  test('renders the built-in role hierarchy with scopes', () => {
    render(<ScopesPanel />);
    expect(screen.getByText('Built-in Role Hierarchy')).toBeInTheDocument();
    for (const role of ['viewer', 'operator', 'builder', 'admin', 'owner']) {
      expect(screen.getByText(role)).toBeInTheDocument();
    }
    // A representative scope from the viewer role.
    expect(screen.getByText('goals:read')).toBeInTheDocument();
    // Owner has the wildcard scope.
    expect(screen.getByText('*')).toBeInTheDocument();
  });

  test('the create-role form is hidden until the button is clicked', () => {
    render(<ScopesPanel />);
    expect(screen.queryByText('Role Name')).not.toBeInTheDocument();
    expect(screen.queryByPlaceholderText('data-entry')).not.toBeInTheDocument();
  });

  test('clicking Create Role reveals the form fields', async () => {
    render(<ScopesPanel />);
    await userEvent.click(screen.getByRole('button', { name: /Create Role/i }));
    expect(screen.getByText('Role Name')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('data-entry')).toBeInTheDocument();
    expect(screen.getByText('Inherits From')).toBeInTheDocument();
    expect(screen.getByText('Extra Scopes (comma-separated)')).toBeInTheDocument();
  });

  test('typing into the role-name field updates its value', async () => {
    render(<ScopesPanel />);
    await userEvent.click(screen.getByRole('button', { name: /Create Role/i }));
    const nameInput = screen.getByPlaceholderText('data-entry') as HTMLInputElement;
    await userEvent.type(nameInput, 'data-entry-clerk');
    expect(nameInput.value).toBe('data-entry-clerk');
  });

  test('clicking Create Role again collapses the form', async () => {
    render(<ScopesPanel />);
    const toggle = screen.getByRole('button', { name: /Create Role/i });
    await userEvent.click(toggle);
    expect(screen.getByText('Role Name')).toBeInTheDocument();
    await userEvent.click(toggle);
    expect(screen.queryByText('Role Name')).not.toBeInTheDocument();
  });
});
