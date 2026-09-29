#!/bin/sh
# Run the full unit suite + least-privilege e2e on the current checkout and commit
# the outcome to validation-status.md (so the result survives a stopped session).
root="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"; cd "$root/agent-verse-backend" || exit 1
out="$root/docs/audits/2026-09-29-reaudit/validation-status.md"
log=$(mktemp -d)
head_sha=$(git rev-parse --short HEAD)
uv run pytest -q --no-cov -p no:cacheprovider -m "not integration and not slow" --ignore=tests/e2e_full --ignore=tests/real_e2e > "$log/unit.txt" 2>&1
env -u NVIDIA_API_KEY -u ONPREM_ENABLED DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock TESTCONTAINERS_RYUK_DISABLED=true E2E_LEAST_PRIVILEGE=1 uv run pytest tests/e2e_full -q --no-cov -p no:cacheprovider > "$log/e2e.txt" 2>&1
{
  echo "# Validation status (auto-written by run_validation.sh)"
  echo
  echo "Code: \`$head_sha\` · finished $(date -u '+%Y-%m-%d %H:%M UTC')"
  echo
  echo "- Unit suite: $(tail -1 "$log/unit.txt")"
  echo "- Least-privilege e2e: $(tail -1 "$log/e2e.txt")"
  echo
  echo "Failures (if any):"
  echo
  grep -hE '^(FAILED|ERROR)' "$log/unit.txt" "$log/e2e.txt" | sed 's/ - .*//' | head -60 | sed 's/^/- /'
} > "$out"
cd "$root" && git add "$out" && git commit -q -m "docs(audit): validation status for $head_sha" -- "$out"
