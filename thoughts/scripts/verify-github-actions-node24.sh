#!/usr/bin/env bash
# Proves: GitHub Actions workflow artifact uploads use a Node 24 action line.
# Created: 2026-06-22 after Node 20 deprecation warnings on daily CI replay jobs.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

deprecated_refs="$(grep -RInE 'actions/upload-artifact@v[0-5]([^0-9]|$)' .github/workflows || true)"
if [ -n "$deprecated_refs" ]; then
  echo "FAIL: deprecated upload-artifact references found"
  echo "$deprecated_refs"
  exit 1
fi

grep -q 'uses: actions/upload-artifact@v7' .github/workflows/quality-daily.yaml || {
  echo "FAIL: quality-daily.yaml should upload proxymock reports with actions/upload-artifact@v7"
  exit 1
}

echo "PASS: GitHub Actions artifact uploads use actions/upload-artifact@v7"
