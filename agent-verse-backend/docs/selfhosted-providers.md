# Self-hosted vLLM provider configuration

```bash
# Self-hosted vLLM provider configuration (OpenAI-compatible endpoints).
#
# Reasoning + tool-calling (chat) share ONE base_url in the current design; the
# model router selects model NAMES against it. To use a SEPARATE server per role
# you would need per-role base_urls (not yet supported) — see the status notes.

# A small self-hosted model that emits <think> chains needs a longer LLM timeout
# than the 60s default — a 6000-token planner generation can exceed it. Raise it:
AGENTVERSE_LLM_CALL_TIMEOUT_SECONDS=300

# ── Reasoning / chat LLM (planner, executor, verifier) ── Qwen3.5-4B @ :30080 ──
DEFAULT_LLM_PROVIDER=openai_compatible
OPENAI_BASE_URL=http://192.168.63.104:30080/v1
OPENAI_API_KEY=sk-noauth                 # vLLM ignores it, but the client needs a non-empty value
DEFAULT_MODEL=Qwen/Qwen3.5-4B
DEFAULT_PLANNING_MODEL=Qwen/Qwen3.5-4B
DEFAULT_EXECUTION_MODEL=Qwen/Qwen3.5-4B
DEFAULT_VERIFICATION_MODEL=Qwen/Qwen3.5-4B

# ── Embedding ── Qwen3-Embedding-0.6B @ :30082 (1024-d) ──
EMBEDDING_BASE_URL=http://192.168.63.104:30082/v1
EMBEDDING_MODEL=Qwen/Qwen3-Embedding-0.6B
EMBEDDING_API_KEY=sk-noauth
EMBEDDING_DIM=1024                        # collections must use a supported dim (768/1024/1536/3072)

# ── Reranker ── Qwen3-Reranker-0.6B @ :30083 (Cohere-compatible /v1/rerank) ──
RAG_DEFAULT_RERANK_ENABLED=true
RAG_DEFAULT_RERANK_STRATEGY=hosted
RAG_HOSTED_RERANKER_URL=http://192.168.63.104:30083/v1/rerank
RAG_HOSTED_RERANKER_MODEL=Qwen/Qwen3-Reranker-0.6B
RAG_HOSTED_RERANKER_API_KEY=sk-noauth
RAG_HOSTED_RERANKER_ALLOW_INTERNAL=true  # required: the endpoint is a private LAN IP

# ── Tool-calling ── gemma-4-E2B @ :30081 ──
# NOTE: this endpoint currently rejects tool calls — the vLLM server must be
# started with:  --enable-auto-tool-choice --tool-call-parser <parser>
# (e.g. hermes for Qwen, or the gemma-appropriate parser). Until then, tool
# calls route through the reasoning model, which supports them.
```
