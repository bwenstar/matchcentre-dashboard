"""Everything that can be checked about a season, checked and named.

Nothing here raises. The dashboard's job is to render the season *and* show what
is wrong with it, so one bad card must not take the page down: every problem comes
back as an :class:`Issue` and lands in ``season.json``'s ``validation`` block,
grouped by the rule that found it.

Two disciplines run through the whole module.

**Severity is a claim about the numbers.** An ``error`` means the figures are, or
may be, wrong: the card's own ``TOTAL`` row contradicts its set rows, two cards
claim the same fixture, a finals round is feeding the ladder. A ``warning`` means
something deserves a human's eye but the numbers stand: a fill-in, a roster the
card printed differently from the club's records, a ladder that is honest but not
yet settled.

**Absence of evidence is never silence.** This is the failure the whole project
exists to prevent, and a validator is where it is most tempting: a check that
cannot run because its input is missing produces a warning saying so, never an
empty result that reads as a pass. Every ``F``-numbered rule below is one check,
and each of them was a real, measured, silent wrong answer in the predecessor.

The card-level checks recompute the totals and the result verb from the card's own
set rows rather than trusting the :class:`~matchcentre.card.CrossCheck` records the
parser attached. That is deliberate duplication: those records are the parser
grading its own work, and this module is the second opinion.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from .card import Card, ParsedSet, Rejection
from .competition import (DRAW_DECLARED, Club, Competition, Provenance,
                          is_finals_label, round_sort_key)
from .format import SetRule, completed, plausible, set_winner
from .model import SIDES, Match, MatchStatus, SetStatus, other_side
from .scoring import ScoringError
from .slugs import near_misses
from .stats import chronological, points_ledger, with_byes

__all__ = ["Issue", "Report", "validate", "ERROR", "WARNING"]

ERROR = "error"
WARNING = "warning"

# The card prints a verb between the team names; the parser passes it through
# untouched for us to reconcile against the computed result. Compared against a key
# with punctuation stripped, so "DEF. BY" and "DEF BY" are one entry.
_HOME_VERBS = frozenset({"defeats", "def", "defeated", "wins", "won", "beats"})
_AWAY_VERBS = frozenset({"def by", "defeated by", "loses to", "lost to",
                         "defeats by"})
_DRAW_VERBS = frozenset({"draws with", "draw", "draws", "drawn", "ties with",
                         "tie", "tied"})

# Statuses that carry no scoreline. Their cards are still checked for identity and
# duplication, but not for set contents, which they do not have.
_UNSCORED = (MatchStatus.BYE, MatchStatus.CANCELLED, MatchStatus.UNPLAYED)


@dataclass(frozen=True, slots=True)
class Issue:
    """One finding, named after the rule that found it.

    ``rule`` is an ``F``-number -- a stable id for one rule about what may be
    inferred from a scorecard -- or a kebab-case check name, and it is what the
    dashboard groups by — so a reader sees "three cards failed
    F6" rather than a flat list. ``subject`` is what the issue is about (a match
    label, a team, a path) and ``detail`` carries the supporting evidence: the
    source file, or both of two disagreeing readings.
    """

    rule: str
    severity: str
    message: str
    subject: str = ""
    detail: str = ""


@dataclass(frozen=True, slots=True)
class Report:
    """The findings, already split by severity. ``ok`` gates the exit status."""

    errors: tuple[Issue, ...]
    warnings: tuple[Issue, ...]

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def issues(self) -> tuple[Issue, ...]:
        """Everything found, errors first. What a caller renders as one list."""
        return self.errors + self.warnings


@dataclass(frozen=True, slots=True)
class _Context:
    """Everything the checks share, so each one is a pure function of it."""

    matches: tuple[Match, ...]
    competition: Competition
    club: Club | None
    cards: tuple[Card, ...]
    rejected: tuple[Rejection, ...]

    @property
    def rule(self) -> SetRule:
        return self.competition.fmt.set_rule

    @property
    def playable(self) -> list[Match]:
        """Matches with scores on them, which is what the set-level checks are for.

        A bye, a washout or a card with no usable scores is a legitimate match with
        no sets, no totals and no roster; running the set and roster invariants over
        it would report a scoreline's absence as a scoreline defect. Those matches
        are not skipped silently — :func:`_check_status` names every one.
        """
        return [m for m in chronological(self.matches) if m.status not in _UNSCORED]


def validate(matches: Sequence[Match], competition: Competition, *,
             club: Club | None = None, cards: Sequence[Card] = (),
             rejected: Sequence[Rejection] = ()) -> Report:
    """Run every check. Findings come back in a deterministic order.

    ``cards`` are the parsed scorecards behind ``matches`` and ``rejected`` the
    PDFs that could not be read; both are optional, and when either is absent the
    checks that needed it say so rather than passing quietly.

    ``club`` only widens what can be *explained*: with it, a player who filled in
    from another competition is named as such instead of being reported as an
    unknown spelling. Its absence never changes a verdict.
    """
    context = _Context(matches=tuple(matches), competition=competition, club=club,
                       cards=tuple(cards), rejected=tuple(rejected))
    issues: list[Issue] = []
    for check in (
        _check_inputs,              # what could not be checked at all
        _check_status,              # matches carrying no result, named
        _check_cards_accounted,     # F4
        _check_cross_checks,        # F6
        _check_totals,              # totals-mismatch
        _check_verb,                # verb-mismatch
        _check_competition_id,      # wrong-competition
        _check_self_play,           # team-plays-itself
        _check_team_list,           # F8
        _check_duplicates,          # F9
        _check_sets,                # set counts and games against the format
        _check_set_players,         # set rows against the rosters
        _check_player_teams,        # one player, one team, one round
        _check_ratings,             # ratings-missing
        _check_rosters,             # card roster against the registered squad
        _check_draw,                # declared draw vs the cards
        _check_byes,                # a bye credited to a team that played
        _check_rounds_total,        # F3
        _check_finals,              # F11
        _check_ladder,              # F10
    ):
        issues.extend(check(context))
    return Report(
        errors=tuple(i for i in issues if i.severity == ERROR),
        warnings=tuple(i for i in issues if i.severity == WARNING),
    )


# --- helpers ------------------------------------------------------------


def _label(match: Match) -> str:
    return f"R{match.round} {match.home} v {match.away}"


def _where(card: Card) -> str:
    return card.source if card.page == 0 else f"{card.source} page {card.page + 1}"


def _set_finished(parsed: ParsedSet, rule: SetRule) -> bool:
    """Whether one transcribed row counts as a completed set.

    Mirrors :attr:`~matchcentre.model.SetResult.completed` deliberately rather than
    calling it: this runs on the :class:`~matchcentre.card.ParsedSet` straight off
    the page, so the totals and verb checks do not inherit a mistake made while
    turning cards into matches. A forfeited set is complete; a retirement or a
    washout is not, however final its printed games look.
    """
    if parsed.status in (SetStatus.RETIRED, SetStatus.INCOMPLETE):
        return False
    if parsed.status is SetStatus.FORFEIT:
        return True
    return completed(parsed.home_games, parsed.away_games, rule)


def _set_winner_of(parsed: ParsedSet, rule: SetRule) -> str | None:
    """Who won one transcribed set row, by the same logic as ``SetResult.winner``."""
    if parsed.status is SetStatus.FORFEIT:
        # The parser encodes a forfeited set as the games the non-forfeiter is
        # credited, so the plain comparison is still the right one.
        if parsed.home_games == parsed.away_games:
            return None
        return "home" if parsed.home_games > parsed.away_games else "away"
    if not _set_finished(parsed, rule):
        return None
    return set_winner(parsed.home_games, parsed.away_games, rule)


def _card_tally(card: Card, rule: SetRule) -> tuple[int, int, int, int]:
    """``(home_sets, home_games, away_games, away_sets)`` from the set rows."""
    winners = [_set_winner_of(s, rule) for s in card.sets]
    return (winners.count("home"), card.home_games, card.away_games,
            winners.count("away"))


def _verb_side(verb: str) -> str | None:
    # Exact match on a normalised key, deliberately: prefix matching would read the
    # away verb "DEF. BY" as the home verb "DEF".
    key = " ".join("".join(c if c.isalnum() else " " for c in verb.lower()).split())
    if key in _DRAW_VERBS:
        return "draw"
    if key in _AWAY_VERBS:
        return "away"
    if key in _HOME_VERBS:
        return "home"
    return None


def _round_is_finals(competition: Competition, number: str) -> bool:
    rnd = competition.rounds.get(number)
    if rnd is not None:
        return rnd.is_finals
    return is_finals_label(number)


# --- what could not be checked -----------------------------------------


def _check_inputs(ctx: _Context) -> list[Issue]:
    """Say out loud which checks had no input to run against.

    The rule this whole program is built around: absence of evidence recorded as
    evidence is the failure mode of this family of programs. A clean report over no data
    is the purest form of it.
    """
    issues: list[Issue] = []
    comp = ctx.competition
    if not ctx.matches:
        issues.append(Issue(
            rule="no-matches", severity=WARNING,
            message=f"{comp.id} has no matches, so nothing about its results "
                    "could be checked.",
            subject=comp.id))
    if ctx.matches and not ctx.cards:
        issues.append(Issue(
            rule="no-cards", severity=WARNING,
            message="the parsed scorecards were not supplied, so the card-level "
                    "cross-checks (F6), the printed TOTAL row and the result verb "
                    "could not be re-read.",
            subject=comp.id,
            detail=f"{len(ctx.matches)} match(es) validated from their scored form "
                   "alone"))
    if not comp.teams:
        issues.append(Issue(
            rule="no-teams", severity=WARNING,
            message=f"{comp.id} has no team list, so no ladder, roster or "
                    "team-identity check could run.",
            subject=comp.id))
    elif not comp.teams_declared:
        issues.append(Issue(
            rule="F8", severity=WARNING,
            message=f"the team list for {comp.id} was derived from the cards, not "
                    "declared, so an unknown team can only be reported as a "
                    "suspicion. Uncomment `teams` in config.toml to make it an "
                    "error.",
            subject=comp.id,
            detail=", ".join(sorted(comp.teams))))
    if not comp.rounds:
        issues.append(Issue(
            rule="no-rounds", severity=WARNING,
            message=f"{comp.id} has no rounds, so no fixture could be checked "
                    "against a draw.",
            subject=comp.id))
    return issues


def _check_status(ctx: _Context) -> list[Issue]:
    """Matches that produce no result, each one named.

    A cancelled or unplayed match is not a defect, but it is the reason a team's
    counted total is short, and a reader who cannot see it here will look for the
    missing card instead. The forfeit case is the exception and an error: the
    predecessor scored a match flagged forfeit with neither side ticked by paying
    the forfeit-win value to *both* teams.
    """
    issues: list[Issue] = []
    for match in chronological(ctx.matches):
        if match.status is MatchStatus.FORFEIT and match.forfeited_by not in SIDES:
            issues.append(Issue(
                rule="forfeit-unattributed", severity=ERROR,
                message=f"{_label(match)}: the card is flagged as a forfeit but "
                        "neither side's box is ticked, so there is nobody to award "
                        "it to and the match cannot be scored.",
                subject=_label(match), detail=match.source))
        if match.disputed:
            issues.append(Issue(
                rule="disputed", severity=WARNING,
                message=f"{_label(match)}: the card's Match Disputed box is ticked. "
                        "The result is counted as printed until the club says "
                        "otherwise.",
                subject=_label(match), detail=match.source))
        if (match.status is MatchStatus.BYE or match.decided
                or match.status is MatchStatus.FORFEIT):
            continue        # a bye is normal; an unattributed forfeit is said above
        need = match.fmt.min_sets_for_result
        why = (f"only {match.completed_sets} of the {need} sets needed for a result "
               "are complete"
               if match.status is MatchStatus.PLAYED
               else f"recorded as {match.status.value}")
        issues.append(Issue(
            rule="no-result", severity=WARNING,
            message=f"{_label(match)}: {why}, so it is not counted in the ladder.",
            subject=_label(match), detail=match.source))
    return issues


# --- F4: every card is accounted for ------------------------------------


def _check_cards_accounted(ctx: _Context) -> list[Issue]:
    """Rule F4. A PDF that was found and not scored is named, by path and reason.

    An error every time, without exception. A scorecard that vanishes quietly is
    the most expensive failure this tool can have: the ladder stays plausible and a
    team is simply short a match, which nothing downstream can detect.
    """
    issues: list[Issue] = []
    for rejection in sorted(ctx.rejected, key=lambda r: (r.path, r.page)):
        issues.append(Issue(
            rule="F4", severity=ERROR,
            message=f"{rejection.describe()} — found under data/ and not scored.",
            subject=rejection.path,
            detail=rejection.detail or rejection.reason))
    return issues


# --- F6: at least one independent cross-check must agree -----------------


def _check_cross_checks(ctx: _Context) -> list[Issue]:
    """Rule F6. A card must reproduce at least one of its own printed results.

    Zero available cross-checks is a warning: some templates print none, and that
    is a fact about the template, not evidence of a bad parse. A cross-check that
    *disagrees* is an error, and it names both readings, because the predecessor's
    fail-open column grid returned confidently reversed scorelines that nothing
    else caught.
    """
    issues: list[Issue] = []
    for card in ctx.cards:
        failed = card.cross_checks_failed
        for check in failed:
            issues.append(Issue(
                rule="F6", severity=ERROR,
                message=f"{_where(card)}: the {check.name} cross-check disagrees "
                        f"with the set rows, so this card's numbers are not "
                        f"trustworthy.",
                subject=_where(card),
                detail=check.detail or f"{check.name} check failed"))
        if not card.cross_checks_available:
            issues.append(Issue(
                rule="F6", severity=WARNING,
                message=f"{_where(card)}: the card prints no TOTAL row, result verb "
                        "or winner tick, so nothing on it confirms the set rows "
                        "were read correctly.",
                subject=_where(card),
                detail=f"{len(card.sets)} set row(s) read"))
    return issues


# --- the card's own printed totals --------------------------------------


def _check_totals(ctx: _Context) -> list[Issue]:
    issues: list[Issue] = []
    rule = ctx.rule
    for card in ctx.cards:
        if not card.totals.present:
            issues.append(Issue(
                rule="totals-missing", severity=WARNING,
                message=f"{_where(card)}: no TOTAL row was read, so the computed "
                        "games and sets are unverified by it.",
                subject=_where(card)))
            continue
        h_sets, h_games, a_games, a_sets = _card_tally(card, rule)
        printed = card.totals
        # Each cell is compared only when the card printed it: a template that
        # totals games and not sets is common, and treating its missing sets as a
        # zero would manufacture a disagreement.
        computed = {"home sets": h_sets, "home games": h_games,
                    "away games": a_games, "away sets": a_sets}
        stated = {"home sets": printed.home_sets, "home games": printed.home_games,
                  "away games": printed.away_games, "away sets": printed.away_sets}
        wrong = [name for name, value in stated.items()
                 if value is not None and value != computed[name]]
        if wrong:
            issues.append(Issue(
                rule="totals-mismatch", severity=ERROR,
                message=f"{_where(card)}: the printed TOTAL row and the set rows "
                        f"disagree on {', '.join(wrong)}.",
                subject=_where(card),
                detail=f"printed sets {printed.home_sets}-{printed.away_sets} games "
                       f"{printed.home_games}-{printed.away_games}; set rows add to "
                       f"sets {h_sets}-{a_sets} games {h_games}-{a_games}"))
    return issues


# --- the card's own result verb -----------------------------------------


def _check_verb(ctx: _Context) -> list[Issue]:
    issues: list[Issue] = []
    rules = ctx.competition.rules
    fmt = ctx.competition.fmt
    for card in ctx.cards:
        if not card.verb:
            continue
        expected = _verb_side(card.verb)
        if expected is None:
            issues.append(Issue(
                rule="verb-unknown", severity=WARNING,
                message=f"{_where(card)}: result verb {card.verb!r} is not one this "
                        "program recognises, so the computed result was not "
                        "cross-checked against it.",
                subject=_where(card), detail=card.verb))
            continue
        h_sets, h_games, a_games, a_sets = _card_tally(card, ctx.rule)
        if card.forfeited_by in SIDES:
            # A whole-match forfeit goes to whoever did not forfeit, whatever the
            # games say — they are often 0-0 on a card nobody turned up for, and
            # comparing the verb against them would report every forfeit as a
            # contradiction. Same rule as Match.winner.
            actual = other_side(card.forfeited_by)
            if actual != expected:
                issues.append(Issue(
                    rule="verb-mismatch", severity=ERROR,
                    message=f"{_where(card)}: the verb {card.verb!r} means "
                            f"{expected} won, but the card says {card.forfeited_by} "
                            "forfeited the match.",
                    subject=_where(card),
                    detail=f"verb {card.verb!r} says {expected}; the Match Forfeited "
                           f"tick says {actual}"))
            continue
        finished = sum(1 for s in card.sets if _set_finished(s, ctx.rule))
        if finished < fmt.min_sets_for_result:
            issues.append(Issue(
                rule="verb-no-result", severity=ERROR,
                message=f"{_where(card)}: the card states a {expected} result but "
                        f"only {finished} of {fmt.min_sets_for_result} sets needed "
                        "for a result are complete.",
                subject=_where(card), detail=card.verb))
            continue
        winner = rules.outcome(home_games=h_games, away_games=a_games,
                              home_sets=h_sets, away_sets=a_sets)
        actual = "draw" if winner is None else winner
        if actual != expected:
            issues.append(Issue(
                rule="verb-mismatch", severity=ERROR,
                message=f"{_where(card)}: the verb {card.verb!r} means {expected} "
                        f"won, but the set rows give {actual}.",
                subject=_where(card),
                detail=f"verb {card.verb!r} says {expected}; sets "
                       f"{h_sets}-{a_sets} and games {h_games}-{a_games} decided on "
                       f"{' then '.join(rules.decide_by)} say {actual}"))
    return issues


# --- every card belongs to the competition being scored -----------------


def _check_competition_id(ctx: _Context) -> list[Issue]:
    """A card scored under the wrong competition is invisible everywhere else.

    It names real teams, scores plausible points and lands on a real ladder row.
    Slug identity makes the cross-division merge far less likely than it was with
    reused draw numbers, but two divisions can still share a team name, and a
    season key mistake would put last season's round 1 on this season's ladder.
    """
    issues: list[Issue] = []
    for match in chronological(ctx.matches):
        if match.competition != ctx.competition.id:
            issues.append(Issue(
                rule="wrong-competition", severity=ERROR,
                message=f"{_label(match)}: this card belongs to "
                        f"{match.competition!r} but is being scored as part of "
                        f"{ctx.competition.id!r}, which would merge two ladders.",
                subject=_label(match), detail=match.source))
    return issues


def _check_self_play(ctx: _Context) -> list[Issue]:
    issues: list[Issue] = []
    for match in chronological(ctx.matches):
        if match.home == match.away:
            issues.append(Issue(
                rule="team-plays-itself", severity=ERROR,
                message=f"{_label(match)}: {match.home} is named on both sides, so "
                        "its games and sets would be counted for and against it at "
                        "once.",
                subject=_label(match), detail=match.source))
    return issues


# --- F8: a card naming a team the competition does not have -------------


def _check_team_list(ctx: _Context) -> list[Issue]:
    """Rule F8. A team on a card that is not in the team list.

    An error when the list was declared: the club has said who is in this
    competition, so a seventh name is a misread banner or a misfiled card, and
    adding it silently is what renumbered the predecessor's whole division.

    A warning when the list was derived, with the near-miss suggestion, because
    there is nothing yet to contradict — but "did you mean *iguanas*?" is how a
    typo'd banner gets found.
    """
    issues: list[Issue] = []
    comp = ctx.competition
    if not comp.teams:
        return issues        # already reported by _check_inputs
    unknown: dict[str, list[Match]] = defaultdict(list)
    for match in chronological(ctx.matches):
        # A set, so a match that names one team on both sides is listed once here;
        # naming itself is _check_self_play's finding, not this one's.
        for slug in {match.home, match.away}:
            if match.is_bye and slug == match.away:
                continue
            if slug not in comp.teams:
                unknown[slug].append(match)
    for slug, involved in sorted(unknown.items()):
        suggestions = near_misses(slug, comp.teams)
        hint = (f" Did you mean {', '.join(suggestions)}?" if suggestions else "")
        where = ", ".join(_label(m) for m in involved)
        if comp.teams_declared:
            issues.append(Issue(
                rule="F8", severity=ERROR,
                message=f"{slug!r} plays in {where} but is not in the declared team "
                        f"list for {comp.id}.{hint}",
                subject=slug,
                detail=f"declared teams: {', '.join(sorted(comp.teams))}"))
        else:
            issues.append(Issue(
                rule="F8", severity=WARNING,
                message=f"{slug!r} appears in {where} and in no other round; with "
                        f"no declared team list this program cannot tell a new team "
                        f"from a misspelt one.{hint}",
                subject=slug,
                detail=f"known teams: {', '.join(sorted(comp.teams))}"))
    return issues


# --- F9: duplicates -----------------------------------------------------


def _check_duplicates(ctx: _Context) -> list[Issue]:
    """Rule F9, plus the two neighbouring forms of the same mistake.

    The predecessor raised an uncaught ``ValueError`` on a duplicate fixture key
    and took the whole run down. Reporting it names both source files, which is
    what a human needs to decide which card is the keeper.
    """
    issues: list[Issue] = []
    by_fixture: dict[tuple[str, str, str], list[Match]] = defaultdict(list)
    by_id: dict[str, list[Match]] = defaultdict(list)
    by_team_round: dict[tuple[str, str], list[Match]] = defaultdict(list)
    for match in chronological(ctx.matches):
        by_fixture[match.key].append(match)
        if match.match_id:
            by_id[match.match_id].append(match)
        if match.status is MatchStatus.BYE:
            # A bye alongside a real card is the same team twice in a round, but it
            # has its own explanation and its own check; do not say it twice.
            continue
        for slug in {match.home, match.away}:
            by_team_round[(match.round, slug)].append(match)

    for key, group in sorted(by_fixture.items()):
        if len(group) > 1:
            issues.append(Issue(
                rule="F9", severity=ERROR,
                message=f"{len(group)} cards claim round {key[0]} "
                        f"{key[1]} v {key[2]}; one of them would silently overwrite "
                        "the other.",
                subject=f"R{key[0]} {key[1]} v {key[2]}",
                detail="; ".join(f"{m.source or 'unknown source'}"
                                 f"{f' (match {m.match_id})' if m.match_id else ''}"
                                 for m in group)))
    for match_id, group in sorted(by_id.items()):
        if len(group) > 1:
            issues.append(Issue(
                rule="F9", severity=ERROR,
                message=f"match id {match_id} appears on {len(group)} cards: "
                        f"{', '.join(_label(m) for m in group)}.",
                subject=match_id,
                detail="; ".join(m.source or "unknown source" for m in group)))
    for (number, slug), group in sorted(by_team_round.items()):
        if len(group) > 1:
            issues.append(Issue(
                rule="team-twice-in-round", severity=ERROR,
                message=f"{slug} has {len(group)} matches in round {number}: "
                        f"{', '.join(_label(m) for m in group)}. Its points for the "
                        "round would be counted twice.",
                subject=f"R{number} {slug}",
                detail="; ".join(m.source or "unknown source" for m in group)))
    return issues


# --- set counts and games, against the format and the set rule ----------


def _check_sets(ctx: _Context) -> list[Issue]:
    """The card's shape and scores against the competition's own format.

    The set rule is the only thing that decides whether a score is finished, which
    is why this defers entirely to :func:`~matchcentre.format.completed` and
    :func:`~matchcentre.format.plausible`. A bare ``>= 6`` here read FAST4's 4-2 as
    unplayed and pro-8's 6-3 as final.
    """
    issues: list[Issue] = []
    fmt = ctx.competition.fmt
    rule = ctx.rule
    expected_keys = list(fmt.keys)
    for match in ctx.playable:
        present = [s.slot.key for s in match.sets]
        missing = [key for key in expected_keys if key not in present]
        surplus = [key for key in present if key not in expected_keys]
        if missing or surplus:
            issues.append(Issue(
                rule="set-count", severity=ERROR,
                message=f"{_label(match)}: the card has {len(match.sets)} set "
                        f"row(s) but {ctx.competition.id} is scored as "
                        f"{fmt.name} ({len(expected_keys)} sets).",
                subject=_label(match),
                detail=f"missing {', '.join(missing) or 'nothing'}; unexpected "
                       f"{', '.join(surplus) or 'nothing'}"))
        if len(set(present)) != len(present):
            issues.append(Issue(
                rule="set-count", severity=ERROR,
                message=f"{_label(match)}: two set rows claim the same slot "
                        f"({', '.join(sorted(present))}).",
                subject=_label(match), detail=match.source))
        for result in match.sets:
            where = f"{_label(match)} {result.slot.key}"
            score = f"{result.home_games}-{result.away_games}"
            if not plausible(result.home_games, result.away_games, rule):
                issues.append(Issue(
                    rule="set-implausible", severity=ERROR,
                    message=f"{where}: {score} cannot happen under "
                            f"{rule.name} ({rule.describe()}), so this row was "
                            "misread.",
                    subject=where, detail=match.source))
                continue
            if result.status is not SetStatus.PLAYED:
                continue      # forfeit, retirement and washout are flagged, not odd
            # Warnings, not errors, and the reasoning is the severity rule itself: a
            # set nobody won awards nobody the set, whether or not the card admits
            # why it stopped, so the games and the set count are the same either
            # way. Real cards do this — one card in the season this project was
            # rewritten from has a doubles left at 2-4 with no box ticked, and its
            # printed TOTAL row agrees with the set rows regardless. Where enough
            # sets are unfinished to leave the match undecided, _check_status says
            # so separately.
            if result.home_games == result.away_games:
                issues.append(Issue(
                    rule="set-tied", severity=WARNING,
                    message=f"{where}: level at {score}, so nobody won the set and "
                            "nothing on the card says why it stopped.",
                    subject=where, detail=match.source))
            elif not completed(result.home_games, result.away_games, rule):
                issues.append(Issue(
                    rule="set-unfinished", severity=WARNING,
                    message=f"{where}: {score} is not a finished set under "
                            f"{rule.name} ({rule.describe()}) and is not marked "
                            "forfeit, retired or incomplete, so nobody is credited "
                            "the set.",
                    subject=where, detail=match.source))
        if (match.is_draw and not ctx.competition.rules.allows_draw):
            issues.append(Issue(
                rule="draw-not-allowed", severity=ERROR,
                message=f"{_label(match)}: the match is level, but "
                        f"{ctx.competition.rules.name} has no value for a draw, so "
                        "either a result was not recorded or a row was misread.",
                subject=_label(match), detail=match.source))
    return issues


# --- set rows against the rosters --------------------------------------


def _check_set_players(ctx: _Context) -> list[Issue]:
    """Who is named in the set rows, against who the card and the club say plays.

    All warnings: a fill-in is legal, an unknown spelling needs an alias rather
    than a correction, and none of it changes a game count. The one thing that
    would be an error — a player counted for two teams in one round — is
    :func:`_check_player_teams`.
    """
    issues: list[Issue] = []
    comp = ctx.competition
    index = comp.player_index()
    for match in ctx.playable:
        for side in ("home", "away"):
            roster = match.roster_for(side)
            team = match.team_for(side)
            if not roster:
                issues.append(Issue(
                    rule="roster-missing", severity=WARNING,
                    message=f"{_label(match)}: no {side} roster was read from the "
                            "card, so fill-ins on that side cannot be detected.",
                    subject=_label(match), detail=match.source))
            nameless: list[str] = []
            for result in match.sets:
                players = result.players_for(side)
                expected = 2 if result.slot.doubles else 1
                where = f"{_label(match)} {result.slot.key}"
                if players and len(players) != expected:
                    issues.append(Issue(
                        rule="set-player-count", severity=WARNING,
                        message=f"{where}: the {side} side has {len(players)} "
                                f"player(s) named, and {result.slot.label} is a "
                                f"{result.slot.discipline} set.",
                        subject=where, detail=", ".join(players)))
                if not players and result.status is SetStatus.PLAYED:
                    # Collected and reported once per side: a card whose whole name
                    # column failed to read would otherwise emit one warning per set
                    # and bury everything else in the report.
                    nameless.append(result.slot.key)
                for slug in players:
                    if roster and slug in roster:
                        continue
                    known = index.get(slug)
                    if known is None:
                        elsewhere = (ctx.club.registered_elsewhere(slug, comp.id)
                                     if ctx.club else None)
                        if elsewhere is not None:
                            issues.append(Issue(
                                rule="fill-in-other-competition", severity=WARNING,
                                message=f"{where}: {slug} is registered in "
                                        f"{elsewhere.id}, not in {comp.id}, and "
                                        f"played {side} for {team} — counted as a "
                                        "fill-in.",
                                subject=where, detail=elsewhere.id))
                            continue
                        issues.append(Issue(
                            rule="unknown-player", severity=WARNING,
                            message=f"{where}: {slug} is on neither the {side} "
                                    "roster nor any registered squad, so this is "
                                    "either a fill-in or a spelling that needs an "
                                    "alias.",
                            subject=where, detail=match.source))
                    elif known.team is None:
                        issues.append(Issue(
                            rule="player-no-team", severity=WARNING,
                            message=f"{where}: {known.name} is registered in "
                                    f"{comp.id} without a team, so this set cannot "
                                    "be checked against a roster.",
                            subject=where, detail=match.source))
                    elif known.team != team:
                        issues.append(Issue(
                            rule="fill-in", severity=WARNING,
                            message=f"{where}: {known.name} is registered to "
                                    f"{known.team} but played {side} for {team} — "
                                    "counted as a fill-in.",
                            subject=where, detail=f"registered to {known.team}"))
                    elif roster:
                        issues.append(Issue(
                            rule="off-roster", severity=WARNING,
                            message=f"{where}: {known.name} plays for {team} but is "
                                    "not in this card's roster block.",
                            subject=where, detail=", ".join(roster)))
            if nameless:
                issues.append(Issue(
                    rule="set-players-missing", severity=WARNING,
                    message=f"{_label(match)}: no {side} player was read for "
                            f"{', '.join(nameless)}, so those games are counted for "
                            f"{match.team_for(side)} but for nobody in it.",
                    subject=_label(match), detail=match.source))
    return issues


def _check_player_teams(ctx: _Context) -> list[Issue]:
    """One player cannot appear for two teams in the same round.

    An error, not a fill-in note: whichever card is wrong, one team's set is
    credited to someone who was on another court at the time, and the player's own
    record double-counts the round.
    """
    issues: list[Issue] = []
    seen: dict[tuple[str, str], set[str]] = defaultdict(set)
    for match in ctx.playable:
        for side in ("home", "away"):
            for result in match.sets:
                for slug in result.players_for(side):
                    seen[(match.round, slug)].add(match.team_for(side))
    for (number, slug), sides in sorted(seen.items()):
        if len(sides) > 1:
            issues.append(Issue(
                rule="player-two-teams", severity=ERROR,
                message=f"{slug} plays for {', '.join(sorted(sides))} in round "
                        f"{number}; one of those cards names the wrong player.",
                subject=f"R{number} {slug}",
                detail=", ".join(sorted(sides))))
    return issues


def _check_ratings(ctx: _Context) -> list[Issue]:
    """A card with no ratings on it, said out loud rather than shown as blanks.

    A warning: ratings are display data and no result depends on them. But a whole
    card losing them usually means the roster block was not read, and that is worth
    knowing before a reader concludes the club stopped publishing them.
    """
    issues: list[Issue] = []
    for match in ctx.playable:
        on_court = set(match.players_on("home")) | set(match.players_on("away"))
        if not on_court:
            continue
        if not match.ratings:
            issues.append(Issue(
                rule="ratings-missing", severity=WARNING,
                message=f"{_label(match)}: no ratings were read from this card, so "
                        "every player on it shows none for this round.",
                subject=_label(match), detail=match.source))
            continue
        absent = sorted(on_court - set(match.ratings))
        if absent:
            issues.append(Issue(
                rule="ratings-missing", severity=WARNING,
                message=f"{_label(match)}: no rating was read for "
                        f"{', '.join(absent)}.",
                subject=_label(match), detail=match.source))
    return issues


def _check_rosters(ctx: _Context) -> list[Issue]:
    """The card's roster block against the squad the competition has registered."""
    issues: list[Issue] = []
    teams = ctx.competition.teams
    for match in ctx.playable:
        for side in ("home", "away"):
            roster = match.roster_for(side)
            slug = match.team_for(side)
            if not roster:
                continue      # already reported by _check_set_players
            team = teams.get(slug)
            if team is None:
                continue      # already reported by _check_team_list (F8)
            registered = {p.slug for p in team.players}
            if not registered:
                continue      # nothing declared to compare against
            extra = sorted(s for s in roster if s not in registered)
            absent = sorted(p.slug for p in team.players if p.slug not in roster)
            if extra:
                issues.append(Issue(
                    rule="roster-mismatch", severity=WARNING,
                    message=f"{_label(match)}: the {side} roster names "
                            f"{', '.join(extra)}, who are not in {slug}'s "
                            "registered squad.",
                    subject=_label(match), detail=", ".join(sorted(registered))))
            if absent:
                issues.append(Issue(
                    rule="roster-incomplete", severity=WARNING,
                    message=f"{_label(match)}: the {side} roster omits registered "
                            f"player(s) {', '.join(absent)} of {slug}.",
                    subject=_label(match), detail=", ".join(sorted(roster))))
    return issues


