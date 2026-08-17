---
description: "Generate a new Alembic migration with proper indexes, RLS, and zero-downtime strategy"
---

# Generate Database Migration

Generate a production-safe Alembic migration following AgentVerse conventions.

## Migration Details
- **What**: {{description}}
- **Tables affected**: {{tables}}
- **Type**: {{type}}  <!-- new_table | add_column | add_index | modify_column | drop_column -->

## Rules

### New Table
```python
def upgrade():
    op.create_table(
        "table_name",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", pg.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        # domain columns...
    )
    # FK indexes (CONCURRENTLY — non-locking):
    op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_table_tenant_id ON table_name(tenant_id)")
    op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_table_status ON table_name(status)")
    # RLS:
    op.execute("ALTER TABLE table_name ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE table_name FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation ON table_name
        USING (tenant_id = current_setting('app.tenant_id')::uuid)
    """)
```

### Add Column (Zero-Downtime)
```python
def upgrade():
    # Phase 1: Add as nullable (instant — no lock)
    op.add_column("table", sa.Column("new_col", sa.String(200), nullable=True))
    # Phase 2 happens in NEXT migration after backfill
```

### Add Index (Non-Locking)
```python
def upgrade():
    # Always CONCURRENTLY — never lock the table
    op.execute(
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_table_column ON table(column)"
    )
```

## Generate

Give me the complete migration file with:
1. Correct `revision` and `down_revision` (I'll tell you the last revision)
2. `upgrade()` with all DDL changes
3. `downgrade()` that fully reverses the changes
4. All indexes using `CONCURRENTLY`
5. RLS policies if tenant-scoped table

Last migration revision: {{last_revision}}
