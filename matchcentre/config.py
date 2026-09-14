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

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import format as fmt_mod
from . import scoring
from .slugs import slugify, team_slug

__all__ = ["Config", "CompetitionConfig", "RoundConfig", "ConfigError",
           "load", "render", "DEFAULT_PATH", "SEASON_MODES"]

DEFAULT_PATH = "config.toml"

SEASON_MODES = ("single", "multi")


class ConfigError(ValueError):
    """The config file exists but cannot be honoured."""


@dataclass(frozen=True, slots=True)
class RoundConfig:
    """One declared round. Everything but ``number`` is optional."""

    number: str
    date: str = ""
    fixtures: tuple[tuple[str, str], ...] = ()
    bye: str = ""
    finals: bool = False
    note: str = ""


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
    scoring: str = ""
    sets: str = ""
    format: str = ""
    slots: tuple[str, ...] = ()
    min_sets_for_result: int | None = None

    @property
    def teams_declared(self) -> bool:
        return bool(self.teams)


@dataclass(frozen=True, slots=True)
class Config:
    """The whole file, normalised, with defaults filled in.

    ``exists`` is false when no file was found. Callers use it to decide whether
    this is a first run — the run that writes the file — rather than guessing from
    whether the contents look empty.
    """

    path: Path
    exists: bool = False
    club_name: str = ""
    strip_team_prefix: str = ""
    seasons_mode: str = "single"
    title: str = ""
    scoring: str = scoring.DEFAULT_RULESET
    sets: str = fmt_mod.DEFAULT_SET_RULE
    format: str = ""
    slots: tuple[str, ...] = ()
    min_sets_for_result: int | None = None
    season_labels: dict[str, str] = field(default_factory=dict)
    team_names: dict[str, str] = field(default_factory=dict)
    player_names: dict[str, str] = field(default_factory=dict)
    aliases: dict[str, str] = field(default_factory=dict)
    competitions: dict[str, CompetitionConfig] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()

    # --- rules ----------------------------------------------------------

    def ruleset_for(self, comp: CompetitionConfig | None = None) -> scoring.Ruleset:
        name = (comp.scoring if comp and comp.scoring else self.scoring)
        return scoring.ruleset(name)

    def set_rule_for(self, comp: CompetitionConfig | None = None) -> fmt_mod.SetRule:
        name = (comp.sets if comp and comp.sets else self.sets)
        return fmt_mod.set_rule(name)

    def declared_format(self, comp: CompetitionConfig | None = None
                        ) -> fmt_mod.MatchFormat | None:
        """The format the club pinned, or ``None`` to read it off the cards.

        A declared format is checked against what each card describes and a
        disagreement stops the run. Neither reading is allowed to win quietly: a
        card with a row missed is indistinguishable from a club that changed
        format, and the two need opposite responses.
        """
        rule = self.set_rule_for(comp)
        minimum = ((comp.min_sets_for_result if comp else None)
                   or self.min_sets_for_result)
        slots = (comp.slots if comp and comp.slots else self.slots)
        if slots:
            return fmt_mod.from_slot_spec(slots, rule=rule,
                                          min_sets_for_result=minimum)
        name = (comp.format if comp and comp.format else self.format)
        if name:
            return fmt_mod.match_format(name, rule=rule, min_sets_for_result=minimum)
        return None

    # --- names ----------------------------------------------------------

    def team_display(self, slug: str) -> str | None:
        return self.team_names.get(slug)

    def player_display(self, slug: str) -> str | None:
        return self.player_names.get(slug)

    def competition(self, comp_id: str) -> CompetitionConfig | None:
        return self.competitions.get(comp_id)

    def season_label(self, key: str) -> str:
        return self.season_labels.get(key, "")

    @property
    def multi_season(self) -> bool:
        return self.seasons_mode == "multi"


