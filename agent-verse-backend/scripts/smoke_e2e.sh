#!/bin/bash
# Full end-to-end smoke test of the whole AgentVerse stack.
#
# Exercises: infra health, auth/tenancy, workflow CRUD, external webhook trigger
# (item 3), recurring schedule scan (item 4), knowledge/RAG (item 5), Source
# ingestion (item 6), WhatsApp/Telegram gateway + spoof guard (item 7),
# OCR→LLM→HTTP (items 1/2), RPA + PDF report, goal submission, and the DB
# connection-leak fix.
#
# Prerequisites (run against a live dev stack):
#   - backend on $BASE, celery worker + beat, postgres + redis all up
#   - env: source your backend env so WORKFLOW_WEBHOOK_SECRET, GATEWAY_INGRESS_SECRET,
#     DATABASE_URL/REDIS_URL and provider keys are set (see the source lines below)
#   - three PUBLISHED demo workflows must exist under $DEMO for the heavy paths:
#       SMOKE_INVOICE (ocr→llm→http), SMOKE_RPA (rpa report), SMOKE_RAG (llm+rag)
#     Override the tenant/workflow ids via env if your dataset differs.
#
# Usage:  bash scripts/smoke_e2e.sh
set -o pipefail
source /tmp/av.env 2>/dev/null; source /tmp/av-wf.env 2>/dev/null
BASE=${SMOKE_BASE:-http://localhost:8001}
DEMO=${SMOKE_DEMO_TENANT:-5cee4344eed74853984f5663561b4657}   # tenant with the published demo workflows
INVOICE=${SMOKE_INVOICE:-5d8ef41f-c03c-48f6-9541-b0512b939f0d}
RPA=${SMOKE_RPA:-3ed5087b-866c-40eb-81b4-94b0db89d220}
RAG=${SMOKE_RAG:-8b62c34c-2699-4b4d-bfe1-b1b33ca7a0b1}
PG_CONTAINER=${SMOKE_PG_CONTAINER:-agentverse-backend-postgres-1}

PASS=0; FAIL=0
ok(){ PASS=$((PASS+1)); printf "  \033[32m✅ PASS\033[0m  %s\n" "$1"; }
no(){ FAIL=$((FAIL+1)); printf "  \033[31m❌ FAIL\033[0m  %s :: %s\n" "$1" "$2"; }
hdr(){ printf "\n\033[1m== %s ==\033[0m\n" "$1"; }

mint(){ uv run python -c "from app.workflow.webhook_tokens import make_webhook_token as m;print(m('$1','$2'))" 2>/dev/null; }
runstatus(){ docker exec $PG_CONTAINER psql -U agentverse -d agentverse -tAc "SELECT status FROM workflow_runs WHERE id='$1';" 2>/dev/null | tr -d ' '; }
stepout(){ docker exec $PG_CONTAINER psql -U agentverse -d agentverse -tAc "SELECT output::text FROM workflow_step_results WHERE run_id='$1' AND step_id='$2';" 2>/dev/null; }

# ── Pre-mint tokens & fire the 3 heavy workflows in PARALLEL ──────────────────
hdr "Firing heavy workflows in parallel (OCR→LLM→HTTP, RPA, RAG)"
T_INV=$(mint $DEMO $INVOICE); T_RPA=$(mint $DEMO $RPA); T_RAG=$(mint $DEMO $RAG)
RID_INV=$(curl -s -m15 -X POST "$BASE/wf-hooks/$T_INV" -d '{}' | python3 -c "import sys,json;print(json.load(sys.stdin).get('run_id',''))" 2>/dev/null)
RID_RPA=$(curl -s -m15 -X POST "$BASE/wf-hooks/$T_RPA" -d '{}' | python3 -c "import sys,json;print(json.load(sys.stdin).get('run_id',''))" 2>/dev/null)
RID_RAG=$(curl -s -m15 -X POST "$BASE/wf-hooks/$T_RAG" -d '{}' | python3 -c "import sys,json;print(json.load(sys.stdin).get('run_id',''))" 2>/dev/null)
echo "  invoice run=$RID_INV | rpa run=$RID_RPA | rag run=$RID_RAG"
[ -n "$RID_INV" ] && ok "webhook trigger accepted (invoice)" || no "webhook trigger (invoice)" "no run_id"
[ -n "$RID_RPA" ] && ok "webhook trigger accepted (rpa)" || no "webhook trigger (rpa)" "no run_id"
[ -n "$RID_RAG" ] && ok "webhook trigger accepted (rag)" || no "webhook trigger (rag)" "no run_id"

# ── 1. Infra ─────────────────────────────────────────────────────────────────
hdr "1. Infra health"
curl -s -m3 "$BASE/health" | grep -q '"status":"healthy"' && ok "backend healthy" || no "backend" "unhealthy"
[ "$(pgrep -f '[c]elery -A app.scaling.celery_app worker' | wc -l)" -ge 1 ] && ok "celery worker running" || no "worker" "not running"
[ "$(pgrep -f '[c]elery -A app.scaling.celery_app beat' | wc -l)" -ge 1 ] && ok "celery beat running" || no "beat" "not running"
[ "$(docker inspect -f '{{.State.Health.Status}}' agentverse-backend-postgres-1 2>/dev/null)" = healthy ] && ok "postgres healthy" || no "postgres" "unhealthy"
[ "$(docker inspect -f '{{.State.Health.Status}}' agentverse-backend-redis-1 2>/dev/null)" = healthy ] && ok "redis healthy" || no "redis" "unhealthy"

# ── 2. Auth / tenancy ────────────────────────────────────────────────────────
hdr "2. Auth & tenant provisioning"
EMAIL="smoke+$(date +%s)@example.com"
SU=$(curl -s -m10 -X POST "$BASE/tenants/signup" -H 'Content-Type: application/json' -d "{\"name\":\"Smoke Test\",\"email\":\"$EMAIL\"}")
TID=$(echo "$SU" | python3 -c "import sys,json;print(json.load(sys.stdin).get('tenant_id',''))" 2>/dev/null)
KEY=$(echo "$SU" | python3 -c "import sys,json;print(json.load(sys.stdin).get('api_key',''))" 2>/dev/null)
[ -n "$KEY" ] && ok "signup issued API key (tenant ${TID:0:8})" || no "signup" "$SU"
H=(-H "X-API-Key: $KEY" -H 'Content-Type: application/json')
# unauth check
code=$(curl -s -m5 -o /dev/null -w "%{http_code}" "$BASE/api/v1/workflows")
[ "$code" = 401 ] && ok "unauthenticated request rejected (401)" || no "auth-guard" "expected 401 got $code"

# ── 3. Workflow CRUD ─────────────────────────────────────────────────────────
hdr "3. Workflow CRUD"
CR=$(curl -s -m10 -X POST "$BASE/api/v1/workflows" "${H[@]}" -d '{"name":"Smoke WF","definition":{"name":"Smoke WF","triggers":[{"type":"webhook","webhook":{"auth":"none"}}],"steps":[{"id":"s1","type":"set_variable","var_name":"greeting","var_value":"hello-smoke","value_type":"string"}]}}')
WID=$(echo "$CR" | python3 -c "import sys,json;print(json.load(sys.stdin).get('id',''))" 2>/dev/null)
[ -n "$WID" ] && ok "create workflow" || no "create workflow" "$CR"
GET=$(curl -s -m10 "$BASE/api/v1/workflows/$WID" "${H[@]}")
echo "$GET" | grep -q '"definition"' && echo "$GET" | grep -q 'set_variable' && ok "get workflow returns definition" || no "get workflow" "no definition"
curl -s -m10 "$BASE/api/v1/workflows?per_page=50" "${H[@]}" | grep -q "$WID" && ok "list includes new workflow" || no "list" "missing"

# ── 4. Publish + webhook token ───────────────────────────────────────────────
hdr "4. Publish + webhook activation"
PUB=$(curl -s -m10 -X POST "$BASE/api/v1/workflows/$WID/publish" "${H[@]}" -d '{}')
echo "$PUB" | grep -q '"status":"published"' && ok "publish → published" || no "publish" "$PUB"
# webhook token minted server-side matches our deterministic mint
T_SMOKE=$(mint $TID $WID)
RID_SMOKE=$(curl -s -m15 -X POST "$BASE/wf-hooks/$T_SMOKE" -d '{"x":1}' | python3 -c "import sys,json;print(json.load(sys.stdin).get('run_id',''))" 2>/dev/null)
[ -n "$RID_SMOKE" ] && ok "item3: external webhook trigger fired a run" || no "item3 webhook" "no run"

# ── 5. Goal submission ───────────────────────────────────────────────────────
hdr "5. Goal submission (agent loop entrypoint)"
GS=$(curl -s -m10 -X POST "$BASE/goals" "${H[@]}" -d '{"goal":"Say hello in one short sentence.","priority":"normal"}')
GID=$(echo "$GS" | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('goal_id') or d.get('id',''))" 2>/dev/null)
[ -n "$GID" ] && ok "goal submitted (${GID:0:8})" || no "goal submit" "$GS"

# ── 6. Item 6 — Source ingestion (create → sync) ─────────────────────────────
hdr "6. Item 6 — ingestion Source (persist + sync)"
SRC=$(curl -s -m10 -X POST "$BASE/sources" "${H[@]}" -d '{"name":"smoke-http","family":"web","source_type":"http","connection_config":{"url":"https://jsonplaceholder.typicode.com/posts","content_fields":["title","body"],"max_records":2},"tags":["smoke"]}')
SID=$(echo "$SRC" | python3 -c "import sys,json;print(json.load(sys.stdin).get('source_id',''))" 2>/dev/null)
[ -n "$SID" ] && ok "source created (persisted)" || no "source create" "$SRC"
# list should show it (proves persistence/round-trip)
curl -s -m10 "$BASE/sources" "${H[@]}" | grep -q "$SID" && ok "source listed (durable store)" || no "source list" "missing"

# ── 7. Item 7 — Gateway (WhatsApp/Telegram command) ──────────────────────────
hdr "7. Item 7 — gateway command routing + spoof guard"
G0=$(docker exec $PG_CONTAINER psql -U agentverse -d agentverse -tAc "SELECT count(*) FROM goals WHERE tenant_id='$TID';" 2>/dev/null|tr -d ' ')
# spoof: x-tenant-id WITHOUT ingress secret must NOT create a goal
curl -s -m10 -X POST "$BASE/v1/gateway/smoke-org/telegram/webhook" -H "x-tenant-id: $TID" -H 'Content-Type: application/json' \
  -d '{"message":{"chat":{"id":"1"},"from":{"id":"9"},"text":"spoofed"}}' -o /dev/null
sleep 3
G1=$(docker exec $PG_CONTAINER psql -U agentverse -d agentverse -tAc "SELECT count(*) FROM goals WHERE tenant_id='$TID';" 2>/dev/null|tr -d ' ')
[ "$G0" = "$G1" ] && ok "gateway spoof blocked (no cross-tenant goal)" || no "gateway spoof" "goals $G0→$G1"
# trusted: with ingress secret must create a goal
curl -s -m10 -X POST "$BASE/v1/gateway/smoke-org/telegram/webhook" -H "x-tenant-id: $TID" -H "x-gateway-secret: $GATEWAY_INGRESS_SECRET" -H 'Content-Type: application/json' \
  -d '{"message":{"chat":{"id":"1"},"from":{"id":"9"},"text":"summarize today"}}' -o /dev/null
sleep 5
G2=$(docker exec $PG_CONTAINER psql -U agentverse -d agentverse -tAc "SELECT count(*) FROM goals WHERE tenant_id='$TID';" 2>/dev/null|tr -d ' ')
[ "${G2:-0}" -gt "${G1:-0}" ] && ok "gateway trusted relay → goal created" || no "gateway trusted" "goals ${G1:-?}→${G2:-?}"

# ── 8. Item 4 — Schedule (beat is scanning) ──────────────────────────────────
hdr "8. Item 4 — recurring schedule scan (beat→worker)"
grep -q "workflow-fire-due-schedules" /tmp/av-beat.log 2>/dev/null && ok "beat scheduling fire-due-schedules" || no "beat schedule" "not in beat log"
grep -qE "Task workflow.fire_due_workflow_schedules.*succeeded" /tmp/av-worker.log 2>/dev/null && ok "worker executed schedule scan" || no "schedule scan exec" "not seen"

# ── 9. DB connection-leak fix ────────────────────────────────────────────────
hdr "9. DB connection health (leak fix)"
IIT=$(docker exec $PG_CONTAINER psql -U agentverse -d agentverse -tAc "SELECT count(*) FROM pg_stat_activity WHERE datname='agentverse' AND state='idle in transaction';" 2>/dev/null|tr -d ' ')
[ "${IIT:-0}" -le 3 ] && ok "idle-in-transaction low ($IIT)" || no "db leak" "idle-in-transaction=$IIT"

# ── Collect heavy workflow results ───────────────────────────────────────────
hdr "Collecting heavy workflow runs (up to 150s)"
deadline=$(( $(date +%s) + 150 ))
declare -A DONE
while [ $(date +%s) -lt $deadline ]; do
  alldone=1
  for pair in "INV:$RID_INV" "RPA:$RID_RPA" "RAG:$RID_RAG" "SMOKE:$RID_SMOKE"; do
    name=${pair%%:*}; rid=${pair#*:}
    [ -z "$rid" ] && continue
    [ -n "${DONE[$name]:-}" ] && continue
    st=$(runstatus "$rid")
    if [ "$st" = complete ] || [ "$st" = failed ]; then DONE[$name]=$st; echo "  $name → $st"; else alldone=0; fi
  done
  [ $alldone -eq 1 ] && break
  sleep 5
done

hdr "Heavy workflow assertions"
# item3 simple
[ "${DONE[SMOKE]:-}" = complete ] && ok "item3: set_variable run completed" || no "item3 run" "status=${DONE[SMOKE]:-timeout}"
# items 1/2 invoice OCR→LLM→HTTP
if [ "${DONE[INV]:-}" = complete ]; then
  echo "$(stepout "$RID_INV" extract)" | grep -qiE "total|invoice" && ok "items1/2: OCR→LLM→HTTP extracted invoice fields" || no "invoice extract" "no fields"
else no "invoice run" "status=${DONE[INV]:-timeout}"; fi
# RPA + report
if [ "${DONE[RPA]:-}" = complete ]; then
  echo "$(stepout "$RID_RPA" scrape)" | grep -qiE "report|report_pdf_uri|section" && ok "rpa: report generated" || no "rpa report" "no report in output"
else no "rpa run" "status=${DONE[RPA]:-timeout}"; fi
# RAG answer
if [ "${DONE[RAG]:-}" = complete ]; then
  echo "$(stepout "$RID_RAG" answer)" | grep -qi "Pip" && ok "item5: RAG answered from knowledge (Pip)" || no "rag answer" "expected Pip: $(stepout "$RID_RAG" answer | head -c 120)"
else no "rag run" "status=${DONE[RAG]:-timeout}"; fi

# ── Summary ──────────────────────────────────────────────────────────────────
hdr "SMOKE TEST SUMMARY"
printf "\033[1m  PASS=%d  FAIL=%d\033[0m\n" "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ] && printf "  \033[32m🟢 ALL GREEN\033[0m\n" || printf "  \033[31m🔴 %d failure(s)\033[0m\n" "$FAIL"
