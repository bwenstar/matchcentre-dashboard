"""Names in, stable identifiers out.

A slug is this project's primary key, for teams and for players alike. That is a
deliberate change from keying teams on their draw number: draw numbers appear on
no scorecard, so deriving them means inventing them, and anything invented shifts
when a card is added, removed or read in a different order. A slug is a pure
function of the printed name, so it does not move.

Everything here is intentionally boring. The only subtle part is
:func:`display_name`, which has to un-shout ``RIVERSIDE ROCKETS`` without
mangling the acronyms clubs genuinely use.
"""

from __future__ import annotations

import re
import unicodedata

__all__ = ["slugify", "team_slug", "apply_alias", "display_name", "near_misses"]

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
# reads as "Kings of the Court".
_MINOR_WORDS = frozenset({"a", "an", "and", "at", "de", "for", "in", "of", "on",
                          "or", "the", "to", "van", "von"})


def slugify(text: str) -> str:
    """``"Ana Fernández"`` -> ``"ana-fernandez"``.

    Raises rather than returning ``""``: an empty identifier would collapse two
    different subjects into one key, and every caller here is holding text it
    read off a card and believes is a name.
    """
    folded = unicodedata.normalize("NFKD", text.strip().casefold())
    stripped = "".join(c for c in folded if not unicodedata.combining(c))
    slug = _NOT_SLUG.sub("-", stripped).strip("-")
    if not slug:
        raise ValueError(f"{text!r} has no sluggable characters")
    return slug


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
    leaves short all-caps tokens alone because those are club abbreviations.

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
        letters = [c for c in word if c.isalpha()]
        shouted = bool(letters) and all(c.isupper() for c in letters)
        # A minor word is checked before the acronym rule, because "OF" and "THE"
        # are short enough to look like club abbreviations: without this,
        # "KINGS OF THE COURT" came out as "Kings OF THE Court". A minor word
        # leading the name still gets a capital, which is ordinary title case --
        # "THE ACES" is "The Aces", not "the Aces".
        if shouted and word.casefold() in _MINOR_WORDS:
            out.append(word.casefold() if index else word.capitalize())
            continue
        if shouted:
            if len(letters) <= _ACRONYM_MAX:
                out.append(word)          # RTC, WDTA — an abbreviation, keep it
                continue
            word = word.capitalize()      # ROCKETS -> Rockets
        out.append(word)
    return " ".join(out)


def unslug(slug: str) -> str:
    """Last resort display name, when no printed spelling was ever seen."""
    return display_name(slug.replace("-", " ").title())


def near_misses(slug: str, known: object, *, limit: int = 3) -> list[str]:
    """Known slugs within edit distance 2 of ``slug``, closest first.

    Exists for one purpose: when a team appears that the config has never heard
    of, the likeliest explanation is a typo or an OCR-ish difference in one
    card's banner, and saying "did you mean *rockets*?" turns a mystifying extra
    team into a one-word fix.
    """
    scored = []
    for candidate in known:
        if candidate == slug:
            continue
        distance = _edit_distance(slug, candidate, cap=2)
        if distance <= 2:
            scored.append((distance, candidate))
    scored.sort()
    return [candidate for _, candidate in scored[:limit]]


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
