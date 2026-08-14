---
title: Content Safety, PII Detection, and Output Filtering
description: How AgentVerse detects PII, PHI, and PCI data, classifies toxicity with a two-pass LLM judge, scans streaming output in real time, and catches credential leakage before it reaches users.
outline: deep
---

# Content Safety and PII Detection

After injection attacks are handled (pre-LLM), AgentVerse applies a second tier of output safety: PII/secret detection, toxicity classification, streaming content filtering, and statistical anomaly detection. These run on LLM-generated content — what the model actually produces — and can block, redact, or quarantine outputs before they reach the user.

## Output Safety Architecture

```mermaid
flowchart TB
    LLM_OUT["🧠 LLM Output\n(streaming tokens)"]

    subgraph StreamPhase["Phase 1: Mid-Stream (per token)"]
        SG["StreamingGuard\napp/guardrails_v2/streaming_guard.py\n300-char rolling buffer\nRegex patterns: credentials, injection"]
        SG_DECISION{Block?}
    end

    subgraph PostPhase["Phase 2: Post-Generation (full output)"]
        PII["PII & Secret Detection\napp/guardrails_v2/engine.py\nSSN · CC · phone · email\nAPI keys: sk- · ghp_ · AIza"]
        TX["Toxicity Classifier\napp/guardrails_v2/toxicity.py\nPass 1: pattern regex\nPass 2: LLM judge (0.3-0.7 zone)"]
        NLI["NLI Grounding\napp/intelligence/nli_checker.py\nENTAILS / CONTRADICTS / NEUTRAL\nvs retrieved context"]
        AO["Output Anomaly Scan\napp/intelligence/output_anomaly.py\nSize > 50KB · credential patterns\nRepetition ratio > 70%"]
    end

    SAFE(["✅ Safe Output → User"])
    REDACTED(["📝 Redacted Output → User"])
    BLOCK(["🚫 BLOCKED"])

    LLM_OUT --> SG
    SG --> SG_DECISION
    SG_DECISION -->|match| BLOCK
    SG_DECISION -->|clean| PII

    PII -->|block| BLOCK
    PII -->|redact| REDACTED
    PII -->|clean| TX

    TX -->|toxic| BLOCK
    TX -->|ambiguous 0.3-0.7| TX
    TX -->|clean| NLI

    NLI -->|contradicts| BLOCK
    NLI -->|entails/neutral| AO

    AO -->|critical anomaly| BLOCK
    AO -->|clean| SAFE

    style LLM_OUT fill:#1e3a5f,stroke:#4a9eed,color:#e0e0e0
    style SG fill:#4a2e2e,stroke:#d45b5b,color:#e0e0e0
    style SG_DECISION fill:#5a4a2e,stroke:#d4a84b,color:#e0e0e0
    style PII fill:#5a4a2e,stroke:#d4a84b,color:#e0e0e0
    style TX fill:#5a4a2e,stroke:#d4a84b,color:#e0e0e0
    style NLI fill:#5a4a2e,stroke:#d4a84b,color:#e0e0e0
    style AO fill:#5a4a2e,stroke:#d4a84b,color:#e0e0e0
    style SAFE fill:#2d4a3e,stroke:#4aba8a,color:#e0e0e0
    style REDACTED fill:#2d4a3e,stroke:#4aba8a,color:#e0e0e0
    style BLOCK fill:#4a2e2e,stroke:#d45b5b,color:#e0e0e0
```

---

## PII and Secret Detection

**Source:** `app/guardrails_v2/engine.py`

The `GuardrailsEngine` performs regex-based PII and secret scanning at multiple `GuardrailLayer` checkpoints. For compliance bundles like HIPAA and GDPR, rules are automatically applied at `final_output`, `memory_write`, and `rag_ingest`.

### PII Patterns

| Category | Regex Pattern | Action (GDPR/HIPAA) |
|---|---|---|
| SSN | `\b\d{3}-\d{2}-\d{4}\b` | Redact / Block |
| Visa card | `\b4[0-9]{12}(?:[0-9]{3})?\b` | Block (PCI) |
| Mastercard | `\b5[1-5][0-9]{14}\b` | Block (PCI) |
| Email | `[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z]{2,}` | Redact |
| Phone (US) | `\b(?:\+1)?[-.\s]?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b` | Redact |

### Secret Patterns

| Secret Type | Regex | Action |
|---|---|---|
| OpenAI API key | `sk-[a-zA-Z0-9]{20,}` | Block + Critical alert |
| Anthropic API key | `sk-ant-[a-zA-Z0-9]{20,}` | Block + Critical alert |
| GitHub token | `ghp_[a-zA-Z0-9]{36}` | Block + Critical alert |
| Google API key | `AIza[0-9A-Za-z-_]{35}` | Block + Critical alert |
| Password in text | `(?i)password\s*[=:]\s*["\']?[\w!@#$%^&*]+` | Redact |