# --- the declared draw against the cards --------------------------------


def _check_draw(ctx: _Context) -> list[Issue]:
    """Fixtures and round labels against a *declared* draw.

    Only a declared draw can contradict a card, which is why every branch here is
    gated on ``draw_source``. A derived draw is built from these same cards, so
    checking cards against it would be checking them against themselves — and
    reporting a fixture the club never published as missing from the draw is
    exactly the fabrication F2 forbids.
    """
    issues: list[Issue] = []
    comp = ctx.competition
    if comp.draw_source != DRAW_DECLARED:
        return issues
    for match in chronological(ctx.matches):
        scheduled = comp.rounds.get(match.round)
        if scheduled is None:
            issues.append(Issue(
                rule="unknown-round", severity=ERROR,
                message=f"{_label(match)}: round {match.round!r} is not in the "
                        "declared draw.",
                subject=_label(match),
                detail=f"declared rounds: {', '.join(comp.round_order)}"))
            continue
        if match.is_bye:
            if match.home not in scheduled.byes:
                issues.append(Issue(
                    rule="bye-not-declared", severity=ERROR,
                    message=f"round {match.round}: {match.home} is being credited a "
                            f"bye, but the declared draw rests "
                            f"{', '.join(scheduled.byes) or 'nobody'}.",
                    subject=f"R{match.round} {match.home}",
                    detail=", ".join(scheduled.byes)))
            continue
        pair = (match.home, match.away)
        if scheduled.is_finals:
            # Finals pair by ladder position, so the fixture list is empty by
            # design and there is nothing to check against.
            continue
        if pair not in scheduled.fixtures:
            if (pair[1], pair[0]) in scheduled.fixtures:
                issues.append(Issue(
                    rule="draw-reversed", severity=ERROR,
                    message=f"{_label(match)}: the declared draw has this fixture "
                            f"the other way round ({match.away} v {match.home}), so "
                            "home and away may be swapped — which reverses the "
                            "scoreline.",
                    subject=_label(match), detail=match.source))
            else:
                listed = ", ".join(f"{h} v {a}" for h, a in scheduled.fixtures)
                issues.append(Issue(
                    rule="draw-mismatch", severity=ERROR,
                    message=f"{_label(match)}: not a round-{match.round} fixture in "
                            "the declared draw.",
                    subject=_label(match), detail=f"declared: {listed}"))
        if scheduled.date and match.date and match.date != scheduled.date:
            issues.append(Issue(
                rule="date-mismatch", severity=WARNING,
                message=f"{_label(match)}: the card is dated {match.date}; the "
                        f"declared draw has round {match.round} on "
                        f"{scheduled.date}. The card wins.",
                subject=_label(match), detail=scheduled.date))
    return issues


