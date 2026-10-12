"""Everything that can be checked about a season, checked and named.

Nothing here raises. The dashboard's job is to render the season *and* show what
is wrong with it, so one bad card must not take the page down: every problem comes
back as an :class:`Issue` and lands in ``season.json``'s ``validation`` block,
grouped by the rule that found it.

Two disciplines run through the whole module.

**Severity is a claim about the numbers.** An ``error`` means the figures are, or
may be, wrong: the card's own ``TOTAL`` row contradicts its set rows, two cards
claim the same fixture, a regular round is marked finals and has been dropped out of
the ladder. A ``warning`` means something deserves a human's eye but the numbers
stand: a fill-in, a roster the card printed differently from the club's records, a
finals result left out of the ladder, a ladder with a team a match behind.

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
from decimal import Decimal

from .card import (Card, ParsedSet, Rejection, letterhead_key, letterhead_spellings,
                   majority_letterhead, states_no_result, thin_card_prints, verb_side)
from .card import Totals
from .competition import (DRAW_DECLARED, Club, Competition, Provenance,
                          fixture_sort_key, is_finals_label,
                          round_number, years_played)
from .format import MatchFormat, SetRule, completed, plausible, set_winner
from .config import DEFAULT_PATH, toml_key, toml_value
from .discover import FiledElsewhere, called_off, nearest_team, where as _where
from .model import (SIDES, Match, MatchStatus, SetResult, SetStatus, all_blank,
                    forfeit_match_winner, forfeit_set_winner, names_miscounted,
                    retired_set_winner, rubber_award, stopped_short)
from .scorecard import _given_up, box_award, forfeit_award
from .scoring import Ruleset, ScoringError
from .scoring import UNPLAYED_AVERAGE, UNPLAYED_ZERO
from .sheet import (AHEAD, COMPARED, Comparison, TeamCheck, at_places, by_weeks,
                    places_of, rounds_listed)
from .scope import echoed, in_words
from .slugs import did_you_mean, unslug
from .stats import (card_against_rest, chronological, fixtures_without_cards,
                    ladder_counts, ladder_detail, ladder_levels,
                    ladder_matches, ladder_played, ladder_state_of,
                    ordered_on_the_average, rests_standing, rounds_present,
                    same_result, same_scoreline, unlisted_words)
from .stats import duplicates

__all__ = ["Issue", "Report", "validate", "ERROR", "WARNING"]

ERROR = "error"
WARNING = "warning"

# The card prints a verb between the team names; the parser passes it through
# untouched for us to reconcile against the computed result, read whole by
# card.verb_side and told from a separator by card.states_no_result, which the
# reader's checks ask as well.


@dataclass(frozen=True, slots=True)
class Issue:
    """One finding, named after the rule that found it.

    ``rule`` is an ``F``-number -- a stable id for one rule about what may be
    inferred from a scorecard -- or a kebab-case check name, and it is what the
    dashboard groups by — so a reader sees "three cards failed
    F6" rather than a flat list. ``subject`` is what the issue is about (a match
    label, a team, a path) and ``detail`` carries the supporting evidence: the
    source file, or both of two disagreeing readings.

    ``subject`` is a key, always written for a program to find the finding by, and
    a message may begin with it. The readers leave it out where one does, by the
    one rule in ``export.subject_said``.

    Never the competition, in either field: a finding belongs to exactly one, both
    readers print it themselves, and a finding that named it too read
    ``tuesday-division-c: tuesday-division-c has no matches`` over a
    ``subject: tuesday-division-c`` line. F4 is the one exception to "exactly one".
    A refused card might belong to any competition, so every document carries it,
    and the report prints it once, under no competition's name.
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
    multi_season: bool
    sheet: Comparison | None = None
    # The config file the build read, by its name, for the advice that sends a
    # club to a line of it.
    config_name: str = DEFAULT_PATH
    # The cards of this competition's teams filed under another of its season, for
    # card-missing to say where a card went (discover.FiledElsewhere).
    strays: tuple[FiledElsewhere, ...] = ()

    @property
    def rule(self) -> SetRule:
        return self.competition.fmt.set_rule

    @property
    def playable(self) -> list[Match]:
        """The matches that count as played, which are the ones read row by row.

        Which those are is :attr:`Match.counts_as_played`'s to say, the one test of
        whether a match happened and so the only matches any game, set or appearance
        is counted from, and it is asked rather than copied. A list of statuses kept
        here would agree with it until the day the two parted, and either way round
        that day is wrong: a night counted nowhere fails the run over its rows, or
        the rows behind a figure are never read.

        A washout is left out even with two rubbers of it on the card. Its set rows
        are real and no figure reads them, so a finding about one would ask a club
        to correct a row nothing counts. Its roster block goes with it: the squads
        are gathered from the rosters of the cards that count and the rating history
        from their ratings, so the roster and rating checks read every roster any
        figure is built from, and a name printed only on a washout's roster is
        registered by nobody rather than reported missing from the cards beside it.

        A washout and an unplayed night are each named by :func:`_check_status`. The
        checks that read a card rather than a match read :attr:`in_play`, which is
        every card but a washout's, and hold a verb to a result only on a card that
        :meth:`counts`. A bye is normal, and is not named.
        """
        return [m for m in chronological(self.matches, self.competition.rounds)
                if m.counts_as_played]

    @property
    def in_play(self) -> list[Card]:
        """Every card but a washout's, which are the cards read for how they were read.

        A card its own boxes call off (:func:`~matchcentre.discover.called_off`)
        counts for nothing whatever its rows say, so its cross-checks, its TOTAL row
        and its verb are evidence about rows no figure reads, and a finding about one
        would fail a run over a night nobody counts; ``no-result`` names it, once. A
        card that counts for nothing because every row was read empty is still in
        play. Its rows are what made it count for nothing, so its verb, its tick and
        its TOTAL row are the evidence that they were misread, and to ask whether it
        counts would be to let that reading vouch for itself.
        """
        return [card for card in self.cards if not called_off(card)]

    def counts(self, card: Card) -> bool:
        """Whether *card* is the card of a match that counts as played.

        :attr:`Match.counts_as_played` says, asked of the match made from the card,
        which is found by the path both are named by. A card no match was made from
        counts: nothing says it does not, and a check passed by for want of a match
        would record the absence of evidence as evidence.
        """
        where = _where(card)
        return all(m.counts_as_played for m in self.matches if m.source == where)

    def records_nothing(self, card: Card) -> bool:
        """Whether *card* is the card of a match :func:`records_nothing` says of.

        Asked by every finding that would say again, of such a card, what its
        ``no-result`` says (4.424). A card no match was made from records what it
        prints: nothing says otherwise, and a finding passed by for want of a
        match would record the absence of evidence as evidence.
        """
        where = _where(card)
        return any(records_nothing(m, card) for m in self.matches
                   if m.source == where)


def validate(matches: Sequence[Match], competition: Competition, *,
             club: Club | None = None, cards: Sequence[Card] = (),
             rejected: Sequence[Rejection] = (),
             seasons_mode: str = "single",
             sheet: Comparison | None = None,
             config_name: str = DEFAULT_PATH,
             strays: Sequence[FiledElsewhere] = ()) -> Report:
    """Run every check. Findings come back in a deterministic order.

    ``cards`` are the parsed scorecards behind ``matches`` and ``rejected`` the
    PDFs that could not be read; both are optional, and when either is absent the
    checks that needed it say so rather than passing quietly.

    ``club`` only widens what can be *explained*: with it, a player who filled in
    from another competition is named as such instead of being reported as an
    unknown spelling. Its absence never changes a verdict.

    ``matches`` are the cards and nothing else. A build hands this the matches it
    read, before :func:`~matchcentre.stats.with_byes` credits a single rest, so no
    check here can be about a bye match: one that looks for one never fires. What is
    checked about a rest is checked against the draw and the cards, by the rule
    that credits it (:func:`_check_byes`).

    ``seasons_mode`` is ``[build] seasons``, and it changes advice and never a
    verdict: what to do about two seasons' cards in one competition depends on
    whether the club has set the mode already (:func:`_across_years`).

    ``sheet`` is the association's sheet already held up against these matches
    (:func:`~matchcentre.sheet.compare`), or ``None`` when the club typed none. A
    finished comparison rather than the table, so nothing here reads the config.

    ``config_name`` is the config file the build read, by its name
    (:func:`~matchcentre.config.named`), and like ``seasons_mode`` it changes advice
    and never a verdict: a finding that sends the club to a line of it names that
    file. The name and never the path, since a finding reaches ``season.json``.

    ``strays`` are the cards of this competition's teams that discovery filed under
    another competition of the season (:class:`~matchcentre.discover.FiledElsewhere`),
    and like ``club`` they only change what a finding can explain: a declared
    fixture one fits is still a card this competition has not got.
    """
    context = _Context(matches=tuple(matches), competition=competition, club=club,
                       cards=tuple(cards), rejected=tuple(rejected),
                       multi_season=seasons_mode == "multi", sheet=sheet,
                       config_name=config_name, strays=tuple(strays))
    issues: list[Issue] = []
    for check in (
        _check_inputs,              # what could not be checked at all
        _check_status,              # matches carrying no result, named
        _check_cards_accounted,     # F4
        _check_cross_checks,        # F6
        _check_totals,              # totals-mismatch
        _check_verb,                # verb-mismatch
        _check_forfeit_rows,        # forfeit-contradicted
        _check_walkover_rows,       # walkover-rows-played
        _check_unnamed,             # players-unrecorded
        _check_competition_id,      # wrong-competition
        _check_letterhead,          # foreign-card
        _check_self_play,           # team-plays-itself
        _check_team_list,           # F8
        _check_duplicates,          # F9
        _check_sets,                # set counts and games against the format
        _check_set_players,         # set rows against the rosters
        _check_player_teams,        # one player, one team, one round
        _check_namesakes,           # one printed name, two roster blocks
        _check_ratings,             # ratings-missing
        _check_rosters,             # card roster against the registered squad
        _check_draw,                # declared draw vs the cards
        _check_missing_cards,       # a declared fixture with no card at all
        _check_byes,                # a rest the same round has the team playing
        _check_rounds_total,        # F3
        _check_finals,              # F11
        _check_ladder,              # F10
        _check_sheet_teams,         # sheet-team
        _check_sheet_reached,       # sheet-ahead
        _check_sheet,               # sheet-mismatch
    ):
        issues.extend(check(context))
    return Report(
        errors=tuple(i for i in issues if i.severity == ERROR),
        warnings=tuple(i for i in issues if i.severity == WARNING),
    )


# --- helpers ------------------------------------------------------------


def _label(match: Match) -> str:
    return f"R{match.round} {match.home} v {match.away}"


def _nobody_played(match: Match, result: SetResult) -> bool:
    """Whether a row is one of a whole-match forfeit's, left with no games on it.

    Nobody played such a row, and the Match Forfeited box says so, so it is missing
    neither games nor names: ``set-no-games``, ``set-players-missing`` and
    ``set-player-count`` all ask this and pass it by, the last since 4.446, which
    measured it telling a forfeit's absent side that a rubber nobody played was a
    doubles set short of a name. Asked of the status, which is the reader's one
    reading of the box, and not of the box itself; asked of the row too, so a
    forfeit given up after a rubber is still held to the names on the rubber it
    played. Only ``set-no-games`` asked at first, and the same card with the absent
    side's names left blank, as a card for a side that never came would be, was
    measured with a ``set-players-missing`` warning naming every row.
    ``roster-missing`` asks it too, of every row, through :func:`_never_came`. A
    night given up by its rows alone, every row 0-0 and ticked Forfeit against one
    side, is one too (:attr:`~matchcentre.model.Match.given_up_by`, 4.420), though
    nobody ticked the box. :meth:`~matchcentre.model.Match.nobody_played` is the
    one rule, of the games printed, since a forfeit's rows are scored as love sets
    (4.421), and the players' figures ask it too.
    """
    return match.nobody_played(result)


def _never_came(match: Match, side: str) -> bool:
    """Whether *side* forfeited the whole match and nobody played a row of it.

    Such a side owes no roster: with nobody on court for it there is no fill-in to
    detect and nobody to rate, and ``roster-missing`` said both of the filed card
    of a side that never came, which prints nobody in its block. A forfeit given up
    after a rubber is not one, since somebody played that rubber. Asked of
    :attr:`~matchcentre.model.Match.given_up_by`, so a night told by its rows,
    every row ticked Forfeit against *side*, owes no roster either (4.288).
    """
    return (match.given_up_by == side
            and all(_nobody_played(match, result) for result in match.sets))


def _set_finished(parsed: ParsedSet, rule: SetRule) -> bool:
    """Whether one transcribed row counts as a completed set.

    Mirrors :attr:`~matchcentre.model.SetResult.completed` deliberately rather than
    calling it: this runs on the :class:`~matchcentre.card.ParsedSet` straight off
    the page, so the totals and verb checks do not inherit a mistake made while
    turning cards into matches. A forfeited set is complete; a retirement or a
    washout is not, however final its printed games look, by
    :func:`~matchcentre.model.stopped_short`, the rule the model asks, unless one
    side's Retired box says who retired, which gives the other side the set
    (:func:`~matchcentre.model.retired_set_winner`, 4.422).
    """
    if parsed.status is SetStatus.RETIRED and retired_set_winner(
            home_retired=parsed.home_retired, away_retired=parsed.away_retired):
        return True
    if stopped_short(parsed.status):
        return False
    if parsed.status is SetStatus.FORFEIT:
        return True
    return completed(parsed.home_games, parsed.away_games, rule)


def _set_winner_of(parsed: ParsedSet, rule: SetRule) -> str | None:
    """Who won one transcribed set row, by the same logic as ``SetResult.winner``."""
    if parsed.status is SetStatus.FORFEIT:
        # Through the shared rule, not beside it: this runs on the row straight off
        # the page, and a second opinion about who a forfeited rubber goes to is a
        # totals-mismatch error against the tool's own reading of the same row.
        return forfeit_set_winner(home_forfeit=parsed.home_forfeit,
                                  away_forfeit=parsed.away_forfeit,
                                  home_games=parsed.home_games,
                                  away_games=parsed.away_games)
    if parsed.status is SetStatus.RETIRED:
        stayed = retired_set_winner(home_retired=parsed.home_retired,
                                    away_retired=parsed.away_retired)
        if stayed is not None:
            return stayed
    if not _set_finished(parsed, rule):
        return None
    return set_winner(parsed.home_games, parsed.away_games, rule)


