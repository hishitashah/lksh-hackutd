"""Check NVIDIA access: list the Nemotron models your API key can use (ARCHITECTURE.md §9.5).

Why this exists
---------------
ARCHITECTURE.md §9.5: don't assume a specific Nemotron model version. Before building the agents,
confirm which models your key can use and put them in .env (SUPER_MODEL / NANO_MODEL).

What it does
------------
1. Reads NVIDIA_API_KEY and the model settings from .env (see .env.example).
2. Calls GET {SUPER_BASE_URL}/models and prints every model with "nemotron" in its name.
3. Says whether your configured SUPER_MODEL and NANO_MODEL are in that list.
4. With --ping, sends one tiny chat message to each configured model and prints the reply.

Usage (from the repo root):  make check-nvidia          (list models)
                             make check-nvidia ARGS=--ping
Exit codes: 0 ok, 1 missing key or request failed, 2 a configured model wasn't found.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx

# Make `nightwatch` importable when this file is run directly from backend/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nightwatch.config import get_settings  # noqa: E402

TIMEOUT_S = 30


def list_models(base_url: str, key: str) -> list[str]:
    """Return every model ID the endpoint offers (OpenAI-compatible GET /models)."""
    resp = httpx.get(f"{base_url}/models", headers={"Authorization": f"Bearer {key}"}, timeout=TIMEOUT_S)
    resp.raise_for_status()
    return sorted(m["id"] for m in resp.json().get("data", []))


def ping(base_url: str, key: str, model: str) -> str:
    """Send one short chat message and return the model's reply text (proves the model answers)."""
    resp = httpx.post(
        f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": model,
            "messages": [{"role": "user", "content": "Reply with the single word: ready"}],
            "max_tokens": 16,
            "temperature": 0,
        },
        timeout=TIMEOUT_S,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()


def main() -> int:
    """Run the checks and return the process exit code."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ping", action="store_true", help="also send one chat message to each configured model")
    args = parser.parse_args()

    s = get_settings()
    if not s.nvidia_api_key:
        print(
            "NVIDIA_API_KEY is not set.\n"
            "  1. Create a key at https://build.nvidia.com (free).\n"
            "  2. Copy .env.example to .env in the repo root and paste the key.\n"
            "  3. Run `make check-nvidia` again."
        )
        return 1

    try:
        models = list_models(s.super_base_url, s.nvidia_api_key)
    except httpx.HTTPError as err:
        print(f"Could not list models from {s.super_base_url}: {err}")
        return 1

    nemotron = [m for m in models if "nemotron" in m.lower()]
    print(f"{len(models)} models available, {len(nemotron)} Nemotron:")
    for m in nemotron:
        print(f"  {m}")

    exit_code = 0
    for label, model in (("SUPER_MODEL", s.super_model), ("NANO_MODEL", s.nano_model)):
        found = model in models
        print(f"{label} = {model}: {'found' if found else 'NOT FOUND, pick one from the list above'}")
        if not found:
            exit_code = 2

    if args.ping:
        # Nano may live on Brev later (T5.8), so ping each model at its own base URL.
        for model, base in ((s.super_model, s.super_base_url), (s.nano_model, s.nano_base_url)):
            try:
                print(f"ping {model}: {ping(base, s.nvidia_api_key, model)!r}")
            except httpx.HTTPError as err:
                print(f"ping {model} failed: {err}")
                exit_code = 1

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
