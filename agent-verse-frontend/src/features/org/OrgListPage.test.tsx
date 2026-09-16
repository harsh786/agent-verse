import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import React, { type ReactNode } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { OrgListPage } from './OrgListPage';

// AnimatePresence's exit animation otherwise leaves the create-form input in
// the DOM after "unmount" in jsdom (no real rAF/animation completion), so
// stub framer-motion the same way sibling org feature tests do.
vi.mock('framer-motion', async (importOriginal) => {
  const actual = await importOriginal<typeof import('framer-motion')>();
  const stubCache = new Map<string, (props: { children?: ReactNode; [k: string]: unknown }) => React.ReactElement>();
  const makeStub = (tag: string) => {
    let stub = stubCache.get(tag);
    if (!stub) {
      stub = ({ children, ...props }) => React.createElement(tag, props as Record<string, unknown>, children);
      stubCache.set(tag, stub);
    }
    return stub;
  };
  return {
    ...actual,
    useReducedMotion: () => true,
    AnimatePresence: ({ children }: { children?: ReactNode }) => <>{children}</>,
    motion: new Proxy(actual.motion as unknown as Record<string, unknown>, {
      get: (target, key: string) => (key in target ? target[key] : makeStub(key)),
    }),
  };
});

const navigateMock = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useNavigate: () => navigateMock };
});

const useOrganizationsMock = vi.fn();
const mutateAsyncMock = vi.fn();
vi.mock('./hooks/useOrg', () => ({
  useOrganizations: () => useOrganizationsMock(),
  useCreateOrganization: () => ({ mutateAsync: mutateAsyncMock, isPending: false }),
}));

function renderPage() {
  return render(<OrgListPage />);
}

beforeEach(() => {
  navigateMock.mockReset();
  mutateAsyncMock.mockReset();
  useOrganizationsMock.mockReset();
});

describe('OrgListPage', () => {
  it('shows a loading state', () => {
    useOrganizationsMock.mockReturnValue({ data: undefined, isLoading: true });
    renderPage();
    expect(screen.getByText('Loading…')).toBeInTheDocument();
  });

  it('shows the empty state when there are no organizations', () => {
    useOrganizationsMock.mockReturnValue({ data: { data: [] }, isLoading: false });
    renderPage();
    expect(screen.getByText('No organizations yet')).toBeInTheDocument();
    expect(screen.getByText('0 organizations')).toBeInTheDocument();
  });

  it('opens the create form from the empty state Create Organization button', async () => {
    useOrganizationsMock.mockReturnValue({ data: { data: [] }, isLoading: false });
    renderPage();
    await userEvent.click(screen.getByRole('button', { name: /create your first organization/i }));
    expect(screen.getByLabelText('Organization name')).toBeInTheDocument();
  });

  it('renders a singular organization count correctly', () => {
    useOrganizationsMock.mockReturnValue({
      data: { data: [{ id: '1', name: 'Solo Org', status: 'active' }] },
      isLoading: false,
    });
    renderPage();
    expect(screen.getByText('1 organization')).toBeInTheDocument();
  });

  it('lists organizations with name, status and industry', () => {
    useOrganizationsMock.mockReturnValue({
      data: {
        data: [
          { id: 'org-1', name: 'Acme Ops', status: 'active', industry: 'Manufacturing' },
          { id: 'org-2', name: 'Paused Co', status: 'paused' },
          { id: 'org-3', name: 'Old Co', status: 'archived' },
        ],
      },
      isLoading: false,
    });
    renderPage();
    expect(screen.getByText('3 organizations')).toBeInTheDocument();
    expect(screen.getByText('Acme Ops')).toBeInTheDocument();
    expect(screen.getByText('Manufacturing')).toBeInTheDocument();
    expect(screen.getByText('Paused Co')).toBeInTheDocument();
    expect(screen.getByText('Old Co')).toBeInTheDocument();
    // status label text is rendered per-card (capitalize)
    expect(screen.getAllByText(/active|paused|archived/i).length).toBeGreaterThan(0);
  });

  it('navigates to the org detail page when a card is clicked', async () => {
    useOrganizationsMock.mockReturnValue({
      data: { data: [{ id: 'org-42', name: 'Clickable Org', status: 'active' }] },
      isLoading: false,
    });
    renderPage();
    await userEvent.click(screen.getByRole('button', { name: /open clickable org organization/i }));
    expect(navigateMock).toHaveBeenCalledWith('/org/org-42');
  });

  it('opens the create form from the header button and cancels it', async () => {
    useOrganizationsMock.mockReturnValue({ data: { data: [] }, isLoading: false });
    renderPage();
    await userEvent.click(screen.getByRole('button', { name: /create new organization/i }));
    const nameInput = screen.getByLabelText('Organization name');
    await userEvent.type(nameInput, 'Draft Name');
    await userEvent.click(screen.getByRole('button', { name: /^cancel$/i }));
    await waitFor(() => expect(screen.queryByLabelText('Organization name')).not.toBeInTheDocument());
  });

  it('disables Create submit button until a name is entered', async () => {
    useOrganizationsMock.mockReturnValue({ data: { data: [] }, isLoading: false });
    renderPage();
    await userEvent.click(screen.getByRole('button', { name: /create new organization/i }));
    expect(screen.getByRole('button', { name: /^create$/i })).toBeDisabled();
    await userEvent.type(screen.getByLabelText('Organization name'), 'New Org');
    expect(screen.getByRole('button', { name: /^create$/i })).not.toBeDisabled();
  });

  it('submits the create form, then navigates to the new org and resets the form', async () => {
    useOrganizationsMock.mockReturnValue({ data: { data: [] }, isLoading: false });
    mutateAsyncMock.mockResolvedValue({ id: 'org-new', name: 'New Org' });
    renderPage();

    await userEvent.click(screen.getByRole('button', { name: /create new organization/i }));
    await userEvent.type(screen.getByLabelText('Organization name'), 'New Org');
    await userEvent.click(screen.getByRole('button', { name: /^create$/i }));

    await waitFor(() => expect(mutateAsyncMock).toHaveBeenCalledWith({ name: 'New Org' }));
    await waitFor(() => expect(navigateMock).toHaveBeenCalledWith('/org/org-new'));
    await waitFor(() => expect(screen.queryByLabelText('Organization name')).not.toBeInTheDocument());
  });

  it('does not submit when the trimmed name is empty', async () => {
    useOrganizationsMock.mockReturnValue({ data: { data: [] }, isLoading: false });
    renderPage();
    await userEvent.click(screen.getByRole('button', { name: /create new organization/i }));
    await userEvent.type(screen.getByLabelText('Organization name'), '   ');
    // Submitting via Enter on the form (button disabled, so submit the form directly)
    const form = screen.getByLabelText('Organization name').closest('form')!;
    form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    expect(mutateAsyncMock).not.toHaveBeenCalled();
  });

  it('handles missing data gracefully (undefined orgs list)', () => {
    useOrganizationsMock.mockReturnValue({ data: undefined, isLoading: false });
    renderPage();
    expect(screen.getByText('No organizations yet')).toBeInTheDocument();
  });
});
