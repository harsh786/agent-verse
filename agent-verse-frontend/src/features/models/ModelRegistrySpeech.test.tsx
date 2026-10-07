import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { ModelRegistryPage } from './ModelRegistryPage';
import { PROBE_LABEL } from './modelFormHelpers';

const row = (over: Record<string, unknown>) => ({
  display_name: over.model_id,
  cost_per_1k_input: 0,
  cost_per_1k_output: 0,
  supports_tools: false,
  supports_vision: false,
  supports_structured_output: false,
  quality_score: 0.5,
  is_available: true,
  provider_ready: true,
  servable: true,
  source: 'override',
  base_url: 'http://192.168.1.20:8000/v1',
  ...over,
  key: `${over.provider}/${over.model_id}`,
});

const REGISTRY = {
  total: 1,
  capabilities: [
    {
      capability: 'speech_to_text',
      status: 'ready',
      ready_count: 1,
      selected_model_id: 'whisper-large-v3',
      fallback_model_ids: [],
      order_mode: 'cost',
      preference: [],
      note: 'Transcription uses the first model in this order.',
      models: [row({ provider: 'custom', model_id: 'whisper-large-v3', capabilities: ['speech_to_text'], rank: 1 })],
    },
  ],
};

const TTS_CHECK = {
  probe: 'text_to_speech', capabilities: ['text_to_speech'], ok: true, latency_ms: 88,
  detail: 'synthesized 12044 bytes of audio/wav', error: null, error_kind: null,
  audio_bytes: 12044, audio_content_type: 'audio/wav',
};
const TTS_OK = {
  ok: true, latency_ms: 88, probe: 'text_to_speech', model_listed: null, served_models: [],
  detail: TTS_CHECK.detail, error: null, error_kind: null, checks: [TTS_CHECK],
};

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

function mockFetch(testBody: unknown = TTS_OK) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/models/configured/access'))
      return json({ can_modify: true, via: 'tenant_admin', needs_admin_key: false, reason: '' });
    if (url.includes('/models/configured/test-endpoint')) return json(testBody);
    if (url.includes('/models/catalog')) return json({ providers: [] });
    if (url.includes('/models/configured')) return json(REGISTRY);
    return json({});
  });
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><ModelRegistryPage /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('speech capabilities in the Model Registry', () => {
  test('coverage chips list speech-to-text and text-to-speech with their truth', async () => {
    mockFetch();
    renderPage();
    const stt = await screen.findByTestId('coverage-speech_to_text');
    expect(stt).toHaveAttribute('data-state', 'ready');
    expect(stt).toHaveTextContent('Speech-to-text');
    const tts = screen.getByTestId('coverage-text_to_speech');
    expect(tts).toHaveAttribute('data-state', 'none');
    expect(tts).toHaveTextContent('Text-to-speech');
  });

  test('the speech group lists its models with the order note', async () => {
    mockFetch();
    renderPage();
    expect(await screen.findByText('whisper-large-v3')).toBeInTheDocument();
    const group = screen.getByTestId('capability-speech_to_text');
    expect(within(group).getByText('whisper-large-v3')).toBeInTheDocument();
    expect(within(group).getByText(/Transcription uses the first model/)).toBeInTheDocument();
  });

  test('the add dialog offers both speech capabilities and the TTS test card shows the audio', async () => {
    const spy = mockFetch();
    renderPage();
    await screen.findByText('whisper-large-v3');
    await userEvent.click(screen.getByRole('button', { name: /Add Model/i }));
    const dialog = screen.getByRole('dialog');
    const ttsChip = within(dialog).getByRole('button', { name: 'Text-to-speech' });
    expect(within(dialog).getByRole('button', { name: 'Speech-to-text' })).toBeInTheDocument();
    // Replace the default reasoning capability with text-to-speech.
    await userEvent.click(within(dialog).getByRole('button', { name: 'Reasoning' }));
    await userEvent.click(ttsChip);
    expect(ttsChip).toHaveAttribute('aria-pressed', 'true');

    await userEvent.type(within(dialog).getByLabelText(/Model ID/i), 'kokoro-82m');
    await userEvent.type(within(dialog).getByLabelText(/Endpoint URL/i), 'http://192.168.1.20:8880/v1');
    await userEvent.click(within(dialog).getByRole('button', { name: /Test connection/i }));

    const card = await within(dialog).findByTestId('endpoint-test-result');
    const check = within(card).getByTestId('probe-check-text_to_speech');
    expect(check).toHaveAttribute('data-ok', 'true');
    expect(check).toHaveTextContent('Text-to-speech (audio)');
    expect(check).toHaveTextContent('synthesized 12044 bytes of audio/wav');
    await waitFor(() => {
      const call = spy.mock.calls.find(([u]) => String(u).includes('/models/configured/test-endpoint'));
      expect(JSON.parse((call?.[1] as RequestInit).body as string).capabilities).toEqual(['text_to_speech']);
    });
  });
});

describe('speech probe labels', () => {
  test('both speech probes have a readable label', () => {
    expect(PROBE_LABEL.speech_to_text).toBe('Speech-to-text (audio)');
    expect(PROBE_LABEL.text_to_speech).toBe('Text-to-speech (audio)');
  });
});
