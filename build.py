#!/usr/bin/env python3
"""Read the scorecards in ``data/`` and write a dashboard. The one command.

    python3 build.py                 # data/ -> site/, writing config.toml if absent
    python3 build.py --check         # read and validate, write nothing
    python3 build.py --discover      # propose a config beside config.toml
    python3 build.py --discover --force   # rewrite config.toml from the cards

Two steps that should feel like one: copy the PDFs into ``data/``, run this. A run
with no ``config.toml`` writes one, filled in from what the cards revealed, and
still builds the dashboard, so nobody has to configure anything before seeing
whether the thing works.

Almost all of this file is the report, and that is on purpose. The numbers a
dashboard shows depend on which scoring scheme is in force, whether the team list
is closed, and whether a season length was ever declared -- and every one of those
is invisible in a ladder that looks perfectly reasonable. So every run prints the
rules in force, what was found, every card that was refused and why, every value
that was worked out rather than declared along with the exact config line that
would declare it, and every rule violation grouped by the rule it broke. A club
that is running the wrong preset has to be able to see that without reading any
code.

Exit status: 0 only when every card found was scored and nothing raised an error.
"""

from __future__ import annotations

import os
import sys

# The version gate comes first, before importing anything that needs a modern
# Python. `tomllib` is 3.11, and an ImportError traceback is a terrible way to
# learn that -- it names a module the reader has never heard of instead of the
# thing to do. Kept in a function called at module level rather than in main() so
# that it runs before the `matchcentre` imports below.
MINIMUM_PYTHON = (3, 11)


def require_python(stream=sys.stderr) -> None:
    if sys.version_info >= MINIMUM_PYTHON:
        return
    want = ".".join(str(part) for part in MINIMUM_PYTHON)
    have = ".".join(str(part) for part in sys.version_info[:3])
    print(f"error: this needs Python {want} or newer; this is Python {have}.",
          file=stream)
    print(f"  {sys.executable}", file=stream)
    print("", file=stream)
    print(f"Python {want} added the tomllib module, which is how the config file is",
          file=stream)
    print("read without installing anything. Try one of:", file=stream)
    print("  python3.11 build.py            # if a newer Python is installed",
          file=stream)
    print("  py -3.11 build.py             # on Windows", file=stream)
    print("  sudo apt install python3.11   # Debian, Ubuntu", file=stream)
    raise SystemExit(2)


require_python()

import argparse                                              # noqa: E402
import errno                                                 # noqa: E402
from dataclasses import dataclass                            # noqa: E402
from datetime import date, datetime                          # noqa: E402
from pathlib import Path                                     # noqa: E402
from typing import NamedTuple                                # noqa: E402

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    # So that `python3 /somewhere/else/build.py` finds its own package.
    sys.path.insert(0, str(ROOT))

import matchcentre                                           # noqa: E402
from matchcentre import version_line                         # noqa: E402
# The quoting of a path in a command to paste, shared with the tools' own
# commands so that one folder is quoted one way whichever program prints it.
from matchcentre import pasteable as _quote                  # noqa: E402
from matchcentre import pasteable_path                       # noqa: E402
from matchcentre import anonymise as anonymise_mod            # noqa: E402
from matchcentre import changes as changes_mod                # noqa: E402
from matchcentre import config as config_mod                  # noqa: E402
from matchcentre import discover as discover_mod              # noqa: E402
from matchcentre import export, scorecard, site               # noqa: E402
from matchcentre import careers as careers_mod               # noqa: E402
from matchcentre import scoring as scoring_mod                # noqa: E402
from matchcentre import sheet as sheet_mod                    # noqa: E402
from matchcentre import stats as stats_mod                    # noqa: E402
from matchcentre import status as status_mod                  # noqa: E402
from matchcentre.competition import (Competition,             # noqa: E402
                                     CompetitionError, Provenance,
                                     is_finals_label, rounds_played_words)
# The report's width, its fill and the parser every command refuses its
# arguments with, in the package so that the tools share them without
# loading the builder.
from matchcentre.cli import WIDTH, Parser, fill               # noqa: E402
from matchcentre.config import ConfigError                    # noqa: E402
from matchcentre.discover import DiscoveryError               # noqa: E402
from matchcentre.format import FormatError                    # noqa: E402
from matchcentre.scope import EVERY_SEASON, Scope, echoed, in_words  # noqa: E402
from matchcentre.scope import in_words_capped                # noqa: E402
from matchcentre.scoring import ScoringError                  # noqa: E402
from matchcentre.site import SiteError                        # noqa: E402
from matchcentre.slugs import label_key                       # noqa: E402

DEFAULT_DATA = "data"
DEFAULT_OUT = "site"

# How many lines of one kind the review section prints before it stops listing
# them individually. The full set is always in config.toml; the report's job is to
# make the first one obvious, not to reproduce the file.
REVIEW_LIMIT = 8


# --- report -----------------------------------------------------------------


class _Report:
    """The run's output, collected then printed once.

    Collected rather than printed as it happens for two reasons: the sections
    have to come out in a fixed order even though the work does not happen in
    that order, and ``--quiet`` has to be able to drop the middle of the report
    without leaving the blank lines that surrounded it. Plain ASCII, no colour, no
    carriage returns -- this is read as often in a piped file as on a terminal.
    """

    # The width of the label column, so that a sentence wrapped under a field lines
    # up with the value above it rather than with the margin.
    LABEL = 13

    # And of the value column, for the fields that carry a value *and* the words
    # explaining it. Only the banner does; it is a class attribute so that a test
    # can check the alignment without hard-coding the sum.
    VALUE = 24

    def __init__(self) -> None:
        self._lines: list[tuple[bool, str]] = []

    def head(self, title: str, *, always: bool = False) -> None:
        bar = "=" * max(3, WIDTH - len(title) - 4)
        self.line(always=always)
        self.line(f"== {title} {bar}", always=always)

    def line(self, text: str = "", *, always: bool = False) -> None:
        self._lines.append((always, text.rstrip()))

    def field(self, label: str, value: str, *, always: bool = False,
              keep: tuple[str, ...] = ()) -> None:
        """``label`` in its column, and ``value`` flowed from the column after it.

        Flowed as a sentence is, so a value too long to share the label's line
        starts the next one, in the value column, by the move ``flow`` makes for
        every line. Printed behind its label, a path under a deep folder ran
        ``source`` to 127 columns and ``file`` to 179, and a terminal folding it
        put the rest at the margin, where it reads as the next field. A value
        that fits is the line it always was. An empty one is the label alone,
        since a flow of no words prints nothing. ``keep`` is ``fill``'s.
        """
        if not value.strip():
            self.line(f"  {label}", always=always)
            return
        self.flow(f"  {label:<{self.LABEL}}", value, always=always, keep=keep)

    def flow(self, opening: str, text: str, *, under: str | None = None,
             always: bool = False, keep: tuple[str, ...] = ()) -> None:
        """``opening``, then ``text`` wrapped to the width the report declares.

        The one place this report wraps anything, so that there is one answer to
        what a folded line looks like. Continuations land under the text by default
        -- under the value of a field, under the word after ``note:`` -- because a
        second line that starts at the margin is indistinguishable from a new item;
        the 437-column note in the demo report came back from an eighty-column
        terminal as six of them. ``under`` overrides that for the lines whose label
        is a competition id rather than a fixed word: aligning under a name of
        unknown length either squeezes the words into a column or indents them past
        the detail lines that belong to them.

        Long words are left running over rather than cut. Only paths and config keys
        reach that length, which is to say only the words that have to be exact, and
        a club can scroll a line it cannot retype. A first word that cannot share
        the opening's line starts the next one instead, under it, as any later word
        too long for its line does: textwrap would put it behind the opening whatever
        its length, and a card's path behind a competition id ran every such warning
        in a multi-season run past the width. So a line this prints past the width
        holds one word and nothing else. The word is textwrap's, measured on the line
        it would print, since a folder named with a no-break space is two words to
        ``str.split`` and one to textwrap. An opening of spaces alone has no text to
        leave the word behind, and is not printed as a line of its own. Each of
        ``keep`` is one word, spaces and all, as ``fill`` has it.
        """
        indent = " " * len(opening) if under is None else under

        def wrapped(first: str) -> list[str]:
            return [line for line in fill(text, indent, first=first,
                                          keep=keep).split("\n") if line]

        lines = wrapped(opening)
        if opening.strip() and lines and len(lines[0]) > WIDTH:
            self.line(opening, always=always)
            lines = wrapped(indent)
        for line in lines:
            self.line(line, always=always)

    def note(self, text: str, *, always: bool = False) -> None:
        """A ``note:`` line: something the run worked out, in prose.

        A method rather than three call sites composing the same prefix, because
        these are the longest lines the report produces -- a note carries the club's
        own team names inside it -- and the fourth one added should not be able to
        invent its own indent.
        """
        self.flow("  note: ", text, always=always)

    def sentence(self, label: str, text: str, *, always: bool = False,
                 keep: tuple[str, ...] = ()) -> None:
        """A field whose value is a sentence rather than a value.

        A field flows the same way; this is the name for a value that is words.
        The ones that explain a failure are long, and hand-wrapping those where they are
        written means re-wrapping them by hand every time a word changes, which is how
        a table ends up with one line running off the edge of the terminal.
        """
        self.flow(f"  {label:<{self.LABEL}}", text, always=always, keep=keep)

    def detail(self, label: str, value: str, text: str, *,
               always: bool = False) -> None:
        """``label``, a value, and the words that say what the value means.

        Three columns, and the third wraps under itself rather than running off the
        edge: the banner's descriptions are the longest lines in the report -- the
        `unplayed` sentence reaches 113 columns under `zero` and 120 under `average`,
        measured against scoring.unplayed_detail() rather than quoted here, since a
        quotation is a second copy of a sentence that module owns -- and they are also
        the ones a club is asked to read and act on.
        Folded by an eighty-column terminal instead, the second half of the sentence
        landed hard against the left margin, where it read as another field.

        Wrapped under the description and not under the label, so that a scan down
        the value column sees preset names and nothing else.
        """
        self.flow(f"  {label:<{self.LABEL}}{value:<{self.VALUE}}", text,
                  always=always)

    def render(self, *, quiet: bool) -> str:
        kept = [text for always, text in self._lines if always or not quiet]
        out: list[str] = []
        for text in kept:
            # Collapse the blank runs that filtering leaves behind, and never
            # start the report with one.
            if not text and (not out or not out[-1]):
                continue
            out.append(text)
        while out and not out[-1]:
            out.pop()
        return "\n".join(out)


# --- entry point ------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    report = _Report()
    _stamp(report)

    try:
        return _run(args, report)
    except (ConfigError, CompetitionError, DiscoveryError, FormatError,
            ScoringError, SiteError) as exc:
        # Every one of these means "the input contradicts itself", which is a
        # sentence to print, not a stack to print. DiscoveryError was missing from
        # this tuple until a fuzz case found it: a card that had lost a set row --
        # a truncated download, or a rubber the club never printed -- made
        # `discover` refuse the competition, and the refusal came out as a
        # traceback with the whole report swallowed, so the one message that said
        # which files disagreed was never shown. Its own docstring says it is a
        # ValueError "so a caller that already handles bad input handles this
        # too"; this is the caller, and it handles them by name.
        _flush(report, args)
        print(f"error: {exc}", file=sys.stderr)
        # One of these arrives wrapped around an OSError: `config.load` reports a file
        # it could not open as a ConfigError, because from a caller's side "your
        # settings did not take effect" is the same kind of problem however it
        # happened. The number underneath it is what says which problem it is, so it
        # is asked for by name rather than assumed -- a malformed file has no `errno`
        # and no sentence here would fit it.
        if isinstance(exc.__cause__, OSError):
            _refuse_config(exc.__cause__, writing=False)
        _record_failure(args, "not-written" if isinstance(exc, SiteError)
                        else "input")
        return 2
    except OSError as exc:
        # The net under everything the three named handlers do not cover: walking
        # data/, reading a directory this account may not list, a refusal on stdout
        # itself. The site write and the config write handle their own where they
        # happen -- the site write because by this line there is a whole report to
        # print and this handler would print it before `_compose` had filled it in,
        # the config write because it is the only OSError in the run that already
        # knows which file it was about.
        #
        # No advice here, deliberately. `_os_advice` ends every sentence by offering
        # `--out`, and `_config_advice` ends every one of its own by naming
        # `--config`; a refusal from somewhere neither flag reaches is fixed by
        # neither, and advice sent to the wrong flag is worse than the number alone.
        _flush(report, args)
        print(f"error: {exc}", file=sys.stderr)
        _record_failure(args, "unopened")
        return 2
    except Exception:
        # Recorded and then let go, so the traceback and the exit status are what
        # they always were: the page says a build failed, and whoever runs it next
        # still sees why.
        _record_failure(args, "internal")
        raise


def _record_failure(args: argparse.Namespace, kind: str, count: int = 0) -> bool:
    """Tell the front page this build did not produce its figures; whether it wrote.

    Every run that ends without building anything says so, manual or scheduled,
    because the sentence is about the figures and not about who ran it: a manual
    run that found ``data/`` empty leaves exactly the stale page a scheduled one
    would. So does the run that built every document and stopped rather than write
    a player's name, whose figures never reach the page either. A flag refused
    before anything is built is not a failed build (a proposal ``--discover``
    will not write over, either ``--only`` refusal): the figures are as current as
    they were.
    ``--check`` is the exception, because it promises to write nothing.
    """
    if args.check:
        return False
    return status_mod.record_failure(args.out, kind, count)


def _stamp(report: _Report) -> None:
    """Which build wrote this report, and which Python ran it.

    Added before any work is done, and marked ``always`` so ``--quiet`` keeps it,
    because the report is the whole of what a maintainer has to go on when a club
    reports a problem -- and the two facts nobody ever thinks to include are which
    version they are running and which Python is running it. Stamping it here
    rather than in ``_compose`` means a run that dies half way through still says
    what died: every path out of ``main`` flushes this report, including the
    exception handlers below and the two refusals in ``_run`` that happen before
    any card is read.

    The line itself belongs to the package, because ``--version`` prints it too and
    a second copy of the format here is a second copy to get wrong.
    """
    report.line(version_line(), always=True)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    ap = Parser(prog="build.py", description=__doc__)
    ap.add_argument("--data", type=Path, default=Path(DEFAULT_DATA),
                    metavar="DIR",
                    help=f"tree of exported scorecard PDFs, searched recursively "
                         f"(default: {DEFAULT_DATA}/)")
    # --out and --config default to None and are filled in after the parse, so
    # that one the club typed can be told from one it did not: args.typed.
    ap.add_argument("--out", type=Path, default=None, metavar="DIR",
                    help=f"where the dashboard is written "
                         f"(default: {DEFAULT_OUT}/)")
    ap.add_argument("--config", type=Path, default=None, metavar="FILE",
                    help=f"config file to read, and to write on a first run "
                         f"(default: {config_mod.DEFAULT_PATH})")
    ap.add_argument("--check", action="store_true",
                    help="read and validate everything; write nothing at all")
    ap.add_argument("--discover", action="store_true",
                    help=f"write a config file from the cards: where one exists, "
                         f"a proposal beside it that nothing reads "
                         f"({config_mod.proposal_path(config_mod.DEFAULT_PATH)} for "
                         f"{config_mod.DEFAULT_PATH}), or over it with --force; "
                         f"with --check, neither")
    ap.add_argument("--force", action="store_true",
                    help="with --discover, rewrite the existing config file rather "
                         "than write a proposal beside it")
    ap.add_argument("--prune", action="store_true",
                    help="delete the dashboards under --out that no card builds "
                         "any more; refuses any directory holding a file this "
                         "tool did not write")
    ap.add_argument("--single-file", action="store_true",
                    help="also write each competition as one self-contained .html "
                         "page, with its season and script folded in, to send to "
                         "somebody; kept up to date afterwards without the flag")
    ap.add_argument("--players", metavar="MODE", choices=config_mod.PLAYER_MODES,
                    default=None,
                    help="print players as names, initials or numbered for this "
                         "run only, whatever [build] players says; never written "
                         "to the config file")
    ap.add_argument("--only", action="append", dest="only", default=[],
                    metavar="SEASON",
                    help="list only this season in the report; every season is "
                         "still read, checked and written. Multi-season mode only. "
                         "Repeat it for more than one season")
    ap.add_argument("-q", "--quiet", action="store_true",
                    help="print only problems and the final result")
    ap.add_argument("--generated", metavar="ISO", default=None,
                    help="date the build with this instead of the time it runs, "
                         "so that two fresh builds from the same cards and "
                         "config write the same bytes")
    # The same line the report starts with, so that a club asked to quote the
    # version can answer from a folder nobody has run yet. Argparse acts on this
    # while parsing and exits, which is why it needs neither data/ nor config.toml.
    ap.add_argument("--version", action="version", version=version_line(),
                    help="print this build and the Python running it, then stop")
    args = ap.parse_args(argv)
    args.typed = frozenset(flag for flag, value in (("--out", args.out),
                                                     ("--config", args.config))
                           if value is not None)
    if args.out is None:
        args.out = Path(DEFAULT_OUT)
    if args.config is None:
        args.config = Path(config_mod.DEFAULT_PATH)

    if args.generated is not None:
        try:
            datetime.fromisoformat(args.generated)
        except ValueError:
            ap.error(f"--generated {args.generated!r} is not an ISO timestamp; "
                     f"try 2026-08-18T19:00:00")
    if any(not value.strip() for value in args.only):
        ap.error(f"--only needs a season's name: the name of its folder under "
                 f"{args.data}/", keep=(f"{args.data}/",))
    if args.force and not args.discover:
        ap.error("--force only means anything with --discover")
    if args.prune and args.check:
        # Refused during the parse, the way --force without --discover is, and not
        # resolved in favour of one of them. --check promises that nothing at all
        # is written, deleting a directory is the loudest possible way to break
        # that promise, and a run that quietly honoured one flag would be a run
        # that did the opposite of what half the command line asked for.
        ap.error("--prune deletes directories and --check writes nothing at all; "
                 "ask for one or the other")
    if args.prune and args.only:
        # Refused the same way, and for the same reason: the dashboards left behind
        # are listed from every season whatever --only says, so plain --prune is
        # already the command that removes exactly what the report named. Pruning
        # inside the season named would be a second rule for the one action that
        # deletes, and pruning everything anyway would ignore half the command line.
        ap.error("--only shortens the report and --prune removes folders from every "
                 "season; run --prune without --only")
    return args


