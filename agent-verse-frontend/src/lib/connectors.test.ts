import { describe, expect, test } from 'vitest';
import {
  connectionSlug, connectorLabel, connectorTypeKey, connectorTypeLabel, qualifiedToolName,
  isDsn, isHttpUrl, isMaskedSecret, maskDsn,
} from './connectors';

describe('connector naming helpers (backend contract)', () => {
  test('label prefers display_name, then name, then the opaque id', () => {
    expect(connectorLabel({ server_id: 'builtin-mongodb:orders-db', name: 'x', display_name: 'orders-db' })).toBe('orders-db');
    expect(connectorLabel({ server_id: 'abc', name: 'GitHub' })).toBe('GitHub');
    expect(connectorLabel({ server_id: 'abc', name: '' })).toBe('abc');
  });

  test('type label uses builtin_type_name, else the canonical id', () => {
    expect(connectorTypeLabel({ builtin_type: 'builtin-mongodb', builtin_type_name: 'MongoDB' })).toBe('MongoDB');
    expect(connectorTypeLabel({ builtin_type: 'builtin-redis', builtin_type_name: '' })).toBe('redis');
    expect(connectorTypeLabel({ builtin_type: null })).toBe('');
  });

  test('type keys normalise ids, names and connection ids alike', () => {
    expect(connectorTypeKey('builtin-google-sheets')).toBe('googlesheets');
    expect(connectorTypeKey('Google Sheets')).toBe('googlesheets');
    expect(connectorTypeKey('builtin-mongodb:orders-db')).toBe('mongodb');
  });

  test('qualified tool names match app/mcp/tool_naming.py', () => {
    expect(connectionSlug('Orders DB')).toBe('orders_db');
    expect(connectionSlug('orders-db')).toBe('orders_db');
    expect(connectionSlug('***')).toBe('connection');
    expect(qualifiedToolName('orders-db', 'mongodb_find')).toBe('orders_db__mongodb_find');
    const long = qualifiedToolName('a'.repeat(80), 'mongodb_find');
    expect(long.length).toBe(64);
    expect(long.endsWith('__mongodb_find')).toBe(true);
  });
});

describe('DSN masking (mongo re-audit A7)', () => {
  test('maskDsn hides the userinfo of any URL, keeps scheme, hosts and path', () => {
    expect(maskDsn('mongodb://alice:S3cretPw@db.example.com:27017/orders')).toBe('mongodb://***@db.example.com:27017/orders');
    expect(maskDsn('mongodb+srv://alice:p%40ss@cluster0.x.net/?retryWrites=true')).toBe('mongodb+srv://***@cluster0.x.net/?retryWrites=true');
    expect(maskDsn('mongodb://u:p@h1:27017,h2:27017/db')).toBe('mongodb://***@h1:27017,h2:27017/db');
    expect(maskDsn('https://user:tok@api.example.com/x')).toBe('https://***@api.example.com/x');
    expect(maskDsn('mongodb://db.example.com:27017')).toBe('mongodb://db.example.com:27017');
    expect(maskDsn('')).toBe('');
  });

  test('isDsn / isHttpUrl tell database URIs from links', () => {
    for (const u of ['mongodb://h', 'mongodb+srv://h', 'postgresql://h', 'postgres://h', 'mysql://h', 'redis://h', 'rediss://h']) {
      expect(isDsn(u)).toBe(true);
      expect(isHttpUrl(u)).toBe(false);
    }
    expect(isDsn('https://api.github.com')).toBe(false);
    expect(isHttpUrl('https://api.github.com')).toBe(true);
    expect(isHttpUrl('javascript:alert(1)')).toBe(false);
  });

  test('isMaskedSecret recognises the backends’ mask placeholders', () => {
    expect(isMaskedSecret('<redacted>')).toBe(true);
    expect(isMaskedSecret('********')).toBe(true);
    expect(isMaskedSecret('mongodb://alice:****@h/db')).toBe(true);
    expect(isMaskedSecret('mongodb://***@h/db')).toBe(true);
    expect(isMaskedSecret('mongodb://alice:pw@h/db')).toBe(false);
    expect(isMaskedSecret('')).toBe(false);
  });
});
