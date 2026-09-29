#!/bin/sh
# Refresh wave7-status.md from the wave-7 worktrees and commit it if it changed.
# Safe to re-run; only touches this one file. Usage: sh update_wave7_status.sh
cd "$(git -C "$(dirname "$0")" rev-parse --show-toplevel)" || exit 1
out=docs/audits/2026-09-29-reaudit/wave7-status.md
{
  echo "# Wave 7 status (auto-refreshed)"
  echo
  echo "Last refresh: $(date -u '+%Y-%m-%d %H:%M UTC'). See HANDOFF.md section 4 for what each item means."
  echo
  for b in fix/hitl-estop-security fix/audit-correctness-defects fix/fake-success-defects fix/audit-llm-ssrf-ingest; do
    git rev-parse --verify -q "$b" >/dev/null || { echo "## $b — branch not found"; echo; continue; }
    wt=$(git worktree list --porcelain | awk -v b="refs/heads/$b" '/^worktree /{w=$2} $0=="branch "b{print w}')
    merged=$(git cherry main "$b" | grep -c '^+')
    echo "## $b — $merged commit(s) not yet on main"
    echo
    git log --format='- `%h` %s (%cr)' main.."$b"
    if [ -n "$wt" ]; then
      echo
      echo "Worktree \`$wt\`: $(git -C "$wt" status --short | grep -vc '^??') tracked file(s) with uncommitted changes."
    fi
    echo
  done
} > "$out.tmp"
if ! cmp -s "$out.tmp" "$out" 2>/dev/null || ! git ls-files --error-unmatch "$out" >/dev/null 2>&1; then
  # Ignore the timestamp line when deciding whether anything really changed.
  if [ -f "$out" ] && [ "$(grep -v '^Last refresh' "$out.tmp")" = "$(grep -v '^Last refresh' "$out")" ]; then
    rm -f "$out.tmp"; exit 0
  fi
  mv "$out.tmp" "$out"
  git add "$out" && git commit -q -m "docs(audit): refresh wave 7 status" -- "$out" >/dev/null 2>&1 || true
else
  rm -f "$out.tmp"
fi
