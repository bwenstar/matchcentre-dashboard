"""Reading ``config.toml``, and writing it back with everything we discovered.

The config's job is narrow and worth stating precisely: **it supplies identity and
rules, never results.** Names, captains, season labels, the closed team list, the
draw, the scoring scheme. No entry in this file can change a scoreline — those come
only from the scorecards — so a club that fills it in wrongly gets wrong *labels*,
not a wrong ladder.

The second job is the interesting one. A first run has no config, so
:func:`render` writes one from what the cards revealed, with a comment on every
line a human should look at. That turns the awkward part of onboarding — telling
the program what your competition *is* — into reviewing a filled-in form.

TOML in, TOML out, both with the standard library only: ``tomllib`` reads (hence
Python 3.11), and :func:`render` writes the small subset of the syntax this schema
uses rather than pulling in a general emitter.
"""

from __future__ import annotations

import math
import re
import sys
import textwrap
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, NamedTuple

from . import __version__
from . import format as fmt_mod
from . import scoring
from .card import round_key
from .scope import choices, echoed, in_words
from .slugs import did_you_mean, label_key, near_misses, nearest, slugify, team_slug

if TYPE_CHECKING:
    from .competition import Competition

__all__ = ["Config", "CompetitionConfig", "RoundConfig", "ConfigError",
           "load", "render", "commented_teams", "team_list_lines",
           "teams_instruction", "named", "DEFAULT_PATH", "SEASON_MODES",
           "PLAYER_MODES", "RULES_SETTINGS", "SECTIONS", "WIDTH"]

DEFAULT_PATH = "config.toml"

# The name of the config `tools/demo.py --seasons multi` writes, and how it begins,
# written there from these constants. The demo writes that file again on every run,
# so a line typed into it is gone the next time, and no note or review of a build
# that reads it asks for one, nor the rules' footer. A copy under another name is
# the club's to edit.
DEMO_CONFIG = "demo-seasons.toml"
DEMO_MARKER = "# Written by tools/demo.py"

# What `build.py --discover` writes beside a config that exists, rather than over
# it (4.431): the file `--discover --force` would write, under a header of its own,
# named after the config and known as this program's by its first line, as the
# demo's is. Nothing reads it, and it is written again on every such run, so a file
# of that name that does not begin so is a club's, and is never written over.
PROPOSAL_SUFFIX = ".proposed.toml"
PROPOSAL_MARKER = "# matchcentre-dashboard: a proposed config, which nothing reads"
# Stands for a space that a paragraph is not to be broken at, while it is flowed.
_GLUE = "\0"

SEASON_MODES = ("single", "multi")

# What `[build] players` may say: whether the dashboards print the players' names,
# or withhold them (see matchcentre/anonymise.py). "names" is the default.
PLAYER_MODES = ("names", "initials", "numbered")

RULES_SETTINGS = ("scoring", "sets", "unplayed", "draws", "format", "slots",
                  "min_sets_for_result", "forfeit_award", "forfeit_points",
                  "finals")
"""Every setting ``[rules]`` accepts, and every one a competition may override.

Named once because four places have to agree about it: the two ``_reject_unknown``
calls that decide whether a line is honoured or warned about, the example config a
club copies the line out of, and the test that holds the second to the first. Spelt
out as a literal rather than derived from :class:`Config`'s fields, because the two
are deliberately not the same set -- ``[rules] slots`` is a list of set kinds and
``Config.slots`` is a tuple, ``[club] name`` is a field and not a rule -- so a
derived set would either be wrong or would need a list of exceptions as long as
this one.
"""


class ConfigError(ValueError):
    """The config file exists but cannot be honoured."""


class NotToml(ValueError):
    """TOML :func:`read_toml` cannot read, and why, in the words a club reads.

    ``invalid`` is true only for ``tomllib``'s own mistake, a file that breaks
    TOML's grammar. A number too long to print and lists nested too deep are
    valid TOML that this program still cannot read, and are not called invalid.
    """

    def __init__(self, why: str, *, invalid: bool = False) -> None:
        super().__init__(why)
        self.invalid = invalid


@dataclass(frozen=True, slots=True)
class RoundConfig:
    """One declared round. Everything but ``number`` is optional.

    ``byes`` is plural, and reading it is forgiving: ``bye = "Rockets"`` and
    ``bye = ["Rockets", "Falcons"]`` both land here as a tuple. The list form is
    not a nicety — a whole competition can rest in the same week — and before it
    was read properly a list was coerced with ``str()``, which produced one team
    whose name was every resting team's name run together. A wrong answer with no
    error, in a program that otherwise fails closed.

    ``finals`` is what the club typed, and ``None`` where it typed nothing: read as
    true or not, a ``finals = false`` on a round labelled ``SF`` was the same as no
    line, and the label made a final of it anyway.
    """

    number: str
    date: str = ""
    fixtures: tuple[tuple[str, str], ...] = ()
    byes: tuple[str, ...] = ()
    finals: bool | None = None
    note: str = ""

    @property
    def is_finals(self) -> bool:
        """Whether the round is a final: as typed, or by its label if nothing was."""
        from .competition import is_finals_label
        return is_finals_label(self.number) if self.finals is None else self.finals


@dataclass(frozen=True, slots=True)
class ExpectedLine:
    """One team's row off the association's sheet, as the club typed it.

    ``points`` is decimal text rather than a number, because the places it was
    typed to are part of what it claims: ``"10.9"`` says one place and ``"10.90"``
    two. ``""`` and ``None`` are a figure the club did not type, which is checked
    by nothing -- never a zero.
    """

    key: str
    points: str = ""
    played: int | None = None


@dataclass(frozen=True, slots=True)
class ExpectedSheet:
    """``[competitions.<id>.expected]``: what the association's sheet says.

    An expectation, never a result. Nothing reads a figure out of it but the check
    that holds it up against the cards, so no ladder figure can come from here.
    ``after_round`` is the last regular round the sheet covers, as a whole number in
    text; team keys are kept as typed, and matched to teams once the cards are read.
    """

    after_round: str
    lines: tuple[ExpectedLine, ...]


@dataclass(frozen=True, slots=True)
class CompetitionConfig:
    """What a club can say about one competition.

    ``teams`` is the field that matters most. Supplying it closes the team list,
    which is the precondition for inferring a bye and for treating an unknown team
    on a card as an error rather than a seventh team. Leaving it out is legal and
    the dashboard still works; it just stays explicit about what it does not know.
    """

    id: str
    label: str = ""
    day: str = ""
    division: str = ""
    season: str = ""
    season_key: str = ""
    rounds_total: int | None = None
    teams: tuple[str, ...] = ()
    captains: dict[str, str] = field(default_factory=dict)
    courts: dict[str, int] = field(default_factory=dict)
    codes: dict[str, int] = field(default_factory=dict)
    rounds: tuple[RoundConfig, ...] = ()
    byes: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # [competitions.<id>.washouts], by round: the fixtures of it the rain stopped, as
    # the club typed them, and () where it stopped the whole round (4.535).
    washouts: dict[str, tuple[tuple[str, str], ...]] = field(default_factory=dict)
    scoring: str = ""
    sets: str = ""
    unplayed: str = ""
    draws: bool | None = None
    format: str = ""
    slots: tuple[str, ...] = ()
    min_sets_for_result: int | None = None
    forfeit_award: str = ""
    forfeit_points: float | None = None
    # What the finals are, as the section typed it, '' where it typed nothing
    # (4.537): "none" is typed, and turns a [rules] "placings" off.
    finals: str = ""
    expected: ExpectedSheet | None = None
    # A sheet table with something typed in it that the loader could compare no line
    # of: no usable after_round, or every line refused. ``expected`` says nothing of
    # it, and ``--discover --force`` writes none of it back, so the header of the
    # proposal ``--discover`` writes beside the file names it.
    expected_unusable: bool = False
    # The lines, by key, of a table it does compare that had a figure refused, or
    # that were typed as no table of figures at all. The table is written back as
    # it was read, so what was refused goes, and that header names the line.
    expected_refused: tuple[str, ...] = ()

    @property
    def teams_declared(self) -> bool:
        return bool(self.teams)

    @property
    def season_length(self) -> int | None:
        """How many rounds the regular season has, as far as the club has said.

        ``rounds_total`` if it gave one, and otherwise the rounds its draw lists,
        finals left out: the rounds a club published are the rounds it intends to
        play, so declaring the draw declares the length. One rule for the page and
        for the file, because ``--discover --force`` drops the draw and writes the
        length it gave as ``rounds_total``: counted in two places, a length only the
        draw stated went with it. And counted off the declared rounds rather than
        the rounds built from them, which a round listed with no fixtures leaves
        once its cards arrive -- nine rounds read as six by round three.
        """
        return self.season_length_for("")

    def season_length_for(self, finals: str) -> int | None:
        """``season_length`` under the finals format the section is played to (4.537).

        Under ``finals = "placings"`` a round of the draw that names a placing match,
        ``Playoff`` or ``Placings``, is the placing week whether or not it was typed
        a final, so it is no round of the season's length: counted in, a draw of
        five rounds and a Playoff read ``5 of 6 rounds played`` for good, and the
        pairs it waited on were never set. A round typed regular is as typed.
        """
        from .competition import names_placing
        if self.rounds_total is not None:
            return self.rounds_total
        return sum(1 for r in self.rounds if not r.is_finals and not (
            r.finals is None and names_placing(r.number, finals))) or None

    @property
    def length_by_labels(self) -> int | None:
        """``season_length`` with each round a final by its label, as ``--force`` writes.

        The rewrite drops the draw, and every ``finals`` typed in it with the draw, so
        the run after it tells a final by its label alone. Counted under the typed
        flags, the length it wrote was a round short when a round was typed a final
        against its label, and F3 stopped the next run, or a round long when a final
        was typed regular. The same rule over the same rounds, so a ``rounds_total``
        the club typed is theirs and comes back as typed.
        """
        return self.length_by_labels_for("")

    def length_by_labels_for(self, finals: str) -> int | None:
        """``length_by_labels`` under the section's finals format (4.537).

        The run after ``--force`` reads a final by its label and, under placings, by
        the placing week's rule (:meth:`finals_after_force`), so the length it is
        written is the regular rounds that run will see.
        """
        return self._after_force(finals)[0]

    def finals_after_force(self, finals: str) -> frozenset[str]:
        """The rounds of the draw the run after ``--force`` reads as finals (4.537).

        Each by its label, and under placings by the placing week's rule over the
        draw's rounds and the length written: a round that names a placing match,
        and the first numbered past the length. ``_what_force_drops`` names the
        rounds this moves from what was typed.
        """
        return self._after_force(finals)[1]

    def finals_now(self, finals: str) -> frozenset[str]:
        """The rounds of the draw this run reads as finals, the typed flags first.

        By the flag a round was typed with, else its label, and under placings the
        placing week: a round named one, and with ``rounds_total`` typed the first
        number past it. A length the draw gave is its own rounds, so none of them is
        past it, as discovery marks them (4.537).
        """
        from .competition import names_placing, placing_weeks
        numbered = () if self.rounds_total is None else (r.number for r in self.rounds)
        weeks = placing_weeks(numbered, length=self.season_length_for(finals),
                              finals=finals)
        return frozenset(r.number for r in self.rounds
                         if (r.is_finals if r.finals is not None else (
                             r.is_finals or r.number in weeks
                             or names_placing(r.number, finals))))

    def _after_force(self, finals: str) -> tuple[int | None, frozenset[str]]:
        """The length ``--force`` writes, and the finals the run after it reads.

        By the labels: a round is a final if its label names one, or under placings
        names a placing match. And under placings the draw's last round, numbered
        past every other regular round it lists and typed ``finals = true``, is the
        placing week by its number once the length leaves it out, as it is in this
        run: counted in, ``--force`` wrote a length one round long, and the run after
        scored the placing week into the ladder with exit 0.
        """
        from .competition import (is_finals_label, names_placing, placing_weeks,
                                  round_number)
        regular = [r for r in self.rounds if not is_finals_label(r.number)
                   and not names_placing(r.number, finals)]
        typed = [r for r in regular
                 if r.finals is True and round_number(r.number) is not None]
        if finals == scoring.FINALS_PLACINGS and len(typed) == 1 and all(
                round_number(r.number) is not None
                and round_number(r.number) < round_number(typed[0].number)
                for r in regular if r is not typed[0]):
            regular.remove(typed[0])
        length = (self.rounds_total if self.rounds_total is not None
                  else len(regular) or None)
        weeks = placing_weeks((r.number for r in self.rounds), length=length,
                              finals=finals)
        return length, frozenset(r.number for r in self.rounds
                                 if is_finals_label(r.number) or r.number in weeks)

    @property
    def can_claim_cards(self) -> bool:
        """Whether a card could ever be routed to this section.

        A day and a division together are how a card finds its settings, so a
        section giving both names a competition that exists whether or not its
        scorecards have arrived. A section giving only one of them names nothing
        in particular: several competitions match it, and the front page would
        have to pick a night for it. That is the whole difference between a
        section this tool can list as awaiting results and one it can only
        complain about, so the test is written down once and shared by the router,
        the report and the front page rather than being spelled out three times.
        """
        return bool(self.day and self.division)

    def byes_by_round(self) -> dict[str, tuple[str, ...]]:
        """``byes`` transposed: round number to the teams resting in it.

        Stored the way the club writes it — one line per team, the rounds it sits
        out — because that is the shape of the sheet an association hands out, and
        transcribing a grid is where mistakes get made. The draw wants the other
        axis, so the turn happens here, once.
        """
        out: dict[str, list[str]] = {}
        for team in sorted(self.byes):
            for number in self.byes[team]:
                out.setdefault(number, []).append(team)
        return {number: tuple(teams) for number, teams in out.items()}