def _card_tally(card: Card, rule: SetRule) -> tuple[int, int, int, int]:
    """``(home_sets, home_games, away_games, away_sets)`` from the set rows."""
    winners = [_set_winner_of(s, rule) for s in card.sets]
    return (winners.count("home"), card.home_games, card.away_games,
            winners.count("away"))


def _card_scored(card: Card, rule: SetRule,
                 rules: Ruleset) -> tuple[int, int, int, int]:
    """The set rows' ``(home_sets, home_games, away_games, away_sets)`` as scored.

    :func:`_card_tally` reckons the games printed; this reckons them as the match
    is scored, by :func:`~matchcentre.model.rubber_award` under the club's set rule
    and forfeit award, the one rule discover scores the match by: a rubber ticked
    Forfeit against one side a love set to the other (4.421), a set one side
    retired from the other's at the games to win it (4.422), and every rubber of a
    match given up whole a love set to the side that came. Under ``as-played`` the
    two reckonings are one. A TOTAL row may total either (4.421).
    """
    given_up = card.forfeit_side if card.forfeit_side in SIDES else None
    winners: list[str | None] = []
    home_games = away_games = 0
    for parsed in card.sets:
        award = rubber_award(
            status=parsed.status, home_games=parsed.home_games,
            away_games=parsed.away_games, games_to_win=rule.games_to_win,
            award=rules.forfeit_award, home_forfeit=parsed.home_forfeit,
            away_forfeit=parsed.away_forfeit, home_retired=parsed.home_retired,
            away_retired=parsed.away_retired, given_up_by=given_up)
        side, home, away = award or (_set_winner_of(parsed, rule),
                                     parsed.home_games, parsed.away_games)
        winners.append(side)
        home_games += home
        away_games += away
    return (winners.count("home"), home_games, away_games, winners.count("away"))


def rows_against_box(card: Card, rule: SetRule, rules: Ruleset) -> str:
    """What a whole-match forfeit's set rows give against its box, or ``""``.

    The rows are evidence against the box only as the club's rules read them:
    every row a finished set under *rule*, the club's set rule, more sets to the
    side the box names than to the side that came, and the match to it by
    *rules*' ``decide_by``. A set the club's rule leaves unfinished, as a
    forfeit given up part way leaves one, is absence of evidence, never
    evidence, and rows giving the side that came as many sets give it nothing to
    answer. The one rule, which ``forfeit-contradicted``, the passes-by of
    ``verb-mismatch`` and ``totals-mismatch``, F6 and ``tools/doctor.py`` ask
    (4.134).
    """
    forfeited, came = card.forfeit_side, forfeit_match_winner(card.forfeit_side)
    if came is None or not all(_set_finished(s, rule) for s in card.sets):
        return ""
    tally = h_sets, h_games, a_games, a_sets = _card_tally(card, rule)
    won = {"home": h_sets, "away": a_sets}
    if won[forfeited] <= won[came] or rules.outcome(
            home_games=h_games, away_games=a_games,
            home_sets=h_sets, away_sets=a_sets) != forfeited:
        return ""
    return _rows_say(tally, rules, forfeited)


def _rows_say(tally: tuple[int, int, int, int], rules: Ruleset, side: str) -> str:
    """What a card's set rows give, decided on *rules*, in the words a detail uses."""
    h_sets, h_games, a_games, a_sets = tally
    return (f"sets {h_sets}-{a_sets} and games {h_games}-{a_games} decided on "
            f"{' then '.join(rules.decide_by)} say {side}")


def _restates_award(card: Card, fmt: MatchFormat) -> bool:
    """Whether a forfeit's printed TOTAL row totals what its box awards under *fmt*."""
    return card.totals.present and not card.totals.differs_on(
        box_award(card.forfeit_side, fmt))


def _results_printed(card: Card, fmt: MatchFormat) -> tuple[
        tuple[str, str, str | None], ...]:
    """A forfeit's printed results besides its rows: check, words, side it names.

    The side is the one each gives the match to, ``draw`` for a draw verb, or
    ``None``: nothing printed, a tick naming nobody, or a TOTAL row that does
    not total the box's award, which ``totals-mismatch`` holds to the rows.
    What ``forfeit-contradicted`` names, and what F6 passes by on a card it
    reports.
    """
    return (("verb", f"the verb {card.verb!r}",
             verb_side(card.verb) if card.verb else None),
            ("winner-tick", f"the {card.winner_tick} Match Winner tick",
             card.winner_tick),
            ("totals", "the printed TOTAL row",
             forfeit_match_winner(card.forfeit_side)
             if _restates_award(card, fmt) else None))


def restating_box(card: Card, fmt: MatchFormat) -> tuple[tuple[str, str], ...]:
    """What a forfeit prints restating its box under *fmt*: check and words.

    What F6 passes by and ``forfeit-contradicted`` names on a card that finding
    reports, and what ``tools/doctor.py`` says it names (4.134).
    """
    came = forfeit_match_winner(card.forfeit_side)
    return tuple((name, what) for name, what, side in _results_printed(card, fmt)
                 if side == came)


def as_restating(words: Sequence[str]) -> str:
    """``", as X and Y do"``, what restates a box said in a sentence, or ``""``."""
    return (f", as {in_words(words)} {'do' if len(words) > 1 else 'does'}"
            if words else "")


# --- what could not be checked -----------------------------------------


def _check_inputs(ctx: _Context) -> list[Issue]:
    """Say out loud which checks had no input to run against.

    The rule this whole program is built around: absence of evidence recorded as
    evidence is the failure mode of this family of programs. A clean report over no data
    is the purest form of it.

    None of these findings names the competition it is about, here or anywhere else in
    this module. Both readers of a finding already know: the report prints every message
    under the competition's id, and the page's data-health list is inside that
    competition's own dashboard. Saying it again read
    ``tuesday-division-c: tuesday-division-c has no matches``.
    """
    issues: list[Issue] = []
    comp = ctx.competition
    if not ctx.matches:
        issues.append(Issue(
            rule="no-matches", severity=WARNING,
            message="no matches were read, so nothing about its results could be "
                    "checked."))
    if ctx.matches and not ctx.cards:
        issues.append(Issue(
            rule="no-cards", severity=WARNING,
            message="the parsed scorecards were not supplied, so the card-level "
                    "cross-checks (F6), the printed TOTAL row and the result verb "
                    "could not be re-read.",
            detail=f"{len(ctx.matches)} match(es) validated from their scored form "
                   "alone"))
    if not comp.teams:
        issues.append(Issue(
            rule="no-teams", severity=WARNING,
            message="there is no team list, so no ladder, roster or team-identity "
                    "check could run."))
    elif not comp.teams_declared:
        issues.append(Issue(
            rule="F8", severity=WARNING,
            # The fact alone: what declaring the list takes depends on what the
            # file holds of it, which the report's note on the team list is told
            # (config.teams_instruction) and this is not.
            message="the team list was derived from the cards, not declared, so an "
                    "unknown team can only be reported as a suspicion.",
            detail=", ".join(sorted(comp.teams))))
    if not comp.rounds:
        issues.append(Issue(
            rule="no-rounds", severity=WARNING,
            message="there are no rounds, so no fixture could be checked against "
                    "a draw."))
    return issues


def _check_status(ctx: _Context) -> list[Issue]:
    """Matches that produce no result, each one named.

    A cancelled or unplayed match is not a defect, but it is the reason a team's
    counted total is short, and a reader who cannot see it here will look for the
    missing card instead. The forfeit case is the exception and an error: the
    predecessor scored a match flagged forfeit with neither side ticked by paying
    the forfeit-win value to *both* teams.

    A cancelled card that also names a winner, by its Match Winner tick or its
    verb, is still a washout, and still one warning: the Match Cancelled tick
    decides, which is a club's own rule and how Match Centre itself exports a
    night stopped by rain with the side ahead ticked. The warning says what else
    the card claims and that the tick decides (:func:`overruled`). It was an
    error (4.233), whose advice to untick one box and export again a club cannot
    take of a card Match Centre wrote; and a walkover, which ticks Match Cancelled
    too, is a forfeit and not this (4.418, 4.419).

    A card that records nothing (:func:`records_nothing`: every row 0-0, and no
    tick, verb or TOTAL row saying otherwise) is told this one finding, and the
    findings that would say it again stand down (4.424). A night not called off
    is told what its rows read and, if it was played, to enter its result in
    Match Centre and export the card again, which is what a club can do about
    it. A night called off is told nothing more, since nothing needs doing.
    """
    issues: list[Issue] = []
    for match in chronological(ctx.matches, ctx.competition.rounds):
        if match.status is MatchStatus.FORFEIT and match.forfeited_by not in SIDES:
            issues.append(Issue(
                rule="forfeit-unattributed", severity=ERROR,
                message=f"{_label(match)}: the card is flagged as a forfeit but "
                        "neither side's box is ticked, so there is nobody to award "
                        "it to and the match cannot be scored.",
                subject=_label(match), detail=match.source))
        if match.disputed:
            # Counted as printed only when there is a result to count: a disputed
            # card short of its sets has none, and the no-result line says why.
            issues.append(Issue(
                rule="disputed", severity=WARNING,
                message=f"{_label(match)}: the card's Match Disputed box is ticked. "
                        "The result is counted as printed until the club says "
                        "otherwise."
                        if match.decided else
                        f"{_label(match)}: the card's Match Disputed box is ticked, "
                        "but the match has no result for the ladder to count.",
                subject=_label(match), detail=match.source))
        if not _no_result(match):
            continue        # a bye is normal; an unattributed forfeit is said above
        need = match.fmt.min_sets_for_result
        why = (f"only {match.completed_sets} of the {need} sets needed for a result "
               "are complete"
               if match.short_of_result
               else f"recorded as {match.status.value}")
        cards = [card for card in ctx.cards if _where(card) == match.source]
        claims = ([claim for card in cards for claim in winner_claims(card)]
                  if match.status is MatchStatus.CANCELLED else [])
        # A night not called off whose rows record nothing may be one nobody
        # entered a result for: the one finding about it says what it read, so a
        # misread card is caught, and what to do if it was played (4.424).
        blank = (match.status is not MatchStatus.CANCELLED
                 and any(records_nothing(match, card) for card in cards))
        issues.append(Issue(
            rule="no-result", severity=WARNING,
            message=f"{_label(match)}: {why}, so it is not counted in the ladder."
                    + (f" {overruled(claims)}." if claims else "")
                    + (" Every set row reads 0-0: if the match was played, enter "
                       "its result in Match Centre and export the card again."
                       if blank else ""),
            subject=_label(match), detail=match.source))
    return issues


def _no_result(match: Match) -> bool:
    """Whether *match* is one ``no-result`` names: no bye, forfeit or result.

    The condition :func:`_check_status` names a match by, and
    :func:`records_nothing` asks it, so the findings that stand down on a card
    recording nothing do so only where ``no-result`` is said of it.
    """
    return not (match.status is MatchStatus.BYE or match.decided
                or match.status is MatchStatus.FORFEIT)


def winner_claims(card: Card) -> list[str]:
    """What a card says won its match: its tick, then its verb.

    Asked of the card of a match called off. A night called off has no winner, and
    the Match Cancelled tick decides: Match Centre exports a washout with the side
    ahead when play stopped ticked and named by the verb, and a club's rules give
    both teams the washout (4.419). So what the card claims is said beside the
    washout's one warning, and overruled (:func:`overruled`). That is a question
    about the card's own boxes, not its rows, which stay held to nothing. A drawn
    verb, ``VS`` and a word nobody knows name no winner. :func:`_check_status`
    finds the card by the name it shares with its match. Public, as
    :func:`~matchcentre.discover.called_off` is, because ``tools/doctor.py`` says
    what the build does with one card called off.
    """
    claims: list[str] = []
    if card.winner_tick in SIDES:
        claims.append(f"the card ticks {card.winner_tick} as Match Winner")
    side = verb_side(card.verb) if card.verb else None
    if side in SIDES:
        claims.append(_verb_says(card.verb, side))
    return claims


def overruled(claims: Sequence[str]) -> str:
    """:func:`winner_claims` as a sentence the Match Cancelled tick overrules.

    "The card also ticks away as Match Winner and the verb 'DEF. BY' means away
    won; the Match Cancelled tick decides", with no full stop: the washout's
    ``no-result`` warning and the doctor's verdict both say it, in these words.
    """
    said = in_words(claims).replace("the card ticks", "the card also ticks", 1)
    return f"{said[:1].upper()}{said[1:]}; the Match Cancelled tick decides"


def records_nothing(match: Match, card: Card) -> bool:
    """Whether the card of *match* records no result and no play, nor claims one.

    Such a card is told one finding, ``no-result`` (4.424). The findings that
    would each say again, in their own words, that nothing on it was played or
    confirmed stand down on it: F6's warning that nothing confirms its rows,
    ``totals-missing``, ``players-unrecorded``, and ``verb-absent`` on a disputed
    card, which counts. Each asks this, through :meth:`_Context.records_nothing`,
    so a check added later asks it too. So do the page's notes on its match,
    which leave off the reader's two warnings that a cross-check is missing
    (``export._notes``).

    Three things. ``no-result`` names the match (:func:`_no_result`): a walkover
    or a forfeit has a result, and is not one. Every row is blank
    (:func:`~matchcentre.model.all_blank`, the test the build records a night
    unplayed by), so no game is left unverified. And the card claims no result:
    it is called off, which holds nothing it prints to its rows, or it prints
    ``VS`` or no verb and no cross-check disagrees with its rows. Blank rows are
    what a misread card looks like too, and a verb naming a result, a word
    nobody reads or a tick over them is the evidence of it, so such a card keeps
    every finding. Public, as :func:`winner_claims` is, because
    ``tools/doctor.py`` says what the build does with one card.

    Rejected: asking only the first two, which told a card printing ``DEFEATS``
    and ticking home over rows read 0-0 to enter a result it prints, and took its
    ``totals-missing``; and a second blank test of the card's own rows, which
    would part from the build's the day either changed what a rubber is worth.
    """
    if not _no_result(match) or not all_blank(match.sets):
        return False
    if called_off(card):
        return True
    return (all(check.agrees for check in card.cross_checks_available)
            and (not card.verb or states_no_result(card.verb)))


# --- F4: every card is accounted for ------------------------------------


