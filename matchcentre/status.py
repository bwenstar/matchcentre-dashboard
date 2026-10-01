"""What the front page says when the last build did not produce its figures.

A build that fails before its write leaves every file under ``--out`` as the last
good build wrote it, which is right -- a half-built dashboard is worse than
yesterday's -- and was silent: the page went on showing its figures and its footer
date with nothing to say they were stale. This module holds the one sentence that
says so, and the one write that puts it in ``build-status.js``.

The sentence is one of ten, chosen by the kind of failure and never built from
the exception, because the page may be published and an exception names paths,
and a path can carry somebody's user name. It says nothing about when: the footer
already dates the figures, and a second date on the page is a second date that
can disagree with it.

The file's format and the clean build's bytes are site.py's (:data:`site.CLEAN`,
:func:`site.build_status_js`); this module only writes over the file when a build
fails, and only when the file is already there and carries the generated mark.
"""

from __future__ import annotations

from pathlib import Path

from .site import STATUS_FILE, _is_link, _marked, _put, build_status_js

__all__ = ["FAILURES", "SENTENCE", "minutes", "failed", "record_failure"]

SENTENCE = ("The last build failed: {reason}, so the figures below may be out "
            "of date.")

# Kind -> reason. "{n}" is a count of cards and "{minutes}" a length of time,
# where the kind has one.
FAILURES: dict[str, str] = {
    "no-folder": "no scorecard folder was found",
    "no-cards": "no scorecards were found",
    "none-read": "none of the {n} scorecards it found could be scored",
    "input": "a problem in the settings or the scorecards stopped it",
    # Every card read and every document built, and a player's name found in one
    # that was to withhold them: its own kind, because "input" says the cards
    # stopped it. Names nobody, like the other nine, since this file is published.
    "withheld": "it stopped rather than write a player's name into the dashboard",
    "not-written": "the dashboard could not be written",
    "unopened": "a file or folder it needed could not be opened",
    "internal": "an internal error stopped it",
    "too-long": "it ran for more than {minutes} and was stopped",
    # The wrapper's other one: build.py ended on a signal, or could not be started,
    # so it reached no line that could say why. What stopped it is in the report.
    "unfinished": "it did not finish",
}

# "none of the 1 scorecards" is what a template with a count in it says about one.
ONE_UNSCORED = "the one scorecard it found could not be scored"


def minutes(n: float) -> str:
    """``1 minute``, ``10 minutes``, ``0.5 minutes``: one spelling for every caller."""
    return "1 minute" if n == 1 else f"{n:g} minutes"


def failed(kind: str, count: float = 0) -> str:
    """The whole sentence for one kind of failure. An unknown kind is a KeyError."""
    template = FAILURES[kind]
    if kind == "none-read" and count == 1:
        template = ONE_UNSCORED
    reason = template.format(n=count, minutes=minutes(count))
    return SENTENCE.format(reason=reason)


def record_failure(out: str | Path, kind: str, count: float = 0) -> bool:
    """Put the failure sentence in ``out/build-status.js``; whether it wrote.

    Only over a file that is already there and starts with the generated mark: a
    failed build never creates a site to say it failed, and never overwrites a
    file it cannot prove is its own. Written through the same compare-then-write
    as every generated file, so a failure repeated every run writes once. Any
    ``OSError`` is a False, because this runs inside build.py's own error handling
    and must not replace the report of what went wrong with one about this file.
    Never through a link, whose mark is read at its other end, in somebody
    else's status file, nor over one, which the rename :func:`_put` writes by
    would replace.
    """
    path = Path(out) / STATUS_FILE
    if _is_link(path) or not _marked(path):
        return False
    data = build_status_js({"level": "fail", "text": failed(kind, count)})
    try:
        return _put(path, data.encode("utf-8"))
    except OSError:
        return False