# --- byes -------------------------------------------------------------


def _check_byes(ctx: _Context) -> list[Issue]:
    """A bye and a card for the same team in the same round.

    :func:`~matchcentre.stats.with_byes` refuses to add a bye where the team has a
    card, so the disagreement between the draw and the cards is a warning: the
    numbers stand, and the likeliest explanation is a rescheduled fixture. A bye
    match that is *already in the list* alongside a card is an error, because that
    team is being paid its average on top of a real result.
    """
    issues: list[Issue] = []
    comp = ctx.competition
    # Keep the card, not just the slug: what a reader needs is the path of the
    # scorecard that contradicts the draw.
    real: dict[str, dict[str, Match]] = defaultdict(dict)
    byes: dict[str, list[Match]] = defaultdict(list)
    for match in chronological(ctx.matches):
        if match.status is MatchStatus.BYE:
            byes[match.round].append(match)
            continue
        for slug in {match.home, match.away}:
            real[match.round].setdefault(slug, match)
    for number in sorted(byes, key=round_sort_key):
        for bye in byes[number]:
            played = real[number].get(bye.home)
            if played is None:
                continue
            issues.append(Issue(
                rule="bye-and-card", severity=ERROR,
                message=f"round {number}: {bye.home} is credited a bye and also has "
                        "a card, so it is paid its average on top of a real result.",
                subject=f"R{number} {bye.home}",
                detail=f"card {played.source or 'unknown source'}"))
    for number in sorted(set(real) | set(byes), key=round_sort_key):
        declared = comp.rounds.get(number)
        if declared is None:
            continue
        for resting in declared.byes:
            played = real[number].get(resting)
            if played is None:
                continue
            issues.append(Issue(
                rule="bye-contradicted", severity=WARNING,
                message=f"round {number}: the {comp.draw_source} draw rests "
                        f"{resting}, but there is a card for it that round. The "
                        "card wins and no bye was credited.",
                subject=f"R{number} {resting}",
                detail=f"card {played.source or 'unknown source'}"))
    return issues


