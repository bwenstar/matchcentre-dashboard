"""Names in, stable identifiers out.

A slug is this project's primary key, for teams and for players alike. That is a
deliberate change from keying teams on their draw number: draw numbers appear on
no scorecard, so deriving them means inventing them, and anything invented shifts
when a card is added, removed or read in a different order. A slug is a pure
function of the printed name, so it does not move.

Everything here is intentionally boring. The only subtle parts are
:func:`display_name`, which has to un-shout ``RIVERSIDE ROCKETS`` without
mangling the acronyms clubs genuinely use, and :func:`person_name`, which
un-shouts ``BO NAKAMA`` whole because a person's name holds no acronym.
"""

from __future__ import annotations

import re
import unicodedata

from .scope import in_words

__all__ = ["fold", "slugify", "label_key", "team_slug", "apply_alias", "display_name",
           "person_name", "near_misses", "nearest", "did_you_mean"]

_NOT_SLUG = re.compile(r"[^a-z0-9]+")

# Tokens left alone by display_name: a club abbreviation is short and already
# upper case, where a shouted word is longer. "TC" and "RTC" survive, "ROCKETS"
# becomes "Rockets".
#
# Three, not four, and the boundary is a real judgement rather than a fact. A
# four-letter all-caps token is ambiguous in a way no rule settles: "ACES" is a
# team and "WDTA" is an association, and they are the same shape. Cutting at
# three keeps the lengths where abbreviations dominate (two and three letters are
# rarely whole words) and title-cases the rest, so a club with a four-letter
# acronym sees "Wdta" and fixes it in one line of [team_names] -- which is what
# that section is for, and why every derived team name is written into it for
# review rather than presented as fact.
_ACRONYM_MAX = 3

# Words that stay lower case in the middle of a name, so "Kings Of The Court"
# reads as "Kings of the Court". "&" has no case, and is here as the "and" it is
# where a word before an A is asked.
_MINOR_WORDS = frozenset({"a", "an", "and", "at", "de", "for", "in", "of", "on",
                          "or", "the", "to", "van", "von", "&"})

# The articles, which never stand before one another: an ``A`` after one is a
# letter, THE A TEAM.
_ARTICLES = frozenset({"a", "an", "the"})

# The words a team's letter stands before: an ``A`` before one is a letter,
# HILLTOP A GRADE beside HILLTOP B GRADE, unless a minor word stands before it.
_LETTER_BEFORE = frozenset({"team", "teams", "grade", "reserve", "reserves",
                            "side", "squad"})

# A shouted word is un-shouted a part at a time, the parts between its hyphens
# and full stops: NORTH-WEST, T.J.
_PARTS = re.compile(r"([-.])")
_APOSTROPHES = "'\u2019"


def fold(text: str) -> str:
    """``"Émile ÅNA"`` -> ``"emile ana"``: *text* with its case and accents set aside.

    How two spellings compare. Each character taken apart by NFKD into the
    letter and the marks it is written with, compatibility forms included, so a
    full-width ``Ｗ`` is a ``W`` and a superscript ``ᴬ`` an ``A``; the marks
    dropped; and then :meth:`str.casefold` rather than ``lower``, the comparison
    Python documents for exactly this, so ``ẞ`` is an ``ss``. A letter with no
    mark to take off, ``Ø`` or ``Ł``, stays itself.

    The one fold: a slug, a career's initial, the careers list's order and
    :mod:`matchcentre.redact`'s matching of a name all make it. There were two,
    this one casefolding first and redact's last, and of every code point they
    folded 700 differently. Casefolding first leaves uncased the capital NFKD
    makes of ``ᴬ``, a mathematical ``𝐀`` or ``ℝ``, so 492 of them lost their
    letter from a slug -- ``ᴬda`` slugged to ``da`` -- and folding its own
    output changed it again on 636. Casefolding last loses none, and folds its
    own output to itself.
    """
    return "".join(c for c in unicodedata.normalize("NFKD", text)
                   if not unicodedata.combining(c)).casefold()


