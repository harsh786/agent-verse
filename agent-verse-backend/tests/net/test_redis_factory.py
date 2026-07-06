"""Tests for the Redis HA client factory.

These tests are pure unit tests — no real Redis connection is required.
They use monkeypatch to control environment variables and unittest.mock to
patch redis client constructors so nothing attempts a network connection.
"""
from __future__ import annotations

import importlib
import sys
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reload_factory(monkeypatch):
    """Force a fresh import of redis_factory after env changes."""
    # Remove cached module so os.getenv() re-runs at import time (needed for
    # get_redis_kwargs which reads env at call-time, not import-time).
    sys.modules.pop("app.net.redis_factory", None)
    return importlib.import_module("app.net.redis_factory")


def _reload_celery_module(monkeypatch):
    """Reload celery_app so module-level os.getenv() picks up new env vars."""
    for mod_name in list(sys.modules):
        if mod_name.startswith("app.scaling"):
            del sys.modules[mod_name]
    # Stub out celery so the module loads without the full dependency tree.
    celery_stub = MagicMock()
    celery_instance = MagicMock()
    celery_stub.return_value = celery_instance
    celery_instance.conf = MagicMock()
    celery_instance.conf.update = MagicMock()
    # beat_scheduler attribute must be a plain string (not a mock) for the
    # getattr check in the Sentinel block.
    type(celery_instance.conf).beat_scheduler = ""
    with patch.dict(sys.modules, {"celery": MagicMock(Celery=celery_stub), "celery.schedules": MagicMock()}):
        mod = importlib.import_module("app.scaling.celery_app")
    return mod


# ---------------------------------------------------------------------------
# get_redis_kwargs
# ---------------------------------------------------------------------------


class TestGetRedisKwargs:
    def test_single_node_defaults(self, monkeypatch):
        monkeypatch.delenv("REDIS_SENTINEL_URLS", raising=False)
        monkeypatch.delenv("REDIS_CLUSTER_NODES", raising=False)
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")

        from app.net.redis_factory import get_redis_kwargs

        kwargs = get_redis_kwargs()
        assert kwargs["sentinel_urls"] == ""
        assert kwargs["cluster_nodes"] == ""
        assert kwargs["redis_url"] == "redis://localhost:6379/0"
        assert kwargs["sentinel_master"] == "mymaster"

    def test_sentinel_env_picked_up(self, monkeypatch):
        monkeypatch.setenv("REDIS_SENTINEL_URLS", "sentinel1:26379,sentinel2:26379")
        monkeypatch.setenv("REDIS_SENTINEL_MASTER", "prod-master")
        monkeypatch.delenv("REDIS_CLUSTER_NODES", raising=False)

        from app.net.redis_factory import get_redis_kwargs

        kwargs = get_redis_kwargs()
        assert kwargs["sentinel_urls"] == "sentinel1:26379,sentinel2:26379"
        assert kwargs["sentinel_master"] == "prod-master"

    def test_cluster_env_picked_up(self, monkeypatch):
        monkeypatch.delenv("REDIS_SENTINEL_URLS", raising=False)
        monkeypatch.setenv("REDIS_CLUSTER_NODES", "node1:6379,node2:6379,node3:6379")

        from app.net.redis_factory import get_redis_kwargs

        kwargs = get_redis_kwargs()
        assert kwargs["cluster_nodes"] == "node1:6379,node2:6379,node3:6379"
        assert kwargs["sentinel_urls"] == ""


# ---------------------------------------------------------------------------
# _parse_host_port_list (internal helper)
# ---------------------------------------------------------------------------


class TestParseHostPortList:
    def test_standard_host_port(self):
        from app.net.redis_factory import _parse_host_port_list

        result = _parse_host_port_list("h1:26379,h2:26380", 26379)
        assert result == [("h1", 26379), ("h2", 26380)]

    def test_host_only_uses_default_port(self):
        from app.net.redis_factory import _parse_host_port_list

        result = _parse_host_port_list("mysentinel", 26379)
        assert result == [("mysentinel", 26379)]

    def test_strips_whitespace(self):
        from app.net.redis_factory import _parse_host_port_list

        result = _parse_host_port_list("  h1 : 26379 , h2 : 26380  ", 26379)
        assert result == [("h1", 26379), ("h2", 26380)]

    def test_skips_empty_entries(self):
        from app.net.redis_factory import _parse_host_port_list

        result = _parse_host_port_list(",h1:26379,,", 26379)
        assert result == [("h1", 26379)]


