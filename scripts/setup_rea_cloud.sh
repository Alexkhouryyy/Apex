#!/usr/bin/env bash
set -euo pipefail

REA_PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
node -e '
const version = process.versions.node;
const [major, minor] = version.split(".").map(Number);
if (!/^\d+\.\d+\.\d+$/.test(version) ||
    !(major === 22 && minor >= 19 || major === 24 && minor >= 11 || major >= 26)) {
  console.error("REA requires Node 22.19+, 24.11+, or 26+. Current: " + version);
  process.exit(1);
}'

npm --cache "${REA_NPM_CACHE:-$REA_PROJECT_ROOT/.apex-rea-cache}" ci \
  --prefix "$REA_PROJECT_ROOT/integrations/rea" --no-audit --no-fund
node "$REA_PROJECT_ROOT/integrations/rea/check.mjs"
printf '%s\n' 'REA is ready for cloud task CLI analysis.'