def slugify(text: str) -> str:
    """``"Ana Fernández"`` -> ``"ana-fernandez"``.

    Raises rather than returning ``""``: an empty identifier would collapse two
    different subjects into one key, and every caller here is holding text it
    read off a card and believes is a name.
    """
    slug = _NOT_SLUG.sub("-", fold(text.strip())).strip("-")
    if not slug:
        raise ValueError(f"{text!r} has no sluggable characters")
    return slug


def label_key(text: str) -> str:
    """One key for the several ways a club may write one label.

    ``"Div C"`` and ``"div c"`` are one division; ``"Tuesday"`` and ``"TUESDAY "`` one
    night. Unlike :func:`slugify` this never raises, because what it is given is a label
    typed into a config file or printed in a column rather than a name something has to
    be filed under -- and a label with no sluggable characters still has to compare
    equal to itself.

    It exists as a function so that the two places asking "are these the same
    competition?" -- the router that files a card, and the config load that refuses two
    sections claiming one competition -- cannot answer it differently. Two spellings
    that the router calls one competition and the load calls two would put every card
    in one section and leave the other's settings doing nothing, silently, which is the
    failure the refusal is there to prevent.
    """
    try:
        return slugify(text)
    except ValueError:
        return text.strip().casefold()


def team_slug(name: str, *, strip_prefix: str | None = None) -> str:
    """Team slug, optionally without the club prefix every team shares.

    A club whose teams are all printed ``RIVERSIDE ROCKETS``, ``RIVERSIDE ACES``
    gains nothing from carrying ``riverside-`` on every key. Only a leading whole
    word is removed, so ``strip_prefix="Riverside"`` leaves ``Riversiders``
    alone.
    """
    cleaned = name.strip()
    if strip_prefix:
        prefix = strip_prefix.strip()
        if prefix:
            pattern = re.compile(rf"^{re.escape(prefix)}\b[\s.-]*", re.IGNORECASE)
            candidate = pattern.sub("", cleaned).strip()
            # Never strip a team down to nothing: a team actually named
            # "Riverside" keeps its name.
            if candidate:
                cleaned = candidate
    return slugify(cleaned)


def apply_alias(slug: str, aliases: dict[str, str]) -> str:
    """Follow an alias chain to its end, tolerating a cycle.

    Clubs write ``aliases`` by hand, and a hand-written ``a -> b, b -> a`` pair is
    a typo, not a reason to hang. The chain is followed while it makes progress
    and abandoned once it repeats.
    """
    seen = {slug}
    current = slug
    while current in aliases:
        nxt = aliases[current]
        if nxt in seen:
            return current
        seen.add(nxt)
        current = nxt
    return current


def display_name(text: str) -> str:
    """A human-facing name from whatever the card printed.

    Cards shout team names (``RIVERSIDE ROCKETS``) and print player names
    properly (``Ana Fernandez``). This has to improve the first without touching
    the second, so it only rewrites tokens that are *entirely* upper case, and
    leaves short all-caps tokens alone because those are club abbreviations. A
    player's name the cards shout is :func:`person_name`'s, which un-shouts a
    word by the same :func:`_unshout`.

        >>> display_name("RIVERSIDE ROCKETS")
        'Riverside Rockets'
        >>> display_name("Ana Fernandez")
        'Ana Fernandez'
        >>> display_name("RTC")
        'RTC'
        >>> display_name("KINGS OF THE COURT")
        'Kings of the Court'
        >>> display_name("THE ACES")
        'The Aces'
    """
    words = text.split()
    out: list[str] = []
    for index, word in enumerate(words):
        shouted = _shouted(word)
        # A minor word is checked before the acronym rule, because "OF" and "THE"
        # are short enough to look like club abbreviations: without this,
        # "KINGS OF THE COURT" came out as "Kings OF THE Court". A minor word
        # leading the name still gets a capital, which is ordinary title case --
        # "THE ACES" is "The Aces", not "the Aces".
        if shouted and word.casefold() in _MINOR_WORDS:
            if word == "A" and index and _is_a_letter(words, index):
                out.append(word)          # HILLTOP A, a team's letter
                continue
            out.append(word.casefold() if index else word.capitalize())
            continue
        if shouted:
            if sum(c.isalpha() for c in word) <= _ACRONYM_MAX:
                out.append(word)          # RTC, WDTA — an abbreviation, keep it
                continue
            word = _unshout(word)         # ROCKETS -> Rockets
        out.append(word)
    return " ".join(out)