# ---------------------------------------------------------------------------
# make_async_redis — topology selection
# ---------------------------------------------------------------------------


class TestMakeAsyncRedis:
    def test_single_node_path(self):
        """No sentinel/cluster env → aioredis.from_url is called."""
        mock_client = MagicMock()

        with patch("redis.asyncio.from_url", return_value=mock_client) as mock_from_url:
            from app.net.redis_factory import make_async_redis

            client = make_async_redis(
                sentinel_urls="",
                cluster_nodes="",
                redis_url="redis://myhost:6379/2",
            )

        mock_from_url.assert_called_once_with("redis://myhost:6379/2")
        assert client is mock_client

    def test_sentinel_path_creates_sentinel_and_master_for(self, monkeypatch):
        monkeypatch.delenv("REDIS_SENTINEL_PASSWORD", raising=False)
        monkeypatch.delenv("REDIS_PASSWORD", raising=False)
        monkeypatch.setenv("REDIS_SENTINEL_DB", "1")

        mock_master_client = MagicMock()
        mock_sentinel_instance = MagicMock()
        mock_sentinel_instance.master_for.return_value = mock_master_client
        mock_sentinel_cls = MagicMock(return_value=mock_sentinel_instance)

        with patch("redis.asyncio.Sentinel", mock_sentinel_cls):
            from app.net.redis_factory import make_async_redis

            client = make_async_redis(
                sentinel_urls="s1:26379,s2:26379",
                sentinel_master="mymaster",
                cluster_nodes="",
                redis_url="redis://ignored:6379/0",
            )

        # Sentinel must be constructed with the parsed node list
        mock_sentinel_cls.assert_called_once_with(
            [("s1", 26379), ("s2", 26379)],
            sentinel_kwargs={},
        )
        mock_sentinel_instance.master_for.assert_called_once()
        call_kwargs = mock_sentinel_instance.master_for.call_args
        assert call_kwargs.args[0] == "mymaster"
        assert call_kwargs.kwargs["db"] == 1
        assert client is mock_master_client

    def test_sentinel_takes_priority_over_cluster(self, monkeypatch):
        """Sentinel env set alongside cluster env → Sentinel wins."""
        monkeypatch.delenv("REDIS_SENTINEL_PASSWORD", raising=False)
        monkeypatch.delenv("REDIS_PASSWORD", raising=False)
        monkeypatch.delenv("REDIS_SENTINEL_DB", raising=False)

        mock_sentinel_instance = MagicMock()
        mock_sentinel_instance.master_for.return_value = MagicMock()

        with patch("redis.asyncio.Sentinel", return_value=mock_sentinel_instance):
            from app.net.redis_factory import make_async_redis

            make_async_redis(
                sentinel_urls="s1:26379",
                sentinel_master="mymaster",
                cluster_nodes="n1:6379,n2:6379",
                redis_url="redis://localhost:6379/0",
            )

        mock_sentinel_instance.master_for.assert_called_once()


# ---------------------------------------------------------------------------
# make_sync_redis — topology selection
# ---------------------------------------------------------------------------


class TestMakeSyncRedis:
    def test_single_node_path(self):
        mock_client = MagicMock()

        with patch("redis.from_url", return_value=mock_client) as mock_from_url:
            from app.net.redis_factory import make_sync_redis

            client = make_sync_redis(
                sentinel_urls="",
                cluster_nodes="",
                redis_url="redis://synchost:6379/0",
            )

        mock_from_url.assert_called_once_with("redis://synchost:6379/0")
        assert client is mock_client

    def test_sentinel_path(self, monkeypatch):
        monkeypatch.delenv("REDIS_SENTINEL_PASSWORD", raising=False)
        monkeypatch.delenv("REDIS_PASSWORD", raising=False)
        monkeypatch.delenv("REDIS_SENTINEL_DB", raising=False)

        mock_master_client = MagicMock()
        mock_sentinel_instance = MagicMock()
        mock_sentinel_instance.master_for.return_value = mock_master_client
        mock_sentinel_cls = MagicMock(return_value=mock_sentinel_instance)

        with patch("redis.Sentinel", mock_sentinel_cls):
            from app.net.redis_factory import make_sync_redis

            client = make_sync_redis(
                sentinel_urls="s1:26379",
                sentinel_master="mymaster",
                cluster_nodes="",
                redis_url="redis://ignored:6379/0",
            )

        mock_sentinel_cls.assert_called_once()
        mock_sentinel_instance.master_for.assert_called_once()
        assert client is mock_master_client


