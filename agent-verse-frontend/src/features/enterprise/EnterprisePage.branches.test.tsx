/**
 * Branch companion for EnterprisePage.
 *
 * EnterprisePage.test.tsx covers Export/Residency/Delete happy paths. This file
 * covers the largely UNTESTED sections: ComplianceDashboard, SAMLWizard,
 * ScimSection, ContractsSection — plus deeper branches of ResidencySection,
 * ExportSection, and DeleteSection (loading/error/onError/edge-case branches).
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { toast } from '@/stores/toast';
import { EnterprisePage } from './EnterprisePage';

vi.mock('@/stores/toast', () => ({ toast: vi.fn() }));

function renderEnterprisePage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <EnterprisePage />
    </QueryClientProvider>
  );
}

/** Default residency fetch response used by most tests (also backs ComplianceDashboard,
 *  since both share the `['residency']` query key / same `/enterprise/compliance/residency`
 *  endpoint). */
function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('EnterprisePage branches', () => {
  beforeEach(() => {
    localStorage.clear();
    useAuthStore.setState({
      apiKey: 'tenant-key',
      tenantId: 'tenant-1',
      plan: 'enterprise',
      isAuthenticated: true,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  // ── ComplianceDashboard ──────────────────────────────────────────────────

  describe('ComplianceDashboard', () => {
    test('renders skeleton placeholders while residency query is pending', async () => {
      let resolveFetch: (r: Response) => void = () => {};
      vi.spyOn(globalThis, 'fetch').mockImplementation(
        () => new Promise((resolve) => { resolveFetch = resolve; })
      );

      const { container } = renderEnterprisePage();

      expect(screen.getByText('Compliance Status')).toBeInTheDocument();
      const skeletons = container.querySelectorAll('.animate-pulse');
      expect(skeletons.length).toBeGreaterThanOrEqual(6);

      resolveFetch(jsonResponse({ region: 'us-east-1' }));
      await waitFor(() => expect(screen.getByText('GDPR')).toBeInTheDocument());
    });

    test('shows active frameworks (GDPR, SOC2) and inactive ones for the rest', async () => {
      vi.spyOn(globalThis, 'fetch').mockResolvedValue(
        jsonResponse({ region: 'us-east-1', compliance_frameworks: ['GDPR', 'SOC2'] })
      );
      renderEnterprisePage();

      await waitFor(() => expect(screen.getByText('GDPR')).toBeInTheDocument());
      const known = ['GDPR', 'SOC2', 'HIPAA', 'ISO27001', 'PCI-DSS', 'CCPA'];
      for (const fw of known) {
        expect(screen.getByText(fw)).toBeInTheDocument();
      }
      // Active ones render with the green-tinted class, inactive with muted text.
      expect(screen.getByText('GDPR').className).not.toMatch(/text-muted-foreground/);
      expect(screen.getByText('HIPAA').className).toMatch(/text-muted-foreground/);
    });

    test('renders all frameworks as inactive when compliance_frameworks is absent', async () => {
      vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse({ region: 'us-east-1' }));
      renderEnterprisePage();

      await waitFor(() => expect(screen.getByText('GDPR')).toBeInTheDocument());
      const known = ['GDPR', 'SOC2', 'HIPAA', 'ISO27001', 'PCI-DSS', 'CCPA'];
      for (const fw of known) {
        expect(screen.getByText(fw).className).toMatch(/text-muted-foreground/);
      }
    });
  });

  // ── SAMLWizard ────────────────────────────────────────────────────────────

  describe('SAMLWizard', () => {
    function mockResidencyFetch() {
      vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
        const url = String(input);
        if (url.includes('/enterprise/compliance/residency')) {
          return jsonResponse({ region: 'us-east-1' });
        }
        return jsonResponse({ region: 'us-east-1' });
      });
    }

    test('step 0: Continue is disabled until an IdP is chosen, then advances to step 1', async () => {
      mockResidencyFetch();
      renderEnterprisePage();

      expect(screen.getByText('Select your identity provider')).toBeInTheDocument();
      for (const idp of ['Okta', 'Azure AD', 'Google Workspace', 'OneLogin', 'PingIdentity']) {
        expect(screen.getByRole('button', { name: idp })).toBeInTheDocument();
      }

      const continueBtn = screen.getByRole('button', { name: /continue/i });
      expect(continueBtn).toBeDisabled();

      await userEvent.click(screen.getByRole('button', { name: 'Okta' }));
      expect(continueBtn).not.toBeDisabled();
      expect(screen.getByRole('button', { name: 'Okta' }).className).toMatch(/border-\[#00D4FF\]/);

      await userEvent.click(continueBtn);
      expect(screen.getByText(/Upload IdP SAML metadata XML/)).toBeInTheDocument();
    });

    test('step 1: Continue disabled until ssoUrl/entityId typed; Back returns to step 0', async () => {
      mockResidencyFetch();
      renderEnterprisePage();

      await userEvent.click(screen.getByRole('button', { name: 'Okta' }));
      await userEvent.click(screen.getByRole('button', { name: /continue/i }));

      expect(screen.getByText('Drag & drop metadata XML or click to browse')).toBeInTheDocument();
      const continueBtn = screen.getByRole('button', { name: /continue/i });
      expect(continueBtn).toBeDisabled();

      await userEvent.type(screen.getByPlaceholderText('https://idp.example.com/saml/sso'), 'https://idp.example.com/saml/sso');
      expect(continueBtn).not.toBeDisabled();

      await userEvent.click(screen.getByRole('button', { name: /back/i }));
      expect(screen.getByText('Select your identity provider')).toBeInTheDocument();
    });

    test('step 1: typing Entity ID also enables Continue and advances to step 2', async () => {
      mockResidencyFetch();
      renderEnterprisePage();

      await userEvent.click(screen.getByRole('button', { name: 'Azure AD' }));
      await userEvent.click(screen.getByRole('button', { name: /continue/i }));

      await userEvent.type(screen.getByPlaceholderText('https://idp.example.com'), 'https://idp.example.com');
      const continueBtn = screen.getByRole('button', { name: /continue/i });
      expect(continueBtn).not.toBeDisabled();

      await userEvent.click(continueBtn);
      expect(screen.getByText('Map SAML attributes to user fields')).toBeInTheDocument();
    });

    test('step 1: uploading a metadata file via the hidden input shows its name and fills fields', async () => {
      mockResidencyFetch();
      renderEnterprisePage();

      await userEvent.click(screen.getByRole('button', { name: 'Okta' }));
      await userEvent.click(screen.getByRole('button', { name: /continue/i }));

      const xml = '<xml><EntityDescriptor entityID="https://idp.example.com"><SingleSignOnService Binding="x" Location="https://idp.example.com/sso"/></EntityDescriptor></xml>';
      const file = new File([xml], 'meta.xml', { type: 'text/xml' });
      const fileInput = document.getElementById('saml-meta-input') as HTMLInputElement;
      await userEvent.upload(fileInput, file);

      await waitFor(() => expect(screen.getByText('meta.xml')).toBeInTheDocument());
      await waitFor(() =>
        expect(screen.getByPlaceholderText('https://idp.example.com/saml/sso')).toHaveValue('https://idp.example.com/sso')
      );
      expect(screen.getByPlaceholderText('https://idp.example.com')).toHaveValue('https://idp.example.com');
    });

    test('step 2: default attribute values show, typing changes them, Continue always enabled, Back returns to step 1', async () => {
      mockResidencyFetch();
      renderEnterprisePage();

      await userEvent.click(screen.getByRole('button', { name: 'Okta' }));
      await userEvent.click(screen.getByRole('button', { name: /continue/i }));
      await userEvent.type(screen.getByPlaceholderText('https://idp.example.com/saml/sso'), 'https://idp.example.com/saml/sso');
      await userEvent.click(screen.getByRole('button', { name: /continue/i }));

      expect(screen.getByText('Map SAML attributes to user fields')).toBeInTheDocument();
      const emailInput = screen.getByDisplayValue('email');
      const nameInput = screen.getByDisplayValue('displayName');
      expect(emailInput).toBeInTheDocument();
      expect(nameInput).toBeInTheDocument();

      await userEvent.clear(emailInput);
      await userEvent.type(emailInput, 'mail');
      expect(emailInput).toHaveValue('mail');

      const continueBtn = screen.getByRole('button', { name: /continue/i });
      expect(continueBtn).not.toBeDisabled();

      await userEvent.click(screen.getByRole('button', { name: /back/i }));
      expect(screen.getByText('Drag & drop metadata XML or click to browse')).toBeInTheDocument();

      // go forward again to reach step 3 for the next describe blocks
      await userEvent.click(screen.getByRole('button', { name: /continue/i }));
      await userEvent.click(screen.getByRole('button', { name: /continue/i }));
      expect(screen.getByText('Verify the SSO flow end-to-end')).toBeInTheDocument();
    });

    async function advanceToStep3() {
      await userEvent.click(screen.getByRole('button', { name: 'Okta' }));
      await userEvent.click(screen.getByRole('button', { name: /continue/i }));
      await userEvent.type(screen.getByPlaceholderText('https://idp.example.com/saml/sso'), 'https://idp.example.com/saml/sso');
      await userEvent.click(screen.getByRole('button', { name: /continue/i }));
      await userEvent.click(screen.getByRole('button', { name: /continue/i }));
    }

    test('step 3: successful test connection shows success and Save button; Save toasts success', async () => {
      vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
        const url = String(input);
        if (url.includes('/enterprise/saml/test')) {
          return jsonResponse({ success: true, latency_ms: 42 });
        }
        return jsonResponse({ region: 'us-east-1' });
      });
      renderEnterprisePage();
      await advanceToStep3();

      const testBtn = screen.getByRole('button', { name: /test sso connection/i });
      await userEvent.click(testBtn);

      await waitFor(() => expect(screen.getByText('Connection successful')).toBeInTheDocument());

      const saveBtn = screen.getByRole('button', { name: /save sso configuration/i });
      await userEvent.click(saveBtn);
      expect(toast).toHaveBeenCalledWith(
        expect.objectContaining({ kind: 'success', message: 'SSO configuration saved' })
      );
    });

    test('step 3: shows "Testing…" while pending', async () => {
      let resolveFetch: (r: Response) => void = () => {};
      vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
        const url = String(input);
        if (url.includes('/enterprise/saml/test')) {
          return new Promise((resolve) => { resolveFetch = resolve; });
        }
        return jsonResponse({ region: 'us-east-1' });
      });
      renderEnterprisePage();
      await advanceToStep3();

      await userEvent.click(screen.getByRole('button', { name: /test sso connection/i }));
      expect(screen.getByRole('button', { name: /testing…/i })).toBeInTheDocument();

      resolveFetch(jsonResponse({ success: true, latency_ms: 10 }));
      await waitFor(() => expect(screen.getByText('Connection successful')).toBeInTheDocument());
    });

    test('step 3: failed test connection (success:false) shows failure and toasts an error', async () => {
      vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
        const url = String(input);
        if (url.includes('/enterprise/saml/test')) {
          return jsonResponse({ success: false, message: 'bad cert' });
        }
        return jsonResponse({ region: 'us-east-1' });
      });
      renderEnterprisePage();
      await advanceToStep3();

      await userEvent.click(screen.getByRole('button', { name: /test sso connection/i }));

      await waitFor(() => expect(screen.getByText('Connection failed')).toBeInTheDocument());
      expect(toast).toHaveBeenCalledWith(
        expect.objectContaining({ kind: 'error', message: expect.stringContaining('bad cert') })
      );
      expect(screen.queryByRole('button', { name: /save sso configuration/i })).not.toBeInTheDocument();
    });

    test('step 3: network rejection on test connection hits onError branch', async () => {
      vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
        const url = String(input);
        if (url.includes('/enterprise/saml/test')) {
          throw new Error('network down');
        }
        return jsonResponse({ region: 'us-east-1' });
      });
      renderEnterprisePage();
      await advanceToStep3();

      await userEvent.click(screen.getByRole('button', { name: /test sso connection/i }));

      await waitFor(() => expect(screen.getByText('Connection failed')).toBeInTheDocument());
      expect(toast).toHaveBeenCalledWith(
        expect.objectContaining({ kind: 'error', message: expect.stringContaining('SAML test failed') })
      );
    });

    test('step indicators: clicking an earlier step jumps back; clicking a later step does nothing', async () => {
      mockResidencyFetch();
      const { container } = renderEnterprisePage();
      await advanceToStep3();

      expect(screen.getByText('Verify the SSO flow end-to-end')).toBeInTheDocument();

      // The 4 step-indicator buttons live in the "flex items-center gap-1 mb-6" bar;
      // their accessible name is "<digit><label>" (e.g. "1Choose IdP").
      const getStepButtons = () =>
        Array.from(container.querySelectorAll('button')).filter((b) => /^[1-4]/.test(b.textContent ?? ''));

      // Step-0 indicator ("1") should navigate back since 0 <= 2 (current step).
      await userEvent.click(getStepButtons()[0]);
      expect(screen.getByText('Select your identity provider')).toBeInTheDocument();

      // Advance back to step 2 (index 2) again.
      await userEvent.click(screen.getByRole('button', { name: 'Okta' }));
      await userEvent.click(screen.getByRole('button', { name: /continue/i }));
      await userEvent.click(screen.getByRole('button', { name: /continue/i }));
      expect(screen.getByText('Map SAML attributes to user fields')).toBeInTheDocument();

      // Step-3 indicator ("4") is ahead of current step (2) — clicking it must not navigate.
      await userEvent.click(getStepButtons()[3]);
      expect(screen.getByText('Map SAML attributes to user fields')).toBeInTheDocument();
    });
  });

  // ── ScimSection ───────────────────────────────────────────────────────────

  describe('ScimSection', () => {
    test('toggles Enable -> Enabled (shows endpoint info, toasts info) and back', async () => {
      vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse({ region: 'us-east-1' }));
      renderEnterprisePage();

      const toggleBtn = screen.getByRole('button', { name: /^enable$/i });
      expect(toggleBtn).toBeInTheDocument();
      expect(screen.queryByText(/scim endpoint/i)).not.toBeInTheDocument();

      await userEvent.click(toggleBtn);
      expect(screen.getByRole('button', { name: /^enabled$/i })).toBeInTheDocument();
      expect(screen.getByText(/scim endpoint/i)).toBeInTheDocument();
      expect(screen.getByText('https://api.agentverse.io/scim/v2')).toBeInTheDocument();
      expect(toast).toHaveBeenCalledWith(
        expect.objectContaining({ kind: 'info', message: expect.stringContaining('enabled') })
      );

      await userEvent.click(screen.getByRole('button', { name: /^enabled$/i }));
      expect(screen.getByRole('button', { name: /^enable$/i })).toBeInTheDocument();
      expect(screen.queryByText(/scim endpoint/i)).not.toBeInTheDocument();
      expect(toast).toHaveBeenCalledWith(
        expect.objectContaining({ kind: 'info', message: expect.stringContaining('disabled') })
      );
    });
  });

  // ── ContractsSection ──────────────────────────────────────────────────────

  describe('ContractsSection', () => {
    test('renders BAA/DPA as signed with dates, SLA as pending without a date', async () => {
      vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse({ region: 'us-east-1' }));
      renderEnterprisePage();

      expect(screen.getByText('Business Associate Agreement (BAA)')).toBeInTheDocument();
      expect(screen.getByText('Data Processing Agreement (DPA)')).toBeInTheDocument();
      expect(screen.getByText('Service Level Agreement (SLA)')).toBeInTheDocument();

      const signedBadges = screen.getAllByText('signed');
      expect(signedBadges).toHaveLength(2);
      expect(screen.getByText('pending')).toBeInTheDocument();

      const signedDates = screen.getAllByText('Signed 2024-01-15');
      expect(signedDates).toHaveLength(2);
    });
  });

  // ── ResidencySection additional branches ─────────────────────────────────

  describe('ResidencySection', () => {
    test('shows a skeleton while loading', async () => {
      let resolveFetch: (r: Response) => void = () => {};
      vi.spyOn(globalThis, 'fetch').mockImplementation(
        () => new Promise((resolve) => { resolveFetch = resolve; })
      );
      const { container } = renderEnterprisePage();

      expect(screen.getByText('Data Residency')).toBeInTheDocument();
      expect(container.querySelector('.animate-pulse.h-20')).toBeInTheDocument();

      resolveFetch(jsonResponse({ region: 'us-east-1' }));
      await waitFor(() => expect(screen.getByText('us-east-1')).toBeInTheDocument());
    });

    test('shows an error message when the residency fetch fails', async () => {
      vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse({ error: { message: 'boom' } }, 500));
      renderEnterprisePage();

      await waitFor(() =>
        expect(screen.getByText('Failed to load residency info.')).toBeInTheDocument()
      );
    });

    test('shows "—" when data_center is missing', async () => {
      vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse({ region: 'us-east-1' }));
      renderEnterprisePage();

      await waitFor(() => expect(screen.getByText('us-east-1')).toBeInTheDocument());
      expect(screen.getByText('—')).toBeInTheDocument();
    });

    test('shows the description paragraph when present', async () => {
      vi.spyOn(globalThis, 'fetch').mockResolvedValue(
        jsonResponse({ region: 'us-east-1', description: 'Hosted in a SOC2 certified facility.' })
      );
      renderEnterprisePage();

      await waitFor(() =>
        expect(screen.getByText('Hosted in a SOC2 certified facility.')).toBeInTheDocument()
      );
    });

    test('omits the description paragraph when absent', async () => {
      vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse({ region: 'us-east-1' }));
      renderEnterprisePage();

      await waitFor(() => expect(screen.getByText('us-east-1')).toBeInTheDocument());
      expect(screen.queryByText(/certified facility/)).not.toBeInTheDocument();
    });
  });

  // ── ExportSection additional branches ────────────────────────────────────

  describe('ExportSection', () => {
    test('shows "Exporting…" while the export mutation is pending', async () => {
      let resolveFetch: (r: Response) => void = () => {};
      vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
        const url = String(input);
        if (url.includes('/enterprise/compliance/export')) {
          return new Promise((resolve) => { resolveFetch = resolve; });
        }
        return jsonResponse({ region: 'us-east-1' });
      });
      renderEnterprisePage();

      await userEvent.click(screen.getByRole('button', { name: /^export$/i }));
      expect(screen.getByRole('button', { name: /exporting…/i })).toBeInTheDocument();

      resolveFetch(jsonResponse({ message: 'done' }));
      await waitFor(() => expect(screen.getByText('done')).toBeInTheDocument());
    });

    test('toasts an error when the export request fails', async () => {
      vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
        const url = String(input);
        if (url.includes('/enterprise/compliance/export')) {
          return jsonResponse({ error: { message: 'export blew up' } }, 400);
        }
        return jsonResponse({ region: 'us-east-1' });
      });
      renderEnterprisePage();

      await userEvent.click(screen.getByRole('button', { name: /^export$/i }));

      await waitFor(() =>
        expect(toast).toHaveBeenCalledWith(
          expect.objectContaining({ kind: 'error', message: expect.stringContaining('export blew up') })
        )
      );
    });

    test('omits the Size line when size_bytes is absent but download_url is present', async () => {
      vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
        const url = String(input);
        if (url.includes('/enterprise/compliance/export')) {
          return jsonResponse({ download_url: 'https://example.com/export.zip' });
        }
        return jsonResponse({ region: 'us-east-1' });
      });
      renderEnterprisePage();

      await userEvent.click(screen.getByRole('button', { name: /^export$/i }));
      await waitFor(() => expect(screen.getByText('Export ready')).toBeInTheDocument());
      expect(screen.queryByText(/^Size:/)).not.toBeInTheDocument();
    });
  });

  // ── DeleteSection additional ──────────────────────────────────────────────

  describe('DeleteSection', () => {
    test('Cancel clears the confirm text and hides the form; reopening shows an empty input', async () => {
      vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse({ region: 'us-east-1' }));
      renderEnterprisePage();

      await userEvent.click(screen.getByRole('button', { name: /^delete$/i }));
      await userEvent.type(screen.getByPlaceholderText('DELETE MY DATA'), 'partial text');
      await userEvent.click(screen.getByRole('button', { name: /cancel/i }));

      expect(screen.queryByPlaceholderText('DELETE MY DATA')).not.toBeInTheDocument();

      await userEvent.click(screen.getByRole('button', { name: /^delete$/i }));
      expect(screen.getByPlaceholderText('DELETE MY DATA')).toHaveValue('');
    });

    test('toasts an error on a failed delete and keeps the confirm form visible', async () => {
      vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
        const url = String(input);
        if (url.includes('/enterprise/compliance/delete')) {
          return jsonResponse({ error: { message: 'delete blew up' } }, 400);
        }
        return jsonResponse({ region: 'us-east-1' });
      });
      renderEnterprisePage();

      await userEvent.click(screen.getByRole('button', { name: /^delete$/i }));
      await userEvent.type(screen.getByPlaceholderText('DELETE MY DATA'), 'DELETE MY DATA');
      await userEvent.click(screen.getByRole('button', { name: /confirm delete/i }));

      await waitFor(() =>
        expect(toast).toHaveBeenCalledWith(
          expect.objectContaining({ kind: 'error', message: expect.stringContaining('delete blew up') })
        )
      );
      expect(screen.getByPlaceholderText('DELETE MY DATA')).toBeInTheDocument();
      expect(screen.queryByText(/data deletion scheduled/i)).not.toBeInTheDocument();
    });
  });
});