# --- F3: rounds_total ---------------------------------------------------


def _check_rounds_total(ctx: _Context) -> list[Issue]:
    """Rule F3. ``rounds_total`` is null unless a human declared it.

    The predecessor set it to the number of rounds played, so its progress meter
    read 100% of a ten-round season at round three. A number here that nobody
    declared is that bug, whatever value it happens to hold.
    """
    issues: list[Issue] = []
    comp = ctx.competition
    total = comp.rounds_total
    if total is None:
        return issues
    if comp.provenance_of("rounds_total") is not Provenance.DECLARED:
        issues.append(Issue(
            rule="F3", severity=ERROR,
            message=f"{comp.id} reports rounds_total = {total} with "
                    f"{comp.provenance_of('rounds_total').value} provenance. Unless "
                    "a human declared the season length it must be null, or the "
                    "dashboard will show a completion percentage it invented.",
            subject=comp.id,
            detail=comp.provenance_of("rounds_total").value))
    # Finals are excluded: a ten-round season with a final has eleven round labels
    # and is not thereby eleven rounds long.
    regular = sorted({m.round for m in ctx.matches
                      if not _round_is_finals(comp, m.round)}, key=round_sort_key)
    if total < len(regular):
        issues.append(Issue(
            rule="F3", severity=ERROR,
            message=f"{comp.id} declares rounds_total = {total} but "
                    f"{len(regular)} regular rounds have been played "
                    f"({', '.join(regular)}).",
            subject=comp.id, detail=f"rounds played: {', '.join(regular)}"))
    return issues


