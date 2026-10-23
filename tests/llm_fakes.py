"""Shared test data and a fake LLM backend for the LLM localization tests."""

from __future__ import annotations

from dfx_repair.llm import SessionRequest, SessionResult

FAKE_TOKENS = 12_000

FULL_SUBMIT = {
    "hypothesis": "upstream",
    "file": "a/A.ets",
    "line": 7,
    "quoted_line": "let author = 'Author_2';",
    "evidence_file": "a/A.ets",
    "evidence_line": 8,
    "evidence_quote": "use(author);",
    "origin_file": "a/A.ets",
    "origin_line": 7,
    "origin_quote": "let author = 'Author_2';",
    "reasoning": "author is produced at line 7",
}


class FakeBackend:
    """Returns a preset session result and keeps the last request."""

    name = "fake"

    def __init__(self, answer: dict | None = None, error: str | None = None,
                 tokens: int = FAKE_TOKENS, over_budget: bool = False) -> None:
        self.answer, self.error, self.tokens, self.over_budget = answer, error, tokens, over_budget
        self.request: SessionRequest | None = None

    def run(self, request: SessionRequest) -> SessionResult:
        self.request = request
        return SessionResult(
            answer=self.answer, error=self.error, over_budget=self.over_budget,
            budget_warned=self.over_budget, turns=3, tokens=self.tokens,
            cache_read_tokens=self.tokens // 2, seconds=1.0,
            calls=({"id": "m1", "model": "fake", "usage": {}},),
            transcript=({"type": "fake"},), meta={"backend": self.name, "attempts": 1})
