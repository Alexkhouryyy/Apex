#!/bin/bash
# Run every scripts/check_*.cjs / *.mjs page check. A failure is retried once
# (some browser checks are timing-sensitive); one that still fails is run on
# origin/main too, so the report separates NEW failures (this change) from ones
# main already has in this environment. Exit 1 only for NEW failures.
#   bash .claude/skills/ship-apex/ui_checks.sh            # all checks
#   bash .claude/skills/ship-apex/ui_checks.sh companion  # names containing "companion"
set -uo pipefail
ROOT="$(git rev-parse --show-toplevel)"; cd "$ROOT"
UI="$HOME/.cache/apex-ui-check/node_modules"            # where the session-start hook installs them
case ":${NODE_PATH:-}:" in *":$UI:"*) ;; *) export NODE_PATH="$UI${NODE_PATH:+:$NODE_PATH}";; esac
export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-/opt/pw-browsers}"
if ! node -e "require('jsdom')" 2>/dev/null; then
  echo "jsdom is missing (NODE_PATH=$NODE_PATH). Run: CLAUDE_CODE_REMOTE=true .claude/hooks/session-start.sh"; exit 2
fi
FILTER="${1:-}"
LOG="$(mktemp -d)"
run() { (cd "$1" && timeout 120 node "$2" >"$LOG/out.txt" 2>&1); }
pass=0; failed=()
for f in scripts/check_*.cjs scripts/check_*.mjs; do
  [ -n "$FILTER" ] && [[ "$f" != *"$FILTER"* ]] && continue
  if run "$ROOT" "$f" || run "$ROOT" "$f"; then pass=$((pass+1)); else
    failed+=("$f"); cp "$LOG/out.txt" "$LOG/$(basename "$f").txt"; fi
done
new=(); old=()
if [ ${#failed[@]} -gt 0 ]; then
  git fetch -q origin main 2>/dev/null || true
  BASE="$LOG/main"; git worktree add -q --detach "$BASE" origin/main
  for f in "${failed[@]}"; do
    if run "$BASE" "$f" || run "$BASE" "$f"; then new+=("$f"); else old+=("$f"); fi
  done
  git worktree remove --force "$BASE"
fi
echo "page checks: $pass passed, ${#old[@]} fail on main too, ${#new[@]} NEW failures"
for f in "${old[@]}"; do echo "  also fails on main (environment): $f"; done
for f in "${new[@]}"; do echo "  NEW: $f"; grep -v '^\s*at ' "$LOG/$(basename "$f").txt" | head -4 | sed 's/^/      /'; done
[ ${#new[@]} -eq 0 ]
