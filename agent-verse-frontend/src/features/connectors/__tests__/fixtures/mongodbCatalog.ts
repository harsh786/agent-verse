/**
 * The REAL MongoDB catalog entry and registered row (mongo re-audit A10 /
 * TG-06, NF-3) — re-exported from src/test/fixtures/mongodb_catalog.json,
 * which agent-verse-backend/tests/api/test_connectors_fe_fixtures.py GENERATES
 * from GET /connectors/catalog and the register/GET responses (and fails when
 * the committed file drifts). Never hand-edit it: regenerate it.
 */
import type { CatalogEntry, ConnectorResponse } from '@/lib/api/client';
import generated from '@/test/fixtures/mongodb_catalog.json';

export const MONGODB_CATALOG_ENTRY = generated.catalog_entry as unknown as CatalogEntry;

/** A registered MongoDB connection as GET /connectors/{id} returns it (secrets '<redacted>'). */
export const MONGODB_REGISTERED_ROW = generated.registered_row as unknown as ConnectorResponse;

/** The POST /connectors register response for mongo_register_request.json. */
export const MONGODB_REGISTER_RESPONSE = generated.register_response as unknown as ConnectorResponse;