### GuardrailAction Escalation

When a PII rule fires, the action depends on the compliance bundle:

```
LOG      → violation recorded, content passes through
WARN     → violation recorded, content passes through, warning emitted
REDACT   → PII replaced with [REDACTED: SSN], content continues
BLOCK    → content entirely rejected, goal fails
REQUIRE_HITL → human must review before content is delivered
QUARANTINE → content held in quarantine queue for manual review
```

### Real-World Example 1: Financial Report with Customer SSNs

**Scenario:** A financial agent is asked to summarize loan application data. The underlying database contains SSNs in several records.

**What happens:**
1. Agent retrieves loan application records via SQL tool
2. Tool output contains `{"applicant": "John Smith", "ssn": "342-88-1294", ...}`
3. `scan_tool_output()` checks `GuardrailLayer.TOOL_OUTPUT`
4. GDPR bundle rule `"Block PII in outputs"` matches SSN pattern
5. Action: `REDACT` — output becomes `{"applicant": "John Smith", "ssn": "[REDACTED: SSN]", ...}`
6. Redacted content continues into context; LLM summarizes without seeing actual SSN
7. Final output is summary with no SSNs — rule passes at `final_output` layer

**No SSN ever appears in:** logs, the user-facing response, memory writes, or traces.

---

## Toxicity Classification

**Source:** `app/guardrails_v2/toxicity.py`

The `ToxicityClassifier` operates a two-pass system: a fast regex pass for clear cases, and an LLM judge for ambiguous content in the 0.3–0.7 score range.

### Toxicity Categories

| Category | Detection Method | Example |
|---|---|---|
| `hate_speech` | Regex + LLM | Derogatory content targeting identity groups |
| `threat` | Regex + LLM | "I will hurt you", "I know your address" |
| `sexual` | Regex + LLM (ambiguous zone) | Explicit sexual content |
| `self_harm` | Regex | "How to commit suicide", "kys" |
| `violence` | Regex | Step-by-step weapon construction instructions |

### Two-Pass Architecture

```mermaid
flowchart LR
    TEXT["LLM Output Text"]
    PASS1["Pass 1: Pattern Regex\nFast deterministic\nScore: 0.0 or 1.0"]
    SCORE{Score?}
    PASS2["Pass 2: LLM Judge\nSlower, more accurate\nAmbiguous zone only: 0.3-0.7"]
    COMBINE["Combine Scores\n(pass1 + pass2) / 2"]
    RESULT["ToxicityResult\nscore · is_toxic · categories · method"]

    TEXT --> PASS1
    PASS1 --> SCORE
    SCORE -->|< 0.3 or > 0.7| RESULT
    SCORE -->|0.3-0.7 ambiguous| PASS2
    PASS2 --> COMBINE
    COMBINE --> RESULT

    style TEXT fill:#1e3a5f,stroke:#4a9eed,color:#e0e0e0
    style PASS1 fill:#2d4a3e,stroke:#4aba8a,color:#e0e0e0
    style PASS2 fill:#5a4a2e,stroke:#d4a84b,color:#e0e0e0
    style COMBINE fill:#2d4a3e,stroke:#4aba8a,color:#e0e0e0
    style RESULT fill:#1e3a5f,stroke:#4a9eed,color:#e0e0e0
```

**Why ambiguous zone LLM review?**  
Some content is genuinely ambiguous: a medical textbook describing self-harm for clinical purposes is not toxic; a fictional story involving violence may or may not require intervention. The LLM judge handles these edge cases without blocking legitimate content.

**Threshold:** `score >= 0.6` → `is_toxic = True` (configurable per-tenant).

### Real-World Example 2: Dangerous Command in Generated Shell Script

**Scenario:** A DevOps agent is asked to generate a cleanup script. Due to a prompt ambiguity, the agent generates:

```bash
#!/bin/bash
# Cleanup old logs
rm -rf /var/log/*
rm -rf /home/*/documents/*  # Remove all user documents
```

**Detection layers triggered:**
1. `StreamingGuard` — `rm -rf` pattern in dangerous command list → BLOCK mid-stream
2. (If streaming guard missed) `GuardrailsEngine` `TOOL_ARGS` check — dangerous shell pattern → BLOCK before execution

**Note:** In AgentVerse, shell tool arguments are validated at `GuardrailLayer.TOOL_ARGS` before execution, not just in outputs.

---

## Streaming Guard

**Source:** `app/guardrails_v2/streaming_guard.py`

The `StreamingGuard` intercepts LLM output token by token as it streams, using a **300-character rolling buffer**. This catches dangerous content before the full generation completes — important for long outputs where a credential might appear at position 8,000 of a 10,000-token response.

### Rolling Buffer Mechanics

