import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, test, vi } from 'vitest';

const mutate = vi.fn();
let isPending = false;

vi.mock('../hooks', () => ({
  useCreateSource: () => ({ mutate, isPending }),
}));

afterEach(() => {
  vi.restoreAllMocks();
  mutate.mockReset();
  isPending = false;
});

async function loadWizard() {
  const { SourceCreateWizard } = await import('./SourceCreateWizard');
  return SourceCreateWizard;
}

/** Drive the wizard from step 1 through to step 3 (configure) for a given family/type. */
async function goToConfigure(familyLabelPattern: RegExp, typePattern: RegExp) {
  const SourceCreateWizard = await loadWizard();
  const onClose = vi.fn();
  const onCreated = vi.fn();
  render(<SourceCreateWizard onClose={onClose} onCreated={onCreated} />);
  await userEvent.click(screen.getByText(familyLabelPattern));
  // AnimatePresence's exit animation delays unmount of the previous step, so
  // the step-2 content appears asynchronously.
  await waitFor(() => expect(screen.getByText(typePattern)).toBeInTheDocument());
  await userEvent.click(screen.getByText(typePattern));
  await waitFor(() => expect(screen.getByText('Source Name *')).toBeInTheDocument());
  return { onClose, onCreated };
}

describe('SourceCreateWizard - step flow', () => {
  test('step 1 shows the family grid with a breadcrumb', async () => {
    const SourceCreateWizard = await loadWizard();
    render(<SourceCreateWizard onClose={vi.fn()} onCreated={vi.fn()} />);
    expect(screen.getByText('Choose a source family')).toBeInTheDocument();
    expect(screen.getByText('Object Storage')).toBeInTheDocument();
    expect(screen.getByText('Communication')).toBeInTheDocument();
  });

  test('selecting a family advances to step 2 (type selection)', async () => {
    const SourceCreateWizard = await loadWizard();
    render(<SourceCreateWizard onClose={vi.fn()} onCreated={vi.fn()} />);
    await userEvent.click(screen.getByText('Object Storage'));
    await waitFor(() => expect(screen.getByText('s3')).toBeInTheDocument());
    expect(screen.getByText('gcs')).toBeInTheDocument();
  });

  test('back button on step 2 returns to step 1', async () => {
    const SourceCreateWizard = await loadWizard();
    render(<SourceCreateWizard onClose={vi.fn()} onCreated={vi.fn()} />);
    await userEvent.click(screen.getByText('Object Storage'));
    await waitFor(() => expect(screen.getByText('s3')).toBeInTheDocument());
    await userEvent.click(screen.getByText('← Back'));
    await waitFor(() => expect(screen.getByText('Choose a source family')).toBeInTheDocument());
  });

  test('selecting a type advances to step 3 (configure) and renders the family form', async () => {
    await goToConfigure(/^Object Storage$/, /^s3$/);
    expect(screen.getByText('Source Name *')).toBeInTheDocument();
    // ObjectStorageForm fields should be present via the FamilyFormRouter
    expect(screen.getByText('Bucket')).toBeInTheDocument();
    expect(screen.getByText('Access Key ID')).toBeInTheDocument();
  });

  test('back button on step 3 returns to step 2', async () => {
    await goToConfigure(/^Object Storage$/, /^s3$/);
    await userEvent.click(screen.getByText('← Back'));
    // 'gcs' only appears in the step-2 type grid, so this uniquely confirms we're back on step 2.
    await waitFor(() => expect(screen.getByText('gcs')).toBeInTheDocument());
  });

  test('routes the database family to DatabaseForm fields', async () => {
    await goToConfigure(/^Relational DB$/, /^postgresql$/);
    expect(screen.getByText('Host')).toBeInTheDocument();
    expect(screen.getByText('CDC Mode')).toBeInTheDocument();
  });

  test('routes the communication family to CommunicationForm fields', async () => {
    await goToConfigure(/^Communication$/, /^slack$/);
    expect(screen.getByText('Bot Token')).toBeInTheDocument();
    expect(screen.getByText('Channel ID')).toBeInTheDocument();
  });

  test('routes an unmapped family (e.g. CRM & ERP) to the GenericSourceForm', async () => {
    await goToConfigure(/^CRM & ERP$/, /^salesforce$/);
    expect(screen.getByText('Endpoint URL')).toBeInTheDocument();
    expect(screen.getByText('Extra Configuration (JSON)')).toBeInTheDocument();
  });

  test('close button (X) calls onClose', async () => {
    const SourceCreateWizard = await loadWizard();
    const onClose = vi.fn();
    render(<SourceCreateWizard onClose={onClose} onCreated={vi.fn()} />);
    await userEvent.click(screen.getByLabelText('Close'));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  test('clicking the dialog backdrop calls onClose', async () => {
    const SourceCreateWizard = await loadWizard();
    const onClose = vi.fn();
    const { container } = render(<SourceCreateWizard onClose={onClose} onCreated={vi.fn()} />);
    const backdrop = container.querySelector('.absolute.inset-0');
    fireEvent.click(backdrop as HTMLElement);
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

describe('SourceCreateWizard - validation gating and submit', () => {
  test('Create Source button is disabled until a source name is entered', async () => {
    await goToConfigure(/^Object Storage$/, /^s3$/);
    const submitBtn = screen.getByRole('button', { name: /create source/i });
    expect(submitBtn).toBeDisabled();
    await userEvent.type(screen.getByPlaceholderText('My s3 source'), 'My Bucket');
    expect(submitBtn).not.toBeDisabled();
  });

  test('whitespace-only source name keeps submit disabled', async () => {
    await goToConfigure(/^Object Storage$/, /^s3$/);
    const submitBtn = screen.getByRole('button', { name: /create source/i });
    await userEvent.type(screen.getByPlaceholderText('My s3 source'), '   ');
    expect(submitBtn).toBeDisabled();
  });

  test('submit is disabled while the mutation is pending', async () => {
    isPending = true;
    await goToConfigure(/^Object Storage$/, /^s3$/);
    await userEvent.type(screen.getByPlaceholderText('My s3 source'), 'My Bucket');
    const submitBtn = screen.getByRole('button', { name: /creating/i });
    expect(submitBtn).toBeDisabled();
  });

  test('submitting calls create.mutate with the assembled payload', async () => {
    await goToConfigure(/^Object Storage$/, /^s3$/);
    await userEvent.type(screen.getByPlaceholderText('My s3 source'), 'My Bucket');
    fireEvent.change(screen.getByPlaceholderText('my-bucket'), { target: { value: 'acme-data' } });
    fireEvent.change(screen.getByPlaceholderText('col-abc123'), { target: { value: 'col-1' } });
    fireEvent.change(screen.getByDisplayValue('Incremental (default)'), { target: { value: 'full' } });

    await userEvent.click(screen.getByRole('button', { name: /create source/i }));

    expect(mutate).toHaveBeenCalledTimes(1);
    const [payload] = mutate.mock.calls[0];
    expect(payload).toMatchObject({
      name: 'My Bucket',
      family: 'object_storage',
      source_type: 's3',
      connection_config: { bucket: 'acme-data' },
      sync_mode: 'full',
      collection_id: 'col-1',
    });
  });

  test('onSuccess callback triggers onCreated and onClose', async () => {
    mutate.mockImplementation((_payload, opts) => {
      opts?.onSuccess?.();
    });
    const { onClose, onCreated } = await goToConfigure(/^Object Storage$/, /^s3$/);
    await userEvent.type(screen.getByPlaceholderText('My s3 source'), 'My Bucket');
    await userEvent.click(screen.getByRole('button', { name: /create source/i }));
    expect(onCreated).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  test('Cancel button in the footer calls onClose without submitting', async () => {
    const { onClose } = await goToConfigure(/^Object Storage$/, /^s3$/);
    await userEvent.click(screen.getByRole('button', { name: /^cancel$/i }));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(mutate).not.toHaveBeenCalled();
  });
});