def person_name(text: str) -> str:
    """A player's human-facing name from whatever the card printed.

    A name printed in mixed case is read by :func:`display_name`, as it always
    was: its printer chose its capitals, and that is the one answer to what they
    mean, so TJ Nakama keeps its TJ and Bo NAKAMA is ``Bo Nakama``. A name printed
    wholly in capitals chose none, and each word is un-shouted by :func:`_unshout`
    and by nothing else of the team rule: in a person's name a shouted word of
    three letters or fewer is a given name or an initial far more often than an
    abbreviation (BO, KIT, ROS, JO A), and a minor word is an initial or a
    particle. What that costs is a TJ read as ``Tj``, a III as ``Iii``, a MCAHEARN
    as ``Mcahearn`` and a DE or VAN as ``De`` or ``Van``, which the capitals of a
    shouted card cannot settle and a ``[player_names]`` line spells.

        >>> person_name("BO NAKAMA")
        'Bo Nakama'
        >>> person_name("TUI-MAE O'DOWELL")
        "Tui-Mae O'Dowell"
        >>> person_name("JO A SMITH")
        'Jo A Smith'
        >>> person_name("TJ Nakama")
        'TJ Nakama'
    """
    if any(c.islower() for c in text):
        return display_name(text)
    return " ".join(_unshout(word) if _shouted(word) else word
                    for word in text.split())


def _shouted(word: str) -> bool:
    """Whether every letter of *word* is a capital, and it has one."""
    letters = [c for c in word if c.isalpha()]
    return bool(letters) and all(c.isupper() for c in letters)


def _unshout(word: str) -> str:
    """A shouted word with one capital to each part: ``(WOMENS)`` -> ``(Womens)``.

    The one step that un-shouts a word, for a team's name and a player's. Each part
    between hyphens and full stops is capitalised at its first letter or digit, so
    a bracket or a quote before it takes no capital and NORTH-WEST is North-West,
    and a part that begins with a digit keeps its letters lower, 2ND is 2nd. An
    apostrophe with one letter before it and two or more after it is a surname's
    prefix, O'Brien, D'Arcy's, and any other is a possessive, King's, Kings',
    whose letters stay lower: the word's own shape tells the two apart, with no
    list of prefixes. Rejected: ``str.capitalize``, which capitalises the first
    character and lowers every other, and gave ``(womens)``, ``O'brien`` and
    ``North-west``.
    """
    out = []
    for part in _PARTS.split(word):
        start = next((i for i, c in enumerate(part) if c.isalnum()), len(part))
        head, rest = part[:start], part[start:]
        mark = next((i for i, c in enumerate(rest) if c in _APOSTROPHES), -1)
        if (mark > 0 and sum(c.isalpha() for c in rest[:mark]) == 1
                and sum(c.isalpha() for c in rest[mark + 1:]) >= 2):
            out.append(head + rest[:mark + 1].capitalize()
                       + rest[mark + 1:].capitalize())
        else:
            out.append(head + rest.capitalize())
    return "".join(out)


