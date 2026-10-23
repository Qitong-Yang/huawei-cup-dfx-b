"""Backend-neutral types for one LLM localization session (spec 4.2-4.8).

A backend runs the agent loop (prompt -> tool calls -> submit) for one case and reports the
answer plus resource use. The locator only sees these types, so backends are swappable and
tests use a fake.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)

PROMPT_VERSION = "v4"


@dataclass(frozen=True)
class LlmConfig:
    """Per-case limits; defaults follow spec 4.2/4.3/4.4/4.8."""

    model: str = "claude-opus-5-5"
    max_turns: int = 14
    token_budget: int = 160_000  # input + output + cache read + cache write, all attempts
    warn_ratio: float = 0.8
    timeout_s: int = 900
    attempts: int = 2
    read_limit: int = 150
    search_limit: int = 40
    log_chars: int = 5000
    window_before: int = 40
    window_after: int = 15


@dataclass(frozen=True)
class SessionRequest:
    case_id: str
    app_root: Path
    system_prompt: str
    user_prompt: str
    config: LlmConfig


@dataclass(frozen=True)
class SessionResult:
    answer: dict | None  # normalized submit arguments, None when nothing valid was submitted
    error: str | None
    over_budget: bool
    budget_warned: bool
    turns: int
    tokens: int
    cache_read_tokens: int
    seconds: float
    calls: tuple[dict, ...]  # one per API message: {"id", "model", "usage"}
    transcript: tuple[dict, ...]  # raw backend events, all attempts
    meta: dict  # backend, attempts, cli_version, num_turns, cost_usd, permission_denials, argv


class Backend(Protocol):
    name: str

    def run(self, request: SessionRequest) -> SessionResult: ...


def load_prompt(version: str = PROMPT_VERSION) -> tuple[str, str]:
    """Return (system prompt text, SHA-256 hex of its UTF-8 bytes) for a frozen prompt version."""
    resource = resources.files("dfx_repair.llm") / "prompts" / f"localize_{version}.txt"
    if not resource.is_file():
        raise FileNotFoundError(f"no prompt file for version {version!r}")
    text = resource.read_text(encoding="utf-8").removesuffix("\n")
    return text, hashlib.sha256(text.encode("utf-8")).hexdigest()
