/**
 * ChannelBindings — TRG-42: tenant-managed messaging gateway bindings.
 *
 * A binding routes a Telegram bot / WhatsApp number / Slack workspace / Teams
 * organisation / generic webhook into this tenant. The server proves ownership
 * (bot token checked against the platform) before binding, stores secrets
 * encrypted, and returns a generated secret only once.
 */
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiRequest } from '@/lib/api/client';

export interface GatewayBinding {
  id: string;
  channel: string;
  addressee: string;
  status: string;
  routable?: boolean;
  has_secret: boolean;
  has_outbound_token: boolean;
  app_id?: string;
  webhook_url: string;
  secret?: string;
}

const CHANNELS = ['telegram', 'whatsapp', 'slack', 'teams', 'webhook'] as const;
type BindingChannel = (typeof CHANNELS)[number];

const ADDRESSEE_LABEL: Record<BindingChannel, string> = {
  telegram: 'Bot id',
  whatsapp: 'Phone number id',
  slack: 'Workspace team id',
  teams: 'Microsoft 365 tenant id',
  webhook: '',
};

const SECRET_LABEL: Record<BindingChannel, string> = {
  telegram: 'Webhook secret_token (blank = generate)',
  whatsapp: 'App secret',
  slack: 'Signing secret',
  teams: '',
  webhook: 'HMAC key (blank = generate)',
};

export function ChannelBindings() {
  const qc = useQueryClient();
  const [channel, setChannel] = useState<BindingChannel>('telegram');
  const [addressee, setAddressee] = useState('');
  const [secret, setSecret] = useState('');
  const [token, setToken] = useState('');
  const [appId, setAppId] = useState('');
  const [created, setCreated] = useState<GatewayBinding | null>(null);

  const { data: bindings = [], isError, error } = useQuery<GatewayBinding[]>({
    queryKey: ['gateway-bindings'],
    queryFn: async () => {
      const data = await apiRequest<unknown>('GET', '/channels/bindings');
      if (!Array.isArray(data)) throw new Error('Unexpected response from the server.');
      return data as GatewayBinding[];
    },
    retry: false,
  });

  const create = useMutation({
    mutationFn: () =>
      apiRequest<GatewayBinding>('POST', '/channels/bindings', {
        channel,
        addressee,
        secret,
        outbound_token: token,
        app_id: appId,
      }),
    onSuccess: (b) => {
      setCreated(b);
      setAddressee('');
      setSecret('');
      setToken('');
      setAppId('');
      qc.invalidateQueries({ queryKey: ['gateway-bindings'] });
    },
  });

  const remove = useMutation({
    mutationFn: (id: string) => apiRequest<{ deleted: boolean }>('DELETE', `/channels/bindings/${encodeURIComponent(id)}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['gateway-bindings'] }),
  });

  return (
    <section aria-label="Channel bindings" className="bg-[#1A1F2E] border border-[#2D3748] rounded-xl p-5 mb-8">
      <h2 className="text-[12px] font-semibold text-[#64748B] uppercase tracking-wider mb-4">Channel bindings</h2>

      <form
        className="grid grid-cols-1 sm:grid-cols-2 gap-2 mb-4 text-[13px]"
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate();
        }}
      >
        <label className="flex flex-col gap-1">
          <span className="text-[#94A3B8]">Channel</span>
          <select
            aria-label="Binding channel"
            value={channel}
            onChange={(e) => setChannel(e.target.value as BindingChannel)}
            className="bg-[#252B3B] rounded px-2 py-1.5 text-[#F1F5F9]"
          >
            {CHANNELS.map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>
        </label>
        {channel !== 'webhook' && (
          <label className="flex flex-col gap-1">
            <span className="text-[#94A3B8]">{ADDRESSEE_LABEL[channel]}</span>
            <input aria-label="Addressee" value={addressee} onChange={(e) => setAddressee(e.target.value)}
              className="bg-[#252B3B] rounded px-2 py-1.5 text-[#F1F5F9]" />
          </label>
        )}
        {channel !== 'teams' && (
          <label className="flex flex-col gap-1">
            <span className="text-[#94A3B8]">{SECRET_LABEL[channel]}</span>
            <input aria-label="Inbound secret" type="password" value={secret} onChange={(e) => setSecret(e.target.value)}
              className="bg-[#252B3B] rounded px-2 py-1.5 text-[#F1F5F9]" />
          </label>
        )}
        {(channel === 'telegram' || channel === 'whatsapp' || channel === 'slack') && (
          <label className="flex flex-col gap-1">
            <span className="text-[#94A3B8]">Bot / access token (proves ownership, sends replies)</span>
            <input aria-label="Outbound token" type="password" value={token} onChange={(e) => setToken(e.target.value)}
              className="bg-[#252B3B] rounded px-2 py-1.5 text-[#F1F5F9]" />
          </label>
        )}
        {channel === 'teams' && (
          <label className="flex flex-col gap-1">
            <span className="text-[#94A3B8]">Bot Framework app id</span>
            <input aria-label="Teams app id" value={appId} onChange={(e) => setAppId(e.target.value)}
              className="bg-[#252B3B] rounded px-2 py-1.5 text-[#F1F5F9]" />
          </label>
        )}
        <button type="submit" disabled={create.isPending}
          className="sm:col-span-2 px-3 py-2 rounded-lg bg-blue-500/10 text-[#00D4FF] hover:bg-blue-500/20 font-medium">
          Add binding
        </button>
      </form>

      {create.isError && (
        <p role="alert" className="text-[12px] text-red-400 mb-3">
          {create.error instanceof Error ? create.error.message : 'Could not add the binding.'}
        </p>
      )}
      {created?.secret && (
        <p role="status" className="text-[12px] text-amber-300 mb-3">
          Save this secret now — it will not be shown again: <code>{created.secret}</code>. Webhook URL:{' '}
          <code>{created.webhook_url}</code>
        </p>
      )}

      {isError ? (
        <p role="alert" className="text-[12px] text-red-400">
          Could not load channel bindings.{error instanceof Error ? ` ${error.message}` : ''}
        </p>
      ) : bindings.length === 0 ? (
        <p className="text-[12px] text-[#64748B]">No channel bindings yet.</p>
      ) : (
        <ul className="divide-y divide-[#252B3B]">
          {bindings.map((b) => (
            <li key={b.id} className="py-2 flex items-center justify-between gap-3 text-[13px]">
              <div className="min-w-0">
                <p className="text-[#F1F5F9]">
                  {b.channel} · <code>{b.addressee}</code>
                </p>
                <p className="text-[11px] font-mono text-[#475569] truncate">{b.webhook_url}</p>
              </div>
              <span className={b.routable === false ? 'text-amber-400 text-[11px]' : 'text-emerald-400 text-[11px]'}>
                {b.status}
              </span>
              <button aria-label={`Remove ${b.channel} binding ${b.addressee}`} onClick={() => remove.mutate(b.id)}
                className="text-[12px] text-red-400 hover:underline">
                Remove
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
