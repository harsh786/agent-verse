#!/usr/bin/env bash
# verify-spec.sh — Comprehensive spec vs implementation verification
# Run manually: bash .githooks/verify-spec.sh
# Or automatically via git hook (post-commit)
#
# EXIT CODES:
#   0 = all checks pass
#   1 = gaps found (see output)
#   2 = tests failing (see output)

set -uo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SPEC="$REPO_ROOT/agent-verse-backend/docs/superpowers/specs/2026-08-17-ai-organization-os-design.md"
BACKEND="$REPO_ROOT/agent-verse-backend/app"
FRONTEND="$REPO_ROOT/agent-verse-frontend/src"
PASSES=0
GAPS=0
WARNINGS=0

echo -e "${BOLD}${BLUE}╔══════════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}${BLUE}║   AgentVerse Spec vs Implementation Audit            ║${NC}"
echo -e "${BOLD}${BLUE}╚══════════════════════════════════════════════════════╝${NC}"
echo ""

# ─── helpers ──────────────────────────────────────────────────────────────────
pass()  { echo -e "  ${GREEN}✅ $1${NC}";  ((PASSES++)); }
gap()   { echo -e "  ${RED}❌ GAP: $1${NC}";  ((GAPS++)); }
warn()  { echo -e "  ${YELLOW}⚠️  $1${NC}"; ((WARNINGS++)); }
section(){ echo -e "\n${BOLD}── $1 ──────────────────────────────────────────${NC}"; }

count_files() { find "$1" -name "$2" 2>/dev/null | wc -l | tr -d ' '; }
count_grep()  { grep -r "$1" "$2" --include="*.py" 2>/dev/null | wc -l | tr -d ' ' || echo 0; }
count_grepr() { grep -rE "$1" "$2" --include="$3" 2>/dev/null | wc -l | tr -d ' ' || echo 0; }

# ─── SPEC FILE ────────────────────────────────────────────────────────────────
section "SPEC FILE"
if [ -f "$SPEC" ]; then
  SPEC_LINES=$(wc -l < "$SPEC" | tr -d ' ')
  SPEC_SECTS=$(grep -c "^## " "$SPEC" 2>/dev/null || echo 0)
  pass "Spec exists ($SPEC_LINES lines, $SPEC_SECTS sections)"
else
  gap "Spec file NOT FOUND at expected location"
fi

# ─── PHASE 0: FOUNDATION ─────────────────────────────────────────────────────
section "PHASE 0: Foundation"

[ -f "$REPO_ROOT/agent-verse-backend/Dockerfile" ]          && pass "Backend Dockerfile" || gap "Backend Dockerfile missing"
[ -f "$REPO_ROOT/agent-verse-frontend/Dockerfile" ]         && pass "Frontend Dockerfile" || gap "Frontend Dockerfile missing"
[ "$(ls $REPO_ROOT/.github/workflows/*.yml 2>/dev/null | wc -l | tr -d ' ')" -gt 5 ] && pass "CI/CD workflows ($(ls $REPO_ROOT/.github/workflows/*.yml | wc -l | tr -d ' ') found)" || gap "CI/CD workflows missing or incomplete"

