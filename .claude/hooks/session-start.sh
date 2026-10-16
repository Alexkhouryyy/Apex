#!/bin/bash
# Cloud sessions start from a fresh container: install what Apex's checks need,
# so `python -m pytest` and the scripts/check_*.cjs page checks just run.
# Idempotent; local machines are left alone.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi
cd "${CLAUDE_PROJECT_DIR:-$(dirname "$0")/../..}"

# Python: the app and the test suite (pytest is in requirements.txt), as CI does.
if ! python -m pip install -q --disable-pip-version-check --root-user-action=ignore -r requirements.txt -r requirements-research.txt; then
  # A distro-installed pip/setuptools can refuse to be upgraded in place.
  python -m pip install -q --disable-pip-version-check --root-user-action=ignore --ignore-installed pip setuptools
  python -m pip install -q --disable-pip-version-check --root-user-action=ignore -r requirements.txt -r requirements-research.txt
fi

# Node: jsdom for the page checks, Playwright for the *_browser checks. Kept
# outside the repo (NODE_PATH), like CI's $RUNNER_TEMP/apex-ui-check.
UI_DIR="$HOME/.cache/apex-ui-check"
# Playwright must match the Chromium preinstalled in /opt/pw-browsers
# (chromium-1194 = Playwright 1.56); never download browsers here.
PLAYWRIGHT_VERSION=1.56.1
mkdir -p "$UI_DIR"
if [ ! -d "$UI_DIR/node_modules/jsdom" ] || ! grep -q "\"version\": \"$PLAYWRIGHT_VERSION\"" "$UI_DIR/node_modules/playwright/package.json" 2>/dev/null; then
  PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm install -s --prefix "$UI_DIR" --no-audit --no-fund \
    jsdom@30.0.1 "playwright@$PLAYWRIGHT_VERSION"
fi

if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  {
    echo "export NODE_PATH=\"$UI_DIR/node_modules\${NODE_PATH:+:\$NODE_PATH}\""
    echo "export PLAYWRIGHT_BROWSERS_PATH=\"\${PLAYWRIGHT_BROWSERS_PATH:-/opt/pw-browsers}\""
  } >> "$CLAUDE_ENV_FILE"
fi
echo "Apex check tools ready: pytest, jsdom, playwright@$PLAYWRIGHT_VERSION (NODE_PATH=$UI_DIR/node_modules)"