def _run(args: argparse.Namespace, report: _Report) -> int:
    if not args.data.is_dir():
        # Flushed before the refusal, not after, so stdout holds the stamp and
        # stderr holds the reason: this is the earliest a run can end, and it used
        # to end with the version printed nowhere. It is also one of the two
        # refusals a club is likeliest to meet on a first evening, which makes it
        # one of the likeliest to be pasted into an issue.
        _flush(report, args)
        print(f"error: no such directory: {args.data}", file=sys.stderr)
        # The `error:` line above stays one line whatever the path costs -- that is
        # the line a cron job greps -- and the sentence under it is filled, like every
        # other piece of advice this program writes to stderr. Hand-wrapped, a path
        # longer than the width put `and run this again` past the edge.
        print(fill(f"Copy the scorecard PDFs you exported from Match Centre into "
                   f"{args.data} and run this again.", keep=(str(args.data),)),
              file=sys.stderr)
        # Last, where the command is the final thing on the screen, and only when
        # this program can see a folder of cards the club's own command could not.
        for line in _cards_elsewhere(args):
            print(line, file=sys.stderr)
        _record_failure(args, "no-folder")
        return 2

    # 2. Load the config. A missing file is normal -- it is what a first run looks
    #    like -- but a file that exists and is malformed is not, and `load` raises.
    cfg = config_mod.load(args.config)
    # `--discover` over a config that exists writes the file `--force` would write
    # beside it, and builds as a plain run (4.431). It refused, exit 2, before a card
    # was read, so a club with a file it typed could not ask what the cards would
    # write without losing the draw and its comments. A file of the proposal's name
    # this program did not write, or cannot read, is refused here, before the work.
    proposed = config_mod.proposal_path(args.config) if _proposes(args, cfg) else None
    in_the_way, unread = (_in_the_way(proposed) if proposed is not None
                          else (False, None))
    if in_the_way and not args.check:
        _flush(report, args)            # the other refusal that precedes the work
        # A file it could not read is not known to be a club's either, so it is not
        # said to be one: the reason is named, as for a config that will not open.
        if unread is None:
            print(f"error: {proposed} is there, and this program did not write it.",
                  file=sys.stderr)
        else:
            print(f"error: {proposed}: cannot be read — {unread.strerror or unread}",
                  file=sys.stderr)
        # The marker on a line of its own, as it stands in the file: flowed into
        # the sentence, its own comma ran on into the words after it.
        print(fill(("" if unread is None else
                    "So this run cannot tell whether it wrote that file. ")
                   + f"--discover writes its proposal under that name, beside "
                     f"{args.config}, and writes over only a file whose first line "
                     f"is the one each proposal it writes begins with:",
                   keep=(str(args.config),)), file=sys.stderr)
        print(f"    {config_mod.PROPOSAL_MARKER}", file=sys.stderr)
        print("  Make it readable by you, or move it aside, and run this again."
              if unread is not None and unread.errno in (errno.EACCES, errno.EPERM)
              else "  Rename it or move it, and run this again.", file=sys.stderr)
        return 2
    if args.only and not cfg.multi_season:
        return _refuse_only_single(report, args, cfg)

    # 3. Read every PDF under data/. A page that cannot be read comes back as a
    #    Rejection rather than an exception, so one bad file cannot end the run
    #    (F4 is about reporting them all, not stopping at the first). A data/ this
    #    account may not list is not one of those: the walk would see it as empty
    #    and report "no scorecards", so it is opened first and its OSError goes to
    #    main's net -- exit 2, the errno, and "could not be opened" on the page.
    scorecard.open_folder(args.data)
    cards, rejected = scorecard.read_tree(args.data,
                                          expect=cfg.declared_format())

    # 4. Group the cards into competitions, tracking where each identity came from.
    #    The notes that send a club to a line of the config are worded from the
    #    file as it stands, or, where this run writes it, from what it writes.
    found = discover_mod.discover(
        cards, rejected, config=cfg, root=args.data,
        config_text=(None if _writes_config(args, cfg)
                     else _config_on_disk(args.config)))

    # Which seasons the report lists. Checked here, once the cards say which seasons
    # there are and before the config is rewritten or a file written, so a name that
    # is not a season costs a club nothing but the reading.
    scope, known = EVERY_SEASON, {}
    if args.only:
        known = _known_seasons(found, cfg, root=args.data)
        scope, refusal = _only_scope(args, known, found)
        if refusal is not None:
            _flush(report, args)
            for line in refusal:
                print(line, file=sys.stderr)
            return 2

    config_text = ""
    config_unwritable: OSError | None = None
    config_target = args.config
    try:
        config_text = _maybe_write_config(args, cfg, found, len(cards))
    except OSError as exc:
        # Kept and carried to the report rather than ending the run here, which is what
        # this used to do -- two statements after every card had been read and filed, and
        # one statement before any of it reached the report. Nothing below depends on the
        # file that failed: the dashboard is built from the config as it was *read*, and
        # on the first run that is the defaults. So the refusal costs a club a file this
        # program offered to write, and not the evening's tennis. What it does change is
        # the exit status, in `_finish`.
        config_unwritable = exc
    proposal: _Proposal | None = None
    if proposed is not None:
        # The same file `--discover --force` would write, from the same call, so the
        # summary the review gives of it is read off what a club would adopt. A
        # proposal that cannot be written is carried as the config's write is.
        proposal = _Proposal(proposed, _config_written(cfg, found, len(cards)),
                             in_the_way=in_the_way, unread=unread)
        if not args.check:
            try:
                text, kept = _proposal_text(proposal, args=args, cfg=cfg,
                                            cards=len(cards))
                if not kept:
                    proposed.write_text(text, encoding="utf-8")
                proposal = proposal._replace(written=True, kept=kept)
            except OSError as exc:
                config_unwritable, config_target = exc, proposed

    # Every competition here has cards behind it: `discover` builds this list from
    # the cards it read, and drops a competition whose cards it could not use. A
    # declared competition nobody has played yet is not one of these and never
    # reaches this loop -- it is listed on the front page, by `site`, from the config
    # section that declares it. The guard that used to stand here skipped a case
    # that cannot arrive, and said the picker did something it did not.
    seasons: list[dict] = []
    sheets: dict[str, sheet_mod.Comparison | None] = {}
    for comp in sorted(found.club.competitions, key=lambda c: c.id):
        comp_cards = found.cards.get(comp.id, [])
        comp_matches = found.matches.get(comp.id, [])
        # The association's sheet against this competition's cards, compared once
        # here and handed to the findings and to the report alike, so the two can
        # never count the teams compared differently.
        declared = cfg.competition(comp.id)
        sheets[comp.id] = sheet_mod.compare(
            comp_matches, comp, declared.expected if declared else None)
        seasons.append(export.build_season(
            comp_matches, competition=comp, source_dir=str(args.data),
            generated=args.generated, club=found.club, cards=comp_cards,
            rejected=found.rejected, seasons_mode=cfg.seasons_mode,
            sheet=sheets[comp.id], config_name=config_mod.named(args.config),
            strays=found.strays.get(comp.id, ())))

    # What the build before this one left where these documents are about to go.
    # Read here, before the write, because that is the only moment both versions
    # exist -- and read for `--check` too, which is the run whose whole purpose is
    # to say what a build would change without changing it.
    previous = {season["meta"]["competition"]:
                site.previous_season(args.out, season, multi=cfg.multi_season)
                for season in seasons}

    # Players' names withheld, when [build] players or --players says so: the
    # finished documents rewritten and then searched, and no dashboard written if
    # a name is still in one. After `previous`, which is read from the named
    # documents like the rest of the report, and before the write, which is handed
    # the withheld ones. The report keeps the names: it is for whoever holds the
    # cards, and fixing config.toml needs them.
    withholding = _withhold(args, cfg, found, seasons)
    refused = withholding is not None and bool(withholding.refused)
    # A refused build is a failed one: every card was read, and the figures on the
    # page stop at the last build that worked. Recorded here, before the report is
    # composed, because the report says whether the front page's file was written.
    status_written = refused and _record_failure(args, "withheld")

    # How this run prints players, resolved here once: `--players` for this run
    # over `[build] players`. The callers of `careers.writes_careers` that decide
    # what this run writes or says -- the write, the left-behind scan and the
    # report -- are handed it, so none of them decides the careers page from the
    # config or the flag alone.
    players, _ = _players_mode(args, cfg)

    written: site.SiteWrite | None = None
    unwritable: OSError | None = None
    if not args.check and seasons and not refused:
        try:
            written = site.write_site(args.out, seasons=(
                                          seasons if withholding is None
                                          else withholding.seasons),
                                      club=found.club,
                                      config=cfg, generated=args.generated,
                                      single_file=args.single_file,
                                      published=withholding is not None,
                                      single_file_for=scope.covers,
                                      players=players)
        except OSError as exc:
            # Caught here rather than left to main's handler, because by this line
            # every card has been read, filed, scored and validated, and none of that
            # is in the report yet -- `_compose` is the next statement. A folder owned
            # by another user, a full disk, a name already taken by a file: not one of
            # those says anything about the cards, so the reading stands and is still
            # printed. What changes is the last word and the exit status.
            unwritable = exc

    # The competitions whose document this run put where the next run reads it:
    # every one when the write returned, and none when no write was tried. A
    # write that failed can have finished some first -- `write_site` goes one
    # competition at a time, and writes the front page after all of them -- and
    # its error names only the file it stopped at. So each document is read back
    # the way `previous` was, and one that is not what the last build left is this
    # run's: the next run measures from it, and will not list its changes again.
    # Each dashboard writes its document last, so one that moved is a dashboard
    # this run wrote whole.
    reached: set[str] = set()
    if written is not None:
        reached = {season["meta"]["competition"] for season in seasons}
    elif unwritable is not None:
        reached = {season["meta"]["competition"] for season in seasons
                   if site.previous_season(args.out, season, multi=cfg.multi_season)
                   != previous[season["meta"]["competition"]]}

    # What is under --out that this run did not write. Only when the run actually
    # built something: pointed at an empty folder -- a mistyped --data, a drive
    # that did not mount -- every dashboard on disk is one this run is not
    # writing, and listing them all would be advice to delete a club's whole site
    # on the strength of having read no cards. And only when the write worked,
    # because a half-written tree is already the subject of its own paragraph.
    orphans: list[site.Orphan] = []
    pruned: dict[Path, int] = {}
    refusals: dict[Path, str] = {}
    # Nor when a folder of cards could not be listed: the dashboard this run did not
    # write may be the one those cards build, and --prune would delete it for that.
    if (seasons and unwritable is None and not refused
            and not any(r.folder for r in found.rejected)):
        keep = {season["meta"]["competition"]:
                site.competition_dir(args.out, season, multi=cfg.multi_season)
                for season in seasons}
        orphans = site.find_orphans(
            args.out, keep=keep,
            writes_careers=careers_mod.writes_careers(cfg, players))
        for orphan in orphans if args.prune else ():
            try:
                pruned[orphan.path] = len(site.remove_orphan(orphan))
            except (SiteError, OSError) as exc:
                # One refusal does not end the prune. The directories are
                # independent, and a club whose first stale folder holds a file of
                # its own would otherwise have to clear that one before any of the
                # others could go.
                refusals[orphan.path] = str(exc)

    _compose(report, args=args, cfg=cfg, found=found, cards=cards,
             rejected=rejected, seasons=seasons, written=written,
             reached=reached, config_text=config_text,
             strays=scorecard.unread_files(args.data),
             orphans=orphans, pruned=pruned, refusals=refusals,
             previous=previous, sheets=sheets, withholding=withholding,
             scope=scope, known=known, players=players,
             status_written=status_written, proposal=proposal)

    # Two stages can refuse a card and the run has to count both. The reader refuses a
    # file it cannot read; the filing above refuses a card it cannot turn into a
    # fixture -- no set rows, a banner with no name in it, both banners naming the same
    # team. From data/ they are one event: a PDF that is in none of the figures.
    # `found.rejected` is the reader's list plus the filing's, and it is the one the
    # page has always been given, so counting anything else here is the report and the
    # dashboard disagreeing about the same file. A folder the reader could not list
    # is among them, and counted as a folder: what it holds is what nobody knows.
    unlisted = sum(1 for r in found.rejected if r.folder)
    unscored = len(found.rejected) - unlisted
    # The errors a competition's cards have. A refused card is in every document's
    # errors as F4, and it has just been counted once as a card not scored.
    errors = sum(1 for comp_id, _ in _findings(seasons, "errors") if comp_id)
    # What an earlier build left that still prints the names this run withholds:
    # the dashboards counted, and the careers page, which is not one, said apart.
    named = [orphan for orphan in orphans
             if withholding is not None and orphan.players == "names"
             and orphan.path not in pruned]
    # And what --prune could not remove, less those: a copy that still prints
    # names is said once, by the names it prints, whether it is a dashboard or the
    # careers page. Decided here for both, so `_finish` only words the clauses.
    kept = [orphan for orphan in orphans
            if orphan.path in refusals and orphan not in named]
    status = _finish(report, args=args, cards=cards, filed=_filed(found),
                     seasons=seasons, written=written, unscored=unscored,
                     unlisted=unlisted, errors=errors, unwritable=unwritable,
                     config_unwritable=config_unwritable,
                     config_target=config_target, proposal=proposal,
                     prune_failed=sum(1 for orphan in kept
                                      if orphan.kind != "careers"),
                     withholding=withholding,
                     named_left=sum(1 for orphan in named
                                    if orphan.kind != "careers"),
                     before=_players_before(args, cfg, seasons, previous),
                     careers_kept=any(orphan.kind == "careers"
                                      for orphan in kept),
                     careers_named=any(orphan.kind == "careers"
                                       for orphan in named),
                     careers_file=next((orphan.path.name for orphan in orphans
                                        if orphan.kind == "careers"),
                                       site.CAREERS_PAGE))
    _flush(report, args)
    if config_unwritable is not None:
        # The one-line form on stderr as well, for the same reason as the write below:
        # a cron job that keeps only stderr still learns which file it was and why. This
        # is the only OSError in the run that already knows which file it was about --
        # `[Errno 17] File exists: 'notes.txt'` on its own does not say that a config
        # file was being written at all, let alone which one, on a run given `--config`.
        # Named the way `config.load` names the other direction, so the two refusals read
        # as the pair they are.
        print(f"error: {config_target}: cannot be written — {config_unwritable}",
              file=sys.stderr)
        if proposal is not None:
            advice = _proposal_advice(config_unwritable, args=args)
            if advice:
                print(fill(advice), file=sys.stderr)
        else:
            _refuse_config(config_unwritable, writing=True)
    if unwritable is not None:
        # On stderr as well, in the one-line form every other refusal in this program
        # uses, so that a cron job which keeps only stderr still learns why.
        _refuse_os_error(unwritable)
        _record_failure(args, "not-written")
    elif refused:
        # The same for the refusal that leaves the page behind the cards. It points
        # at the report's section and names nobody, so it can be mailed as it is.
        print("error: a player's name survived withholding, so no dashboard was "
              "written", file=sys.stderr)
        print(fill(f'The report\'s "{_REFUSED}" section says where, by the name\'s '
                   f"shape and never the name."), file=sys.stderr)
    elif not seasons:
        # A folder first: "the one scorecard it found could not be scored" says
        # nothing of the folders beside it, and the report names both.
        _record_failure(args, "unopened" if unlisted
                        else "none-read" if unscored else "no-cards",
                        count=unscored)
    return status


@dataclass(frozen=True)
class _Withholding:
    """What a run that withholds players' names did to the documents it builds."""

    mode: str
    from_flag: bool
    anonymised: dict[str, anonymise_mod.Anonymised]    # by competition id
    refused: list[anonymise_mod.Refusal]

    @property
    def seasons(self) -> list[dict]:
        """The documents the write is handed, in the order the named ones came."""
        return [done.season for done in self.anonymised.values()]


def _players_mode(args: argparse.Namespace,
                  cfg: config_mod.Config) -> tuple[str, bool]:
    """How this run prints players, and whether ``--players`` is what said so."""
    if args.players is not None:
        return args.players, True
    return cfg.players_mode, False


def _withhold(args: argparse.Namespace, cfg: config_mod.Config, found,
              seasons: list[dict]) -> _Withholding | None:
    """The run's documents with players' names withheld, and every name still in them.

    ``None`` when this run prints names, which is every run that never set the
    option: those build exactly what they always did. Each document is published
    here, once, and rewritten; the search reads the rewritten documents, and the
    write is handed them as already published, so the search is of the bytes
    that would reach the disk.
    """
    mode, from_flag = _players_mode(args, cfg)
    if mode not in anonymise_mod.WITHHOLDING or not seasons:
        return None
    shown = [export.published(season) for season in seasons]
    # And each line a card left out of its squads and quoted in a warning, which no
    # document holds anywhere else, so the run learns it from the cards themselves.
    dropped = [line for cards in found.cards.values() for card in cards
               for line in card.dropped]
    done, refused = anonymise_mod.withhold_run(shown, cfg, found.club, mode=mode,
                                               dropped=dropped)
    # Each rewrite under the competition its own `meta` names, and not paired with
    # the named documents by position, so no order can give one another's count.
    return _Withholding(mode=mode, from_flag=from_flag,
                        anonymised={one.season["meta"]["competition"]: one
                                    for one in done},
                        refused=refused)


def _players_before(args: argparse.Namespace, cfg: config_mod.Config,
                    seasons: list[dict], previous: dict) -> list[tuple[Path, str]]:
    """``(folder, how it printed players)`` for each dashboard an earlier build left.

    Only the ones this run's competitions would write over, in the report's order,
    and read off the document on disk by the rule the left-behind list reads by.
    """
    out = []
    for season in seasons:
        before = previous.get(season["meta"]["competition"])
        if isinstance(before, dict):
            out.append((site.competition_dir(args.out, season,
                                             multi=cfg.multi_season),
                        site.players_of(before.get("meta"))))
    return out


def _players_row(done: anonymise_mod.Anonymised) -> str:
    """A competition's ``players`` row: how many, how, and what it cost the page."""
    mode = done.season["meta"]["players"]
    said = f"{done.players} withheld, " + ("numbered" if mode == "numbered"
                                           else "by initials")
    if done.shared:
        said += f"; {done.shared} of them share initials and are numbered"
    if done.withheld:
        said += f"; {done.withheld} message(s) on the page withheld whole"
    return said


def _players_result(report: _Report, *, args: argparse.Namespace,
                    withholding: _Withholding | None, before) -> None:
    """The ``players`` line of ``== result``, whenever it has something to say.

    A withheld run says so every time, ``--quiet`` included, because it is a
    promise about every file the run wrote. A run printing names says nothing new,
    unless it is about to put names back on a dashboard that withheld them: that
    can be deliberate, and it has to be seen.
    """
    if withholding is not None:
        how = withholding.mode + (", --players" if withholding.from_flag else "")
        built = "would build" if args.check else "built"
        report.sentence("players", f"withheld ({how}): no player's full name is in "
                                   f"any dashboard this run {built}", always=True)
        return
    withheld = [folder for folder, players in before if players != "names"]
    if not withheld:
        return
    if len(withheld) == 1:
        where = f"{withheld[0]} withheld" if not args.check else \
            f"{withheld[0]} withholds"
    else:
        where = (f"{len(withheld)} dashboard(s) under {args.out} "
                 + ("withhold" if args.check else "withheld"))
    tail = "them, and a build would not" if args.check else "them until this run"
    report.sentence("players", f"names: {where} {tail}", always=True)


# What a refusal tells the club to do, by the kind of place the name was found in.
# {config} is the config file the build read, by config.named, or "your config
# file" where its name is one of the places refused.
_RENAME = {
    "label": "Rename it in {config}: a team under [team_names], a competition "
             "under its [competitions] section.",
    "front": "Rename it in {config}: the title under [build], the club under "
             "[club], a competition under its [competitions] section.",
    "file": "Rename the file, and give its new name to --config.",
    "key": "Rename it: a competition's id is the name of its [competitions] "
           "section in {config}, and a season's key the name of its folder "
           "under {data}.",
}


# The heading of that section, which the refusal's stderr line sends a club to.
_REFUSED = "players: no dashboard written"


def _players_refusal_section(report: _Report, *, args: argparse.Namespace,
                             refused: list, before,
                             status_written: bool = False) -> None:
    """Every player's name the build found where it was about to write it.

    Printed whatever --quiet says, because the run ends on it. Each place gives
    the name's shape and never the name, so a club can paste the section into a
    report of its own, and it ends on what is on disk: nothing written by this run
    is not the same as no name published, when an earlier build printed them. The
    one file it may have written is the front page's status, which it names.
    """
    report.head(_REFUSED, always=True)
    file_refused = any(found.kind == "file" for found in refused)
    for found in refused:
        where = (f"{found.place} and {found.others} other place(s)"
                 if found.others else found.place)
        several = bool(found.others)
        # The file's name, unless it is the name refused: this section gives a
        # name by its shape and never as it stands.
        config = ("your config file" if file_refused
                  else config_mod.named(args.config))
        advice = _RENAME.get(found.kind, "").format(data=args.data, config=config)
        if found.card:
            advice = (f"It is in a line under a squad on {found.card} that was read "
                      f"as a heading and may be a player's name. {advice}")
        if found.kind == "fault":
            said = (f"{where} {'still hold' if several else 'still holds'} a "
                    f"player's name, {found.shape}, after the names were "
                    f"withheld. That is a fault in this program and not in your "
                    f"cards: please report it as SECURITY.md describes.")
        elif found.kind == "key":
            said = (f"{where} {'hold' if several else 'holds'} a player's name or "
                    f"a word of one, {found.shape}, and the dashboard's folder and "
                    f"web address are made from it. {advice}")
        else:
            said = (f"{where} {'print' if several else 'prints'} a player's name, "
                    f"{found.shape}, as it stands. {advice}")
        report.line(f"  {found.owner}", always=True)
        report.flow("      ", said, always=True)
    report.line("", always=True)
    # "To --out", because on a first run this run did write something: config.toml.
    said = f"Nothing was written to {args.out} by this run."
    if status_written:
        said = (f"Nothing was written to {args.out} by this run but "
                f"{args.out / site.STATUS_FILE}, so the front page now says the "
                f"last build failed.")
    named = [str(folder) for folder, players in before if players == "names"]
    if before and not named:
        said += (f" {args.out} already holds {len(before)} of its dashboards from "
                 f"an earlier build, and none of them prints a player's name.")
    elif before:
        said += (f" {args.out} already holds {len(before)} of its dashboards from "
                 f"an earlier build, and {len(named)} of them "
                 f"{'prints' if len(named) == 1 else 'print'} players' "
                 f"names: {in_words(named)}. The lines above give each name's "
                 f"shape and never the name, so this section can be quoted as it "
                 f"is.")
    report.flow("  ", said, always=True)


def _flush(report: _Report, args: argparse.Namespace) -> None:
    """Print the whole report at once, at the very end.

    Built up and then printed rather than streamed, so that a run which raises
    part way through prints nothing half-finished, and so the ladder columns can be
    sized from the widest team name in it.
    """
    text = report.render(quiet=args.quiet)
    if not text:
        return
    try:
        print(text)
        sys.stdout.flush()
    except BrokenPipeError:
        # `build.py | head` closes the pipe early. Without this the failure lands
        # on the interpreter's own final flush instead, which prints a traceback
        # nobody asked for and turns a finished build into exit 120. Pointing the
        # descriptor at os.devnull is the documented way to make the rest of
        # shutdown silent.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())


# --- when the operating system says no --------------------------------------