@dataclass(frozen=True, slots=True)
class Config:
    """The whole file, normalised, with defaults filled in.

    ``exists`` is false when no file was found. Callers use it to decide whether
    this is a first run — the run that writes the file — rather than guessing from
    whether the contents look empty.
    """

    path: Path
    exists: bool = False
    # The demo's own file (:func:`written_by_demo`): no note, review or footer
    # asks for a line of it.
    demo: bool = False
    club_name: str = ""
    strip_team_prefix: str = ""
    seasons_mode: str = "single"
    title: str = ""
    players_mode: str = "names"
    # '' is "not declared", as in a [competitions] section: the defaults are
    # applied by ruleset_for and set_rule_for, so whether a club chose them is kept.
    scoring: str = ""
    sets: str = ""
    unplayed: str = ""
    # Tri-state, and it has to be: ``None`` is "the preset decides", ``False`` is a
    # club saying its grade cannot draw. Collapsing the two into a bool would make
    # the setting unsettable, since its whole purpose is the falsy value.
    draws: bool | None = None
    format: str = ""
    slots: tuple[str, ...] = ()
    min_sets_for_result: int | None = None
    # What a match forfeited whole is worth (4.420): '' and None are "the preset
    # decides", as for unplayed and draws; points are read only under "fixed".
    forfeit_award: str = ""
    forfeit_points: float | None = None
    # What the season's finals are (4.537): '' is "not declared", as unplayed's.
    finals: str = ""
    season_labels: dict[str, str] = field(default_factory=dict)
    team_names: dict[str, str] = field(default_factory=dict)
    # Each [team_names] key as typed -> the slugs the loader filed it under, so
    # whether a line reached a team is asked of what the loader did.
    team_name_keys: dict[str, tuple[str, ...]] = field(default_factory=dict)
    player_names: dict[str, str] = field(default_factory=dict)
    aliases: dict[str, str] = field(default_factory=dict)
    # Player slug -> the team slugs whose roster rows are each a person of their
    # own. The inverse of ``aliases``, which folds two spellings into one player.
    namesakes: dict[str, tuple[str, ...]] = field(default_factory=dict)
    competitions: dict[str, CompetitionConfig] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()

    # --- rules ----------------------------------------------------------

    def ruleset_for(self, comp: CompetitionConfig | None = None) -> scoring.Ruleset:
        """The preset, with ``unplayed``, ``draws`` and the forfeit award over it.

        Each preset carries an opinion about what a bye is worth, about whether a
        night can end level, and about what a forfeit pays, and each is only an
        opinion: two associations running identical points schemes routinely differ
        on any of the three, so they have to be settable without inventing a preset
        per pairing. Left unset, the preset's own values stand.

        Every clause is applied, not one or another. An early return after the
        first would silently drop the rest, and which got dropped would depend on
        the order they happen to be read in.
        """
        base = scoring.ruleset(self._declared_rule(comp, "scoring")
                               or scoring.DEFAULT_RULESET)
        mode = self._declared_rule(comp, "unplayed")
        if mode:
            base = replace(base, average_unplayed=(mode == scoring.UNPLAYED_AVERAGE))
        # Asked with ``is None`` and never for truth: ``draws = false`` is the point
        # of the setting, so ``comp.draws or self.draws`` would read a competition
        # that has said "no" as one that has said nothing.
        draws = self.draws if comp is None or comp.draws is None else comp.draws
        if draws is False:
            base = replace(base, draw=None)
        # What a forfeit is worth (4.420): the award as declared, and the points
        # only where the award is "fixed", the one that reads them; load has
        # refused a "fixed" with no points and warned of points nothing reads.
        award = self._declared_rule(comp, "forfeit_award")
        if award:
            base = replace(base, forfeit_award=award,
                           forfeit_points=(self._forfeit_points(comp)
                                           if award == "fixed" else None))
        return base

    def _forfeit_points(self, comp: CompetitionConfig | None) -> float | None:
        """``forfeit_points`` as declared: the section's, else [rules]'s, else None.

        Asked with ``is None`` and never for truth, as ``draws`` is: 0 is a price.
        """
        if comp is not None and comp.forfeit_points is not None:
            return comp.forfeit_points
        return self.forfeit_points

    def finals_for(self, comp: CompetitionConfig | None = None) -> str:
        """The finals format *comp* is played to as declared, ``""`` where unsaid.

        The section's, else [rules]'s, as ``unplayed`` is: ``"placings"``, or
        ``"none"``, which plays as a club that said nothing does and is kept, since
        a club that typed it has answered what F3 would otherwise ask (4.537).
        """
        return self._declared_rule(comp, "finals")

    def set_rule_for(self, comp: CompetitionConfig | None = None) -> fmt_mod.SetRule:
        return fmt_mod.set_rule(self._declared_rule(comp, "sets")
                                or fmt_mod.DEFAULT_SET_RULE)

    def _declared_rule(self, comp: CompetitionConfig | None, key: str) -> str:
        """``scoring``, ``sets``, ``unplayed``, ``forfeit_award`` or ``finals`` as
        declared: the section's, else [rules]'s, else ''."""
        return getattr(comp, key) if comp and getattr(comp, key) else getattr(self, key)

    def rules_left_to_default(self, comp: CompetitionConfig | None = None
                              ) -> tuple[str, ...]:
        """Which of ``scoring`` and ``sets`` *comp* is scored under nobody chose.

        No card prints either, so the default is a guess until a club declares it,
        in the competition's section or under [rules]. One of the two declared is
        not both: the other is still the program's guess, and is the one named.
        """
        return tuple(key for key in ("scoring", "sets")
                     if not self._declared_rule(comp, key))

    def declared_format(self, comp: CompetitionConfig | None = None
                        ) -> fmt_mod.MatchFormat | None:
        """The format the club pinned, or ``None`` to read it off the cards.

        A declared format is checked against what each card describes and a
        disagreement stops the run. Neither reading is allowed to win quietly: a
        card with a row missed is indistinguishable from a club that changed
        format, and the two need opposite responses.
        """
        rule = self.set_rule_for(comp)
        minimum = self.min_sets_for(comp)
        slots = (comp.slots if comp and comp.slots else self.slots)
        if slots:
            return fmt_mod.from_slot_spec(slots, rule=rule,
                                          min_sets_for_result=minimum)
        name = (comp.format if comp and comp.format else self.format)
        if name:
            return fmt_mod.match_format(name, rule=rule, min_sets_for_result=minimum)
        return None

    def min_sets_for(self, comp: CompetitionConfig | None = None) -> int | None:
        """The sets a result needs, as the club said it: own section, else [rules].

        ``None`` when neither says, which is the match format's default and is the
        one answer the review list asks about. Asked with ``is None`` and never for
        truth, so a number is either the club's or nobody's.
        """
        if comp is not None and comp.min_sets_for_result is not None:
            return comp.min_sets_for_result
        return self.min_sets_for_result

    def rules_minimum(self, competitions: Iterable[Competition]) -> int | None:
        """The one ``[rules] min_sets_for_result`` that changes no result, or None.

        A ``[rules]`` number reaches every competition that does not set its own,
        so it is offered only when all of those already need that number.
        """
        return _agreed(comp.fmt.min_sets_for_result for comp in
                       self._inheriting(competitions, "min_sets_for_result"))

    def rules_format(self, competitions: Iterable[Competition]) -> str | None:
        """The one ``[rules] format`` that refuses no card, or None. As above."""
        return _agreed(comp.fmt.name for comp in
                       self._inheriting(competitions, "format", "slots"))

    def _inheriting(self, competitions: Iterable[Competition], *keys: str
                    ) -> list[Competition]:
        """The competitions a ``[rules]`` line of these keys would reach."""
        out = []
        for comp in competitions:
            own = self.competition(comp.id)
            if own is None or all(getattr(own, key) in (None, "", ())
                                  for key in keys):
                out.append(comp)
        return out

    # --- names ----------------------------------------------------------

    def club_display(self, letterhead: str) -> str:
        """The club's name as the pages show it: the club's own, else the letterhead's.

        One rule, one function, for discovery, which names the club the pages are
        headed with, and for :func:`render`, which makes the title it suggests from
        the same name. Each wrote the line out for itself, so the suggestion could
        follow one name while the heading followed another.
        """
        return self.club_name or letterhead

    def team_display(self, slug: str) -> str | None:
        return self.team_names.get(slug)

    def player_display(self, slug: str) -> str | None:
        return self.player_names.get(slug)

    def split_namesake(self, slug: str, team: str) -> str:
        """The player slug to use for a printed name a club has said is two people.

        One rule, one function, because the squad builder, the set rows, the roster
        lists, the ratings and the declared captain all have to reach the same
        identity from the same name, and the only way they can is to ask here.

        Undeclared names and teams the declaration did not name come back exactly
        as they went in. The split is a statement a human made and never a guess:
        one name in two teams' roster blocks is equally a player who changed teams
        mid-season, and splitting that would invent somebody who does not exist.
        """
        if team and team in self.namesakes.get(slug, ()):
            return f"{slug}-{team}"
        return slug

    def competition(self, comp_id: str) -> CompetitionConfig | None:
        return self.competitions.get(comp_id)

    def season_label(self, key: str) -> str:
        """The ``[seasons]`` label of the season keyed *key*, or ``""``.

        Each key is read by the rule a folder under ``data/`` is read by, so
        ``"2026-Winter"`` labels the season ``data/2026-winter/`` files its cards
        under: asked as typed, it labelled nothing, and nothing said so. Two keys
        that rule reads as one season settle whatever order the file lists them
        in: the one spelt as the season is, else a label every one of them agrees
        on, else none. :func:`~matchcentre.discover.discover` says which, once.
        """
        typed = self.season_label_keys(key)
        if key in typed:
            return self.season_labels[key]
        labels = {self.season_labels[line] for line in typed}
        return labels.pop() if len(labels) == 1 else ""

    def season_label_keys(self, key: str) -> set[str]:
        """The ``[seasons]`` keys, as typed, that the folder rule reads as *key*.

        Empty for ``""``, which is no season: the rule reads a key like ``"!!!"``
        as nothing, and a card keyed by nothing is not one that line labels.
        """
        from .discover import season_key_of_folder
        return {line for line in self.season_labels
                if key and season_key_of_folder(line) == key}

    @property
    def multi_season(self) -> bool:
        return self.seasons_mode == "multi"


# --- reading ----------------------------------------------------------------


def read_toml(text: str) -> dict:
    """*text* read as TOML, or :class:`NotToml` saying why it cannot be.

    The one reader for both TOML files this program takes, the config and the
    fetcher's index. ``tomllib`` raises three things, and only one is its own: a
    ``TOMLDecodeError``, whose text names the line and column and is kept; a
    plain ValueError for a decimal whole number longer than Python prints
    (``sys.get_int_max_str_digits()``); and a RecursionError for lists or tables
    nested past Python's stack. A hexadecimal number is read at any length and
    raised the same ValueError later, wherever it was printed, so every number
    read is held to the limit here, once. Only a hexadecimal, octal or binary
    number can be that long here, and TOML gives those no sign. The refusal
    counts the decimal digits Python would print, so it is true of a number
    written in hexadecimal too. It names no key: tomllib's ValueError gives no
    line, and naming one for the hexadecimal spelling alone would say one
    mistake two ways.
    """
    limit = sys.get_int_max_str_digits()
    too_long = f"a whole number in it is more than {limit} decimal digits long"
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise NotToml(str(exc), invalid=True) from None
    except ValueError:
        raise NotToml(too_long) from None
    except RecursionError:
        raise NotToml("its lists or tables are nested too deep to read") from None
    if limit:
        ceiling = 10 ** limit
        stack: list[object] = [data]
        while stack:
            value = stack.pop()
            if isinstance(value, dict):
                stack.extend(value.values())
            elif isinstance(value, list):
                stack.extend(value)
            elif isinstance(value, int) and value >= ceiling:
                raise NotToml(too_long)
    return data


def written_by_demo(path: Path, text: str) -> bool:
    """Whether *text*, read from *path*, is the config the demo writes again: the
    file of its name, begun as it begins it."""
    return path.name == DEMO_CONFIG and text.startswith(DEMO_MARKER)


def proposal_path(path: str | Path) -> Path:
    """Where ``--discover`` proposes a config for the one at *path*: beside it,
    named after it, so ``other.toml`` has ``other.proposed.toml``. Beside it and
    not under ``--out``, which is published."""
    path = Path(path)
    return path.with_name(f"{path.stem}{PROPOSAL_SUFFIX}")


def written_as_proposal(text: str) -> bool:
    """Whether *text* is a proposal this program wrote: begun as each one is."""
    return text.startswith(PROPOSAL_MARKER + "\n")


def proposal_written(text: str) -> str | None:
    """The date the header of the proposal *text* says it was written on, as it
    says it, or ``None`` for a text that is not one, or names none: what a run
    renders the proposal with to ask whether the file holds it already."""
    if not written_as_proposal(text):
        return None
    header = []
    for line in text.splitlines()[1:]:
        if line == _PROPOSAL_RULE:
            break
        header.append(line[2:])
    said = re.search(r"\) on (.+?), from the \d+ scorecards? it read\.",
                     " ".join(header))
    return said.group(1) if said else None


def proposal(rendered: str, *, config_name: str, written: str, cards: int,
             drops: str) -> str:
    """The proposal ``--discover`` writes beside *config_name*: *rendered*, the
    file ``--discover --force`` would write over it, under a header that says so.

    The header says which run wrote it, that nothing reads it, how to adopt a part
    of it or the whole, and, in *drops*, what adopting it whole loses: the words
    ``--discover`` used to refuse with, said where they are acted on. *written* is
    the run's date as the page prints one. The rendered file follows the row of
    dashes the header ends with, so deleting down to them leaves it whole.
    """
    # The date is flowed as one word: split at a space, "on 8" ended one line and
    # "Oct 2026" began the next, at 13 of 90 lengths of version and date.
    glued = written.replace(" ", _GLUE)
    paragraphs = (
        f"Written by `python3 build.py --discover` (matchcentre-dashboard "
        f"{__version__}) on {glued}, from the {cards} "
        f"scorecard{'' if cards == 1 else 's'} it read. {config_name} already "
        f"exists, so it was left as it was and this was written beside it instead. "
        f"No run reads this file.",
        f"It is the file `python3 build.py --discover --force` would write over "
        f"{config_name}: what {config_name} declares is live, and what the cards "
        f"offer besides is behind a #. The report of the run that wrote it lists "
        f"what it offers that {config_name} lacks.",
        f"To use a part of it, copy those lines into {config_name}. To adopt it "
        f"whole, copy it over {config_name} and delete these lines, down to and "
        f"including the row of dashes below. That does to {config_name} what "
        f"--discover --force does. {drops}",
        f"The next --discover writes this file again, so make your changes in "
        f"{config_name} rather than here.",
    )
    lines = [PROPOSAL_MARKER]
    for paragraph in paragraphs:
        lines.append("#")
        lines.extend(line.replace(_GLUE, " ") for line in _comment_flow(paragraph))
    lines.append(_PROPOSAL_RULE)
    return "\n".join(lines) + "\n" + rendered


def load(path: str | Path = DEFAULT_PATH) -> Config:
    """Read the config, or return usable defaults when there is no file.

    A missing file is not an error — it is what a first run looks like. A file
    that exists and is malformed *is* an error: silently falling back to defaults
    would mean a club's carefully entered team list stopped taking effect without
    anyone being told.
    """
    target = Path(path)
    if not target.exists():
        return Config(path=target, exists=False)
    try:
        text = target.read_text(encoding="utf-8")
        raw = read_toml(text)
    except NotToml as exc:
        what = "not valid TOML" if exc.invalid else "not TOML this can read"
        raise ConfigError(f"{target}: {what} — {exc}") from None
    except OSError as exc:
        # Chained, unlike the TOML branch above: the message carries the number for a
        # caller that only prints it, and the exception carries the original for a
        # caller that can say what to do about that particular refusal. Which
        # `build.py` does -- a folder standing where the file should be and a file this
        # account may not read are two different jobs for whoever is at the keyboard,
        # and `errno` is the only thing that tells them apart.
        raise ConfigError(f"{target}: cannot be read — {exc}") from exc
    return replace(_from_raw(raw, target), demo=written_by_demo(target, text))


# Every section this file may contain. Public because `config.example.toml` is the
# only place a club can find out that a section exists, and the suite holds the two
# to each other: a section added here and not documented there is one nobody outside
# this repository can learn about.
SECTIONS = ("club", "build", "rules", "seasons", "team_names", "player_names",
            "aliases", "namesakes", "competitions")


def _from_raw(raw: dict, path: Path) -> Config:
    warnings: list[str] = []

    for key in sorted(set(raw) - set(SECTIONS)):
        # A mistyped section does nothing at all, which is the kind of silence
        # this project exists to avoid. Name it on every run, and name the section
        # it is one keystroke from as well.
        warnings.append(f"[{key}] is not a section this program understands "
                        f"and was ignored.{_did_you_mean(key, SECTIONS)}")

    club = _table(raw, "club", warnings)
    build = _table(raw, "build", warnings)
    rules = _table(raw, "rules", warnings)
    # Before `[build]` is checked for keys it does not know, because a players line
    # this program cannot honour is refused rather than warned about.
    players = _players_mode(raw, path)

    _reject_unknown(club, {"name", "strip_team_prefix"}, "club", warnings)
    _reject_unknown(build, {"seasons", "title", "players"}, "build", warnings)
    _reject_unknown(rules, set(RULES_SETTINGS), "rules", warnings)

    mode = str(build.get("seasons", "single")).strip().lower()
    if mode not in SEASON_MODES:
        raise ConfigError(
            f"{path}: [build] seasons = {toml_value(build['seasons'])} is not "
            f"{choices(SEASON_MODES)}.{_did_you_mean(mode, SEASON_MODES)}")

    prefix = str(club.get("strip_team_prefix", "") or "")

    # Left as '' when not declared, so whether the club chose them is not lost.
    scheme = str(rules.get("scoring", "") or "")
    set_name = str(rules.get("sets", "") or "")
    # Fail here rather than three modules later, while we can still name the file.
    try:
        if scheme:
            scoring.ruleset(scheme)
        if set_name:
            fmt_mod.set_rule(set_name)
    except (scoring.ScoringError, fmt_mod.FormatError) as exc:
        raise ConfigError(f"{path}: [rules] {exc}") from None

    team_keys: dict[str, tuple[str, ...]] = {}
    config = Config(
        path=path,
        exists=True,
        club_name=str(club.get("name", "") or ""),
        strip_team_prefix=prefix,
        seasons_mode=mode,
        title=str(build.get("title", "") or ""),
        players_mode=players,
        scoring=scheme,
        sets=set_name,
        unplayed=_unplayed(rules.get("unplayed"), "[rules] unplayed", path),
        draws=_draws(rules.get("draws"), "[rules] draws", path),
        format=str(rules.get("format", "") or ""),
        slots=tuple(str(s) for s in rules.get("slots", ()) or ()),
        min_sets_for_result=_minimum_sets(rules.get("min_sets_for_result"),
                                          "[rules] min_sets_for_result", path),
        forfeit_award=_forfeit_award(rules.get("forfeit_award"),
                                     "[rules] forfeit_award", path),
        forfeit_points=_forfeit_points(rules.get("forfeit_points"),
                                       "[rules] forfeit_points", path),
        finals=_finals(rules.get("finals"), "[rules] finals", path),
        season_labels=_season_labels(raw.get("seasons"), mode, warnings),
        team_names=_name_map(raw.get("team_names"), "team_names", prefix, warnings,
                             team_keys),
        team_name_keys=team_keys,
        player_names=_name_map(raw.get("player_names"), "player_names", None,
                               warnings),
        aliases=_alias_map(raw.get("aliases"), warnings),
        namesakes=_namesakes(raw.get("namesakes"), prefix, warnings),
        competitions=_competitions(raw.get("competitions"), path, prefix, warnings,
                                   multi=mode == "multi"),
        warnings=tuple(warnings),
    )
    config = _priced_forfeits(config, path)
    # Validate the declared format now, for the same reason.
    try:
        config.declared_format()
    except fmt_mod.FormatError as exc:
        raise ConfigError(f"{path}: [rules] {exc}") from None
    return config


def _players_mode(raw: dict, path: Path) -> str:
    """``[build] players``, or the refusal of a players line this program cannot honour.

    The one setting in this file that is refused rather than warned about when it is
    wrong, and on purpose, which departs from how a misplaced ``seasons`` is
    treated. Every other mistake here costs a wrong label; a ``players`` line that
    is ignored publishes the names it was written to withhold, and a weekly script
    does not read warnings. So three mistakes stop the build, in one function:

    * a value of ``[build] players`` that is not one of :data:`PLAYER_MODES`;
    * a key in ``[build]`` that is one slip from ``players``;
    * a players line anywhere but ``[build]``, at any depth -- the top of the file,
      ``[rules]``, a competition's own section, a misspelt ``[biuld]`` -- that
      asks for a mode, one slip from a mode, or any other text, even beside
      ``[build] players = "names"``, because a club withholding one division is a
      club whose other divisions a build would publish.

    The last two only while ``[build] players`` prints names, and never for a line
    that asks for names itself: ignored, neither publishes a name somebody asked to
    withhold, so each is left to the warning it already gets. So is a line outside
    ``[build]`` whose value is not text, such as ``[rules] players = 3``, and a key
    in a table of the club's own names whose value is no mode, such as a team
    called the Players.
    """
    build = raw.get("build")
    build = build if isinstance(build, dict) else {}
    mode = _build_players(build, path)
    if mode != "names":
        return mode
    for where, key, value in _players_lines(raw, ()):
        in_build = where == ("build",)
        if in_build and key == "players" or _read_here(where, key):
            continue
        meant = _meant(value)
        if meant == "names":
            continue
        if in_build:
            raise ConfigError(
                f"{path}: [build] {key} is not a setting this program understands. "
                f"Did you mean players? Nothing was built: a misspelt players line "
                f"would publish the names it was written to withhold.")
        if not isinstance(value, str) or (meant is None and _names_the_keys(where)):
            continue
        # The table as the file writes it, so the club can search for the line.
        name = ".".join(toml_key(part) for part in where if part != "[]")
        place = ("at the top of the file" if not where
                 else f"under [[{name}]]" if where[-1] == "[]"
                 else f"under [{name}]")
        if value.strip().lower() == meant:
            raise ConfigError(
                f"{path}: {toml_key(key)} = {toml_value(meant)} is written {place}, "
                f"where it sets nothing. It is a [build] setting, for every dashboard "
                f"at once: "
                f"write players = {toml_value(meant)} under [build]. Nothing was "
                f"built, because building would publish the names this line was "
                f"written to withhold.")
        said = toml_value(value)
        raise ConfigError(
            f"{path}: {toml_key(key)} = {said} is written {place}, where it sets "
            f"nothing, and {said} is not {choices(PLAYER_MODES)}. It is a [build] "
            f"setting, for every dashboard at once."
            + (f" Did you mean players = {toml_value(meant)} under [build]?"
               if meant else "")
            + " Nothing was built: a players line this program cannot read is not "
              "guessed at, and ignoring it would publish the names it may have been "
              "written to withhold.")
    return mode


