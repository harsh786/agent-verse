"""Tests for tokenizer."""
import pytest
from app.agent.tokenizer import Tokenizer, count_tokens


class TestTokenizer:
    def test_count_returns_int(self):
        t = Tokenizer()
        assert isinstance(t.count("hello world"), int)
        assert t.count("hello world") > 0

    def test_count_empty_string(self):
        t = Tokenizer()
        assert t.count("") == 0

    def test_count_longer_text_more_tokens(self):
        t = Tokenizer()
        assert t.count("hello world foo bar baz") > t.count("hello")

    def test_truncate_to_tokens(self):
        t = Tokenizer()
        long_text = "word " * 100
        truncated = t.truncate_to_tokens(long_text, 10)
        assert t.count(truncated) <= 12  # small tolerance
        assert len(truncated) < len(long_text)

    def test_truncate_short_text_unchanged(self):
        t = Tokenizer()
        short = "hello world"
        assert t.truncate_to_tokens(short, 1000) == short

    def test_module_singleton_count(self):
        result = count_tokens("test text")
        assert isinstance(result, int)
        assert result > 0

    def test_is_accurate_bool(self):
        t = Tokenizer()
        assert isinstance(t.is_accurate, bool)

    def test_fallback_when_tiktoken_unavailable(self, monkeypatch):
        """When tiktoken import raises, fallback byte heuristic is used without error."""
        import builtins
        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "tiktoken":
                raise ImportError("tiktoken not available")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)
        t = Tokenizer()
        result = t.count("hello world test")
        assert isinstance(result, int)
        assert result > 0
        assert t.is_accurate is False

    def test_truncate_returns_subset_of_text(self):
        t = Tokenizer()
        text = "The quick brown fox jumps over the lazy dog. " * 20
        truncated = t.truncate_to_tokens(text, 5)
        assert len(truncated) < len(text)
        assert t.count(truncated) <= 7  # allow slight tolerance


class TestVectorCacheBackend:
    @pytest.mark.asyncio
    async def test_in_memory_store_and_retrieve(self):
        from app.rag.vector_cache_backend import InMemoryCacheBackend
        backend = InMemoryCacheBackend()

        embedding = [0.1, 0.2, 0.3, 0.4, 0.5]
        await backend.store("test query", embedding, "test response", "t1")

        result = await backend.get_similar(embedding, "t1", threshold=0.99)
        assert result is not None
        assert result["response"] == "test response"
        assert result["score"] > 0.99

    @pytest.mark.asyncio
    async def test_in_memory_miss_below_threshold(self):
        from app.rag.vector_cache_backend import InMemoryCacheBackend
        backend = InMemoryCacheBackend()

        embedding_a = [1.0, 0.0, 0.0]
        embedding_b = [0.0, 1.0, 0.0]  # orthogonal, similarity = 0

        await backend.store("query a", embedding_a, "response a", "t1")
        result = await backend.get_similar(embedding_b, "t1", threshold=0.5)
        assert result is None

    @pytest.mark.asyncio
    async def test_in_memory_clear(self):
        from app.rag.vector_cache_backend import InMemoryCacheBackend
        backend = InMemoryCacheBackend()

        embedding = [0.1, 0.2, 0.3]
        await backend.store("q", embedding, "r", "t1")
        await backend.clear("t1")
        result = await backend.get_similar(embedding, "t1", threshold=0.5)
        assert result is None

    @pytest.mark.asyncio
    async def test_in_memory_tenant_isolation(self):
        from app.rag.vector_cache_backend import InMemoryCacheBackend
        backend = InMemoryCacheBackend()

        embedding = [0.1, 0.2, 0.3]
        await backend.store("q", embedding, "tenant-A response", "tenant-A")

        result = await backend.get_similar(embedding, "tenant-B", threshold=0.5)
        assert result is None

    @pytest.mark.asyncio
    async def test_select_cache_backend_returns_in_memory_without_db(self):
        from app.rag.vector_cache_backend import select_cache_backend, InMemoryCacheBackend
        backend = await select_cache_backend(db_factory=None, redis=None)
        assert isinstance(backend, InMemoryCacheBackend)

    @pytest.mark.asyncio
    async def test_in_memory_stats(self):
        from app.rag.vector_cache_backend import InMemoryCacheBackend
        backend = InMemoryCacheBackend()
        embedding = [0.5, 0.5, 0.0]
        await backend.store("q", embedding, "r", "t1")
        # one hit
        await backend.get_similar(embedding, "t1", threshold=0.9)
        # one miss
        await backend.get_similar([0.0, 0.0, 1.0], "t1", threshold=0.9)
        stats = await backend.stats("t1")
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert stats["backend"] == "in_memory"