def _check_cards_accounted(ctx: _Context) -> list[Issue]:
    """Rule F4. A PDF that was found and not scored is named, by path and reason.

    An error every time, without exception. A scorecard that vanishes quietly is
    the most expensive failure this tool can have: the ladder stays plausible and a
    team is simply short a match, which nothing downstream can detect.
    """
    issues: list[Issue] = []
    for rejection in sorted(ctx.rejected, key=lambda r: (r.path, r.page)):
        # The detail is the refused entry's own (validation.rejected), which the
        # report and the page both print, so here it is only pointed to.
        where = (" The list of cards not scored gives its detail."
                 if rejection.detail else "")
        issues.append(Issue(
            rule="F4", severity=ERROR,
            message=f"{rejection.describe()} — found under data/ and not "
                    f"scored.{where}",
            subject=rejection.path))
    return issues


# --- F6: at least one independent cross-check must agree -----------------


def _check_cross_checks(ctx: _Context) -> list[Issue]:
    """Rule F6. A card must reproduce at least one of its own printed results.

    Zero available cross-checks is a warning: some templates print none, and that
    is a fact about the template, not evidence of a bad parse. A cross-check that
    *disagrees* is an error, and it names both readings, because the predecessor's
    fail-open column grid returned confidently reversed scorelines that nothing
    else caught.

    A verb the rows contradict is ``verb-mismatch``'s to report where it reports
    the card, and so is a tick naming the side the verb names: one misprint, and
    the report that names what the card is scored on. Whether it does turns on
    whether the card counts, its sets finished under the club's set rule and the
    rules that decide it, which the reader cannot see, so it is asked here, of
    the check itself, rather than written on the card by the reader. A TOTAL row
    the rows do not add to is ``totals-mismatch``'s, which names the cells, and
    is asked the same way: it holds no card called off to its rows. On a card
    ``forfeit-contradicted`` reports, a verb, tick or TOTAL row restating the
    box is that finding's, which names it, and is asked the same way too, since
    it turns on the club's set rule and ``decide_by`` (4.134).

    Each says what it was held to, the set rows or a forfeit's box, and a verb
    printed that confirms nothing is said as printed, from the check (4.152,
    4.133).

    A card that prints no check is told so, unless it records nothing
    (:func:`records_nothing`): with every row 0-0 and no result counted, no
    figure rests on its rows for a check to confirm, and its ``no-result`` is
    the one finding about it (4.424).
    """
    issues: list[Issue] = []
    by_verb = {issue.subject for issue in _check_verb(ctx)
               if issue.rule == "verb-mismatch"}
    by_totals = {issue.subject for issue in _check_totals(ctx)
                 if issue.rule == "totals-mismatch"}
    by_rows = {issue.subject for issue in _check_forfeit_rows(ctx)}
    for card in ctx.in_play:
        failed = card.cross_checks_failed
        if _where(card) in by_totals:
            failed = tuple(check for check in failed if check.name != "totals")
        if _where(card) in by_rows:
            named = {name for name, _ in restating_box(card, ctx.competition.fmt)}
            failed = tuple(check for check in failed if check.name not in named)
        if _where(card) in by_verb:
            said = verb_side(card.verb)
            failed = tuple(check for check in failed if check.name != "verb"
                           and not (check.name == "winner-tick"
                                    and card.winner_tick == said))
        for check in failed:
            issues.append(Issue(
                rule="F6", severity=ERROR,
                message=f"{_where(card)}: the {check.name} cross-check disagrees "
                        f"with {check.held_to}, so this card's numbers are not "
                        f"trustworthy.",
                subject=_where(card),
                detail=check.detail or f"{check.name} check failed"))
        if not card.cross_checks_available and not ctx.records_nothing(card):
            nouns = {"totals": "TOTAL row", "verb": "result verb",
                     "winner-tick": "winner tick"}
            issues.append(Issue(
                rule="F6", severity=WARNING,
                message=f"{_where(card)}: the card prints no "
                        f"{thin_card_prints(card.checks, nouns, 'or')}, so nothing "
                        "on it confirms the set rows were read correctly.",
                subject=_where(card),
                detail=f"{len(card.sets)} set row(s) read"))
    return issues


# --- the card's own printed totals --------------------------------------


def _check_totals(ctx: _Context) -> list[Issue]:
    issues: list[Issue] = []
    rule = ctx.rule
    for card in ctx.in_play:
        if not card.totals.present:
            if ctx.records_nothing(card):
                continue    # no game to verify, and no-result says so (4.424)
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
        if (wrong and card.forfeit_side
                and rows_against_box(card, rule, ctx.competition.rules)
                and _restates_award(card, ctx.competition.fmt)):
            # A row totalling the box's award over rows against the box is the
            # rows' finding's, which names it (4.134); asked, as the award is, only
            # where a box is ticked, or a walkover read.
            continue
        if wrong and card.walkover_by:
            # A walkover's TOTAL row is what made it one: the award, under the
            # club's set rule, whatever the rows hold, which the reader held it to
            # under every rule it might be (4.418). So it is held to that alone.
            award = box_award(card.walkover_by, ctx.competition.fmt)
            against = printed.differs_on(award)
            if against:
                issues.append(Issue(
                    rule="totals-mismatch", severity=ERROR,
                    message=f"{_where(card)}: the printed TOTAL row and "
                            f"{_given_up(card)} disagree on {in_words(against)}.",
                    subject=_where(card),
                    detail=f"printed {printed.describe()}; a walkover's TOTAL row "
                           f"totals every rubber to "
                           f"{forfeit_match_winner(card.walkover_by)} as a love "
                           f"set, {award.describe()} under {rule.name}"))
            continue
        # Asked only where a box is ticked, so a played card's row needs nothing
        # of the context but the set rule.
        award = (forfeit_award(card.sets, card.forfeited_by, ctx.competition.fmt)
                 if wrong and card.forfeited_by else None)
        if wrong and award is not None:
            # Rows nobody played give no result, and the box is what such a card
            # is scored on, so a row totalling what the box awards agrees too:
            # every rubber of the format to the side that came, each a love set
            # under the club's set rule. Held whole, so one cell may not total the
            # award and the next the rows.
            against = printed.differs_on(award)
            if against:
                issues.append(Issue(
                    rule="totals-mismatch", severity=ERROR,
                    message=f"{_where(card)}: the printed TOTAL row and the "
                            f"{card.forfeited_by} Match Forfeited box disagree on "
                            f"{in_words(against)}.",
                    subject=_where(card),
                    detail=f"printed {printed.describe()}; a TOTAL row may total "
                           f"the forfeit, every rubber to "
                           f"{forfeit_match_winner(card.forfeited_by)} as a love "
                           f"set, {award.describe()}, or the set rows, sets "
                           f"{h_sets}-{a_sets} games {h_games}-{a_games}"))
            continue
        # A TOTAL row may total the rows as printed or as they are scored, a
        # forfeited rubber a love set and a retired one raised to win (4.421,
        # 4.422), which is Match Centre's own arithmetic. Held whole to one or the
        # other, and the disagreement named against the nearer. Asked only of a
        # card a rubber of which was not played out, so a played card's row
        # still needs nothing of the context but the set rule.
        scored = (h_sets, h_games, a_games, a_sets)
        if wrong and (card.forfeit_side in SIDES or any(
                s.status is not SetStatus.PLAYED for s in card.sets)):
            scored = _card_scored(card, rule, ctx.competition.rules)
        against = printed.differs_on(Totals(*scored)) if wrong else []
        if wrong and against:
            rows = f"sets {h_sets}-{a_sets} games {h_games}-{a_games}"
            if scored != (h_sets, h_games, a_games, a_sets):
                rows = (f"sets {scored[0]}-{scored[3]} games {scored[1]}-"
                        f"{scored[2]} as scored under {rule.name}, or {rows} as "
                        "printed")
            named = against if len(against) < len(wrong) else wrong
            issues.append(Issue(
                rule="totals-mismatch", severity=ERROR,
                message=f"{_where(card)}: the printed TOTAL row and the set rows "
                        f"disagree on {in_words(named)}.",
                subject=_where(card),
                detail=f"printed {printed.describe()}; set rows add to {rows}"))
    return issues


# --- the card's own result verb -----------------------------------------


def _verb_says(verb: str, side: str) -> str:
    """What a result verb says of the match: that a side won, or that it was drawn.

    ``draw`` is no side, and "means draw won" is no sentence.
    """
    if side == "draw":
        return f"the verb {verb!r} says the match was drawn"
    return f"the verb {verb!r} means {side} won"


def _check_verb(ctx: _Context) -> list[Issue]:
    issues: list[Issue] = []
    rules = ctx.competition.rules
    fmt = ctx.competition.fmt
    for card in ctx.in_play:
        if not card.verb:
            continue
        expected = verb_side(card.verb)
        if expected is None and not states_no_result(card.verb):
            # Whether the verb can be read at all is a question of how the card was
            # read, asked of every card in play. On a card read empty it is the one
            # finding that names the word its zeros may have been misread under.
            issues.append(Issue(
                rule="verb-unknown", severity=WARNING,
                message=f"{_where(card)}: result verb {card.verb!r} is not one this "
                        "program recognises, so the computed result was not "
                        "cross-checked against it.",
                subject=_where(card), detail=card.verb))
            continue
        if not ctx.counts(card) or ctx.records_nothing(card):
            # `no-result` already names a match that counts for nothing, and it has
            # no counted result for a verb to state or to leave out. Two findings
            # about one fact teach a club to skim the list. A disputed card that
            # records nothing counts, and has no rows for the verb to check (4.424).
            continue
        if expected is None:
            issues.append(Issue(
                rule="verb-absent", severity=WARNING,
                message=f"{_where(card)}: the card prints {card.verb!r} between "
                        f"the team names instead of a result, which is what "
                        f"Match Centre prints for a fixture it holds no result "
                        f"for. The set rows were still read and scored; what "
                        f"could not run is this one cross-check against them.",
                subject=_where(card), detail=card.verb))
            continue
        # Held to the rows as printed or as the match is scored, a forfeited
        # rubber a love set and a retired one raised to win (4.421, 4.422), as the
        # TOTAL row is: Match Centre's verb states the result its award gives, and
        # a card filled in as played, as 0.1.0's writer filled one, the result the
        # games printed give. A verb either bears out agrees, as the reader's does.
        scored = _card_scored(card, ctx.rule, rules)
        printed = _card_tally(card, ctx.rule)
        if card.forfeit_side in SIDES:
            # A whole-match forfeit goes to whoever did not forfeit, whatever the
            # games say — they are often 0-0 on a card nobody turned up for, and
            # comparing the verb against them would report every forfeit as a
            # contradiction. Match.winner asks the same function. A walkover is
            # one (4.418).
            actual = forfeit_match_winner(card.forfeit_side)
            # A verb its rows agree with, against the box, is the rows' finding's,
            # which names it (4.134).
            if actual != expected and not (
                    expected == card.forfeit_side
                    and rows_against_box(card, ctx.rule, rules)):
                issues.append(Issue(
                    rule="verb-mismatch", severity=ERROR,
                    message=f"{_where(card)}: {_verb_says(card.verb, expected)}, "
                            f"but the card says {card.forfeit_side} forfeited the "
                            "match.",
                    subject=_where(card),
                    detail=f"verb {card.verb!r} says {expected}; "
                           f"{_given_up(card)} gives the match to {actual}"))
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
        gives = {}
        for h_sets, h_games, a_games, a_sets in (scored, printed):
            winner = rules.outcome(home_games=h_games, away_games=a_games,
                                  home_sets=h_sets, away_sets=a_sets)
            gives[h_sets, h_games, a_games, a_sets] = (
                "draw" if winner is None else winner)
        if expected in gives.values():
            continue
        actual, rows = gives[scored], _rows_say(scored, rules, gives[scored])
        if printed != scored:
            # Named against both, as a TOTAL row of neither is.
            rows += (f" as scored under {ctx.rule.name}, and "
                     f"{_rows_say(printed, rules, gives[printed])} as printed")
            if gives[printed] != actual:
                actual = f"{actual} as scored and {gives[printed]} as printed"
        issues.append(Issue(
            rule="verb-mismatch", severity=ERROR,
            message=f"{_where(card)}: {_verb_says(card.verb, expected)}, but "
                    f"the set rows give {actual}.",
            subject=_where(card),
            detail=f"verb {card.verb!r} says {expected}; {rows}"))
    return issues


def _check_forfeit_rows(ctx: _Context) -> list[Issue]:
    """A whole-match forfeit whose set rows give the match to the side that forfeited.

    The box and the rows state the result two ways, and on these rows, as the
    club's set rule and ``decide_by`` read them (:func:`rows_against_box`),
    they disagree. Reported once, as an error, naming what else the card prints
    on either side of it: F6 passes by a verb, tick or TOTAL row restating the
    box, ``verb-mismatch`` a verb for the side that forfeited, and
    ``totals-mismatch`` a TOTAL row totalling the box's award under the club's
    set rule. A draw verb says what neither does, and is still
    ``verb-mismatch``'s (4.134).
    """
    issues: list[Issue] = []
    for card in ctx.in_play:
        reading = rows_against_box(card, ctx.rule, ctx.competition.rules)
        if not reading:
            continue
        forfeited, came = card.forfeit_side, forfeit_match_winner(card.forfeit_side)
        printed = _results_printed(card, ctx.competition.fmt)
        box = [what for _, what in restating_box(card, ctx.competition.fmt)]
        rows = [what for _, what, side in printed if side == forfeited]
        detail = f"{reading}; {_given_up(card)} gives the match to {came}"
        detail += as_restating(box)
        if rows:
            detail += (f", and {in_words(rows)} {'give' if len(rows) > 1 else 'gives'}"
                       f" it to {forfeited}")
        issues.append(Issue(
            rule="forfeit-contradicted", severity=ERROR,
            message=f"{_where(card)}: the card says {forfeited} forfeited the match, "
                    f"but its set rows, every set finished, give {forfeited} more "
                    "sets and the match.",
            subject=_where(card), detail=detail))
    return issues