def _build_players(build: dict, path: Path) -> str:
    """``[build] players`` as a mode: names when it is missing, refused when unread."""
    if "players" not in build:
        return "names"
    value = build["players"]
    mode = str(value).strip().lower()
    if isinstance(value, str) and mode in PLAYER_MODES:
        return mode
    raise ConfigError(
        f"{path}: [build] players = {toml_value(value)} is not "
        f"{choices(PLAYER_MODES)}.{_did_you_mean(mode, PLAYER_MODES)} Nothing "
        f"was built: this line decides whether players' names are published, so a "
        f"value this program cannot read is not guessed at.")


def _meant(value: object) -> str | None:
    """The mode a players line asks for, as written or one slip from it, if any."""
    if not isinstance(value, str):
        return None
    said = value.strip().lower()
    if said in PLAYER_MODES:
        return said
    near = near_misses(said, PLAYER_MODES)
    return near[0] if near else None


# The tables whose keys are the club's own names, a team's or a player's or a
# season's, and a competition's tables of its teams. A key there that says player
# is somebody's name, and only a value that is a mode is read as a players line.
_NAMED_TABLES = ("seasons", "team_names", "player_names", "aliases", "namesakes")
_NAMED_TABLES_OF_A_COMPETITION = ("captains", "courts", "codes", "byes", "expected")

# The keys of one team's line in [competitions.<id>.expected], the sheet as typed.
EXPECTED_KEYS = ("points", "played")


def _read_here(where: tuple[str, ...], key: str) -> bool:
    """Whether *key* is a setting this program reads in the table at *where*.

    A team's ``played`` in the sheet table is two slips from ``players``, and is the
    matches the sheet says that team has played: no players line.
    """
    return (len(where) == 4 and where[0] == "competitions" and where[2] == "expected"
            and key in EXPECTED_KEYS)


def _names_the_keys(where: tuple[str, ...]) -> bool:
    """Whether the table at *where* is keyed by the club's names and not settings."""
    if where[:1] and where[0] in _NAMED_TABLES:
        return True
    return (len(where) > 2 and where[0] == "competitions"
            and where[2] in _NAMED_TABLES_OF_A_COMPETITION)


def _players_lines(table: dict, where: tuple[str, ...]):
    """Every ``(table, key, value)`` whose key reads as ``players``, at any depth."""
    for key, value in table.items():
        if isinstance(value, dict):
            yield from _players_lines(value, where + (key,))
        elif isinstance(value, list) and value and all(
                isinstance(item, dict) for item in value):
            for item in value:
                yield from _players_lines(item, where + (key, "[]"))
        elif _means_players(key):
            yield where, key, value


def _means_players(key: str) -> bool:
    """Whether *key* is an attempt at ``players``: it says player, or is a slip."""
    folded = key.casefold()
    return "player" in folded or bool(near_misses(folded, ["players"]))


def _table(raw: dict, key: str, warnings: list[str]) -> dict:
    value = raw.get(key, {})
    if not isinstance(value, dict):
        warnings.append(f"[{key}] should be a section, not a "
                        f"{type(value).__name__}; ignored")
        return {}
    return value


def _reject_unknown(table: dict, known: set[str], name: str,
                    warnings: list[str]) -> None:
    for key in sorted(set(table) - known):
        warnings.append(f"[{name}] {key} is not a setting this program "
                        f"understands and was ignored."
                        f"{_did_you_mean(key, known)}")


def _did_you_mean(key: str, known: object) -> str:
    """`` Did you mean club?`` -- or ``""`` when nothing in ``known`` is close.

    The nearest of ``known`` by :func:`~matchcentre.slugs.nearest`, asked about by
    :func:`~matchcentre.slugs.did_you_mean`, as a misspelt team or player is: an
    ignored key is already named on every run, and what the club still has to do
    is find the one they meant. ``known`` is whichever set of names was legal
    *where the key was written* -- the section names for a section, that section's
    own settings for a setting -- so a `[rules]` typo is never answered with a
    section name. The candidates are the loader's own for that reason, and not the
    team rule's: a setting has no sibling, a side of a club told apart by a tag.

    Suggested, never applied, and only within two edits. Reading ``naem`` as ``name``
    would mean a config taking effect in a spelling nobody wrote; guessing further
    out than two edits would send a reader to a setting that does something else,
    which costs them more than the silence did.
    """
    return did_you_mean(nearest(key, sorted(known)))


def _agreed(values: Iterable[object]) -> object | None:
    """The value every one of these shares, or None if they differ or are none."""
    found = set(values)
    return found.pop() if len(found) == 1 else None


def _minimum_sets(value: object, label: str, path: Path) -> int | None:
    """``min_sets_for_result``, refused below 1 where it is typed.

    0 was read as leaving the line out, because the number was resolved with
    ``or``, and a number declared now answers the review list's question about it.
    """
    number = _optional_int(value, label, path)
    if number is not None and number < 1:
        raise ConfigError(f"{path}: {label} must be 1 or more, got {number}: it is "
                          "how many of a match's sets must finish before it has a "
                          "result, and a result needs at least one")
    return number


def _optional_int(value: object, label: str, path: Path) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ConfigError(f"{path}: {label} must be a whole number, "
                          f"got {value!r}") from None


def _unplayed(value: object, label: str, path: Path) -> str:
    """``[rules] unplayed``: ``"average"``, ``"zero"``, or unset.

    Rejected rather than warned about, unlike a misspelt section name. A typo here
    would fall back to the preset's own convention, which is a points column that
    is quietly not the one the club asked for — and the club asked precisely
    because it had noticed the difference.
    """
    if value is None or value == "":
        return ""
    mode = str(value).strip().casefold()
    if mode not in scoring.UNPLAYED_MODES:
        raise ConfigError(
            f"{path}: {label} = {toml_value(value)} is not "
            f"{choices(scoring.UNPLAYED_MODES)}."
            f"{_did_you_mean(mode, scoring.UNPLAYED_MODES)}")
    return mode


def _draws(value: object, label: str, path: Path) -> bool | None:
    """``[rules] draws`` and a round's ``finals``: a TOML boolean, or refused.

    TOML's own booleans and nothing else. ``"false"`` is a non-empty string and ``0``
    is a number, and both are spellings a club would reasonably read back as the
    setting working -- accepted loosely, ``draws = "false"`` would leave draws
    allowed, which is the one outcome the club was writing the line to prevent.

    Rejected rather than warned about, for the reason ``_unplayed`` is: falling back
    to the preset means every level card is scored as a draw in a competition that
    has none, and the ladder that comes out is wrong rather than incomplete.

    A round's ``finals`` is read here too, and for the same reason: read loosely,
    ``finals = "false"`` made a final of the round the club was keeping on the ladder.
    """
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ConfigError(f"{path}: {label} must be true or false, "
                          f"got {value!r}")
    return value


def _forfeit_award(value: object, label: str, path: Path) -> str:
    """``[rules] forfeit_award``: ``"as-played"``, ``"full-games"``, ``"fixed"``, or unset.

    Refused rather than warned about, for the reason ``_unplayed`` is: a typo that
    fell back to the preset would pay every forfeit of the season a price the club
    had written a line to change (4.420).
    """
    if value is None or value == "":
        return ""
    award = str(value).strip().casefold()
    if award not in scoring.FORFEIT_AWARDS:
        raise ConfigError(
            f"{path}: {label} = {toml_value(value)} is not "
            f"{choices(scoring.FORFEIT_AWARDS)}."
            f"{_did_you_mean(award, scoring.FORFEIT_AWARDS)}")
    return award


def _forfeit_points(value: object, label: str, path: Path) -> float | None:
    """``[rules] forfeit_points``: the flat price a ``"fixed"`` award pays, or unset.

    A TOML number, 0 or more, and nothing else. ``"5"`` and ``true`` are refused
    rather than read as 5 and 1, as ``draws`` refuses ``"false"``, and so are a
    negative number, ``nan`` and ``inf``, which no ladder can add up.
    """
    if value is None:
        return None
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 0):
        raise ConfigError(f"{path}: {label} must be a number of points, 0 or more, "
                          f"got {value!r}")
    return float(value)


def _finals(value: object, label: str, path: Path) -> str:
    """``[rules] finals``: ``"none"``, ``"placings"``, or unset (4.537).

    Refused rather than warned about, for the reason ``_unplayed`` is: a typo that
    fell back to no format would leave every placing match placing nobody, the
    season the club wrote the line for.
    """
    if value is None or value == "":
        return ""
    mode = str(value).strip().casefold()
    if mode not in scoring.FINALS_FORMATS:
        raise ConfigError(
            f"{path}: {label} = {toml_value(value)} is not "
            f"{choices(scoring.FINALS_FORMATS)}."
            f"{_did_you_mean(mode, scoring.FINALS_FORMATS)}")
    return mode


def _priced_forfeits(config: Config, path: Path) -> Config:
    """*config*, refused where a ``"fixed"`` award has no price, and warned where
    a ``forfeit_points`` is read by no ``"fixed"`` award.

    Asked of the config whole, because which points a ``"fixed"`` award reads is
    a question across sections: a competition's own, else [rules]'s. A ``"fixed"``
    with neither cannot be built into a scheme, so the club is told in its own
    file's words (4.420); points no award reads change nothing, so the run goes
    on, and is told the line did nothing, as a misplaced key is. Rejected: reading
    points as choosing ``"fixed"``, a setting taking effect in a word nobody wrote.
    """
    comps = list(config.competitions.values())
    sections: list[tuple[str, CompetitionConfig | None]] = [("[rules]", None)]
    sections += [(f"[competitions.{comp.id}]", comp) for comp in comps]
    fixed = {comp.id for comp in comps
             if config._declared_rule(comp, "forfeit_award") == "fixed"}
    for where, comp in sections:
        declared = config.forfeit_award if comp is None else comp.forfeit_award
        if declared == "fixed" and config._forfeit_points(comp) is None:
            there = "" if comp is None else " there or under [rules]"
            raise ConfigError(
                f'{path}: {where} forfeit_award = "fixed" pays a flat '
                f"forfeit_points, and no forfeit_points is set{there}. Add one: "
                f"the points a match forfeited whole pays the side that turned up.")
    warnings = list(config.warnings)
    for where, comp in sections:
        points = config.forfeit_points if comp is None else comp.forfeit_points
        if points is None:
            continue
        # The award in force there: declared, or the preset's own.
        award = (config._declared_rule(comp, "forfeit_award")
                 or scoring.ruleset(config._declared_rule(comp, "scoring")
                                    or scoring.DEFAULT_RULESET).forfeit_award)
        if award == "fixed":
            continue
        # [rules]'s points are also read by a section whose own "fixed" sets none.
        if comp is None and any(c.forfeit_points is None for c in comps
                                if c.id in fixed):
            continue
        warnings.append(
            f"{where} forfeit_points = {toml_value(points)} was not read: it "
            f'prices a forfeit only where forfeit_award = "fixed", and '
            f'forfeit_award is "{award}" here.')
    return replace(config, warnings=tuple(warnings))


def _str_map(value: object, name: str, warnings: list[str]) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        warnings.append(f"[{name}] should be a section of key = \"value\" "
                        f"pairs; ignored")
        return {}
    return {str(k): str(v) for k, v in value.items()}


# Keys a club writes inside [seasons] when they are reaching for the mode. Read
# together with a value that is a mode word: a folder labelled "mode" is nobody's
# season, but a folder labelled "Modest Cup" is, and only one of the two is a
# setting written in the wrong place.
_MODE_KEYS = ("mode", "season", "seasons")


def mode_among_labels(key: str, label: str) -> bool:
    """Whether ``[seasons] key = label`` is the mode, written among the labels."""
    return (key.strip().lower() in _MODE_KEYS
            and label.strip().lower() in SEASON_MODES)


def _season_labels(value: object, mode: str, warnings: list[str]) -> dict[str, str]:
    """``[seasons]`` read as folder labels, naming the three ways it sets nothing.

    The mode -- one ladder per season or the whole archive on one -- is ``seasons``
    under ``[build]``. This section is the labels, and it is one word from the mode,
    so all three of these are a club asking for multi-season mode and getting
    silence: ``seasons = "multi"`` written at the top of the file, where it arrives
    here as a string instead of a table; ``mode = "multi"`` written inside this
    section, where it is a perfectly good label for a folder called ``mode``; and a
    table of correct labels while the mode is still ``single``, where there is no
    season for a label to name.

    Every one of them ends the same way, which is why they are worth three warnings:
    single-season mode files every card under ``data/`` into one competition, so the
    second season lands on the first season's fixtures.

    Nothing here is applied and nothing is dropped. A mode taken from an undocumented
    line would make the shape of a dashboard depend on a spelling, and a label table
    the program has silently edited is one a club cannot match to the warning about
    it, so the misplaced line stays where it was written and the warning names the
    line to add.
    """
    if isinstance(value, str) and value.strip().lower() in SEASON_MODES:
        warnings.append(
            f'seasons = "{value.strip().lower()}" is written where the [seasons] '
            f'table of labels goes, so it set nothing. The mode is a [build] '
            f'setting: write seasons = "{value.strip().lower()}" under [build].')
        return {}
    labels = _str_map(value, "seasons", warnings)
    misplaced = [key for key, label in labels.items()
                 if mode_among_labels(key, label)]
    for key in misplaced:
        warnings.append(
            f'[seasons] {key} = "{labels[key]}" was read as the label of a season '
            f'folder called {key!r}, not as the mode. Write '
            f'seasons = "{labels[key].strip().lower()}" under [build] instead.')
    if mode == "single" and set(labels) - set(misplaced):
        warnings.append(
            f"[seasons] labels {len(labels)} season"
            f"{'' if len(labels) == 1 else 's'}, but [build] seasons is \"single\", "
            f"so every card under data/ is one season and no label is used. Set "
            f"seasons = \"multi\" under [build] to give each season its own ladder.")
    return labels


def _name_map(value: object, name: str, prefix: str | None,
              warnings: list[str],
              keys: dict[str, tuple[str, ...]] | None = None) -> dict[str, str]:
    """Display-name overrides, keyed by slug.

    Forgiving about the key: discovery writes slugs, but a club member editing by
    hand will reasonably type the name as it appears on the card, so both are
    registered. ``keys``, where given, is told each key as typed and the slugs it
    was registered under.
    """
    plain = _str_map(value, name, warnings)
    out: dict[str, str] = {}
    for key, display in plain.items():
        try:
            filed = (slugify(key),)
            out[filed[0]] = display
            if prefix:
                filed += (team_slug(key, strip_prefix=prefix),)
                out[filed[1]] = display
        except ValueError:
            warnings.append(f"[{name}] key {key!r} has no letters or digits "
                            f"in it and was ignored")
            continue
        if keys is not None:
            keys[key] = filed
    return out


def _alias_map(value: object, warnings: list[str]) -> dict[str, str]:
    plain = _str_map(value, "aliases", warnings)
    out: dict[str, str] = {}
    for key, target in plain.items():
        try:
            out[slugify(key)] = slugify(target)
        except ValueError:
            warnings.append(f"[aliases] {key!r} = {target!r} is not a usable "
                            f"pair and was ignored")
    return out


def _namesakes(value: object, prefix: str,
               warnings: list[str]) -> dict[str, tuple[str, ...]]:
    """``[namesakes]``: one printed name, one person per team named against it.

    Forgiving about both sides for the same reason ``_name_map`` is: the report
    prints a line to paste, which carries slugs, while somebody editing by hand
    will type the name and the teams as the card spells them.

    Fewer than two teams is warned about rather than accepted. It would split
    nothing -- a single team's roster row is already a player of its own -- so a
    club that wrote one has either mistyped a team or misread what the setting
    does, and either way silence would leave them believing it had taken effect.
    """
    if value is None:
        return {}
    if not isinstance(value, dict):
        warnings.append('[namesakes] should be a section of "name" = ["Team A", '
                        '"Team B"] pairs; ignored')
        return {}
    out: dict[str, tuple[str, ...]] = {}
    for key, listed in value.items():
        if isinstance(listed, str) or not isinstance(listed, (list, tuple)):
            warnings.append(f"[namesakes] {key!r} should be a list of the teams "
                            f"whose players share that name; ignored")
            continue
        try:
            slug = slugify(str(key))
        except ValueError:
            warnings.append(f"[namesakes] key {key!r} has no letters or digits in "
                            f"it and was ignored")
            continue
        teams: list[str] = []
        for name in listed:
            try:
                team = team_slug(str(name), strip_prefix=prefix)
            except ValueError:
                warnings.append(f"[namesakes] {key!r} names a team with no letters "
                                f"or digits in it ({name!r}); it was ignored")
                continue
            if team not in teams:
                teams.append(team)
        if len(teams) < 2:
            warnings.append(f"[namesakes] {key!r} needs at least two teams to "
                            f"separate; it names {len(teams)} and was ignored")
            continue
        out[slug] = tuple(teams)
    return out