# --- reading ----------------------------------------------------------------


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
        raw = tomllib.loads(target.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{target}: not valid TOML — {exc}") from None
    except OSError as exc:
        raise ConfigError(f"{target}: cannot be read — {exc}") from None
    return _from_raw(raw, target)


_TOP_LEVEL = {"club", "build", "rules", "seasons", "team_names", "player_names",
              "aliases", "competitions"}


def _from_raw(raw: dict, path: Path) -> Config:
    warnings: list[str] = []

    for key in sorted(set(raw) - _TOP_LEVEL):
        # A mistyped section does nothing at all, which is the kind of silence
        # this project exists to avoid. Name it on every run.
        warnings.append(f"[{key}] is not a section this program understands "
                        f"and was ignored")

    club = _table(raw, "club", warnings)
    build = _table(raw, "build", warnings)
    rules = _table(raw, "rules", warnings)

    _reject_unknown(club, {"name", "strip_team_prefix"}, "club", warnings)
    _reject_unknown(build, {"seasons", "title"}, "build", warnings)
    _reject_unknown(rules, {"scoring", "sets", "format", "slots",
                            "min_sets_for_result"}, "rules", warnings)

    mode = str(build.get("seasons", "single")).strip().lower()
    if mode not in SEASON_MODES:
        raise ConfigError(
            f"{path}: [build] seasons must be one of {SEASON_MODES}, got {mode!r}")

    prefix = str(club.get("strip_team_prefix", "") or "")

    scheme = str(rules.get("scoring", scoring.DEFAULT_RULESET) or
                 scoring.DEFAULT_RULESET)
    set_name = str(rules.get("sets", fmt_mod.DEFAULT_SET_RULE) or
                   fmt_mod.DEFAULT_SET_RULE)
    # Fail here rather than three modules later, while we can still name the file.
    try:
        scoring.ruleset(scheme)
        fmt_mod.set_rule(set_name)
    except (scoring.ScoringError, fmt_mod.FormatError) as exc:
        raise ConfigError(f"{path}: [rules] {exc}") from None

    config = Config(
        path=path,
        exists=True,
        club_name=str(club.get("name", "") or ""),
        strip_team_prefix=prefix,
        seasons_mode=mode,
        title=str(build.get("title", "") or ""),
        scoring=scheme,
        sets=set_name,
        format=str(rules.get("format", "") or ""),
        slots=tuple(str(s) for s in rules.get("slots", ()) or ()),
        min_sets_for_result=_optional_int(rules.get("min_sets_for_result"),
                                          "[rules] min_sets_for_result", path),
        season_labels=_str_map(raw.get("seasons"), "seasons", warnings),
        team_names=_name_map(raw.get("team_names"), "team_names", prefix, warnings),
        player_names=_name_map(raw.get("player_names"), "player_names", None,
                               warnings),
        aliases=_alias_map(raw.get("aliases"), warnings),
        competitions=_competitions(raw.get("competitions"), path, prefix, warnings),
        warnings=tuple(warnings),
    )
    # Validate the declared format now, for the same reason.
    try:
        config.declared_format()
    except fmt_mod.FormatError as exc:
        raise ConfigError(f"{path}: [rules] {exc}") from None
    return config


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
                        f"understands and was ignored")


def _optional_int(value: object, label: str, path: Path) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ConfigError(f"{path}: {label} must be a whole number, "
                          f"got {value!r}") from None


def _str_map(value: object, name: str, warnings: list[str]) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        warnings.append(f"[{name}] should be a section of key = \"value\" "
                        f"pairs; ignored")
        return {}
    return {str(k): str(v) for k, v in value.items()}


def _name_map(value: object, name: str, prefix: str | None,
              warnings: list[str]) -> dict[str, str]:
    """Display-name overrides, keyed by slug.

    Forgiving about the key: discovery writes slugs, but a club member editing by
    hand will reasonably type the name as it appears on the card, so both are
    registered.
    """
    plain = _str_map(value, name, warnings)
    out: dict[str, str] = {}
    for key, display in plain.items():
        try:
            out[slugify(key)] = display
            if prefix:
                out[team_slug(key, strip_prefix=prefix)] = display
        except ValueError:
            warnings.append(f"[{name}] key {key!r} has no letters or digits "
                            f"in it and was ignored")
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


def _competitions(value: object, path: Path, prefix: str,
                  warnings: list[str]) -> dict[str, CompetitionConfig]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"{path}: [competitions] should be a section")
    known = {"label", "day", "division", "season", "season_key", "rounds_total",
             "teams", "captains", "courts", "codes", "rounds", "scoring", "sets",
             "format", "slots", "min_sets_for_result"}
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
            season_key=str(table.get("season_key", "") or ""),
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
            scoring=str(table.get("scoring", "") or ""),
            sets=str(table.get("sets", "") or ""),
            format=str(table.get("format", "") or ""),
            slots=tuple(str(s) for s in table.get("slots", ()) or ()),
            min_sets_for_result=_optional_int(
                table.get("min_sets_for_result"),
                f"[competitions.{comp_id}] min_sets_for_result", path),
        )
    return out


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
        _reject_unknown(entry, {"number", "date", "fixtures", "bye", "finals",
                                "note"},
                        f"competitions.{comp_id}.rounds", warnings)
        number = str(entry.get("number", "") or "").strip()
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
            date=str(entry.get("date", "") or ""),
            fixtures=tuple(fixtures),
            bye=str(entry.get("bye", "") or ""),
            finals=bool(entry.get("finals", False)),
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


