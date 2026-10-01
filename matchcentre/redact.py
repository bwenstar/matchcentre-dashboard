"""How a name is found in a piece of text and swapped for something that is not one.

Two parts of this project promise that a person's name does not get out.
``tools/doctor.py --redact`` promises it of the report a club pastes to somebody
else, and ``[build] players`` promises it of every file under the output folder.
SECURITY.md says what to do when either promise is broken. The two promises used
to be kept by two copies of one regular expression, which is how the day one copy
was fixed and the other was not would have arrived.

So this module holds the rule, and each caller states its policy when it asks.
The doctor matches case-sensitively, because its own labels sit beside the page's
words in the report it scrubs (``club hint`` beside ``Club``). The build ignores
case, because cards print names in capitals, and it adds each spelling without
its accents, because a name is spelt with them on one card and without them on the
next.

Nothing here knows what a player is. The caller learns the spellings, and this
module finds them.
"""

from __future__ import annotations

import functools
import re
from collections.abc import Callable, Collection, Iterable

# How two spellings compare: the one fold, which a slug is made of too.
from .slugs import fold

__all__ = ["fold", "words", "candidates", "substitute", "shape"]

# What a learned spelling is trimmed of before it is tried a second time: the
# punctuation that sits around a name on a page without being part of it.
_TRIM = "'\"()[]{},.:;!?-_ "


def words(text: str) -> list[str]:
    """The folded words of *text*, in order."""
    return re.findall(r"\w+", fold(text))


def candidates(text: str, ordinary: Collection[str]) -> list[str]:
    """What one spelling adds to the things a scrub must find.

    The spelling as written, and again with the punctuation round it trimmed off,
    each only when it is two characters or more, has a letter in it and is not an
    *ordinary* word -- one the caller must print anyway. *ordinary* is compared
    against ``casefold``, so the caller passes it folded. A lone letter is never a
    secret: shaping every "J" on a page would take the page's own punctuation with
    it.
    """
    out: list[str] = []
    for candidate in (text.strip(), text.strip(_TRIM)):
        if len(candidate) < 2:
            continue
        if not any(ch.isalpha() for ch in candidate):
            continue                      # a number is a score, or an id
        if candidate.casefold() in ordinary:
            continue
        if candidate not in out:
            out.append(candidate)
    return out


def substitute(text: str, secrets: Iterable[str],
               replace: Callable[[str], str], *, ignore_case: bool) -> str:
    """*text* with every whole occurrence of a secret replaced by ``replace(matched)``.

    Longest first, so a full name is replaced as one name rather than as two words;
    one pass, so a replacement cannot be matched again. Lookarounds rather than
    ``\\b``, because a secret can begin or end with punctuation, where ``\\b`` means
    the opposite of what is wanted. Equal lengths are ordered by the text, so the
    pattern is the same whatever order the secrets arrived in.

    *ignore_case* is the caller's policy, stated at every call. ``replace`` gets the
    text as it was matched, which differs from the secret when case is ignored.
    """
    pattern = _pattern(tuple(secrets), ignore_case)
    if pattern is None:
        return text
    return pattern.sub(lambda match: replace(match.group()), text)


@functools.lru_cache(maxsize=16)
def _pattern(secrets: tuple[str, ...], ignore_case: bool) -> re.Pattern[str] | None:
    """The one expression :func:`substitute` matches *secrets* by, built once.

    A build asks with the same secrets about every string it writes, thousands of
    them, and building the alternation again for each was most of what the guard
    cost. Keyed by the secrets as given; the order they came in changes the key and
    never the pattern.
    """
    ordered = sorted(set(secrets), key=lambda secret: (-len(secret), secret))
    if not ordered:
        return None
    return re.compile(r"(?<!\w)(?:" + "|".join(re.escape(s) for s in ordered)
                      + r")(?!\w)", re.IGNORECASE if ignore_case else 0)


def shape(text: str) -> str:
    """What a withheld string is replaced by: its size, not its content."""
    count = len(text.split())
    if count > 1:
        return f"<{count} words, {len(text)} chars>"
    return f"<{len(text)} chars>"