# --- F11: no finals in the ladder ---------------------------------------


def _check_finals(ctx: _Context) -> list[Issue]:
    """Rule F11. A finals round must not contribute ladder points.

    Everything in :mod:`~matchcentre.stats` counts every match it is given, so a
    finals match reaching this list *is* the defect: it will be in the points
    column. Finals have to be filtered out before scoring, not discounted after.
    """
    issues: list[Issue] = []
    comp = ctx.competition
    scored: dict[str, list[Match]] = defaultdict(list)
    for match in chronological(ctx.matches):
        if match.decided:
            scored[match.round].append(match)
    for number in sorted({m.round for m in ctx.matches}, key=round_sort_key):
        rnd = comp.rounds.get(number)
        numeric = number.strip().isdigit()
        finals = _round_is_finals(comp, number)
        if finals and scored[number]:
            count = len(scored[number])
            issues.append(Issue(
                rule="F11", severity=ERROR,
                message=f"round {number} is a finals round and {count} of its "
                        f"matches {'is' if count == 1 else 'are'} in the set being "
                        "scored, so finals results would contribute ladder points.",
                subject=f"R{number}",
                detail=", ".join(_label(m) for m in scored[number])))
        if (rnd is not None and rnd.is_finals and numeric
                and comp.draw_source != DRAW_DECLARED):
            issues.append(Issue(
                rule="F11", severity=ERROR,
                message=f"round {number} is marked finals, but its label is a "
                        "number and no declared draw says so. A round is finals "
                        "only when it is declared or its own label is not numeric, "
                        "so this would drop a regular round out of the ladder.",
                subject=f"R{number}", detail=comp.draw_source))
        if not finals and not numeric and not is_finals_label(number):
            issues.append(Issue(
                rule="F11", severity=WARNING,
                message=f"round label {number!r} is neither a number nor a finals "
                        "label this program recognises; it is being scored as a "
                        "regular round.",
                subject=f"R{number}", detail=number))
    return issues


