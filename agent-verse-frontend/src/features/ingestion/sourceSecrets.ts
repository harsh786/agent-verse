import { isMaskedSecret } from '@/lib/connectors';

/**
 * The connection_config to PATCH from an edited draft of a masked config.
 *
 * GET /sources returns secrets as "********" and PATCH treats that mask as
 * "unchanged" (app/ingestion/source_secrets.merge_masked_update). The secret
 * inputs show a masked value as empty, so a secret the user left empty — or
 * cleared — is sent back as its original mask; a typed value replaces it.
 */
export function restoreMaskedSecrets(
  draft: Record<string, unknown>,
  original: Record<string, unknown>,
): Record<string, unknown> {
  const out = { ...draft };
  for (const [key, value] of Object.entries(original)) {
    if (typeof value !== 'string' || !isMaskedSecret(value)) continue;
    const edited = out[key];
    if (edited === undefined || edited === null || edited === '') out[key] = value;
  }
  return out;
}
