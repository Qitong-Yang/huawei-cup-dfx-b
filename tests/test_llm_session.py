import dataclasses
import hashlib

import pytest

from dfx_repair.llm import LlmConfig, load_prompt


def test_prompt_is_frozen_v4():
    text, sha = load_prompt()
    assert sha == "12e096761c18ebfdf9828063944d583d7ef3ce5f0016614a3e075aa36c4c68fe"
    assert hashlib.sha256(text.encode("utf-8")).hexdigest() == sha
    assert "Call submit exactly once." in text and not text.endswith("\n")


def test_unknown_prompt_version_raises():
    with pytest.raises(FileNotFoundError):
        load_prompt("v999")


def test_config_defaults_match_spec():
    c = LlmConfig()
    assert (c.max_turns, c.token_budget, c.warn_ratio, c.read_limit, c.search_limit) == (
        14, 160_000, 0.8, 150, 40)
    assert c.model == "claude-opus-5-5"
    with pytest.raises(dataclasses.FrozenInstanceError):
        c.max_turns = 3  # type: ignore[misc]