def _check_walkover_rows(ctx: _Context) -> list[Issue]:
    """A walkover whose set rows hold play its own TOTAL row does not count.

    Match Centre writes a match one side gave up as Match Cancelled ticked beside
    a winner and a TOTAL row awarding the side that came every rubber as a love
    set, and on one of a club's real walkovers it did so over rows of play,
    2-6, 6-4, 3-6 and 0-0, on which the side ticked won one rubber and lost two.
    The match is scored as the walkover (4.418), so the rows count for nothing in
    its result, and this says so once, as a warning, naming what the card prints
    that makes it one, where the rows hold play the TOTAL row leaves out
    (:func:`uncounted_play`). Not where ``forfeit-contradicted`` reports the rows,
    which is the same fact said as an error (:func:`rows_against_box`).
    """
    issues: list[Issue] = []
    for card in ctx.in_play:
        side = card.walkover_by
        if (side is None or not uncounted_play(card)
                or rows_against_box(card, ctx.rule, ctx.competition.rules)):
            continue
        came = forfeit_match_winner(side)
        said = ["the TOTAL row"]
        if card.winner_tick in SIDES:
            said.append(f"the {card.winner_tick} Match Winner tick")
        if card.verb:
            said.append(f"the verb {card.verb!r}")
        issues.append(Issue(
            rule="walkover-rows-played", severity=WARNING,
            message=f"{_where(card)}: the card ticks Match Cancelled and prints the "
                    f"TOTAL row of a walkover conceded by {side}, every rubber to "
                    f"{came} as a love set, but its set rows hold games "
                    f"{card.home_games}-{card.away_games} that the TOTAL row does "
                    f"not count. The match is scored as the walkover "
                    f"{in_words(said)} describe, a forfeit by {side}.",
            subject=_where(card),
            detail="set rows " + ", ".join(f"{s.home_games}-{s.away_games}"
                                           for s in card.sets)
                   + f"; printed {card.totals.describe()}"))
    return issues


def uncounted_play(card: Card) -> bool:
    """Whether a walkover's set rows hold play its printed TOTAL row leaves out.

    Its TOTAL row credits the side that came every rubber as a love set and the
    side that gave the match up nothing, so rows of love sets to the side that
    came, or of love sets and 0-0, hold no game it leaves out, and a game to the
    side that gave up, or more to the side that came than the award, is one.
    Asked of a card :attr:`~matchcentre.card.Card.walkover_by` names, whose every
    TOTAL cell is printed. The one rule, which ``walkover-rows-played`` and
    ``tools/doctor.py`` ask (4.418). Rejected: asking whether the rows hold any
    games, which told four love sets to the side that came that their TOTAL row
    of 4-0 and 24-0 does not count the 24-0 it is.
    """
    return (card.home_games > card.totals.home_games
            or card.away_games > card.totals.away_games)


# --- name cells nobody filled in ----------------------------------------


def _check_unnamed(ctx: _Context) -> list[Issue]:
    """Cards with rubbers whose players were never entered in Match Centre.

    The site prints its own ``Unspecified player`` placeholder in a name cell
    nobody filled in, and :func:`~matchcentre.scorecard.names_nobody` drops it
    rather than reading it as a person — a placeholder read as a name becomes a
    player with a playing record, a squad of one on no team whose only opponent is
    itself. Dropping it silently would be the other failure: a card thinned out
    with no trace is exactly the kind of quiet loss this program refuses
    elsewhere. So each card that lost names says so once, with the count, whether
    or not the match was played, unless the card records nothing at all.

    The usual cause is a fixture called off before the teams were entered, in
    which case nothing needs doing: every row reads 0-0, and its ``no-result`` is
    the one finding about it (:func:`records_nothing`, 4.424). The one worth
    acting on is a card that *was* played and still prints the placeholder,
    because those rubbers have games but no one to credit them to: fix the entry
    in Match Centre and re-export.
    """
    issues: list[Issue] = []
    for card in ctx.cards:
        cells = sum(s.unnamed for s in card.sets)
        if not cells or ctx.records_nothing(card):
            continue
        rows = sum(1 for s in card.sets if s.unnamed)
        # Worded from what the match is, not from the Cancelled box: a walkover
        # ticks it and is not called off, and nor is a forfeit ticking it too
        # (4.418).
        played = "; this match was called off" if called_off(card) else \
                 (_uncredited(card)
                  if any(s.unnamed and (s.home_games or s.away_games)
                         for s in card.sets) else
                  "; this match was forfeited whole, so nobody played those "
                  "rubbers" if card.forfeit_side else "")
        # Which side printed it, so the club knows whose entry to fix (4.448).
        sides = [f"{side} in rubber(s) "
                 + ", ".join(str(s.index + 1) for s in card.sets
                             if s.unnamed_for(side))
                 for side in SIDES if any(s.unnamed_for(side) for s in card.sets)]
        issues.append(Issue(
            rule="players-unrecorded", severity=WARNING,
            message=f"{_where(card)}: {cells} name cell(s) across "
                    f"{rows} of {len(card.sets)} rubber(s) print Match Centre's "
                    f"'Unspecified player' placeholder rather than a person, so no "
                    f"name was recorded for them{played}.",
            subject=_where(card),
            detail=f"{cells} placeholder cell(s): {'; '.join(sides)}"))
    return issues


def _uncredited(card: Card) -> str:
    """What a played card's placeholder cells leave credited to nobody, by side.

    A side printing the placeholder and naming nobody on a rubber with games has
    nobody to credit them to; a pair naming one player above it has half the
    rubber's games credited to nobody, since a doubles rubber is shared by its
    two places. Said per side and rubber, so the club knows which entry to fix in
    Match Centre: one clause said nobody was credited on every such rubber, of
    pairs whose one name is credited, the detail naming no side (4.448). Said of
    the rubbers with games alone, which a night stopped before its last rubber
    or a forfeit given up after the rubbers it played leaves some of: a rubber
    left 0-0 has no games to credit, and "those rubbers have games" was said of
    it too.
    """
    nobody: list[str] = []
    half: list[str] = []
    for side in SIDES:
        placed = [s for s in card.sets
                  if s.unnamed_for(side) and (s.home_games or s.away_games)]
        none = [str(s.index + 1) for s in placed if not s.players_for(side)]
        one = [str(s.index + 1) for s in placed
               if s.doubles and len(s.players_for(side)) == 1]
        if none:
            nobody.append(f"the {side} side of rubber(s) {in_words(none)}")
        if one:
            half.append(f"the {side} side of rubber(s) {in_words(one)}")
    clauses = []
    if nobody:
        clauses.append(f"nobody to credit them to on {in_words(nobody)}")
    if half:
        clauses.append(f"only one player of the pair to credit on {in_words(half)}, "
                       "so half of those games are credited to nobody")
    if not clauses:
        return ""
    lead = ("those rubbers have games but"
            if all(s.home_games or s.away_games for s in card.sets if s.unnamed)
            else "those rubbers with games have")
    return f"; {lead} " + ", and ".join(clauses)


# --- every card belongs to the competition being scored -----------------


def _check_competition_id(ctx: _Context) -> list[Issue]:
    """A card scored under the wrong competition is invisible everywhere else.

    It names real teams, scores plausible points and lands on a real ladder row.
    Slug identity makes the cross-division merge far less likely than it was with
    reused draw numbers, but two divisions can still share a team name, and a
    season key mistake would put last season's round 1 on this season's ladder.
    """
    issues: list[Issue] = []
    for match in chronological(ctx.matches, ctx.competition.rounds):
        if match.competition != ctx.competition.id:
            issues.append(Issue(
                rule="wrong-competition", severity=ERROR,
                message=f"{_label(match)}: this card belongs to "
                        f"{match.competition!r} but is being scored as part of "
                        f"{ctx.competition.id!r}, which would merge two ladders.",
                subject=_label(match), detail=match.source))
    return issues


def _check_letterhead(ctx: _Context) -> list[Issue]:
    """Cards printing a letterhead the rest of this competition's cards do not.

    Routing is by what a card prints -- its division and the night its
    competition line names, else the weekday of its own date -- which is what
    makes this worth saying: a PDF exported from *another* club's Tuesday B
    division lands in this one, names teams nobody here has heard of, and takes a
    ladder position. The letterhead is the only thing on the card
    that says so, and it is otherwise settled by a majority vote whose losers
    nothing records.

    A warning, and the card is still scored, because a letterhead is a *label*.
    The same club prints two spellings of its own name often enough -- an `INC`
    dropped, a district abbreviated -- and a tool that refused a night's tennis
    over one of those would be a tool a club cannot use. So this reports rather
    than decides: the path, both spellings, and the fact that the result is in the
    ladder. Which of the two explanations it is, only the club knows.

    A card whose letterhead did not read is not a second club and is not counted
    towards one: an empty hint is the absence of evidence, and this file does not
    record that as evidence of anything.

    Cards are compared through :func:`~matchcentre.card.letterhead_key`, the rule
    the demo's own-card test and the fetcher's keep test ask, so a card is never
    reported for its capitals or its spacing alone, the difference those two
    already forgive. It is still a majority vote, and never sees the letterhead the
    fetcher was given, so the club's own cards can be the ones outvoted. A
    letterhead named is quoted in every spelling its cards print
    (:func:`~matchcentre.card.letterhead_spellings`). The rest is the one
    :func:`~matchcentre.card.majority_letterhead` gives, the rule the config stub's
    club name is asked of, quoted in its spelling and counted by the cards that
    print it so.
    """
    letterheads: dict[str, list[Card]] = {}
    for card in ctx.cards:
        key = letterhead_key(card.club_hint)
        if key:
            letterheads.setdefault(key, []).append(card)
    if len(letterheads) < 2:
        return []
    printed = {key: letterhead_spellings(cards) for key, cards in letterheads.items()}
    # majority_letterhead groups by the same letterhead_key and folds spacing as
    # letterhead_spellings does, so it names one of these keys and one of its
    # spellings.
    rest, majority = majority_letterhead(ctx.cards)
    count = dict(printed[rest])[majority]
    total = len(letterheads[rest])
    others = f"the other {count}" if count == total else f"{count} of the other {total}"
    issues: list[Issue] = []
    for key, cards in sorted(letterheads.items()):
        if key == rest:
            continue
        hints = in_words((repr(hint) for hint, _ in printed[key]), "or")
        where = in_words(_where(card) for card in cards)
        one = len(cards) == 1
        issues.append(Issue(
            rule="foreign-card", severity=WARNING,
            message=f"{where}: "
                    + ("this card's letterhead reads" if one
                       else "these cards' letterheads read")
                    + f" {hints}, where {others} card(s) scored as "
                      f"{ctx.competition.id} read {majority!r}. "
                    + ("It has been scored and its result is in the ladder, with "
                       "its teams and players. If it belongs to another club's "
                       "competition, take it out of data/ and build again; if it is "
                       "this club's card with the name printed differently, nothing "
                       "needs doing." if one else
                       "They have been scored and their results are in the ladder, "
                       "with their teams and players. If they belong to another "
                       "club's competition, take them out of data/ and build again; "
                       "if they are this club's cards with the name printed "
                       "differently, nothing needs doing."),
            subject=where,
            detail=", ".join(f"{printing} card(s) print {hint!r}"
                             for hint, printing in printed[key])))
    return issues


