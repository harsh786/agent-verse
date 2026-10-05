/**
 * The REAL `GET /connectors/catalog` MongoDB entry and `GET /connectors` row
 * (mongo re-audit A10 / TG-06). Copied from the backend serializer, not
 * invented: agent-verse-backend/app/api/connectors.py list_catalog() over
 * app/mcp/catalog.py ConnectorSpec(name="mongodb", auth_type="connection_string",
 * default_url="mongodb://localhost:27017") with the generic connection_string
 * auth field from _DEFAULT_AUTH_FIELDS. `has_builtin` / `builtin_server_id`
 * follow the fix/mongo-mcp contract (the catalog entry is bound to the
 * builtin-mongodb handler). Keep this in sync with the backend — a faked
 * fixture (auth_type 'api_key') is how A1-A3 went unnoticed.
 */
import type { CatalogEntry, ConnectorResponse } from '@/lib/api/client';

export const MONGODB_CATALOG_ENTRY: CatalogEntry = {
  name: 'mongodb',
  display_name: 'Mongodb',
  description: 'MongoDB — documents CRUD, aggregations, index management',
  auth_type: 'connection_string',
  default_url: 'mongodb://localhost:27017',
  icon: 'mongodb',
  category: 'database',
  auth_fields: [
    {
      key: 'url',
      label: 'Connection URL',
      placeholder: 'service://host:port/db',
      field_type: 'url',
      required: true,
      hint: '',
    },
  ],
  has_builtin: true,
  builtin_server_id: 'builtin-mongodb',
  is_configured: false,
  connector_type: 'mongodb',
};

/** A registered MongoDB connection as GET /connectors returns it (credentials masked). */
export const MONGODB_REGISTERED_ROW: ConnectorResponse & { connector_type: string } = {
  server_id: 'builtin-mongodb:orders-db',
  name: 'orders-db',
  display_name: 'orders-db',
  builtin_type: 'builtin-mongodb',
  builtin_type_name: 'MongoDB',
  connector_type: 'mongodb',
  url: 'builtin://',
  upstream_url: 'cluster0.example.mongodb.net',
  status: 'active',
  auth_type: 'connection_string',
  auth_config: { uri: '<redacted>', username: 'alice', password: '<redacted>', database: 'orders' },
  has_builtin: true,
  auto_approve: false,
};
