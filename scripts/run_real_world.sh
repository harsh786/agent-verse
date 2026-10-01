#!/usr/bin/env bash
# Run the AgentVerse REAL-WORLD scenario suite against the live local Docker stack,
# then write a JSON + markdown pass/fail report.
#
#   scripts/run_real_world.sh <report-dir>
#
# Credentials (never printed): AGENTVERSE_API_KEY, or AGENTVERSE_TENANT_FILE pointing
# at a JSON file with an "api_key" field. Optional:
#   AGENTVERSE_BASE_URL (default http://localhost:8000)  BASE_URL (frontend, default :5173)
#   RW_PYTEST_ARGS  extra pytest args (e.g. "-k hitl")   RW_SKIP_UI=1  skip Playwright
#   RW_HITL_PERSIST=0 skip WF-HITL-RESTART               RW_RSS_URL / RW_MONGO_URI / RW_REDIS_URL
set -uo pipefail

OUT="${1:?usage: $0 <report-dir>}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"

if [[ -z "${AGENTVERSE_API_KEY:-}" && -z "${AGENTVERSE_TENANT_FILE:-}" ]]; then
  echo "set AGENTVERSE_API_KEY or AGENTVERSE_TENANT_FILE" >&2
  exit 2
fi

BACKEND_RESULTS="$OUT/backend_results.jsonl"
PW_JSON="$OUT/playwright_results.json"
rm -f "$BACKEND_RESULTS" "$PW_JSON"

echo "== backend real-world scenarios"
(
  cd "$ROOT/agent-verse-backend" &&
  AGENTVERSE_REAL_WORLD=1 RW_RESULTS_FILE="$BACKEND_RESULTS" \
    uv run pytest tests/real_world --no-cov -p no:cacheprovider \
      -W default -q --tb=short ${RW_PYTEST_ARGS:-} 2>&1 | tee "$OUT/backend_pytest.log"
)
BACKEND_RC=${PIPESTATUS[0]}

PW_RC=0
if [[ "${RW_SKIP_UI:-0}" != "1" ]]; then
  echo "== Playwright real-world UI spec"
  (
    cd "$ROOT/agent-verse-frontend" &&
    REAL_WORLD=1 RW_PW_JSON="$PW_JSON" \
      npx playwright test --config=playwright.real-world.config.ts 2>&1 \
      | tee "$OUT/playwright.log"
  )
  PW_RC=${PIPESTATUS[0]}
fi

echo "== report"
(
  cd "$ROOT/agent-verse-backend" &&
  uv run python -m tests.real_world.report "$BACKEND_RESULTS" "$PW_JSON" "$OUT"
)
echo "report: $OUT/real_world_report.md  $OUT/real_world_report.json"

# Exit non-zero when anything failed (xfail does not count).
if [[ $BACKEND_RC -ne 0 || $PW_RC -ne 0 ]]; then exit 1; fi
exit 0
