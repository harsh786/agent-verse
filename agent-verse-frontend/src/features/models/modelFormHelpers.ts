/** Model Registry dialog helpers (kept out of the component file for Fast Refresh). */

export const MAX_OUTPUT_DIMENSIONS = 8192;

/** The typed output width: null when empty, NaN when not a valid 1..8192 integer. */
export const parseOutputDimensions = (raw: string): number | null => {
  const t = raw.trim();
  if (!t) return null;
  const n = Number(t);
  return Number.isInteger(n) && n > 0 && n <= MAX_OUTPUT_DIMENSIONS ? n : NaN;
};

/** An embedding model whose id does not look like one (e.g. a chat model picked by mistake). */
export const looksLikeChatModel = (modelId: string) => {
  const id = modelId.trim();
  return !!id && !/embed/i.test(id);
};

export const API_KEY_REJECTED_HINT =
  "The provider rejected the API key (or none was sent). Add a key above, or set the provider's key on the server.";

/** A provider failure caused by a missing / invalid credential (HTTP 401/403, or a 400 about the key). */
export const isApiKeyFailure = (message: string) => {
  const status = Number(/HTTP (\d{3})/.exec(message)?.[1] ?? 0);
  if (status === 401 || status === 403) return true;
  return status === 400 && /api[\s_-]?key/i.test(message);
};