def _is_a_letter(words: list[str], index: int) -> bool:
    """Whether the one-letter ``A`` at *index* is a letter and not the article.

    A lone capital is shouted and title case at once, so the word cannot say which
    it is, and its neighbours are asked. It is a letter where a team's letter
    stands: closing the name (HILLTOP A, DIVISION A), before anything that does not
    begin with a letter (HILLTOP A 2, HILLTOP A (W)), and after an article (THE A
    TEAM). And in a name printed in mixed case, whose capitals the printer chose
    (Hilltop A). And before a word a team's letter stands before, HILLTOP A GRADE
    beside HILLTOP B GRADE, unless a minor word stands before it, PART OF A TEAM.
    Anywhere else it is the article of a name that reads as a phrase: KINGS OF A
    COURT.

    The costs are phrases read as a team's name. WHAT A TEAM and FIVE A SIDE keep a
    capital A, and so does WORTH A 1000 WORDS, whose count reads as HILLTOP A 2's
    number does: no demo season or fixture prints such a name, so a rule telling a
    count from a team's number would be a guess, and ``[team_names]`` spells the
    name.
    """
    if index + 1 == len(words) or not words[index + 1][:1].isalpha():
        return True
    if words[index - 1].casefold() in _ARTICLES:
        return True
    if (words[index + 1].casefold() in _LETTER_BEFORE
            and words[index - 1].casefold() not in _MINOR_WORDS):
        return True
    return any(c.islower() for word in words for c in word)


def unslug(slug: str) -> str:
    """Last resort display name, when no printed spelling was ever seen."""
    return display_name(slug.replace("-", " ").title())


# How far a spelling may be from a known slug and still be taken for a typo of it,
# in edits. One limit for the question a near miss asks and the answer the nearest
# gives, so the two never disagree about what counts as close.
_NEAR = 2


def near_misses(slug: str, known: object, *, limit: int = 3) -> list[str]:
    """Known slugs within edit distance 2 of ``slug``, closest first.

    Every one of them, up to ``limit``, for a note that lists each name a banner is
    near and for asking whether a word is near a name at all. A question asks
    :func:`nearest` instead, through :func:`did_you_mean`: a list of every name
    within two edits asks about names that are further off as though they were as
    likely, and ``hiltop-a`` would be asked about Hilltop B, two edits away, beside
    Hilltop A, one.
    """
    scored = []
    for candidate in known:
        if candidate == slug:
            continue
        distance = _edit_distance(slug, candidate, cap=_NEAR)
        if distance <= _NEAR:
            scored.append((distance, candidate))
    scored.sort()
    return [candidate for _, candidate in scored[:limit]]


def nearest(slug: str, known: object) -> list[str]:
    """The known slugs nearest ``slug``, within two edits: one, a tie, or none.

    For every "did you mean", and for advice that hands over a line to paste. A
    list of every candidate within two edits is wrong for both: ``bo-nakamuru`` is
    one edit from ``bo-nakamura`` and two from a misspelling of it, and a list puts
    the misspelling up as an answer. So only the nearest come back, and all of them
    when two are equally near, because choosing between them would be guessing
    which one was meant.
    """
    scored = [(_edit_distance(slug, candidate, cap=_NEAR), candidate)
              for candidate in known]
    best = min((distance for distance, _ in scored if distance <= _NEAR),
               default=None)
    return sorted(candidate for distance, candidate in scored if distance == best)


def did_you_mean(names: list[str] | tuple[str, ...], *, keyed: bool = True) -> str:
    """What :func:`nearest` gave, as the question a line keyed on nobody ends with.

    ``""`` for none, the one name asked about, and a tie named whole, ``a or b``,
    with the advice to choose, because choosing for the club would be guessing which
    one it meant. One wording for every question about a name, the loader's, the
    ``[player_names]`` check's, the sheet's and the three about a card's banner, so
    a club told of a tie in any of them is told it the same way. A banner is no line
    the club keyed, and *keyed* false asks about it without the advice. Rejected:
    the sheet's check keeping the first of :func:`near_misses`, which on a tie is
    the one the alphabet chose, or wording a tie of its own, which is two sentences
    for one answer.
    """
    if not names:
        return ""
    asked = f" Did you mean {in_words(names, 'or')}?"
    if len(names) == 1 or not keyed:
        return asked
    return f"{asked} Key the line on the one it is for."


def _edit_distance(a: str, b: str, *, cap: int) -> int:
    """Levenshtein distance, abandoned once it cannot come in under ``cap``."""
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            current.append(min(previous[j] + 1,
                               current[j - 1] + 1,
                               previous[j - 1] + (ca != cb)))
        if min(current) > cap:
            return cap + 1
        previous = current
    return previous[-1]
