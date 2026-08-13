import ws from 'k6/ws';
import { check } from 'k6';

const base = (__ENV.BASE_URL || 'http://localhost:8000').replace(/^http/, 'ws');
const tenant = __ENV.PROGRAM13_TEST_TENANT;
if (!tenant || !__ENV.API_KEY || !__ENV.SESSION_ID) throw new Error('PROGRAM13_TEST_TENANT, API_KEY, and SESSION_ID are required');
export const options = { thresholds: { checks: ['rate>0.99'], ws_connecting: ['p(95)<100'] } };

export default function () {
  const idempotency = `chat-${tenant}-${__VU}-${__ITER}`;
  const response = ws.connect(`${base}/api/v1/coordination/sessions/${__ENV.SESSION_ID}/group-chat/ws?after_sequence=0`,
    { headers: { 'X-API-Key': __ENV.API_KEY, 'Idempotency-Key': idempotency } },
    (socket) => {
      socket.on('open', () => socket.send(JSON.stringify({ type: 'human', client_message_id: idempotency, content: 'load certification ping', classification: 'internal' })));
      socket.on('message', (data) => { if (String(data).includes('ack')) socket.close(); });
      socket.setTimeout(() => socket.close(), 5000);
    });
  check(response, { 'websocket upgraded': (r) => r && r.status === 101 });
}

// Cleanup uses the disposable PROGRAM13_TEST_TENANT namespace.
