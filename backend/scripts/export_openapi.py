"""Write the API's OpenAPI spec to frontend/src/api/openapi.json (part of `make types`, task T0.2).

Where this sits in the app
--------------------------
models.py -> FastAPI builds an OpenAPI spec -> this script saves it -> openapi-typescript turns
it into frontend/src/api/types.ts. Run `make types` after changing any model or route; CI fails
if the committed types are out of date.

Usage (from the repo root):  make types
Directly (from backend/):    uv run python scripts/export_openapi.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Make `nightwatch` importable when this file is run directly from backend/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nightwatch.api import app  # noqa: E402
from nightwatch.config import REPO_ROOT  # noqa: E402

OUT = REPO_ROOT / "frontend" / "src" / "api" / "openapi.json"


def main() -> None:
    """Generate the spec and write it with stable formatting so diffs stay small."""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    spec = app.openapi()
    OUT.write_text(json.dumps(spec, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    print(f"wrote {OUT.relative_to(REPO_ROOT)} ({len(spec.get('paths', {}))} routes)")


if __name__ == "__main__":
    main()