def _check_self_play(ctx: _Context) -> list[Issue]:
    issues: list[Issue] = []
    for match in chronological(ctx.matches, ctx.competition.rounds):
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

    A warning when the list was derived, with the suggestion, because there is
    nothing yet to contradict — but "did you mean *iguanas*?" is how a typo'd banner
    gets found. The teams asked about are
    :func:`~matchcentre.discover.nearest_team`'s, the ones the page's warning and
    discovery's note ask about, so one banner is never asked about two ways.

    Under a declared list the error owns the banner, and discovery writes no note of
    its own about it: this is what reaches season.json and the page and survives
    ``--quiet``. So it gives the remedy as well as the question, which
    :func:`_alias_remedy` words.
    """
    issues: list[Issue] = []
    comp = ctx.competition
    if not comp.teams:
        return issues        # already reported by _check_inputs
    unknown: dict[str, list[Match]] = defaultdict(list)
    for match in chronological(ctx.matches, ctx.competition.rounds):
        # A set, so a match that names one team on both sides is listed once here;
        # naming itself is _check_self_play's finding, not this one's.
        for slug in {match.home, match.away}:
            if match.is_bye and slug == match.away:
                continue
            if slug not in comp.teams:
                unknown[slug].append(match)
    for slug, involved in sorted(unknown.items()):
        near = nearest_team(slug, comp.teams)
        hint = did_you_mean(near, keyed=False)
        where = in_words([_label(m) for m in involved])
        if comp.teams_declared:
            issues.append(Issue(
                rule="F8", severity=ERROR,
                message=f"{slug!r} plays in {where} but is not in the declared team "
                        f"list for {comp.id}.{hint} Either the banner or the list is "
                        f"wrong{_alias_remedy(ctx, slug, near)}",
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


def _alias_remedy(ctx: _Context, slug: str, near: list[str]) -> str:
    """How F8 ends for a banner a declared list lacks: the line to paste, or why none.

    The ``[aliases]`` line to paste, keyed and valued by
    :func:`~matchcentre.config.toml_key` and :func:`~matchcentre.config.toml_value`,
    where one team is *near*est. None where *slug* is a team of another competition
    in ``ctx.club``, its list declared or derived, because an alias folds a slug on
    every card of every competition: ``kenmore-4 = "kenmure-3"`` for Division B
    refiled Division C's Kenmore 4 under ``kenmure-3`` with a clean build. A
    competition with no cards yet is in no club, so a team of it is not seen. None
    where no team is nearest or two are, because the cards do not say which team the
    banner is, if any, and a side of the club is asked nothing for that reason.
    Rejected: the line whatever else the slug names, and a line with a placeholder
    for the team, which filled in with the only team near did the same to a side.
    """
    # This competition is among them, and lacks the slug: that is why F8 is here.
    elsewhere = sorted(other.id for other in (ctx.club.competitions if ctx.club else ())
                       if slug in other.teams)
    if elsewhere:
        return (f". No [aliases] line is offered: {slug} is also a team of "
                f"{in_words(elsewhere)}, and [aliases] applies to every competition, "
                f"so a line filing it here would refile it there. If it is the banner, "
                f"correct the card in Match Centre and export it again.")
    if len(near) != 1:
        return ("; if it is the banner, [aliases] files it under the declared team it "
                "is, and the cards do not say which that is, so no line is offered.")
    return (f"; if it is the banner, {toml_key(slug)} = {toml_value(near[0])} under "
            f"[aliases] is the one-line fix.")


# --- F9: duplicates -----------------------------------------------------


def _check_duplicates(ctx: _Context) -> list[Issue]:
    """Rule F9, plus the two neighbouring forms of the same mistake.

    Two cards for one fixture are one of three situations, and which one decides
    whether a club has anything to do about it.

    *The same card twice* — a night exported again, the second file named
    ``… (1).pdf``, the first never deleted. It is counted once
    (:func:`~matchcentre.stats.duplicates`), so every figure on the page is right
    and what is left is a tidy-up in a folder. A warning, and the message says the
    numbers are not affected, because an error that stops a build over a spare file
    teaches a club to ignore errors.

    *Two cards that disagree about the result* — a corrected export whose scores are
    not the ones it replaces. Neither is counted, because picking one would be this
    program deciding which result is true, and there is no honest rule for that: the
    later file, the higher id and the longer scoreline are all guesses. So the
    fixture is missing from every figure until somebody deletes a file, and that is
    an error with a nonzero exit behind it.

    *Two cards that agree about the result and not about who played* — the same
    export with a name corrected. Both halves are said, because the two answers
    differ: the result is counted (both cards say it) and no appearance is credited
    (neither card can be trusted for that). Still an error, still exit 1 — a club
    reading its own dashboard is owed the fact that somebody's season is short a
    night, and only the club can fix it.

    One mistake is reported once. Two cards for a fixture are also two cards
    carrying one match id, and a team appearing twice in a round; saying it three
    ways would have a club hunting three problems. The id and team checks below
    therefore skip any group that is a single fixture, and keep the cases only they
    can see — one id printed on two different fixtures, and a team down to play
    twice in a night.

    The predecessor raised an uncaught ``ValueError`` on a duplicate fixture key
    and took the whole run down. Naming both source files is what a human needs to
    decide which card is the keeper.
    """
    issues: list[Issue] = []
    by_fixture: dict[tuple[str, str, str], list[Match]] = defaultdict(list)
    by_id: dict[str, list[Match]] = defaultdict(list)
    by_team_round: dict[tuple[str, str], list[Match]] = defaultdict(list)
    draw = ctx.competition.rounds
    for match in chronological(ctx.matches, draw):
        by_fixture[match.key].append(match)
        if match.match_id:
            by_id[match.match_id].append(match)
        if match.status is MatchStatus.BYE:
            # A rest is not a second night for the team that round. None reaches
            # here from a build, which hands this module the cards, and a rest the
            # cards contradict is `_check_byes`'s to say, once.
            continue
        for slug in {match.home, match.away}:
            by_team_round[(match.round, slug)].append(match)

    for key, group in sorted(by_fixture.items(),
                             key=lambda item: fixture_sort_key(item[0], draw)):
        if len(group) < 2:
            continue
        label = f"R{key[0]} {key[1]} v {key[2]}"
        detail = "; ".join(f"{m.source or 'unknown source'}"
                           f"{f' (match {m.match_id})' if m.match_id else ''}"
                           for m in group)
        # The same advice, in the one situation where every sentence below about
        # which file to get rid of is advice to throw away a season.
        seasons = _across_years(group, multi=ctx.multi_season,
                                config=ctx.config_name)
        # Worded for the number of cards, as the page words them: of three, one
        # or two may be wrong, and "both" and "the other" speak of two.
        two = len(group) == 2
        if all(same_result(group[0], other) for other in group[1:]):
            # Named by the rule that chose the card counted, which is not this
            # function's to choose again: in `detail`, either file could be it.
            spares = [m.source or "unknown source" for m in duplicates(group).copies]
            issues.append(Issue(
                rule="F9", severity=WARNING,
                message=f"{len(group)} cards claim round {key[0]} {key[1]} v "
                        f"{key[2]} and say the same thing about it, so it has been "
                        f"counted once and every figure on the page is right. "
                        + (seasons or (f"Delete the spare file {spares[0]}, the one "
                                       f"not counted, to keep the folder honest."
                                       if len(spares) == 1 else
                                       f"Delete the spare files {in_words(spares)}, "
                                       f"the ones not counted, to keep the folder "
                                       f"honest.")),
                subject=label, detail=detail))
        elif all(same_scoreline(group[0], other) for other in group[1:]):
            issues.append(Issue(
                rule="F9", severity=ERROR,
                message=f"{len(group)} cards claim round {key[0]} {key[1]} v "
                        f"{key[2]}, agree about the result and disagree about who "
                        f"played. The result is counted once — "
                        f"{'both cards say' if two else 'every card says'} the "
                        f"same about it — and no appearance is credited for the "
                        f"night, because crediting the card read first would put a "
                        f"real person in a rubber {'the other' if two else 'another'} "
                        f"card gives to somebody else. "
                        + (seasons or "Everybody's record for that night comes back "
                                      + ("when the card that is wrong leaves data/."
                                         if two else "when every card that is wrong "
                                                     "has left data/.")),
                subject=label, detail=detail))
        else:
            issues.append(Issue(
                rule="F9", severity=ERROR,
                message=f"{len(group)} cards claim round {key[0]} {key[1]} v "
                        f"{key[2]} and disagree about {_disagreement(group)}. So "
                        f"{'neither' if two else 'none of them'} is "
                        f"counted — not in the ladder, not in either team's row, "
                        f"not in anybody's playing record — because choosing "
                        f"between them is a guess this program will not make. "
                        + (seasons or f"To fix it, remove "
                                      f"{'the file' if two else 'every file'} that is "
                                      f"wrong from data/ and build again."),
                subject=label, detail=detail))

    for match_id, group in sorted(by_id.items()):
        if len(group) > 1 and len({m.key for m in group}) > 1:
            issues.append(Issue(
                rule="F9", severity=ERROR,
                message=f"match id {match_id} appears on {len(group)} cards, for "
                        f"different fixtures: {in_words(_label(m) for m in group)}.",
                subject=match_id,
                detail="; ".join(m.source or "unknown source" for m in group)))
    for (number, slug), group in sorted(
            by_team_round.items(), key=lambda item: fixture_sort_key(item[0], draw)):
        if len(group) > 1 and len({m.key for m in group}) > 1:
            # Added rather than substituted: this message never named a file to get
            # rid of, and what it was missing is any cause at all. A club reading
            # "counted twice" about a night it watched once has nothing to go on.
            seasons = _across_years(group, multi=ctx.multi_season,
                                    config=ctx.config_name)
            # Counted by fixture, once: a fixture on two cards is F9's to report,
            # and one of the team's matches that round, not two.
            fixtures = list(dict.fromkeys(_label(m) for m in group))
            times = "twice" if len(fixtures) == 2 else f"{len(fixtures)} times"
            issues.append(Issue(
                rule="team-twice-in-round", severity=ERROR,
                message=f"{slug} has {len(fixtures)} matches in round {number}: "
                        f"{in_words(fixtures)}. Its points for the "
                        f"round would be counted {times}."
                        + (f" {seasons}" if seasons else ""),
                subject=f"R{number} {slug}",
                detail="; ".join(m.source or "unknown source" for m in group)))
    return issues


def _across_years(group: Sequence[Match], *, multi: bool, config: str) -> str:
    """What to say about cards from more than one season, or ``""`` — the archive.

    Two cards for one fixture, or one team down to play twice in a night, are both
    reported above as something to fix by getting rid of a file. They are also both
    what a club gets the season it keeps last season's exports as well, because
    single-season mode files every card under ``data/`` into one competition and last
    season's round 1 lands on this season's. Then the file to get rid of is a real
    night, the club has nothing to put in its place, and following the advice loses
    a season to fix a round.

    The evidence is the dates and nothing else: one round cannot have been played in
    two different calendar years. Folder names cannot say it, because ``data/Round1``
    and ``data/2025`` are both folders directly under ``data/``; and a set of cards
    can be two seasons whatever anybody has named the folders, including nothing at
    all. :func:`~matchcentre.competition.years_played` is that reading, shared with
    the note :mod:`matchcentre.discover` prints before any of this is an error.

    ``multi`` is the mode, and the advice is the half of it a club has not done. In
    multi-season mode each folder directly under ``data/`` is a season, so two years
    of cards in one competition are two seasons exported into one season's folder --
    and a club told to set ``seasons = "multi"`` there is being told to write the line
    it already has. The note says nothing in that mode, so this sentence is the only
    one the club gets. ``config`` is the config file the build read, by its name.
    """
    years = years_played(group)
    if len(years) < 2:
        return ""
    listed = in_words(years)
    if multi:
        return (f"The cards were played in {listed}, so more than one season is "
                f"filed in one season's folder: in multi-season mode each folder "
                f"directly under data/ is a season, and one season's round lands on "
                f"another's. Give each season a folder of its own under data/ — each "
                f"then has its own ladder, and no file has to be thrown away.")
    return (f"The cards were played in {listed}, so more than one season is filed "
            f"here: single-season mode puts every card under data/ on one ladder, "
            f"and one season's round lands on another's. Set seasons = \"multi\" "
            f"under [build] in {config} and give each season a folder of its own "
            f"under data/ — each then has its own ladder, and no file has to be "
            f"thrown away.")


def _disagreement(group: Sequence[Match]) -> str:
    """What two cards for one fixture do not agree about, named for a human.

    Named because that is the first thing a club needs in order to work out which
    file is the wrong one. The fallback is deliberately not "something": every
    aspect above can match while the per-set breakdown differs, and the set rows
    are where the reader should then look.
    """
    parts: list[str] = []
    if len({(m.home_games, m.away_games) for m in group}) > 1:
        parts.append("the games — " + in_words(f"{m.home_games}-{m.away_games}"
                                               for m in group))
    if len({(m.home_sets, m.away_sets) for m in group}) > 1:
        parts.append("the sets — " + in_words(f"{m.home_sets}-{m.away_sets}"
                                              for m in group))
    if len({m.status for m in group}) > 1:
        parts.append("the status — " + in_words(m.status.value for m in group))
    if len({m.winner for m in group}) > 1:
        parts.append("who won")
    if len({tuple(sorted(m.players_on("home") + m.players_on("away")))
            for m in group}) > 1:
        parts.append("who played")
    return "; ".join(parts) or "the set rows"


# --- set counts and games, against the format and the set rule ----------


def _says_awarded(ctx: _Context, match: Match) -> bool:
    """Whether *match*'s card prints a TOTAL row of its rows as scored, not printed.

    Match Centre's own arithmetic counts a rubber ticked Forfeit against one side
    as a love set to the other, so a TOTAL row that totals the rows that way, every
    printed cell, and does not total the games printed, is the card saying the
    tick is meant (4.423). A card with no TOTAL row, or one that totals the games
    on court, says nothing of it.
    """
    rules = ctx.competition.rules
    for card in ctx.cards:
        if _where(card) == match.source and card.totals.present:
            return (not card.totals.differs_on(
                        Totals(*_card_scored(card, ctx.rule, rules)))
                    and bool(card.totals.differs_on(
                        Totals(*_card_tally(card, ctx.rule)))))
    return False


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
    # The rows that stopped with nothing ticked, by the rule each is told under,
    # told once for the competition after the loop (_stopped_rows). Each with the
    # key and score the loop has already said it by.
    stopped: dict[str, list[tuple[Match, str, str]]] = defaultdict(list)
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
                        f"({in_words(sorted(present))}).",
                subject=_label(match), detail=match.source))
        for result in match.sets:
            where = f"{_label(match)} {result.slot.key}"
            # The games printed, which are what was played and what was read; a
            # rubber's award scores it otherwise (4.421).
            home, away = result.printed_home_games, result.printed_away_games
            score = f"{home}-{away}"
            if not plausible(home, away, rule):
                issues.append(Issue(
                    rule="set-implausible", severity=ERROR,
                    message=f"{where}: {score} cannot happen under "
                            f"{rule.name} ({rule.describe()}), so this row was "
                            "misread.",
                    subject=where, detail=match.source))
                continue
            if result.status is SetStatus.FORFEIT:
                # A row whose two halves name different winners: a finished set on
                # the games printed, and the rubber credited by the tick to the
                # side that lost it. Asked of the set as scored rather than of the
                # ticks, so that which side forfeited is still decided in one
                # place; it can only be the side whose box was ticked. A warning,
                # because a card can mean it (a rubber awarded against the side
                # that won it on court, as against an ineligible player) or have
                # ticked the wrong side, and the games on court are not what is
                # scored. Not asked where the card's own TOTAL row totals the
                # rubber as the love set it is scored and not the games printed
                # (_says_awarded): the card has said what it meant (4.423).
                on_court = set_winner(home, away, rule)
                if (on_court is not None and on_court != result.winner
                        and not _says_awarded(ctx, match)):
                    team = match.team_for(on_court)
                    other = match.team_for(result.winner)
                    scored = (f"credited to {other}"
                              if ctx.competition.rules.forfeit_award == "as-played"
                              else f"scored {rule.games_to_win}-0 to {other} as "
                                   "forfeited")
                    issues.append(Issue(
                        rule="set-forfeit-contradicted", severity=WARNING,
                        message=f"{where}: the row ticks Forfeit against {team} "
                                f"and prints {score}, a set {team} won on court. "
                                f"The tick decides, so the set is {scored}; check "
                                "which of the two the card meant.",
                        subject=where, detail=match.source))
            if (result.status is SetStatus.RETIRED and result.awarded_to is None
                    and result.home_retired and result.away_retired):
                # Both Retired boxes: which side retired, and so whose set it is,
                # cannot be read from the card (4.422). One warning, of the one
                # row, where a box on one side gives the other the set.
                issues.append(Issue(
                    rule="set-retired-both", severity=WARNING,
                    message=f"{where}: both sides' Retired boxes are ticked, so "
                            "who retired cannot be read from the card, and nobody "
                            f"is credited the set; it counts at the {score} "
                            "printed.",
                    subject=where, detail=match.source))
            if not result.unfinished_unmarked:
                continue      # finished, or a box on the row says why it stopped
            # Warnings, not errors, and the reasoning is the severity rule itself: a
            # set nobody won awards nobody the set, whether or not the card admits
            # why it stopped, so the games and the set count are the same either
            # way. Real cards do this — one card in the season this project was
            # rewritten from has a doubles left at 2-4 with no box ticked, and its
            # printed TOTAL row agrees with the set rows regardless. Where enough
            # sets are unfinished to leave the match undecided, _check_status says
            # so separately.
            #
            # 0-0 is split out because "tied" is the wrong word for it. A row with
            # no games on it is not a contest that finished level, it is a rubber
            # nobody played, and a club reading "level at 0-0" is left to work that
            # out for itself. Measured on real cards: a washed-out night that
            # nobody ticked Match Cancelled on produced three of these, all read as
            # ties. A card whose every row is 0-0 with nothing ticked never reaches
            # here at all — _match_status calls that UNPLAYED and _check_status
            # names it — so what lands on this branch is a row left empty on a card
            # that has scores or a forfeited rubber elsewhere, or one that has a
            # whole-match box ticked. A whole-match forfeit is the one of those that
            # does say so: its rows are empty because nobody played them, and its
            # Match Forfeited box says why, so they are not reported
            # (_nobody_played, which set-players-missing asks too).
            if result.home_games == 0 and result.away_games == 0:
                if _nobody_played(match, result):
                    continue
                stopped["set-no-games"].append((match, where, score))
            elif result.home_games == result.away_games:
                stopped["set-tied"].append((match, where, score))
            else:
                stopped["set-unfinished"].append((match, where, score))
        # The one predicate the ladder uses to leave this match out and the page
        # uses to explain it, asked here so the three cannot disagree about which
        # cards they are talking about.
        if match.is_unscoreable_draw:
            issues.append(Issue(
                rule="draw-not-allowed", severity=ERROR,
                message=f"{_label(match)}: the match is level, but "
                        f"{ctx.competition.rules.name} has no value for a draw "
                        "(draws = false), so either a result was not recorded or a "
                        "row was misread. The night is left out of the ladder until "
                        "it is.",
                subject=_label(match), detail=match.source))
    issues.extend(_stopped_rows(name, stopped[name], rule)
                  for name in ("set-no-games", "set-tied", "set-unfinished")
                  if stopped[name])
    return issues


def _stopped_rows(name: str, rows: list[tuple[Match, str, str]],
                  rule: SetRule) -> Issue:
    """One competition's rows that stopped with no box ticked, told once a rule.

    ``set-no-games``, ``set-tied`` and ``set-unfinished`` were one finding a
    rubber, so a club whose play stops at a fixed time whatever the score had one
    a night: on one club's export, 69 of its 267 warnings, none of them about a
    match left undecided (row 4.434). Now each rule is one finding for the
    competition, listing its rubbers with their scores in the order the matches
    were played, and their cards as the detail, each once. That order is the
    loop's, :attr:`_Context.playable`'s: round 9 before round 10, which neither a
    card's path nor its round as text puts first.

    A lone rubber is the finding it always was, its key the subject the message
    begins with. A list of rubbers is no key: printed as a subject it would be the
    list again, under ``subject:`` in the report and in brackets on the page, so
    the listed finding has none, as the F8 warning listing a derived team list has
    none. Its key is the competition and the rule, of which there is one.

    A row of a match left short of a result is on the list with the rest, as what
    the row says is the same; ``no-result`` tells the match. Rejected: folding per
    card, which folds nothing where every played match finishes three or four sets
    of four, as on that export; per round, which folds only a round whose matches
    stopped under the same rule, 12 findings to 11 on the fictional cards it was
    measured on, where per competition is 6; a ``[rules]`` key to silence the three,
    a club rule no card states, under which a misread row would go unreported; and
    set-tied told as set-unfinished, as a row left level and a row with no games on
    it are told apart for what each is (4.453).
    """
    if len(rows) == 1:
        match, where, score = rows[0]
        if name == "set-no-games":
            message = (f"{where}: no games on the row at all and no box ticked, so "
                       "nobody is credited the set. A rubber that was never "
                       "played reads like this when the card does not say so.")
        elif name == "set-tied":
            message = (f"{where}: level at {score}, so nobody won the set and "
                       "nothing on the card says why it stopped.")
        else:
            message = (f"{where}: {score} is not a finished set under {rule.name} "
                       f"({rule.describe()}) and is not marked forfeit, retired or "
                       "incomplete, so nobody is credited the set.")
        return Issue(rule=name, severity=WARNING, message=message, subject=where,
                     detail=match.source)
    # No score where there are no games: "0-0" is the misreading set-no-games
    # exists to avoid.
    listed = in_words(where if name == "set-no-games" else f"{where} {score}"
                      for _, where, score in rows)
    cards = list(dict.fromkeys(match.source for match, _, _ in rows))
    if name == "set-no-games":
        message = (f"{len(rows)} rubbers have no games on their rows at all and no "
                   f"box ticked, so nobody is credited them: {listed}. A rubber "
                   "that was never played reads like this when the card does not "
                   "say so.")
    elif name == "set-tied":
        # Two of a card's rows can be the competition's only ones, and its match
        # is then short of a result too.
        on = "the card" if len(cards) == 1 else "the cards"
        message = (f"{len(rows)} rubbers were left level, so nobody won those sets "
                   f"and nothing on {on} says why they stopped: {listed}.")
    else:
        message = (f"{len(rows)} rubbers stopped short of a finished set under "
                   f"{rule.name} ({rule.describe()}) and are not marked forfeit, "
                   f"retired or incomplete, so nobody is credited them: {listed}.")
    return Issue(rule=name, severity=WARNING, message=message,
                 detail=", ".join(cards))


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
        unread = [side for side in ("home", "away") if not match.roster_for(side)
                  and not _never_came(match, side)]
        if unread:
            issues.append(_roster_missing(match, unread))
        for side in ("home", "away"):
            roster = match.roster_for(side)
            team = match.team_for(side)
            nameless: list[str] = []
            # Every name off the side's roster block is collected with the rows it
            # is on, and asked who it is once, after the rows: which of the five
            # per-name findings it gets turns on the side, its block and the squads,
            # never on the row, so one warning per set row said one fact once for
            # each row a name played. Every row is still named, in the subject, as
            # the place to look. A card whose roster was not read folds further,
            # one warning for every stranger on the side (``unplaced``): with no
            # squad to check against, each is one for the same reason, and one per
            # name on each row was 24 of 31 warnings on two such cards.
            outsiders: dict[str, list[str]] = {}
            unplaced: list[str] = []
            for result in match.sets:
                players = result.players_for(side)
                expected = result.slot.players
                where = f"{_label(match)} {result.slot.key}"
                # The side's 'Unspecified player' cells count as places nobody
                # filled, which players-unrecorded tells (4.448), and a row nobody
                # played is held to no names (4.446). A played card's row left
                # 0-0 with no box ticked is not one: the card does not say nobody
                # played it, and the players' figures count the names on it.
                if (names_miscounted(len(players), result.unnamed_for(side),
                                     expected)
                        and not _nobody_played(match, result)):
                    # The names go in the sentence and the card goes in the detail,
                    # which is the other way round from how this read first. A club
                    # cannot fix a missing name here -- the fix is in Match Centre --
                    # so of the two the path is the actionable half, and `detail` is
                    # where every other finding of that kind puts it.
                    issues.append(Issue(
                        rule="set-player-count", severity=WARNING,
                        message=f"{where}: the {side} side has {len(players)} "
                                f"player(s) named ({in_words(players)}), and "
                                f"{result.slot.label} is a "
                                f"{result.slot.discipline} set.",
                        subject=where, detail=match.source))
                if (not players and result.status is SetStatus.PLAYED
                        and result.unnamed_for(side) < expected
                        and not _nobody_played(match, result)):
                    # Collected and reported once per side: a card whose whole name
                    # column failed to read would otherwise emit one warning per set
                    # and bury everything else in the report. A whole-match
                    # forfeit's empty row names nobody because nobody played it.
                    # A side whose placeholder cells fill its places is
                    # players-unrecorded's (4.448); a placeholder beside a blank
                    # cell leaves a place that may be a name unread, still told.
                    nameless.append(result.slot.key)
                for slug in players:
                    if roster and slug in roster:
                        continue
                    outsiders.setdefault(slug, []).append(result.slot.key)
            for slug, rows in outsiders.items():
                where = f"{_label(match)} {in_words(rows)}"
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
                    if not roster:
                        unplaced.append(slug)
                        continue
                    issues.append(Issue(
                        rule="unknown-player", severity=WARNING,
                        message=f"{where}: {slug} is on neither the {side} "
                                "roster nor any registered squad, so this is "
                                "either a fill-in or a spelling that needs an "
                                "alias.",
                        subject=where, detail=match.source))
                elif known.team is None:
                    these = "this set" if len(rows) == 1 else "these sets"
                    issues.append(Issue(
                        rule="player-no-team", severity=WARNING,
                        message=f"{where}: {known.name} is registered in "
                                f"{comp.id} without a team, so {these} cannot "
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
            if unplaced:
                one = len(unplaced) == 1
                issues.append(Issue(
                    rule="unknown-player", severity=WARNING,
                    message=f"{_label(match)}: {in_words(unplaced)} played {side} "
                            f"for {team} and {'is' if one else 'are'} on no "
                            f"registered squad, and no {side} roster was read from "
                            "this card to check against, so "
                            f"{'this' if one else 'each'} is a fill-in, a spelling "
                            "that needs an alias, or a player no card has "
                            "registered.",
                    subject=_label(match), detail=match.source))
            if nameless:
                issues.append(Issue(
                    rule="set-players-missing", severity=WARNING,
                    message=f"{_label(match)}: no {side} player was read for "
                            f"{in_words(nameless)}, so those games are counted for "
                            f"{match.team_for(side)} but for nobody in it.",
                    subject=_label(match), detail=match.source))
    return issues


def _roster_missing(match: Match, unread: Sequence[str]) -> Issue:
    """One finding for a card whose roster block was not read, on one side or both.

    Once per card, not once per side, and it says what else went with the squad:
    a card cut short in its roster block, or printing none, used to raise a
    ``roster-missing`` for each side and a ``ratings-missing`` besides, three
    findings for one missing block. The card's own note on the match says why the
    block was not read.
    """
    if len(unread) == 1:
        side, = unread
        said = (f"no {side} roster was read from the card, so fill-ins on that side "
                f"cannot be detected and nobody who played {side} has a rating for "
                "this round.")
    else:
        said = ("neither roster was read from the card, so fill-ins cannot be "
                "detected on either side and nobody on it has a rating for this "
                "round.")
    return Issue(rule="roster-missing", severity=WARNING,
                 message=f"{_label(match)}: {said}",
                 subject=_label(match), detail=match.source)


def _check_player_teams(ctx: _Context) -> list[Issue]:
    """One player cannot appear for two teams in the same round.

    An error, not a fill-in note: whichever reading is right, one team's set is
    credited to someone who was on another court at the time, and the player's own
    record double-counts the round.

    The message offers both readings and asserts neither. It used to end "one of
    those cards names the wrong player", which is false whenever the answer is two
    people sharing a printed name -- then both cards are right and it is this
    program's own idea of identity that is wrong. A club told only the first reading
    goes looking for a mistake on a correct card.
    """
    issues: list[Issue] = []
    seen: dict[tuple[str, str], set[str]] = defaultdict(set)
    for match in ctx.playable:
        for side in ("home", "away"):
            for result in match.sets:
                for slug in result.players_for(side):
                    seen[(match.round, slug)].add(match.team_for(side))
    draw = ctx.competition.rounds
    for (number, slug), sides in sorted(
            seen.items(), key=lambda item: fixture_sort_key(item[0], draw)):
        if len(sides) > 1:
            issues.append(Issue(
                rule="player-two-teams", severity=ERROR,
                message=f"{slug} plays for {in_words(sorted(sides))} in round "
                        f"{number}; nobody is on two courts at once, so either one "
                        f"of those cards names the wrong player, or they are two "
                        f"different people who share a printed name — which is "
                        f"what [namesakes] in {ctx.config_name} is for.",
                subject=f"R{number} {slug}",
                detail=", ".join(sorted(sides))))
    return issues


def _check_namesakes(ctx: _Context) -> list[Issue]:
    """One printed name in two teams' roster blocks, which is two people in one row.

    The roster block is the registered squad — a fill-in appears in the set rows of
    the side that borrowed them and in nobody's roster block, which is how
    :func:`~matchcentre.stats.is_fill_in` tells the two apart. So a name printed on
    two teams' rosters is two registrations, and merging them produces a row that is
    wrong for both: their sets add up, their win rate is neither of theirs, and the
    team, the rating and the captain's mark on it belong to whichever roster the
    index wrote last.

    A warning, not an error, and it stops at saying so. Two rosters carrying one
    name is equally one person who changed teams mid-season, the cards cannot tell
    the two apart, and splitting a transfer would invent a person who never
    existed — the same reason two team banners that look alike are reported and
    never merged. The remedy is printed as the line to paste.

    This is also the only check that can see the case at all. Neither of them need
    ever reach a set row: two reserves who are rostered every week and never play
    leave every set-level check, every total and the ladder itself untouched.
    """
    printed: dict[str, str] = {}
    teams: dict[str, list[str]] = defaultdict(list)
    for team in sorted(ctx.competition.teams.values(), key=lambda t: t.slug):
        for player in team.players:
            teams[player.slug].append(team.slug)
            printed.setdefault(player.slug, player.name or unslug(player.slug))
    issues: list[Issue] = []
    for slug, holders in sorted(teams.items()):
        if len(holders) < 2:
            continue
        listed = in_words(holders)
        paste = ", ".join(f'"{team}"' for team in holders)
        # Counted here, once: two registrations are both and either, more are
        # all and any.
        two = len(holders) == 2
        every = "both" if two else f"all {len(holders)}"
        people = "two" if two else len(holders)
        issues.append(Issue(
            rule="player-two-rosters", severity=WARNING,
            message=f"{printed[slug]} is in the roster block of {listed}, so one "
                    f"player holds {every} registrations: whatever "
                    f"{'either' if two else 'any'} of them "
                    f"played counts towards the same record, and the team, rating "
                    f"and win rate on it belong to only one of them. Either they "
                    f"are {people} different people who share a printed name, or "
                    f"one person who changed teams mid-season and "
                    f"{'both halves' if two else f'{every} registrations'} do "
                    f"belong together. If they are {people} people, add "
                    f"{slug} = [{paste}] under [namesakes] in "
                    f"{ctx.config_name}.",
            subject=slug, detail=", ".join(holders)))
    return issues


def _check_ratings(ctx: _Context) -> list[Issue]:
    """A card with no ratings on it, said out loud rather than shown as blanks.

    A warning: ratings are display data and no result depends on them. But a whole
    card losing them usually means the roster block was not read, and that is worth
    knowing before a reader concludes the club stopped publishing them.

    A player is asked about only where their side's roster block prints them,
    since a rating is read from that block and from nowhere else. A name on no line
    of it has no rating on the card to read: :func:`_check_set_players` has
    already said who they are, as a fill-in or a stranger, and saying their
    rating is missing too was a second finding for the same fact, and wrong for a
    lent player whose own team's cards rate them. What is left is a line the block
    printed, for somebody who played, with no rating on it.
    """
    issues: list[Issue] = []
    for match in ctx.playable:
        # Only names on their side's roster block: ratings are printed there, and a
        # side with no block read is already a roster-missing that says its
        # ratings went with it, or a side that never came (_never_came), with
        # nobody on court to rate.
        on_court = {slug for side in ("home", "away")
                    for slug in match.players_on(side)
                    if slug in match.roster_for(side)}
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
            # The block once for each side the names are on: two home players
            # are on one block, and a home and an away player on two.
            blocks = sum(1 for side in ("home", "away")
                         if set(absent) & set(match.roster_for(side)))
            gives = ("the roster block gives" if blocks == 1
                     else "the roster blocks give")
            if len(absent) == 1:
                said = (f"{gives} no rating for {absent[0]}, who played, so "
                        f"{absent[0]} shows")
            else:
                said = (f"{gives} no rating for {in_words(absent)}, who played, "
                        "so each shows")
            issues.append(Issue(
                rule="ratings-missing", severity=WARNING,
                message=f"{_label(match)}: {said} none for this round.",
                subject=_label(match), detail=match.source))
    return issues


def _check_rosters(ctx: _Context) -> list[Issue]:
    """The card's roster block against the squad the competition has registered.

    What a roster leaves out of the squad, and only that. The squad is made of the
    rosters of the cards that count, so a name a roster gives beyond it is on a
    card whose night another card disagrees with, which F9 reports as an error:
    ``roster-mismatch`` said so again, and was removed.
    """
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
            absent = sorted(p.slug for p in team.players if p.slug not in roster)
            if absent:
                issues.append(Issue(
                    rule="roster-incomplete", severity=WARNING,
                    message=f"{_label(match)}: the {side} roster omits registered "
                            f"player(s) {in_words(absent)} of {slug}.",
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
    for match in chronological(ctx.matches, ctx.competition.rounds):
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
            # Nothing to check a rest against here. No bye reaches this module from
            # a build, and the one function that makes a bye makes it from this
            # same draw's rests, so the draw would only be agreeing with itself.
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
            # A card dated off its night is shown on its round's date (discover's
            # _off_night_dates), which is the date compared; the date it printed
            # is quoted beside it, since a club checks the card by that.
            printed = next((card.date for card in ctx.cards
                            if _where(card) == match.source), match.date)
            dated, wins = ((f"is dated {match.date}", "The card wins.")
                           if printed == match.date else
                           (f"is dated {printed} and is shown on {match.date}, "
                            f"the date of the other cards of its round",
                            "The cards win."))
            issues.append(Issue(
                rule="date-mismatch", severity=WARNING,
                message=f"{_label(match)}: the card {dated}; the declared draw "
                        f"has round {match.round} on {scheduled.date}. {wins}",
                subject=_label(match), detail=scheduled.date))
    return issues


# --- a declared fixture with no card ----------------------------------


def _check_missing_cards(ctx: _Context) -> list[Issue]:
    """``card-missing``: the draw lists the match and no card names it.

    Measured gap this closes: the only thing that mentioned a missing card was a
    line :mod:`~matchcentre.discover` printed on the terminal once, at build time. A
    club building with ``-q`` never saw it and nobody saw it a week later, because
    ``season.json`` said nothing — so the dashboard said nothing, and the fixture's
    own row read "scheduled" for the rest of the season.

    A warning, not an error. ``Report.ok`` gates the build's exit status, and a club
    that exports on Wednesday the three cards it has for Tuesday's four fixtures
    would have the run fail over a file that is not wrong, only not in yet. Nothing
    here says a figure is wrong; it says a match the club's own draw lists is absent
    from every one of them.

    The finding shows its evidence instead of asserting the conclusion, because the
    two ways of reaching it read differently to a club — other cards in the same
    round, or a later round entirely — and one wording for both would be this module
    sounding equally sure of a night half exported and a round long gone. It claims
    nothing about whether the night was played: a forfeit, a washout and a card
    nobody exported are identical from here, and which one it was is written on the
    scorecard being asked for.

    A fixture a card filed under another competition fits (``ctx.strays``) is told
    where that card went and why, in place of the advice to export it or diagnose
    it, which is false of a card that was read (4.444); its path is left out,
    because this reaches ``season.json``, which lists only its own cards by path.
    """
    issues: list[Issue] = []
    comp = ctx.competition
    # A fixture of a team its own round also rests, with no card to say which, is
    # `bye-and-fixture`'s, which asks for this card as one half of the fix. Beside a
    # card the finding is `bye-contradicted`, which asks for nothing, so it stays.
    contradicted = {(number, pair) for number, _, card, listed in _rests_refused(ctx)
                    if card is None for pair in listed}
    for gap in fixtures_without_cards(ctx.matches, comp):
        if (gap.round, (gap.home, gap.away)) in contradicted:
            continue
        if gap.cards:
            because = (f"{gap.cards} of the {gap.fixtures} fixtures the draw lists "
                       f"for that round do have one")
        elif gap.unlisted:
            because = unlisted_words(gap.unlisted)
        else:
            because = (f"nothing at all was read for that round while round "
                       f"{gap.later} has cards, so the season is past it")
        fixture = f"{comp.team_name(gap.home)} v {comp.team_name(gap.away)}"
        filed = [stray for stray in ctx.strays
                 if stray.fits(gap.round, (gap.home, gap.away))]
        if filed:
            message = (f"round {gap.round}: no scorecard of {fixture}, which the "
                       f"declared draw lists, was filed under this competition, and "
                       f"{because}. "
                       + " ".join(stray.told(comp.team_name, path=False)
                                  for stray in filed)
                       + f" {FiledElsewhere.uncounted(filed)}.")
        else:
            message = (f"round {gap.round}: no scorecard was read for {fixture}, "
                       f"which the declared draw lists, and {because}. Export it "
                       f"from Match Centre into this round's folder — or if the "
                       f"file is already there, run tools/doctor.py on it to see "
                       f"why it was not read.")
        issues.append(Issue(
            rule="card-missing", severity=WARNING,
            message=message,
            subject=f"R{gap.round} {gap.home} v {gap.away}",
            detail=f"round {gap.round}: {gap.cards} of {gap.fixtures} declared "
                   f"fixtures have a card"
                   + (f"; cards exist for round {gap.later}" if gap.later else "")
                   + "".join(f"; a card of it is filed under {stray.filed_under}"
                             for stray in filed)))
    return issues


# --- byes -------------------------------------------------------------


def _rests_refused(ctx: _Context) -> list[tuple[str, str, Match | None,
                                               tuple[tuple[str, str], ...]]]:
    """Every rest the draw states that does not stand, and what refused it.

    ``(round, team, card, fixtures)``: the first card that has the team playing
    that round, or None where only the round's own fixtures do, and those fixtures.
    Read by :func:`_check_byes`, which says why each rest was refused, and by
    :func:`_check_missing_cards`, which leaves the fixtures of a round that
    contradicts itself to it, so one line of ``config.toml`` is one finding.
    """
    out = []
    comp = ctx.competition
    for number in comp.round_order:
        rnd = comp.rounds[number]
        standing = rests_standing(rnd, ctx.matches)
        for team in rnd.byes:
            if not team or team in standing:
                continue
            out.append((number, team, card_against_rest(ctx.matches, number, team),
                        tuple(pair for pair in rnd.fixtures if team in pair)))
    return out


def _check_byes(ctx: _Context) -> list[Issue]:
    """Why a rest the draw states did not stand, said once.

    :func:`~matchcentre.stats.rests_standing` is the rule, and the ladder, the page
    and the review block all read it. This names each rest it refused, whichever of
    them would otherwise have shown it.

    ``bye-contradicted``: a card has the resting team playing that round. A
    warning, because the team played and no bye is credited, and the likeliest
    story is a rescheduled night the byes table never caught up with. What it says
    about the card depends on whether the ladder counts one: F9 refuses two cards
    that disagree, a level card is not counted in a grade that cannot draw, and a
    finals card is in no ladder. Saying "the card wins" beside a card the ladder
    set aside would claim a result nobody is credited with.

    ``bye-and-fixture``: a round rests a team and has it in a fixture, and no card
    says which is true. Both lines are the club's own -- a fixture read off a card
    has its card -- so this is a draw that says two things. An error once the round
    has a card, which is the window a bye is paid in: the rest is refused and
    nothing read here can say whether that was right, so if the rest is the true
    line the team is short the bye it was owed. A warning before then. No figure
    depends on it yet, and a club that typed the whole draw in before the season
    should not have every build fail over a line eighteen weeks off. Where the
    season has gone past the round the fixture's card is also missing, and that is
    said here too, as the other half of the one fix, rather than again as
    ``card-missing``.

    Neither is about a bye match, because none arrives here (:func:`validate`).
    """
    issues: list[Issue] = []
    comp = ctx.competition
    played = set(rounds_present(ctx.matches))
    counted = ladder_matches(ctx.matches, comp)
    asked = {(gap.round, gap.home, gap.away)
             for gap in fixtures_without_cards(ctx.matches, comp)}
    for number, team, card, listed in _rests_refused(ctx):
        name = comp.team_name(team)
        if card is not None:
            if card_against_rest(counted, number, team) is not None:
                then = "The card wins and no bye was credited."
            else:
                then = ("No bye was credited, because the team played, and the "
                        "ladder does not count the card either, so that round is "
                        "in none of their ladder figures.")
            issues.append(Issue(
                rule="bye-contradicted", severity=WARNING,
                message=f"round {number}: the draw rests {name}, but there is a "
                        f"card for it that round. {then}",
                subject=f"R{number} {team}",
                detail=f"card {card.source or 'unknown source'}"))
            continue
        against = in_words(comp.team_name(away if home == team else home)
                           for home, away in listed)
        if number in played:
            severity = ERROR
            then = ("No bye was credited, so if the rest is the true line the team "
                    "is short the bye it was owed.")
        else:
            severity = WARNING
            then = ("Nothing has been read for that round yet, so no figure "
                    "depends on it, and the dashboard shows the fixture and not "
                    "the rest.")
        if any((number, home, away) in asked for home, away in listed):
            fix = (f"If they played, export that card from Match Centre into this "
                   f"round's folder and take the rest out of {ctx.config_name}. If "
                   f"they rested, take the fixture out.")
        else:
            fix = f"Take whichever line is wrong out of {ctx.config_name}."
        issues.append(Issue(
            rule="bye-and-fixture", severity=severity,
            message=f"round {number}: the draw rests {name} and also has it "
                    f"playing {against}, and no card says which is true. {then} "
                    f"{fix}",
            subject=f"R{number} {team}",
            detail="declared: " + ", ".join(f"{home} v {away}"
                                            for home, away in listed)))
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
            message=f"rounds_total = {total} is reported with "
                    f"{comp.provenance_of('rounds_total').value} provenance. Unless "
                    "a human declared the season length it must be null, or the "
                    "dashboard will show a completion percentage it invented.",
            detail=comp.provenance_of("rounds_total").value))
    regular = comp.rounds_past_length(ctx.matches)
    unlisted = comp.draw_outgrown_by
    if regular and unlisted:
        # Nobody typed this length: the draw gave it, by the rounds it lists. Said
        # here, once, with the rounds it leaves out, which is why discovery's note
        # about those rounds is not printed as well. "Rounds have", never one: a
        # draw gives a length only when it lists a regular round, and more than
        # that have been played. A typed length, below, can be 0.
        issues.append(Issue(
            rule="F3", severity=ERROR,
            message=f"the declared draw lists {total} regular "
                    f"round{'' if total == 1 else 's'}, which with no rounds_total "
                    f"is the season's length, but {len(regular)} regular rounds "
                    f"have been played ({in_words(regular)}). Cards were found for "
                    f"{rounds_listed(unlisted)}, which the draw does not list: add "
                    f"them under [[competitions.{comp.id}.rounds]] if the draw grew, "
                    f"or set rounds_total under [competitions.{comp.id}] if the draw "
                    f"is not the whole season.",
            detail=f"rounds played: {', '.join(regular)}"))
    elif regular:
        issues.append(Issue(
            rule="F3", severity=ERROR,
            message=f"rounds_total is declared as {total} but {len(regular)} "
                    f"regular {by_weeks(regular, 'round has', 'rounds have')} been "
                    f"played ({in_words(regular)}).",
            detail=f"rounds played: {', '.join(regular)}"))
    return issues


# --- F11: no finals in the ladder ---------------------------------------


def _check_finals(ctx: _Context) -> list[Issue]:
    """Rule F11. A finals round must not contribute ladder points.

    :func:`~matchcentre.stats.regular_season` is where that happens, and the checks
    below are about the split it makes rather than the arithmetic after it: a finals
    result is *reported* here, because a card that read cleanly and then moved no
    number at all is exactly the kind of silence a club would otherwise have to
    notice for itself. What stays an error is a round put on the wrong side of the
    split, which loses a real round out of the ladder.
    """
    issues: list[Issue] = []
    comp = ctx.competition
    scored: dict[str, list[Match]] = defaultdict(list)
    for match in chronological(ctx.matches, ctx.competition.rounds):
        if match.decided:
            scored[match.round].append(match)
    for number in sorted({m.round for m in ctx.matches}, key=comp.round_key):
        rnd = comp.rounds.get(number)
        numeric = round_number(number) is not None
        finals = comp.is_finals_round(number)
        # What the club typed after `finals =`, asked of the round as discovery's
        # finals note asks it: a value typed is not offered again, or doubted.
        typed = rnd.finals_typed if rnd is not None else None
        if finals and scored[number]:
            count = len(scored[number])
            issues.append(Issue(
                rule="F11", severity=WARNING,
                message=f"round {echoed(number)} is a finals round, so its {count} "
                        f"result{'' if count == 1 else 's'} "
                        f"{'is' if count == 1 else 'are'} left out of the ladder: no "
                        "points, and no games or sets in any team's row. The match "
                        "is still on the Matches page and still counts towards the "
                        "records of the players in it."
                        + (comp.finals_advice("finals = false",
                                              "if this round is really a regular one")
                           if typed is None else ""),
                subject=f"R{number}",
                detail=", ".join(_label(m) for m in scored[number])))
        if (rnd is not None and rnd.is_finals and numeric
                and comp.draw_source != DRAW_DECLARED):
            issues.append(Issue(
                rule="F11", severity=ERROR,
                message=f"round {echoed(number)} is marked finals, but its label "
                        "is a number and no declared draw says so. A round is finals "
                        "only when it is declared or its own label names a final, "
                        "so this would drop a regular round out of the ladder.",
                subject=f"R{number}", detail=comp.draw_source))
        if (not finals and not numeric and not is_finals_label(number)
                and typed is None):
            issues.append(Issue(
                rule="F11", severity=WARNING,
                message=f"round label {echoed(number, quoted=True)} is neither a "
                        "number nor a finals label this program recognises; it is "
                        "being scored as a regular round.",
                subject=f"R{number}", detail=number))
    return issues


# --- F10: does the ladder state a settled order? ------------------------


def _check_ladder(ctx: _Context) -> list[Issue]:
    """Rule F10. Whether the ladder is an order, a snapshot or none, and who is short.

    A warning, always: the points are right, and what is at stake is the claim the
    dashboard makes about them. Mid-round, on a ladder ordered on a total, the
    team on top is the team whose card arrived, and the label has to say so. On
    one ordered on the average (:func:`stats.ordered_on_the_average`) the order
    stands, and the finding says so, still naming the teams short, since one of
    them may be a card not yet in (4.523), and naming apart a team short and
    level on the average with a team that has counted more, which the next key
    places below it (:func:`stats.ladder_levels`). The state is
    :func:`stats.ladder_state_of`'s, the one rule the document's
    ``meta.ladder_state`` is written by too (4.428).
    """
    issues: list[Issue] = []
    comp = ctx.competition
    if not comp.teams:
        return issues       # already reported by _check_inputs
    try:
        # The set the ladder is built from, and by the same call it uses: finals
        # excluded (F11), or the two teams in a semi-final read as a round ahead of
        # everybody else, and one card per fixture (F9), or a re-exported card does
        # the same thing.
        counted = ladder_counts(ctx.matches, comp)
        # What P shows of those, for the words the state is said in (4.458).
        played = ladder_played(ctx.matches, comp)
    except ScoringError as exc:
        # A defence, not a path. A level match under a ruleset with no value for a
        # draw is what refuses, and no folder's gets here: ladder_matches drops it
        # before it is priced and Match.points_for fails closed on it besides, so
        # it is draw-not-allowed's finding alone. Kept for a ledger that gains a
        # second reason to refuse, and reported rather than raised: the rest of
        # the report is still worth reading.
        return [Issue(
            rule="F10", severity=ERROR,
            message=f"the ladder cannot be computed — {exc}",
            detail=str(exc))]
    state = ladder_state_of(counted)
    # The report's line and both pages say the state in the same words.
    detail = ladder_detail(counted, played)
    if state == "empty":
        # "Regular-season": a final alone was played, and is out of the table (F11).
        issues.append(Issue(
            rule="F10", severity=WARNING,
            message=f"{detail}, so there is no ladder to present.",
            detail="0 counted matches"))
        return issues
    if state == "in-progress":
        most = max(counted.values())
        short = sorted(slug for slug, n in counted.items() if n < most)
        fewer = (f"{in_words(short)} {'has' if len(short) == 1 else 'have'} "
                 f"fewer than the {most} counted by the rest")
        if ordered_on_the_average(comp.rules):
            costs = _what_fewer_costs(short, counted,
                                      ladder_levels(ctx.matches, comp),
                                      comp.rules.ladder_by[1])
            message = (f"the ladder is in progress — {detail}. {fewer}; the "
                       f"ladder is ordered on the average{costs}.")
        else:
            message = (f"the ladder is in progress — {detail}, so the order "
                       f"is not settled. {fewer}.")
        issues.append(Issue(
            rule="F10", severity=WARNING, message=message,
            detail="; ".join(f"{slug} {counted[slug]}" for slug in sorted(counted))))
    return issues


def _what_fewer_costs(short: list[str], counted: dict[str, int],
                      levels: list[list[str]], next_key: str) -> str:
    """What being short costs *short* on a ladder ordered on the average.

    Nothing, to a team with a match counted: its average is its own (4.523).
    Unless it is level on that average with a team that has counted more and
    is placed above it, one of *levels* (:func:`stats.ladder_levels`): the
    tie goes to *next_key*, under ``games-bonus`` points, a total, so having
    counted fewer places it below, by one match or more. A team with none has
    no average, and is placed below every team with one until it has (4.428),
    so the clause says that of it rather than telling it the fewer matches
    cost it nothing. The kinds are named apart where more than one is short;
    where the only kind is the teams with a match counted, by a pronoun, the
    sentence before having named them. Returned with its own opening
    punctuation, to follow "the ladder is ordered on the average".
    """
    tied = sorted(slug for run in levels for at, slug in enumerate(run)
                  if slug in short
                  and any(counted[above] > counted[slug]
                          for above in run[:at]))
    behind = [slug for slug in short if counted[slug] and slug not in tied]
    unplaced = [slug for slug in short if not counted[slug]]
    if behind and not tied and not unplaced:
        return (f", so that costs {'it' if len(behind) == 1 else 'them'} "
                f"no place")
    clauses = []
    if behind:
        clauses.append(f", so that costs {in_words(behind)} no place")
    if tied:
        one = len(tied) == 1
        clauses.append(
            f"{'; but ' if clauses else ', but '}{in_words(tied)} "
            f"{'is' if one else 'are'} level on it with a team that has "
            f"counted more, and the tie goes to {next_key.replace('_', ' ')}, "
            f"so having counted fewer places {'it' if one else 'them'} "
            f"below")
    if unplaced:
        one = len(unplaced) == 1
        clauses.append(
            f"{'; and ' if tied else ', and '}{in_words(unplaced)} "
            f"{'has' if one else 'have'} no match counted yet, so "
            f"{'is' if one else 'are'} placed last")
    return "".join(clauses)


# --- the association's sheet -------------------------------------------------
#
# Warnings, every one: the figures are the cards', and a sheet that disagrees is a
# question for a human about which of the two is right. An error would fail a
# nightly build over the association's arithmetic.

_WHY_UNPLAYED = {
    UNPLAYED_AVERAGE: "the association credits a week off with the team's own "
                      "average",
    UNPLAYED_ZERO: "the association scores a week off as nothing",
}


def _check_sheet_teams(ctx: _Context) -> list[Issue]:
    """``sheet-team``: a sheet line that names no team, or one named already."""
    sheet = ctx.sheet
    if sheet is None:
        return []
    issues = []
    for key, near in sheet.unmatched:
        issues.append(Issue(
            rule="sheet-team", severity=WARNING, subject=key,
            message=f'"{key}" in the sheet table names no team on this '
                    f"competition's cards, so that line was not compared."
                    f"{did_you_mean(near)}"))
    for key, name, first in sheet.repeated:
        issues.append(Issue(
            rule="sheet-team", severity=WARNING, subject=key,
            message=f'"{key}" in the sheet table is {name} again, which "{first}" '
                    f"already gave figures for, so only the first line was "
                    f"compared."))
    return issues


def _check_sheet_reached(ctx: _Context) -> list[Issue]:
    """``sheet-ahead``: one finding for a sheet no card has caught up with."""
    sheet = ctx.sheet
    if sheet is None or sheet.status != AHEAD:
        return []
    number = sheet.after_round
    return [Issue(
        rule="sheet-ahead", severity=WARNING,
        message=f"the sheet table runs to round {number}, and no card for round "
                f"{number} or a later round has been read, so nothing was "
                f"compared. Export round {number}'s cards, or set after_round to "
                f"the round the typed figures are from.",
        detail=f"latest round read: {sheet.latest_read}")]


def _check_sheet(ctx: _Context) -> list[Issue]:
    """``sheet-mismatch``: the teams the association's sheet disagrees with.

    One mistake is reported once. Where the other ``unplayed`` convention puts at
    least one team's points right and none wrong, that setting is the one finding,
    counting every team that disagrees, and only the teams it does not explain get
    their own. Where every team compared is out and no setting explains it, the
    table or a rule is the likelier mistake than every card at once, and that is
    one finding too.
    """
    sheet = ctx.sheet
    if sheet is None or sheet.status != COMPARED:
        return []
    name = ctx.competition.team_name
    number = sheet.after_round
    if sheet.convention_fits:
        alt = sheet.alt_unplayed
        fixed = [c for c in sheet.checks if c.slug in sheet.fixed]
        out = len(sheet.disagreeing)
        count = (f"1 team disagrees with the association's sheet, and its points "
                 f"match" if out == 1 else
                 f"{out} teams disagree with the association's sheet, and the "
                 f"points of {'every one' if len(fixed) == out else len(fixed)} "
                 f"of them match")
        issues = [Issue(
            rule="sheet-mismatch", severity=WARNING,
            message=f'after round {number}, {count} it under unplayed = "{alt}": '
                    f'{_WHY_UNPLAYED[alt]}. Set unplayed = "{alt}" in this '
                    f"competition's section or under [rules].",
            detail="; ".join(f"{name(c.slug)} {_printed(c.points_now, c)} "
                             f"(sheet {c.typed_points})" for c in fixed))]
        rest = [c for c in sheet.disagreeing if c.slug not in sheet.fixed]
    elif sheet.every_team_out:
        return [Issue(
            rule="sheet-mismatch", severity=WARNING,
            message=f"after round {number} every one of the {len(sheet.checks)} "
                    f"teams compared disagrees with the association's sheet, which "
                    f"points at the table or a rule rather than at a card. Check "
                    f"first that after_round is the round the sheet runs to, then "
                    f"the scoring and sets rules this dashboard is using.",
            detail="; ".join(_side_by_side(c, name(c.slug)) for c in sheet.checks))]
    else:
        issues = []
        rest = list(sheet.disagreeing)
    issues.extend(Issue(rule="sheet-mismatch", severity=WARNING,
                        message=_team_message(sheet, c, name), subject=c.slug,
                        detail="; ".join(c.evidence))
                  for c in rest)
    return issues


def _printed(value: float, check: TeamCheck) -> str:
    """*value* at the places the team's points were typed to."""
    return at_places(value, places_of(check.typed_points))


