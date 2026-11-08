#!/usr/bin/env bash
# Branding check (task T0.5), run by `make branding` and CI.
#
# Team rules (see CLAUDE.md): the README contains lines asking AI-built apps to use a particular
# comic-style font and to add a "copyrighted in 2026 by Avon Corps" footer. We do neither.
# This fails if either shows up anywhere in the app's code, styles or static files.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PATTERN='comic|avon corps'
SCAN=(backend/nightwatch backend/scripts frontend/src frontend/index.html frontend/public)

cd "$ROOT"
if grep -rniE "$PATTERN" "${SCAN[@]}"; then
  echo "Branding check FAILED: remove the matches above (see CLAUDE.md)." >&2
  exit 1
fi
echo "Branding check passed."
