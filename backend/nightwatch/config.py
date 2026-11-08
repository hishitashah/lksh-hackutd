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
from typing import Literal

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

    # --- Monitoring thresholds (ARCHITECTURE.md §3.2-3.3, §13: documented and configurable) --------------
    # Tuned on history/full_week: with these values all 23 labelled problems are found, with 0 false alarms
    # and 0 duplicate incidents (see backend/scripts/score.py). σ is each asset's own noise level
    # (MAD-based standard deviation of its running samples), so the thresholds adapt to any network.
    detect_window_samples: int = Field(
        default=6, description="Readings averaged together (while the pump runs) before judging a leak or wear."
    )
    leak_pressure_sigma: float = Field(
        default=2.5, description="Leak: pipeline pressure must be this many σ below its normal level."
    )
    leak_gap_sigma: float = Field(
        default=0.5, description="Leak: pump flow minus pipeline receive flow must be this many σ above normal."
    )
    wear_discharge_sigma: float = Field(
        default=2.0, description="Worn pump: discharge pressure must be this many σ below normal (no leak signature)."
    )
    backdate_sigma: float = Field(
        default=1.0, description="Onset is moved back while the signal stays more than this many σ from normal."
    )
    merge_gap_hours: float = Field(
        default=6.0, description="Abnormal stretches closer than this are one incident (no duplicates)."
    )
    min_segment_samples: int = Field(default=3, description="Abnormal readings needed before an incident is raised.")
    baseline_min_samples: int = Field(
        default=24, description="Running readings needed to learn an asset's normal behavior before judging it."
    )
    countdown_fit_samples: int = Field(
        default=6, description="Latest tank readings fitted with a line to estimate the current level (sensor noise)."
    )

    # --- Environmental risk policy (ARCHITECTURE.md §6.3: "only use classifications with defined criteria") ---
    # These are policy choices, not physics. In history/full_week a leak loses ~4 bbl in 2 h, 12-19 bbl by 6 h and
    # 23-32 bbl by 12 h, with the missing rate growing ~40 -> ~160 bpd, so 'high' is reached ~10-12 h into a leak.
    env_leak_high_bbl: float = Field(
        default=25.0, description="A leak is 'high' environmental risk once this much water is unaccounted for (bbl)."
    )
    env_leak_high_rate_bpd: float = Field(
        default=150.0, description="A leak is also 'high' risk once water goes missing at least this fast (bpd)."
    )

    # --- Orchestrator (ARCHITECTURE.md §9.2, §9.4) ------------------------------------------------------
    agent_timeout_s: float = Field(
        default=30.0,
        description="Seconds each specialist agent gets before it's marked 'timeout' (a Nemotron explanation takes ~4 s).",
    )
    env_veto_risk: Literal["moderate", "high", "critical"] = Field(
        default="high",
        description=(
            "Hard constraint: an option the environmental agent rates at or above this level is excluded when a "
            "lower-risk option exists for the same incident. Policy choice, documented and configurable."
        ),
    )

    # --- Field dispatch (ARCHITECTURE.md §10) -----------------------------------------------------------
    field_stream_max_s: float = Field(
        default=300.0,
        description=(
            "Seconds a /field/stream connection stays open before the server closes it; the browser's EventSource "
            "reconnects on its own and gets every current order again (de-duplicated by ID)."
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
    # Not every listed model is enabled for every account: `make check-nvidia ARGS=--ping` shows which ones answer.
    # These defaults answered on our key (the 'super-120b' and 'nano-3' models returned "not found for account").
    super_model: str = Field(
        default="nvidia/nemotron-3-ultra-550b-a55b",
        description="Main Nemotron model for agent and orchestrator explanations (must answer the --ping check).",
    )
    nano_model: str = Field(
        default="nvidia/nemotron-3.5-lightning-30b-a3b",
        description="Smaller, faster Nemotron model (not used yet). Must answer `make check-nvidia ARGS=--ping`.",
    )
    nemotron_thinking: bool = Field(
        default=False,
        description=(
            "Nemotron 3 models can 'think' before answering. Off by default: explanations come back in ~1 s instead "
            "of ~6-9 s, and thinking can use up the answer budget and return empty text."
        ),
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
    # Orders reach phones through the app's own live /field feed (T5.5), so no push service is configured.
    discord_webhook_url: str | None = Field(default=None, description="Discord webhook for the on-screen dispatch log.")

    # --- Work orders on GitHub Issues (M5, T5.13) -----------------------------
    github_token: str | None = Field(
        default=None,
        description="Fine-grained GitHub token with Issues read/write on github_repo only. Never commit it.",
    )
    github_repo: str | None = Field(
        default=None, description="Repo that holds work orders, as 'owner/name' (e.g. a separate demo repo)."
    )

    @property
    def data_dir_list(self) -> list[str]:
        """``data_dirs`` split into a clean list, e.g. ['history', 'test']."""
        return [d.strip() for d in self.data_dirs.split(",") if d.strip()]


@lru_cache
def get_settings() -> Settings:
    """Return the settings, read once and cached. Call ``get_settings.cache_clear()`` to re-read."""
    return Settings()