# Why a write was refused, in words, for the handful of reasons a club actually
# meets. Keyed by the name in `errno.errorcode` rather than by the number, which
# means nothing to a reader of this table, or by the exception class, since
# PermissionError covers two causes that want different advice. A name this platform
# does not define simply never comes back from `errorcode`, so the entry lies unused
# instead of having to be guarded.
_NOT_ALLOWED = ("this program is not allowed to write there. Check who owns the "
                "folder -- a run under sudo leaves one root owns -- close anything "
                "that has a file in it open, or build somewhere you can write with "
                "--out.")
_OS_ADVICE = {
    "EACCES": _NOT_ALLOWED,
    "EPERM": _NOT_ALLOWED,
    "ENOSPC": ("the disk is full. Free some space, or build onto a disk that has "
               "room with --out."),
    "EDQUOT": ("you are over your disk quota, so the disk has room and this account "
               "may not use it. Ask for more, delete something of your own, or build "
               "somewhere else with --out."),
    "EROFS": ("that disk is mounted read-only, so nothing at all can be written to "
              "it. Remount it, or build somewhere you can write with --out."),
    "ENOTDIR": ("part of that path is a file, not a folder. Rename or move the file, "
                "or point --out somewhere else."),
    "EISDIR": ("a folder is already there under a name the dashboard needs for a "
               "file. Move it aside, or point --out somewhere else."),
    "ENAMETOOLONG": ("that path is longer than this filesystem allows. Build closer "
                     "to the top of the disk with --out."),
}


def _os_advice(exc: OSError) -> str:
    """What to do about a refusal from the operating system, in words.

    ``[Errno 13] Permission denied: 'site/index.html'`` is the whole of what Python
    gives, and it names the file, and the number, and nothing to do about it. A club
    that hits it has one of a few ordinary problems -- a folder owned by another user,
    a full disk, a read-only drive -- and the number is the only clue which. Returns
    ``""`` for a reason with no sentence rather than guessing at one: ``strerror`` is
    printed either way, and advice that sends a club to the wrong place costs more
    than no advice at all.
    """
    # `exc.errno` is None on an `OSError("...")` raised by hand, which `errorcode`
    # answers with the default like any other key it does not hold.
    return _OS_ADVICE.get(errno.errorcode.get(exc.errno, ""), "")


# The same job for `config.toml`, and a separate table because the sentences above
# cannot be borrowed for it: every one of them ends by offering `--out`, which is the
# way out of a folder the dashboard cannot be written to and no use at all for the
# file that says how this competition scores a match.
#
# Reading and writing get tables of their own, and the difference is more than
# wording. A config file that could not be *written* is this program's own suggestion,
# so writing it somewhere else costs the club nothing. A config file that could not be
# *read* is the club's settings, and the obvious way out -- point `--config` at
# something else -- is the one thing not to do, because the scoring preset lives in
# there and a build without it is a plausible, wrong ladder. So the read sentences
# name the flag in order to warn against it.
_CONFIG_NOT_READABLE = (
    "this program is not allowed to read that file. Check who owns it -- one run "
    "under sudo leaves the config it wrote owned by root -- and make it readable by "
    "you again. Pointing --config at a different file is the one thing not to do: "
    "this one holds the scoring your competition uses, and a build without it is a "
    "ladder computed under a preset nobody chose.")
_CONFIG_READ_ADVICE = {
    "EACCES": _CONFIG_NOT_READABLE,
    "EPERM": _CONFIG_NOT_READABLE,
    "EISDIR": ("that path is a folder, not a file. Point --config at the file itself, "
               "or -- if the folder is standing where the file should be -- move it "
               "aside and put the config file back under that name."),
    "ENOTDIR": ("part of that path is a file, not a folder, so nothing under it can be "
                "opened. --config takes the whole path to the config file, not the "
                "folder to look for it in."),
    "ENAMETOOLONG": ("that path is longer than this filesystem allows. Move the config "
                     "file closer to the top of the disk and name it with --config."),
}
_CONFIG_NOT_WRITABLE = (
    "this program is not allowed to write there. Nothing of yours is at risk: this "
    "file is the program's suggestion, filled in from the cards, and not anything you "
    "have entered. Check who owns the folder, or write it somewhere you can with "
    "--config and copy it back when you can.")
_CONFIG_IN_THE_WAY = (
    "part of that path is a file, not a folder, so the folder this config file needs "
    "cannot be made. Move or rename the file, or point --config at a path whose "
    "folders are folders.")
_CONFIG_WRITE_ADVICE = {
    "EACCES": _CONFIG_NOT_WRITABLE,
    "EPERM": _CONFIG_NOT_WRITABLE,
    "EEXIST": _CONFIG_IN_THE_WAY,
    "ENOTDIR": _CONFIG_IN_THE_WAY,
    "EISDIR": ("a folder is already there under the name the config file needs. Move "
               "it aside, or write the file under another name with --config."),
    "EROFS": ("that disk is mounted read-only, so nothing at all can be written to it. "
              "Remount it, or write the config somewhere you can with --config."),
    "ENOSPC": ("the disk is full. Free some space, or write the config somewhere that "
               "has room with --config."),
    "EDQUOT": ("you are over your disk quota, so the disk has room and this account "
               "may not use it. Ask for more, delete something of your own, or write "
               "the config somewhere else with --config."),
}


def _config_advice(exc: OSError, *, writing: bool) -> str:
    """What to do about a config file the operating system would not open.

    ``writing`` rather than one table for both, because the two failures are two
    different problems: the file that cannot be written is this program's suggestion
    and the file that cannot be read is the club's own settings. A club told to write
    it somewhere else when the trouble is reading it would build a dashboard under
    defaults nobody chose, which is the failure this project exists to prevent.

    Returns ``""`` for a reason with no sentence, on the same rule as ``_os_advice``:
    ``strerror`` is printed either way, and a guess costs more than silence.
    """
    table = _CONFIG_WRITE_ADVICE if writing else _CONFIG_READ_ADVICE
    return table.get(errno.errorcode.get(exc.errno, ""), "")


def _proposal_advice(exc: OSError, *, args: argparse.Namespace) -> str:
    """What to do about a proposal the operating system would not write.

    Not the config's table: each of those ends in ``--config``, and a proposal is
    written beside the file ``--config`` names, so moving that would move the club's
    own config to get a file this program suggests. ``""`` for a reason with none,
    as there.
    """
    folder = f"the folder {args.config} is in"
    return {
        "EACCES": f"this program is not allowed to write in {folder}, and the "
                  f"proposal is written beside it. Check who owns the folder.",
        "EPERM": f"this program is not allowed to write in {folder}, and the "
                 f"proposal is written beside it. Check who owns the folder.",
        "EISDIR": "a folder is already there under the name the proposal needs. "
                  "Move it aside, and run this again.",
        "EROFS": "that disk is mounted read-only, so nothing at all can be written "
                 "to it. Remount it, and run this again.",
        "ENOSPC": "the disk is full. Free some space, and run this again.",
        "EDQUOT": "you are over your disk quota, so the disk has room and this "
                  "account may not use it. Ask for more, or delete something of "
                  "your own.",
    }.get(errno.errorcode.get(exc.errno, ""), "")


def _refuse_config(exc: OSError, *, writing: bool) -> None:
    """The way out of a config file that would not open, under the line naming it.

    The line above it is printed by whoever caught the failure, because only they know
    which direction it was -- ``config.load`` says "cannot be read" in the message it
    raises, and the write says "cannot be written" where it happens.
    """
    advice = _config_advice(exc, writing=writing)
    if advice:
        print(fill(advice), file=sys.stderr)


def _refuse_os_error(exc: OSError) -> None:
    """The one-line refusal on stderr, with the reason under it."""
    print(f"error: {exc}", file=sys.stderr)
    advice = _os_advice(exc)
    if advice:
        print(fill(advice), file=sys.stderr)


# --- one season at a time (--only) ------------------------------------------


def _refuse_only_single(report: _Report, args: argparse.Namespace,
                        cfg: config_mod.Config) -> int:
    """``--only`` in single-season mode, refused before a card is read.

    Every card is one season there, keyed by nothing, so there is nothing to leave
    out. Ignoring the flag would print a report that looks narrowed and is not, and
    reading the cards first would spend a club's time on a run that cannot do what
    it asked.
    """
    _flush(report, args)
    print("error: --only needs multi-season mode, and this run is a single season",
          file=sys.stderr)
    if cfg.exists:
        why = (f'{args.config} does not set seasons = "multi" under [build], so '
               f"every card under {args.data}/ is one season and there is no other "
               f"season to leave out.")
    else:
        why = (f"There is no {args.config} yet, and without one every card under "
               f"{args.data}/ is one season, so there is no other season to leave "
               f"out.")
    print(fill(f"{why} Run it again without --only. To give each folder under "
               f"{args.data}/ a season of its own, see \"Several seasons\" in "
               f"README.md.", keep=(str(args.config), str(args.data))),
          file=sys.stderr)
    return 2


def _known_seasons(found, cfg: config_mod.Config, *,
                   root: Path) -> dict[str, tuple[str, bool]]:
    """Every season ``--only`` may name: key -> (label, whether a card is in it).

    Newest first, in the front page's order. A season is known from a card filed
    in it, from a card refused in its folder -- a season whose every card failed to
    read is the one a club most needs to look at -- or from ``config.toml`` naming
    it, which is what lets a command the club types or scripts itself name a
    season before the season's first card. The label is the one its dashboards
    carry, else the config's.

    Every key the config declares is read by the folder rule, the one the name is
    read by: ``"2027-Winter"`` is the season ``data/2027-Winter/`` will be, labelled
    by what that key says, and a key with nothing in it to read -- which no folder
    can match -- is no season at all.
    """
    filed = discover_mod.seasons_read(found.club, found.rejected, root=root)
    declared: dict[str, str] = {}
    for key in cfg.season_labels:
        season = discover_mod.season_key_of_folder(key)
        declared.setdefault(season, cfg.season_label(season))
    for comp in cfg.competitions.values():
        declared.setdefault(discover_mod.season_key_of_folder(comp.season_key), "")
    labels: dict[str, str] = {}
    for comp in found.club.competitions:
        labels.setdefault(comp.season_key, comp.season)
    keys = sorted((filed | set(declared)) - {""}, key=site.season_order_key,
                  reverse=True)
    return {key: (labels.get(key) or declared.get(key, ""), key in filed)
            for key in keys}


def _only_scope(args: argparse.Namespace, known: dict[str, tuple[str, bool]],
                found) -> tuple[Scope, list[str] | None]:
    """The seasons ``--only`` named, or the stderr lines refusing them.

    The one place a value is decided to be a season: turned into a key by the
    router's own rule, so capitals and spaces do not matter, and then looked up. A
    name that is not a season is refused rather than narrowed to, because a report
    of a season nothing is filed in lists nothing and says nothing is wrong.
    """
    keys: list[str] = []
    bad: list[str] = []
    seen: set[str] = set()
    for value in args.only:
        key = discover_mod.season_key_of_folder(value)
        if key in known:
            if key not in keys:
                keys.append(key)
        elif (key or value.strip()) not in seen:
            seen.add(key or value.strip())
            bad.append(value.strip())
    if not bad:
        return Scope(tuple(keys)), None
    lines = [f"error: --only {', '.join(_quote(value) for value in bad)}: "
             f"{'not a season' if len(bad) == 1 else 'not seasons'} this run found"]
    for hint, command in (_only_hint(args, value, known, found) for value in bad):
        if hint:
            lines.append(fill(hint, keep=tuple(map(_quote, bad))))
        if command:
            lines.extend(["", *command, ""])
    if not known:
        lines.append(fill(f"No card under {args.data}/ has been filed into a season "
                          f"yet, so there is nothing to choose between. Nothing was "
                          f"written.", keep=(str(args.data),)))
        return EVERY_SEASON, lines
    lines.append("  The seasons this run found, newest first:")
    width = max(len(key) for key in known) + 4
    for key, (label, has_cards) in known.items():
        said = " ".join(part for part in (label, "" if has_cards else (
            f"(named in {args.config.name}; no card is filed in it)")) if part)
        lines.append(fill(said, " " * (4 + width), first=f"    {key:<{width}}",
                          keep=(args.config.name,)) if said else f"    {key}")
    lines.append("  Capitals and spaces in a name do not matter. Nothing was written.")
    return EVERY_SEASON, lines


def _only_hint(args: argparse.Namespace, value: str,
               known: dict[str, tuple[str, bool]], found) -> tuple[str, list[str]]:
    """What a name that is not a season is instead, when it is something: a
    season's label, the path to a season's folder, or a competition's id, and
    ``("", [])`` when it is none of them. And the command that names the season,
    as the lines it is printed on, for the sentence to end at its colon above.

    Named rather than accepted, because labels repeat -- "Season 1" is printed on
    every card of some clubs' every season -- and a rule that works while they
    happen not to is a rule a club learns twice.
    """
    carriers = [key for key, (label, _) in known.items()
                if label and label_key(label) == label_key(value)]
    if len(carriers) == 1:
        return (f'"{value}" is the label of {carriers[0]}, and --only takes the '
                f"folder's name:", _command_hint(args, f"--only {carriers[0]}"))
    if carriers:
        return (f'"{value}" is the label of {in_words(carriers)}; --only takes the '
                f"folder's name, which tells them apart.", [])
    # What a shell completes `--only data/` to. Named rather than accepted: a path
    # would be a second way to spell a season, with rules of its own for a round's
    # folder inside it and for a path outside data/.
    folder = discover_mod.season_key_of_folder(Path(value).name)
    if Path(value).name != value and folder in known:
        return (f"{_quote(value)} is the path to a season's folder, and --only takes "
                f"the folder's name alone:", _command_hint(args, f"--only {folder}"))
    key = discover_mod.season_key_of_folder(value)
    comp = next((comp for comp in found.club.competitions
                 if key and comp.id == key and comp.season_key in known), None)
    if comp is None:
        return "", []
    return (f"{value} is a competition in {comp.season_key}, and --only picks whole "
            f"seasons:", _command_hint(args, f"--only {comp.season_key}"))


# --- config -----------------------------------------------------------------


def _maybe_write_config(args: argparse.Namespace, cfg: config_mod.Config,
                        found, card_count: int) -> str:
    """Write ``config.toml`` when this is a first run, or when asked to.

    Returns the text written, empty when nothing was, so the review section can
    report what the file actually says instead of assuming. ``--discover`` over a
    file that exists, without ``--force``, writes nothing here: ``_run`` writes
    the proposal beside the file instead (4.431).

    The dashboard is still built in the same run, from the config as it was *read*.
    `config.render` writes what this run worked out commented, the names among it,
    so re-reading the file would declare nothing the club did not. Building from
    the config as read still keeps every "derived" mark in this report true of this
    run, whatever a line the club already uncommented says.

    A file that wrote them live was read back by the next run as the club's word:
    that run moved the teams and captains off "derived" and rewrote four of the
    demo's nine files, with nothing new on any card.
    """
    if not _writes_config(args, cfg):
        return ""
    text = _config_written(cfg, found, card_count)
    args.config.parent.mkdir(parents=True, exist_ok=True)
    args.config.write_text(text, encoding="utf-8")
    return text


def _writes_config(args: argparse.Namespace, cfg: config_mod.Config) -> bool:
    """Whether this run writes the config: a first run, or ``--discover --force``,
    and never ``--check``, which writes nothing."""
    return not args.check and (not cfg.exists or (args.discover and args.force))


def _proposes(args: argparse.Namespace, cfg: config_mod.Config) -> bool:
    """Whether this run proposes a config beside the one there: ``--discover``
    over a file that exists, without ``--force``. ``--check`` proposes one in the
    report, and writes nothing."""
    return args.discover and cfg.exists and not args.force


def _in_the_way(path: Path) -> tuple[bool, OSError | None]:
    """Whether a file is at the proposal's *path* that this run may not write
    over, and the reason it could not be read, where that is why.

    Known by its first line, as the demo's config is: a file of the name that does
    not begin so is a club's, and ``--discover`` will not write over it. Only the
    line is read, and as bytes, so a file saved in another encoding is one that
    does not begin so. What this program writes there is a file, so a folder or a
    link to nothing is not one of its own. A file it cannot read is not known to
    be either, and is not written over; the reason is returned, so the refusal
    says that and not whose the file is. Said as a club's, the refusal of a
    proposal this program wrote and could not read back sent the club to rename a
    file whose permissions were the trouble.
    """
    if not os.path.lexists(path):
        return False, None
    if not path.is_file():
        return True, None
    try:
        with path.open("rb") as handle:
            head = handle.read(len(config_mod.PROPOSAL_MARKER) + 1)
    except OSError as exc:
        return True, exc
    return not config_mod.written_as_proposal(head.decode("utf-8", "replace")), None


class _Proposal(NamedTuple):
    """The config a ``--discover`` over one that exists proposes beside it.

    ``body`` is the file ``--discover --force`` would write, which the review's
    summary is read off; ``written`` whether the file there holds this run's
    proposal once it is done, which ``--check`` and a write that failed leave it
    not, and ``kept`` whether it held it already, and so was left as it was;
    ``in_the_way`` whether a file of its name that this program did not write, or
    could not read, is there, which only ``--check`` gets past, and ``unread`` the
    reason it could not be read.
    """
    path: Path
    body: str
    written: bool = False
    in_the_way: bool = False
    kept: bool = False
    unread: OSError | None = None


def _proposal_text(proposal: _Proposal, *, args: argparse.Namespace,
                   cfg: config_mod.Config, cards: int) -> tuple[str, bool]:
    """The proposal this run writes at ``proposal.path``, and whether the file
    there holds it already.

    Dated as the site's pages are (``site._dated_as_last_changed``): rendered with
    the date the file there was written on, and where that is the file there byte
    for byte, kept with it, and left alone. Dated by the run, every ``--discover``
    without ``--generated`` wrote the file again with the clock's minute, 9:35am
    and then 9:36am over the same cards and config, in a run that said all 9 files
    of the site already held what it would write. A file edited by hand, or one
    whose body the cards have since moved, is the run's to date. Rejected:
    comparing the body under the row of dashes alone, which would keep a header
    another version of this program wrote, with its version and its words.
    """
    def text(written: str) -> str:
        return config_mod.proposal(proposal.body, config_name=args.config.name,
                                   written=written, cards=cards,
                                   drops=_what_force_drops(cfg))

    try:
        on_disk = proposal.path.read_bytes()
    except OSError:
        on_disk = b""
    was = config_mod.proposal_written(on_disk.decode("utf-8", "replace"))
    if was is not None and text(was).encode("utf-8") == on_disk:
        return text(was), True
    return text(site.pretty_date(
        args.generated or datetime.now().replace(microsecond=0).isoformat(),
        with_time=True, with_year=True)), False


def _config_written(cfg: config_mod.Config, found, card_count: int) -> str:
    """The config file a run writes, and the one ``--discover --force`` would.

    One call for both, so that what the review's rewrite offer says lands in the
    file is read off the file that command writes.
    """
    return config_mod.render(cfg, competitions=sorted(found.club.competitions,
                                                      key=lambda c: c.id),
                             club_name=found.club_name, cards=card_count,
                             matches=found.matches)


# --- report sections --------------------------------------------------------


def _compose(report: _Report, *, args: argparse.Namespace,
             cfg: config_mod.Config, found, cards, rejected,
             seasons: list[dict], written: site.SiteWrite | None,
             reached: set[str],
             config_text: str, strays=(), orphans=(), pruned=None,
             refusals=None, previous=None, sheets=None,
             withholding=None, scope: Scope = EVERY_SEASON, known=None,
             players: str = "names", status_written: bool = False,
             proposal: _Proposal | None = None) -> None:
    # Counted from the documents this run built, which are the ones the careers
    # page is written from, so --check prints the figure a build would publish.
    # First, and through the page's own conversion, so a document the page cannot
    # add up stops --check where it stops a build: before any section is printed.
    # A run that writes no page only because it withholds names says so instead,
    # where the figure would be: a page that goes without a word looks like a fault.
    # A write that returned says what it wrote, and is not worked out again.
    careers_said = None
    if seasons and careers_mod.writes_careers(cfg, players):
        careers_said = _careers_line(
            *(written.careers if written is not None
              else site.careers_summary(args.out, seasons, multi=cfg.multi_season)),
            multi=cfg.multi_season)
    elif seasons and _careers_withheld(cfg, players):
        careers_said = CAREERS_WITHHELD.format(
            part=careers_mod.part(cfg.multi_season))
    _rules_section(report, cfg, found)
    _cards_section(report, args=args, cards=cards, rejected=rejected,
                   found=found, cfg=cfg, strays=strays, careers=careers_said,
                   scope=scope,
                   only=_only_sentence(args, found=found, seasons=seasons,
                                       scope=scope, known=known or {},
                                       written=written))
    # `found.rejected`, not the reader's list: the filing refuses cards too, and this
    # is the section that names every card found and not scored.
    _rejected_section(report, found.rejected)
    for season in _listed(seasons, scope):
        competition = season["meta"]["competition"]
        _competition_section(report, season, sheet=(sheets or {}).get(competition),
                             anonymised=(None if withholding is None
                                         else withholding.anonymised[competition]))
    _review_section(report, seasons=seasons, found=found, cfg=cfg,
                    args=args, config_text=config_text, scope=scope,
                    cards=len(cards), proposal=proposal)
    _problems_section(report, seasons, scope=scope)
    # After the warnings, because what it counts is theirs and the review's, and
    # before the changes, which stay the last thing said about the figures.
    _not_listed_section(report, args=args, seasons=seasons, found=found, cfg=cfg,
                        scope=scope, written=written)
    # After the problems and before the folder: this is about the figures, and it
    # is the last thing said about them. A club that reads to here has the rules,
    # the cards, the ladder and the complaints, which is everything the sentence
    # "R2 Hilltop Aces v Parkview Wanderers has gone" has to be read against.
    # In the tense of the write, competition by competition: `reached` is what
    # this run's write put on disk, which a write that failed part way has done
    # for some competitions and not for others.
    _changes_section(report, args=args, seasons=seasons, previous=previous or {},
                     reached=reached, scope=scope)
    # Last, against the `result` section that follows it, because it is the only
    # section about the output directory rather than about the cards -- and the
    # last two things a club reads are then "this folder is stale" and the line
    # saying which folder was just written. In the tense of the front page: a
    # run whose write returned has rewritten it, and --check, the one other run
    # that lists what it left, has not.
    _left_behind_section(report, args=args, cfg=cfg, orphans=orphans,
                         pruned=pruned or {}, refusals=refusals or {},
                         withheld=withholding is not None, players=players,
                         wrote=written is not None, seasons=seasons)
    if withholding is not None and withholding.refused:
        _players_refusal_section(report, args=args, refused=withholding.refused,
                                 before=_players_before(args, cfg, seasons,
                                                        previous or {}),
                                 status_written=status_written)