def _competitions(value: object, path: Path, prefix: str,
                  warnings: list[str], *,
                  multi: bool) -> dict[str, CompetitionConfig]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"{path}: [competitions] should be a section")
    # Its own settings, plus every [rules] setting, because a competition may
    # override any of them. Unioned rather than re-typed: a rule added to
    # RULES_SETTINGS and not here would be honoured at the top of the file and
    # warned about one section down, which is the worst of both answers.
    known = {"label", "day", "division", "season", "season_key", "rounds_total",
             "teams", "captains", "courts", "codes", "rounds",
             "byes", "washouts", "expected"} | set(RULES_SETTINGS)
    out: dict[str, CompetitionConfig] = {}
    for comp_id, table in value.items():
        if not isinstance(table, dict):
            warnings.append(f"[competitions.{comp_id}] should be a section; ignored")
            continue
        _reject_unknown(table, known, f"competitions.{comp_id}", warnings)
        teams = tuple(str(t) for t in table.get("teams", ()) or ())
        if len(set(teams)) != len(teams):
            raise ConfigError(
                f"{path}: [competitions.{comp_id}] teams lists a team twice")
        out[str(comp_id)] = CompetitionConfig(
            id=str(comp_id),
            label=str(table.get("label", "") or ""),
            day=str(table.get("day", "") or ""),
            division=str(table.get("division", "") or ""),
            season=str(table.get("season", "") or ""),
            season_key=_season_key(table.get("season_key")),
            rounds_total=_optional_int(table.get("rounds_total"),
                                       f"[competitions.{comp_id}] rounds_total",
                                       path),
            teams=teams,
            captains=_str_map(table.get("captains"),
                             f"competitions.{comp_id}.captains", warnings),
            courts=_int_map(table.get("courts"),
                            f"competitions.{comp_id}.courts", path),
            codes=_int_map(table.get("codes"),
                           f"competitions.{comp_id}.codes", path),
            rounds=_rounds(table.get("rounds"), comp_id, path, warnings),
            byes=_byes_table(table.get("byes"), comp_id, teams, path),
            washouts=_washouts_table(table.get("washouts"), comp_id, teams, path),
            scoring=str(table.get("scoring", "") or ""),
            sets=str(table.get("sets", "") or ""),
            unplayed=_unplayed(table.get("unplayed"),
                               f"[competitions.{comp_id}] unplayed", path),
            draws=_draws(table.get("draws"),
                         f"[competitions.{comp_id}] draws", path),
            format=str(table.get("format", "") or ""),
            slots=tuple(str(s) for s in table.get("slots", ()) or ()),
            min_sets_for_result=_minimum_sets(
                table.get("min_sets_for_result"),
                f"[competitions.{comp_id}] min_sets_for_result", path),
            forfeit_award=_forfeit_award(
                table.get("forfeit_award"),
                f"[competitions.{comp_id}] forfeit_award", path),
            forfeit_points=_forfeit_points(
                table.get("forfeit_points"),
                f"[competitions.{comp_id}] forfeit_points", path),
            finals=_finals(table.get("finals"),
                           f"[competitions.{comp_id}] finals", path),
            expected=(sheet := _expected_table(table.get("expected"), comp_id,
                                               warnings, refused=(lost := []))),
            expected_unusable=(_sheet_in_use(table.get("expected"))
                               and not (sheet and sheet.lines)),
            # A table it compares no line of is named whole, and not line by line.
            expected_refused=tuple(lost) if sheet and sheet.lines else (),
        )
        _refuse_rested_and_washed_out(out[str(comp_id)], path)
    _refuse_double_claims(out, path, multi=multi)
    return out


def _season_key(value: object) -> str:
    """A declared ``season_key``, slugged as the folder name it stands for.

    A card's key is its folder's name slugged -- ``data/2026-Winter/`` files its
    cards under ``2026-winter`` -- and a section's was compared with that as typed,
    so ``season_key = "2026-Winter"``, the folder's name exactly as the refusal for
    two seasons of one division asks for it, claimed no card. Slugged here, once, so
    the router, the double-claim check and the id all read the key the folder has.
    One with nothing in it to slug is kept as typed; it matches no folder either way.
    """
    text = str(value or "")
    return slugify_or_none(text) or text


def _refuse_double_claims(comps: dict[str, CompetitionConfig], path: Path, *,
                          multi: bool) -> None:
    """Two sections claiming one competition, refused before anything is built.

    Cards are routed on ``day`` and ``division`` (and, in multi-season mode,
    ``season_key``), so two sections filling those in identically both claim the same
    cards and only one of them can have them. The loser's team list, captains, byes,
    round dates and ``rounds_total`` then stop taking effect with nothing on the page to
    say so, which turns a closed roster back into an open one and an uncredited bye into
    a missing night. Neither section is more right than the other, so this is refused
    rather than resolved -- the same treatment as a team listed twice or a round declared
    twice, and for the same reason: the file contradicts itself and only its author can
    say which way.

    Sections that fill in only one of the two fields are skipped, because the router
    skips them too. They claim no cards at all, which the report already says.

    ``multi`` is the mode, and it changes one sentence. The advice for two seasons of
    one division is the mode and a season key each; a club already in multi-season
    mode has done the first half, and was being told to do it again -- in the file
    ``--discover`` wrote for it, before that wrote ``season_key``.
    """
    claimed: dict[tuple[str, str, str], str] = {}
    for comp in comps.values():
        if not (comp.day and comp.division):
            continue
        # Keyed exactly as the router matches: the day and division by label, so two
        # spellings of one night collide here as well, and the season key as loaded,
        # slugged as its folder is and then compared exactly, since looseness in that
        # comparison is what merged two seasons in the predecessor.
        key = (label_key(comp.day), label_key(comp.division), comp.season_key)
        first = claimed.setdefault(key, comp.id)
        if first != comp.id:
            season = (f" and season_key {comp.season_key!r}" if comp.season_key
                      else "")
            # Two fixes, because there are two ways to arrive here and they are not
            # each other's answer. Usually a section was renamed or copied and the old
            # one left behind, and the fix is to delete or merge it. But a club keeping
            # last season's section beside this one has two real competitions and
            # wants them apart, not merged -- so the season key is offered too, and
            # only when neither section has one, since a pair that already agrees on a
            # season key is not that case.
            if comp.season_key:
                apart = ""
            elif multi:
                apart = (" If they are two seasons of one division, give each "
                         "section its own season_key: the name of the folder under "
                         "data/ its cards are in. seasons is already \"multi\" "
                         "under [build], and in that mode the folder is what keeps "
                         "two seasons of one division apart.")
            else:
                apart = (" If they are two seasons of one division, set seasons = "
                         "\"multi\" under [build] and give each section its own "
                         "season_key.")
            raise ConfigError(
                f"{path}: [competitions.{first}] and [competitions.{comp.id}] both "
                f"claim day {comp.day!r} and division {comp.division!r}{season}. "
                f"Cards are matched on those fields, not on the section name, so "
                f"every card would go to one of the two and the other's settings "
                f"would quietly do nothing. Delete one, or merge them.{apart}")


def _int_map(value: object, name: str, path: Path) -> dict[str, int]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"{path}: [{name}] should be a section")
    out: dict[str, int] = {}
    for key, number in value.items():
        try:
            out[str(key)] = int(number)
        except (TypeError, ValueError):
            raise ConfigError(f"{path}: [{name}] {key} = {number!r} is not a "
                              f"whole number") from None
    return out


def _round_byes(entry: dict, comp_id: str, number: str,
                path: Path) -> tuple[str, ...]:
    """``bye`` or ``byes`` inside one ``[[...rounds]]`` block, either shape.

    Both spellings are read, because a club that has just typed ``fixtures = [...]``
    will reasonably reach for either, and an ignored key here silently loses a
    bye. Both at once is refused: two lists of resting teams in one round is a
    contradiction to resolve by asking, not by picking one.
    """
    label = f"[competitions.{comp_id}] round {number}"
    if "bye" in entry and "byes" in entry:
        raise ConfigError(f"{path}: {label} sets both bye and byes. "
                          f"They mean the same thing — keep one.")
    value = entry.get("byes", entry.get("bye"))
    if value is None or value == "":
        return ()
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        raise ConfigError(f"{path}: {label}: bye must be a team name or a list "
                          f"of team names, got {value!r}")
    names: list[str] = []
    for name in value:
        if not isinstance(name, str):
            raise ConfigError(f"{path}: {label} rests {name!r}, which is not a "
                              f"team name")
        text = name.strip()
        if text and text not in names:
            names.append(text)
    return tuple(names)


def _byes_table(value: object, comp_id: str, teams: tuple[str, ...],
                path: Path) -> dict[str, tuple[str, ...]]:
    """``[competitions.<id>.byes]``: one line per team, the rounds it sits out.

    ::

        [competitions.monday-division-b2.byes]
        "Riverside Rockets" = [1, 6]
        "Willowbank Falcons" = [4, 6]

    Written this way round because that is the shape of the sheet an association
    publishes — a grid of teams down the side and rounds across the top, with a
    ``B`` in the cells. Reading it off by team is one pass down a column; by round
    it is a pass across every row per line typed.

    A club only needs the rounds this program cannot see for itself. One team
    missing from a round it *has* cards for is already inferred, given a closed team
    list; what needs writing down is the week nobody played, or a round whose cards
    have not arrived yet.

    A team here that is not in a declared ``teams`` list stops the run. The whole
    value of closing the team list is that a name this program does not recognise
    becomes an error instead of a new team, and a bye is the one thing that pays a
    team points for playing nothing — a typo here would credit a team that does not
    exist and quietly leave the real one a match short.
    """
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"{path}: [competitions.{comp_id}.byes] should be a "
                          f"section of \"Team name\" = [rounds] lines")
    known = {slugify_or_none(name) for name in teams} - {None}
    out: dict[str, tuple[str, ...]] = {}
    for team, rounds in value.items():
        name = str(team)
        label = f"[competitions.{comp_id}.byes] {name!r}"
        if known and slugify_or_none(name) not in known:
            listed = in_words(sorted(teams))
            raise ConfigError(
                f"{path}: {label} is not one of this competition's teams. "
                f"A bye credits points for playing nothing, so an unrecognised "
                f"name here is refused rather than guessed at. Declared: {listed}")
        if isinstance(rounds, (str, int)) and not isinstance(rounds, bool):
            # One round is the commonest line in the table, and requiring `[6]`
            # for it is the sort of friction that ends in the table not being
            # filled in at all.
            rounds = [rounds]
        if not isinstance(rounds, (list, tuple)):
            raise ConfigError(
                f"{path}: {label} should be a list of round numbers, "
                f"like [1, 6], got {rounds!r}")
        numbers: list[str] = []
        for number in rounds:
            # `bool` is an `int` in Python, so it has to be turned away by name or
            # `Rockets = true` becomes a bye in a round called "True".
            if isinstance(number, bool) or not isinstance(number, (str, int)):
                raise ConfigError(
                    f"{path}: {label} lists {number!r}, which is not a round "
                    f"number. Rounds are written as in the draw: [1, 6] or "
                    f"[\"SF\"].")
            text = round_key(str(number))
            if text and text not in numbers:
                numbers.append(text)
        if numbers:
            out[name] = tuple(numbers)
    return out


def _washouts_table(value: object, comp_id: str, teams: tuple[str, ...],
                    path: Path) -> dict[str, tuple[tuple[str, str], ...]]:
    """``[competitions.<id>.washouts]``: the rounds, or fixtures of them, rained off.

    ::

        [competitions.tuesday-division-a1.washouts]
        9 = true
        7 = [["Hilltop Aces", "Riverside Rockets"]]

    Keyed by round, because a washout is a night rather than a team's: ``true`` is
    the whole round, the week a club has no card of at all, and a list of
    ``[home, away]`` pairs the fixtures of it the rain stopped while the rest were
    played. One fixture may leave off its outer brackets, as one round in the byes
    table may. ``false`` and an empty list say nothing was, so a club can turn a
    line off without deleting it (4.535).

    A team here that is not in a declared ``teams`` list stops the run, as it does
    in the byes table and for the same reason: under ``unplayed = "average"`` a
    washout credits each of its teams, and a typo would credit nobody while the
    team meant stayed a round short. With the list open it is taken on trust, and
    the run reports a name no card has (``washout-team``).
    """
    if value is None:
        return {}
    where = f"[competitions.{comp_id}.washouts]"
    if not isinstance(value, dict):
        raise ConfigError(f"{path}: {where} should be a section of round = true "
                          f"or round = [[\"Home\", \"Away\"]] lines")
    known = {slugify_or_none(name) for name in teams} - {None}
    out: dict[str, tuple[tuple[str, str], ...]] = {}
    lines: set[str] = set()
    for key, entry in value.items():
        number = round_key(str(key))
        if number in lines:
            # `9` and `" 9"` are one round, its spaces no part of a label, and the
            # second line replaced the first with nothing said.
            raise ConfigError(f"{path}: {where} has round {number} on two lines. "
                              f"Keep the one that is true.")
        lines.add(number)
        if not number:
            # A round of no label is a round no card can be filed under, and on
            # the page a heading of nothing, as an unnumbered draw block would be.
            raise ConfigError(f"{path}: {where} has a line with no round before "
                              f"its =. Write the round as the cards print it, as "
                              f"9 = true.")
        label = f"{where} round {number}"
        shape = (f"Write {toml_key(number)} = true for the whole round, or "
                 f"{toml_key(number)} = [[\"Home\", \"Away\"]] for the fixtures "
                 f"of it that were washed out.")
        if isinstance(entry, bool):
            if entry:
                out[number] = ()
            continue
        if not isinstance(entry, list):
            raise ConfigError(f"{path}: {label} should be true or a list of "
                              f"fixtures, got {entry!r}. {shape}")
        if not entry:
            continue
        if all(isinstance(item, str) for item in entry):
            # One fixture, its outer brackets left off: `9 = ["Home", "Away"]`.
            pairs = [entry]
        elif all(isinstance(item, list) for item in entry):
            pairs = entry
        else:
            raise ConfigError(f"{path}: {label} mixes team names and fixtures, "
                              f"got {entry!r}. {shape}")
        fixtures: list[tuple[str, str]] = []
        seen: dict[str, str] = {}
        for pair in pairs:
            if len(pair) != 2 or not all(isinstance(name, str) for name in pair):
                raise ConfigError(f"{path}: {label}: a fixture is two team names, "
                                  f"home then away, got {pair!r}. {shape}")
            home, away = (name.strip() for name in pair)
            slugs = []
            for name in (home, away):
                slug = slugify_or_none(name)
                if slug is None:
                    raise ConfigError(f"{path}: {label} names {name!r}, which is "
                                      f"not a team name. {shape}")
                if known and slug not in known:
                    raise ConfigError(
                        f"{path}: {label} names {name!r}, which is not one of this "
                        f"competition's teams. A washout credits a team under "
                        f"unplayed = \"average\", so an unrecognised name here is "
                        f"refused rather than guessed at. Declared: "
                        f"{in_words(sorted(teams))}")
                slugs.append(slug)
            if slugs[0] == slugs[1]:
                raise ConfigError(f"{path}: {label} has {home} playing itself. "
                                  f"{shape}")
            for slug, name in zip(slugs, (home, away)):
                if slug in seen:
                    raise ConfigError(f"{path}: {label} has {name} in two fixtures. "
                                      f"A team plays once a night: keep the fixture "
                                      f"that was washed out.")
                seen[slug] = name
            fixtures.append((home, away))
        out[number] = tuple(fixtures)
    return out


def _refuse_rested_and_washed_out(comp: CompetitionConfig, path: Path) -> None:
    """A team its round rests and has in a fixture washed out, refused (4.535).

    Two of the club's own lines answering what one team did that night, the byes
    table or the round's ``bye`` and the washouts table, and only its author can
    say which is true; the ladder would credit the night either way, so nothing
    else would ever mention it. A whole round washed out over a team resting is no
    contradiction: in a five-team grade somebody rests every week, rain or not.
    """
    rests: dict[str, list[str]] = {number: list(names)
                                   for number, names in comp.byes_by_round().items()}
    for rnd in comp.rounds:
        rests.setdefault(rnd.number, []).extend(rnd.byes)
    for number, fixtures in comp.washouts.items():
        resting = {slugify_or_none(name) for name in rests.get(number, ())}
        for home, away in fixtures:
            for name in (home, away):
                if slugify_or_none(name) in resting:
                    raise ConfigError(
                        f"{path}: [competitions.{comp.id}] round {number} rests "
                        f"{name} and has {home} v {away} washed out. Those are two "
                        f"answers to what {name} did that night: keep the line "
                        f"that is true.")


# What a figure in the sheet table was typed as when it is not one: kept apart from
# ``None``, which is a figure the club left blank and is owed no warning about.
_INVALID = object()
_SHEET_DECIMAL = re.compile(r"[0-9]+(\.[0-9]+)?")
_SHEET_WHOLE = re.compile(r"[0-9]+")


def _expected_table(value: object, comp_id: str, warnings: list[str], *,
                    refused: list[str] | None = None) -> ExpectedSheet | None:
    """``[competitions.<id>.expected]``: the association's sheet, typed in.

    ::

        [competitions.tuesday-division-b.expected]
        after_round = 3
        "Hilltop Aces" = { points = 10.9, played = 3 }

    Every mistake in it is a warning and costs only the part it is in, because the
    table credits nothing: a typo here can make the check say less, and can never
    change a figure. Which is the opposite of the byes table beside it, where a
    typo pays a team points and is refused.

    The table is *in use* once any team line carries anything typed, and only then
    is a missing ``after_round`` worth a warning. A table with nothing in it is a
    table nobody has filled in yet. ``after_round`` is a whole number or nothing:
    finals are not on the ladder (F11), so a sheet "after SF" is the ladder after
    the last regular round, and that round's number is the one to write.

    Team keys are kept as typed. A derived team list exists only once the cards
    are read, so that is where a key is matched to a team. The key of each line
    that had something refused goes on *refused*, once, for ``--force`` to name:
    the file is written from what was read, and a refused figure was read as none.
    """
    if value is None:
        return None
    where = f"[competitions.{comp_id}.expected]"
    refused = [] if refused is None else refused
    if not isinstance(value, dict):
        warnings.append(f'{where} should be a section of "Team name" = '
                        f'{{ points = …, played = … }} lines, so it was not '
                        f'compared.')
        return None
    entries = [(str(key), entry) for key, entry in value.items()
               if key != "after_round"]
    if not _sheet_in_use(value):
        return None
    raw_round = value.get("after_round", "")
    number = _expected_round(raw_round)
    if number is None:
        warnings.append(f"{where} has no after_round, so it was not compared. Add "
                        f"the last round your association's sheet covers — "
                        f"after_round = 5 — so the check stays right when later "
                        f"cards arrive.")
    elif number is _INVALID:
        warnings.append(f"{where} after_round = "
                        f"{echoed(raw_round, quoted=True)} is not a round "
                        f"number, so the table was not compared. Write the last "
                        f"regular round the sheet covers as a whole number, "
                        f"after_round = 5: finals are not on a ladder.")
    lines: list[ExpectedLine] = []
    for key, entry in entries:
        if not isinstance(entry, dict):
            warnings.append(f"{where} {echoed(key, quoted=True)} = "
                            f"{echoed(entry, quoted=True)} does not say which "
                            f"figure it is, so that team was not compared. Write "
                            f"it as {toml_key(key)} = {{ points = 10.9, played = "
                            f"3 }}, with the sheet's own figures.")
            refused.append(key)
            continue
        _reject_unknown(entry, set(EXPECTED_KEYS),
                        f"competitions.{comp_id}.expected.{toml_key(key)}", warnings)
        points = _expected_points(entry.get("points", ""))
        if points is _INVALID:
            warnings.append(f"{where} {echoed(key, quoted=True)} points = "
                            f"{echoed(entry['points'], quoted=True)} is not "
                            f"a number, so its points were not compared. Type it "
                            f"as the sheet prints it: points = 10.9.")
            points = None
            refused.append(key)
        played = _expected_played(entry.get("played", ""))
        if played is _INVALID:
            warnings.append(f"{where} {echoed(key, quoted=True)} played = "
                            f"{echoed(entry['played'], quoted=True)} is not "
                            f"a whole number of matches, so its matches played "
                            f"were not compared.")
            played = None
            if key not in refused:
                refused.append(key)
        if points is None and played is None:
            continue
        lines.append(ExpectedLine(key=key, points=points or "", played=played))
    if not isinstance(number, str):
        return None
    return ExpectedSheet(after_round=number, lines=tuple(lines))