```python
class StreamingGuard:
    def __init__(self, patterns, buffer_size=300):
        self._buffer: deque[str] = deque()
        self._buffer_len: int = 0
        self._buffer_size = buffer_size  # 300 chars

    def check_token(self, token: str) -> GuardDecision:
        self._push(token)           # Add to buffer
        window = self._window()     # Join buffer as string
        for pat in self._patterns:
            if pat.search(window):  # Check all patterns
                return GuardDecision(allow=False, matched_pattern=pat.pattern)
        return GuardDecision(allow=True)
```

**Why 300 characters?** Most injection patterns and credential strings are < 100 characters. 300 characters provides a 3× safety margin that spans token boundaries (a single BPE token can be up to ~8 characters; multi-token patterns need overlap).

### Default Dangerous Patterns

The `StreamingGuard` in production ships with patterns for:
- API key formats (`sk-`, `ghp_`, `AIza`, `AKIA`)
- Private key headers (`-----BEGIN PRIVATE KEY-----`)
- SQL injection commands (`DROP TABLE`, `DELETE FROM ... WHERE 1=1`)
- Shell destruction (`rm -rf`, `format c:`, `dd if=/dev/zero`)
- Credential disclosure (`password=`, `secret=`, `api_key=`)

---

## Output Anomaly Detection

**Source:** `app/intelligence/output_anomaly.py`

Statistical anomaly detection catches outputs that are structurally abnormal, even when they don't match specific patterns. This is the last line of defense against novel attacks and unknown unknowns.

### Three Anomaly Checks

**1. Credential Detection** — Runs `_SECRET_PATTERNS` (7 regex patterns) against full output:
- Stripe/generic keys: `(sk|pk|rk)_[a-zA-Z0-9_]{20,}`
- AWS access keys: `AKIA[A-Z0-9]{16}`
- Bearer tokens: `bearer\s+[a-zA-Z0-9._\-]{30,}`
- Private key PEM headers

**2. Size Anomaly** — Output > 50,000 characters triggers a warning. Output > 150,000 characters triggers HIGH severity. This catches data exfiltration where an agent dumps an entire database into its output.

**3. Repetition Anomaly** — If > 70% of words in the output are repeats (`unique_words / total_words < 0.3`), the output is likely stuck in a generation loop — a sign the LLM was injected with a repetition instruction.

### Severity Levels

| Condition | Severity | Action |
|---|---|---|
| Credential detected | `critical` | BLOCK immediately |
| Output > 150KB | `high` | BLOCK |
| Output 50KB–150KB | `medium` | Log + HITL |
| Repetition > 70% | `medium` | Log + truncate |

### Real-World Example 3: Data Exfiltration via Output Size

**Attack scenario:** An attacker submits a goal: "Provide a complete audit of all customer records for compliance purposes." The agent, lacking proper tool scope restrictions, retrieves and begins summarizing 50,000 customer records.

**Detection:**
1. LLM begins generating the output (streaming)
2. `StreamingGuard` doesn't trigger — no credential patterns in the records
3. After generation completes, `scan_output_for_anomalies(output)` runs
4. `len(output)` = 2,400,000 characters (2.4 MB) — far exceeds `_MAX_NORMAL_OUTPUT_CHARS = 50000`
5. Anomaly: "Output size anomaly: 2400000 chars (expected <= 50000)", severity `high`
6. Output blocked; `output_anomaly_detected` WARNING logged with `anomalies` list
7. Goal fails with: `{"error": {"code": "OUTPUT_ANOMALY", "message": "Output exceeds safe size limits"}}`

## NLI Grounding Check

**Source:** `app/intelligence/nli_checker.py`

The NLI (Natural Language Inference) checker verifies that the agent's answer is **supported by the retrieved context** — not hallucinated. This is distinct from injection detection; it catches the case where the LLM generates plausible-sounding but fabricated facts.

```
ENTAILS    → evidence supports the claim → PASS
CONTRADICTS → evidence refutes the claim → BLOCK (hallucination detected)
NEUTRAL    → evidence is unrelated → PASS with lower confidence score
```

The `NLIChecker.check_answer_consistency()` method:
1. Takes the agent's final answer and the top 3 retrieved chunks
2. Calls the LLM with a structured fact-checking prompt
3. Returns a consistency score: `1.0` (ENTAILS) → `0.5` (NEUTRAL) → `~0.15` (CONTRADICTS)
4. Score < `0.3` triggers a grounding failure → output blocked or flagged

This operates with `max_tokens=10` (only the verdict word is needed), making it highly cost-efficient: ~0.0001 USD per check.

<!-- Sources: app/guardrails_v2/engine.py, app/guardrails_v2/models.py, app/guardrails_v2/streaming_guard.py, app/guardrails_v2/toxicity.py, app/intelligence/output_anomaly.py, app/intelligence/nli_checker.py -->