# ---------------------------------------------------------------------------
# Celery broker URL builder
# ---------------------------------------------------------------------------


class TestCeleryBrokerUrl:
    def test_single_node_without_sentinel(self, monkeypatch):
        monkeypatch.delenv("REDIS_SENTINEL_URLS", raising=False)
        monkeypatch.setenv("REDIS_URL", "redis://myredis:6379/1")

        # Reload to re-evaluate module-level os.getenv() calls.
        for mod in list(sys.modules):
            if "app.scaling" in mod:
                del sys.modules[mod]

        with patch.dict(
            sys.modules,
            {"celery": MagicMock(Celery=MagicMock(return_value=MagicMock())), "celery.schedules": MagicMock()},
        ):
            mod = importlib.import_module("app.scaling.celery_app")

        url = mod._build_celery_broker_url()
        assert url == "redis://myredis:6379/1"

    def test_sentinel_url_format(self, monkeypatch):
        monkeypatch.setenv("REDIS_SENTINEL_URLS", "h1:26379,h2:26380")
        monkeypatch.setenv("REDIS_SENTINEL_MASTER", "master1")
        monkeypatch.delenv("REDIS_PASSWORD", raising=False)
        monkeypatch.delenv("REDIS_SENTINEL_PASSWORD", raising=False)
        monkeypatch.delenv("REDIS_SENTINEL_DB", raising=False)

        for mod in list(sys.modules):
            if "app.scaling" in mod:
                del sys.modules[mod]

        with patch.dict(
            sys.modules,
            {"celery": MagicMock(Celery=MagicMock(return_value=MagicMock())), "celery.schedules": MagicMock()},
        ):
            mod = importlib.import_module("app.scaling.celery_app")

        url = mod._build_celery_broker_url()
        assert url.startswith("sentinel://")
        assert "h1:26379" in url
        assert "h2:26380" in url

    def test_sentinel_url_uses_semicolon_separator(self, monkeypatch):
        """Celery requires semicolons between Sentinel nodes."""
        monkeypatch.setenv("REDIS_SENTINEL_URLS", "s1:26379,s2:26379,s3:26379")
        monkeypatch.delenv("REDIS_PASSWORD", raising=False)
        monkeypatch.delenv("REDIS_SENTINEL_PASSWORD", raising=False)
        monkeypatch.delenv("REDIS_SENTINEL_DB", raising=False)

        for mod in list(sys.modules):
            if "app.scaling" in mod:
                del sys.modules[mod]

        with patch.dict(
            sys.modules,
            {"celery": MagicMock(Celery=MagicMock(return_value=MagicMock())), "celery.schedules": MagicMock()},
        ):
            mod = importlib.import_module("app.scaling.celery_app")

        url = mod._build_celery_broker_url()
        # All three nodes present, separated by ";"
        assert "s1:26379;s2:26379;s3:26379" in url

    def test_sentinel_url_includes_password(self, monkeypatch):
        monkeypatch.setenv("REDIS_SENTINEL_URLS", "s1:26379")
        monkeypatch.setenv("REDIS_PASSWORD", "supersecret")
        monkeypatch.delenv("REDIS_SENTINEL_PASSWORD", raising=False)
        monkeypatch.delenv("REDIS_SENTINEL_DB", raising=False)

        for mod in list(sys.modules):
            if "app.scaling" in mod:
                del sys.modules[mod]

        with patch.dict(
            sys.modules,
            {"celery": MagicMock(Celery=MagicMock(return_value=MagicMock())), "celery.schedules": MagicMock()},
        ):
            mod = importlib.import_module("app.scaling.celery_app")

        url = mod._build_celery_broker_url()
        assert ":supersecret@" in url

    def test_sentinel_url_no_auth_when_no_password(self, monkeypatch):
        monkeypatch.setenv("REDIS_SENTINEL_URLS", "s1:26379")
        monkeypatch.delenv("REDIS_PASSWORD", raising=False)
        monkeypatch.delenv("REDIS_SENTINEL_PASSWORD", raising=False)
        monkeypatch.delenv("REDIS_SENTINEL_DB", raising=False)

        for mod in list(sys.modules):
            if "app.scaling" in mod:
                del sys.modules[mod]

        with patch.dict(
            sys.modules,
            {"celery": MagicMock(Celery=MagicMock(return_value=MagicMock())), "celery.schedules": MagicMock()},
        ):
            mod = importlib.import_module("app.scaling.celery_app")

        url = mod._build_celery_broker_url()
        # No auth segment
        assert "@" not in url
