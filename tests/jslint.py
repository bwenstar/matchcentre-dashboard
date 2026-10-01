"""A structural reader for JavaScript, written because there is no JS engine here.

This machine has no node, no deno, and no JavaScript parser for Python, so the
dashboard's browser code cannot be executed by its own test suite. That makes one
class of mistake unusually expensive: an unbalanced brace in ``app.js`` is not a
wrong number on a page, it is a *blank page*, and nothing between the edit and the
reader would say so.

So this module does the one thing that can be done honestly without an engine: it
tokenises far enough to know which characters are code and which are inside a
string, a comment or a regular expression, then reports two kinds of fault --
a literal that is never closed, and brackets that do not balance. That is not a
syntax check and this module does not pretend to be one; it cannot tell a typo'd
identifier from a good one. It can tell you the file is still a plausible program,
which is exactly what a careless edit breaks.

Both fault kinds are needed, and finding that out took an experiment. Blanking an
unterminated ``/*`` runs to the end of the file, which *hides* every bracket after
it -- appending ``/* oops`` to a balanced file left it still looking balanced, so
a bracket check alone called a file healthy that a browser would refuse to load.
An unclosed literal is therefore reported in its own right rather than inferred.

Known limitations, stated rather than hidden. A backtick inside a ``${...}``
substitution inside a template literal will confuse the scanner; the dashboard
code is deliberately ES5-compatible and uses no template literals, and
:func:`uses_template_literals` exists so a test can assert that stays true. And
telling a regular expression from a division is decided on what precedes the
slash, which is a heuristic: after a keyword it is a regex, after a value it is
division, so a reserved word used as a property name -- ``counts.in / 2`` -- is
read as a regex. No such name appears in the dashboard.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["Fault", "blank_literals", "code_only", "without_comments", "unterminated",
           "balance",
           "uses_template_literals", "find_calls"]

# A '/' begins a regular expression unless the previous significant character
# could end a value -- after `)`, `]`, an identifier or a number it is division.
_VALUE_END = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
                 "0123456789_$)]")

# Except after these, which are words rather than values: `return /"/.test(s)` is a
# regular expression, and read as a division the quote inside it opens a string that
# runs to the end of the file. The letter before the '/' cannot tell the two apart, so
# the whole word is read -- `noreturn / 2` is still a division.
_WORD_BEFORE_REGEX = frozenset({
    "await", "case", "delete", "do", "else", "in", "instanceof", "new", "of",
    "return", "throw", "typeof", "void", "yield",
})
_WORD_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
                        "0123456789_$")

_PAIRS = {"(": ")", "[": "]", "{": "}"}
_CLOSERS = {v: k for k, v in _PAIRS.items()}


@dataclass(frozen=True, slots=True)
class Fault:
    """Where the file stopped being a plausible program.

    ``kind`` is ``"unterminated"`` for a literal or comment that never closes and
    ``"bracket"`` for a bracket mismatch. Line and column point at the opening
    token for anything left open, and at the offending character otherwise,
    because that is the end a reader has to go and edit.
    """

    kind: str
    line: int
    column: int
    found: str
    expected: str
    detail: str

    def describe(self) -> str:
        return f"line {self.line}, column {self.column}: {self.detail}"


def _position(source: str, index: int) -> tuple[int, int]:
    """1-based line and column of ``index``, matching :func:`balance`'s counting."""
    line = source.count("\n", 0, index) + 1
    start = source.rfind("\n", 0, index) + 1
    return line, index - start + 1


def _scan(source: str, *, templates: bool = True,
          literals: bool = True) -> tuple[str, Fault | None]:
    """Blank out every literal, and report the first one that never closed.

    Returns the blanked text and the fault, if any. The text is always the same
    length as the input and keeps every newline, so line and column numbers
    computed from it match the original file. With ``literals=False`` the text
    returned has only its comments blanked; the scan that decides where each one
    is stays the same scan, so the two answers cannot disagree about a comment.
    """
    out = list(source)
    kept = list(source)    # the same text with only the comments blanked
    length = len(source)
    index = 0
    previous = ""          # last significant (non-space) code character
    fault: Fault | None = None

    def blank(start: int, stop: int, *, comment: bool = False) -> None:
        for position in range(start, min(stop, length)):
            if out[position] != "\n":
                out[position] = " "
                if comment:
                    kept[position] = " "

    def word_before(at: int) -> str:
        """The identifier ending just before ``at``, over already-blanked text.

        Reading ``out`` rather than ``source`` is what makes a comment between the
        word and the slash invisible: by the time the slash is reached everything
        before it has been blanked, so ``return /* why */ /x/`` reads ``return``.
        """
        cursor = at - 1
        while cursor >= 0 and out[cursor].isspace():
            cursor -= 1
        stop = cursor + 1
        while cursor >= 0 and out[cursor] in _WORD_CHARS:
            cursor -= 1
        return "".join(out[cursor + 1:stop])

    def unclosed(start: int, opener: str, closer: str) -> None:
        nonlocal fault
        if fault is not None:
            return             # keep the first, which is the one to go and fix
        line, column = _position(source, start)
        fault = Fault("unterminated", line, column, opener, closer,
                      f"{opener!r} opened at line {line} column {column} is "
                      f"never closed by {closer!r}")

    while index < length:
        char = source[index]

        # Line comment.
        if char == "/" and source.startswith("//", index):
            stop = source.find("\n", index)
            stop = length if stop < 0 else stop
            blank(index, stop, comment=True)
            index = stop
            continue

        # Block comment.
        if char == "/" and source.startswith("/*", index):
            stop = source.find("*/", index + 2)
            if stop < 0:
                unclosed(index, "/*", "*/")
                blank(index, length, comment=True)
                break
            blank(index, stop + 2, comment=True)
            index = stop + 2
            continue

        # Quoted string, single or double.
        if char in "\"'":
            cursor = index + 1
            closed = False
            while cursor < length:
                here = source[cursor]
                if here == "\\":
                    cursor += 2        # escaped anything, line continuation too
                    continue
                if here == "\n":
                    break              # a plain string cannot span a raw newline
                cursor += 1
                if here == char:
                    closed = True
                    break
            if not closed:
                unclosed(index, char, char)
            blank(index, cursor)
            index = cursor
            previous = "x"         # a string is a value, so a following / divides
            continue

        # Template literal, treated as opaque. See the module docstring.
        if char == "`" and templates:
            cursor = index + 1
            closed = False
            while cursor < length:
                here = source[cursor]
                if here == "\\":
                    cursor += 2
                    continue
                cursor += 1
                if here == "`":
                    closed = True
                    break
            if not closed:
                unclosed(index, "`", "`")
            blank(index, cursor)
            index = cursor
            previous = "x"
            continue

        # Regular expression literal.
        if char == "/" and (previous not in _VALUE_END
                            or word_before(index) in _WORD_BEFORE_REGEX):
            cursor = index + 1
            in_class = False
            while cursor < length:
                here = source[cursor]
                if here == "\\":
                    cursor += 2
                    continue
                if here == "\n":
                    break          # a regex cannot span lines; treat as division
                if here == "[":
                    in_class = True
                elif here == "]":
                    in_class = False
                elif here == "/" and not in_class:
                    break
                cursor += 1
            if cursor < length and source[cursor] == "/":
                # Consume trailing flags so a following '/' is read as division.
                cursor += 1
                while cursor < length and source[cursor].isalpha():
                    cursor += 1
                blank(index, cursor)
                index = cursor
                previous = "x"
                continue
            # Not a regex after all; fall through and treat as an operator.

        if not char.isspace():
            previous = char
        index += 1

    return "".join(out if literals else kept), fault