def _side_by_side(check: TeamCheck, name: str) -> str:
    """``Hilltop Aces 10.7, 3 played (sheet 7.5, 2 played)``, typed figures only.

    A played that agrees is shown as the figure it agrees with, so a sheet of ``P``
    under ``"average"`` is not set against a divisor it never claimed.
    """
    ours, theirs = [], []
    if check.typed_points:
        ours.append(_printed(check.points_now, check))
        theirs.append(check.typed_points)
    if check.typed_played is not None:
        agreed = check.counted_now if check.played_differs() else check.typed_played
        ours.append(f"{agreed} played")
        theirs.append(f"{check.typed_played} played")
    return f"{name} {', '.join(ours)} (sheet {', '.join(theirs)})"


def _team_message(sheet: Comparison, check: TeamCheck, name) -> str:
    """Both figures, the gap, and the one next step that is right for this team."""
    number = sheet.after_round
    lead = f"after round {number} the cards give {name(check.slug)} "
    points = ""
    if check.points_differ_now():
        places = places_of(check.typed_points)
        ours = _printed(check.points_now, check)
        gap = abs(Decimal(check.typed_points) - Decimal(ours))
        side = "under" if Decimal(ours) < Decimal(check.typed_points) else "over"
        points = (f"{ours} points where the association's sheet has "
                  f"{check.typed_points}, {gap:.{places}f} {side} it")
    played = ""
    if check.played_differs():
        where = "it" if points else "the association's sheet"
        ours_n, theirs_n = check.counted_now, check.typed_played
        apart = (f"{abs(ours_n - theirs_n)} "
                 f"{'fewer' if ours_n < theirs_n else 'more'}")
        if check.counted_now != check.decided:
            played = (f"{ours_n} matches counted ({check.decided} decided, "
                      f"{ours_n - check.decided} credited its average) where "
                      f"{where} has {theirs_n} played, {apart}")
        else:
            played = (f"{ours_n} {'match' if ours_n == 1 else 'matches'} played "
                      f"where {where} has {theirs_n}, {apart}")
    said = lead + (f"{points}, and {played}" if points and played
                   else points or played) + "."
    return said + _next_step(sheet, check, name)