def _rules_section(report: _Report, cfg: config_mod.Config, found) -> None:
    """The three settings that decide the numbers, spelled out, every run.

    None of them can be inferred from a scorecard, all of them reorder the
    ladder, and a club running the wrong preset sees nothing wrong on the
    dashboard itself. So they are printed before anything else, in words rather
    than as preset names.
    """
    rules = cfg.ruleset_for()
    set_rule = cfg.set_rule_for()
    declared = cfg.declared_format()

    report.head("rules in force")
    report.detail("scoring", rules.name, rules.describe())
    report.detail("sets", set_rule.name, set_rule.describe())
    # Invisible in the ladder's order and plainly visible in its points column,
    # which is the combination that makes it worth a line of its own: a club
    # comparing totals against its association's sheet has no other way to tell
    # which convention produced the number in front of it.
    unplayed = (scoring_mod.UNPLAYED_AVERAGE if rules.average_unplayed
                else scoring_mod.UNPLAYED_ZERO)
    report.detail("unplayed", unplayed,
                  scoring_mod.unplayed_detail(rules.average_unplayed))
    # Only where it is set, unlike the lines above. Every club has a scoring scheme
    # and a convention for a bye; almost none turn this on, and a row saying "draws
    # are allowed" would cost the rest of them a line to repeat what the scoring line
    # two above already prints. The club that did turn it on is the club with a night
    # missing from its ladder, and this is the line that accounts for it.
    if not rules.allows_draw:
        report.detail("draws", "no", scoring_mod.draws_detail(rules.allows_draw))
    if declared is None:
        report.detail("format", "read from each card",
                      "the row bands and marker column of every card")
    else:
        report.detail("format", declared.name, declared.describe())
        report.detail("", "", f"a result needs {declared.min_sets_for_result} "
                              f"of {len(declared.slots)} sets complete")
    report.line()
    # Where they come from is not said: from a line of the file, or from the
    # defaults of a file that holds none, and "remove the #" of a default is the
    # step a club that says it is wrong must not take. The file is the one read, as
    # --config gave it, which is how the note on the rules names it, but not the
    # demo's, which the demo writes over on its next run.
    footer = ("The scoring and the sets are never read from a card: no scorecard "
              "records how its competition awards points, and 6-3 looks the same "
              "whether the set was played to 6 or to 8.")
    name = str(cfg.path)
    if not cfg.demo:
        footer += (f" If the lines above are not your competition's, set yours "
                   f"under [rules] in {name} and re-run.")
    report.flow("  ", footer, keep=(name,))
    # The loader's warnings, then the ones only the cards could settle: both are about
    # a line of the club's file, and both are printed where a quiet run prints them.
    for warning in (*cfg.warnings, *found.config_warnings):
        # Through `sentence` rather than printed as it stands: with the section name
        # of a competition in front of it and a suggestion behind it, this is the
        # longest line the report can produce.
        report.sentence("config", warning, always=True)


def _filed(found, comp_ids=None) -> int:
    """How many cards became fixtures.

    Not the number of pages the reader understood: a card can read perfectly and still
    have no fixture in it -- both banners naming the same team, no set rows to score --
    and the filing refuses those. Counted from the cards each competition kept, which
    is the same list the pages are built from, so ``scored`` cannot claim a card the
    dashboard does not have.

    ``comp_ids`` counts only those competitions', for ``--only``'s share of the same
    figure: one count restricted, rather than a second count from somewhere else.
    """
    return sum(len(comp_cards) for comp_id, comp_cards in found.cards.items()
               if comp_ids is None or comp_id in comp_ids)


def _cards_section(report: _Report, *, args: argparse.Namespace, cards,
                   rejected, found, cfg: config_mod.Config,
                   strays=(), careers: str | None = None,
                   scope: Scope = EVERY_SEASON, only: str = "") -> None:
    filed = _filed(found)
    # What the reader saw, counted from the reader's own two lists and before any
    # filing. Deriving it from the two lines below instead would make the arithmetic
    # add up by construction, which is the one thing it must not do.
    # A folder it could not list is no page, and is said on a line of its own.
    seen = len(cards) + sum(1 for r in rejected if not r.folder)
    refused = [r for r in found.rejected if not r.folder]
    unlisted = len(found.rejected) - len(refused)

    report.head("cards")
    report.field("source", str(args.data), keep=(str(args.data),))
    # Every page found is one of the two below (F4). They are printed even when
    # the second is zero, because the reader's question is "did it see all of my
    # cards?" and only the arithmetic answers it.
    report.field("pages seen", str(seen))
    report.field("scored", str(filed))
    report.field("refused", str(len(refused)), always=bool(refused))
    if unlisted:
        report.field("not opened", f"{unlisted} folder(s)", always=True)
    if filed + len(refused) != seen:
        # And if the arithmetic is what answers it, then it has to be arithmetic
        # rather than a restatement: `pages seen` is what the reader saw, counted
        # before any of this, so the two lines under it can fail to add up. No input
        # is known to produce that -- every stage that drops a card records a
        # rejection for it -- which is exactly why it is said out loud instead of
        # trusted. A card in none of the three numbers is a card nobody is looking for.
        report.note(f"{seen - filed - len(refused)} page(s) are in neither line "
                    f"above. That is a fault in this program, not in your cards; "
                    f"please report it.", always=True)
    report.field("competitions", str(len(found.club.competitions)))
    if cfg.multi_season:
        keys = ", ".join(found.club.season_keys) or "none"
        # A sentence, so that a club of many seasons has the list wrapped under
        # its value rather than run past the width.
        report.sentence("seasons", f"multi: {keys}")
    else:
        report.field("seasons", "single (every card is one season)")
    if careers is not None:
        # A sentence, so that a line longer than the width is wrapped under its
        # label; not `always`, so that --quiet hides it as it hides `seasons`.
        report.sentence("careers", careers)
    if only:
        report.sentence("only", only)
    _strays_note(report, args=args, strays=strays)
    for note in _listed_notes(found, scope)[0]:
        report.note(note)


# The careers field of a run that withholds names, which writes no careers page:
# that it wrote none, and why, in the place the count would be. A label is one
# season document's, which in single-season mode is one competition's: *part* is
# the page's own word for one of its rows (`careers.part`).
CAREERS_WITHHELD = "no page: this run withholds names, and a label is one {part}'s"


def _careers_withheld(cfg: config_mod.Config, players: str) -> bool:
    """Whether withholding names is all that keeps this run from a careers page.

    Asked of the one decider twice, and of nothing else: a build of this config
    printing names would write the page, and this run does not.
    """
    return (careers_mod.writes_careers(cfg, "names")
            and not careers_mod.writes_careers(cfg, players))


def _careers_line(counts: careers_mod.Counts,
                  split: site.CareersSplit | None = None, *,
                  multi: bool = True) -> str:
    """What the careers page adds up, in one field under ``seasons``.

    People who played, never people named: somebody who is only ever on a roster
    has no season to span, and counting them would make the page's whole point --
    how many careers run across seasons -- look smaller than it is. In
    single-season mode the page adds up competitions, and *counts* is of them.

    And, once the page is split by initial, why: the size it would be as one
    page, or that an earlier build split it. Never a size on a page that fits,
    which is a number there is nothing to do about. And a page of the split still
    past the limit by itself, the largest initial's first at a few thousand
    careers, is named with its size, so a split never reads as the page brought
    under it.
    """
    part = careers_mod.part(multi)
    if counts.with_results == 0:
        said = f"no {part} has a result in it yet"
    elif counts.with_results == 1:
        said = f"one {part} so far; totals add up from the second"
    else:
        said = (f"{counts.spanning} of {counts.players} player(s) played in more "
                f"than one {part}")
    if split is None:
        return said
    limit = _megabytes(site.CAREERS_LIMIT)
    if split.kept:
        said = (f"{said}. An earlier build split the page, so careers.html stays "
                f"an index and each initial keeps a page of its own")
    else:
        said = (f"{said}. As one page it would be {_megabytes(split.size)}, over "
                f"the {limit} the page is split at, so careers.html is an index "
                f"and each initial has a page of its own")
    if not split.over:
        return said
    (name, size), *more = split.over
    said = f"{said}. Even split, {name} comes to {_megabytes(size)}, over {limit}"
    if more:
        said += f", and {len(more)} more of its page(s) do too"
    return said


def _megabytes(size: int) -> str:
    """Bytes as megabytes to a tenth, rounded up.

    Up, so a page past the limit never reads as one at it: 1,500,001 bytes is
    1.6 MB and not the 1.5 MB it is over.
    """
    return f"{-(-size // 100_000) / 10:.1f} MB"


# How many stray names to print before summarising. A club that keeps a season of
# photographs beside its cards should not lose the rest of the report to them, and
# five is enough to recognise what they are.
_STRAY_NAMES = 5


def _strays_note(report: _Report, *, args: argparse.Namespace, strays) -> None:
    """The files in the folder that were never offered to the reader.

    One line, not one per file, and not a refusal: these are a club's own furniture
    and none of them is a missing card -- the file that stands for a missing card is
    the unfinished download, and the reader refuses that by name. What this closes is
    "I put the file in and nothing happened": a club that saved the Match Centre page
    instead of exporting it has an .html or a .png here, and until now the run read it,
    said nothing about it, and reported no cards.
    """
    if not strays:
        return
    shown = [str(Path(p)) for p in strays[:_STRAY_NAMES]]
    more = len(strays) - len(shown)
    listed = in_words([*shown, *([f"{more} more"] if more else [])])
    report.note(f"{len(strays)} file(s) under {args.data} are not PDFs and were "
                f"not read: {listed}. Match Centre exports a scorecard as a PDF; "
                f"anything else here is left alone.")


def _refused_clauses(cards: int, folders: int,
                     card: str = "card(s) found and not scored") -> list[str]:
    """The refusals counted as the report counts them: the cards, then the folders.

    One rule for the refused section's heading, the last line under it and each
    competition's ``problems`` line, which calls a card *card*. A folder the
    reader could not list is refused beside the cards and said as a folder,
    because how many cards are in it is exactly what nobody knows.
    """
    return ([f"{cards} {card}"] if cards else []) + (
        [f"{folders} folder(s) not opened"] if folders else [])


def _rejected_section(report: _Report, rejected) -> None:
    """Every PDF that was found and not scored, by path (F4).

    Listed even under ``--quiet``. A card that vanishes silently is the most
    expensive failure this tool can have: the ladder stays plausible and one team
    is simply short a match.
    """
    if not rejected:
        return
    folders = sum(1 for r in rejected if r.folder)
    report.head(f"refused: "
                f"{in_words(_refused_clauses(len(rejected) - folders, folders))}",
                always=True)
    for item in sorted(rejected, key=lambda r: (r.path, r.page)):
        where = item.path if item.page == 0 else f"{item.path} page {item.page + 1}"
        report.line(f"  {where}", always=True)
        # Hanging two rather than aligned, here and in the problems section: the
        # reason and the detail are siblings at the same indent, so a wrap that came
        # back to six would read as a third line of its own.
        report.flow("      ", item.reason, under=" " * 8, always=True)
        if item.detail:
            report.flow("      ", item.detail, under=" " * 8, always=True)

    # The doctor reads pages, so it is offered on a file that has one. A download that
    # never finished has nothing structural to diagnose and its reason above already
    # says what to do about it; the doctor could only add "this is not a readable PDF"
    # about a file everybody already knows is half-written, and that is a dead end
    # dressed as the next step. Offered on a real PDF among them, or not at all. Nor
    # on a folder it could not open, whatever it is called: a folder has no pages.
    diagnosable = [item for item in sorted(rejected, key=lambda r: r.path)
                   if Path(item.path).suffix.lower() == ".pdf" and not item.folder]
    if not diagnosable:
        return
    report.line(always=True)
    # Not "any one of them" over a list with a folder in it, which it is not for.
    whom = ("any file among them" if any(item.folder for item in rejected)
            else "any one of them")
    report.line(f"  Point the doctor at {whom} for the full structural",
                always=True)
    report.line("  reason, and what usually causes it:", always=True)
    # A path handed to a program, so through the one rule for those: quoted by
    # hand, a card under `--data ./-cards` was offered as `-cards/cut.pdf`, which
    # the doctor refused as a flag it does not have. The doctor is the project's,
    # so from anywhere else the line goes there first, as the cd hint does.
    cd_to = None if Path.cwd().resolve() == ROOT else ROOT
    card = pasteable_path(diagnosable[0].path, project=cd_to)
    for line in _paste_lines([("python3", "tools/doctor.py"), (card,)], cd=cd_to,
                             indent=" " * 6):
        report.line(line, always=True)


def _competition_section(report: _Report, season: dict, *,
                         sheet: sheet_mod.Comparison | None = None,
                         anonymised: anonymise_mod.Anonymised | None = None
                         ) -> None:
    meta = season["meta"]
    rules = meta["rules"]
    prov = meta["provenance"]

    report.head(meta["competition"])
    report.field("name", meta["label"])
    report.field("rules", f"{rules['scoring']} / {rules['set_rule']} / "
                          f"{rules['format']}")
    # Sentences, as a long season has more rounds than one line holds. Never an
    # empty list, which the "none" this once fell back on said: every competition
    # this is called for has cards (the comment above `seasons` in `main`).
    report.sentence("cards", f"{meta['cards_scored']} across "
                             f"{sheet_mod.rounds_listed(meta['rounds_present'])}")
    report.field("teams", f"{meta['teams_total']} ({prov['teams']})")
    if anonymised is not None:
        report.sentence("players", _players_row(anonymised))
    if meta["rounds_total"] is None and prov["rounds_total"] == "declared":
        # A length was declared and F3 refused it, which the document says by
        # giving none: not "not declared", which the club that typed it would read
        # as the program not having seen it.
        report.sentence("rounds", f"{meta['rounds_played']} played; the season was "
                                  f"declared shorter than that (F3), so no "
                                  f"percentage is shown")
    elif meta["rounds_total"] is None:
        # F3: no percentage, and the report says why rather than leaving a reader
        # to wonder where the progress bar went.
        report.sentence("rounds", f"{meta['rounds_played']} played; season length "
                                  f"not declared, so no percentage is shown")
    else:
        report.field("rounds", f"{meta['rounds_played']} of "
                               f"{meta['rounds_total']} played")
    report.field("draw", f"{meta['draw_source']} ({prov['draw']})")
    # The state's words are the document's, as F10's and the pages' are: played
    # where every team's counted is its P, and counted where not (4.458).
    if meta["ladder_state"] == "complete":
        report.field("ladder", f"complete: {meta['ladder_detail']}")
    elif meta["ladder_state"] == "empty":
        # Every team on nothing is no unequal numbers, and nobody is placed (4.428).
        report.field("ladder", f"not started: {meta['ladder_detail']}")
    else:
        report.field("ladder", f"in progress: {meta['ladder_detail']}")
    _ladder(report, season)
    _sheet_sentence(report, sheet, {t["slug"]: t["name"] for t in season["teams"]})

    # The competition's own errors, and the refused cards apart from them: every
    # document carries every refusal, and the errors section prints each once.
    # With them the line leaves out "-- see below", which would wrap to a word.
    everything = season["validation"]["errors"]
    errors = len(export.own_findings(everything))
    refused = len(everything) - errors
    warnings = len(season["validation"]["warnings"])
    if errors or warnings or refused:
        # A folder among them is said as the refused list says it: as no card.
        folders = meta["folders_unlisted"]
        said = _refused_clauses(refused - folders, folders, card="refused card(s)")
        tail = (f" and the run's {in_words(said)}" if refused
                else " -- see below")
        report.sentence("problems", f"{errors} error(s), {warnings} warning(s)"
                                    f"{tail}")


def _sheet_sentence(report: _Report, sheet: sheet_mod.Comparison | None,
                    names: dict[str, str]) -> None:
    """How much of the association's sheet was compared, in one flowed sentence.

    Printed only when a table is in use, and it names every team that was not
    compared and why, so "all agree" is never read as covering a team nobody
    checked. The findings themselves are in the problems section: this line only
    counts them, from the same comparison.
    """
    if sheet is None:
        return
    number = sheet.after_round
    if sheet.status == sheet_mod.AHEAD:
        report.sentence("sheet", f"after round {number}: not compared -- see "
                                 f"sheet-ahead below")
        return
    if sheet.status == sheet_mod.NO_LADDER:
        report.sentence("sheet", f"after round {number}: not compared -- there is "
                                 f"no ladder (see below)")
        return
    teams = f"{sheet.teams_total} team{'' if sheet.teams_total == 1 else 's'}"
    compared = len(sheet.checks)
    if not compared:
        text = f"after round {number}: none of the {teams} compared"
    else:
        out = len(sheet.disagreeing)
        verdict = ("all agree" if not out else
                   f"{out} {'differs' if out == 1 else 'differ'} -- see below")
        text = f"after round {number}: {compared} of {teams} compared, {verdict}"
    groups = []
    for reason in (sheet_mod.CARD_MISSING, sheet_mod.NEARLY_NAMED,
                   sheet_mod.NOT_TYPED):
        held = [names.get(slug, slug) for slug, why in sheet.held if why == reason]
        if held:
            groups.append(f"{in_words(held)} ({reason})")
    if groups:
        text += "; not compared: " + ", ".join(groups)
    report.sentence("sheet", text)


def _ladder(report: _Report, season: dict) -> None:
    teams = sorted(season["teams"], key=lambda t: (t.get("position") or 999,
                                                   t["slug"]))
    if not teams:
        return
    width = max(8, min(28, max(len(t["short"]) for t in teams)))
    # What each average divides by, where a credited round makes it more than P, as
    # the page's Counted column is shown (4.459). Three wide and a space: at the
    # name's cap of 28 the table was 74 columns, and the report's width is 78.
    credited = stats_mod.rounds_credited({t["slug"]: t["counted"] for t in teams},
                                         {t["slug"]: t["played"] for t in teams})
    count = f" {'cnt':>3}" if credited else ""
    report.line()
    report.line(f"   {'#':>2}  {'team':<{width}}  {'P':>2} {'W':>2} {'L':>2} "
                f"{'D':>2}  {'pts':>6}{count}  {'avg':>5}  {'games':>9}")
    for team in teams:
        # Half-up on the printed decimal, which is what the spreadsheet a club
        # compares this against does. A plain format spec rounds the stored double
        # instead, and 6.225 is stored a hair under a half: it prints 6.22 where the
        # club's own noticeboard says 6.23.
        average = stats_mod.round_half_up(team.get("points_average"), 2)
        shown = "    -" if average is None else f"{average:5.2f}"
        points = stats_mod.round_half_up(team["points"], 1)
        count = f" {team['counted']:>3}" if credited else ""
        report.line(f"   {team.get('position') or '-':>2}  "
                    f"{team['short'][:width]:<{width}}  "
                    f"{team['played']:>2} {team['won']:>2} {team['lost']:>2} "
                    f"{team['drawn']:>2}  {points:>6.1f}{count}  {shown}  "
                    f"{team['games_won']:>4}-{team['games_lost']:<4}")