def toml_string(value: str) -> str:
    escaped = (value.replace("\\", "\\\\").replace('"', '\\"')
               .replace("\n", "\\n").replace("\t", "\\t"))
    return f'"{escaped}"'


def toml_key(key: str) -> str:
    """Bare where it can be, quoted where it must be."""
    if key and all(c.isalnum() or c in "-_" for c in key):
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


HEADER = """\
# matchcentre-dashboard
#
# Written for you from the scorecards in data/. Everything here is optional: the
# dashboard already works. Filling it in makes the dashboard say what you know
# instead of what this program could work out on its own.
#
# Nothing in this file can change a score. Results only ever come from the
# scorecards. This file supplies names, captains, season labels, the list of
# teams, the draw, and which scoring scheme your competition uses.
#
# Edit it, then run `python3 build.py` again.
"""


def render(config: Config, *, competitions: object = (),
           club_name: str = "", cards: int = 0) -> str:
    """The config file, filled in from a discovery pass.

    ``competitions`` is a sequence of :class:`~matchcentre.competition.Competition`.
    Values this program merely *worked out* are written commented, so adopting one
    is a deliberate act — deleting a ``#``. That is not friction for its own sake:
    an uncommented ``teams`` list closes the team roster, which switches on bye
    handling and turns an unrecognised team on a card into an error. Both are
    right once a human has checked the list and wrong before.
    """
    comps = list(competitions)
    lines: list[str] = [HEADER]

    lines.append("[club]")
    lines.append(f"name = {toml_value(config.club_name or club_name)}")
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
    lines.append(f"title = {toml_value(config.title or default_title(club_name))}")
    lines.append("")

    lines.append("[rules]")
    lines.append("# How your competition turns sets into ladder points. Getting this")
    lines.append("# wrong reorders the ladder, so it is printed on every run and")
    lines.append("# shown on the dashboard. Options:")
    for name in sorted(scoring.RULESETS):
        lines.append(f"#   {name:<12} {scoring.RULESETS[name].describe()}")
    lines.append(f"scoring = {toml_value(config.scoring)}")
    lines.append("# When a set is finished. No scorecard can tell us this — 6-3 looks")
    lines.append("# the same whether the set was played to 6 or to 8. Options:")
    for name in sorted(fmt_mod.SET_RULES):
        lines.append(f"#   {name:<18} {fmt_mod.SET_RULES[name].describe()}")
    lines.append(f"sets = {toml_value(config.sets)}")
    lines.append("# The shape of a match is read from your scorecards, so you only")
    lines.append("# need these to pin it down or to override what was read.")
    lines.append(f"# format = {toml_value(config.format or fmt_mod.DEFAULT_FORMAT)}"
                 f"   # {', '.join(sorted(fmt_mod.FORMATS))}")
    lines.append('# slots = ["doubles", "singles", "singles", "doubles"]')
    lines.append("# min_sets_for_result = 3   # sets that must finish before a "
                 "result counts")
    lines.append("")

    if config.multi_season or config.season_labels:
        lines.append("[seasons]")
        lines.append("# Pretty names for the folders under data/.")
        keys = dict(config.season_labels)
        for comp in comps:
            if comp.season_key:
                keys.setdefault(comp.season_key, comp.season or comp.season_key)
        for key in sorted(keys):
            lines.append(f"{toml_key(key)} = {toml_value(keys[key])}")
        if not keys:
            lines.append('# "2026-winter" = "2026 Winter Season"')
        lines.append("")

    lines.extend(_names_section(config, comps))
    lines.extend(_aliases_section(config))

    for comp in comps:
        lines.extend(_competition_section(config, comp))

    if not comps:
        lines.append("# No scorecards were found under data/, so there is nothing")
        lines.append("# to describe yet. Add your PDFs and run build.py again.")
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

    Public, and public for a reason: :mod:`matchcentre.site` needs the same
    answer. When each module had its own version of this line, the first build
    headed the page "<club> dashboards" and the config it wrote said "<club>
    season dashboards", so the second build silently retitled the site -- a diff
    with no cause a club could point at.
    """
    return f"{club_name} season dashboards" if club_name else "Season dashboards"


def _names_section(config: Config, comps: list) -> list[str]:
    """Display-name overrides, pre-filled with every team we had to guess at."""
    from .competition import Provenance

    lines = ["[team_names]",
             "# Scorecards shout team names, so these are this program's best",
             "# guess at how you write them. Fix any that read wrongly."]
    written: dict[str, str] = dict(config.team_names)
    for comp in comps:
        for slug, team in sorted(comp.teams.items()):
            if team.name_source is Provenance.DECLARED:
                continue
            written.setdefault(slug, team.name)
    for slug in sorted(written):
        lines.append(f"{toml_key(slug)} = {toml_value(written[slug])}")
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


def _competition_section(config: Config, comp) -> list[str]:
    from .competition import Provenance

    declared = config.competition(comp.id)
    header = f"[competitions.{toml_key(comp.id)}]"
    lines = [header]
    lines.append(f"label = {toml_value(comp.display_label)}")
    if comp.day:
        lines.append(f"day = {toml_value(comp.day)}")
    if comp.division:
        lines.append(f"division = {toml_value(comp.division)}")

    season_line = f"season = {toml_value(comp.season)}"
    if comp.provenance_of("season") is Provenance.DECLARED and comp.season:
        lines.append(season_line)
    else:
        lines.append("# What your association calls this season. Scorecards do not")
        lines.append("# reliably print it, so the dashboard leaves it out until you")
        lines.append("# say. Remove the # to use it.")
        lines.append(f"# season = {toml_value(comp.season or '2026 Season 2')}")

    played = len(comp.rounds)
    if comp.rounds_total is not None and declared and declared.rounds_total:
        lines.append(f"rounds_total = {comp.rounds_total}")
    else:
        lines.append("# Rounds in the full season. Without it the dashboard says")
        lines.append(f"# \"{played} rounds played\" and shows no percentage, rather")
        lines.append("# than implying the season is over. Remove the # to use it.")
        lines.append(f"# rounds_total = {max(played, 1)}")

    names = [comp.teams[slug].name for slug in sorted(comp.teams)]
    if declared and declared.teams_declared:
        lines.append(f"teams = {toml_value(names)}")
    else:
        lines.append("# The full list of teams in this competition, as read from")
        lines.append("# the cards. Check it, then remove the # — that tells the")
        lines.append("# program the list is complete, which lets it handle byes and")
        lines.append("# flag a misspelt team instead of inventing a new one.")
        lines.append(f"# teams = {toml_value(names)}")

    captains = {}
    for slug in sorted(comp.teams):
        team = comp.teams[slug]
        if not team.captain:
            continue
        player = team.player(team.captain)
        captains[team.name] = player.name if player else team.captain
    lines.append("")
    lines.append(f"[competitions.{toml_key(comp.id)}.captains]")
    if captains:
        lines.append("# Read from the (C) marks on the scorecards. Correct as needed.")
        for team_name in sorted(captains):
            lines.append(f"{toml_key(team_name)} = {toml_value(captains[team_name])}")
    else:
        lines.append("# No (C) marks were found on the cards. Add captains here if")
        lines.append("# you want them shown.")
        for team_name in sorted(names):
            lines.append(f"# {toml_key(team_name)} = \"\"")

    courts = {comp.teams[s].name: comp.teams[s].home_court
              for s in sorted(comp.teams) if comp.teams[s].home_court}
    lines.append("")
    lines.append(f"[competitions.{toml_key(comp.id)}.courts]")
    lines.append("# Each team's home court, if your club plays on numbered courts.")
    for team_name in sorted(courts):
        lines.append(f"{toml_key(team_name)} = {courts[team_name]}")
    if not courts:
        for team_name in sorted(names)[:1]:
            lines.append(f"# {toml_key(team_name)} = 1")

    lines.append("")
    lines.append("# Your published draw, if you have one. Declaring it lets the")
    lines.append("# dashboard show fixtures that have not been played yet, and is")
    lines.append("# the only way byes can be credited correctly.")
    lines.append(f"# [[competitions.{toml_key(comp.id)}.rounds]]")
    lines.append('# number = "1"')
    lines.append('# date = "2026-08-18"')
    example = names[:2] if len(names) >= 2 else ["Home Team", "Away Team"]
    lines.append(f"# fixtures = [{toml_value(example)}]")
    lines.append('# bye = ""')
    lines.append('# finals = false')
    lines.append("")
    return lines
