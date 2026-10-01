"""What every command prints to a terminal: its width, the one fill and the parser.

build.py and the five tools print to a terminal, and a club reads what they print
there, in the mail cron sends, or in an issue it was pasted into. Two things
decide the shape of it, and each is one function here so that no command has a
second: :func:`fill`, which the report flows by and every refusal is filled by,
and :class:`Parser`, which every command reads its arguments with.

Here in the package, and not in build.py where the fill was, because the fetcher
does not load the builder and a package module cannot import the script that
imports it: ``tools/fetch.py``, and ``tools/demo.py`` that it imports, could not
have refused their arguments by a parser in build.py. build.py imports both.

Imports nothing from the rest of the package.
"""

from __future__ import annotations

import argparse
import sys
import textwrap
from typing import NoReturn

__all__ = ["WIDTH", "fill", "Parser"]

WIDTH = 78
"""How wide a line may be: the report's width, and every refusal's under it."""


def fill(text: str, indent: str = "  ", *, first: str | None = None,
         keep: tuple[str, ...] = ()) -> str:
    """*text* filled to the width for stderr, under *indent*, after *first* if given.

    The one fill for everything build.py and tools/scheduled.py print to stderr
    under an ``error:`` line, for every command's refusal of its arguments, and
    the one the report flows by. No word is cut: textwrap cuts one longer than
    the line where the line runs out, and a path in a refusal came out in two
    pieces, which cannot be pasted. Such a word stands on a line of its own
    instead, as it does in the report. Rejected: the flag at each call, which is
    how the refusals came to pass one flag of the two and scheduled.py neither.

    Each of *keep* is one word, spaces and all: a path with a space in it was
    words to textwrap, and a missing folder under ``the folder this club keeps
    every exported season in`` printed its advice on lines of 77, 76 and 59
    columns, the path parted at two of its spaces. Its spaces are held by a
    private-use character the text does not have, the longest path first so that
    a folder inside it does not part it, and given back after; a text that has
    every one of them keeps nothing. Rejected: a no-break space, which prints and
    pastes as a different path, and is a folder's own character to some.
    """
    held = next((chr(n) for n in range(0xE000, 0xF900) if chr(n) not in text), " ")
    for part in sorted(keep, key=len, reverse=True):
        text = text.replace(part, part.replace(" ", held))
    return textwrap.fill(text, width=WIDTH,
                         initial_indent=indent if first is None else first,
                         subsequent_indent=indent, break_long_words=False,
                         break_on_hyphens=False).replace(held, " ")


class _Layout(argparse.RawDescriptionHelpFormatter):
    """argparse's usage and help, laid out at :data:`WIDTH` whatever the terminal.

    argparse takes its width from the terminal, less two, so the same usage was
    104 columns at a terminal 120 wide and 78 in a pipe. Raw, so that a
    description prints as it is written, and so that the version line, which
    argparse prints through the formatter, keeps its double space: the default
    formatter fills it, and collapsed it.
    """

    def __init__(self, prog: str, **kwargs) -> None:
        kwargs["width"] = WIDTH
        super().__init__(prog, **kwargs)


class Parser(argparse.ArgumentParser):
    """The one parser every command reads its arguments with.

    argparse printed its refusal on one line however long it was, and a command's
    own checks refused through it too: ``build.py --prune --only`` was 117 columns
    and ``tools/scheduled.py --keep 1`` 112. It laid its usage out at the
    terminal's width, and a list of choices was one part of it that no line could
    split, so ``tools/demo.py``'s usage was 149 columns on every refusal. Here:

    * the usage and the help are laid out at the width, by :class:`_Layout`;
    * a list of choices is named in the usage by a word, the flag's own in
      capitals unless the command names it, and the command's help lists them;
    * a refusal is the usage, then ``<prog>: error: <message>`` through
      :func:`fill`, each line after the first hung under it, at exit 2 as
      argparse's is, since both are what a script reads. Every argument typed
      with a space in it is kept whole in each form argparse's own messages echo
      it (:meth:`_echoed`), and a command's own check hands ``keep`` any path it
      names another way.

    Rejected: an error function each command calls, which is a line in each to
    forget; and rewording argparse's own messages, whose words change with the
    Python that prints them.
    """

    def __init__(self, prog: str, **kwargs) -> None:
        super().__init__(prog=prog, formatter_class=_Layout, **kwargs)
        self._typed: tuple[str, ...] = ()

    def add_argument(self, *args, **kwargs) -> argparse.Action:
        action = super().add_argument(*args, **kwargs)
        if action.choices is not None and action.metavar is None:
            action.metavar = action.dest.upper() if action.option_strings else action.dest
        return action

    def parse_known_args(self, args=None, namespace=None):
        self._typed = tuple(sys.argv[1:] if args is None else args)
        return super().parse_known_args(args, namespace)

    def _echoed(self) -> list[str]:
        """Each argument typed with a space in it, in every form argparse echoes it.

        As typed, in ``unrecognized arguments:``; an option typed with its value,
        ``--page=N``, by the value alone; and a value it cannot take by ``repr``,
        which doubles each backslash. Kept only as typed, ``tools/doctor.py
        --page=<a path with spaces>`` printed the path on lines of 76, 76 and 4
        columns, and ``--page 'C:\\Users\\Club Secretary\\...'`` parted it at
        ``Club``. A word of spaces alone is no path, and holds nothing.
        """
        values = [word.partition("=")[2] if word.startswith("-") else word
                  for word in self._typed]
        echoes = (*self._typed, *values, *(repr(value)[1:-1] for value in values))
        return [echo for echo in echoes if " " in echo.strip()]

    def error(self, message: str, *, keep: tuple[str, ...] = ()) -> NoReturn:
        self.print_usage(sys.stderr)
        self.exit(2, fill(f"{self.prog}: error: {message}", first="",
                          keep=(*keep, *self._echoed())) + "\n")