def _review_section(report: _Report, *, seasons: list[dict], found,
                    cfg: config_mod.Config, args: argparse.Namespace,
                    config_text: str, scope: Scope = EVERY_SEASON,
                    cards: int = 0, proposal: _Proposal | None = None) -> None:
    """Every identity this program worked out, and the line that would declare it.

    This is the part of the report that earns the config file. A derived value is
    not wrong, but it is a guess this program made and cannot check, and the only
    way a club can tell it apart from a fact is if something says so.

    A run that proposed a config says here, before the values, what the proposal
    offers that the config lacks (:func:`_proposal_summary`), on a run with no
    value to list too: the proposal is this section's question asked of the whole
    file.
    """
    items = _review_list(seasons, found, cfg, scope=scope)
    if not items and proposal is None:
        return

    report.head(f"review: {len(items)} value(s) worked out, not declared")
    # Flowed rather than wrapped by hand, because `--config` can name a path longer
    # than the whole report and these sentences are built around it. Hand-wrapped at
    # a column that assumed a short path, what a terminal folds away is everything
    # after the path -- which here is the half that says the dashboard already works.
    #
    # Where each value is, from the file on disk, on every run: the run after the
    # one that wrote it asked for every value again, "Add these to config.toml", of
    # a file that held each one behind a #, and a run with no file said nothing of
    # the draw, which every other run says is for the report only. With no file it
    # is asked of an empty one, whose answer is "not in it" for every value.
    text = config_text or _config_on_disk(args.config)
    if not items:
        pass
    elif config_text:
        report.flow("  ", f"{args.config} has just been written from the cards, and "
                          f"each value below says whether it is in there live, "
                          f"behind a #, or not in it at all. The dashboard already "
                          f"works either way.")
    elif cfg.exists:
        report.flow("  ", f"Each value below says whether {args.config} has it live, "
                          f"behind a #, or not at all, and adopting one turns a "
                          f"guess into a fact. The dashboard works either way; "
                          f"declared values simply stop being marked as derived on "
                          f"it.")
        _rewrite_offer(report, cfg=cfg, args=args, items=items,
                       rewrite=_config_written(cfg, found, cards))
    else:
        # Every competition a run with no file lists has its draw in the list, the
        # one value that is for the report only.
        report.flow("  ", f"Add these to {args.config} to turn a guess into a "
                          f"fact, except any said below to be for this report "
                          f"only. The dashboard works either way; declared values "
                          f"simply stop being marked as derived on it.")
    if proposal is not None:
        _proposal_summary(report, args=args, cfg=cfg, found=found, items=items,
                          text=text, proposal=proposal)
    # A table two values open is one the file can hold once, so those two are told
    # to share it; one the file has already, that its lines go under it.
    tables = [item.table for item in items]
    for item in items:
        report.line()
        report.line(f"  {item.subject}")
        report.flow("      why: ", item.why)
        where = config_mod.holding(item.lines, text)
        report.flow(f"      in {args.config.name}: ",
                    _held_status(item, where.status, at=where.at,
                                 opens=config_mod.opens(item.table, text),
                                 shared=tables.count(item.table) > 1),
                    under=" " * 8)
        for line in item.lines:
            # Through the TOML writer rather than printed as it stands: a team list
            # or a round of the draw is wider than the report, and these are the
            # lines a club selects with a mouse and pastes into its config file. It
            # is given this section's indent rather than handed the lines to indent
            # afterwards, so that what it fits to and what is printed are the same.
            for part in config_mod.toml_lines(line, width=WIDTH, indent=" " * 6):
                report.line(part)


def _proposal_summary(report: _Report, *, args: argparse.Namespace,
                      cfg: config_mod.Config, found, items: list[_Item], text: str,
                      proposal: _Proposal) -> None:
    """What a proposed config offers that the config it was written beside lacks.

    In the config's terms, since that is the file a club has to change: the
    competitions the cards reach that it does not declare, the teams the cards
    name that a list it declares, or its draw, lacks, the captains and home courts
    the cards give where it names none or another, and every other value below
    that it does not hold at all, each counted, with the lines that declare it.
    An offer the file holds already, behind a ``#`` or live, is not one it lacks,
    so a first run's file asked again is told it is offered nothing; one that
    differs from a line the file declares is listed whatever else the file holds,
    since the holding reader calls any declared key live, and two values for one
    line are for the club to choose between. Read off the proposal's own rules
    (:func:`config.captain_offers`, :func:`config.court_offers`), so a line
    listed here is a line in the file, and each group stops at ``REVIEW_LIMIT``
    lines, as a value of the review does.
    """
    name, proposed = args.config.name, proposal.path
    # Under the values' own paragraph; a review with none opens with this.
    if items:
        report.line()
    besides = (f"the file --discover --force would write over {name}, with "
               f"everything {name} declares kept live and what the cards offer "
               f"besides behind a #")
    if proposal.written:
        # Kept as the site's pages are, and said in the words of their line.
        done = (f"{proposed}, beside {args.config}, {_HELD}, so it was left as it "
                f"was" if proposal.kept
                else f"{proposed} has been written beside {args.config}")
        lead = (f"{done}: {besides}. Nothing reads it, and {name} is as it was. Its "
                f"first lines say how to adopt it, and what adopting it whole loses.")
    elif args.check:
        lead = (f"--check writes nothing, so {proposed} was not written. It would "
                f"be {besides}.")
        if proposal.in_the_way:
            whose = ("this run cannot read" if proposal.unread is not None
                     else "this program did not write")
            lead += (f" A file of that name is there already that {whose}, so a run "
                     f"without --check would refuse to write over it.")
    else:
        lead = (f"{proposed} could not be written, as the end of this report says. "
                f"It would be {besides}.")
    report.flow("  ", lead, keep=(str(proposed), str(args.config)))

    by_id = {comp.id: comp for comp in found.club.competitions}
    undeclared = [comp_id for comp_id in sorted(by_id)
                  if cfg.competition(comp_id) is None]
    groups: list[tuple[str, list[list[str]]]] = []

    def header(comp_id: str, table: str = "") -> str:
        return (f"[competitions.{config_mod.toml_key(comp_id)}"
                f"{'.' + table if table else ''}]")

    def lacks(lines: list[str]) -> bool:
        return config_mod.held_in(lines, text) == "absent"

    if undeclared:
        groups.append((f"{len(undeclared)} competition(s) the cards reach that "
                       f"{name} does not declare:",
                       [[header(comp_id)] for comp_id in undeclared]))
    declared = [(by_id[comp_id], cfg.competition(comp_id))
                for comp_id in sorted(by_id) if comp_id not in undeclared]
    # Whichever closed the list, its ``teams`` or its draw: the draw's list missed
    # Riverside Rockets, which F8 named on the same run.
    lists, strangers = [], []
    for comp, section in declared:
        if comp.strangers:
            lists.append(config_mod.team_list_lines(
                comp.id, [*config_mod.listed_teams(comp, section), *comp.strangers]))
            strangers.extend(comp.strangers)
    if lists:
        groups.append((f"{len(strangers)} team(s) the cards name that a declared "
                       f"list lacks, {in_words(_capped(strangers))}, with "
                       + ("the list that adds " + ("it:" if len(strangers) == 1
                                                   else "them:")
                          if len(lists) == 1 else "the lists that add them:"),
                       lists))
    for table, offered, words in (
            ("captains", config_mod.captain_offers,
             ("captain(s) the (C) marks give where", "names")),
            ("courts", config_mod.court_offers,
             ("home court(s) the home cards print where", "declares"))):
        new: list[list[str]] = []
        other: list[list[str]] = []
        for comp, section in declared:
            for offer in offered(comp, section):
                line = (f"{config_mod.toml_key(offer.key)} = "
                        f"{config_mod.toml_value(offer.value)}")
                if offer.instead is None:
                    if lacks([header(comp.id, table), line]):
                        new.append([header(comp.id, table), line])
                    continue
                # A captain typed as "" names nobody, so it is one the file names
                # none for; its line is shown all the same, which the offer replaces.
                (other if offer.named else new).append(
                    [header(comp.id, table),
                     f"# in place of {config_mod.toml_key(offer.key)} = "
                     f"{config_mod.toml_value(offer.instead)}", line])
        for entries, verb in ((new, "none"), (other, "another")):
            if entries:
                groups.append((f"{len(entries)} {words[0]} {name} {words[1]} "
                               f"{verb}:", entries))
    # The review's own values, of the competitions the file declares: a value it
    # holds nowhere that the proposal holds behind a # is one more offer, and is
    # listed below already, with its lines.
    whole = {header(comp_id) for comp_id in undeclared}
    others = [item.subject for item in items
              if not item.report_only and item.table not in whole
              and lacks(item.lines)
              and config_mod.held_in(item.lines, proposal.body) == "commented"]
    if others:
        groups.append((f"{len(others)} other value(s) {name} does not hold at all, "
                       f"each listed below and behind a # in {proposed.name}: "
                       f"{in_words(_capped(others))}.", []))

    report.line()
    would = "offers" if proposal.written else "would offer"
    if groups:
        report.flow("  ", f"Against {name}, it {would}:")
    else:
        report.flow("  ", f"{proposed.name} {would} nothing {name} lacks.")
    missing = [comp_id for comp_id in sorted(cfg.competitions) if comp_id not in by_id]
    if missing:
        groups.append((f"{len(missing)} competition(s) {name} declares that no card "
                       f"reached this run, which {proposed.name} writes back as "
                       f"{name} has them:",
                       [[header(comp_id)] for comp_id in missing]))
    for words, entries in groups:
        report.line()
        report.flow("    ", words)
        _offer_lines(report, entries)


def _capped(words: list[str]) -> list[str]:
    """*words* to ``REVIEW_LIMIT``, and how many more, for :func:`in_words`."""
    if len(words) <= REVIEW_LIMIT:
        return list(words)
    return [*words[:REVIEW_LIMIT], f"{len(words) - REVIEW_LIMIT} more"]


def _offer_lines(report: _Report, entries: list[list[str]]) -> None:
    """The lines of a group of offers, each under its table's header once, to
    ``REVIEW_LIMIT`` entries and then a count of the rest, six columns in."""
    shown: str | None = None
    for entry in entries[:REVIEW_LIMIT]:
        table, *lines = entry
        if table != shown:
            report.line(f"      {table}")
            shown = table
        for line in lines:
            for part in config_mod.toml_lines(line, width=WIDTH, indent=" " * 6):
                report.line(part)
    if len(entries) > REVIEW_LIMIT:
        report.line(f"      # and {len(entries) - REVIEW_LIMIT} more")


class _Item(NamedTuple):
    """One value the review lists: what it is, why, and the TOML that declares it.

    ``report_only`` is what to do instead, for a value that is not the club's to
    declare as it stands, and "" for every value that is. The draw read from the
    cards is the one: it is the club's to check its association's draw against.

    ``to_type`` is what the club types after the ``=`` of a value only it can
    know, which is printed with nothing there and does not load until it is
    typed, and "" for every value printed whole. The season's length is the one,
    and a season's name where no card or date gave one: offered as the rounds
    played, ``rounds_total = 7`` declared a season of ten over at round 7, and
    round 8's cards stopped the next run on F3.
    """
    subject: str
    why: str
    lines: list[str]
    report_only: str = ""
    to_type: str = ""

    @property
    def table(self) -> str:
        """The header its lines open, which every item's first line is."""
        return self.lines[0]


def _config_on_disk(path: Path) -> str:
    """The config file as it stands, or "" where there is none this run can read."""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return ""


def _held_status(item: _Item, held: str, *, opens: bool = True,
                 shared: bool = False, at: tuple[int, ...] = ()) -> str:
    """What the review says of one item, given where ``config.holding`` found it.

    *at* is, where *held* is ``"split"``, the line of each ``# [x]`` holding a part.
    *opens* is ``config.opens`` of the item's table, False where the file has it
    already, and *shared* whether another value listed opens the same one. Either
    way the lines as printed start with a header the file can hold once: added as
    they stand, the pairs cards' ``[player_names]`` stopped the next run at exit 2,
    "Cannot declare ('player_names',) twice".
    """
    if held == "live":
        return "live already, so the next run reads it as declared -- check it now"
    if item.report_only:
        where = ("commented out, and best left so"
                 if held in ("commented", "split")
                 else "not in it, and in this report only")
        return f"{where}: {item.report_only}"
    if held == "commented" and item.to_type:
        # Not "delete the # to adopt it": the line deleted of its # alone stops
        # the next run at exit 2, "not valid TOML".
        return (f"commented out, with nothing after the =: type there "
                f"{item.to_type}, then delete the #")
    if held == "commented":
        return "commented out; delete the # to adopt it"
    if held == "split":
        # Measured with a second competition's two names under the second header:
        # gathered under the first, as the six were told, the next run was exit 2.
        # Under a third, which holds none of the six and is not named, "one of
        # those headers" could not be done at all.
        where = (f"the same # {item.table} as any other value here you adopt, or "
                 f"under one of those headers if there is none" if shared
                 else "one of those headers")
        return (f"commented out, but split between the # {item.table} at lines "
                f"{in_words(at)}, and a file declares a table once: move its "
                f"lines under {where}, and delete the # to adopt it")
    typed = f", with {item.to_type} typed after the =," if item.to_type else ""
    if not opens:
        return (f"not in it; add the lines below to the {item.table} it has "
                f"already{typed or ','} to adopt it")
    if shared:
        return (f"not in it; add the lines below{typed} to adopt it, under the "
                f"same {item.table} as any other value here you adopt")
    return f"not in it; add the lines below{typed} to adopt it"


def _review_list(seasons: list[dict], found, cfg: config_mod.Config, *,
                 scope: Scope = EVERY_SEASON, hidden: bool = False
                 ) -> list[_Item]:
    """The review's items under *scope*: what the section lists, or with ``hidden``
    what "not listed" counts for the seasons ``--only`` leaves out, by the one rule.

    Handed every season and not only the listed ones, because the ``[rules]``
    question is one for the whole run. Built from each half, it was asked in both:
    a run of 13 values listed 7 and counted 7, each about half its matches.
    """
    if cfg.demo:
        # The demo's own config, which the demo writes again on every run: every
        # item would be lines to add to it, lost the next time the demo runs.
        return []
    items: list[_Item] = []
    by_id = {c.id: c for c in found.club.competitions}
    shape = cfg.rules_format(found.club.competitions)
    for season in _listed(seasons, scope, hidden=hidden):
        comp = by_id.get(season["meta"]["competition"])
        if comp is not None:
            items.extend(_review_items(comp, season, cfg,
                                       shared_shape=shape is not None))
    # Last, and not in the loop: the one item that can span competitions.
    items.extend(_threshold_items(seasons, found, cfg, scope=scope, hidden=hidden))
    return items


def _what_force_drops(cfg: config_mod.Config) -> str:
    """What a rewrite loses, which is the draw and the comments: said by the
    header of the proposal ``--discover`` writes beside a config, of adopting it
    whole, since 4.431, and by the refusal it replaced before.

    It said "Everything you have declared in it would be replaced by what the cards
    say", which named nothing a club could check the rewritten file against. What a
    rewrite loses is the ``[[...rounds]]`` blocks, because no card records a fixture
    that has not been played, and any comment the club typed, because the file is
    written fresh. So the sections whose draw would go are named, and a file with no
    draw in it is not told it has one to lose.

    A round's ``finals`` is declared in those blocks and nowhere else, so it goes
    with them, and the run after tells a final by its label alone. The rounds this
    moves are named, each with the side of the ladder it moves to; a round whose
    label says what was typed on it loses nothing, and is not named. The length the
    draw gave is counted the same way (``length_by_labels``), and for each section
    whose rounds move the length written is named beside them, moved or not; a
    ``rounds_total`` typed is not moved by them, and is not named. A length its
    cards have played past, the one the draw gave or the one the labels give, is
    written commented out, and this is said from the file alone, without the
    cards, so each length named is said with that. Once for the list, it read as
    said of a section that has no length written, and not of the one before it
    that had.

    A sheet table is written back as it was read, and one the loader could compare
    no line of was read as nothing, so it would come back as the blank template.
    Such a table is named, and the club is sent to the run that says what is wrong
    with it; a table nobody has filled in is not. So is each line, in a table it
    does compare, that had a figure refused: the rest of the table comes back, and
    the refused figure does not.
    """
    drawn = [f"[[competitions.{config_mod.toml_key(comp_id)}.rounds]]"
             for comp_id, section in cfg.competitions.items() if section.rounds]
    moved: dict[bool, list[str]] = {True: [], False: []}
    shifted = set()
    for comp_id, section in cfg.competitions.items():
        for rnd in section.rounds:
            if rnd.is_finals != is_finals_label(rnd.number):
                moved[rnd.is_finals].append(f"round {echoed(rnd.number)} of {comp_id}")
                shifted.add(comp_id)
    turned = []
    for typed, said in (
            (True, ("is scored in the ladder as a regular round, not as the final",
                    "are scored in the ladder as regular rounds, not as the finals")),
            (False, ("is left out of the ladder as a final, not scored as the "
                     "regular round",
                     "are left out of the ladder as finals, not scored as the "
                     "regular rounds"))):
        if moved[typed]:
            turned.append(f"{in_words(moved[typed])} "
                          f"{said[len(moved[typed]) > 1]} you declared")
    lengths = []
    for comp_id, section in cfg.competitions.items():
        was, now = section.season_length, section.length_by_labels
        if comp_id in shifted and section.rounds_total is None:
            gave = ("no length" if was is None
                    else f"{was} round{'' if was == 1 else 's'}")
            if now is None:
                lengths.append(f"no rounds_total is written for {comp_id} where "
                               f"your draw gave it {gave}")
                continue
            # Said from the file alone, so not which. Played past the length the
            # draw gave, F3 stops this run; past the labels', it would stop the run
            # after; either way it is written commented out.
            where = ("as your draw gave it" if was == now
                     else f"where your draw gave it {gave}")
            past = "it" if was in (None, now) else "either length"
            lengths.append(f"rounds_total = {now} is written for {comp_id} {where}, "
                           f"unless its cards have played past {past}")
    sheets = [f"[competitions.{config_mod.toml_key(comp_id)}.expected]"
              for comp_id, section in cfg.competitions.items()
              if section.expected_unusable]
    tables = (f"the {in_words(sheets)} "
              f"{'tables' if len(sheets) > 1 else 'table'}" if sheets else "")
    refused = [f"the {in_words(repr(key) for key in section.expected_refused)} "
               f"line{'s' if len(section.expected_refused) > 1 else ''} of the "
               f"[competitions.{config_mod.toml_key(comp_id)}.expected] table"
               for comp_id, section in cfg.competitions.items()
               if section.expected_refused]
    unread = f"no build could compare in {in_words(refused)}" if refused else ""
    lost = f"what {unread}" if unread else ""
    if drawn:
        blocks = in_words(drawn)
        kept = (f"Everything you have declared in it is written back except "
                f"{in_words(part for part in ('the draw', tables, lost) if part)}: "
                f"no card records a fixture that has not been played, so the "
                f"{blocks} blocks go, and with them the dates, fixtures, byes and "
                f"notes they hold.")
        if turned:
            kept += (f" The run after it tells a final by its round's label alone, "
                     f"so {', and '.join(turned)}.")
        if lengths:             # clauses, some with a comma, so joined as turned is
            kept += (f" The season's length is counted the same way, so "
                     f"{', and '.join(lengths)}.")
        named = tables          # the colon above was the draw's, so said again
    elif sheets:
        kept = (f"Everything you have declared in it is written back except "
                f"{in_words(part for part in (tables, lost) if part)}.")
        named = tables if lost else "them" if len(sheets) > 1 else "it"
    elif lost:
        kept = (f"Everything you have declared in it is written back except {lost}: "
                f"put it right first, as a run without --discover says how.")
    else:
        kept = "Everything you have declared in it is written back."
    if sheets:
        kept += (f" No build could compare a line of {named} as typed, so what is "
                 f"typed there is not kept: put it right first, as a run without "
                 f"--discover says how.")
    if lost and sheets:         # named again, as the tables are
        kept += f" What {unread} is not kept either: put it right first too."
    elif lost and drawn:
        kept += (f" What {unread} is not kept: put it right first, as a run without "
                 f"--discover says how.")
    return f"{kept} The file is written fresh, so a comment you added to it goes."


def _rewrite_offer(report: _Report, *, cfg: config_mod.Config,
                   args: argparse.Namespace, items: list[_Item],
                   rewrite: str) -> None:
    """The one command that fills a config file in, said out loud where it is safe.

    A club that ran this tool before exporting anything got a config file written
    on an empty ``data/``: correct, and describing no competition, because there
    was none to describe. Every plain run afterwards reads that file, builds the
    dashboard from the cards and leaves the file exactly as it is -- which is the
    right rule, and which made the stub permanent. The list above was the club's
    only way out of it, by hand, and ``--discover`` was named in no report.

    Offered only while the file describes no competition: that is the one state in
    which a rewrite can lose nothing a card cannot supply. A config with a
    ``[competitions.…]`` section in it can hold a declared draw, and ``--force``
    drops the draw -- which :func:`_what_force_drops` says, in the header of the
    proposal plain ``--discover`` writes beside the file (4.431).

    How many of the values below land in it behind a ``#`` is asked of *rewrite*,
    the file that command would write: said of every value, it was false of the
    draw, which no file is written with, and of players' spellings, which a
    rewrite leaves to the club. One lands at least, each competition's team list,
    which a file that describes no competition cannot have declared. A value
    written with nothing after its ``=`` is said apart, since deleting its ``#``
    is not all adopting it takes.
    """
    if not cfg.exists or cfg.competitions:
        return
    adoptable = [item for item in items if not item.report_only]
    landed = [item for item in adoptable
              if config_mod.held_in(item.lines, rewrite) == "commented"]
    if len(landed) == len(adoptable):
        lands = ("each value below that can be adopted lands in it commented out, "
                 "so adopting one is deleting a # rather than typing the line "
                 "yourself")
    else:
        lands = (f"{len(landed)} of the {len(adoptable)} values below that can be "
                 f"adopted land in it commented out, so adopting one of those is "
                 f"deleting a # rather than typing the line yourself")
    # A value only the club knows lands too, with nothing after its =, and the #
    # deleted alone leaves a file that does not load.
    if any(item.to_type for item in landed):
        lands += ("; any of those with nothing after the = needs its value typed "
                  "there too")
    report.line()
    report.flow("  ", f"Or have them written for you. {args.config} describes no "
                      f"competition yet, so a rewrite has nothing of yours to "
                      f"lose: what you have set under [club], [build], [rules] "
                      f"and the name tables is kept as it stands, and {lands}.")
    report.line()
    # Its own lines, unflowed: the flow would break a command at the space in front
    # of --force, and half a command is worse to be handed than none.
    for line in _command_hint(args, "--discover", "--force"):
        report.line(line)