MIGRATIONS=$(ls $REPO_ROOT/agent-verse-backend/app/db/migrations/versions/*.py 2>/dev/null | wc -l | tr -d ' ')
[ "$MIGRATIONS" -gt 50 ] && pass "DB migrations ($MIGRATIONS found)" || gap "DB migrations insufficient (found $MIGRATIONS, expected >50)"

[ -f "$BACKEND/main.py" ]     && pass "FastAPI main.py" || gap "FastAPI main.py missing"
[ -f "$BACKEND/core/config.py" ] || [ -f "$BACKEND/core/settings.py" ] && pass "Settings/Config" || gap "Settings/Config missing"

OTEL_COUNT=$(count_grep "start_as_current_span" "$BACKEND")
[ "$OTEL_COUNT" -gt 20 ] && pass "OTel instrumentation ($OTEL_COUNT spans)" || warn "Low OTel coverage ($OTEL_COUNT spans, expected >20)"

# ─── PHASE 1: CORE ORG + MISSION ──────────────────────────────────────────────
section "PHASE 1: Core Org + Mission"

check_module() {
  local name=$1 path=$2
  [ -d "$path" ] && pass "$name module exists" || gap "$name module MISSING: $path"
}

check_module "Agent/Mission" "$BACKEND/agent"
check_module "Governance/HITL" "$BACKEND/governance"
check_module "Tenancy/Auth" "$BACKEND/tenancy"
check_module "Reliability" "$BACKEND/reliability"
check_module "Providers (LLM)" "$BACKEND/providers"
check_module "MCP" "$BACKEND/mcp"
check_module "Services (orchestration)" "$BACKEND/services"
check_module "Scaling (Celery)" "$BACKEND/scaling"

# LangGraph
[ -f "$BACKEND/agent/loop.py" ] || [ -f "$BACKEND/agent/graph.py" ]
LANGGRAPH=$(count_grep "StateGraph\|langgraph" "$BACKEND")
[ "$LANGGRAPH" -gt 5 ] && pass "LangGraph agent loop ($LANGGRAPH references)" || gap "LangGraph not found in app/"

# Celery
CELERY_TASKS=$(count_grep "@celery_app.task\|@app.task" "$BACKEND")
[ "$CELERY_TASKS" -gt 5 ] && pass "Celery tasks ($CELERY_TASKS defined)" || gap "Celery tasks insufficient"

# Circuit breakers
CB_COUNT=$(count_grep "CircuitBreaker\|circuit_breaker" "$BACKEND")
[ "$CB_COUNT" -gt 5 ] && pass "Circuit breakers ($CB_COUNT usages)" || warn "Low circuit breaker coverage ($CB_COUNT)"

# Outbox pattern
OUTBOX=$(count_grep "OutboxEvent\|outbox" "$BACKEND")
[ "$OUTBOX" -gt 2 ] && pass "Outbox pattern ($OUTBOX references)" || warn "Outbox pattern sparse ($OUTBOX)"

# ─── PHASE 2: MULTI-TENANCY ───────────────────────────────────────────────────
section "PHASE 2: Multi-Tenancy + Auth"

RLS=$(grep -r "ROW LEVEL SECURITY\|CREATE POLICY" $REPO_ROOT/agent-verse-backend/app/db/migrations/versions/ 2>/dev/null | wc -l | tr -d ' ')
[ "$RLS" -gt 10 ] && pass "RLS policies ($RLS found in migrations)" || gap "RLS policies insufficient ($RLS)"

[ -d "$BACKEND/tenancy" ] && pass "Tenancy module" || gap "Tenancy module missing"
SSO=$(count_grep "keycloak\|SAML\|OIDC\|sso" "$BACKEND")
[ "$SSO" -gt 5 ] && pass "SSO/SAML integration ($SSO references)" || warn "SSO integration sparse ($SSO)"

RBAC_MIG=$(ls $REPO_ROOT/agent-verse-backend/app/db/migrations/versions/*rbac* 2>/dev/null | wc -l)
[ "$RBAC_MIG" -gt 0 ] && pass "RBAC migrations exist" || gap "RBAC migrations missing"

# ─── PHASE 3: INTELLIGENCE ────────────────────────────────────────────────────
section "PHASE 3: Intelligence Layer"

check_module "Memory" "$BACKEND/memory"
check_module "RAG" "$BACKEND/rag"
check_module "Intelligence (evals/self-improvement)" "$BACKEND/intelligence"
check_module "Guardrails v2" "$BACKEND/guardrails_v2"
check_module "Evals" "$BACKEND/evals"

# ─── PHASE 4: KNOWLEDGE / GRAPHIFY ────────────────────────────────────────────
section "PHASE 4: Knowledge + Graphify"

check_module "Knowledge Graph" "$BACKEND/knowledge_graph"
check_module "Knowledge" "$BACKEND/knowledge"
check_module "Ingestion" "$BACKEND/ingestion"

GRAPHIFY=$(count_grep "graphify\|Graphify\|knowledge_graph" "$BACKEND")
[ "$GRAPHIFY" -gt 3 ] && pass "Graphify implementation ($GRAPHIFY references)" || gap "Graphify not found (check app/knowledge_graph/)"

# ─── PHASE 5: VOICE + COLLABORATION ───────────────────────────────────────────
section "PHASE 5: Voice + Collaboration"

check_module "Voice" "$BACKEND/voice"
check_module "Collaboration" "$BACKEND/collab"

STT=$(count_grep "STT\|speech_to_text\|whisper\|AssemblyAI" "$BACKEND")
[ "$STT" -gt 3 ] && pass "STT engine ($STT references)" || warn "STT implementation sparse"

# ─── PHASE 6: GATEWAY ─────────────────────────────────────────────────────────
section "PHASE 6: Gateway + Integrations"

check_module "Integrations/UCG" "$BACKEND/integrations"
check_module "Coordination (A2A)" "$BACKEND/coordination"

TELEGRAM=$(count_grep "telegram\|Telegram" "$BACKEND")
[ "$TELEGRAM" -gt 3 ] && pass "Telegram integration ($TELEGRAM references)" || warn "Telegram sparse ($TELEGRAM)"

# ─── PHASE 7: ENTERPRISE ──────────────────────────────────────────────────────
section "PHASE 7: Enterprise"

check_module "Enterprise" "$BACKEND/enterprise"
check_module "Civilization" "$BACKEND/civilization"

# ─── FRONTEND ─────────────────────────────────────────────────────────────────
section "FRONTEND"

# Generic quantity checks
FE_PAGES=$(find "$FRONTEND/features" -name "*Page.tsx" 2>/dev/null | wc -l | tr -d ' ')
[ "$FE_PAGES" -gt 10 ] && pass "Frontend pages ($FE_PAGES feature pages)" || gap "Frontend pages insufficient ($FE_PAGES)"

LAZY=$(count_grepr "React\.lazy|lazy\(\(\)" "$FRONTEND" "*.tsx")
[ "$LAZY" -gt 5 ] && pass "Lazy loading ($LAZY lazy imports)" || gap "Code splitting insufficient ($LAZY)"

EB_COUNT=$(count_grepr "ErrorBoundary" "$FRONTEND" "*.tsx")
[ "$EB_COUNT" -gt 5 ] && pass "Error boundaries ($EB_COUNT usages)" || gap "Error boundaries insufficient ($EB_COUNT)"

QUERY_TS=$(count_grepr "useQuery|useMutation" "$FRONTEND" "*.ts")
QUERY_TSX=$(count_grepr "useQuery|useMutation" "$FRONTEND" "*.tsx")
QUERY=$((QUERY_TS + QUERY_TSX))
[ "$QUERY" -gt 10 ] && pass "TanStack Query hooks ($QUERY usages)" || gap "TanStack Query underused ($QUERY)"

A11Y=$(count_grepr "aria-label|aria-live|role=" "$FRONTEND" "*.tsx")
[ "$A11Y" -gt 20 ] && pass "Accessibility attributes ($A11Y found)" || warn "Accessibility attributes low ($A11Y)"

# ── SPECIFIC NAMED COMPONENT CHECKS (the missing layer) ──────────────────────
# These prevent "quantity passes but named feature missing" false positives.
section "FRONTEND: Phase-specific components"

[ -f "$FRONTEND/features/org/OrgListPage.tsx" ]          && pass "OrgListPage exists"       || gap "OrgListPage MISSING — /org route has no list page"
[ -f "$FRONTEND/features/org/OrgPage.tsx" ]              && pass "OrgPage exists"           || gap "OrgPage MISSING — /org/:orgId route has no page"
[ -f "$FRONTEND/features/org/components/MissionCard.tsx" ] && pass "MissionCard exists"    || gap "MissionCard MISSING"
[ -f "$FRONTEND/features/org/components/MissionsList.tsx" ] && pass "MissionsList exists"  || gap "MissionsList MISSING"
[ -f "$FRONTEND/features/org/components/MissionDetail.tsx" ] && pass "MissionDetail exists" || gap "MissionDetail MISSING"
[ -f "$FRONTEND/features/org/components/CreateMissionDrawer.tsx" ] && pass "CreateMissionDrawer exists" || gap "CreateMissionDrawer MISSING"
[ -f "$FRONTEND/features/org/components/DepartmentTree.tsx" ] && pass "DepartmentTree exists" || gap "DepartmentTree MISSING"
[ -f "$FRONTEND/features/org/components/ActivityFeed.tsx" ]   && pass "ActivityFeed exists"  || gap "ActivityFeed MISSING"
grep -q "org/:orgId\|/org" "$FRONTEND/app/App.tsx" 2>/dev/null && pass "Org routes in App.tsx" || gap "Org routes MISSING from App.tsx"
grep -q '"/org"' "$FRONTEND/components/ui/Sidebar.tsx" 2>/dev/null && pass "Org nav link in Sidebar" || gap "Org nav link MISSING from Sidebar"
[ -f "$REPO_ROOT/agent-verse-frontend/e2e/org.spec.ts" ]      && pass "Org E2E spec exists"  || gap "Org E2E spec MISSING"

# ─── TEST COVERAGE ────────────────────────────────────────────────────────────
section "TEST COVERAGE"

BE_TESTS=$(find "$REPO_ROOT/agent-verse-backend/tests" -name "test_*.py" 2>/dev/null | wc -l | tr -d ' ')
[ "$BE_TESTS" -gt 20 ] && pass "Backend test files ($BE_TESTS)" || gap "Backend tests insufficient ($BE_TESTS)"

FE_TESTS=$(find "$FRONTEND" -name "*.test.tsx" -o -name "*.test.ts" 2>/dev/null | wc -l | tr -d ' ')
[ "$FE_TESTS" -gt 20 ] && pass "Frontend test files ($FE_TESTS)" || gap "Frontend tests insufficient ($FE_TESTS)"

# ─── SECURITY ────────────────────────────────────────────────────────────────
section "SECURITY"

SEC_TESTS=$(find "$REPO_ROOT/agent-verse-backend/tests" -name "test_*security*" -o -name "test_*access*" 2>/dev/null | wc -l | tr -d ' ')
[ "$SEC_TESTS" -gt 0 ] && pass "Security test files ($SEC_TESTS)" || gap "Security tests MISSING — add tests/security/"

RLS_TESTS=$(grep -r "tenant_a\|tenant_b\|other_tenant\|cross.*tenant" $REPO_ROOT/agent-verse-backend/tests --include="*.py" 2>/dev/null | wc -l | tr -d ' ')
[ "$RLS_TESTS" -gt 5 ] && pass "Tenant isolation tests ($RLS_TESTS)" || warn "Tenant isolation tests sparse ($RLS_TESTS)"

# ─── INSTRUMENTATION ─────────────────────────────────────────────────────────
section "OBSERVABILITY"

OTEL_SPANS=$(count_grep "start_as_current_span" "$BACKEND")
[ "$OTEL_SPANS" -gt 30 ] && pass "OTel spans ($OTEL_SPANS)" || warn "OTel coverage sparse ($OTEL_SPANS)"

STRUCTLOG=$(count_grep "structlog\|log\.(info\|error\|warning)" "$BACKEND")
[ "$STRUCTLOG" -gt 50 ] && pass "Structured logging ($STRUCTLOG)" || warn "Logging coverage low ($STRUCTLOG)"

PROMETHEUS=$(count_grep "create_counter\|create_histogram\|prometheus" "$BACKEND")
[ "$PROMETHEUS" -gt 5 ] && pass "Prometheus metrics ($PROMETHEUS)" || warn "Prometheus metrics low ($PROMETHEUS)"

# ─── SUMMARY ─────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}╔══════════════════════════════════╗${NC}"
echo -e "${BOLD}║   AUDIT SUMMARY                  ║${NC}"
echo -e "${BOLD}╠══════════════════════════════════╣${NC}"
echo -e "${BOLD}║  ✅ Passed:   $(printf '%4d' $PASSES)                ║${NC}"
echo -e "${BOLD}║  ⚠️  Warnings: $(printf '%4d' $WARNINGS)                ║${NC}"
echo -e "${BOLD}║  ❌ Gaps:     $(printf '%4d' $GAPS)                ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════╝${NC}"

if [ "$GAPS" -gt 0 ]; then
  echo ""
  echo -e "${RED}${BOLD}IMPLEMENTATION INCOMPLETE: $GAPS gaps found.${NC}"
  echo -e "${YELLOW}Run: @implementation-auditor to implement all gaps${NC}"
  echo -e "${YELLOW}Or:  use .github/prompts/reaudit-implementation.prompt.md${NC}"
  exit 1
elif [ "$WARNINGS" -gt 0 ]; then
  echo ""
  echo -e "${YELLOW}${BOLD}WARNINGS: $WARNINGS items below recommended levels.${NC}"
  echo -e "${YELLOW}Consider improving before next release.${NC}"
  exit 0
else
  echo ""
  echo -e "${GREEN}${BOLD}ALL CHECKS PASSED! Implementation matches spec.${NC}"
  exit 0
fi
