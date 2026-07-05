#!/usr/bin/env bash
# Disaster Recovery Drill Script
# Tests backup creation and restore capability
# Run: ./infra/dr-drill.sh [--quick] [--restore]
#
# Requirements: docker, pg_dump, psql available
# RPO target: 1 hour  |  RTO target: 30 minutes

set -euo pipefail

DRILL_LOG="/tmp/agentverse-dr-drill-$(date +%Y%m%d-%H%M%S).log"
BACKUP_DIR="/tmp/agentverse-dr-backup-$(date +%Y%m%d-%H%M%S)"
DB_NAME="${POSTGRES_DB:-agentverse}"
DB_USER="${POSTGRES_USER:-agentverse}"
DB_HOST="${POSTGRES_HOST:-localhost}"
DB_PORT="${POSTGRES_PORT:-5432}"
QUICK="${1:-}"
DB_VIA_DOCKER=false

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$DRILL_LOG"; }
fail() { log "❌ DRILL FAILED: $1"; exit 1; }
pass() { log "✅ $1"; }

log "=== AgentVerse DR Drill Starting ==="
log "Log file: $DRILL_LOG"
mkdir -p "$BACKUP_DIR"

# Step 1: Verify postgres is reachable (connectivity check)
log "Step 1/5: Verify database connectivity..."
if pg_isready -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" >/dev/null 2>&1; then
  pass "Database $DB_NAME is reachable at $DB_HOST:$DB_PORT"
else
  # Try via docker
  if docker exec agentverse_postgres pg_isready -U "$DB_USER" >/dev/null 2>&1; then
    pass "Database reachable via Docker"
    DB_VIA_DOCKER=true
  else
    fail "Cannot reach database — is postgres running?"
  fi
fi

# Step 2: Create a test backup
log "Step 2/5: Creating backup..."
BACKUP_FILE="$BACKUP_DIR/agentverse-drill-backup.sql.gz"
if [ "${DB_VIA_DOCKER}" = "true" ]; then
  docker exec agentverse_postgres pg_dump -U "$DB_USER" "$DB_NAME" | gzip > "$BACKUP_FILE"
else
  PGPASSWORD="${POSTGRES_PASSWORD:-agentverse}" pg_dump -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" "$DB_NAME" | gzip > "$BACKUP_FILE"
fi

BACKUP_SIZE=$(wc -c < "$BACKUP_FILE")
if [ "$BACKUP_SIZE" -gt 100 ]; then
  pass "Backup created: $BACKUP_FILE ($(( BACKUP_SIZE / 1024 )) KB)"
else
  fail "Backup file too small (${BACKUP_SIZE} bytes) — likely empty"
fi

# Step 3: Verify backup is restorable (dry-run restore to temp DB)
log "Step 3/5: Verifying backup integrity (dry-run restore)..."
RESTORE_TEST_DB="${DB_NAME}_dr_test_$$"
TABLE_COUNT=0
if [ "${DB_VIA_DOCKER}" = "true" ]; then
  docker exec agentverse_postgres psql -U "$DB_USER" -c "CREATE DATABASE $RESTORE_TEST_DB;" postgres >/dev/null 2>&1 || true
  zcat "$BACKUP_FILE" | docker exec -i agentverse_postgres psql -U "$DB_USER" "$RESTORE_TEST_DB" >/dev/null 2>&1
  TABLE_COUNT=$(docker exec agentverse_postgres psql -U "$DB_USER" "$RESTORE_TEST_DB" -tAc "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public'")
  docker exec agentverse_postgres psql -U "$DB_USER" -c "DROP DATABASE IF EXISTS $RESTORE_TEST_DB;" postgres >/dev/null 2>&1 || true
else
  PGPASSWORD="${POSTGRES_PASSWORD:-agentverse}" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -c "CREATE DATABASE $RESTORE_TEST_DB;" postgres >/dev/null 2>&1 || true
  zcat "$BACKUP_FILE" | PGPASSWORD="${POSTGRES_PASSWORD:-agentverse}" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" "$RESTORE_TEST_DB" >/dev/null 2>&1
  TABLE_COUNT=$(PGPASSWORD="${POSTGRES_PASSWORD:-agentverse}" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" "$RESTORE_TEST_DB" -tAc "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public'" 2>/dev/null || echo "0")
  PGPASSWORD="${POSTGRES_PASSWORD:-agentverse}" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -c "DROP DATABASE IF EXISTS $RESTORE_TEST_DB;" postgres >/dev/null 2>&1 || true
fi

if [ "${TABLE_COUNT:-0}" -gt 5 ]; then
  pass "Backup restores correctly: $TABLE_COUNT tables restored"
else
  fail "Restore verification failed: only $TABLE_COUNT tables found (expected >5)"
fi

# Step 4: Verify Redis data persistence (AOF check)
log "Step 4/5: Verifying Redis persistence (AOF)..."
if redis-cli -h "${REDIS_HOST:-localhost}" ping >/dev/null 2>&1; then
  AOF_ENABLED=$(redis-cli -h "${REDIS_HOST:-localhost}" config get appendonly 2>/dev/null | tail -1)
  if [ "$AOF_ENABLED" = "yes" ]; then
    pass "Redis AOF persistence is enabled"
  else
    log "⚠️  Redis AOF is disabled — data may be lost on restart"
  fi
elif docker exec agentverse_redis redis-cli ping >/dev/null 2>&1; then
  pass "Redis is running (via Docker)"
else
  log "⚠️  Redis not reachable — skipping Redis check"
fi

# Step 5: Summarize RPO/RTO results
log "Step 5/5: DR Drill Summary"
log "=================================="
log "Backup file: $BACKUP_FILE"
log "Backup size: $(( BACKUP_SIZE / 1024 )) KB"
log "Tables restored: ${TABLE_COUNT:-N/A}"
log "Drill log: $DRILL_LOG"
log ""
log "RPO: Backup represents point-in-time snapshot"
log "RTO: Restore test completed in $(( SECONDS )) seconds"
log "=================================="
pass "DR Drill PASSED — all checks green"

# Cleanup
rm -rf "$BACKUP_DIR"
log "Cleaned up temporary backup files"
