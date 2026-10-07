# Model-selection audit (main 26e01b9b5) — drives the "registry for every capability" fixes

Single-resolver design (shared by all fix branches): a facade `app/ai_router/resolve.py` with one function per
capability returning `Resolution(model, fallbacks, provider, base_url, source)` where source ∈ {registry_preference,
registry_cheapest, env_pin, local_default, deployment_role_map}. When nothing qualifies → raise
`ModelNotConfiguredError(capability, hint)` (never a vendor literal). Order per capability:
registry preference order for that capability → explicit env pin → local default (where one exists) → honest error.

## VISION (resolver: resolve_vision)
- app/ai_router/selection.py:269-299 resolve_vision_model → tighten (registry vision order w/ supports_vision → VISION_MODEL/NVIDIA_VISION_MODEL → raise).
- app/providers/model_defaults.py:51-63 configured_vision_model falls back to the REASONING model → drop that fallback.
- app/ai_router/seeder.py:228-232 BUG: seeds the vision model as [TG,VISION,OCR] and REPLACES the reasoning model's entry (registry.py:205-207, supports_tools=False) when VISION_MODEL unset → seed vision only when explicitly set; merge capabilities, never replace.
- app/ingestion/parsers/vision_parser.py:85-93, 259-300 raw AsyncOpenAI(OPENAI_BASE_URL) + "gpt-4o" fallback, ignores the model's base_url → route via ModelDispatchProvider; :306-310 ANTHROPIC_VISION_MODEL/"claude-3-5-sonnet-20241022" → registry.
- app/perception/browser_agent.py:350-364 configured_vision_model("claude-opus-4-5"), no registry/failover (used by rpa/executor.py:525, workflow/steps/rpa_step.py:67, page_analyzer) → resolver + failover; "vision available" gated on registry, not provider.supports_vision().
- app/multimodal/pipeline.py:110-118, 456-510 select_for_content_type: resolve_vision_model("gpt-4o"), no fallbacks → resolver + fallbacks.
- app/ai_router/model_orchestrator.py:128-163 _PROVIDER_VISION_MODEL / _MULTIMODAL_MODELS literals (gpt-4o, claude-3-5-sonnet, gemini-2.5-pro, gpt-4o-audio) → registry.
- app/org/model_gateway.py:100-107 "multimodal" profile literals → registry.
## OCR (resolver: resolve_ocr)
- app/ocr/engine.py:107-128, 795-830 already registry; Tesseract local tier (OCR_TESSERACT_ENABLED).
- app/ai_router/selection.py:302-326: ocr order → vision order → env (may fall back to reasoning model) → drop reasoning fallback; order: registry ocr → registry vision → OCR_MODEL → Tesseract (if enabled) → raise.
- app/ocr/extractors/general.py:60 sends literal model id "default" → reasoning resolver (role extraction).
- ollama OCR (ollama_provider.py:198, config.py:208 OLLAMA_OCR_MODEL) → registry.

## RERANK (resolver: resolve_reranker → a Reranker)
- app/rag/cross_encoder.py:57,133-145 hardcoded cross-encoder/ms-marco-MiniLM-L-6-v2 (no setting) → local tier, new setting RAG_CROSS_ENCODER_MODEL (default ms-marco).
- app/core/config.py:247 rag_default_rerank_strategy="auto"; app/rag/rerank_stage.py:107-142 and app/context/rerank_policy.py:296-317 AUTO never consults the registry → auto = registry chain first.
- app/context/rerank_policy.py:262-265 sync rerank() with HOSTED silently becomes score order → async or flag degraded.
- app/context/rerank_policy.py:627-648 _hosted_rerank uses registry chain (reranker_chain_from_settings, app/rag_platform/registry_reranker.py:356-437) only for strategy=hosted → make it the default tier.
- app/rag_platform/hosted_reranker.py:50,150-210 env RAG_HOSTED_RERANKER_URL/MODEL (default rerank-english-v3.0) → env-pin tier.
- app/main.py:163-165 onprem reranker (Qwen3-Reranker /v1/rerank) copied into settings; app/ai_router/seeder.py:233-236 seeds rerank only from env RAG_HOSTED_RERANKER_* → also seed from settings (onprem_reranker_url/model).
- ColBERT stays a separate local strategy; BUG app/rag/engine.py:2039-2041 ColBERTPattern(alpha=0.5) ignores settings.colbert_checkpoint → pass it.
- Dead: app/rag/engine.py:1125-1170 rerank_results, app/rag_platform/reranker.py legacy LLM reranker (tests only), model_orchestrator assignment.reranker (never read).