def _threshold_items(seasons: list[dict], found, cfg: config_mod.Config, *,
                     scope: Scope = EVERY_SEASON, hidden: bool = False
                     ) -> list[_Item]:
    """How many sets a result needs, asked where a played match decided nothing.

    ``min_sets_for_result`` is the one setting no card states that can take a
    played match out of the ladder, and a club that never set it was told only
    that each such match had no result. So it is asked, once, while nothing has
    declared it and a match is short of it: under ``[rules]`` when one number is
    every competition's that would inherit it, otherwise in each competition's own
    section with that competition's number.

    The number offered is the one in force, so adopting the line changes nothing;
    what a club is asked is whether it is its association's.
    """
    by_id = {c.id: c for c in found.club.competitions}
    asked: list[tuple[Competition, dict, list]] = []
    for season in seasons:
        comp = by_id.get(season["meta"]["competition"])
        if comp is None or cfg.min_sets_for(cfg.competition(comp.id)) is not None:
            continue
        short = [m for m in found.matches.get(comp.id, []) if m.short_of_result]
        if short:
            asked.append((comp, season, short))
    if not asked:
        return []
    number = cfg.rules_minimum(found.club.competitions)
    if number is not None:
        groups = [(asked, number, "[rules]", False)]
    else:
        groups = [([one], one[0].fmt.min_sets_for_result,
                   f"[competitions.{config_mod.toml_key(one[0].id)}]", True)
                  for one in asked]
    out: list[_Item] = []
    for group, need, table, alone in groups:
        # Placed by the seasons it is about: listed if any of them is, and counted
        # under "not listed" only if none is, so that it is never in both.
        if bool(_listed([season for _, season, _ in group], scope)) == hidden:
            continue
        # The listed competitions first, so that past REVIEW_LIMIT the ones this
        # run prints are still named; and the why says how many matches no-result,
        # which prints only the listed seasons, leaves out.
        ids = [comp.id for comp, _, _ in
               sorted(group, key=lambda one: not _listed([one[1]], scope))]
        away = sum(len(matches) for _, season, matches in group
                   if not _listed([season], scope))
        short = [m for _, _, matches in group for m in matches]
        unmarked = sum(1 for m in short
                       if any(r.unfinished_unmarked for r in m.sets))
        # Every one the list holds for the seasons this item is about, which is
        # what "(see verb-no-result)" points at.
        printed = sum(1 for _, season, _ in group
                      for e in season["validation"]["errors"]
                      if e["rule"] == "verb-no-result")
        out.append(_Item(
            f"the sets needed for a result in "
            f"{ids[0] if len(ids) == 1 else f'{len(ids)} competitions'}",
            _threshold_why(len(short), unmarked, printed, need, ids, alone=alone,
                           away=away),
            [table, f"min_sets_for_result = {need}"]))
    return out


def _threshold_why(short: int, unmarked: int, printed: int, need: int,
                   ids: list[str], *, alone: bool, away: int = 0) -> str:
    """The reminder's why: what decided nothing, what to check first, what to write.

    The set length comes before the number. A set nobody ticked as stopped may be
    a set played to 4 read as one to 6, and lowering the number over it gives
    those matches results off the wrong sets. *away* is how many of the matches
    are in seasons ``--only`` does not list, whose no-result warnings are not
    printed.
    """
    where = ""
    if len(ids) > 1:
        more = len(ids) - REVIEW_LIMIT
        where = " in " + in_words([*ids[:REVIEW_LIMIT],
                                   *([f"{more} more"] if more > 0 else [])])
    see = "see no-result"
    if away:
        see += f", which leaves out the {away} in seasons not listed"
    clauses = [f"{short} played match(es){where} decided nothing because fewer "
               f"than {need} of their sets finished ({see})"]
    if unmarked:
        clauses.append(f"{unmarked} of them left a set unfinished with no box "
                       "ticked, so check [rules] sets first, since it decides "
                       "which sets count as finished")
    if printed:
        clauses.append(f"{printed} card(s) print a result anyway (see "
                       "verb-no-result)")
    clauses.append(f"{need} is a default, three quarters of the sets rounded up, "
                   "as no card states it, so if your association counts a result "
                   "on fewer, write its number below")
    if alone:
        clauses.append("set for this competition alone, as the competitions in "
                       "this run need different numbers")
    return "; ".join(clauses)


def _review_items(comp: Competition, season: dict, cfg: config_mod.Config, *,
                  shared_shape: bool = True) -> list[_Item]:
    key = config_mod.toml_key(comp.id)
    header = f"[competitions.{key}]"
    meta = season["meta"]
    out: list[_Item] = []

    if not comp.teams_declared:
        names = [comp.teams[slug].name for slug in sorted(comp.teams)]
        out.append(_Item(
            f"the team list for {comp.id}",
            "a closed list is what lets a bye be credited to the right team "
            "and a misspelt team be flagged instead of quietly added as a new "
            "one",
            config_mod.team_list_lines(comp.id, names)))

    if comp.rounds_total is None:
        # No number, as the file writes none: no card prints the length, and the
        # rounds played, adopted, declared the season over where it had reached.
        out.append(_Item(
            f"the season length for {comp.id}",
            f"without it the dashboard says "
            f"\"{rounds_played_words(meta['rounds_played'])}\" and shows no "
            f"percentage, rather than implying the season is over",
            [header, "rounds_total ="],
            to_type="the number of rounds your association's draw gives the "
                    "season"))

    if comp.provenance_of("season") is not Provenance.DECLARED:
        # The one offer the file makes too, asked of export.season_offer. Where
        # there is none, a bare line to type: ``season = ""`` added as printed was
        # "live already, so the next run reads it as declared", and declared
        # nothing.
        offer = export.season_offer(
            comp, [match.get("date") or "" for match in season["matches"]])
        shown = export.season_label_from(comp)
        made = ("the year on the season's cards"
                if shown == export.SEASON_FROM_DATE
                else "the season's folder name made readable")
        out.append(_Item(
            f"the season name for {comp.id}",
            f"no card prints it, so the dashboard shows {made} until a human "
            f"says what it is" if shown else
            "scorecards do not reliably print it, so the dashboard leaves it "
            "out until a human says what it is",
            [header, f"season = {config_mod.toml_value(offer)}" if offer
             else "season ="],
            to_type="" if offer else "the season's name in double quotes"))

    if comp.provenance_of("draw") is not Provenance.DECLARED:
        # Not a draw to adopt: measured on the demo's first three rounds pasted in
        # as this prints them, round 4's cards stopped the next run on F3, and a
        # match of round 2 filed late stopped it on draw-mismatch.
        # The next round's cards stop it only while rounds_total is undeclared:
        # with it declared, round 4's were scored and noted as a round the draw
        # does not list.
        out.append(_Item(
            f"the draw for {comp.id}",
            "a draw read from the cards is a history and never a schedule, so "
            "the dashboard cannot show a fixture that has not been played; "
            "declared as it stands, a postponed match's card would stop the run"
            + (", and so would the next round's while no rounds_total is declared"
               if comp.rounds_total is None else ""),
            _draw_lines(comp, key, season),
            report_only="type in your association's draw, and check it against "
                        "these rounds"))

    if not comp.format_declared:
        out.append(_Item(
            f"the match format for {comp.id}",
            f"read from the row bands on the cards as "
            f"{season['meta']['rules']['format']}; pinning it makes a card with "
            f"a row missed an error instead of a different format",
            # [rules] only where one shape is every competition's there; the
            # file offers a competition's own shape in its own section otherwise.
            ["[rules]" if shared_shape else header, config_mod.shape_line(comp.fmt)]))

    guessed_teams = sorted(slug for slug, team in comp.teams.items()
                           if team.name_source is Provenance.DERIVED)
    if guessed_teams:
        lines = ["[team_names]"]
        for slug in guessed_teams[:REVIEW_LIMIT]:
            lines.append(f"{config_mod.toml_key(slug)} = "
                         f"{config_mod.toml_value(comp.teams[slug].name)}")
        if len(guessed_teams) > REVIEW_LIMIT:
            lines.append(f"# and {len(guessed_teams) - REVIEW_LIMIT} more")
        out.append(_Item(
            f"{len(guessed_teams)} team name(s) un-shouted from the cards",
            "cards print team names in capitals, so these are this program's "
            "best guess at how the club writes them",
            lines))

    players = comp.player_index()
    guessed_players = sorted(slug for slug, player in players.items()
                             if player.name_source is Provenance.DERIVED)
    if guessed_players:
        lines = ["[player_names]"]
        for slug in guessed_players[:REVIEW_LIMIT]:
            lines.append(f"{config_mod.toml_key(slug)} = "
                         f"{config_mod.toml_value(players[slug].name)}")
        if len(guessed_players) > REVIEW_LIMIT:
            lines.append(f"# and {len(guessed_players) - REVIEW_LIMIT} more")
        out.append(_Item(
            f"{len(guessed_players)} player name(s) with no printed spelling",
            "these were rebuilt from a slug because no card printed the name in "
            "mixed case; check the spelling and the capitals",
            lines))
    return out


def _draw_lines(comp: Competition, key: str, season: dict) -> list[str]:
    """The rounds this competition actually has, as the TOML to declare them.

    Every fixture printed here was played and read off a card. Nothing is
    extrapolated: F2 forbids inventing the rest of the season, so a club that wants
    the unplayed rounds shown types them in, and the moment it does the draw stops
    being marked as derived.

    ``bye`` is only emitted when the team list is closed. With an open list a team
    with no card and a team resting are the same observation, and guessing between
    them is F1. The rests are the ones *season* draws, so the block offers back
    what the Matches tab shows and nothing it left out.
    """
    rounds = [comp.rounds[number] for number in comp.round_order]
    drawn = {r["number"]: r["byes"] for r in season["rounds"]}
    if not rounds:
        return [f"[[competitions.{key}.rounds]]",
                "# no round could be read from the cards"]
    lines: list[str] = []
    for rnd in rounds[:REVIEW_LIMIT]:
        pairs = [[comp.team_name(home), comp.team_name(away)]
                 for home, away in rnd.fixtures]
        lines.append(f"[[competitions.{key}.rounds]]")
        lines.append(f"number = {config_mod.toml_value(rnd.number)}")
        if rnd.date:
            lines.append(f"date = {config_mod.toml_value(rnd.date)}")
        lines.append(f"fixtures = {config_mod.toml_value(pairs)}")
        if rnd.is_finals:
            # Kept out of the ladder either way (F11); declaring it is what stops
            # a finals round being counted the moment the draw is pinned. Spelled
            # `finals`, which is the key the reader accepts: this block is meant to
            # be pasted into config.toml, and `is_finals` -- the field name on the
            # Round, and what this printed for a while -- is warned about and
            # ignored there, so the declared draw lost its finals flag.
            lines.append("finals = true")
        # The page's rests, which are the ones that stand (stats.rests_standing).
        # Offering back a rest the round's own cards contradict hands the club a
        # draw that says two things, to paste in as fact.
        resting = [comp.team_name(slug) for slug in drawn.get(rnd.number, [])]
        if comp.teams_declared and resting:
            lines.append(f"bye = {config_mod.toml_value(resting)}"
                         if len(resting) > 1 else
                         f"bye = {config_mod.toml_value(resting[0])}")
        lines.append("")
    if lines and lines[-1] == "":
        lines.pop()
    if len(rounds) > REVIEW_LIMIT:
        lines.append(f"# and {len(rounds) - REVIEW_LIMIT} more round(s) read from "
                     f"the cards")
    return lines


def _problems_section(report: _Report, seasons: list[dict],
                      scope: Scope = EVERY_SEASON) -> None:
    """Errors then warnings, grouped by the rule each one belongs to.

    Grouped by rule rather than by competition because the rules are the thing a
    reader can act on: five F8 warnings in five divisions are one misspelt team
    name, and five different rules in one division are five different problems.

    Errors come from every season whatever ``--only`` says, because they fail the
    run and the run writes every season; warnings only from the seasons listed.
    """
    for severity, plural in (("errors", "error"), ("warnings", "warning")):
        grouped: dict[str, list[tuple[str, dict]]] = {}
        for comp_id, issue in _findings(
                seasons if severity == "errors" else _listed(seasons, scope),
                severity):
            grouped.setdefault(issue["rule"], []).append((comp_id, issue))
        if not grouped:
            continue
        total = sum(len(v) for v in grouped.values())
        always = severity == "errors"
        report.head(f"{total} {plural}{'' if total == 1 else 's'}",
                    always=always)
        for rule in sorted(grouped, key=_rule_key):
            report.line(f"  {rule}", always=always)
            for comp_id, issue in grouped[rule]:
                # Hanging two, not aligned under the message: the label here is a
                # competition id, so aligning would leave the club with the longest
                # division name reading its warnings down a narrow column -- and
                # would indent them deeper than the subject line below, which
                # belongs to the warning rather than to the wrap.
                report.flow(f"      {comp_id}: " if comp_id else "      ",
                            issue["message"], under=" " * 8, always=always)
                # Only where the message has not already begun with it.
                if issue.get("subject") and not export.subject_said(issue):
                    report.flow("          subject: ", str(issue["subject"]),
                                under=" " * 12, always=always)
                if issue.get("detail"):
                    report.flow("          ", str(issue["detail"]),
                                under=" " * 12, always=always)
        if always:
            report.line("", always=True)
            report.flow("  ", "Each F-number is a stable id for one rule about "
                        "what may be inferred from a scorecard. Quote it if you "
                        "report a problem.", always=True)


def _findings(seasons: list[dict], severity: str) -> list[tuple[str, dict]]:
    """Every finding of one severity in the run, each paired with its competition.

    A refused card (F4) is the exception. It might belong to any division, so every
    document names it and every page lists it. In the run it is still one card, so
    it comes back once, in the order the first document gives it, with ``""`` in
    place of a competition. The problems section prints it under no competition's
    name, and the last line leaves it out of the validation errors, because the
    `card(s) found and not scored` clause has already counted it. Which findings
    are a competition's own is ``export.own_findings``, the page's rule as well.
    """
    out: list[tuple[str, dict]] = []
    for season in seasons:
        findings = season["validation"][severity]
        own = {id(issue) for issue in export.own_findings(findings)}
        for issue in findings:
            if id(issue) in own:
                out.append((season["meta"]["competition"], issue))
            elif ("", issue) not in out:
                out.append(("", issue))
    return out


def _rule_key(rule: str) -> tuple[int, int, str]:
    """F1..F11 in numeric order, then named consistency checks alphabetically."""
    if rule[:1] == "F" and rule[1:].isdigit():
        return (0, int(rule[1:]), "")
    return (1, 0, rule)


def _listed(seasons: list[dict], scope: Scope, *, hidden: bool = False) -> list[dict]:
    """The documents the report lists under *scope*, or with ``hidden`` the rest.

    The one filter for everything ``--only`` shortens -- the competition blocks,
    the review, the warnings -- so that what is left out and what "not listed"
    counts are the two halves of one list.
    """
    return [season for season in seasons
            if scope.hides(season["meta"].get("season_key", "")) == hidden]


def _listed_notes(found, scope: Scope) -> tuple[list[str], list[str]]:
    """The discovery notes the report prints, and the season of each one it does not.

    Every note when there is no tag for each one -- a :class:`Discovery` built by
    hand -- because a note that cannot be placed in a season is no evidence that it
    belongs to another.
    """
    tags = found.note_seasons
    if len(tags) != len(found.notes):
        return list(found.notes), []
    return ([note for note, tag in zip(found.notes, tags) if not scope.hides(tag)],
            [tag for tag in tags if scope.hides(tag)])


def _only_sentence(args: argparse.Namespace, *, found, seasons: list[dict],
                   scope: Scope, known: dict[str, tuple[str, bool]],
                   written) -> str:
    """The cards section's ``only`` field, or ``""`` without ``--only``.

    Both figures in it are the ``scored`` field's, restricted: the competitions and
    the cards of the seasons listed, out of the whole run's.
    """
    if not scope.narrowed:
        return ""
    listed = [season["meta"]["competition"] for season in _listed(seasons, scope)]
    done = "read, checked and written" if written is not None else "read and checked"
    text = (f"{scope.words()}: {len(listed)} of {len(found.club.competitions)} "
            f"competition(s), {_filed(found, listed)} of {_filed(found)} card(s) "
            f"scored. Every season was {done}")
    if _listed(seasons, scope, hidden=True) or _listed_notes(found, scope)[1]:
        text += (f"; only {'this one is' if len(scope.only) == 1 else 'these are'} "
                 f'listed below, and the others are counted under "not listed".')
    else:
        text += "."
    for key in scope.only:
        if not known.get(key, ("", True))[1]:
            text += (f" {key}: no card under {args.data} is filed in it; "
                     f"{args.config.name} names it.")
    return text


def _not_listed_section(report: _Report, *, args: argparse.Namespace,
                        seasons: list[dict], found, cfg: config_mod.Config,
                        scope: Scope, written) -> None:
    """What ``--only`` left out, counted, so that nothing is simply gone.

    Counts rather than lines: the other seasons' review values, warnings and notes.
    What fails the run -- a refused card, an error -- and what no later run would
    say -- a change, a dashboard left behind -- are never left out, and the closing
    sentence says so. Then, from the cards' dates, any season left out that has
    been played later than the one listed: a command the club types or scripts
    itself can keep its --only into the next season.
    """
    hidden = _listed(seasons, scope, hidden=True)
    hidden_notes = _listed_notes(found, scope)[1]
    if not scope.narrowed or not (hidden or hidden_notes):
        return
    keys = sorted({season["meta"].get("season_key", "") for season in hidden}
                  | set(hidden_notes), key=site.season_order_key, reverse=True)
    report.head(f"not listed: {len(hidden)} competition(s) in {len(keys)} other "
                f"season(s)")
    report.sentence("seasons", ", ".join(keys))
    review = len(_review_list(seasons, found, cfg, scope=scope, hidden=True))
    if review:
        report.field("review", f"{review} value(s) worked out, not declared")
    warnings = sum(len(season["validation"]["warnings"]) for season in hidden)
    if warnings:
        report.field("warnings", str(warnings))
    if hidden_notes:
        report.field("notes", str(len(hidden_notes)))
    later = _played_after_scope(seasons, scope)
    if later:
        (key, when), rest = later[0], [other for other, _ in later[1:]]
        others = (f" ({in_words(rest)} {'has' if len(rest) == 1 else 'have'} one too)"
                  if rest else "")
        report.flow("  ", f"{key} has a card dated {when.isoformat()}, later than "
                          f"every card in {scope.words()}{others}. If {key} is the "
                          f"season being played, run:")
        report.line()
        for line in _command_hint(args, f"--only {key}"):
            report.line(line)
        report.line()
    done = ("Their dashboards were written like every other" if written is not None
            else "They were read and checked like every other")
    report.flow("  ", f"{done}, and anything in them that fails the run is listed "
                      f"above. Run without --only to list these too.")


def _played_after_scope(seasons: list[dict],
                        scope: Scope) -> list[tuple[str, date]]:
    """The seasons left out whose latest card is later than every card listed.

    Latest first, and a tie in the front page's order. From the cards' dates and
    never from the seasons' names: ``2026-winter`` sits above ``2026-summer`` on
    every club's front page, including the club whose summer is the later season.
    A tie is not later, a date that does not read is skipped, and a listed season
    with no date at all is no evidence that anything is later.
    """
    latest: dict[str, date] = {}
    for season in seasons:
        key = season["meta"].get("season_key", "")
        for match in season.get("matches", ()):
            try:
                played = date.fromisoformat(match.get("date") or "")
            except (TypeError, ValueError):
                continue
            if key not in latest or played > latest[key]:
                latest[key] = played
    listed = [when for key, when in latest.items() if not scope.hides(key)]
    if not listed:
        return []
    later = [(key, when) for key, when in latest.items()
             if scope.hides(key) and when > max(listed)]
    later.sort(key=lambda item: site.season_order_key(item[0]), reverse=True)
    later.sort(key=lambda item: item[1], reverse=True)
    return later


