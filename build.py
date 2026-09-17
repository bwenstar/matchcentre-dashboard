#!/usr/bin/env python3
"""Read the scorecards in ``data/`` and write a dashboard. The one command.

    python3 build.py                 # data/ -> site/, writing config.toml if absent
    python3 build.py --check         # read and validate, write nothing
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
    print(f"Python {want} added the tomllib module, which is how config.toml is",
          file=stream)
    print("read without installing anything. Try one of:", file=stream)
    print("  python3.11 build.py            # if a newer Python is installed",
          file=stream)
    print("  py -3.11 build.py             # on Windows", file=stream)
    print("  sudo apt install python3.11   # Debian, Ubuntu", file=stream)
    raise SystemExit(2)


require_python()

import argparse                                              # noqa: E402
from datetime import datetime                                # noqa: E402
from pathlib import Path                                     # noqa: E402

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    # So that `python3 /somewhere/else/build.py` finds its own package.
    sys.path.insert(0, str(ROOT))

from matchcentre import __version__                           # noqa: E402
from matchcentre import config as config_mod                  # noqa: E402
from matchcentre import discover as discover_mod              # noqa: E402
from matchcentre import export, scorecard, site               # noqa: E402
from matchcentre import scoring as scoring_mod                # noqa: E402
from matchcentre import stats as stats_mod                    # noqa: E402
from matchcentre.competition import (Competition,             # noqa: E402
                                     CompetitionError, Provenance)
from matchcentre.config import ConfigError                    # noqa: E402
from matchcentre.discover import DiscoveryError               # noqa: E402
from matchcentre.format import FormatError                    # noqa: E402
from matchcentre.scoring import ScoringError                  # noqa: E402
from matchcentre.site import SiteError                        # noqa: E402

DEFAULT_DATA = "data"
DEFAULT_OUT = "site"

WIDTH = 78

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

    def __init__(self) -> None:
        self._lines: list[tuple[bool, str]] = []

    def head(self, title: str, *, always: bool = False) -> None:
        bar = "=" * max(3, WIDTH - len(title) - 4)
        self.line(always=always)
        self.line(f"== {title} {bar}", always=always)

    def line(self, text: str = "", *, always: bool = False) -> None:
        self._lines.append((always, text.rstrip()))

    def field(self, label: str, value: str, *, always: bool = False) -> None:
        self.line(f"  {label:<13}{value}", always=always)

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
        return 2
    except OSError as exc:
        _flush(report, args)
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _stamp(report: _Report) -> None:
    """Which build wrote this report, and which Python ran it.

    Added before any work is done, and marked ``always`` so ``--quiet`` keeps it,
    because the report is the whole of what a maintainer has to go on when a club
    reports a problem -- and the two facts nobody ever thinks to include are which
    version they are running and which Python is running it. Stamping it here
    rather than in ``_compose`` means a run that dies half way through still says
    what died: every path out of ``main`` flushes this report, including the
    exception handlers below.
    """
    python = ".".join(str(part) for part in sys.version_info[:3])
    report.line(f"matchcentre-dashboard {__version__}  "
                f"(python {python} on {sys.platform})", always=True)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        prog="build.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, default=Path(DEFAULT_DATA),
                    metavar="DIR",
                    help=f"tree of exported scorecard PDFs, searched recursively "
                         f"(default: {DEFAULT_DATA}/)")
    ap.add_argument("--out", type=Path, default=Path(DEFAULT_OUT), metavar="DIR",
                    help=f"where the dashboard is written "
                         f"(default: {DEFAULT_OUT}/)")
    ap.add_argument("--config", type=Path,
                    default=Path(config_mod.DEFAULT_PATH), metavar="FILE",
                    help=f"config file to read, and to write on a first run "
                         f"(default: {config_mod.DEFAULT_PATH})")
    ap.add_argument("--check", action="store_true",
                    help="read and validate everything; write nothing at all")
    ap.add_argument("--discover", action="store_true",
                    help="rewrite the config file from the cards; needs --force "
                         "if it already exists")
    ap.add_argument("--force", action="store_true",
                    help="allow --discover to overwrite an existing config file")
    ap.add_argument("-q", "--quiet", action="store_true",
                    help="print only problems and the final result")
    ap.add_argument("--generated", metavar="ISO", default=None,
                    help="pin meta.generated to this timestamp, so that "
                         "rebuilding unchanged cards produces an identical file")
    args = ap.parse_args(argv)

    if args.generated is not None:
        try:
            datetime.fromisoformat(args.generated)
        except ValueError:
            ap.error(f"--generated {args.generated!r} is not an ISO timestamp; "
                     f"try 2026-08-18T19:00:00")
    if args.force and not args.discover:
        ap.error("--force only means anything with --discover")
    return args


def _run(args: argparse.Namespace, report: _Report) -> int:
    if not args.data.is_dir():
        print(f"error: no such directory: {args.data}", file=sys.stderr)
        print(f"  Copy the scorecard PDFs you exported from Match Centre into "
              f"{args.data} and run this again.", file=sys.stderr)
        return 2

    # 2. Load the config. A missing file is normal -- it is what a first run looks
    #    like -- but a file that exists and is malformed is not, and `load` raises.
    cfg = config_mod.load(args.config)
    if args.discover and cfg.exists and not args.force:
        print(f"error: {args.config} already exists and --discover would "
              f"overwrite it.", file=sys.stderr)
        print("  Add --force if that is what you want. Everything you have "
              "declared in it would be replaced by what the cards say.",
              file=sys.stderr)
        return 2

    # 3. Read every PDF under data/. A page that cannot be read comes back as a
    #    Rejection rather than an exception, so one bad file cannot end the run
    #    (F4 is about reporting them all, not stopping at the first).
    cards, rejected = scorecard.read_tree(args.data,
                                          expect=cfg.declared_format())

    # 4. Group the cards into competitions, tracking where each identity came from.
    found = discover_mod.discover(cards, rejected, config=cfg, root=args.data)

    config_text = _maybe_write_config(args, cfg, found, len(cards))

    seasons: list[dict] = []
    for comp in sorted(found.club.competitions, key=lambda c: c.id):
        comp_cards = found.cards.get(comp.id, [])
        comp_matches = found.matches.get(comp.id, [])
        if not comp_cards and not comp_matches:
            # Declared in the config but nothing has been played yet. It is still
            # listed on the picker -- "no results yet" and "this tool has never
            # heard of it" look identical from outside, and only one of them is
            # something the reader can wait out.
            continue
        seasons.append(export.build_season(
            comp_matches, competition=comp, source_dir=str(args.data),
            generated=args.generated, club=found.club, cards=comp_cards,
            rejected=found.rejected, seasons_mode=cfg.seasons_mode))

    written: list[Path] = []
    if not args.check and seasons:
        written = site.write_site(args.out, seasons=seasons, club=found.club,
                                  config=cfg, generated=args.generated)

    _compose(report, args=args, cfg=cfg, found=found, cards=cards,
             rejected=rejected, seasons=seasons, written=written,
             config_text=config_text)

    unscored = len(rejected)
    errors = sum(len(s["validation"]["errors"]) for s in seasons)
    status = _finish(report, args=args, cards=cards, seasons=seasons,
                    written=written, unscored=unscored, errors=errors)
    _flush(report, args)
    return status


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


# --- config -----------------------------------------------------------------


def _maybe_write_config(args: argparse.Namespace, cfg: config_mod.Config,
                        found, card_count: int) -> str:
    """Write ``config.toml`` when this is a first run, or when asked to.

    Returns the text written, empty when nothing was, so the review section can
    report what the file actually says instead of assuming.

    The dashboard is still built in the same run, from the config as it was *read*.
    That matters more than it looks: `config.render` writes some of its suggestions
    live rather than commented out, so re-reading the file would turn values this
    run derived into values it was told, and the report would then describe a run
    that never happened. Building from the config as read keeps every "derived"
    mark in this report true of this run.
    """
    if args.check:
        return ""
    if cfg.exists and not args.discover:
        return ""
    text = config_mod.render(cfg, competitions=sorted(found.club.competitions,
                                                      key=lambda c: c.id),
                             club_name=found.club_name, cards=card_count)
    args.config.parent.mkdir(parents=True, exist_ok=True)
    args.config.write_text(text, encoding="utf-8")
    return text


# --- report sections --------------------------------------------------------


def _compose(report: _Report, *, args: argparse.Namespace,
             cfg: config_mod.Config, found, cards, rejected,
             seasons: list[dict], written: list[Path],
             config_text: str) -> None:
    _rules_section(report, cfg)
    _cards_section(report, args=args, cards=cards, rejected=rejected,
                   found=found, cfg=cfg)
    _rejected_section(report, rejected)
    for season in seasons:
        _competition_section(report, season)
    _review_section(report, seasons=seasons, found=found, cfg=cfg,
                    args=args, config_text=config_text)
    _problems_section(report, seasons)


def _rules_section(report: _Report, cfg: config_mod.Config) -> None:
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
    report.field("scoring", f"{rules.name:<24}{rules.describe()}")
    report.field("sets", f"{set_rule.name:<24}{set_rule.describe()}")
    # Invisible in the ladder's order and plainly visible in its points column,
    # which is the combination that makes it worth a line of its own: a club
    # comparing totals against its association's sheet has no other way to tell
    # which convention produced the number in front of it.
    unplayed = (scoring_mod.UNPLAYED_AVERAGE if rules.average_unplayed
                else scoring_mod.UNPLAYED_ZERO)
    report.field("unplayed", f"{unplayed:<24}"
                             f"{scoring_mod.unplayed_detail(rules.average_unplayed)}")
    if declared is None:
        report.field("format", f"{'read from each card':<24}"
                               f"the row bands and marker column of every card")
    else:
        report.field("format", f"{declared.name:<24}{declared.describe()}")
        report.field("", f"{'':<24}a result needs "
                         f"{declared.min_sets_for_result} of "
                         f"{len(declared.slots)} sets complete")
    report.line()
    report.line("  These come from config.toml, never from a card: no scorecard")
    report.line("  records how its competition awards points, and 6-3 looks the")
    report.line("  same whether the set was played to 6 or to 8. If the lines")
    report.line("  above are not your competition, edit [rules] and re-run.")
    for warning in cfg.warnings:
        report.line(f"  config: {warning}", always=True)


def _cards_section(report: _Report, *, args: argparse.Namespace, cards,
                   rejected, found, cfg: config_mod.Config) -> None:
    report.head("cards")
    report.field("source", str(args.data))
    # Every page found is one of the two below (F4). They are printed even when
    # the second is zero, because the reader's question is "did it see all of my
    # cards?" and only the arithmetic answers it.
    report.field("pages seen", str(len(cards) + len(rejected)))
    report.field("scored", str(len(cards)))
    report.field("refused", str(len(rejected)), always=bool(rejected))
    report.field("competitions", str(len(found.club.competitions)))
    if cfg.multi_season:
        keys = ", ".join(found.club.season_keys) or "none"
        report.field("seasons", f"multi: {keys}")
    else:
        report.field("seasons", "single (every card is one season)")
    for note in found.notes:
        report.line(f"  note: {note}")


def _rejected_section(report: _Report, rejected) -> None:
    """Every PDF that was found and not scored, by path (F4).

    Listed even under ``--quiet``. A card that vanishes silently is the most
    expensive failure this tool can have: the ladder stays plausible and one team
    is simply short a match.
    """
    if not rejected:
        return
    report.head(f"refused: {len(rejected)} card(s) found and not scored",
                always=True)
    for item in sorted(rejected, key=lambda r: (r.path, r.page)):
        where = item.path if item.page == 0 else f"{item.path} page {item.page + 1}"
        report.line(f"  {where}", always=True)
        report.line(f"      {item.reason}", always=True)
        if item.detail:
            report.line(f"      {item.detail}", always=True)
    report.line(always=True)
    report.line("  Point the doctor at any one of them for the full structural",
                always=True)
    report.line("  reason, and what usually causes it:", always=True)
    report.line(f"      python3 tools/doctor.py "
                f"{_quote(sorted(rejected, key=lambda r: r.path)[0].path)}",
                always=True)


def _competition_section(report: _Report, season: dict) -> None:
    meta = season["meta"]
    rules = meta["rules"]
    prov = meta["provenance"]

    report.head(meta["competition"])
    report.field("name", meta["label"])
    report.field("rules", f"{rules['scoring']} / {rules['set_rule']} / "
                          f"{rules['format']}")
    rounds = ", ".join(meta["rounds_present"]) or "none"
    report.field("cards", f"{meta['cards_scored']} across round(s) {rounds}")
    report.field("teams", f"{meta['teams_total']} ({prov['teams']})")
    if meta["rounds_total"] is None:
        # F3: no percentage, and the report says why rather than leaving a reader
        # to wonder where the progress bar went.
        report.field("rounds", f"{meta['rounds_played']} played; season length "
                               f"not declared, so no percentage is shown")
    else:
        report.field("rounds", f"{meta['rounds_played']} of "
                               f"{meta['rounds_total']} played")
    report.field("draw", f"{meta['draw_source']} ({prov['draw']})")
    if meta["ladder_complete"]:
        report.field("ladder", "complete: every team has played the same number")
    else:
        report.field("ladder", "in progress: teams have played unequal numbers "
                               "of matches")
    _ladder(report, season)

    errors = len(season["validation"]["errors"])
    warnings = len(season["validation"]["warnings"])
    if errors or warnings:
        report.field("problems", f"{errors} error(s), {warnings} warning(s) "
                                 f"-- see below")


def _ladder(report: _Report, season: dict) -> None:
    teams = sorted(season["teams"], key=lambda t: (t.get("position") or 999,
                                                   t["slug"]))
    if not teams:
        return
    width = max(8, min(28, max(len(t["short"]) for t in teams)))
    report.line()
    report.line(f"   {'#':>2}  {'team':<{width}}  {'P':>2} {'W':>2} {'L':>2} "
                f"{'D':>2}  {'pts':>6}  {'avg':>5}  {'games':>9}")
    for team in teams:
        # Half-up on the printed decimal, which is what the spreadsheet a club
        # compares this against does. A plain format spec rounds the stored double
        # instead, and 6.225 is stored a hair under a half: it prints 6.22 where the
        # club's own noticeboard says 6.23.
        average = stats_mod.round_half_up(team.get("points_average"), 2)
        shown = "    -" if average is None else f"{average:5.2f}"
        points = stats_mod.round_half_up(team["points"], 1)
        report.line(f"   {team.get('position') or '-':>2}  "
                    f"{team['short'][:width]:<{width}}  "
                    f"{team['played']:>2} {team['won']:>2} {team['lost']:>2} "
                    f"{team['drawn']:>2}  {points:>6.1f}  {shown}  "
                    f"{team['games_won']:>4}-{team['games_lost']:<4}")


def _review_section(report: _Report, *, seasons: list[dict], found,
                    cfg: config_mod.Config, args: argparse.Namespace,
                    config_text: str) -> None:
    """Every identity this program worked out, and the line that would declare it.

    This is the part of the report that earns the config file. A derived value is
    not wrong, but it is a guess this program made and cannot check, and the only
    way a club can tell it apart from a fact is if something says so.
    """
    items: list[tuple[str, str, list[str]]] = []
    by_id = {c.id: c for c in found.club.competitions}
    for season in seasons:
        comp = by_id.get(season["meta"]["competition"])
        if comp is not None:
            items.extend(_review_items(comp, season, cfg))
    if not items:
        return

    report.head(f"review: {len(items)} value(s) worked out, not declared")
    if config_text:
        report.line(f"  {args.config} has just been written from the cards, and")
        report.line("  each value below says whether it landed in there live or")
        report.line("  commented out. The dashboard already works either way.")
    else:
        report.line(f"  Add these to {args.config} to turn a guess into a fact.")
        report.line("  The dashboard works either way; declared values simply")
        report.line("  stop being marked as derived on it.")
    live = _live_keys(config_text)
    for subject, why, lines in items:
        report.line()
        report.line(f"  {subject}")
        report.line(f"      why: {why}")
        if config_text:
            report.line(f"      in {args.config.name}: "
                        + ("live already, so the next run reads it as declared "
                           "-- check it now"
                           if _declared_in(lines, live) else
                           "commented out; delete the # to adopt it"))
        for text in lines:
            report.line(f"      {text}")


def _live_keys(text: str) -> set[str]:
    """The ``table.key`` names a config file actually declares, comments ignored.

    `config.render` decides for itself which of its suggestions are safe to write
    live and which have to be commented out, and it does not report which it chose.
    Reading the file back is the only way this section can say where a value ended
    up; asserting that everything is commented out would be a claim about another
    module's behaviour that this one has not checked.
    """
    table = ""
    out: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("["):
            # `[[x]]` and `[x]` name the same table for this purpose: we only need
            # the prefix a key below it belongs to.
            table = stripped.strip("[]").strip()
            continue
        key, sep, _ = stripped.partition("=")
        if sep:
            out.add(f"{table}.{_unquote(key.strip())}" if table
                    else _unquote(key.strip()))
    return out


def _declared_in(lines: list[str], live: set[str]) -> bool:
    """Whether any key of one review item is live in the config we just wrote."""
    table = ""
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("["):
            table = stripped.strip("[]").strip()
            continue
        key, sep, _ = stripped.partition("=")
        if sep and f"{table}.{_unquote(key.strip())}" in live:
            return True
    return False


def _unquote(key: str) -> str:
    """A TOML key without its quotes, so a quoted key matches an unquoted one."""
    if len(key) >= 2 and key[0] == key[-1] and key[0] in "\"'":
        return key[1:-1]
    return key


def _review_items(comp: Competition, season: dict,
                  cfg: config_mod.Config) -> list[tuple[str, str, list[str]]]:
    key = config_mod.toml_key(comp.id)
    header = f"[competitions.{key}]"
    meta = season["meta"]
    out: list[tuple[str, str, list[str]]] = []

    if not comp.teams_declared:
        names = [comp.teams[slug].name for slug in sorted(comp.teams)]
        out.append((
            f"the team list for {comp.id}",
            "a closed list is what lets a bye be credited to the right team "
            "and a misspelt team be flagged instead of quietly added as a new "
            "one",
            [header, f"teams = {config_mod.toml_value(names)}"]))

    if comp.rounds_total is None:
        out.append((
            f"the season length for {comp.id}",
            f"without it the dashboard says \"{meta['rounds_played']} rounds "
            f"played\" and shows no percentage, rather than implying the season "
            f"is over",
            [header, f"rounds_total = {max(meta['rounds_played'], 1)}"]))

    if comp.provenance_of("season") is not Provenance.DECLARED:
        # Only what the cards actually support: the season string if one was
        # printed, else the year the matches were played in, else nothing. A
        # plausible-looking invented season name here would be pasted into
        # config.toml unread and become a declared fact.
        guess = comp.season or meta["season_label"] or _year_seen(season)
        out.append((
            f"the season name for {comp.id}",
            "scorecards do not reliably print it, so the dashboard leaves it "
            "out until a human says what it is",
            [header, f"season = {config_mod.toml_value(guess)}"]
            + ([] if guess else
               ["# no card printed a season and no date gave a year: "
                "fill this in"])))

    if comp.provenance_of("draw") is not Provenance.DECLARED:
        out.append((
            f"the draw for {comp.id}",
            "a draw read from the cards is a history and never a schedule, so "
            "the dashboard cannot show a fixture that has not been played",
            _draw_lines(comp, key)))

    if not comp.format_declared:
        out.append((
            f"the match format for {comp.id}",
            f"read from the row bands on the cards as "
            f"{season['meta']['rules']['format']}; pinning it makes a card with "
            f"a row missed an error instead of a different format",
            ["[rules]",
             f"format = {config_mod.toml_value(comp.fmt.name)}"]))

    guessed_teams = sorted(slug for slug, team in comp.teams.items()
                           if team.name_source is Provenance.DERIVED)
    if guessed_teams:
        lines = ["[team_names]"]
        for slug in guessed_teams[:REVIEW_LIMIT]:
            lines.append(f"{config_mod.toml_key(slug)} = "
                         f"{config_mod.toml_value(comp.teams[slug].name)}")
        if len(guessed_teams) > REVIEW_LIMIT:
            lines.append(f"# and {len(guessed_teams) - REVIEW_LIMIT} more")
        out.append((
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
        out.append((
            f"{len(guessed_players)} player name(s) with no printed spelling",
            "these were rebuilt from a slug because no card printed the name in "
            "mixed case; check the spelling and the capitals",
            lines))

    guessed_captains = sorted(
        slug for slug, team in comp.teams.items()
        if team.captain and _captain_source(comp, team.captain)
        is Provenance.DERIVED)
    if guessed_captains:
        lines = [f"[competitions.{key}.captains]"]
        for slug in guessed_captains[:REVIEW_LIMIT]:
            team = comp.teams[slug]
            player = team.player(team.captain)
            shown = player.name if player else team.captain
            lines.append(f"{config_mod.toml_key(team.name)} = "
                         f"{config_mod.toml_value(shown)}")
        out.append((
            f"{len(guessed_captains)} captain(s) with no (C) mark on any card",
            "no card marked a captain for these teams, so the name shown was "
            "inferred",
            lines))
    return out


def _year_seen(season: dict) -> str:
    """The year the matches were played in, from their own dates.

    Empty when no card printed a date, which is the honest answer: a season name
    this program made up would be indistinguishable from one a human checked.
    """
    years = sorted({m["date"][:4] for m in season["matches"]
                    if len(m.get("date") or "") >= 4})
    return years[0] if len(years) == 1 else " to ".join(years)


def _draw_lines(comp: Competition, key: str) -> list[str]:
    """The rounds this competition actually has, as the TOML to declare them.

    Every fixture printed here was played and read off a card. Nothing is
    extrapolated: F2 forbids inventing the rest of the season, so a club that wants
    the unplayed rounds shown types them in, and the moment it does the draw stops
    being marked as derived.

    ``bye`` is only emitted when the team list is closed. With an open list a team
    with no card and a team resting are the same observation, and guessing between
    them is F1.
    """
    rounds = [comp.rounds[number] for number in comp.round_order]
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
        if comp.teams_declared and rnd.byes:
            resting = [comp.team_name(slug) for slug in rnd.byes]
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


def _captain_source(comp: Competition, slug: str) -> Provenance:
    player = comp.player_index().get(slug)
    return player.captain_source if player else Provenance.DERIVED


def _problems_section(report: _Report, seasons: list[dict]) -> None:
    """Errors then warnings, grouped by the rule each one belongs to.

    Grouped by rule rather than by competition because the rules are the thing a
    reader can act on: five F8 warnings in five divisions are one misspelt team
    name, and five different rules in one division are five different problems.
    """
    for severity, plural in (("errors", "error"), ("warnings", "warning")):
        grouped: dict[str, list[tuple[str, dict]]] = {}
        for season in seasons:
            for issue in season["validation"][severity]:
                grouped.setdefault(issue["rule"], []).append(
                    (season["meta"]["competition"], issue))
        if not grouped:
            continue
        total = sum(len(v) for v in grouped.values())
        always = severity == "errors"
        report.head(f"{total} {plural}{'' if total == 1 else 's'}",
                    always=always)
        for rule in sorted(grouped, key=_rule_key):
            report.line(f"  {rule}", always=always)
            for comp_id, issue in grouped[rule]:
                report.line(f"      {comp_id}: {issue['message']}", always=always)
                if issue.get("subject"):
                    report.line(f"          subject: {issue['subject']}",
                                always=always)
                if issue.get("detail"):
                    report.line(f"          {issue['detail']}", always=always)
        if always:
            report.line("", always=True)
            report.line("  Each F-number is a stable id for one rule about what "
                        "may be inferred from a scorecard. Quote it if you "
                        "report a problem.", always=True)


def _rule_key(rule: str) -> tuple[int, int, str]:
    """F1..F11 in numeric order, then named consistency checks alphabetically."""
    if rule[:1] == "F" and rule[1:].isdigit():
        return (0, int(rule[1:]), "")
    return (1, 0, rule)


# --- the last word ----------------------------------------------------------


def _finish(report: _Report, *, args: argparse.Namespace, cards,
            seasons: list[dict], written: list[Path], unscored: int,
            errors: int) -> int:
    report.head("result", always=True)

    if not cards:
        report.line(f"  No scorecards were read from {args.data}.", always=True)
        report.line("  Export a match from matchcentre.tennis.com.au as a PDF, "
                    "put it", always=True)
        report.line(f"  in {args.data}, and run this again. Any folder layout "
                    "underneath", always=True)
        report.line("  works: the round, division and date are read off the "
                    "cards.", always=True)
        report.line("  exit 1: nothing was built", always=True)
        return 1

    if args.check:
        report.line("  --check: nothing was written.", always=True)
    elif written:
        index = args.out / "index.html"
        report.field("wrote", f"{len(written)} file(s) under {args.out}",
                     always=True)
        report.field("open", f"{index}  (double-click works; no web server "
                             f"needed)", always=True)
    else:
        report.line("  Nothing to write: no competition had any cards.",
                    always=True)

    if unscored or errors:
        parts = []
        if unscored:
            parts.append(f"{unscored} card(s) found and not scored")
        if errors:
            parts.append(f"{errors} validation error(s)")
        report.line(f"  exit 1: {', '.join(parts)}", always=True)
        return 1

    report.line(f"  exit 0: all {len(cards)} card(s) scored, no errors",
                always=True)
    return 0


def _quote(path: str) -> str:
    """Shell-quote a path only when it needs it, so the hint can be pasted."""
    return path if all(c.isalnum() or c in "./_-" for c in path) else f'"{path}"'


if __name__ == "__main__":
    raise SystemExit(main())
