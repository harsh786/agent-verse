"""Canonical target keys of ingestion Sources (duplicate-registration guard).

The same upstream target registered twice into one knowledge collection must
map to one key whatever the spelling (host case, host order, default port,
credentials, list order); a different collection list / prefix / table set must
map to a different key; credentials never influence the key.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.ingestion.source_config import SourceConfig, SourceFamily
from app.ingestion.source_identity import (
    canonical_source_url,
    canonical_target,
    canonical_target_hash,
    find_duplicate_in,
)


def _h(source_type: str, cc: dict[str, Any], **kw: Any) -> str | None:
    return canonical_target_hash(source_type, cc, **kw)


# ── MongoDB ──────────────────────────────────────────────────────────────────


def test_mongodb_same_target_any_spelling_is_one_key() -> None:
    a = {
        "uri": "mongodb://rs-a.example.com:27017,rs-b.example.com:27018/?replicaSet=rs0",
        "username": "reader",
        "password": "s3cret-1",
        "database": "ops",
        "collections": ["postmortems", "incidents"],
        "batch_size": 500,
    }
    b = {
        # host order + case, credentials inline, a different password, options,
        # collection order, a different batch size
        "uri": "MONGODB://other:pw2@RS-B.example.com:27018,rs-a.EXAMPLE.com/ops?tls=true",
        "password": "another-password",
        "collections": "incidents, postmortems",
        "batch_size": 50,
    }
    assert _h("mongodb", a) is not None
    assert _h("mongodb", a) == _h("mongodb", b)


def test_mongodb_host_and_port_fields_match_uri() -> None:
    uri = {"uri": "mongodb://db.example.com:27017/", "database": "ops", "collection": "pm"}
    fields = {"host": "DB.example.com", "database": "ops", "collections": ["pm"]}
    assert _h("mongodb", uri) == _h("mongodb", fields)


@pytest.mark.parametrize(
    "change",
    [
        {"collections": ["postmortems", "runbooks"]},  # different collection set
        {"collections": ["Postmortems"]},  # collection names are case-sensitive
        {"database": "ops2"},
        {"uri": "mongodb://other.example.com/"},
        {"uri": "mongodb+srv://rs-a.example.com/"},
    ],
)
def test_mongodb_different_target_is_a_different_key(change: dict[str, Any]) -> None:
    base = {"uri": "mongodb://rs-a.example.com/", "database": "ops", "collections": ["postmortems"]}
    assert _h("mongodb", base) != _h("mongodb", {**base, **change})


def test_mongodb_all_collections_differs_from_a_list() -> None:
    base = {"uri": "mongodb://h/", "database": "ops"}
    assert _h("mongodb", base) != _h("mongodb", {**base, "collections": ["a"]})


# ── secrets never in the key ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("source_type", "cc"),
    [
        ("mongodb", {"uri": "mongodb://u:TOPSECRET@h.example.com/db", "collections": ["c"]}),
        ("postgresql", {"dsn": "postgresql://u:TOPSECRET@pg.example.com/app", "tables": ["t"]}),
        ("redis", {"uri": "redis://:TOPSECRET@cache.example.com:6379/2"}),
        ("s3", {"bucket": "b", "secret_access_key": "TOPSECRET", "access_key_id": "AK"}),
        ("http", {"url": "https://api.example.com/x?token=TOPSECRET&page=1"}),
        ("elasticsearch", {"url": "https://u:TOPSECRET@es.example.com", "index": "logs"}),
        ("web_crawl", {"seed_urls": ["https://u:TOPSECRET@docs.example.com/"]}),
        ("arxiv", {"categories": ["cs.AI"], "api_key": "TOPSECRET"}),
    ],
)
def test_secrets_never_reach_the_canonical_target(source_type: str, cc: dict[str, Any]) -> None:
    target = canonical_target(source_type, cc)
    assert "TOPSECRET" not in json.dumps(target)


def test_a_different_password_is_the_same_target() -> None:
    a = {"dsn": "postgresql://app:one@pg.example.com:5432/app", "tables": ["orders"]}
    b = {"dsn": "postgresql://app:two@pg.example.com/app", "tables": ["ORDERS"]}
    assert _h("postgresql", a) == _h("postgresql", b)


def test_account_named_only_by_a_token_is_never_comparable() -> None:
    assert _h("hubspot", {"access_token": "x", "object_types": ["contacts"]}) is None
    assert _h("pagerduty", {"api_token": "x"}) is None
    # Slack channel *names* repeat across workspaces (named only by the token).
    assert _h("slack", {"bot_token": "x", "channel_names": ["general"]}) is None
    # Unknown connector with credentials and no endpoint outside them.
    assert _h("someconnector", {"api_key": "x", "kinds": ["a"]}) is None


# ── SQL ───────────────────────────────────────────────────────────────────────


def test_sql_dsn_and_fields_agree_and_tables_are_a_set() -> None:
    dsn = {"dsn": "postgres://u:p@PG.example.com/app?sslmode=require", "tables": ["b", "a"]}
    fields = {"host": "pg.example.com", "port": 5432, "database": "app", "tables": ["A", "B"]}
    keyword = {"dsn": "host=pg.example.com port=5432 dbname=app user=u", "tables": "a,b"}
    assert _h("postgresql", dsn) == _h("postgresql", fields) == _h("postgresql", keyword)
    assert _h("postgresql", dsn) != _h("postgresql", {**dsn, "tables": ["a"]})
    assert _h("postgresql", fields) != _h("postgresql", {**fields, "database": "other"})
    assert _h("postgresql", fields) != _h("postgresql", {**fields, "port": 6543})


def test_sql_multi_host_dsn_sorted() -> None:
    a = {"dsn": "postgresql://u:p@h1:5432,h2:5433/app", "tables": ["t"]}
    b = {"dsn": "postgresql://h2:5433,H1/app", "tables": ["t"]}
    assert _h("postgresql", a) == _h("postgresql", b)


def test_mysql_query_whitespace_is_normalised_but_not_case() -> None:
    base = {"host": "my.example.com", "database": "shop", "query": "SELECT *  FROM t\nWHERE x='A'"}
    same = {**base, "query": "SELECT * FROM t WHERE x='A'"}
    other = {**base, "query": "SELECT * FROM t WHERE x='a'"}
    assert _h("mysql", base) == _h("mysql", same)
    assert _h("mysql", base) != _h("mysql", other)


# ── object storage ───────────────────────────────────────────────────────────


def test_s3_bucket_prefix_endpoint() -> None:
    base = {"bucket": "Reports", "prefix": "/q1/", "region": "us-east-1", "access_key_id": "A"}
    assert _h("s3", base) == _h("s3", {"bucket": "reports", "prefix": "q1/", "region": "eu-west-1"})
    assert _h("s3", base) != _h("s3", {**base, "prefix": "q2/"})
    assert _h("s3", base) != _h("s3", {**base, "bucket": "other"})
    assert _h("s3", base) != _h("s3", {**base, "endpoint_url": "https://minio.example.com"})


def test_minio_and_s3_on_the_same_endpoint_are_one_target() -> None:
    cc = {"endpoint_url": "https://MINIO.example.com:443/", "bucket": "docs", "prefix": "kb/"}
    same = {"endpoint_url": "https://minio.example.com", "bucket": "docs", "prefix": "kb/"}
    assert _h("minio", cc) == _h("s3", same)


def test_gcs_and_azure() -> None:
    assert _h("gcs", {"bucket": "B", "prefix": "x"}) == _h("gcs", {"bucket": "b", "prefix": "x"})
    assert _h("gcs", {"bucket": "b", "prefix": "x"}) != _h("gcs", {"bucket": "b", "prefix": "y"})
    az = {"account_name": "Acct", "container": "Docs", "account_key": "k1"}
    assert _h("azure_blob", az) == _h(
        "azure_blob", {"account_name": "acct", "container": "docs", "account_key": "k2"}
    )
    # Account named only inside the (secret) connection string: not comparable.
    assert _h("azure_blob", {"connection_string": "AccountName=a;...", "container": "c"}) is None


# ── web / RSS / HTTP ─────────────────────────────────────────────────────────


def test_web_urls_normalised() -> None:
    a = {"urls": ["HTTPS://Example.com:443/feed/", "https://b.example.com/rss?x=1&a=2"]}
    b = {"urls": ["https://b.example.com/rss?a=2&x=1", "https://example.com/feed#top"]}
    assert _h("rss", a) == _h("rss", b)
    assert _h("rss", a) != _h("rss", {"urls": ["https://example.com/other"]})
    assert _h("web_crawl", {"seed_urls": ["https://docs.example.com/"]}) == _h(
        "web_crawl", {"seed_urls": ["https://DOCS.example.com"]}
    )
    assert _h("web_crawl", {"seed_urls": ["https://docs.example.com/"]}) != _h(
        "web_crawl",
        {"seed_urls": ["https://docs.example.com/"], "include_url_pattern": "/api/"},
    )


# ── Redis / Elasticsearch / Kafka ────────────────────────────────────────────


def test_redis_es_kafka() -> None:
    assert _h("redis", {"uri": "redis://Cache.example.com/2", "key_patterns": ["a:*", "b:*"]}) == _h(
        "redis", {"host": "cache.example.com", "port": 6379, "db": "2", "key_patterns": "b:*,a:*"}
    )
    assert _h("redis", {"host": "c", "db": 0}) != _h("redis", {"host": "c", "db": 1})
    assert _h("elasticsearch", {"url": "https://ES.example.com:443", "index": "Logs"}) == _h(
        "elasticsearch", {"url": "https://es.example.com/", "index": "logs"}
    )
    assert _h("kafka", {"bootstrap_servers": "k2:9092,K1:9092", "topics": ["a"]}) == _h(
        "kafka", {"bootstrap_servers": ["k1:9092", "k2:9092"], "topics": "a", "group_id": "g2"}
    )


def test_include_exclude_patterns_are_part_of_the_target() -> None:
    cc = {"bucket": "b"}
    assert _h("s3", cc, include_patterns=["*.md"]) != _h("s3", cc, include_patterns=["*.pdf"])
    assert _h("s3", cc, include_patterns=["*.md", "*.txt"]) == _h(
        "s3", cc, include_patterns=["*.txt", "*.md"]
    )


def test_default_canonicaliser_is_exact_normalised_match() -> None:
    a = {"categories": ["cs.AI", "cs.CL"], "keywords": "rag", "max_results": 50}
    b = {"keywords": "rag ", "categories": ["cs.CL", "cs.AI"], "max_results": 10}
    assert _h("arxiv", a) == _h("arxiv", b)  # tuning keys ignored, order ignored
    assert _h("arxiv", a) != _h("arxiv", {**a, "keywords": "llm"})
    # An endpoint outside the credentials makes a secret-bearing config comparable.
    assert _h("newthing", {"base_url": "https://x.example.com", "api_key": "k"}) is not None


def test_malformed_config_is_never_an_identity() -> None:
    assert _h("mongodb", {"database": "x"}) is None  # no host at all
    assert _h("s3", {"prefix": "x"}) is None


# ── canonical document URL (retrieval de-dup) ───────────────────────────────


def test_canonical_source_url() -> None:
    assert canonical_source_url("mongodb://B.example.com:27017,a.example.com/ops/pm/PM-1") == (
        canonical_source_url("mongodb://user:pw@a.example.com:27017,b.example.com/ops/pm/PM-1/")
    )
    # Document keys keep their case.
    assert canonical_source_url("https://x.com/Doc") != canonical_source_url("https://x.com/doc")
    assert canonical_source_url("") == ""


# ── duplicate lookup over SourceConfigs ──────────────────────────────────────


def _src(sid: str, *, tenant: str = "t1", collection: str = "kb", **cc: Any) -> SourceConfig:
    return SourceConfig(
        source_id=sid,
        tenant_id=tenant,
        name=sid,
        family=SourceFamily.NOSQL_DATABASE,
        source_type="mongodb",
        connection_config={"uri": "mongodb://h/", "database": "ops", **cc},
        collection_id=collection,
    )


def test_find_duplicate_in_scopes_tenant_and_collection() -> None:
    first = _src("s1", collections=["pm"])
    configs = [first]
    assert find_duplicate_in(configs, _src("s2", collections=["pm"])) == "s1"
    assert find_duplicate_in(configs, _src("s2", collections=["other"])) is None
    assert find_duplicate_in(configs, _src("s2", collection="kb2", collections=["pm"])) is None
    assert find_duplicate_in(configs, _src("s2", tenant="t2", collections=["pm"])) is None
    assert find_duplicate_in(configs, _src("s2", collection="", collections=["pm"])) is None
    assert find_duplicate_in(configs, first, exclude_source_id="s1") is None
