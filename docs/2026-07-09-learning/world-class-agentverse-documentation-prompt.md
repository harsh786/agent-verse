# World-Class AgentVerse Documentation Master Prompt

Use this prompt from the repository root of AgentVerse.

```text
You are a principal AI platform architect, senior staff engineer, technical writer, and codebase analyst.

Your task is to create the world's best code-grounded learning documentation for AgentVerse, a vendor-agnostic, multi-tenant operating system for autonomous AI agents.

You must deeply analyze the AgentVerse repository before writing. Do not produce generic AI-agent documentation. Every explanation must be grounded in how this specific codebase works.

Repository context:
- Monorepo root: Agent-Verse
- Backend: agent-verse-backend, Python 3.12, FastAPI, LangGraph, Celery, Postgres, pgvector
- Frontend: agent-verse-frontend, React, Vite, TanStack Query, Zustand, Tailwind
- SDKs: agent-verse-sdk-python and agent-verse-sdk-typescript
- GitHub Action: agent-verse-github-action
- Existing architecture docs may exist under docs/architecture and docs/superpowers. Use them as references, but verify against source code.

Primary mission:
Create a dated learning documentation folder named YYYY-MM-DD-learning, using today's date, and write a complete world-class documentation corpus that explains how AgentVerse works end to end.Create multiple file for each concept

Critical rule:
Before writing final docs, inspect the codebase. Read source files, tests, migrations, docs, and recent commits. Cite real files and modules. If a feature is only scaffolded, experimental, partially wired, or documented but not implemented, say so clearly.

Required analysis workflow:
1. Map the repository structure and identify all deployable projects.
2. Read AGENTS.md, README.md, existing architecture docs, and recent implementation plans.
3. Inspect backend application assembly, especially app/main.py and lifespan wiring.
4. Inspect the agent runtime, especially app/agent/graph.py, app/agent/loop.py, app/agent/state.py, app/agent/prompts.py, app/agent/model_router.py, and app/agent/patterns/.
5. Inspect RAG, retrieval, ingestion, embeddings, memory, knowledge graph, multimodal, evals, observability, guardrails, governance, tenancy, reliability, scaling, and MCP modules.
6. Inspect representative tests to understand intended behavior and real coverage.
7. Build a concept-to-code map before writing explanations.
8. Write documentation only after the concept-to-code map is complete.

Required coverage:
You must cover all of the following areas in depth:

1. Agent patterns
- Plan-and-execute
- ReAct and tool calling
- Reflection
- Reflexion
- Goal trees
- Supervisor agents
- Debate agents
- Consensus and self-consistency
- Tree of thoughts
- Workflow planning and workflow execution
- Agent routing
- Agent spawning and multi-agent orchestration
- Human-in-the-loop execution
- Recovery, rollback, retry, and replanning

2. RAG patterns
- Basic RAG
- Hybrid RAG
- Fusion RAG
- Corrective RAG or CRAG
- Self-RAG
- Speculative RAG
- FLARE or forward-looking retrieval
- RAPTOR or hierarchical retrieval if present
- Agentic RAG
- Multi-hop retrieval
- Federated retrieval
- Web retrieval if present
- Memory-augmented RAG
- Graph-augmented RAG
- Code RAG
- Retrieval strategy selection

3. Agent memories
- Current run context
- Agent state
- Execution memory
- Long-term memory
- Reflexion memory
- Episodic memory if present
- Procedural memory if present
- Semantic memory if present
- RPA and visual memory
- Memory scoping by tenant, agent, goal, collection, and source
- Memory write and recall flows
- Memory safety and retention considerations

4. Knowledge and knowledge graph
- Knowledge collections
- Documents
- Chunks
- Metadata
- Citations
- KnowledgeStore behavior
- Vector and lexical indexes
- Knowledge graph nodes, edges, relationships, and retrieval path
- When to use graph retrieval versus vector retrieval
- How knowledge graph combines with RAG, memory, and agents

5. Ingestion
- PDF ingestion
- DOCX ingestion
- Markdown and text ingestion
- HTML and web page ingestion
- Code ingestion
- GitHub ingestion
- Jira ingestion
- Confluence ingestion
- Slack ingestion
- CSV and JSON ingestion
- Image ingestion
- Audio ingestion
- Video ingestion
- Browser/RPA extraction
- Content classification
- Parser selection
- Metadata preservation
- Deduplication and hashing
- Failure handling and reprocessing

6. Retrieval strategies
- Vector similarity
- PostgreSQL full-text search
- Trigram fuzzy search
- BM25
- Hybrid scoring
- Reciprocal rank fusion
- Cross-encoder reranking
- ColBERT or late interaction if present
- Parent-child retrieval
- Sentence window retrieval
- Query expansion
- HyDE
- Multi-query retrieval
- Multi-hop retrieval
- Reranking and score calibration
- Retrieval confidence and fallback paths

7. Embeddings
- Provider abstraction
- Embed request and response models
- Embedding router
- Embedding orchestrator
- Model dimensions
- Text embeddings
- Code embeddings
- Multimodal embeddings if present
- Embedding fallbacks
- Tenant plan and cost constraints
- Drift detection
- Re-embedding policies
- Index compatibility and dimension mismatch handling

8. Prompt builder
- Planner prompt construction
- Executor prompt construction
- Verifier prompt construction
- System prompt injection
- Tool schema injection
- RAG context injection
- Memory injection
- Visual context injection
- Feedback and verifier failure injection
- Prompt variants
- Prompt optimization
- Prompt safety and injection resistance

9. Agent improvement
- Eval-driven improvement
- Prompt optimizer
- Self-optimizer
- Lessons learned
- Reflexion feedback loop
- Memory-assisted improvement
- Model selection improvement
- Retrieval quality improvement
- Tool success feedback
- Cost and latency optimization
- Regression detection

10. Evals
- Goal evals
- Retrieval evals
- Tool relevance evals
- Safety evals
- Accuracy and grounding evals
- SLA and latency evals
- Cost evals
- Judge models
- Eval datasets and suites
- Online versus offline evaluation
- How evals feed improvement

11. Observability
- Structured logging
- Metrics
- Tracing
- Runtime decision traces
- RAG traces
- Cost breakdown
- Goal events
- SSE events
- Worker events
- Audit correlation
- Debugging workflows
- Production incident workflows

12. Guardrails
- Prompt injection detection
- Encoded injection detection
- Unicode or homoglyph attack handling
- Dangerous command detection
- PII detection and leakage prevention
- Tool hallucination prevention
- Tool name validation
- Tool argument validation
- Output grounding
- Citation verification
- Safety fail-closed behavior
- Human approval gates

13. Governance
- Tenant isolation
- API key scopes
- RBAC
- Row-level security
- Policy engine
- Permission matrix
- Cost control
- Budget enforcement
- Audit trail
- Compliance controls
- Approval workflows
- Tool risk classification
- Enterprise controls

14. Scopes
- Tenant scope
- API key scope
- Agent scope
- Knowledge collection scope
- Connector scope
- Tool scope
- Memory scope
- Policy scope
- Runtime context scope
- How scopes prevent data leakage and unsafe action

15. Multimodal
- Text
- PDF
- Image
- Screenshot
- Audio
- Video
- OCR
- Tables
- Code
- Browser DOM and page screenshots
- Asset ingestion jobs
- Extracted spans
- Visual context in planning
- Multimodal limitations and future work

16. Multi AI model router
- Planner model selection
- Executor model selection
- Verifier model selection
- Judge model selection
- Embedding model selection
- Vision model selection
- Audio model selection
- Reranker model selection
- Cost-aware routing
- Latency-aware routing
- Tenant-plan-aware routing
- Provider fallback
- Circuit breaker behavior

17. Chunking strategies
- Fixed token chunking
- Token-aware chunking
- Semantic chunking
- Markdown heading chunking
- Code-aware chunking
- Parent-child chunking
- Sentence window chunking
- Timestamp chunking
- Table chunking
- JSON/CSV chunking
- Image region chunking if present
- Agentic chunking
- Chunk overlap
- Chunk metadata
- When to use each strategy

18. Hallucination handling
- Retrieval grounding
- Citation requirements
- Tool schema validation
- Tool name validation
- Verifier checks
- Self-checking
- Confidence estimation
- Corrective retrieval
- Debate or consensus for uncertainty
- Guardrails
- Human-in-the-loop fallback
- Observability for hallucination debugging

19. Core platform workflows
- App startup and lifespan service wiring
- In-memory versus DB/Redis-backed services
- Tenant authentication
- Goal submission
- Queue routing
- Worker execution
- AgentGraph lifecycle
- Tool discovery through MCP
- Tool execution through MCP
- Vault credential resolution
- RAG retrieval before planning
- Planning
- Execution
- Verification
- Replanning
- Completion
- Failure
- Memory write
- Eval scoring
- Observability event emission
- Frontend SSE/WebSocket update path
- SDK and GitHub Action interaction path

20. Other core platform concepts
- MCP registry and clients
- Provider abstraction
- Cost control
- Rate limiting
- Bulkheads
- Circuit breakers
- Deduplication
- Rollback and compensating actions
- Triggers and schedules
- Celery queues
- Postgres and pgvector persistence
- Redis usage
- RLS and multi-tenancy
- Marketplace/templates if present
- RPA and browser automation if present
- SDKs and external integration boundaries

Explanation standard for every concept:
For each pattern, subsystem, or concept, answer all of these questions:
1. What is it?
2. Why does it exist?
3. What problem does it solve?
4. Where should it be used?
5. Where should it not be used?
6. How does AgentVerse implement it?
7. Which files, classes, functions, tests, or migrations are involved?
8. What is the end-to-end runtime flow?
9. How does it interact with agents, RAG, tools, memory, evals, observability, guardrails, governance, scopes, and model routing?
10. What are its failure modes?
11. How is it tested or how should it be tested?
12. What operational signals should be monitored?

Required output folder:
Create a folder named YYYY-MM-DD-learning at the repository root.

Required documentation files:
1. 00-index.md
   - Navigation index
   - Reader guide
   - System mental model
   - Concept-to-code map
   - Recommended reading paths for beginner, engineer, architect, and operator

2. 01-platform-lifecycle-and-core-workflows.md
   - End-to-end platform lifecycle
   - Startup, app wiring, tenant resolution, goal lifecycle, queueing, worker execution, AgentGraph, MCP, verification, memory, evals, observability

3. 02-agent-patterns.md
   - All agent patterns
   - Implementation map
   - Trade-offs
   - Runtime flows
   - Use cases

4. 03-rag-knowledge-ingestion-retrieval-chunking.md
   - All RAG patterns
   - Knowledge model
   - Knowledge graph
   - Ingestion
   - Retrieval
   - Chunking
   - Reranking
   - Citations

5. 04-memory-embeddings-prompt-builder.md
   - All memory types
   - Embedding architecture
   - Prompt builder architecture
   - Context injection and prompt safety

6. 05-multimodal-and-model-routing.md
   - Multimodal pipeline
   - AI model router
   - Planner/executor/verifier/judge/embedder/vision/audio routing
   - Provider fallback and cost-aware routing

7. 06-improvement-evals-observability.md
   - Agent improvement loops
   - Eval systems
   - Observability systems
   - Debugging workflows

8. 07-guardrails-governance-scopes-safety.md
   - Guardrails
   - Governance
   - Scopes
   - Tenant isolation
   - Policy, HITL, audit, cost, compliance

9. 08-hallucination-risk-and-reliability.md
   - Hallucination causes
   - Prevention layers
   - Grounding
   - Verification
   - Corrective RAG
   - Observability and debugging
   - Reliability patterns

10. 09-real-world-100-use-cases.md
   - Exactly 100 real-world use cases
   - Each use case must include goal, actors, inputs, components used, end-to-end flow, safety/governance path, memory/eval/observability path, and expected output

11. 10-end-to-end-flow-atlas.md
   - Cross-cutting flow diagrams in text form
   - How all subsystems work together
   - Common happy paths and failure paths

12. 11-code-analysis-map.md
   - Deep code analysis map
   - Key files and modules
   - Responsibilities
   - Cross-module dependencies
   - Tests and migrations to inspect

13. 12-glossary.md
   - Definitions of all core terms
   - Simple explanation plus AgentVerse-specific meaning

Required use-case format:
For each of the 100 real-world use cases, use this structure:

Use Case N: <name>
- Goal: <natural-language goal>
- Business problem: <why this matters>
- Actors: <user, agent, systems, tools>
- Inputs: <documents, APIs, events, user prompts, files, tickets, etc.>
- Agent pattern: <plan-execute, ReAct, supervisor, debate, goal tree, etc.>
- RAG pattern: <hybrid, fusion, corrective, graph, memory, etc.>
- Memory used: <current state, execution memory, long-term memory, reflexion, etc.>
- Ingestion path: <source to parser to chunker to embeddings to index>
- Retrieval path: <query planning to retrieval legs to reranking to citations>
- Model routing: <planner, executor, verifier, embedder, judge choices>
- Guardrails and governance: <scopes, policies, HITL, audit, PII, grounding>
- End-to-end flow:
  1. <step>
  2. <step>
  3. <step>
- Observability: <logs, metrics, traces, events, cost>
- Eval path: <how success is scored>
- Expected output: <final artifact or action>
- Failure modes: <what can go wrong and how AgentVerse handles it>
- Code references: <real files/modules from repo>

The 100 use cases must cover at least these domains:
- Software engineering
- DevOps and SRE
- Security operations
- Compliance and audit
- Legal and policy review
- Customer support
- Sales and CRM
- Finance operations
- HR operations
- Product management
- Project management
- Healthcare administration
- Insurance operations
- Banking and fintech
- E-commerce
- Education and training
- Research and knowledge management
- Data analytics
- Executive reporting
- Browser/RPA automation
- Multimodal document intelligence
- Incident response
- Procurement and vendor management
- Marketing operations
- Field operations

Code citation requirements:
- Cite exact file paths whenever possible.
- Prefer module-level citations when line numbers are not available.
- Include tests when they prove behavior.
- Include migrations when persistence or schema matters.
- Separate implemented behavior from planned, scaffolded, or inferred behavior.

Depth requirements:
- Do not stop at definitions.
- Explain why each design exists.
- Explain trade-offs and alternatives.
- Explain how each subsystem collaborates with others.
- Include failure modes and operational debugging guidance.
- Include examples using AgentVerse flows, not generic chatbot examples.
- Use clear diagrams in text form where helpful.

Style requirements:
- Write for four audiences at once: beginner learner, backend engineer, platform architect, and production operator.
- Use precise headings.
- Use tables when comparing patterns.
- Use step-by-step flows for runtime behavior.
- Use short code snippets only when they clarify data models or call paths.
- Avoid marketing language.
- Avoid vague phrases like "seamlessly", "robust", "powerful", or "world-class" unless you prove the claim with implementation detail.
- Prefer factual, code-grounded explanations.

Quality gates before final answer:
Before saying the work is complete, perform a self-review and fix issues:
1. Coverage gate: every required topic above appears in at least one documentation file.
2. Code-grounding gate: every major concept has at least one repo file/module citation.
3. Integration gate: docs explain how concepts work together, not only in isolation.
4. Use-case gate: exactly 100 use cases exist and each follows the required format.
5. Accuracy gate: implemented, partial, scaffolded, and future behavior are clearly labeled.
6. Specificity gate: remove generic AI explanations that could apply to any project.
7. Completeness gate: every concept answers what, why, where, how, problem solved, interactions, failure modes, tests, and observability.
8. Consistency gate: terminology is consistent across all docs.
9. Navigation gate: 00-index.md links to every file and gives reading paths.
10. No-placeholder gate: no TODO, TBD, placeholder, empty section, or unfinished use case remains.

Recommended source areas to inspect:
- AGENTS.md
- README.md
- docs/architecture/
- docs/superpowers/specs/
- docs/superpowers/plans/
- agent-verse-backend/app/main.py
- agent-verse-backend/app/agent/
- agent-verse-backend/app/rag/
- agent-verse-backend/app/knowledge/
- agent-verse-backend/app/ingestion/
- agent-verse-backend/app/embedding/
- agent-verse-backend/app/memory/
- agent-verse-backend/app/intelligence/
- agent-verse-backend/app/observability/
- agent-verse-backend/app/governance/
- agent-verse-backend/app/tenancy/
- agent-verse-backend/app/reliability/
- agent-verse-backend/app/mcp/
- agent-verse-backend/app/multimodal/
- agent-verse-backend/app/perception/
- agent-verse-backend/app/rpa/
- agent-verse-backend/app/scaling/
- agent-verse-backend/app/triggers/
- agent-verse-backend/app/db/models/
- agent-verse-backend/app/db/migrations/
- agent-verse-backend/tests/
- agent-verse-frontend/src/features/
- agent-verse-frontend/src/lib/
- agent-verse-sdk-python/
- agent-verse-sdk-typescript/
- agent-verse-github-action/

Final response after creating docs:
Return a concise summary with:
- Folder path created
- Files created
- Number of use cases generated
- Any features found to be partial/scaffolded
- Verification performed
- Recommended next reading order
```