# --- F10: does the ladder state a settled order? ------------------------


def _check_ladder(ctx: _Context) -> list[Issue]:
    """Rule F10. Whether the ladder is an order or a snapshot, and who is short.

    A warning, always: the points are right, and what is at stake is the claim the
    dashboard makes about them. Mid-round, the team on top is the team whose card
    arrived, and the label has to say so.
    """
    issues: list[Issue] = []
    comp = ctx.competition
    if not comp.teams:
        return issues       # already reported by _check_inputs
    try:
        counted = {slug: points_ledger(with_byes(ctx.matches, comp), slug).counted
                   for slug in comp.teams}
    except ScoringError as exc:
        # A level match under a ruleset that has no value for a draw. Reported
        # rather than raised: the rest of the report is still worth reading.
        return [Issue(
            rule="F10", severity=ERROR,
            message=f"{comp.id}: the ladder cannot be computed — {exc}",
            subject=comp.id, detail=str(exc))]
    most = max(counted.values(), default=0)
    if most == 0:
        issues.append(Issue(
            rule="F10", severity=WARNING,
            message=f"{comp.id}: no team has a counted match yet, so there is no "
                    "ladder to present.",
            subject=comp.id, detail="0 counted matches"))
        return issues
    short = sorted(slug for slug, n in counted.items() if n < most)
    if short:
        issues.append(Issue(
            rule="F10", severity=WARNING,
            message=f"{comp.id}: the ladder is in progress — teams have played "
                    f"unequal numbers of matches, so the order is not settled. "
                    f"{', '.join(short)} "
                    f"{'has' if len(short) == 1 else 'have'} fewer than the "
                    f"{most} counted by the rest.",
            subject=comp.id,
            detail="; ".join(f"{slug} {counted[slug]}" for slug in sorted(counted))))
    return issues