def _sheet_in_use(value: object) -> bool:
    """Whether a sheet table has a team line with anything typed in it.

    The one rule for a table nobody has filled in yet, which is no table and no
    warning, asked by the loader and by what ``--discover --force`` says it drops.
    """
    if value is None:
        return False
    if not isinstance(value, dict):
        return True
    return any(_sheet_typed(entry) for key, entry in value.items()
               if key != "after_round")


def _sheet_typed(entry: object) -> bool:
    """Whether a sheet line has anything in it: a blank figure is not typed."""
    if not isinstance(entry, dict):
        return True
    return any(not (isinstance(v, str) and not v.strip()) for v in entry.values())


def _expected_round(value: object) -> object:
    """``after_round`` as a whole number in text, ``None`` blank, or ``_INVALID``."""
    if isinstance(value, bool):
        return _INVALID     # an ``int`` in Python, and not a round
    if isinstance(value, int):
        return str(value) if value >= 1 else _INVALID
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            if _SHEET_WHOLE.fullmatch(text) and int(text) >= 1:
                return str(int(text))
        except ValueError:      # digits past int()'s 4300-digit limit
            pass
    return _INVALID


def _expected_points(value: object) -> object:
    """Points as the decimal text typed, ``None`` blank, or ``_INVALID``.

    A float is kept as ``repr`` gives it back, which is the shortest text that is
    that float: ``12.0`` stays ``"12.0"``, a claim to one place. NaN, infinity and a
    negative fail the same pattern a quoted figure has to match.
    """
    # Belt and braces: ``str(True)`` is ``"True"``, which the pattern below refuses
    # anyway. Kept so that the table's three readers open alike: in the other two
    # a bool is an ``int`` that would otherwise pass.
    if isinstance(value, bool):
        return _INVALID
    if isinstance(value, int):
        text = str(value)
    elif isinstance(value, float):
        text = repr(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
    else:
        return _INVALID
    return text if _SHEET_DECIMAL.fullmatch(text) else _INVALID


def _expected_played(value: object) -> object:
    """Matches played as a whole number, ``None`` blank, or ``_INVALID``."""
    if isinstance(value, bool):
        return _INVALID
    if isinstance(value, int):
        return value if value >= 0 else _INVALID
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if _SHEET_WHOLE.fullmatch(text):
            try:
                return int(text)
            except ValueError:  # digits past int()'s 4300-digit limit
                return _INVALID
    return _INVALID


def slugify_or_none(name: str) -> str | None:
    """``slugify``, with an unsluggable name as ``None`` instead of an exception.

    A team name with no letters or digits in it cannot match anything, and here
    that is a comparison that fails rather than an error in its own right: the
    caller has a better message for it.
    """
    try:
        return slugify(name)
    except ValueError:
        return None


def _rounds(value: object, comp_id: str, path: Path,
            warnings: list[str]) -> tuple[RoundConfig, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ConfigError(
            f"{path}: [[competitions.{comp_id}.rounds]] should be a list of rounds")
    out: list[RoundConfig] = []
    for index, entry in enumerate(value, start=1):
        if not isinstance(entry, dict):
            warnings.append(f"[[competitions.{comp_id}.rounds]] entry {index} "
                            f"is not a table; ignored")
            continue
        _reject_unknown(entry, {"number", "date", "fixtures", "bye", "byes",
                                "finals", "note"},
                        f"competitions.{comp_id}.rounds", warnings)
        number = round_key(str(entry.get("number", "") or ""))
        if not number:
            raise ConfigError(f"{path}: [[competitions.{comp_id}.rounds]] entry "
                              f"{index} has no number")
        fixtures: list[tuple[str, str]] = []
        for pair in entry.get("fixtures", ()) or ():
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                raise ConfigError(
                    f"{path}: [competitions.{comp_id}] round {number}: each "
                    f"fixture must be a pair like [\"Home\", \"Away\"], "
                    f"got {pair!r}")
            fixtures.append((str(pair[0]), str(pair[1])))
        out.append(RoundConfig(
            number=number,
            # Stripped as the number is, so a date of spaces is no date to every
            # reader alike: the front page's card, the dashboard's draw, and a
            # round's fallback to its cards' own date.
            date=str(entry.get("date", "") or "").strip(),
            fixtures=tuple(fixtures),
            byes=_round_byes(entry, comp_id, number, path),
            finals=_draws(entry.get("finals"),
                          f"[competitions.{comp_id}] round {number}: finals", path),
            note=str(entry.get("note", "") or ""),
        ))
    numbers = [r.number for r in out]
    if len(set(numbers)) != len(numbers):
        raise ConfigError(f"{path}: [competitions.{comp_id}] declares the same "
                          f"round number twice")
    return tuple(out)


# --- writing ----------------------------------------------------------------
# Only the TOML this schema needs. Deliberately not a general emitter: a small,
# readable writer that puts a comment next to everything a human should check is
# worth more here than full syntax coverage.

WIDTH = 78
"""How wide the file this module writes is allowed to be.

The same number as ``build.WIDTH``, and it has to be: the review section prints
:func:`toml_lines` output at the report's width, and the same team list wrapped to
two different numbers breaks in two places in the two places a club reads it.

Two constants rather than one import, because a package module cannot import the
script that imports it. A test in ``tests/test_foundation.py`` holds them equal,
which is the honest way round -- the alternative was this module reaching for
``build`` at runtime and failing in every context except a build.
"""

# The row of dashes a proposal's header ends with, and the file under it begins.
_PROPOSAL_RULE = "# " + "-" * (WIDTH - 2)


def _option_lines(name: str, detail: str, *, pad: int) -> list[str]:
    """One row of an option table: ``#   name    what it does``, wrapped.

    Wrapped under the description rather than back at the margin, so that the column
    of names stays a column a club can run its eye down. Three of these tables are the
    only place the file says what a preset *does*, and the ``scoring`` one describes the
    setting the report reprints on every run because getting it wrong reorders a ladder
    -- so the sentences are folded and never shortened.
    """
    opening = f"#   {name:<{pad}} "
    return textwrap.wrap(detail, width=WIDTH, initial_indent=opening,
                         subsequent_indent="#" + " " * (len(opening) - 1),
                         break_long_words=False, break_on_hyphens=False)


def _chosen_rule_lines(config: Config, comps: list[Competition], key: str,
                       default: str) -> list[str]:
    """``[rules] scoring`` or ``sets``: live as the club chose it, else behind a #.

    No card says either, so a line written live for a club that chose neither
    read as its choice, and silenced the note that asks a club to check them.
    Where every competition sets its own, those the cards built and those only
    declared, a [rules] line reaches none of them and the default is in force for
    none, so none is offered: "in force until you choose" over it was untrue.
    """
    declared = getattr(config, key)
    if declared:
        return [f"{key} = {toml_value(declared)}"]
    if comps and not config._inheriting(comps, key) and all(
            getattr(section, key) for section in config.competitions.values()):
        return _comment_flow(f"{key} is set in each [competitions] section below, "
                             "because each of them sets its own.")
    return [*_comment_flow("Commented out, this is the program's default, in force "
                           "until you choose. Remove the # once it is your "
                           "competition's."),
            f"# {key} = {toml_value(default)}"]


def _comment_flow(text: str, *, indent: str = "# ") -> list[str]:
    """A paragraph of prose as comment lines, none of them over the width."""
    return textwrap.wrap(text, width=WIDTH, initial_indent=indent,
                         subsequent_indent=indent, break_long_words=False,
                         break_on_hyphens=False)


def commented_teams(names: list[str]) -> tuple[list[str], str]:
    """The commented ``teams`` array, and the words for deleting its ``#``.

    The two come back together because they have to agree, and they are told to a club
    in two different places: this file writes the array with the instruction above it,
    and the run's own report tells a club to go and do it. Both said "remove the #"
    while the array was one line. Wrapped to the width -- four commented lines for four
    ordinary team names -- one ``#`` out of four leaves ``teams = [`` on its own and a
    file that will not load, so a club doing exactly what it was told gets a parse
    error naming a line this program wrote.

    Which is why :func:`teams_instruction`, which words the report's note, asks this
    for the sentence, and for the array where the file is this run's to write: a
    second copy of the arithmetic behind it is how the two would drift back apart.
    """
    block = toml_lines(f"teams = {toml_value(list(names))}", width=WIDTH, indent="# ")
    return block, ("remove the # from every line of" if len(block) > 1
                   else "remove the # from")


def team_list_lines(comp_id: str, names: Iterable[str]) -> list[str]:
    """The TOML that declares a competition's team list, as the review offers it.

    One place, so that the note that sends a club to the list and the review that
    prints it ask the file about the same lines.
    """
    return [f"[competitions.{toml_key(comp_id)}]",
            f"teams = {toml_value(list(names))}"]


def listed_teams(comp: "Competition",
                 declared: CompetitionConfig | None) -> list[str]:
    """The team list the writer writes for *comp*, which the list that adds the
    teams the cards name besides is built on, in the file and in the proposal's
    summary alike: the club's ``teams``, as it spelt and ordered them, and
    otherwise the competition's teams, which a draw the club declared closes and
    the cards otherwise name. Keyed on ``teams`` alone, the summary missed a team
    the cards name that a declared draw lacks."""
    if declared and declared.teams_declared:
        return list(declared.teams)
    return [comp.teams[slug].name for slug in sorted(comp.teams)]


def named(path: Path | str) -> str:
    """The config file as a published sentence names it: its name, not its folders.

    A finding that sends a club to a line of the config is written into a season's
    document, and the front page says which file declared a competition with no
    cards, so both name the file the build read through this, and so does the
    refusal of a player's name, which gives every name by its shape. The name and
    not the path, because a path typed with ``--config`` holds the club's own
    folders. The report's own notes give the file as ``--config`` gave it.
    """
    return Path(path).name


def teams_instruction(comp_id: str, names: list[str], path: Path | str,
                      text: str | None) -> str:
    """What a club does to the file at *path* to declare the team list *names*.

    Worded from :func:`held_in` of the lines the review offers, in the config
    *text*: the list is behind a ``#``, live, split between headers, or not in the
    file at all, and its table live or not (:func:`opens`). *text* is None where
    this run writes the file, which is asked of the lines it writes, the table and
    :func:`commented_teams`' array under it. The step and the file, as the sentence
    around it gives the rest. The file is *path* as given, as ``--config`` gave it,
    which is how every other note of the report names it: the note is the
    report's, and never published (:func:`named`).
    """
    lines = team_list_lines(comp_id, names)
    table = lines[0]
    block, uncomment = commented_teams(names)
    if text is None:
        text = "\n".join([table, *block]) + "\n"
    held = held_in(lines, text)
    if held == "commented" and opens(table, text):
        # Behind a # under a header behind one too: the list's lines alone, with
        # no # in front, land in whatever table is above them.
        return (f"remove the # from {table} in {path}, and "
                f"{uncomment.removeprefix('remove the # ')} teams = [...] under it")
    if held == "commented":
        return f"{uncomment} teams = [...] under {table} in {path}"
    if held == "split":
        return (f"gather teams = [...] under one # {table} in {path}, as a file "
                f"declares a table once, and {uncomment} it")
    if held == "live":
        return (f"fill in teams under {table} in {path}: the line is there, and "
                f"lists no team")
    if opens(table, text):
        return f"add {table} to {path}, and teams = [...] under it"
    return f"add teams = [...] under {table} in {path}"


def toml_string(value: str) -> str:
    escaped = (value.replace("\\", "\\\\").replace('"', '\\"')
               .replace("\n", "\\n").replace("\t", "\\t"))
    return f'"{escaped}"'


def toml_key(key: str) -> str:
    """Bare where it can be, quoted where it must be.

    TOML's bare keys are ASCII letters, digits, ``-`` and ``_``. ``str.isalnum`` is
    true of every letter and figure in Unicode, so a key of one word in another
    script, a team ``Ñandúes`` or a round ``３``, went out bare, and the file the
    writer had just written would not load (4.535).
    """
    if key and all(c.isascii() and (c.isalnum() or c in "-_") for c in key):
        return key
    return toml_string(key)


def toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return f"{value:g}" if isinstance(value, float) else str(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(toml_value(v) for v in value) + "]"
    return toml_string(str(value))


# How far an array's items are indented under the key they belong to. Four, not
# two: the caller's own indent may be two, and an item at the same indent as its
# key reads as another key.
_ITEM_INDENT = "    "


def toml_lines(assignment: str, *, width: int, indent: str = "") -> list[str]:
    """One ``key = value`` line, indented, as however many lines ``width`` allows.

    Here rather than in the report that calls it because the only breaks worth
    taking are the ones TOML allows, and knowing which those are is this module's
    job. A club pastes these lines into its config file, so a break that leaves the
    paste unparseable is worse than a line running off the edge of a terminal: the
    error it gets back names a line this program wrote.

    Arrays break between items and never inside one -- a draw split down the middle
    of ``["Aces", "Iguanas"]`` still loads, as two teams playing nobody. Anything
    else long is handed back whole: a TOML basic string cannot be continued on the
    next line, so for a club name that runs over there is nothing honest to do.

    ``indent`` is passed in rather than added by the caller afterwards, so that the
    indent and the width cannot disagree. A caller that printed these six columns in
    and asked for the whole width got lines six columns over it, and the subtraction
    that would have fixed that was invisible from both ends.
    """
    if len(indent) + len(assignment) <= width:
        return [indent + assignment]
    key, sep, value = assignment.partition(" = ")
    value = value.strip()
    if not sep or not (value.startswith("[") and value.endswith("]")):
        return [indent + assignment]
    items = _toml_items(value[1:-1])
    if not items:
        return [indent + assignment]

    out = [f"{indent}{key} = ["]
    body = indent + _ITEM_INDENT
    row = ""
    for item in items:
        # Trailing comma on every item, the last one included. TOML allows it, and
        # it means a club adding a team appends a line rather than editing two.
        piece = f"{item},"
        if row and len(body) + len(row) + 1 + len(piece) > width:
            out.append(body + row)
            row = piece
        else:
            row = f"{row} {piece}" if row else piece
    if row:
        out.append(body + row)
    out.append(f"{indent}]")
    return out


def _toml_items(inner: str) -> list[str]:
    """The top-level items of an array's contents, splitting only where it is safe.

    A comma inside a quoted name and a comma between two fixtures look the same to
    ``str.split``, and a club whose team is called "Rockets, Reserves" is the one
    who would find out.
    """
    items: list[str] = []
    depth = 0
    quote = ""
    start = 0
    i = 0
    while i < len(inner):
        char = inner[i]
        if quote:
            # Only a basic string has escapes; in a literal string a backslash is a
            # backslash, so skipping the next character there would eat a quote.
            if char == "\\" and quote == '"':
                i += 1
            elif char == quote:
                quote = ""
        elif char in "\"'":
            quote = char
        elif char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
        elif char == "," and depth == 0:
            items.append(inner[start:i].strip())
            start = i + 1
        i += 1
    items.append(inner[start:].strip())
    return [item for item in items if item]


def slots_line(fmt: fmt_mod.MatchFormat) -> str:
    """The ``slots`` line that declares *fmt*'s shape, set by set in row order."""
    words = ["doubles" if slot.doubles else "singles" for slot in fmt.slots]
    return f"slots = {toml_value(words)}"


def shape_line(fmt: fmt_mod.MatchFormat) -> str:
    """The one line that declares *fmt*'s shape: its name, or its slots if it has none.

    One rule for the file :func:`render` writes and for the review list's item, which
    says of that line "delete the # to adopt it". Both offered ``format = <name>``
    whatever the shape, and a shape format.py has no name for is named by its
    pattern: ``format = "d1-d2-s1"``, adopted, stopped the next run at exit 2 with
    "unknown match format". ``slots`` is the key that declares any shape at all.
    """
    name = fmt_mod.shape_name(fmt.slots)
    return f"format = {toml_value(name)}" if name else slots_line(fmt)


# A statement's key at the start of a line: bare, dotted or quoted, then `=`.
_KEY_START = re.compile(r"""\s*(?:"[^"\n]*"|'[^'\n]*'|[A-Za-z0-9_.-]+)\s*=""")


# A key with nothing after its `=`: a value written for the club to type, which
# no TOML reader loads until it is typed. Read as one value of its own, so that
# the line as written is held where it stands and a line with a value typed
# behind its # is not that line.
_BARE_KEY = re.compile(r"\s*([A-Za-z0-9_-]+)\s*=\s*")


class _ToType:
    """The value of a bare ``key =`` line, equal only to itself."""

    def __repr__(self) -> str:
        return "<to type>"


_TO_TYPE = _ToType()


def _header_of(line: str) -> tuple[tuple[str, ...], bool] | None:
    """The table a ``[x]`` or ``[[x]]`` line opens, and if it is an array; or None."""
    stripped = line.strip()
    if not stripped.startswith("["):
        return None
    try:
        node: object = tomllib.loads(stripped)
    except tomllib.TOMLDecodeError:
        return None
    path: list[str] = []
    # An array of tables is a list only at its last key, where the walk stops.
    while isinstance(node, dict) and len(node) == 1:
        (key, node), = node.items()
        path.append(key)
    return tuple(path), stripped.startswith("[[")


def _uncommented(line: str) -> str:
    """A comment line with its ``#`` deleted, and the one space after it."""
    body = line.strip()[1:]
    return body[1:] if body.startswith(" ") else body


class _Block(NamedTuple):
    """One table of an item or a file: which, if an array, and the values under it.

    ``behind`` is a file's header line as it reads with its ``#`` deleted, where
    the header is commented, and "" where it is live or the block is an item's.
    ``line`` is that commented header's line in the file, counted from 1, and 0
    where there is none.
    """
    path: tuple[str, ...]
    array: bool
    values: dict
    behind: str = ""
    line: int = 0


def _fragment_blocks(lines: Iterable[str]) -> list[_Block]:
    """A review item's TOML as blocks, one to each header, comments none."""
    blocks: list[_Block] = []
    for line in lines:
        head = _header_of(line)
        if head is not None:
            blocks.append(_Block(*head, {}))
        elif blocks and (bare := _BARE_KEY.fullmatch(line)):
            blocks[-1].values[bare.group(1)] = _TO_TYPE
        elif blocks:
            try:
                blocks[-1].values.update(tomllib.loads(line))
            except tomllib.TOMLDecodeError:
                continue
    return blocks


def _commented_blocks(text: str) -> list[_Block]:
    """What a file holds behind a ``#``, as the table each statement sits in.

    A statement is filed under the last header above it, live or commented, and
    read over as many commented lines as it takes to parse: a list is written one
    item per line once it is too wide for one. A line that never parses, prose with
    an ``=`` in it, is left where it was, and the lines after it read on their own.
    """
    blocks: list[_Block] = [_Block((), False, {})]
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        if not line.strip().startswith("#"):
            head = _header_of(line)
            if head is not None:
                blocks.append(_Block(*head, {}))
            continue
        body = _uncommented(line)
        head = _header_of(body)
        if head is not None:
            blocks.append(_Block(*head, {}, behind=body.strip(), line=index))
            continue
        if not _KEY_START.match(body):
            continue
        if bare := _BARE_KEY.fullmatch(body):
            # Read on, it took in the comment under it and held nothing.
            blocks[-1].values[bare.group(1)] = _TO_TYPE
            continue
        statement, end = body, index
        while True:
            try:
                blocks[-1].values.update(tomllib.loads(statement))
            except tomllib.TOMLDecodeError:
                if (end < len(lines) and lines[end].strip().startswith("#")
                        and _header_of(_uncommented(lines[end])) is None):
                    statement += "\n" + _uncommented(lines[end])
                    end += 1
                    continue
                break
            index = end
            break
    return blocks


def _table_at(document: dict, path: tuple[str, ...]) -> object:
    node: object = document
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def opens(header: str, text: str) -> bool:
    """Whether a ``[x]`` or ``[[x]]`` *header* can be added to the config *text*.

    False where the file declares that table already, by a header of its own or
    by a key that made it, so that a second one stops the next run at exit 2,
    "Cannot declare ... twice". The review offered ``[player_names]`` and its
    lines "to add" to a file that had that table, and ``held_in`` counted values
    under a ``# [x]`` whose ``#`` could not be deleted for the same reason.

    Asked of the loader, by parsing the file with the header added, and not of a
    copy of TOML's rules: a table may follow its own subtable, and may not follow
    a dotted key that made it, and ``[competitions."x"]`` is ``[competitions.x]``.
    """
    try:
        tomllib.loads(f"{text}\n{header.strip()}\n")
    except tomllib.TOMLDecodeError:
        return False
    return True


def _holds(block: _Block, wanted: _Block) -> bool:
    """Whether one block of a file has every value of one block of an item."""
    return (block.path == wanted.path and block.array == wanted.array
            and bool(wanted.values)
            and all(key in block.values and block.values[key] == value
                    for key, value in wanted.values.items()))


class Held(NamedTuple):
    """Where a config file holds one review item, as :func:`holding` answers it.

    ``status`` is :func:`held_in`'s answer, and ``at`` the line of each ``# [x]``
    header whose block holds a part of it, in file order, where that is
    ``"split"``, and () otherwise.
    """
    status: str
    at: tuple[int, ...] = ()


def _split_over(one: _Block, held: list[_Block]) -> list[_Block]:
    """The blocks of *held* that hold *one* between them, or [] where they do not.

    Only a plain ``[x]`` table: two ``[[x]]`` blocks are two entries of an array,
    and one entry's values over the two are not an entry the file holds. Only a
    block under a ``# [x]``, since each part is named by its header's line.
    """
    each = [one._replace(values={key: value}) for key, value in one.values.items()]
    held = [block for block in held if block.line]
    if one.array or not all(any(_holds(block, value) for block in held)
                            for value in each):
        return []
    return [block for block in held if any(_holds(block, value) for value in each)]


def held_in(lines: Iterable[str], text: str) -> str:
    """Where the config *text* holds one review item's TOML *lines*: the status of
    :func:`holding`, which says where as well."""
    return holding(lines, text).status


def holding(lines: Iterable[str], text: str) -> Held:
    """Where the config *text* holds one review item's TOML *lines*.

    ``"live"`` when a key of it is declared there, whatever its value, so the next
    run reads it; ``"commented"`` when every block of it is in the file behind a
    ``#``, whole, in one block of its own table and kind, so deleting the ``#``
    adopts it; ``"split"`` when a plain table of it is not, but is over several
    blocks of that table, each behind a ``# [x]``, whose lines have to be gathered
    under one of those headers first, as a file declares a table once; and
    ``"absent"`` otherwise, a file that holds none of it or other values
    under the same keys. One answer for every run: the review said where each
    value landed only on the run that had just written the file, and on every run
    after asked for all of them again, though the file held each one behind a ``#``.

    A block under a commented header counts only where :func:`opens` says that
    header can be added, since its ``#`` is one of those a club deletes. Read from
    the file rather than from what :func:`render` would write, because a club edits
    it: a line changed behind its ``#`` is not the value offered.
    """
    wanted = _fragment_blocks(lines)
    try:
        declared = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        declared = {}
    for block in wanted:
        node = _table_at(declared, block.path)
        if (isinstance(node, list) and bool(node) if block.array
                else isinstance(node, dict)
                and any(key in node for key in block.values)):
            return Held("live")
    held = [block for block in _commented_blocks(text)
            if not block.behind or opens(block.behind, text)]
    whole = [any(_holds(block, one) for block in held) for one in wanted]
    if wanted and all(whole):
        return Held("commented")
    # Split, measured on the demo's file with its six team names moved under two
    # "# [team_names]": "not in it", of a file that held all six, and deleting the
    # # of both headers stopped the next run at exit 2, "Cannot declare twice".
    parts = [[] if kept else _split_over(one, held)
             for one, kept in zip(wanted, whole)]
    if wanted and all(kept or part for kept, part in zip(whole, parts)):
        return Held("split", tuple(sorted({block.line for part in parts
                                           for block in part})))
    return Held("absent")


HEADER = """\
# matchcentre-dashboard
#
# Written for you from the scorecards in data/. Everything here is optional:
# the dashboard already works. Filling it in makes the dashboard say what you
# know instead of what this program could work out on its own.
#
# Nothing in this file can change a score. Results only ever come from the
# scorecards. This file supplies names, captains, season labels, the list of
# teams, the draw, and which scoring scheme your competition uses.
#
# Edit it, then run `python3 build.py` again.
"""

# The same file, written by a run that read nothing. It used to carry the header
# above, which says where every value in it came from -- and none of them came
# from anywhere, because there were no cards to read. A club meeting this file
# has an empty `data/` and has not exported anything yet, so what it needs from
# the first sentence is that the dashboard is not waiting on this file.
EMPTY_HEADER = """\
# matchcentre-dashboard
#
# Written for you before any scorecard was read: data/ held none, so nothing
# here came from one. Everything in it is optional anyway -- the dashboard is
# built from the cards, and reads this file only for the things a card cannot
# say.
#
# Nothing in this file can change a score. Results only ever come from the
# scorecards. This file supplies names, captains, season labels, the list of
# teams, the draw, and which scoring scheme your competition uses.
#
# Export a match from matchcentre.tennis.com.au as a PDF, put it in data/, and
# run `python3 build.py`.
"""


def render(config: Config, *, competitions: object = (),
           club_name: str = "", cards: int = 0, matches: object = None) -> str:
    """The config file, filled in from a discovery pass.

    ``competitions`` is a sequence of :class:`~matchcentre.competition.Competition`.
    Values this program merely *worked out* are written commented, so adopting one
    is a deliberate act — deleting a ``#``. That is not friction for its own sake:
    an uncommented ``teams`` list closes the team roster, which switches on bye
    handling and turns an unrecognised team on a card into an error. Both are
    right once a human has checked the list and wrong before.

    ``cards`` is how many were read, and it decides the header: a file written by
    a run that read none of them cannot say where its values came from, because
    none of them came from anywhere.

    ``matches`` is the same pass's matches, by competition id, and what a length
    left commented says the dashboard will show is counted from them.

    A line is live only when ``config`` declared it, and that includes the names:
    the club's own, a title, a team's, a captain's and a season's label. Written
    live, the file a first run wrote was read back by the second as the club's
    declaration, so two runs over the same cards built two different dashboards.
    Commented, the next run reads what this one read until the club removes a #.
    """
    comps = list(competitions)
    lines: list[str] = [HEADER if cards else EMPTY_HEADER]

    lines.append("[club]")
    # Live when the letterhead gave nothing: the stub a run before the first card
    # writes, whose empty name declares nothing and is the line a club types into.
    if config.club_name or not club_name:
        lines.append(f"name = {toml_value(config.club_name)}")
    else:
        lines.append("# As the cards' letterhead prints it. Remove the # to use it,")
        lines.append("# written the way you would.")
        lines.append(f"# name = {toml_value(club_name)}")
    lines.append("# Every team of yours printed with the same prefix? Put it here")
    lines.append("# and it stops cluttering the dashboard, e.g. \"Riverside\".")
    prefix_line = f"strip_team_prefix = {toml_value(config.strip_team_prefix)}"
    lines.append(prefix_line if config.strip_team_prefix
                 else f"# {prefix_line}")
    lines.append("")

    lines.append("[build]")
    lines.append('# "single" if data/ holds one season, "multi" if you keep past')
    lines.append("# seasons in their own folders under data/ (data/2026-winter/...).")
    lines.append(f"seasons = {toml_value(config.seasons_mode)}")
    # Made from the name the page's own heading is made from, the club's where it
    # gave one: made from the letterhead, a rewrite over a declared name suggested
    # a heading in capitals that the page did not show.
    title = site_title(config, config.club_display(club_name))
    title_line = f"title = {toml_value(title)}"
    if config.title:
        lines.append(title_line)
    else:
        lines.append("# The heading over every page, made from the club's name while")
        lines.append("# this is commented out. Remove the # to fix it as written.")
        lines.append(f"# {title_line}")
    lines.append("# To keep players' names off the dashboards, see "
                 "\"Publishing without")
    lines.append("# names\" in README.md.")
    players_line = f"players = {toml_value(config.players_mode)}"
    lines.append(players_line if config.players_mode != "names"
                 else '# players = "numbered"')
    lines.append("")

    lines.append("[rules]")
    lines.append("# How your competition turns sets into ladder points. Getting this")
    lines.append("# wrong reorders the ladder, so it is printed on every run and")
    lines.append("# shown on the dashboard. Options:")
    for name in sorted(scoring.RULESETS):
        lines.extend(_option_lines(name, scoring.RULESETS[name].describe(), pad=12))
    lines.extend(_chosen_rule_lines(config, comps, "scoring",
                                    scoring.DEFAULT_RULESET))
    lines.append("# When a set is finished. No scorecard can tell us this — 6-3 looks")
    lines.append("# the same whether the set was played to 6 or to 8. Options:")
    for name in sorted(fmt_mod.SET_RULES):
        lines.extend(_option_lines(name, fmt_mod.SET_RULES[name].describe(), pad=18))
    lines.extend(_chosen_rule_lines(config, comps, "sets", fmt_mod.DEFAULT_SET_RULE))
    lines.append("# What a round a team did not play is worth — a bye, a washout, a")
    lines.append("# week called off. This changes the points column of every team")
    lines.append("# that has had a week off, and can change the ladder order: all")
    lines.append("# the presets but games-bonus rank on points first, and under")
    lines.append("# games-bonus it can reorder teams level on average. Options:")
    for mode in scoring.UNPLAYED_MODES:
        lines.extend(_option_lines(
            mode, scoring.unplayed_detail(mode == scoring.UNPLAYED_AVERAGE), pad=9))
    if config.unplayed:
        lines.append(f"unplayed = {toml_value(config.unplayed)}")
    else:
        lines.append(f"# unplayed = {toml_value(scoring.UNPLAYED_ZERO)}"
                     f"   # left off: whatever the preset above says")
    lines.append("# Can a night end level? Many associations decide every match — a")
    lines.append("# tiebreak, a countback, a by-law. Say draws = false and a level")
    lines.append("# card is reported as a result nobody recorded, and left out of")
    lines.append("# the ladder, instead of being scored as a draw you do not have.")
    if config.draws is None:
        lines.append("# draws = false   # left off: whatever the preset above says")
    else:
        lines.append(f"draws = {toml_value(config.draws)}")
    # What a forfeit is worth (4.420), live where the club declared it and behind
    # a # where it did not, as unplayed and draws are.
    lines.append("# What a match forfeited whole is worth: the Match Forfeited box,")
    lines.append("# or a walkover Match Centre exported as cancelled. Options:")
    for award in scoring.FORFEIT_AWARDS:
        lines.extend(_option_lines(award, scoring.forfeit_detail(award), pad=11))
    if config.forfeit_award:
        lines.append(f"forfeit_award = {toml_value(config.forfeit_award)}")
    else:
        lines.append(f"# forfeit_award = {toml_value(scoring.DEFAULT_FORFEIT_AWARD)}"
                     f"   # left off: whatever the preset above says")
    if config.forfeit_points is not None:
        lines.append(f"forfeit_points = {toml_value(config.forfeit_points)}")
    else:
        lines.append('# forfeit_points = 5   # read only by forfeit_award = "fixed"')
    # What the finals are (4.537), live where the club declared it and behind a #
    # where it did not, as the forfeit award is.
    lines.append("# What your finals are, after the regular rounds. Options:")
    for mode in scoring.FINALS_FORMATS:
        lines.extend(_option_lines(mode, scoring.finals_detail(mode), pad=9))
    if config.finals:
        lines.append(f"finals = {toml_value(config.finals)}")
    else:
        lines.append(f"# finals = {toml_value(scoring.FINALS_PLACINGS)}"
                     f"   # left off: no format, and nobody is placed")
    lines.append("# The shape of a match is read from your scorecards, so you only")
    lines.append("# need these to pin it down or to override what was read. Named")
    lines.append("# shapes:")
    # Above the value, not in a trailing comment on it. Ten names after `# format =
    # "doubles-singles-doubles"` made the longest line this program writes, at 165
    # columns, and a name a club has to spell exactly is not a thing to hide off-screen.
    lines.extend(_comment_flow(choices(sorted(fmt_mod.FORMATS)), indent="#   "))
    # Live when the club declared them, commented when this file is suggesting
    # them -- the rule the `unplayed` and `draws` lines above follow, and the one
    # these three did not. Written commented whatever the config said, a rewrite
    # put a club's declared format in a comment and so unset it: the shape went
    # back to "read from each card", and with it the check that makes a card with
    # a row missed an error rather than a card of a different shape.
    #
    # A suggestion is what the cards already have, since the review list says of
    # it "delete the # to adopt it". A [rules] line reaches every competition that
    # sets none of its own, so where those differ no [rules] line is right, and
    # each of them is offered its own in its own section instead.
    shape = config.rules_format(comps)
    minimum = config.rules_minimum(comps)
    split_shape = (not config.format and not config.slots and shape is None
                   and bool(config._inheriting(comps, "format", "slots")))
    split_minimum = (config.min_sets_for_result is None and minimum is None
                     and bool(config._inheriting(comps, "min_sets_for_result")))
    # Each shape is offered the line shape_line gives it, and slots as well where
    # the shape has a name, as the cards have it: the one slots line this wrote
    # for every shape was doubles-singles-doubles, and a club of any other that
    # deleted its # had every card refused. A shape with no name has no format
    # line, since the name it was offered was a pattern the loader refuses.
    #
    # With no card this line reaches, there is no shape to offer: the four-set
    # default it offered, its # deleted, refused every card of any other shape.
    offered = config.declared_format() or next(
        (comp.fmt for comp in config._inheriting(comps, "format", "slots")
         if comp.fmt.name == shape), None)
    if config.format:
        lines.append(f"format = {toml_value(config.format)}")
    elif split_shape:
        lines.extend(_comment_flow(
            "format, or slots for a shape with no name, is set in each "
            "[competitions] section below, because the cards of these "
            "competitions describe different shapes."))
    elif offered is None:
        lines.extend(_comment_flow(
            "format, or slots for a shape with no name, is set in each "
            "[competitions] section below, because each of them sets its own."
            if comps else
            "No card has been scored yet, so no shape is written here: one your "
            "cards do not have would refuse every one of them. format takes a "
            "name above; slots, for a shape with no name, takes the kind of each "
            "set, doubles or singles, in the order the cards print them."))
    elif fmt_mod.shape_name(offered.slots):
        lines.append(f"# {shape_line(offered)}")
    if config.slots:
        lines.append(f"slots = {toml_value(list(config.slots))}")
    elif not split_shape and offered is not None:
        lines.extend(toml_lines(slots_line(offered), width=WIDTH, indent="# "))
    if config.min_sets_for_result is not None:
        lines.append(f"min_sets_for_result = {toml_value(config.min_sets_for_result)}"
                     f"   # sets that must finish before a result counts")
    elif split_minimum:
        lines.extend(_comment_flow(
            "min_sets_for_result is set in each [competitions] section below, "
            "because these competitions need different numbers."))
    elif minimum is None:
        lines.extend(_comment_flow(
            "min_sets_for_result is set in each [competitions] section below, "
            "because each of them sets its own." if comps else
            "min_sets_for_result, the sets that must finish before a result "
            "counts, is left to the shape. One is offered here when this file is "
            "filled in from cards that were scored."))
    else:
        lines.append(f"# min_sets_for_result = {minimum}   # sets that must finish "
                     "before a result counts")
    lines.append("")
    own_shape = ({comp.id for comp in config._inheriting(comps, "format", "slots")}
                 if split_shape else set())
    own_minimum = ({comp.id for comp in
                    config._inheriting(comps, "min_sets_for_result")}
                   if split_minimum else set())

    lines.extend(_seasons_section(config, comps))
    lines.extend(_names_section(config, comps))
    lines.extend(_aliases_section(config))
    lines.extend(_namesakes_section(config, comps))

    for comp in comps:
        own = []
        if comp.id in own_shape:
            own.extend(toml_lines(shape_line(comp.fmt), width=WIDTH, indent="# "))
        if comp.id in own_minimum:
            own.append(f"# min_sets_for_result = {comp.fmt.min_sets_for_result}   "
                       "# sets that must finish before a result counts")
        lines.extend(_competition_section(config, comp,
                                          (matches or {}).get(comp.id, ()), own=own))
    # A section the club declared and no card was filed under this run is still the
    # club's. Walking only the competitions the cards built dropped it from the
    # file, and with it a night whose first round had not been played yet.
    built = {comp.id for comp in comps}
    carried = [section for comp_id, section in config.competitions.items()
               if comp_id not in built]
    for section in carried:
        lines.extend(_declared_section(section, config.finals_for(section)))

    if not comps and not carried:
        # The advice here used to be "Add your PDFs and run build.py again", which
        # is the one thing that cannot fill this file in: a plain run leaves a
        # config that exists exactly as it is, which is what `--discover` is for.
        # So the file told a club to wait for something no run does, while the
        # dashboard was built beside it from cards this file said nothing about.
        # The dashboard comes first all the same, because it needs nothing here.
        if cards:
            lines.extend(_comment_flow(
                f"No competition could be built from the {cards} scorecard"
                f"{'' if cards == 1 else 's'} under data/, so there is nothing to "
                f"describe yet. The report above says what each card was refused "
                f"for."))
        else:
            lines.extend(_comment_flow(
                "No scorecards were found under data/, so there is nothing to "
                "describe yet."))
        lines.extend(_comment_flow(
            "Put the PDFs there and run this, which builds the dashboard and "
            "needs nothing from this file:"))
        lines.append("#")
        # On a line of its own, and not inside the paragraph: flowed to the width,
        # the command broke between --discover and --force, and a club copies what
        # it reads. The same reason the report prints a command this way.
        lines.append("#   python3 build.py")
        lines.append("#")
        lines.extend(_comment_flow(
            "Then this, to fill this file in from those cards:"))
        lines.append("#")
        lines.append("#   python3 build.py --discover --force")
        lines.append("#")
        lines.extend(_comment_flow(
            "A plain run never writes over a config file that already exists, "
            "so without --force this one stays exactly as it is."))
        lines.append("#")
        lines.append('# [competitions."thursday-division-b"]')
        lines.append('# label = "Thursday Division B"')
        lines.append('# day = "Thursday"')
        lines.append('# division = "B"')
        lines.append("")

    if cards:
        lines.append(f"# Written from {cards} scorecard"
                     f"{'' if cards == 1 else 's'} in data/.")
    return "\n".join(lines).rstrip() + "\n"


def default_title(club_name: str) -> str:
    """The site heading to use when nobody has declared one.

    Asked only by :func:`site_title`. When each module had its own version of
    this line, the first build headed the page "<club> dashboards" and the config
    it wrote said "<club> season dashboards", so the second build silently
    retitled the site -- a diff with no cause a club could point at.
    """
    return f"{club_name} season dashboards" if club_name else "Season dashboards"


def site_title(config: Config, club_name: str) -> str:
    """The heading over every page: the declared ``[build] title``, else the default.

    *club_name* is the name the page prints, :meth:`Config.club_display` of the
    letterhead. The page, the ``title =`` line :func:`render` writes and the guard
    that searches the front page for a withheld name all ask this one function,
    so the guard searches exactly the title the site prints.
    """
    return config.title or default_title(club_name)


def _names_section(config: Config, comps: list) -> list[str]:
    """Display-name overrides: the club's live, and each team guessed at commented."""
    from .competition import Provenance

    # The club's names live and this program's guesses commented, every guess and
    # a mixed-case print among them: written live, the next run read each one back
    # as declared and moved the teams' provenance from "derived" to "mixed".
    lines = ["[team_names]"]
    # The two guesses named are the team rule's judged costs (slugs.display_name),
    # which a club adopting every line would otherwise take unread.
    lines.extend(_comment_flow(
        "Scorecards shout team names, so a line below with a # in front is this "
        "program's best guess at how you write one. Two guesses most need checking: "
        'an acronym of four letters or more is read as a word, so ABCD is offered '
        'as "Abcd", and a letter A before another word can be read as the word "a", '
        'so PLAN A ROCKETS is offered as "Plan a Rockets". Correct a line that reads '
        "wrongly and remove the # to use it."))
    written: dict[str, str] = dict(config.team_names)
    for comp in comps:
        for slug, team in sorted(comp.teams.items()):
            if team.name_source is Provenance.DECLARED:
                continue
            written.setdefault(slug, team.name)
    for slug in sorted(written):
        line = f"{toml_key(slug)} = {toml_value(written[slug])}"
        lines.append(line if slug in config.team_names else f"# {line}")
    if not written:
        lines.append('# "riverside-rockets" = "Riverside Rockets"')
    lines.append("")

    lines.append("[player_names]")
    lines.append("# Player names come off the scorecards as printed, so this is")
    lines.append("# normally empty. Add a line to correct a spelling.")
    for slug in sorted(config.player_names):
        lines.append(f"{toml_key(slug)} = {toml_value(config.player_names[slug])}")
    if not config.player_names:
        lines.append('# "j-smith" = "Jo Smith"')
    lines.append("")
    return lines


def _aliases_section(config: Config) -> list[str]:
    lines = ["[aliases]",
             "# One person, two spellings across cards. Left slug becomes right.",
             "# The dashboard lists anyone who appears only once or twice under",
             "# 'players to check', which is where you will spot these."]
    for slug in sorted(config.aliases):
        lines.append(f"{toml_key(slug)} = {toml_value(config.aliases[slug])}")
    if not config.aliases:
        lines.append('# "a-fernandez" = "ana-fernandez"')
    lines.append("")
    return lines


def _namesakes_section(config: Config, comps: list) -> list[str]:
    """The exact inverse of the aliases above, pre-filled with anything found.

    A name printed in two teams' roster blocks arrives already written out and
    commented. The club's job is to decide which of the two things it is, and
    typing the line is not part of that job -- the same reason the ``teams`` array
    is written out rather than described.
    """
    lines = ["[namesakes]",
             "# Two people, one printed name, told apart by the team each plays",
             "# for: every team named here gets a player of its own. The inverse",
             "# of [aliases] above, which folds two spellings into one person.",
             "# Leave an entry off for one person who changed teams mid-season —",
             "# then both halves belong to one record, which is what you already",
             "# have. To use an entry below, delete every # in front of it."]
    for slug in sorted(config.namesakes):
        lines.extend(toml_lines(
            f"{toml_key(slug)} = {toml_value(list(config.namesakes[slug]))}",
            width=WIDTH))
    shared = _shared_rosters(comps)
    printed = {team: comp.team_name(team) for comp in comps for team in comp.teams}
    for slug in sorted(shared):
        found = shared[slug]
        if set(config.namesakes.get(slug, ())) >= set(found):
            # Already decided. Offering the same name back commented reads as
            # though the decision had not been made, and the next club to look
            # would have no way to tell which of the two lines was in force.
            continue
        lines.extend(toml_lines(
            f"{toml_key(slug)} = "
            f"{toml_value([printed.get(team, team) for team in found])}",
            width=WIDTH, indent="# "))
    if not config.namesakes and not shared:
        lines.append('# "j-smith" = ["Riverside Rockets", "Hilltop Aces"]')
    lines.append("")
    return lines


def _shared_rosters(comps: list) -> dict[str, list[str]]:
    """Player slugs more than one team in a competition has on its roster.

    Which is the whole of what this file can offer: whether they are two people or
    one who moved is not in the cards, and :mod:`matchcentre.validate` reports the
    same finding in prose for whoever is reading the run rather than the file.

    Held twice *inside* one competition, because that is the only place a merge
    happens — each competition gets a dashboard of its own. But the entry is keyed
    on the name, so one key must carry every team any competition found it in:
    written a competition at a time, the second grade overwrote the first and
    offered a club a line that fixed one grade and left the other merged. Teams are
    added rather than replaced, and only once, since two seasons of one grade are
    two competitions that quite properly share a team list.
    """
    out: dict[str, list[str]] = {}
    for comp in comps:
        holders: dict[str, list[str]] = {}
        for slug, team in sorted(comp.teams.items()):
            for player in team.players:
                holders.setdefault(player.slug, []).append(slug)
        for slug, teams in sorted(holders.items()):
            if len(teams) > 1:
                found = out.setdefault(slug, [])
                found.extend(team for team in teams if team not in found)
    return out


def _seasons_section(config: Config, comps) -> list[str]:
    """The ``[seasons]`` table, from the club's labels and the seasons found.

    The club's labels live, and each other season's name as its pages show it
    commented: written live, that name came back declared, and a rewrite then
    made it live in the season's own section too. Asked of
    :func:`~matchcentre.export.season_offer`, as every offer of a season's name
    is: offered as its key stood, ``# 2026-winter = "2026-winter"`` headed the
    front page "2026-winter" once its # was deleted, where it had read "2026
    Winter". Nothing where neither mode nor label calls for the table.
    """
    from .export import season_offer

    if not (config.multi_season or config.season_labels):
        return []
    lines = ["[seasons]", "# Pretty names for the folders under data/."]
    worked_out: dict[str, str] = {}
    for comp in comps:
        if comp.season_key and not config.season_label_keys(comp.season_key):
            worked_out.setdefault(comp.season_key, season_offer(comp))
    if worked_out:
        lines.extend(_comment_flow(
            "A line with a # in front is the season's name as its pages show it, "
            "from its cards, its own section or its folder's name made readable. "
            "Remove the # to use it."))
    for key in sorted({**config.season_labels, **worked_out}):
        if key in config.season_labels:
            label = config.season_labels[key]
            lines.append(f"{toml_key(key)} = {toml_value(label)}")
        else:
            lines.append(f"# {toml_key(key)} = {toml_value(worked_out[key])}")
    if not config.season_labels and not worked_out:
        lines.append('# "2026-winter" = "2026 Winter Season"')
    lines.append("")
    return lines


def _competition_section(config: Config, comp, matches=(), *,
                         own: list[str] | tuple = ()) -> list[str]:
    from . import export as export_mod
    from .competition import (Provenance, Round, is_finals_label, placing_weeks,
                              rounds_played_words)
    from .sheet import beyond_words

    declared = config.competition(comp.id)
    header = f"[competitions.{toml_key(comp.id)}]"
    lines = [header]
    lines.append(f"label = {toml_value(comp.display_label)}")
    if comp.day:
        lines.append(f"day = {toml_value(comp.day)}")
    if comp.division:
        lines.append(f"division = {toml_value(comp.division)}")
    if comp.season_key:
        # The third thing a card is filed on in multi-season mode, and the only one
        # of the three that is not on the card. Live, because it is not a guess: it
        # is the folder the club put the cards in. Without it the two seasons of one
        # division below were two sections claiming the same cards, which the loader
        # refuses, and a section on its own claimed its cards only while it kept the
        # name it was given here. Single-season mode files cards with no key at all,
        # so there it is never written: a section given one there claims nothing.
        lines.extend(_season_key_lines(comp.season_key))

    season_line = f"season = {toml_value(comp.season)}"
    if declared and declared.season:
        lines.append(season_line)
    elif comp.provenance_of("season") is Provenance.DECLARED and comp.season:
        # Declared by the season's [seasons] line, not by this section: written
        # live, the copy outranked the line, and an edit to the line renamed nothing.
        lines.extend(_comment_flow(
            "This season's name comes from its line under [seasons], which names "
            "every competition of the season. Remove the # to give this "
            "competition a name of its own, which that line then no longer "
            "changes."))
        lines.append(f"# {season_line}")
    elif shown := export_mod.season_label_from(comp):
        made = ("the year on its cards" if shown == export_mod.SEASON_FROM_DATE
                else "its folder's name made readable")
        lines.extend(_comment_flow(
            f"What your association calls this season. No card prints it, so the "
            f"dashboard shows {made} until you say, here or by the season's line "
            f"under [seasons]. Remove the # to use it."))
        lines.append(f"# season = {toml_value(export_mod.season_offer(comp))}")
    elif offer := export_mod.season_offer(comp, [match.date for match in matches]):
        lines.append("# What your association calls this season. Scorecards do not")
        lines.append("# reliably print it, so the dashboard leaves it out until you")
        lines.append("# say. Remove the # to use it.")
        lines.append(f"# season = {toml_value(offer)}")
    else:
        # No line: a `season = ""` to uncomment declares nothing, and the review
        # then calls it live and declared.
        lines.extend(_comment_flow(
            "What your association calls this season. No card printed it and no "
            "date gave a year, so the dashboard leaves it out until you say: add "
            "season = and the name in double quotes."))

    # The regular rounds with cards, counted by the function the page's number is.
    # Counted off the draw, a round it lists that nobody has played was a round
    # played, and so was a week the byes table rests the whole grade in; counted
    # with the finals, a final was a round of the season's length. Counted as the
    # run after it counts them, which has no draw to read a typed ``finals`` in.
    # The washouts are kept, as the rewrite keeps their table, so a round rained
    # off is counted played here as it is on the page (4.535).
    # And under placings the placing week by that run's rule, of the rounds it will
    # have, the cards' and the washouts', and the length written, so a placing week
    # with a fixture washed out is no regular round to it (4.537).
    finals = config.finals_for(declared)
    kept = declared.length_by_labels_for(finals) if declared else None
    numbers = {m.round for m in matches} | {
        number for number, rnd in comp.rounds.items() if rnd.washed_out or rnd.washouts}
    weeks = placing_weeks(numbers, length=kept, finals=finals)
    after = replace(comp, rounds={
        number: Round(number=number,
                      is_finals=is_finals_label(number) or number in weeks,
                      washed_out=comp.rounds[number].washed_out
                      if number in comp.rounds else False,
                      washouts=comp.rounds[number].washouts
                      if number in comp.rounds else ())
        for number in numbers})
    played = len(after.rounds_played(matches))
    # Live whenever the club gave the length, in rounds_total or through its draw:
    # the draw is not written back, and the length it gave would go with it. Not a
    # length the draw gave that its cards have played past, which F3 stopped this
    # run for: written live, it would stop every run after, with the draw gone.
    length = declared.season_length_for(finals) if declared else None
    # Counted as the run after it counts rounds, since a typed ``finals`` goes too:
    # ``kept``, above. Nor a length that run finds played past, by rounds with cards
    # the draw does not list: it would stop on it as this run stops on the one above.
    past = kept is not None and declared.rounds_total is None and bool(
        replace(after, rounds_total=kept).rounds_past_length(matches))
    if kept is not None and not comp.draw_outgrown_by and not past:
        lines.append(f"rounds_total = {kept}")
    else:
        if comp.draw_outgrown_by:
            lines.extend(_comment_flow(
                f"The draw you declared gave the season {length} "
                f"round{'' if length == 1 else 's'}, and "
                f"{beyond_words(comp.draw_outgrown_by, {m.round for m in matches})}, "
                f"which it does not list, so that length is not written here."))
        elif past:
            # Said as the draw's is. Without it, a length the draw had given went
            # from the file with nothing there to say why.
            lines.extend(_comment_flow(
                f"The draw you declared gave the season {kept} "
                f"round{'' if kept == 1 else 's'}, with a round counted a final "
                f"only if its label names one, and cards were found for {played} "
                f"regular rounds, so that length is not written here."))
        # No number: the rounds played, behind a # the club is told to delete,
        # declared the season over at the round it had reached, and the next
        # round's cards then stopped the run on F3. Left bare, the line does not
        # load until the club types the length, so no deleted # declares one.
        lines.extend(_comment_flow(
            f"Rounds in the full season, which no card prints. Without it the "
            f"dashboard says \"{rounds_played_words(played)}\" and shows no "
            f"percentage, rather than implying the season is over. Type after the "
            f"= the number of rounds your association's draw gives the season, "
            f"then remove the #."))
        lines.append("# rounds_total =")

    names = listed_teams(comp, declared)
    if declared and declared.teams_declared:
        # As the club spelt and ordered it. Written from the names the page shows, it
        # took in [team_names] too, and "The Rockets" declared a team no card names.
        lines.extend(toml_lines(f"teams = {toml_value(names)}", width=WIDTH))
    else:
        # A list a declared draw closed is the draw's teams, and not the cards':
        # said to be read from the cards, it left out a team they name.
        block, uncomment = commented_teams(names)
        source = ("your declared draw names them" if comp.teams_declared
                  else "read from the cards")
        lines.extend(_comment_flow(
            f"The full list of teams in this competition, as {source}. "
            f"Declaring it tells the program the list is complete, which lets it "
            f"handle byes and flag a misspelt team instead of inventing a new one."))
        lines.extend(_comment_flow(f"Check it, then {uncomment} it to use it."))
        lines.extend(block)
    if comp.strangers:
        # The teams the cards name that the list lacks, which F8 reports, as the
        # list that adds them: one of them may be another spelling of a team in
        # it, which an [aliases] line settles instead, so behind a #.
        block, uncomment = commented_teams([*names, *comp.strangers])
        many = len(comp.strangers) > 1
        lines.extend(_comment_flow(
            f"The cards also name {in_words(comp.strangers)}, which the list "
            f"above lacks. If {'those are teams' if many else 'that is a team'} "
            f"of this competition and not another spelling of one in it, this "
            f"is the list with {'them' if many else 'it'}: delete the list "
            f"above and {uncomment} this one."))
        lines.extend(block)
    lines.extend(_rule_lines(declared))
    if own:
        lines.extend(_comment_flow(
            "What this competition's cards say. Another competition here differs, "
            "so it goes in this section and not under [rules]: delete the # to pin "
            "it."))
        lines.extend(own)

    # What the club declared, as it keyed and spelt it, live, and then the (C) marks
    # for the teams it named nobody for, commented. Built from the teams alone, a
    # declared captain on no card came back as its slug, and one for a team with no
    # card yet did not come back at all. Written live, a mark came back declared.
    # Where the marks name another than the club does, that is offered under the
    # club's line, commented (4.431's proposal says so of each).
    offers = captain_offers(comp, declared)
    captains = {offer.key: offer.value for offer in offers if offer.instead is None}
    instead = {offer.key: offer for offer in offers if offer.instead is not None}
    stated = declared.captains if declared else {}
    captains.update(stated)
    lines.append("")
    lines.append(f"[competitions.{toml_key(comp.id)}.captains]")
    if captains:
        if stated:
            # This file's, and not "yours": a file an earlier version wrote holds the
            # (C) marks live, and they are read back as named whoever typed them.
            lines.extend(_comment_flow(
                "As this file had them, with any others read from the (C) marks on "
                "the scorecards written with a # in front. Remove the # to use one."))
        else:
            lines.extend(_comment_flow(
                "Read from the (C) marks on the scorecards. Correct a line if it "
                "reads wrongly and remove the # to use it."))
        for team_name in sorted(captains):
            line = f"{toml_key(team_name)} = {toml_value(captains[team_name])}"
            lines.append(line if team_name in stated else f"# {line}")
            if team_name in instead:
                # The team as the courts' comment names it, and not the club's key.
                lines.extend(_comment_flow(
                    f"The (C) marks on the cards of "
                    f"{comp.team_by_name(team_name).name} name "
                    f"{instead[team_name].value}. To use that, delete the line "
                    f"above and the # below."))
                lines.append(f"# {toml_key(team_name)} = "
                             f"{toml_value(instead[team_name].value)}")
    else:
        # "On the cards that count": a washout's (C), or a (C) on a night not yet
        # played, names nobody, so a folder of those alone prints marks and has none.
        lines.append("# No (C) mark on a card that counts names a captain. Add")
        lines.append("# captains here if you want them shown.")
        for team_name in sorted(names):
            lines.append(f"# {toml_key(team_name)} = \"\"")

    lines.extend(_courts_lines(comp, declared, names))
    # Codes are printed on no card, so all there is of them is what was declared:
    # written from the declaration, and not from the teams it reached.
    lines.extend(_table_lines(
        comp.id, "codes", declared.codes if declared else {},
        "# The number your published draw gives each team."))

    lines.extend(_byes_lines(config, comp))
    lines.extend(_washouts_lines(comp.id, declared))
    lines.extend(_expected_lines(config, comp, names))

    lines.append("")
    # Taught in words and not written out behind a #, because no round this file
    # could write is the club's to adopt. The example round it wrote, dated
    # 2026-08-18 and pairing the first two teams, gave three draw-mismatch errors
    # once its # was deleted, and the cards' own rounds are a history: declared,
    # the week a postponed match's card arrives stops the run.
    lines.extend(_comment_flow(
        f"Your published draw, if you have one. Declaring it lets the dashboard "
        f"show fixtures that have not been played yet, and pins the season's "
        f"length. Type it in from your association's sheet, a "
        f"[[competitions.{toml_key(comp.id)}.rounds]] table for each round with "
        f"its number, date and fixtures, and bye or finals where a round has one; "
        f"config.example.toml has one worked through. None is written here: the "
        f"cards show only the rounds played so far, and each run's review lists "
        f"them, to check the sheet against."))
    lines.append("")
    return lines


def _season_key_lines(key: str) -> list[str]:
    """A section's ``season_key``, with what it is and why it has to stay.

    Shared by a section the cards built and one carried over with none, since it is
    the same key and a rename disconnects either. It is the folder's name as the run
    slugs it, so the comment says capitals and spaces do not matter rather than
    calling ``2026-winter`` the name of ``data/2026-Winter/``; and a card sitting in
    ``data/`` itself is filed under its year, which is a key no folder has.
    """
    return ["# The season folder under data/ these cards are in, or their year",
            "# if they sit in data/ itself; capitals and spaces do not matter.",
            "# Keep it if you rename the section: it is how a card finds this one.",
            f"season_key = {toml_value(key)}"]


def _byes_lines(config: Config, comp) -> list[str]:
    """The ``[competitions.<id>.byes]`` block, pre-filled with what was worked out.

    Written by team and by round number, which is how an association's own sheet
    reads. A club only has to add the rounds this program could not see: the week
    nobody played at all, or a round whose cards have not arrived. Everything
    already inferred is written *commented*, so it is visible and confirmable
    without changing what the next run does.

    How a line is typed is said in words, and not by an example line: the one
    written, ``"<first team>" = [1, 6]``, its # deleted, gave that team two byes no
    card or sheet gave, and five F8 errors on the demo season.
    """
    declared = config.competition(comp.id)
    stated = dict(declared.byes) if declared else {}
    worked_out: dict[str, list[str]] = {}
    for number in comp.round_order:
        for slug in comp.rounds[number].byes:
            worked_out.setdefault(comp.team_name(slug), []).append(number)

    lines = ["", f"[competitions.{toml_key(comp.id)}.byes]",
             "# Which rounds each team sits out, as your association's sheet has",
             "# them. Only the ones this program cannot see for itself: a round",
             "# with cards and one team missing is already worked out, given the",
             "# teams list above. A week the whole grade rests is not.",
             *_comment_flow("Each line is a team's name in double quotes, as the "
                            "teams list above spells it, then = and the rounds it "
                            "sits out in square brackets, as the sheet has them; "
                            "config.example.toml has one worked through.")]
    for team in sorted(stated):
        lines.extend(_bye_lines(team, stated[team]))
    inferred = sorted(set(worked_out) - set(stated))
    if inferred:
        # Said once, above the group, rather than in a trailing comment on each line. A
        # club with long team names and a long season had the note pushed off the edge
        # by the very thing it was explaining, and a note that only fits sometimes is
        # worse than one that is always in the same place.
        lines.append("# Already worked out from the cards. Here to confirm against"
                     " your sheet,")
        lines.append("# not to type out again:")
    for team in inferred:
        lines.extend(_bye_lines(team, worked_out[team], indent="# "))
    return lines


def _bye_lines(team: str, numbers, *, indent: str = "") -> list[str]:
    """One team's line of the byes table, its rounds written as a club types them.

    The loader keeps a round as text, as it has to -- a round can be ``"SF"`` -- and
    the byes written back from it came out ``["4"]`` where the club had typed ``[4]``.
    So a round that is a whole number is written as one, and anything else as the
    string it was: ``"SF"`` stays a string, and so does ``"04"``, which no TOML
    integer can spell and which is therefore what the club typed. Whether it is a
    number is ``competition.round_number``'s to say, as it is for the round order
    and F11, where it asked ``int`` on its own and raised on a round of more digits
    than ``int`` reads (4.151).
    """
    from .competition import round_number
    weeks = [round_number(n) for n in numbers]
    rounds = [week if week is not None and str(week) == n else n
              for n, week in zip(numbers, weeks)]
    return toml_lines(f"{toml_key(team)} = {toml_value(rounds)}", width=WIDTH,
                      indent=indent)


def _washouts_lines(comp_id: str, declared: CompetitionConfig | None) -> list[str]:
    """``[competitions.<id>.washouts]`` as the club typed it, or nothing (4.535).

    Kept, as the byes are, because ``--discover --force`` drops the draw and keeps
    every table a club typed: a rewrite that lost this would take the rounds it
    names out of the rounds played. Written by round, in the order the season
    runs, each fixture as the loader gave it back, so one typed without its outer
    brackets comes back with them, and a line that said nothing was washed out
    does not come back at all. No template where there is none: a washout is
    rare, and the README, the example file and the run that meets a round with no
    card say how one is typed. In the order of the declared draw, which places a
    final it names its own way (4.71).
    """
    washouts = declared.washouts if declared else {}
    if not washouts:
        return []
    from .competition import round_sort_key
    draw = {rnd.number: rnd for rnd in declared.rounds}
    lines = ["", f"[competitions.{toml_key(comp_id)}.washouts]"]
    for number in sorted(washouts, key=lambda label: round_sort_key(label, draw)):
        fixtures = washouts[number]
        # A key is text however it is typed, so `9` and `"9"` load alike, and the
        # round goes back bare where TOML allows it.
        value = toml_value([list(pair) for pair in fixtures]) if fixtures else "true"
        lines.extend(toml_lines(f"{toml_key(number)} = {value}", width=WIDTH))
    return lines


def _expected_lines(config: Config, comp, names: list[str]) -> list[str]:
    """``[competitions.<id>.expected]``: the club's sheet as typed, or one to fill in.

    The template is the byes block's pattern, a live heading over commented lines,
    and it carries no figure: the check is worth something only because the
    figures in it are the association's, and one this program wrote in would be
    its own ladder agreeing with itself. Uncommented as written, every figure is
    blank, which the loader reads as no table.
    """
    declared = config.competition(comp.id)
    typed = _sheet_lines(comp.id, declared.expected if declared else None)
    if typed:
        return typed
    lines = ["", f"[competitions.{toml_key(comp.id)}.expected]"]
    lines.extend(_comment_flow(
        "Your association's published sheet, so each build can check this "
        "dashboard against it. Copy each team's points and matches played off the "
        "sheet, set after_round to the last regular round the sheet covers, and "
        "remove the # from that line and from each team you fill in. A team left "
        "out is not checked. Type the sheet's own figures: this dashboard's would "
        "agree with it and check nothing."))
    lines.append('# after_round = ""')
    for name in names:
        lines.extend(toml_lines(f'{toml_key(name)} = {{ points = "", played = "" }}',
                                width=WIDTH, indent="# "))
    return lines


def _sheet_lines(comp_id: str, sheet: ExpectedSheet | None) -> list[str]:
    """A typed sheet table, written back live, in the order and keys it was typed.

    Each figure is written as TOML that loads back as the text it was read as, which
    is what the check compares: ``after_round`` and ``played`` as whole numbers, and
    points through :func:`_sheet_points`. Nothing for a sheet with no line, which
    loads as no table whatever else it says.
    """
    if sheet is None or not sheet.lines:
        return []
    lines = ["", f"[competitions.{toml_key(comp_id)}.expected]",
             f"after_round = {sheet.after_round}"]
    for line in sheet.lines:
        figures = ([f"points = {_sheet_points(line.points)}"] if line.points else [])
        if line.played is not None:
            figures.append(f"played = {line.played}")
        lines.extend(toml_lines(f"{toml_key(line.key)} = {{ {', '.join(figures)} }}",
                                width=WIDTH))
    return lines


def _sheet_points(text: str) -> str:
    """Sheet points as TOML: bare if TOML reads the bare figure back as *text*.

    ``12.0`` is written bare, since a float's ``repr`` gives it back, and ``"10.90"``
    and ``"07"`` quoted: bare, one loads as 10.9 and TOML refuses the other. Not
    :func:`toml_value`, whose ``:g`` writes 12.0 as 12, a claim to no places where
    the club typed one.
    """
    try:
        bare = repr(float(text)) if "." in text else str(int(text))
    except ValueError:          # digits past int()'s 4300-digit limit
        bare = ""
    return text if bare == text else toml_string(text)


def _rule_lines(declared: CompetitionConfig | None) -> list[str]:
    """The ``[rules]`` settings a competition overrides, written back live.

    Every one of RULES_SETTINGS a section set, and nothing for one it left to
    ``[rules]``. They were not written at all, and a Tuesday night declared
    ``scoring = "simple"`` went back to the preset on the next run: the same
    cards, a different ladder, and exit 0.
    """
    if declared is None:
        return []
    set_here = [(name, getattr(declared, name)) for name in RULES_SETTINGS
                if getattr(declared, name) not in (None, "", ())]
    if not set_here:
        return []
    lines = ["# Where this competition's rules differ from those under [rules]."]
    for name, value in set_here:
        lines.extend(toml_lines(f"{name} = {toml_value(value)}", width=WIDTH))
    return lines


class Offer(NamedTuple):
    """A line the cards give one team, as the writer offers it behind a ``#``.

    ``key`` is the line's key: the club's own where its table has a line for the
    team, and the team's name on the page where it has none. ``value`` is what the
    cards give, and ``instead`` the club's value it differs from, ``None`` where
    the club gives none. ``named`` is whether that value names anybody: a captain
    typed as ``""`` names nobody, so the summary counts the line as one the club
    names none in, and still shows it, since the offer goes in its place.
    """
    key: str
    value: object
    instead: object = None
    named: bool = True


def _keyed(comp: "Competition", table: dict) -> dict[str, str]:
    """Each team of *comp* that a table keyed by team name has a line for: its
    slug, and the key. Read by the rule discovery reads those tables by, any key
    that slugs to the team's slug or its name's, so the line offered beside is the
    line that was read; the first such key, as there."""
    keyed: dict[str, str] = {}
    for key in sorted(table):
        team = comp.team_by_name(key)
        if team is not None:
            keyed.setdefault(team.slug, key)
    return keyed


def captain_offers(comp: "Competition",
                   declared: CompetitionConfig | None) -> list[Offer]:
    """The captain the (C) marks give each team where the club names none, or
    another: the one rule the writer writes them by and the proposal's summary
    counts them by. A declared captain the marks also name is offered nothing."""
    stated = declared.captains if declared else {}
    keyed = _keyed(comp, stated)
    offers = []
    for slug in sorted(comp.teams):
        team = comp.teams[slug]
        key = keyed.get(slug)
        # Where the club names nobody, the captain is the marks'.
        if key is None and team.captain and not team.captain_declared:
            offers.append(Offer(team.name, _player_named(team, team.captain)))
        elif key is not None and team.captain_marked and (
                team.captain != team.captain_marked or not team.captain_declared):
            offers.append(Offer(key, _player_named(team, team.captain_marked),
                                stated[key], named=bool(team.captain_declared)))
    return offers


def _player_named(team, slug: str) -> str:
    """A player of *team* by the name the page gives him, else his slug."""
    player = team.player(slug)
    return player.name if player else slug


def court_offers(comp: "Competition",
                 declared: CompetitionConfig | None) -> list[Offer]:
    """The court each team's home cards print most, where the club declares none
    or another: as :func:`captain_offers` is for the captains."""
    stated = declared.courts if declared else {}
    keyed = _keyed(comp, stated)
    offers = []
    for slug in sorted(comp.teams):
        team = comp.teams[slug]
        court, key = team.court_printed, keyed.get(slug)
        if court is None:
            continue
        if key is None:
            offers.append(Offer(team.name, court))
        elif stated[key] != court:
            offers.append(Offer(key, court, stated[key]))
    return offers


def _courts_printed(team) -> str:
    """``court 2 on 2 cards and court 4 on 1``: each court a team's home cards
    print, and on how many of them, the one printed most first."""
    (court, cards), *rest = team.courts_printed
    return in_words([f"court {court} on {cards} card{'' if cards == 1 else 's'}",
                     *(f"court {other} on {more}" for other, more in rest)])


def _courts_lines(comp: "Competition", declared: CompetitionConfig | None,
                  names: list[str]) -> list[str]:
    """The ``courts`` table: the club's lines live, and the cards' behind a ``#``.

    Its comment said "Courts and codes are printed on no card", and so the table
    offered no court the cards gave, and the court 1 of the first team, which no
    card gave. Every card prints the court it was played on, and a team's home
    cards print its home court, so each team with a home card is offered the court
    those cards print most (:attr:`~matchcentre.competition.Team.court_printed`),
    in the words the captains are offered in. Where they print more than one, a
    comment says each and how often, and a tie is offered no court; a team with no
    home card is offered nothing, and a court the club declares is written back
    live and not offered again, with the cards' court below it where that differs.
    A competition no card of which printed a court keeps the example line.
    """
    courts = dict(declared.courts) if declared else {}
    lines = ["", f"[competitions.{toml_key(comp.id)}.courts]"]
    if not any(team.courts_printed for team in comp.teams.values()):
        lines.append("# Each team's home court, if your club plays on numbered courts.")
        for team_name in sorted(courts):
            lines.append(f"{toml_key(team_name)} = {courts[team_name]}")
        if not courts:
            for team_name in sorted(names)[:1]:
                lines.append(f"# {toml_key(team_name)} = 1")
        return lines
    if courts:
        lines.extend(_comment_flow(
            "As this file had them, with any others read from the court printed on "
            "each team's home cards written with a # in front. Remove the # to use "
            "one."))
    else:
        lines.extend(_comment_flow(
            "Read from the court printed on each team's home cards. Correct a line "
            "if it reads wrongly and remove the # to use it."))
    entries: dict[str, list[str]] = {
        key: [f"{toml_key(key)} = {toml_value(courts[key])}"] for key in courts}
    for offer in court_offers(comp, declared):
        team = comp.team_by_name(offer.key)
        line = f"# {toml_key(offer.key)} = {offer.value}"
        if offer.instead is not None:
            entries[offer.key].extend([*_comment_flow(
                f"The home cards of {team.name} print {_courts_printed(team)}. To "
                f"use court {offer.value}, delete the line above and the # below."),
                line])
        else:
            entries[offer.key] = [*(_comment_flow(
                f"The home cards of {team.name} print {_courts_printed(team)}.")
                if len(team.courts_printed) > 1 else []), line]
    keyed = _keyed(comp, courts)
    for slug in sorted(comp.teams):
        team = comp.teams[slug]
        if team.courts_printed and team.court_printed is None and slug not in keyed:
            entries[team.name] = _comment_flow(
                f"The home cards of {team.name} print {_courts_printed(team)}, so "
                f"no court is offered for it.")
    for key in sorted(entries):
        lines.extend(entries[key])
    return lines


def _table_lines(comp_id: str, name: str, entries: dict, *comment: str) -> list[str]:
    """``[competitions.<id>.<name>]`` with the club's entries, or nothing at all."""
    if not entries:
        return []
    lines = ["", f"[competitions.{toml_key(comp_id)}.{name}]", *comment]
    for key in sorted(entries):
        lines.append(f"{toml_key(key)} = {toml_value(entries[key])}")
    return lines


def _declared_section(declared: CompetitionConfig, finals: str = "") -> list[str]:
    """A section no card was filed under this run, written back as the club had it.

    Everything it declared but the draw, which ``--discover`` drops from every
    section: keeping it here alone would make what the flag drops depend on whether
    a card had arrived. Each value is written only if it was set, so the section
    loads back as it was read.
    """
    key = toml_key(declared.id)
    lines = [f"[competitions.{key}]"]
    lines.extend(_comment_flow(
        "No card was filed under this section this run, so it is written back as "
        "you had it" + (", less the draw you declared in it." if declared.rounds
                        else ".")))
    for name in ("label", "day", "division"):
        if getattr(declared, name):
            lines.append(f"{name} = {toml_value(getattr(declared, name))}")
    if declared.season_key:
        lines.extend(_season_key_lines(declared.season_key))
    if declared.season:
        lines.append(f"season = {toml_value(declared.season)}")
    if declared.length_by_labels_for(finals) is not None:
        lines.append(f"rounds_total = {declared.length_by_labels_for(finals)}")
    if declared.teams:
        lines.extend(toml_lines(f"teams = {toml_value(declared.teams)}", width=WIDTH))
    lines.extend(_rule_lines(declared))
    lines.extend(_table_lines(declared.id, "captains", declared.captains))
    lines.extend(_table_lines(declared.id, "courts", declared.courts))
    lines.extend(_table_lines(declared.id, "codes", declared.codes))
    if declared.byes:
        lines.extend(("", f"[competitions.{key}.byes]"))
        for team in sorted(declared.byes):
            lines.extend(_bye_lines(team, declared.byes[team]))
    lines.extend(_washouts_lines(declared.id, declared))
    lines.extend(_sheet_lines(declared.id, declared.expected))
    lines.append("")
    return lines