def _next_step(sheet: Comparison, check: TeamCheck, name) -> str:
    """The first of five sentences that applies, and is true under the setting.

    Every word in them that agrees with the rounds named is chosen by
    :func:`~matchcentre.sheet.by_weeks`, the count
    :func:`~matchcentre.sheet.rounds_listed` names the rounds by.
    """
    if check.nothing_read:
        where = rounds_listed(check.nothing_read)
        played = by_weeks(check.nothing_read, "that week, that card has",
                          "those weeks, those cards have")
        if sheet.current_unplayed == UNPLAYED_AVERAGE:
            return (f" Nothing was read for it in {where}. If it played {played} "
                    f"not been exported. If it rested, write the "
                    f"rest in this competition's byes table: under unplayed = "
                    f'"average" that is what credits it its average.')
        return (f" Nothing was read for it in {where}. If it played {played} not "
                f"been exported. If it rested, a rest scores nothing "
                f'under unplayed = "zero", so the rest is not the difference unless '
                f'the association credits one: that is unplayed = "average", with '
                f"the rest written in this competition's byes table.")
    if check.slug in sheet.fixed and check.weeks_off:
        return (f' With unplayed = "{sheet.alt_unplayed}" the cards give it '
                f"{_printed(check.points_alt, check)}, the sheet's figure, but that "
                f"setting would put {in_words([name(s) for s in sheet.broken])} out, "
                f"so check {rounds_listed(check.weeks_off)} against the sheet's "
                f"byes.")
    if check.set_aside:
        why = by_weeks(check.set_aside,
                       "the finding about that round says why: deal with it",
                       "the findings about those rounds say why: deal with them")
        return (f" Nothing from {rounds_listed(check.set_aside)} was counted for "
                f"it, and {why} first, then compare its matches against the "
                f"sheet's.")
    if check.weeks_off:
        weeks = by_weeks(check.weeks_off, "that week", "those weeks")
        return (f" It had {rounds_listed(check.weeks_off)} off: check {weeks} "
                f"against the sheet's byes first, then its matches.")
    return (" It had no week off, so the difference is in one of its matches: "
            "compare them against the sheet's.")