def _changes_section(report: _Report, *, args: argparse.Namespace,
                     seasons: list[dict], previous: dict, reached: set[str],
                     scope: Scope = EVERY_SEASON) -> None:
    """What this run changes, against the documents the last run left.

    The report already says how many files were written, which answers "did that
    do anything" and not "did it do what I meant". This section is the second
    question: the fixtures that arrived, the figures that moved with the old ones
    beside them, the fixtures that have gone, and the teams that changed places.

    The ``gone`` lines are the reason it exists and the only ones that survive
    ``--quiet``. A card this program cannot read is refused by name and counted in
    the exit status; a card it was never given is silent, and the dashboard it
    writes is a plausible, complete-looking ladder one match short. So a fixture
    that has stopped existing is printed at every verbosity, with the file the last
    build read it from -- the only name left for a club to go looking for -- and
    with both reasons it happens, because the right answer to a card exported twice
    and removed is nothing at all, and the right answer to a folder tidied into the
    wrong place is to put it back.

    Capped per kind at the review section's limit, for the same reason: a club
    loading a season in one sitting wants the first lines, not eighty, and the
    dashboard holds all of them anyway. Earliest round first, so the two left out
    are the two nobody is waiting for.
    """
    found = [(season["meta"]["competition"], items)
             for season in seasons
             for items in (changes_mod.since(
                 previous.get(season["meta"]["competition"]), season),)
             if items]
    if not found:
        return

    # Headed in the tense of the write, which is not the flag's: a club reading
    # "since the last build" over a list of things that have not happened would go
    # looking for them on a dashboard that does not show them, and one reading
    # "a build would make" over a dashboard already rewritten would wait for a
    # build that never lists them. --check writes nothing, and neither does a run
    # refused for a name, which is why that run and its --check print one
    # section. A write that stopped part way prints two: what it finished, and
    # then what it did not, which is the list the next build prints as made.
    for wrote in (True, False):
        part = [(comp_id, items) for comp_id, items in found
                if (comp_id in reached) == wrote]
        if part:
            _changes_list(report, args=args, seasons=seasons, found=part,
                          wrote=wrote, scope=scope)


def _changes_list(report: _Report, *, args: argparse.Namespace,
                  seasons: list[dict], found: list, wrote: bool,
                  scope: Scope) -> None:
    """One heading of the changes section, over the competitions of one tense."""
    loud = any(change.kind == "gone" for _, items in found for change in items)
    report.head("since the last build" if wrote
                else "changes a build would make", always=loud)
    # Never narrowed by --only, and said so only when it shows: the next run's "last
    # build" is this one, so a change this report left out would be printed nowhere.
    season_of = {season["meta"]["competition"]: season["meta"].get("season_key", "")
                 for season in seasons}
    if any(scope.hides(season_of.get(comp_id, "")) for comp_id, _ in found):
        report.flow("  ", ("Every season is in this section whatever --only says: "
                           "a change is reported by the run that makes it and by "
                           "no later run, so a line left out here would never be "
                           "printed.") if wrote else
                          ("Every season is in this section whatever --only says, "
                           "because a build writes every season and this is the "
                           "list that build would print."))
    for comp_id, items in found:
        # Per competition, not per run: a club with four divisions has one folder
        # per division, and "a fixture has gone" is a question about one of them.
        gone = any(change.kind == "gone" for change in items)
        report.line(f"  {comp_id}", always=gone)
        for kind in changes_mod.KINDS:
            of_kind = [change for change in items if change.kind == kind]
            for change in of_kind[:REVIEW_LIMIT]:
                report.flow(f"      {kind:<9}", change.text, under=" " * 15,
                            always=kind == "gone")
            if len(of_kind) > REVIEW_LIMIT:
                report.flow(f"      {kind:<9}",
                            f"... and {len(of_kind) - REVIEW_LIMIT} more",
                            under=" " * 15, always=kind == "gone")
    if loud:
        report.line("", always=True)
        said, short = (("is not on the dashboard any more", "what this run wrote is")
                       if wrote else ("would not be on the dashboard a build writes",
                                      "that dashboard would be"))
        report.flow("  ", f"A fixture listed as gone {said}. That is right if you "
                          f"removed a card that had been exported twice; if you "
                          f"removed nothing, then a card that was in {args.data} "
                          f"is not there now, and {short} a match short.",
                    always=True)


def _left_behind_section(report: _Report, *, args: argparse.Namespace,
                         cfg: config_mod.Config, orphans, pruned: dict,
                         refusals: dict, withheld: bool = False,
                         players: str = "names", wrote: bool,
                         seasons=()) -> None:
    """The dashboards under ``--out`` that this run did not write, and a careers page.

    Loud -- ``always=True`` on every line -- because it is the one way this program
    can leave a wrong figure published. Every competition directory is a complete
    copy of the dashboard, which is what makes one zippable and emailable, and a
    copy nothing rebuilds keeps opening and keeps showing the ladder it was
    written with. The front page stops linking it, and that is the trap rather
    than the fix: it is then invisible to everyone except the people who
    bookmarked it.

    Each one gets the stale copy's own figures as the evidence, because this run
    has no cards for that competition and cannot describe it any other way, and a
    reason, because "this folder is stale" and "you are now in multi-season mode"
    call for different things to be done.
    """
    if not orphans:
        return
    pruning = bool(pruned or refusals)
    report.head("pruned" if pruning else "left behind", always=True)
    for orphan in orphans:
        report.line(f"  {orphan.path}", always=True)
        report.flow("      ", _orphan_evidence(orphan), always=True)
        report.flow("      ", _orphan_reason(orphan, args=args, cfg=cfg,
                                             players=players, wrote=wrote,
                                             seasons=seasons),
                    always=True)
        if withheld and orphan.players == "names":
            # The one reason a copy this run did not write breaks a promise this run
            # made: every other orphan is stale, and this one is also public.
            report.flow("      ", "it prints players' names, and this run withholds "
                                  "them", always=True)
        if orphan.path in pruned:
            report.flow("      ", f"removed {pruned[orphan.path]} file(s)",
                        always=True)
        elif orphan.path in refusals:
            # The path stripped off the front of the reason, because the line above
            # this one is the path. A `SiteError` carries it -- every error that
            # module raises names the file at fault, and a caller that is not this
            # report needs it -- so it is removed here rather than left out there,
            # and an `OSError` message, which begins with an errno instead, is
            # unaffected.
            reason = refusals[orphan.path].removeprefix(f"{orphan.path} ")
            report.flow("      kept: ", reason, under=" " * 6, always=True)
    report.line("", always=True)
    if not pruning:
        _left_behind_lead(report, args=args, orphans=orphans, wrote=wrote)
    else:
        _pruned_summary(report, args=args, orphans=orphans, pruned=pruned,
                        refusals=refusals)


def _left_behind_lead(report: _Report, *, args: argparse.Namespace,
                      orphans, wrote: bool) -> None:
    """What to do about the list above: the command, and what it will not do.

    A careers page is one file and not a dashboard, so it has a paragraph of its
    own -- ahead of the dashboards' when both are listed, and alone when it is --
    and the dashboards' paragraphs are the words they always were. A page is
    listed only by a run that withholds names, in either mode (4.430), so it is
    the names it shows that matter, and the seasons or competitions it adds up
    are not only the ones listed. Its paragraphs leave out why this run did not
    write the page, which the reason and the names line above them have just
    said: as first written they said it again, the same point three times over
    one page.
    """
    page = any(orphan.kind == "careers" for orphan in orphans)
    dashboards = any(orphan.kind != "careers" for orphan in orphans)
    if page and not dashboards:
        said = (f"{_unlinked(args, 'the careers page above', wrote=wrote)}, "
                f"but it opens by double-click and shows every name it was "
                f"built with, which is why this run names it. To delete it:")
        report.flow("  ", said, always=True)
        report.line("", always=True)
        for line in _command_hint(args, "--prune"):
            report.line(line, always=True)
        return
    if page:
        report.flow("  ", "The command below removes the careers page above with "
                          "the dashboards.", always=True)
        report.line("", always=True)
    # Written without a singular and a plural of itself. One stale dashboard is
    # the common case and six is what a mode switch produces, and a sentence
    # with six number-dependent words in it is a sentence that will one day
    # print "it are".
    report.flow("  ", f"Every directory above is a complete dashboard this tool "
                      f"wrote and this run did not. "
                      f"{_unlinked(args, 'them', wrote=wrote)}, so nobody will "
                      f"find one by accident -- but each opens by double-click "
                      f"like every other copy and shows the ladder it was "
                      f"built with, which is why this run names them. To "
                      f"delete all of them:", always=True)
    report.line("", always=True)
    for line in _command_hint(args, "--prune"):
        report.line(line, always=True)
    report.line("", always=True)
    # The refusal rule up front rather than only when it fires, because the
    # club deciding whether to type that command is the one who needs to know
    # that it will not take a page of theirs with it -- and, in the same breath,
    # that it does not ask which folders they meant.
    report.flow("  ", f"That refuses any directory holding a file this tool did "
                      f"not write. It does not ask which ones you meant, "
                      f"though, so move a dashboard you published as an "
                      f"archive out of {args.out} before pruning.", always=True)


def _unlinked(args: argparse.Namespace, what: str, *, wrote: bool) -> str:
    """That nothing under ``--out`` links *what*, in the tense of the run.

    Said by the two leads above of a folder or page this run did not write. A
    run that wrote rewrote the front page, and says what that page does. Under
    --check, the one other run that lists what it left, the front page is the
    last build's and still links everything that build wrote, so the sentence
    is what a build would leave. One function, so that no lead can be in a
    different tense from the other.
    """
    if wrote:
        return f"Nothing under {args.out} links {what} any more"
    return f"A build would leave nothing under {args.out} linking {what}"


def _pruned_summary(report: _Report, *, args: argparse.Namespace, orphans,
                    pruned: dict, refusals: dict) -> None:
    """What ``--prune`` removed and what it kept, the careers page said apart."""
    pages = {orphan.path for orphan in orphans if orphan.kind == "careers"}
    removed = {path: count for path, count in pruned.items() if path not in pages}
    page_removed = any(path in pages for path in pruned)
    if removed:
        also = " and the careers page" if page_removed else ""
        report.flow("  ", f"{sum(pruned.values())} file(s) removed from "
                          f"{len(removed)} dashboard(s){also}. Nothing this run "
                          f"wrote was touched.", always=True)
    elif page_removed:
        report.flow("  ", f"{sum(pruned.values())} file(s) removed: the careers "
                          f"page. Nothing this run wrote was touched.", always=True)
    kept = [path for path in refusals if path not in pages]
    if kept:
        # The rule --prune deletes by is the rule the build already overwrites by:
        # the five files this tool writes into a competition directory are its own,
        # and anything else in there was put there by a person.
        report.flow("  ", f"{len(kept)} dashboard(s) not removed: deleting one "
                          f"would have taken a file this tool did not write with "
                          f"it. Delete the directory yourself, or move that file "
                          f"out of it and prune again:", always=True)
        report.line("", always=True)
        for line in _command_hint(args, "--prune"):
            report.line(line, always=True)
        report.line("", always=True)
    if any(path in pages for path in refusals):
        report.flow("  ", "The careers page was not removed; the line under it "
                          "says why.", always=True)


def _orphan_evidence(orphan) -> str:
    """What the stale copy says about itself: its label, its size, its date.

    From its own ``season.json`` and nowhere else. The config may no longer
    mention the competition and ``data/`` no longer holds a card for it, which is
    the whole reason it is on this list, so "a directory called
    tuesday-division-c" is all this run could otherwise offer a club deciding
    whether to keep it.
    """
    if orphan.kind == "careers":
        # A page has no cards of its own; its date is the only fact it carries.
        return (f"{orphan.label} -- built {orphan.generated}" if orphan.generated
                else orphan.label)
    facts = [f"{orphan.cards} card(s)"]
    if orphan.generated:
        facts.append(f"built {orphan.generated}")
    body = ", ".join(facts)
    return f"{orphan.label} -- {body}" if orphan.label else body


def _orphan_reason(orphan, *, args: argparse.Namespace,
                   cfg: config_mod.Config, players: str = "names",
                   wrote: bool, seasons=()) -> str:
    """Why this run did not write it, in the order the answers are most use.

    The mode switch comes first, because it is the one cause that orphans a club's
    entire site in a single run and the one a club has no chance of guessing:
    nothing about ``site/`` looks different afterwards except its depth. It is not
    *also* reported as a duplicate, and cannot be -- a competition id carries the
    season key in multi mode and not in single mode, so the id a mode switch
    orphans is never one this run wrote.

    A careers page has a reason of its own, ahead of all of that: it sits at the
    root in either mode, so the sentence about depth would be untrue of it. Every
    build that prints names writes one, in either mode (4.430), so the one run
    that lists one is a run that withholds names, and the reason is that, worded
    for what a label is one of in this run's mode.

    A second copy's reason is the one in a tense, asked of the ``wrote`` that
    :func:`_unlinked` takes: a run that wrote says where it wrote the
    competition, and ``--check``, which wrote nothing, says where a build
    writes it. The rest are as true of either run.

    Last, a copy whose every card this run read, in *seasons*, and filed under
    another competition: a card filed under the night of a date printed wrong,
    since put right. It is not a copy whose cards are gone, and "no card under
    data belongs to it" sent a club looking for one, so it says where they were
    filed: :func:`_refiled`.
    """
    if orphan.kind == "careers":
        part = careers_mod.part(cfg.multi_season)
        return (f"only a build that prints players' names writes a careers "
                f"page: a withheld label belongs to one {part}, not to a "
                f"person")
    if orphan.mode and orphan.mode != cfg.seasons_mode:
        was = "multi-season" if orphan.mode == "multi" else "single-season"
        now = "multi-season" if cfg.multi_season else "single-season"
        return (f"written in {was} mode, and this run is in {now} mode, which "
                f"puts every competition at a different depth under {args.out}")
    if orphan.duplicate_of is not None:
        if wrote:
            return f"a second copy: this competition is written to " \
                   f"{orphan.duplicate_of} now"
        return f"a second copy: a build writes this competition to " \
               f"{orphan.duplicate_of}"
    found = _read_elsewhere(orphan, seasons)
    if found:
        return _refiled(orphan, found, args=args, cfg=cfg, wrote=wrote)
    return f"no card under {args.data} belongs to it any more"


def _read_elsewhere(orphan, seasons) -> list[tuple[dict, str]]:
    """This run's cards of a stale copy's fixtures, and where each was filed.

    A fixture as :func:`changes.fixture_of` keys it, the round and the two teams
    as the card printed them, home first, looked for in the copy's own season,
    because a draw repeats from one season to the next. And then only where one
    competition of that season fields both teams, and holds the cards of the
    fixture the copy holds, as many and printing the same ids
    (:func:`site.card_of`). Two nights can field teams of one name, and the
    other night's card of the same round and teams is not this copy's: matched
    on the fixture alone, a copy whose card was deleted was said to have been
    filed under the other night, and one whose card was put right could be said
    to be wherever the other night's card was. The ids answer the one case the
    nights cannot, a card deleted whose own night fields one of its teams no
    more, where the two nights' cards print ids of their own. And a copy
    holding a card this run did not read, or not holding one it did, holds a
    result no card this run read accounts for.

    Rejected: one card per fixture rather than one night per pair of teams,
    since a deleted card leaves the other night's the only one; the id alone,
    which some cards leave out and others share; either way round, one more
    thing two nights' cards would have to share; the file's path, which a
    re-export renames. A card re-exported under a new id is not found, which
    leaves the copy the reason a deleted season gets, true of it still.

    Every fixture or none: a copy one of whose cards was not read holds a result
    no card under data accounts for, and so does a copy whose matches cannot be
    read, so both keep the reason a deleted season gets.
    """
    fielding: dict[tuple[str, str], set[str]] = {}
    held: dict[tuple[str, ...], list[tuple[str, dict, str]]] = {}
    for season in seasons:
        key = str(season["meta"].get("season_key", ""))
        competition = season["meta"]["competition"]
        for team in season["teams"]:
            fielding.setdefault((key, str(team.get("slug", ""))),
                                set()).add(competition)
        for row in season["matches"]:
            *fixture, printed = site.card_of(row)
            held.setdefault((key, *fixture), []).append(
                (printed, row, competition))
    found = []
    for fixture in dict.fromkeys(match[:3] for match in orphan.matches):
        _, home, away = fixture
        nights = (fielding.get((orphan.season_key, home), set())
                  & fielding.get((orphan.season_key, away), set()))
        cards = held.get((orphan.season_key, *fixture), [])
        ids = sorted(m[3] for m in orphan.matches if m[:3] == fixture)
        if len(nights) != 1 or sorted(card[0] for card in cards) != ids:
            return []
        found.extend((row, competition) for _, row, competition in cards)
    return found


# The fixtures a left-behind copy's reason names before it counts the rest. The
# owner's stray competitions held one or two cards each, and a whole night filed
# wrong holds a season, which named in full is a paragraph to say one thing.
_FIXTURES_NAMED = 3


def _refiled(orphan, found: list[tuple[dict, str]], *, args: argparse.Namespace,
             cfg: config_mod.Config, wrote: bool) -> str:
    """Where this run filed the cards of a copy left behind, and what is left of it.

    One card is named with the file it was read from, so the club can open it;
    several are counted, with a few of their fixtures, and no file, which the
    dashboard they were filed under names on each one's page. Then that the copy
    is stale, and that --prune removes it where it would and this run is not one.

    And while config.toml declares the copy's competition with a day and a
    division, that the section is all that keeps it on the front page: the first
    run writes one live for every night it files a card under, and nothing else
    says this one now names a night no card is filed under. The name is the one
    the front page prints for it, the section's. The command is the lead's to
    print, below the list, so it is not said here. Rejected: hiding the entry,
    which would make a published page depend on what an earlier build left.
    """
    filed = in_words(dict.fromkeys(competition for _, competition in found))
    if len(found) == 1:
        row = found[0][0]
        said = (f"its 1 card's fixture, {changes_mod._subject(row)}, was read this "
                f"run from {row['source']} and filed under {filed}")
    else:
        named = dict.fromkeys(changes_mod._subject(row) for row, _ in found)
        listed = in_words_capped(named, _FIXTURES_NAMED)
        said = (f"its {len(found)} cards' fixtures, {listed}, were read this run "
                f"and filed under {filed}")
    said += ", so this copy is stale"
    # Not under --prune, whose line under this one says what it did with the copy.
    if orphan.removable and not args.prune:
        said += " and --prune removes it"
    section = cfg.competition(orphan.competition)
    if section is not None and section.can_claim_cards:
        front = "the front page" if wrote else "the front page a build writes"
        cards = "that card" if len(found) == 1 else "those cards"
        said += (f". [competitions.{config_mod.toml_key(orphan.competition)}] in "
                 f"{args.config} is all that keeps {site._declared_label(section)} "
                 f"on {front}, with no results: if it was written for {cards}, "
                 f"delete it")
    return said


def _paste_lines(words: list[tuple[str, ...]], *, cd: Path | None = None,
                 indent: str = "    ") -> list[str]:
    """A command to paste, as the lines the report prints it on: one when it fits
    the width, and otherwise continued with `` \\`` where one of *words* begins.

    One function for every command the build hands a club -- the ``cd`` hint,
    the ``--only`` and ``--prune`` hints, the ``--discover --force`` offer and
    the doctor's line -- so that a command too wide for the report has one
    answer. A word here is the reader's, made of the shell's: ``("python3",
    "build.py")``, or a flag with its value, ``("--out", "'our club'\\''s
    site'")``. Spliced into a sentence, a command was wrapped with the sentence
    at any space, a quoted path's included, and pasted from there a line break
    inside the quotes is part of the path and one outside them ends the command.

    A word is kept on one line while it fits on a line of its own, and is
    otherwise parted where its tokens meet, so that a flag ends one line and its
    value begins the next. Kept whole, an ``--out`` of 63 characters with a
    ``--config`` after it ran its line past the width, where the value alone
    fitted. A token is never parted. One too long for any line has a line to
    itself and runs past the width, as a path does in the prose, with nothing
    printed behind it, because a club can scroll a line it cannot retype. The
    folder of a ``cd`` is such a token when the project is deep, and its ``&&``
    then begins the next line.

    Continued, and not started at the margin instead, because a path too long
    for the line is too long at column 0 as well. A POSIX shell reads a
    backslash before the end of a line as no break at all, and what follows
    hangs four further in, so that it reads as the same command. On Windows the
    command is printed on one line whatever its width: neither cmd nor
    PowerShell reads the backslash as going on, and which of them a club pastes
    into is not decided here.

    ``cd`` is a folder the command goes to first, ``cd P &&`` being one word of
    three tokens.
    """
    words = [*([("cd", _quote(str(cd)), "&&")] if cd is not None else []), *words]
    if matchcentre.WINDOWS:
        return [indent + " ".join(" ".join(word) for word in words)]
    hang = indent + " " * 4
    pieces = []
    for number, word in enumerate(words, start=1):
        # Room left for the `` \`` a line ends with when a later word is not on it.
        room = WIDTH if number == len(words) else WIDTH - 2
        whole = " ".join(word)
        pieces.extend([whole] if len(hang) + len(whole) <= room else word)
    lines, line = [], indent + pieces[0]
    for number, piece in enumerate(pieces[1:], start=2):
        room = WIDTH if number == len(pieces) else WIDTH - 2
        if len(line) + 1 + len(piece) <= room:
            line += " " + piece
        else:
            lines.append(line + " \\")
            line = hang + piece
    return [*lines, line]


