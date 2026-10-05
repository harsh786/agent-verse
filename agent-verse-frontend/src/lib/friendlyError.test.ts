import { describe, expect, test } from 'vitest';
import { ApiError } from '@/lib/api/client';
import { friendlyConnectionError, sanitizeErrorDetail } from './friendlyError';

const TOPOLOGY_REFUSED =
  "127.0.0.1:10: [Errno 61] Connection refused (configured timeouts: socketTimeoutMS: 20000.0ms, connectTimeoutMS: 20000.0ms), " +
  "Timeout: 5.0s, Topology Description: <TopologyDescription id: 66f1c0ffee, topology_type: Unknown, servers: " +
  "[<ServerDescription ('127.0.0.1', 10) server_type: Unknown, rtt: None, error=AutoReconnect('127.0.0.1:10: [Errno 61] Connection refused')>]>";

describe('friendlyConnectionError (mongo re-audit A9/B6)', () => {
  test.each([
    [TOPOLOGY_REFUSED, /refused the connection/i],
    ["Authentication failed., full error: {'ok': 0.0, 'errmsg': 'Authentication failed.', 'code': 18, 'codeName': 'AuthenticationFailed'}", /authentication failed/i],
    ['Credentials were rejected by the connector endpoint', /credentials were rejected/i],
    ['cluster0-shard-00-00.abcd.mongodb.net:27017: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed (_ssl.c:1006)', /tls/i],
    ['SSRF protection: disallowed URL', /blocked/i],
    ['Connector URL rejected by SSRF guard: 10.0.0.5 is a private address', /blocked/i],
    ['The DNS query name does not exist: _mongodb._tcp.cluster0.example.net.', /could not be resolved/i],
    ['db.example.com:27017: timed out (configured timeouts: connectTimeoutMS: 5000.0ms), Timeout: 5.0s', /timed out/i],
    ['MongoDB connector has no connection URI configured. Configure credentials on the connector', /no connection uri/i],
    ['MongoDB connection URI must start with mongodb:// or mongodb+srv://', /must start with mongodb/i],
    ["MongoDB auth mechanism 'GSSAPI' is not allowed; use SCRAM or PLAIN", /mechanism is not allowed/i],
    ['MONGODB-X509 authentication needs tls_client_cert and its private key', /client certificate/i],
    ['Connector endpoint returned HTTP 404', /HTTP 404/],
    ['tls_ca_pem must be a PEM-encoded certificate bundle', /PEM-encoded/],
  ])('%s', (raw, expected) => {
    const f = friendlyConnectionError(raw);
    expect(f.message).toMatch(expected);
  });

  test('details never carry hosts or credentials', () => {
    const f = friendlyConnectionError(TOPOLOGY_REFUSED);
    expect(f.detail).toBeTruthy();
    expect(f.detail).not.toContain('127.0.0.1');
    expect(f.detail).toContain('Errno 61');
    const tls = friendlyConnectionError('cluster0-shard-00-00.abcd.mongodb.net:27017: [SSL: CERTIFICATE_VERIFY_FAILED]');
    expect(`${tls.message} ${tls.detail}`).not.toContain('mongodb.net');
  });

  test('sanitizeErrorDetail strips URIs, userinfo, IPs, host:port, domains and key=value secrets', () => {
    const out = sanitizeErrorDetail(
      "connect mongodb://alice:S3cretPw@db.example.com:27017/orders failed; seed [::1]:27017 and ('mongo-1', 27017) " +
      "and 10.1.2.3 and node-2:27018 and https://api.example.com/x password=hunter2 token: abc123",
    );
    for (const leak of ['alice', 'S3cretPw', 'db.example.com', '::1', 'mongo-1', '10.1.2.3', 'node-2', 'api.example.com', 'hunter2', 'abc123']) {
      expect(out).not.toContain(leak);
    }
    expect(out).toContain('mongodb://');
  });

  test('an unmatched short message is kept (sanitised); a long unmatched one is summarised', () => {
    expect(friendlyConnectionError('A connector named orders-db already exists').message)
      .toBe('A connector named orders-db already exists');
    const long = friendlyConnectionError(`weird driver failure ${'x'.repeat(300)}`);
    expect(long.message).toMatch(/connection failed/i);
    expect(long.detail).toContain('weird driver failure');
  });

  test('accepts ApiError / Error / unknown and uses its message', () => {
    expect(friendlyConnectionError(new ApiError(400, 'SSRF protection: disallowed URL')).message).toMatch(/blocked/i);
    expect(friendlyConnectionError(new Error('Connection refused')).message).toMatch(/refused/i);
    expect(friendlyConnectionError(undefined, 'Test failed').message).toBe('Test failed');
    expect(friendlyConnectionError(undefined, 'Test failed').detail).toBeNull();
  });
});

describe('backend error ids (fix/mongo-mcp: "... (error id <12 hex>)")', () => {
  test('a classified backend message is kept as-is with its error id', () => {
    const raw = 'Could not reach the MongoDB server (connection refused or timed out, DNS, firewall, or no primary available) (error id 3f9a0c12be47)';
    const f = friendlyConnectionError(raw);
    expect(f.message).toBe(raw);
    expect(f.message).toContain('(error id 3f9a0c12be47)');
    expect(f.detail).toBeNull();
  });

  test('the error id survives inside an ApiError and a "Test failed" prefix context', () => {
    const f = friendlyConnectionError(new Error('Authentication failed: check the username, password and auth source (error id 0123456789ab)'));
    expect(f.message).toMatch(/authentication failed/i);
    expect(f.message).toContain('error id 0123456789ab');
  });

  test('a raw driver dump that carries an error id is still summarised, id appended', () => {
    const f = friendlyConnectionError('10.0.0.5:27017: [Errno 61] Connection refused, Topology Description: <x> (error id abcdefabcdef)');
    expect(f.message).toMatch(/refused the connection/i);
    expect(f.message).toContain('(error id abcdefabcdef)');
    expect(f.message).not.toContain('10.0.0.5');
  });
});
