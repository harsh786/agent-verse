import { describe, expect, test } from 'vitest';
import {
  connectionSlug, connectorLabel, connectorTypeKey, connectorTypeLabel, qualifiedToolName,
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
