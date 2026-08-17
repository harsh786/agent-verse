#!/usr/bin/env bash
# setup-hooks.sh — Install AgentVerse git hooks for this repo
# Run once after cloning: bash .githooks/setup-hooks.sh

set -euo pipefail

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'

echo -e "${YELLOW}🔧 Installing AgentVerse git hooks...${NC}"

# Point git to our hooks directory
git config core.hooksPath .githooks

# Make all hooks executable
chmod +x .githooks/pre-commit
chmod +x .githooks/pre-push
chmod +x .githooks/commit-msg
chmod +x .githooks/post-merge
chmod +x .githooks/prepare-commit-msg

echo -e "${GREEN}✅ Git hooks installed from .githooks/${NC}"
echo ""
echo "Hooks active:"
echo "  pre-commit        → ruff, mypy (changed files), secret scan, migration safety"
echo "  commit-msg        → Conventional Commits format enforcement"
echo "  prepare-commit-msg→ Auto-fills scope from branch name"
echo "  pre-push          → full unit tests + typecheck (integration on main)"
echo "  post-merge        → auto-sync deps + migration reminder"
echo ""
echo -e "${YELLOW}ℹ️  To bypass a hook temporarily (emergencies only):${NC}"
echo "  git commit --no-verify"
echo "  git push --no-verify"
