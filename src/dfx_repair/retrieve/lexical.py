"""Conservative, position-preserving lexical masking for source structure checks.

This is not an ArkTS parser. Literal contents and comments are blanked while line
breaks and code coordinates remain intact. Template expressions are deliberately
masked with their template: callers must not infer data flow inside those literals.
"""

from __future__ import annotations

import re


def _quoted_end(text: str, start: int) -> int:
    quote = text[start]
    i = start + 1
    while i < len(text):
        if text[i] == "\\":
            i += 2
        elif text[i] == quote:
            return i + 1
        else:
            i += 1
    return len(text)


def _regex_end(text: str, start: int) -> int | None:
    """Recognize a single-line regex, including escaped slashes and character classes."""
    i, in_class = start + 1, False
    while i < len(text) and text[i] not in "\r\n":
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "[":
            in_class = True
        elif ch == "]":
            in_class = False
        elif ch == "/" and not in_class:
            i += 1
            while i < len(text) and text[i].isalpha():
                i += 1
            return i
        i += 1
    return None


def _can_start_regex(previous: str) -> bool:
    return not previous or previous in {
        "(", "[", "{", "}", "=", ":", ",", ";", "!", "?", "&", "|", "+",
        "-", "*", "%", "^", "~", "<", ">", "return", "throw", "case", "yield",
        "await", "typeof", "void", "delete", "in", "of", "else", "do", "=>",
    }


def _template_end(text: str, start: int) -> int:
    i = start + 1
    while i < len(text):
        if text[i] == "\\":
            i += 2
        elif text[i] == "`":
            return i + 1
        elif text.startswith("${", i):
            i = _template_expression_end(text, i + 2)
        else:
            i += 1
    return len(text)


def _template_expression_end(text: str, start: int) -> int:
    depth, i, previous = 1, start, ""
    parens: list[bool] = []
    while i < len(text):
        ch = text[i]
        if ch in "'\"":
            i = _quoted_end(text, i)
            previous = "literal"
        elif ch == "`":
            i = _template_end(text, i)
            previous = "literal"
        elif text.startswith("//", i):
            end = text.find("\n", i)
            i = len(text) if end < 0 else end
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = len(text) if end < 0 else end + 2
        elif ch == "/" and _can_start_regex(previous) and (regex_end := _regex_end(text, i)):
            i, previous = regex_end, "literal"
        elif ch == "{":
            depth += 1
            i += 1
            previous = ch
        elif ch == "}":
            depth -= 1
            i += 1
            if depth == 0:
                return i
            previous = ch
        elif ch == "(":
            parens.append(previous in {"if", "while", "for", "switch", "with", "catch"})
            previous, i = ch, i + 1
        elif ch == ")":
            previous = ";" if parens and parens.pop() else ")"
            i += 1
        elif ch.isalpha() or ch in "_$":
            match = re.match(r"[\w$]+", text[i:])
            previous = match.group() if match else ch
            i += len(previous)
        else:
            if not ch.isspace():
                previous = ch
            i += 1
    return len(text)


def mask_non_code(text: str) -> str:
    """Blank comments/literals without changing length or newlines.

    Regex-vs-division uses a conservative expression-prefix heuristic. Ambiguous
    language constructs still need a parser; this helper never claims full semantics.
    """
    out = list(text)
    i, previous = 0, ""
    parens: list[bool] = []
    while i < len(text):
        ch = text[i]
        end = None
        if text.startswith("//", i):
            found = text.find("\n", i)
            end = len(text) if found < 0 else found
        elif text.startswith("/*", i):
            found = text.find("*/", i + 2)
            end = len(text) if found < 0 else found + 2
        elif ch in "'\"":
            end = _quoted_end(text, i)
            previous = "literal"
        elif ch == "`":
            end = _template_end(text, i)
            previous = "literal"
        elif ch == "/" and _can_start_regex(previous):
            end = _regex_end(text, i)
            if end is not None:
                previous = "literal"
        if end is not None:
            for j in range(i, min(end, len(text))):
                if text[j] not in "\r\n":
                    out[j] = " "
            i = end
            continue
        if ch == "(":
            parens.append(previous in {"if", "while", "for", "switch", "with", "catch"})
            previous, i = ch, i + 1
        elif ch == ")":
            previous = ";" if parens and parens.pop() else ")"
            i += 1
        elif ch.isalpha() or ch in "_$":
            match = re.match(r"[\w$]+", text[i:])
            previous = match.group() if match else ch
            i += len(previous)
        else:
            if not ch.isspace():
                previous = ch
            i += 1
    return "".join(out)


def matching_braces(code: str) -> dict[int, int]:
    """Return matched opening-to-closing brace offsets in already masked source."""
    stack: list[int] = []
    pairs: dict[int, int] = {}
    for i, ch in enumerate(code):
        if ch == "{":
            stack.append(i)
        elif ch == "}" and stack:
            pairs[stack.pop()] = i
    return pairs