def _command_hint(args: argparse.Namespace, *options: str,
                  cd: Path | None = None) -> list[str]:
    """A command to paste, carrying the folders this run was actually given.

    A bare ``python3 build.py --prune`` quoted at a club that keeps its site
    somewhere else finds nothing to prune and says so, which reads as this report
    having been wrong. One function rather than one per command: the second
    caller carried the same three folders as the first, and the copy of this loop
    it would have been is the copy that gets updated once.

    And ``--players``, when this run was given it. A run that withholds names
    for this once, told to paste ``python3 build.py --prune``, pasted it: the
    command built with the config's names, found nothing to prune, and wrote
    every name back under the folder this run had just withheld them from. The
    flag is never written anywhere, so the command is the only place that can
    carry it.

    ``cd`` is the project the command runs in, for a line that begins by going
    there. A site or settings file the club typed is then carried whatever it
    says, resolved where it was typed, as the demo's ``next`` line resolves its
    folder, and one it did not type is left to the project's own. Carried as
    typed, ``--config club.toml --out mysite`` from the club's own folder pasted
    to a build that read and wrote ``club.toml`` and ``mysite`` inside the
    project, measured, with the club's title never read; and asked whether its
    text was the default's, a typed ``--out site`` or ``--config ./config.toml``
    was dropped, to the same effect. ``args.typed`` names the ones typed, which
    the parse's own default cannot. ``--data`` is carried as typed,
    because that line exists to read the project's own cards. Each path goes
    through ``pasteable_path``, the one rule for a path handed to a flag, which
    keeps the ``./`` of a folder whose name begins with a dash.

    Returned as the lines ``_paste_lines`` prints it on, so that no caller can
    splice it into a sentence; each option is one word to that function, a flag
    with its value after a space, ``"--only 2026-winter"``.
    """
    parts = [("python3", "build.py"),
             *(tuple(option.split(" ", 1)) for option in options)]
    for flag, value, default, where in (
            ("--data", args.data, DEFAULT_DATA, None),
            ("--out", args.out, DEFAULT_OUT, cd),
            ("--config", args.config, config_mod.DEFAULT_PATH, cd)):
        carried = (flag in args.typed if where is not None
                   else str(value) != default)
        if carried:
            parts.append((flag, pasteable_path(value, project=where)))
    if args.players is not None:
        parts.append(("--players", args.players))
    return _paste_lines(parts, cd=cd)


def _cards_elsewhere(args: argparse.Namespace, *, root: Path = ROOT) -> list[str]:
    """Lines naming the project's own card folder, for a run that is standing
    somewhere with none -- or nothing at all, which is the usual answer.

    A folder named on the command line is read next to wherever the club is
    standing, which is the right rule and the one this program keeps: two checkouts
    would otherwise fight over one ``site/``. From anywhere else, though, it made
    `python3 /opt/matchcentre/build.py` -- the cron line, the desktop shortcut --
    answer with `no such directory: data` and then advise copying in the PDFs a club
    had exported last week and could see next to ``build.py``. The project's own
    folder was named nowhere, and neither was any command that reads it.

    Two ways to say nothing. The folder beside ``build.py`` may *be* the folder
    already looked in, which is a club standing in its own project -- the ordinary
    case, every run until one is automated -- and there is nothing to point at
    because there is nowhere else. That comparison also answers an absolute
    ``--data``: joining a path that begins at the root onto another yields the same
    path back, so a folder named in full is always the folder already looked in, and
    it should be, since it names one directory and there is no second one it could
    have meant. And the folder may hold nothing, counted rather than read: a second
    empty folder is not a place to send anybody, and a folder that is not there at
    all counts as empty without needing to be asked about separately.

    ``root`` is a parameter for one test's sake, and it earns its keep: the case
    worth pinning is the club standing in its own project, and a test cannot arrange
    for *this* project's ``data/`` to hold cards without leaving them behind in it.

    Returned as lines and not printed, because the two runs that need it print
    differently -- one is a refusal on stderr, one is a section of the report -- and
    the sentence should not be able to differ between them.
    """
    data = args.data
    beside = root / data
    if beside.resolve() == data.resolve():
        return []
    count = scorecard.cards_waiting(beside)
    if not count:
        return []
    waiting = f"{count} scorecard PDF" if count == 1 else f"{count} scorecard PDFs"
    lines = fill(f"It looked in {data.resolve()}, because a folder named "
                 f"without a leading / is read next to wherever you are "
                 f"standing rather than next to build.py. The project you "
                 f"ran has one of its own at {beside}, holding {waiting}. "
                 f"To read those:",
                 keep=(str(data.resolve()), str(beside))).split("\n")
    # The command on lines of its own and never wrapped into the prose: the `cd` and
    # the run are one thing to paste, and a fold inside them hands a club half of it.
    return [*lines, "", *_command_hint(args, cd=root)]


# --- the last word ----------------------------------------------------------


def _finish(report: _Report, *, args: argparse.Namespace, cards, filed: int,
            seasons: list[dict], written: site.SiteWrite | None, unscored: int,
            errors: int, unwritable: OSError | None = None, unlisted: int = 0,
            config_unwritable: OSError | None = None,
            config_target: Path | None = None, proposal: _Proposal | None = None,
            prune_failed: int = 0, withholding=None, named_left: int = 0,
            before=(), careers_kept: bool = False,
            careers_named: bool = False,
            careers_file: str = site.CAREERS_PAGE) -> int:
    report.head("result", always=True)

    floor = 0
    # Things this run was asked to do and could not, which are not what the run was
    # *for*. Each one raises the floor under the status and adds a clause to the
    # last line: a list rather than one string, because the second of these arrived
    # months after the first and appended itself to a sentence built for one.
    unfinished: list[str] = []
    if config_unwritable is not None:
        # First in the section, above even `wrote`, for the reason `_not_written` is
        # where it is: this is the only thing here a club has to act on, and a line
        # offering a dashboard needs nobody to do anything. It also means the branches
        # below can end the run where they already did -- a folder with no readable card
        # in it returns three lines from here -- without any of them having to remember
        # to mention a file they know nothing about.
        target = config_target or args.config
        _config_not_written(report, args=args, exc=config_unwritable, path=target,
                            proposal=proposal is not None)
        # `--discover` was asked for exactly one thing and did not get it, so that run is
        # a refusal like any other. A plain run's write is a courtesy on top of a
        # dashboard that is correct without it, so it is exit 1: worth a script noticing,
        # and not the same as a build that produced nothing. A proposal is that one
        # thing too.
        floor = 2 if args.discover else 1
        unfinished.append(f"{target} not written")
    if proposal is not None and proposal.written:
        # Beside the config, which is as it was: the one file of this run outside
        # --out, so the line names it where a club reads what was written.
        report.field("proposed", str(proposal.path), always=True,
                     keep=(str(proposal.path),))
    if prune_failed:
        # Exit 1, like every other thing this program was asked to do and could
        # not. A club that prunes from a script has no other way to learn that a
        # directory it expected to be gone is still there -- the report says so, and
        # a script does not read the report.
        floor = max(floor, 1)
        unfinished.append(f"{prune_failed} dashboard(s) not removed")
    if named_left:
        # Exit 1 for the same reason: "no player's full name under --out" is the promise
        # a withheld run makes, a copy an earlier build left there breaks it, and a
        # script does not read the report. A copy --prune removed adds nothing.
        floor = max(floor, 1)
        unfinished.append(f"{named_left} dashboard(s) still print players' names")
    if careers_named:
        # The same promise, broken by the one file that is not a dashboard, and
        # said once: a page --prune could not remove is the same page still there,
        # which is why `_run` does not count it as kept. Named as the left-behind
        # entry is, by the file it is listed under: a split's pages with
        # careers.html deleted are listed under careers-a.html.
        floor = max(floor, 1)
        unfinished.append(f"{careers_file} still prints players' names")
    if careers_kept:
        # The same, for the one file that is not a dashboard.
        floor = max(floor, 1)
        unfinished.append(f"{careers_file} not removed")

    def ending(status: int, *clauses: str) -> int:
        """The last line and the status returned, so the two cannot disagree.

        Every exit in this function goes through here. The floor is applied to the
        status *and* said in words, because a club reads the bottom of the report and a
        CI log may show that line on its own. The clauses are a list in a sentence, so
        `in_words` joins them, as it does the refused heading above.

        Wrapped, alone among the lines this function writes, because it is the only one
        with a path in it that is not a field: `--config` can name a path of any length,
        and `  exit 1: all 6 card(s) scored, no errors and config.toml not written` is
        already 69 of the 78 columns with the shortest path a club can give it.
        """
        status = max(status, floor)
        report.flow(f"  exit {status}: ", in_words([*clauses, *unfinished]),
                    always=True)
        return status

    if not cards:
        # Every paragraph from here down is flowed rather than hand-wrapped: each one
        # has `--data` or `--out` inside it, and a club that keeps its seasons in a
        # folder named after the season prints a path longer than this whole report.
        report.flow("  ", f"No scorecards were read from {args.data}.", always=True)
        # A full folder and an empty one are two different problems, and only one
        # of them is solved by going back to Match Centre. Every card here was
        # found, named and refused in the section above; telling this club to
        # export a match and put it in the folder it is already in is the one
        # instruction certain not to help. Nor is a folder the cards are behind:
        # exporting them again puts them in a folder this run could not read
        # either. The pointer to the section above once, for both.
        said = ([f"{unscored} card(s) were found there and none of them could "
                 f"be read."] if unscored else []) + (
            [f"{unlisted} folder(s) there could not be opened, so no card in "
             f"them was read."] if unlisted else [])
        if said:
            report.flow("  ", " ".join([
                *said, "The section above names each one and why. Nothing was "
                       "built, because there was nothing readable to build from."]),
                always=True)
            # The count again, on the line a club reads first and a CI log may show
            # on its own.
            return ending(1, *_refused_clauses(unscored, unlisted), "nothing built")
        report.flow("  ", f"Export a match from matchcentre.tennis.com.au as a PDF, "
                          f"put it in {args.data}, and run this again. Any folder "
                          f"layout underneath works: the round, division and date "
                          f"are read off the cards.", always=True)
        # A `data/` that exists and is empty gets the same pointer as one that is not
        # there at all: from a home directory, either is a run that cannot see the
        # cards next to build.py, and only one of the two is a refusal.
        for line in _cards_elsewhere(args):
            report.line(line, always=True)
        return ending(1, "nothing was built")

    if withholding is not None and withholding.refused:
        # Before the --check branch, so that --check refuses the way the build would.
        return ending(2, "a player's name survived withholding, no dashboard written")
    if args.check:
        report.line("  --check: nothing was written.", always=True)
    elif unwritable is not None:
        # Before the validation count below, and returning instead of falling through
        # to it: a report a club cannot open is the worse of the two problems, and the
        # last line of a report is the one line that always gets read.
        return ending(2, _not_written(report, args=args, exc=unwritable))
    elif written is not None and written.paths:
        index = args.out / "index.html"
        _wrote_line(report, args=args, written=written)
        # A field whose value is a path *and* then some words, so it wraps: a deep
        # `--out` pushed `(double-click works; no web server needed)` off the edge,
        # and that parenthesis is the whole of what a club needs to know about
        # opening it. `wrote` above it stays a plain field, because its path is the
        # last thing on the line and a folded path costs a reader nothing.
        report.sentence("open", f"{index}  (double-click works; no web server "
                                f"needed)", always=True, keep=(str(index),))
        _one_file_line(report, written=written)
    else:
        report.line("  Nothing to write: no competition had any cards.",
                    always=True)
    _players_result(report, args=args, withholding=withholding, before=before)

    if unscored or unlisted or errors:
        parts = _refused_clauses(unscored, unlisted)
        if errors:
            parts.append(f"{errors} validation error(s)")
        return ending(1, *parts)

    # `filed`, not the number of pages read: a card the reader understood and the
    # filing could not place is not a scored card, and this is the line that would
    # otherwise say it was. Unreachable while F4 turns any such card into an error --
    # which is the point of stating the number that is true either way.
    return ending(0, f"all {filed} card(s) scored, no errors")


# What the files a run left alone are said to have done, on both lines that count a
# write. One phrase, because the two lines are read one under the other.
_HELD = "already held what this run would write"


def _count_written(count: int, total: int, what: str, *, none: str) -> str:
    """*count* written of the *total* a run answered for, in the report's three shapes.

    One rule for the two lines that count a write, the ``wrote`` line and the ``one
    file`` line under it, each asking it with its own count and its own total. Every
    one of them is the count and *what* they are. Some of them is the count out of
    the total, with the rest said to be current, because the rest is the half a club
    acts on. None of them is *none*, the sentence each line says why in, because a
    count of nothing reads as a failure.

    Asked with each line's own numbers, because the two counts part company in both
    directions: the run after a club removes the # from its team names rewrites every
    folded page and leaves half the site alone (11 of 22 files over three divisions),
    and a run that puts back one missing ``index.html`` writes a file and none of the
    pages. A ``one file`` line that took its some-or-none from the line above said
    ``0 of 3 written`` on that run.
    """
    if count == total:
        return f"{count} {what}"
    if count:
        return f"{count} of {total} {what}; the other {total - count} {_HELD}"
    return none


def _wrote_line(report: _Report, *, args: argparse.Namespace,
                written: site.SiteWrite) -> None:
    """What the run did to the files it is responsible for.

    Three shapes, because three things happen and a club acts differently on each.
    Every file written is a first build or a season that moved, and says so as a
    count -- the line this program has always printed. Some of them written is the
    ordinary week, and the count of the ones left alone is the interesting half: it
    is the difference between "tonight's cards landed" and "something rewrote the
    whole dashboard".

    And nothing written is the one that needs a sentence. The tree is complete and
    correct, so "wrote 0 file(s)" would read as a failure; what actually happened is
    that the cards have not moved since the date already on the page. That date is
    printed here, in the same words the page itself uses -- :func:`site.pretty_date`
    rather than a second formatter -- because the question a club asks next is
    whether the dashboard is current, and the answer is a date, not a file count.
    """
    total, count = len(written.paths), len(written.written)
    dated = site.pretty_date(written.stamp, with_time=True) if written.stamp else ""
    since = (f", so the dashboard still shows {dated} -- the build its figures "
             f"come from" if dated else "")
    said = _count_written(count, total, f"file(s) under {args.out}",
                          none=f"nothing: all {total} file(s) under {args.out} "
                               f"{_HELD}{since}")
    # The count on its own stays the field it has always been; the other two
    # shapes are sentences, with a full stop.
    if count == total:
        report.field("wrote", said, always=True, keep=(str(args.out),))
        return
    report.sentence("wrote", f"{said}.", always=True, keep=(str(args.out),))


def _one_file_line(report: _Report, *, written: site.SiteWrite) -> None:
    """The folded dashboards, when the run has any: what to send, and how big.

    The size, because the file exists to be attached to something and 358KB is the
    number that decides whether it can be. Named in full for one competition, since
    a club with one division wants the path and this is the answer to "where is the
    thing I can send"; counted for several, because nine divisions is nine paths in a
    section that is eight lines long, and each of the nine sits beside a dashboard
    the line above has already named.

    Silent when the run folded nothing, which is every run without ``--single-file``
    in a tree that has never had it. A line saying so would be a line about a flag
    the club did not use, printed under the one that tells them where their
    dashboard is.

    The count is of the pages this run wrote, said by :func:`_count_written` as the
    ``wrote`` line above says its own: all of them, some of them with the rest said
    to be current, and none, which says why. Counting the pages the run answered for
    said ``3 written`` on a run that wrote one, and on a run whose line above said it
    wrote nothing.
    """
    folded = site.folded_pages(written.paths)
    if not folded:
        return
    # Off disk rather than from the bytes that were written: the interesting case is
    # the run that wrote nothing because nothing had changed, and it is the file
    # already there that a club would be sending.
    total = round(sum(path.stat().st_size for path in folded) / 1024)
    if len(folded) == 1:
        report.sentence("one file", f"{folded[0]}  ({total}KB)  -- the whole "
                                    f"dashboard in one page, to email or put on a "
                                    f"phone", always=True)
        return
    # The folds above that this run wrote, so the count and the total are of the
    # same files. Asked of the written paths alone, the rule would miss a fold
    # whose season document was already current, because it needs that document
    # beside it among the paths it is given.
    written_now = set(written.written)
    count = sum(1 for path in folded if path in written_now)
    beside = f"one beside each dashboard ({total}KB in all)"
    said = _count_written(count, len(folded), f"written, {beside}",
                          none=f"none written: all {len(folded)}, {beside}, {_HELD}")
    report.sentence("one file", f"{said}  -- each the whole dashboard in one page, "
                               f"to email or put on a phone", always=True)


def _not_written(report: _Report, *, args: argparse.Namespace,
                 exc: OSError) -> str:
    """The last word when the reading worked and the writing did not.

    Everything above this in the report is a fact about the club's cards, and nothing
    the disk did changes any of it, so the ending says so rather than leaving a reader
    to work out how much of what they just read still stands. What it does not say is
    that nothing was written: ``write_site`` writes one competition at a time, so a
    failure on the third of four leaves the first two on disk, and "nothing was
    written" would be a guess about those two.

    Returns the reason for the last line rather than printing it, so that the one line
    every reader is certain to see is written in one place, with the status it returns
    beside it.
    """
    report.flow("  ", f"The dashboard was not written to {args.out}.", always=True)
    if exc.filename:
        report.field("file", str(exc.filename), always=True,
                     keep=(str(exc.filename),))
    # The number as well as the words: this report is what a club pastes into a
    # message asking for help, and the errno is the searchable part of it.
    reason = exc.strerror or str(exc)
    if exc.errno:
        reason = f"{reason} (errno {exc.errno})"
    report.field("reason", reason, always=True)
    advice = _os_advice(exc)
    if advice:
        report.sentence("what to do", advice, always=True)
    # Loud, because the failure mode it heads off is the one this project exists to
    # prevent: a club that cannot write today opens last week's dashboard, which looks
    # exactly like this week's and is wrong.
    report.flow("  ", f"What is in {args.out} is still there and out of date -- an "
                      f"earlier run wrote it, or the part of this one that got as "
                      f"far as writing did.", always=True)
    # Not loud: under --quiet there is nothing above this to reassure anybody about.
    report.flow("  ", "Everything above was read and checked before the write was "
                      "tried, so those numbers are what your cards say.")
    return f"could not write the dashboard to {args.out}"


def _config_not_written(report: _Report, *, args: argparse.Namespace,
                        exc: OSError, path: Path | None = None,
                        proposal: bool = False) -> None:
    """The lines about a starter config that would not write, above the last word.

    Shorter than its sibling above, and on purpose. That one is about the thing a club
    came for and has to say how much of the report still stands; this one is about a file
    this program offered to write, from cards the club already has, holding nothing
    anybody typed. So it says which file, why, what to do -- and then that the dashboard
    is unaffected, because "config" sounds like something a build depends on and the
    club has no way to know from here that this one is not.

    A *proposal* is the same file written beside a config that is there, and is
    *path*: what it says to do sends no club to ``--config``, which names the file
    it is beside and not where it goes, and what it says is not at stake is the
    config, which was only read.
    """
    report.flow("  ", f"{path or args.config} could not be written.", always=True)
    if exc.filename:
        report.field("file", str(exc.filename), always=True,
                     keep=(str(exc.filename),))
    # The number as well as the words, for the same reason as the write above: this
    # report is what a club pastes into a message asking for help.
    reason = exc.strerror or str(exc)
    if exc.errno:
        reason = f"{reason} (errno {exc.errno})"
    report.field("reason", reason, always=True)
    advice = (_proposal_advice(exc, args=args) if proposal
              else _config_advice(exc, writing=True))
    if advice:
        report.sentence("what to do", advice, always=True)
    if proposal:
        report.flow("  ", f"Nothing you entered is at stake: {args.config} was only "
                          f"read, and is as it was. The dashboard was built from it, "
                          f"so it is unaffected.", always=True)
        return
    # Not loud: under --quiet there is nothing above this to reassure anybody about.
    # Flowed rather than hand-wrapped so that the next club to reword a sentence of it
    # cannot leave a line hanging over the margin.
    report.flow("  ", "Nothing you entered is at stake: that file is this program's "
                      "own suggestion, filled in from the cards. The dashboard was "
                      "built from the settings as they were read, so it is unaffected "
                      "-- and on a first run those are the defaults, which the `rules "
                      "in force` section above prints in full.")


if __name__ == "__main__":
    raise SystemExit(main())
