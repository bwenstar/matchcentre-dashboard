"""What shape a match is, and when a set is over.

The predecessor of this project hardcoded one club's format: exactly four sets in
the order doubles, singles, singles, doubles, each first to six games. Both facts
are club-specific, and both were load-bearing in ways that produced *wrong
numbers* rather than errors when they did not hold. Measured, on the old code:

* a FAST4 set won ``4-2`` failed the ``>= 6`` completion test, so every match read
  as unplayed and the ladder came out blank;
* an eight-game pro set standing at ``6-3`` passed it, so a mid-set score was
  scored as the final one.

So the format is data. Better still, most of it is data the scorecard itself
carries: the number of set rows is the number of row bands the card's own rules
describe, and each row states its own discipline in the marker column (``1&2``
for a doubles pairing, a bare ``1`` for singles). :func:`from_rows` turns that
observation into a :class:`MatchFormat`.

What no card can tell us is the *set* rule — ``6-3`` looks the same whether the
set was played to six or to eight — so that comes from configuration, with
``first-to-6`` the default because it is what the Match Centre template's usual
club competitions run.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["Slot", "SetRule", "MatchFormat", "SET_RULES", "FORMATS",
           "set_rule", "match_format", "from_rows", "completed", "set_winner",
           "FormatError"]


class FormatError(ValueError):
    """A format was requested that cannot be honoured, or two disagree."""


@dataclass(frozen=True, slots=True)
class Slot:
    """One set within a match.

    ``key`` is what reaches ``season.json`` and the dashboard's per-slot tables,
    so it has to stay stable across runs: it is assigned from the order the rows
    appear on the card, counting doubles and singles separately.
    """

    key: str
    label: str
    doubles: bool

    @property
    def discipline(self) -> str:
        return "doubles" if self.doubles else "singles"


@dataclass(frozen=True, slots=True)
class SetRule:
    """When a set is finished, and what a legal final score looks like.

    ``win_by`` is 1, not 2, for the common club convention where 5-5 is decided
    by a sudden-death tiebreak and the winner's score is exactly 6. ``max_games``
    caps the score: with a set tiebreak at 6-6 the cap is 7, and with advantage
    scoring there is no cap at all.
    """

    name: str
    games_to_win: int
    win_by: int
    max_games: int | None

    @property
    def max_games_per_set(self) -> int:
        """The most games one side can win in a set, for forfeit pricing."""
        return self.max_games if self.max_games is not None else self.games_to_win

    def describe(self) -> str:
        if self.max_games == self.games_to_win:
            return f"first to {self.games_to_win}"
        if self.max_games is None:
            return f"first to {self.games_to_win}, advantage set"
        return f"first to {self.games_to_win}, tiebreak at {self.max_games - 1}-all"


@dataclass(frozen=True, slots=True)
class MatchFormat:
    """The full shape of one match: its sets, and the rule each set follows."""

    name: str
    slots: tuple[Slot, ...]
    set_rule: SetRule
    min_sets_for_result: int

    def __post_init__(self) -> None:
        if not self.slots:
            raise FormatError("a match format needs at least one set")
        keys = [slot.key for slot in self.slots]
        if len(set(keys)) != len(keys):
            raise FormatError(f"duplicate slot keys: {keys}")
        if not 1 <= self.min_sets_for_result <= len(self.slots):
            raise FormatError(
                f"min_sets_for_result={self.min_sets_for_result} is outside "
                f"1..{len(self.slots)}")

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(slot.key for slot in self.slots)

    @property
    def pattern(self) -> str:
        """``"d1-s1-s2-d2"`` — the shape, ignoring the set rule."""
        return "-".join(self.keys)

    def slot(self, key: str) -> Slot:
        for slot in self.slots:
            if slot.key == key:
                return slot
        raise FormatError(f"no slot {key!r} in format {self.name}")

    @property
    def max_games_per_match(self) -> int:
        """Every game one side could possibly win. Prices a forfeit."""
        return len(self.slots) * self.set_rule.max_games_per_set

    def with_set_rule(self, rule: SetRule) -> MatchFormat:
        return MatchFormat(name=self.name, slots=self.slots, set_rule=rule,
                           min_sets_for_result=self.min_sets_for_result)

    def describe(self) -> str:
        shape = ", ".join(slot.label for slot in self.slots)
        return f"{shape} ({self.set_rule.describe()})"


# --- named rules and formats ------------------------------------------------
# Names are the config's vocabulary. Adding one here is the whole cost of
# supporting another competition's conventions.

SET_RULES: dict[str, SetRule] = {
    # 5-5 decided by a sudden-death tiebreak, so a winning score is exactly 6.
    "first-to-6": SetRule("first-to-6", games_to_win=6, win_by=1, max_games=6),
    # Must win by two, no tiebreak: 7-5, 8-6, 24-22.
    "advantage-6": SetRule("advantage-6", games_to_win=6, win_by=2, max_games=None),
    # 6-6 goes to a tiebreak recorded as 7-6.
    "six-game-tiebreak": SetRule("six-game-tiebreak", games_to_win=6, win_by=2,
                                 max_games=7),
    "fast4": SetRule("fast4", games_to_win=4, win_by=1, max_games=4),
    "short-sets-4": SetRule("short-sets-4", games_to_win=4, win_by=2, max_games=None),
    "pro-8": SetRule("pro-8", games_to_win=8, win_by=1, max_games=8),
    "pro-8-tiebreak": SetRule("pro-8-tiebreak", games_to_win=8, win_by=2, max_games=9),
    "pro-10": SetRule("pro-10", games_to_win=10, win_by=1, max_games=10),
}

DEFAULT_SET_RULE = "first-to-6"


def _slots(spec: str) -> tuple[Slot, ...]:
    """``"dssd"`` -> the four slots, numbered per discipline in row order."""
    counts = {"d": 0, "s": 0}
    out = []
    for letter in spec:
        if letter not in counts:
            raise FormatError(f"slot spec {spec!r} may only contain 'd' and 's'")
        counts[letter] += 1
        doubles = letter == "d"
        word = "Doubles" if doubles else "Singles"
        out.append(Slot(key=f"{letter}{counts[letter]}",
                        label=f"{word} {counts[letter]}", doubles=doubles))
    return tuple(out)


def _default_minimum(count: int) -> int:
    """Three of four sets, five of six: enough of the match to call a result."""
    return max(1, -(-count * 3 // 4))


_FORMAT_SPECS: dict[str, str] = {
    "doubles-singles-doubles": "dssd",
    "singles-doubles": "ssdd",
    "singles-3": "sss",
    "singles-4": "ssss",
    "singles-6": "ssssss",
    "doubles-2": "dd",
    "doubles-3": "ddd",
    "doubles-4": "dddd",
    "mixed-3": "dsd",
    "mixed-5": "ddsss",
}

FORMATS: dict[str, MatchFormat] = {
    name: MatchFormat(name=name, slots=_slots(spec),
                      set_rule=SET_RULES[DEFAULT_SET_RULE],
                      min_sets_for_result=_default_minimum(len(spec)))
    for name, spec in _FORMAT_SPECS.items()
}

DEFAULT_FORMAT = "doubles-singles-doubles"


def set_rule(name: str) -> SetRule:
    try:
        return SET_RULES[name]
    except KeyError:
        raise FormatError(
            f"unknown set rule {name!r}; known: {', '.join(sorted(SET_RULES))}"
        ) from None


def match_format(name: str, *, rule: SetRule | None = None,
                 min_sets_for_result: int | None = None) -> MatchFormat:
    try:
        base = FORMATS[name]
    except KeyError:
        raise FormatError(
            f"unknown match format {name!r}; known: {', '.join(sorted(FORMATS))}"
        ) from None
    return MatchFormat(
        name=base.name,
        slots=base.slots,
        set_rule=rule or base.set_rule,
        min_sets_for_result=min_sets_for_result or base.min_sets_for_result,
    )


def from_slot_spec(spec: object, *, name: str | None = None,
                   rule: SetRule | None = None,
                   min_sets_for_result: int | None = None) -> MatchFormat:
    """A format from an explicit config list like ``["doubles", "singles", ...]``."""
    letters = []
    for entry in spec:
        word = str(entry).strip().lower()
        if word in ("d", "doubles"):
            letters.append("d")
        elif word in ("s", "singles"):
            letters.append("s")
        else:
            raise FormatError(
                f"slot {entry!r} is neither 'singles' nor 'doubles'")
    slots = _slots("".join(letters))
    return MatchFormat(
        name=name or "-".join(slot.key for slot in slots),
        slots=slots,
        set_rule=rule or SET_RULES[DEFAULT_SET_RULE],
        min_sets_for_result=min_sets_for_result or _default_minimum(len(slots)),
    )


def from_rows(doubles_flags: object, *, rule: SetRule | None = None,
              min_sets_for_result: int | None = None) -> MatchFormat:
    """The format a single scorecard describes.

    ``doubles_flags`` is one boolean per set row, in the order the rows appear —
    exactly what the parser reads from each row's own marker column. A named
    preset is returned when the pattern matches one, so ``season.json`` says
    ``doubles-singles-doubles`` rather than ``d1-s1-s2-d2`` for the common case.
    """
    flags = list(doubles_flags)
    if not flags:
        raise FormatError("no set rows: cannot tell what format this card is")
    spec = "".join("d" if flag else "s" for flag in flags)
    slots = _slots(spec)
    name = next((known for known, known_spec in _FORMAT_SPECS.items()
                 if known_spec == spec), "-".join(s.key for s in slots))
    return MatchFormat(
        name=name,
        slots=slots,
        set_rule=rule or SET_RULES[DEFAULT_SET_RULE],
        min_sets_for_result=min_sets_for_result or _default_minimum(len(spec)),
    )


# --- set arithmetic ---------------------------------------------------------


def completed(home: int, away: int, rule: SetRule) -> bool:
    """Whether a set with this score is finished under ``rule``.

    The only test in the codebase allowed to decide this. A bare ``>= 6``
    anywhere else is the bug described in this module's docstring.
    """
    if home == away:
        # Nobody has won a level set, whatever the score. Without this a 6-6 read
        # under a first-to-6 rule satisfied the "reached the cap" test below and
        # was scored as a finished set with no winner.
        return False
    high, low = max(home, away), min(home, away)
    if high < rule.games_to_win:
        return False
    if rule.max_games is not None and high > rule.max_games:
        return False        # impossible under this rule; caller should warn
    if high - low >= rule.win_by:
        return True
    # A capped rule finishes at the cap even without the margin: 7-6, 4-3.
    return rule.max_games is not None and high == rule.max_games


def plausible(home: int, away: int, rule: SetRule) -> bool:
    """Whether the score could occur at all under ``rule``.

    Separate from :func:`completed` because an unfinished set is normal (a
    retirement, a washout) while an *impossible* one means the card was misread —
    which under the predecessor's fail-open column grid is precisely what a
    reversed or offset parse looked like.
    """
    if home < 0 or away < 0:
        return False
    cap = rule.max_games
    if cap is None:
        return True                       # an advantage set has no ceiling
    # The winner may reach the cap; the loser cannot, or nobody won. Under
    # ``first-to-6`` (cap 6) that rejects 6-6, which is unreachable because the
    # first side to six ends the set. Under ``six-game-tiebreak`` (cap 7) it
    # allows 6-6, which is a real in-progress score on the way to 7-6.
    return max(home, away) <= cap and min(home, away) <= cap - 1


def set_winner(home: int, away: int, rule: SetRule) -> str | None:
    """``"home"``, ``"away"``, or ``None`` when the set is level or unfinished."""
    if not completed(home, away, rule):
        return None
    if home == away:
        return None
    return "home" if home > away else "away"