## SPEECH (resolvers: resolve_stt / resolve_tts)
- No registry support: TaskType.SPEECH / SPEECH_TO_TEXT / TEXT_TO_SPEECH exist but no _TASK_CAPABILITY mapping, no _CAPABILITY_SELECT_TASK entry (preferences API 400s), no seeding, no resolver.
- app/ingestion/parsers/audio_parser.py:111-121 raw AsyncOpenAI(OPENAI_BASE_URL) + configured_audio_model("whisper-1") (AUDIO_MODEL/TRANSCRIPTION_MODEL/NVIDIA_AUDIO_MODEL); used by multimodal/pipeline.py:563-582 + video parser → STT resolver via dispatch.
- app/multimodal/pipeline.py:201,247 metadata "extractor":"whisper-1" literal → resolved model.
- app/voice/providers/__init__.py:62,86 VOICE_STT_PROVIDER / VOICE_TTS_PROVIDER; stt/faster_whisper.py:89 VOICE_STT_MODEL default "tiny"; voice/router.py:86-97 /voice/status reports "large-v3-turbo" (mismatch!) → report resolved model.
- stt/whisper_api.py:33 whisper-1; tts openai_tts.py:48 tts-1, elevenlabs.py:47, omnivoice.py:108 VOICE_TTS_MODEL, kokoro.py:94-107 → registry STT/TTS w/ local tiers.
- app/core/config.py:421-424 voice_* settings never read (code uses os.getenv) → wire up.

## REASONING (later branch, after fix/registry-llm-no-fake merges)
- ~60 sites send model="" or provider._default_model → central hook in guarded_completion.complete_decision + ModelDispatchProvider: fill "" / "default" from resolve_reasoning(role) (skip BYOK + FakeProvider).
- Missing roles: refine, execute, summarization, extraction, chat, critique, synthesis, rag_*; judge missing from both routers; reasoning_mixin.py:140-143 refine → profile fallback literal.
- RAG LLM resolvers main.py:1050-1131, scaling/tasks.py:3743-3760, api/knowledge.py:356-392 → registry.
- chat (service.py:1720,1921,1958; intent.py:279,427-446 hardcoded available_models; preferred_model never applied), org (service.py:517,2246; quality_gates.py:372; team_formation.py:516; advanced_services.py:250 BUG always claude-sonnet-4-5; model_gateway.py gpt-4o-mini per tick), guardrails (guardrail_engine.py:379,543; HIPAA template pin gpt-4o-mini guardrail_patterns.py:577), evals (eval_suite.py:188, ai_ops_runner.py:120), self_optimizer_v2 (1118, 238, 334 literal claude-haiku-3-5), workflow llm_step (configured_default_model("gpt-4o")), nl_trigger ("gpt-4o"), nl_scheduler ("claude-opus-4-8"), tool_intelligence ("claude-haiku-3-5"), KG extractor, memory consolidation.
- Router literal profiles model_router.py:38-89, model_orchestrator.py _TIER_MODELS / _PROVIDER_CHAT_MODEL / select_models ignoring preference order.
- GET /models/default (api/model_registry.py:177) shows env model → registry head.

## EMBEDDING side paths (later branch, after feat/per-collection-embedders merges)
- main.py:997-1038 _embed_providers_by_name, embedding/model_registry.py + embedding/orchestrator.py content-type literals, workflow/steps/llm_step.py:76 embedder=provider (chat provider!), providers/onprem.py:197-207 MultiEndpointLLMProvider.embed, providers/registry.py:364, BYOK embed (llm_resolution.py:173, scaling/tasks.py:906, tenant_provider.py:283), memory/embedding.py:97 model_id captured once.

## Allowlist (legit literals): catalogs/pricing/dimension tables, behaviour detection (openai_compatible model-family checks, execution_strategy profiles, metrics), provider catalogs, vendor MCP connectors' tool-argument defaults (refresh deprecated ones), job-defined ids (RAFT), local asset names (kokoro files, colbert default setting), migrations.