def blank_literals(source: str, *, templates: bool = True) -> str:
    """``source`` with comments, strings and regexes replaced by spaces.

    Newlines are preserved so reported line numbers still match the original
    file, and the result is the same length as the input so column numbers do
    too. With ``templates=False`` a backtick is left as an ordinary character,
    which is how :func:`uses_template_literals` spots one.
    """
    return _scan(source, templates=templates)[0]


def code_only(source: str) -> str:
    """Alias for :func:`blank_literals`, for callers that read better this way."""
    return blank_literals(source)


def without_comments(source: str) -> str:
    """``source`` with its comments replaced by spaces and every string kept.

    What a page can say: a sentence is in a string, and a sentence in a comment is
    a note to whoever reads the code. Same length and same newlines as the input,
    so an offset found here is an offset into ``source`` and into
    :func:`code_only`'s text alike.
    """
    return _scan(source, literals=False)[0]


def unterminated(source: str) -> Fault | None:
    """The first string, template or block comment that is never closed."""
    return _scan(source)[1]


def balance(source: str) -> Fault | None:
    """``None`` when the file is a plausible program, else the first fault.

    An unclosed literal is reported before any bracket complaint, because it is
    the earlier and more fundamental fault: everything after an unclosed ``/*``
    is comment, so bracket counts past that point mean nothing.
    """
    code, fault = _scan(source)
    if fault is not None:
        return fault
    stack: list[tuple[str, int, int]] = []
    line = 1
    column = 0
    for char in code:
        column += 1
        if char == "\n":
            line += 1
            column = 0
            continue
        if char in _PAIRS:
            stack.append((char, line, column))
        elif char in _CLOSERS:
            if not stack:
                return Fault("bracket", line, column, char, "",
                             f"closing {char!r} with nothing open")
            opener, open_line, open_col = stack.pop()
            if _PAIRS[opener] != char:
                return Fault(
                    "bracket", line, column, char, _PAIRS[opener],
                    f"closing {char!r} but {opener!r} opened at line "
                    f"{open_line} column {open_col} needs {_PAIRS[opener]!r}")
    if stack:
        opener, open_line, open_col = stack[-1]
        return Fault("bracket", open_line, open_col, "", _PAIRS[opener],
                     f"{opener!r} opened at line {open_line} column "
                     f"{open_col} is never closed")
    return None


def uses_template_literals(source: str) -> bool:
    """Whether a backtick appears in the code, outside a string or comment.

    Worth asserting false: the dashboard is deliberately ES5-compatible so it
    runs in whatever browser a club has, and a template literal is also the one
    construct this module's scanner cannot follow reliably.
    """
    return "`" in blank_literals(source, templates=False)


def find_calls(source: str, function: str) -> list[str]:
    """Every string literal passed as the sole argument to ``function``.

    Used to collect the element ids ``document.getElementById('...')`` asks for,
    so a test can check they exist in the HTML. Only literal single-argument
    calls are found, which is the point: a computed id cannot be checked and
    should not be silently counted as checked.
    """
    found: list[str] = []
    needle = function + "("
    start = 0
    while True:
        at = source.find(needle, start)
        if at < 0:
            return found
        cursor = at + len(needle)
        while cursor < len(source) and source[cursor].isspace():
            cursor += 1
        if cursor < len(source) and source[cursor] in "\"'":
            quote = source[cursor]
            end = source.find(quote, cursor + 1)
            if end > 0:
                argument = source[cursor + 1:end]
                rest = source[end + 1:end + 2]
                if rest == ")" and "\\" not in argument:
                    found.append(argument)
        start = at + len(needle)
