"""Shared test data for the LLM localization tests."""

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
