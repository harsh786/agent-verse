# Redis knowledge source

**UI:** Sources → Add source → *NoSQL Database* → `redis`
**Backend:** `agent-verse-backend/app/ingestion/connectors/redis_connector.py` (`source_type: "redis"`, family `nosql_database`)
**Frontend form:** `agent-verse-frontend/src/features/ingestion/components/families/RedisForm.tsx`

This is a **Sources (knowledge ingestion)** connector. It reads keys from a tenant's Redis and
indexes them into a knowledge collection. It is configured on the Source and has nothing to do
with a Redis MCP server registered under Connectors.

## Connection fields (`connection_config`)

Secret fields are vault-encrypted at rest (`enc:v1:` via `source_secrets`), returned by the API
as `********`, and kept unchanged when `********` is sent back on `PATCH`. They are never logged.

| Field | Applies to | Notes |
|---|---|---|
| `mode` | all | `standalone` (default), `sentinel`, `cluster` |
| `uri` **(secret)** | standalone | `redis://[user[:password]@]host[:port][/db]` or `rediss://` (TLS). Query options are refused, so set TLS files and the like in the fields. Explicit fields override credentials in the URL. |
| `host`, `port` (6379), `db` (0) | standalone | Use these or `uri`. |
| `auth_type` | all | `none`, `password` (requirepass / default user), `acl` (username + password). Inferred when omitted. |
| `username` | `acl` | Redis 6+ ACL user. |
| `password` **(secret)** | `password`, `acl` | Data-node password. |
| `sentinels` | sentinel | `h1:26379,h2:26379` (default port 26379). |
| `sentinel_master` | sentinel | Master (service) name. |
| `sentinel_username`, `sentinel_password` **(secret)** | sentinel | The Sentinels' own auth, separate from the data password. |
| `sentinel_tls` | sentinel | TLS to the Sentinels. Defaults to `tls`. |
| `cluster_nodes` | cluster | Seed nodes `h1:7000,h2:7001`. If empty, `host`/`port` is the seed. `db` must be 0. |
| `tls` | all | Use TLS. Implied by `rediss://` and by any TLS field. |
| `tls_ca_pem` | all | PEM CA bundle used to verify the server. The default is the system trust store. |
| `tls_client_cert`, `tls_client_private_key` **(secret)** | all | PEM client certificate and key for mutual TLS. Give both or neither. |
| `tls_client_key_password` **(secret)** | all | Passphrase for an encrypted client key. |
| `tls_check_hostname` | all | Default `true`. Turn it off only for servers reached by an IP that the certificate does not name, such as a master address reported by Sentinel. |
| `tls_allow_invalid_certificates` | all | Explicit opt-out of certificate verification. Not recommended. |
| `key_patterns` | all | Glob patterns, as a list or comma-separated. Default `*`. |
| `types` | all | Subset of `string, hash, list, set, zset, stream, json` (RedisJSON). Default: all. |
| `max_keys_per_sync` | all | Default 10000. The next sync resumes from the stored SCAN position. |
| `max_value_bytes` | all | Default 1 MiB. Larger values are truncated and the document is flagged `truncated: true`. |
| `max_items` | all | Default 1000. Maximum entries read from a hash, list, set, zset or stream. |

## Behaviour

- **Validate (`GET /sources/{id}/health`)** connects, authenticates and runs discovery. It returns
  the Redis version, mode, key count, and up to 1000 sampled keys grouped into key patterns
  (`user:*`) with their type counts. For sentinel it also returns the master address; for cluster,
  the primaries.
- **Sync** turns each key into one document with a stable id, `title` = the key, and metadata
  `key`, `type`, `db`, `node`, `truncated`. The cursor stores the SCAN position per node and
  pattern. Once a full pass finishes, the next sync starts a new pass, and the pipeline's content
  hash skips values that have not changed.
- **Egress.** Every address the connector dials passes the connector egress policy and is pinned
  to the checked IPs. That covers the configured host, each Sentinel, the master a Sentinel
  reports, and every cluster node a seed announces. Internal addresses are refused unless the
  operator sets `INGESTION_ALLOW_INTERNAL_SOURCES=true` and lists the host in
  `INGESTION_INTERNAL_SOURCE_ALLOWLIST`. Tenant config can never widen that list. Cluster keys
  are read from each primary directly, so `MOVED` redirects, which can name any host, are never
  followed.

## Verified against real Redis

`agent-verse-backend/tests/ingestion/test_redis_source_integration.py` (testcontainers) covers:
no auth, requirepass, ACL user (including ACL key scoping), `redis://` and `rediss://` URLs, TLS
with a custom CA (and refusal without it), mutual TLS (refused without a client certificate),
Sentinel with Sentinel auth, Cluster, and egress refusal of a Sentinel-reported master or an
announced cluster node at a disallowed address.
