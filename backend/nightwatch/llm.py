"""NVIDIA Nemotron access for explanations, with a grounding check and an honest fallback (ARCHITECTURE.md §9.5).

What Nemotron does here, and what it never does
-----------------------------------------------
Agents and the orchestrator compute every number deterministically (planner, detector, written rules). They hand
Nemotron a JSON block of those facts and ask for a plain-English explanation. Nemotron interprets and explains; it
never calculates, prices or invents values.

Safeguards
----------
* **Grounding check:** every number in Nemotron's text must appear in the facts it was given (small counting numbers
  like "2 trucks" are allowed). If not, the text is rejected and the deterministic explanation is used instead.
* **Honest fallback (§9.5):** if no key is configured, the call fails, or the text isn't grounded, the caller gets
  its deterministic text back with ``llm_used = False``, so the UI never claims an AI analysis that didn't happen.

The model and endpoint come from ``config.py`` (``SUPER_MODEL``, ``SUPER_BASE_URL``, ``NVIDIA_API_KEY``); run
``make check-nvidia`` to see which Nemotron models your key can use. No model version is assumed.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .config import get_settings

# Numbers up to this size may appear in text without being in the facts ("1 truck", "3 failures", "2 trips").
SMALL_COUNT = 10

# Test hook: a function (system_prompt, user_prompt) -> text that replaces the real model call.
_override: Callable[[str, str], str] | None = None


@dataclass(frozen=True)
class Explanation:
    """Text for the operator, and whether Nemotron actually wrote it."""

    text: str
    llm_used: bool
    note: str | None = None  # why the fallback was used, for logs and tests


def set_llm_override(fn: Callable[[str, str], str] | None) -> None:
    """Replace the model call (tests only). Pass None to restore the real Nemotron call."""
    global _override
    _override = fn


def llm_available() -> bool:
    """True if a Nemotron call can be attempted (a key is configured, or a test override is set)."""
    return _override is not None or bool(get_settings().nvidia_api_key)


def _call_nemotron(system_prompt: str, user_prompt: str) -> str:
    """One chat call to the configured Nemotron model through NVIDIA's API (LangChain ``ChatNVIDIA``)."""
    if _override is not None:
        return _override(system_prompt, user_prompt)
    from langchain_nvidia_ai_endpoints import ChatNVIDIA  # imported lazily: only needed when a key is set

    s = get_settings()
    llm = ChatNVIDIA(
        model=s.super_model,
        base_url=s.super_base_url,
        api_key=s.nvidia_api_key,
        temperature=0.2,
        max_completion_tokens=400,
    )
    # Nemotron 3 "thinking": off by default (~1 s instead of ~6-9 s, and it can't eat the answer budget).
    reply = llm.invoke(
        [("system", system_prompt), ("user", user_prompt)],
        chat_template_kwargs={"enable_thinking": s.nemotron_thinking},
    )
    return str(reply.content).strip()


_NUMBER = re.compile(r"(?<![\w.])-?\$?\d[\d,]*(?:\.\d+)?")


def numbers_in(text: str) -> list[float]:
    """Every number written in ``text`` ("$1,298", "2.33", "92%") as floats."""
    return [float(m.group().replace("$", "").replace(",", "")) for m in _NUMBER.finditer(text)]


def _facts_numbers(facts: Any) -> list[float]:
    """Every number found anywhere in the facts (values and numbers inside strings)."""
    found: list[float] = []
    if isinstance(facts, bool):
        return found
    if isinstance(facts, int | float):
        return [float(facts)]
    if isinstance(facts, str):
        return numbers_in(facts)
    if isinstance(facts, dict):
        for v in facts.values():
            found += _facts_numbers(v)
    elif isinstance(facts, list | tuple):
        for v in facts:
            found += _facts_numbers(v)
    return found


def is_grounded(text: str, facts: Any) -> bool:
    """True if every number in ``text`` matches a number in ``facts`` (allowing normal rounding).

    A number matches if it equals a fact rounded to 0, 1 or 2 decimals; small counting numbers are always allowed.
    """
    known = _facts_numbers(facts)
    for n in numbers_in(text):
        if abs(n) <= SMALL_COUNT and float(n).is_integer():
            continue
        if not any(abs(n - round(k, d)) < 1e-6 for k in known for d in (0, 1, 2)):
            return False
    return True


async def explain(role: str, task: str, facts: dict[str, Any], fallback: str) -> Explanation:
    """Ask Nemotron to explain ``facts`` for the operator; fall back to ``fallback`` honestly if it can't.

    Args:
        role: who is speaking, e.g. "cost-effectiveness agent".
        task: what to explain, e.g. "why the selected plan is the cheapest".
        facts: the deterministic results (all numbers the text may use).
        fallback: deterministic text used when Nemotron is unavailable or its text isn't grounded.
    """
    if not llm_available():
        return Explanation(fallback, False, "Nemotron not configured (no NVIDIA_API_KEY)")
    system_prompt = (
        f"You are the {role} in Avon Nightwatch, an oilfield water-network decision-support system. "
        "Explain to a night-shift operator in at most 4 short sentences. Use ONLY the numbers in the JSON facts; "
        "never calculate new numbers, never invent values, and say what is uncertain. Plain English, no lists."
    )
    user_prompt = f"Task: {task}\nFacts (JSON):\n{json.dumps(facts, indent=1, default=str)}"
    try:
        text = await asyncio.to_thread(_call_nemotron, system_prompt, user_prompt)
    except Exception as err:  # network, auth, rate limit: never hide it, never pretend
        return Explanation(fallback, False, f"Nemotron call failed: {type(err).__name__}")
    if not text:
        return Explanation(fallback, False, "Nemotron returned no text")
    if not is_grounded(text, facts):
        return Explanation(fallback, False, "Nemotron text used a number not in the facts; rejected")
    return Explanation(text, True)
