export interface SseFrame<T> {
  id?: string;
  event?: string;
  data: T;
}

export async function* parseSseStream<T>(
  response: Response,
  options: { signal?: AbortSignal; strict?: boolean } = {},
): AsyncGenerator<SseFrame<T>> {
  if (!response.ok || !response.body) throw new Error(`SSE stream failed: ${response.status}`);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  while (true) {
    if (options.signal?.aborted) {
      await reader.cancel();
      throw options.signal.reason ?? new DOMException('Aborted', 'AbortError');
    }
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split('\n\n');
    buffer = frames.pop() ?? '';
    for (const raw of frames) {
      let id: string | undefined;
      let event: string | undefined;
      const data: string[] = [];
      for (const line of raw.split('\n')) {
        if (line.startsWith('id:')) id = line.slice(3).trim();
        else if (line.startsWith('event:')) event = line.slice(6).trim();
        else if (line.startsWith('data:')) data.push(line.slice(5).trimStart());
      }
      if (!data.length || data.join('\n') === '[DONE]') continue;
      try {
        yield { id, event, data: JSON.parse(data.join('\n')) as T };
      } catch {
        if (options.strict) throw new Error('Malformed SSE data frame');
      }
    }
  }
}
