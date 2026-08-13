import { describe, expect, it } from 'vitest';
import { parseSseStream } from '../src/streaming.js';

function response(body: string): Response {
  return new Response(body, { status: 200, headers: { 'Content-Type': 'text/event-stream' } });
}

describe('coordination SSE parser', () => {
  it('parses event IDs and typed data', async () => {
    const stream = parseSseStream<{ sequence: number }>(
      response('id: 8\nevent: message\ndata: {"sequence":8}\n\n'),
      { strict: true },
    );
    const frames = [];
    for await (const frame of stream) frames.push(frame);
    expect(frames).toEqual([{ id: '8', event: 'message', data: { sequence: 8 } }]);
  });

  it('fails closed for malformed coordination data', async () => {
    const consume = async () => {
      for await (const _ of parseSseStream(response('data: nope\n\n'), { strict: true })) {
        // consume
      }
    };
    await expect(consume()).rejects.toThrow('Malformed SSE data frame');
  });
});
