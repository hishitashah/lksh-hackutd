"""Settings for the backend, read from environment variables or the repo-root ``.env`` file.

Where this sits in the app
--------------------------
Every module that needs a setting calls ``get_settings()`` instead of reading
``os.environ`` itself, so all configuration lives in one place. Copy ``.env.example``
to ``.env`` and fill in your own values; ``.env`` is gitignored so keys never get pushed.

Tests change settings with ``monkeypatch.setenv(...)`` followed by
``get_settings.cache_clear()``.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/nightwatch/config.py -> parents[2] is the repo root (where history/ and test/ live).
REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """All backend settings. Env var names are the upper-case field names (e.g. NIGHTWATCH_MOCK)."""

    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

    # --- Data -----------------------------------------------------------------
    data_dirs: str = Field(
        default="history,test",
        description=(
            "Comma-separated folders (relative to the repo root) to scan for datasets. A folder that "
            "holds the 3 files is a dataset itself (test/); otherwise each subfolder that holds them is "
            "(history/full_week, history/snapshot_...)."
        ),
    )

    # --- Mock mode (M0) ----------------------------------------------------------
    nightwatch_mock: bool = Field(
        default=True,
        description=(
            "True = routes serve fixtures/ so the frontend and agent can be built before the engine exists. "
            "Must be False for the demo; the UI shows a MOCK DATA badge while it's on."
        ),
    )

    # --- NVIDIA Nemotron (M5) -------------------------------------------------
    nvidia_api_key: str | None = Field(default=None, description="build.nvidia.com API key. Never commit it.")
    super_model: str = Field(
        default="nvidia/nemotron-3-super-120b-a12b",
        description="Orchestrator model for Sentinel. Confirm the name with `make check-nvidia`.",
    )
    nano_model: str = Field(
        default="nvidia/nemotron-3-nano-30b-a3b",
        description="Fast model for evidence text and handoffs. Confirm the name with `make check-nvidia`.",
    )
    super_base_url: str = Field(
        default="https://integrate.api.nvidia.com/v1",
        description="OpenAI-compatible endpoint for the Super model (NVIDIA hosted API).",
    )
    nano_base_url: str = Field(
        default="https://integrate.api.nvidia.com/v1",
        description="Endpoint for the Nano model. Point at the Brev vLLM server once it's up (T5.8).",
    )

    # --- Field-crew notifications (M5) ----------------------------------------
    ntfy_topic: str | None = Field(default=None, description="ntfy.sh topic for phone pushes. Use a random name.")
    discord_webhook_url: str | None = Field(default=None, description="Discord webhook for the on-screen dispatch log.")

    @property
    def data_dir_list(self) -> list[str]:
        """``data_dirs`` split into a clean list, e.g. ['history', 'test']."""
        return [d.strip() for d in self.data_dirs.split(",") if d.strip()]


@lru_cache
def get_settings() -> Settings:
    """Return the settings, read once and cached. Call ``get_settings.cache_clear()`` to re-read."""
    return Settings()
