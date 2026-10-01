"""A structural reader for the stylesheet, plus the contrast arithmetic WCAG defines.

Sibling of :mod:`jslint`, and here for the same reason: there is no browser on
this machine, so the only honest way to know whether the dashboard's words are
legible is to compute it from the file. A colour contrast is one of the few things
about a page that *can* be settled arithmetically -- it is a ratio between two
relative luminances, defined exactly, with no layout and no font in it -- so a
missing browser is no excuse for shipping grey-on-grey.

Three things live here.

**A rule reader.** Enough CSS parsing to answer one question: which colour does
each rule paint words in, and what is behind them. That is a question about pairs,
and the pairs are in the file -- ``.res.L`` declares its own background and its own
text colour in one rule -- so a reader that only collected the token table would
have to guess at exactly the places the file is explicit. And the two questions
every palette depends on: whether a rule writes a colour in by hand
(:func:`literal_colours`), which is a colour no palette can reach, and which
tokens it reads (:func:`tokens_read`), each of which a palette has to declare.

**A token reader.** The stylesheet declares its palette four times over: once for
light, twice for dark (the OS media query and the in-page toggle, which must
agree), once for print. A contrast that passes in one and fails in another is the
normal way this goes wrong, so every scope is resolved separately, each one
layered over the light base the way the cascade layers it.

**The arithmetic**, from WCAG 2.1 SC 1.4.3 verbatim: sRGB channels linearised
piecewise, weighted 0.2126/0.7152/0.0722, and the ratio taken with the 0.05 flare
term on both sides. :func:`contrast` is checked in the test suite against six
published pairs rather than against itself.

Known limitation, stated rather than hidden: this reader does not know the
cascade. It cannot tell that ``.pill .dot`` sits inside ``.pill``, so a rule that
declares a colour and no background of its own is checked against every surface
the page has -- which is stricter than the browser, and stricter in the safe
direction. It also does not resolve ``currentColor``, ``color-mix()`` or nested
``var()`` fallbacks; the stylesheet uses none of them, and :func:`resolve`
returns ``None`` rather than a wrong answer if one appears. And
:func:`literal_colours` knows all 148 colours CSS names but not the system colours
(``Canvas``, ``ButtonText`` and the rest), which are the browser's to choose rather
than a hand's, and which the stylesheet does not use.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = ["Colour", "Rule", "Scope", "strip_comments", "rules", "paper_only",
           "is_palette",
           "scopes", "literal_colours", "tokens_read", "resolve", "luminance",
           "contrast", "AA_SMALL"]

# WCAG 2.1 SC 1.4.3, the threshold for text below 18.66px bold / 24px regular.
# Everything this stylesheet paints with a shared token is checked against it --
# see the module docstring of the test that uses it for why the large-text
# exemption is not taken.
AA_SMALL = 4.5


@dataclass(frozen=True, slots=True)
class Colour:
    """An sRGB colour and its alpha, which is 1.0 unless the file said otherwise.

    Alpha is kept rather than flattened because a translucent background is not a
    colour until you know what is behind it, and the file does not always say.
    :meth:`over` is where that gets decided, by the caller, once.
    """

    rgb: tuple[int, int, int]
    alpha: float = 1.0

    @property
    def opaque(self) -> bool:
        return self.alpha >= 1.0

    def over(self, backdrop: tuple[int, int, int]) -> tuple[int, int, int]:
        """This colour composited onto ``backdrop``, rounded to 8-bit channels.

        Rounded because that is what a browser rasterises to, and a check that
        worked in unrounded floats could pass on a value no screen can show.
        """
        return tuple(round(f * self.alpha + b * (1 - self.alpha))
                     for f, b in zip(self.rgb, backdrop))


@dataclass(frozen=True, slots=True)
class Rule:
    """One ``selector { ... }`` block, with whatever at-rule encloses it.

    ``declarations`` keeps the source order and keeps duplicates, because the last
    one wins and a reader that deduped would answer the wrong question about a
    rule that sets a property twice.
    """

    selector: str
    at_rule: str
    declarations: tuple[tuple[str, str], ...]
    line: int

    def value(self, prop: str) -> str | None:
        """The winning value of ``prop`` in this rule, or ``None``.

        Last declaration wins, which is the cascade's rule within a single block.
        """
        found = [v for name, v in self.declarations if name == prop]
        return found[-1] if found else None

    @property
    def where(self) -> str:
        """A human-readable location, for a failure message that can be acted on."""
        place = f"{self.at_rule} " if self.at_rule else ""
        return f"styles.css:{self.line} {place}{self.selector}"


@dataclass(frozen=True, slots=True)
class Scope:
    """One resolved palette: a name to say it by, and every custom property in it.

    The tokens are already layered -- a dark scope holds the light base's values
    for everything it did not override -- because that is what a browser would
    read back, and a check against a half-populated table would silently skip the
    tokens the scope inherited.
    """

    name: str
    tokens: dict[str, str]


def strip_comments(css: str) -> str:
    """``/* ... */`` blanked, newlines kept, so reported line numbers are true."""
    def blank(match: re.Match[str]) -> str:
        return "\n" * match.group(0).count("\n")
    return re.sub(r"/\*.*?\*/", blank, css, flags=re.DOTALL)


def _declarations(body: str) -> tuple[tuple[str, str], ...]:
    found = []
    for part in body.split(";"):
        name, sep, value = part.partition(":")
        if sep and name.strip():
            found.append((name.strip().lower(), value.strip()))
    return tuple(found)


def rules(css: str) -> tuple[Rule, ...]:
    """Every rule in the file, at-rules descended into.

    Written as a brace walk rather than a regular expression because a regex for
    "a block that may contain blocks" is either wrong or unreadable, and the
    stylesheet's ``@media`` blocks are exactly that shape.
    """
    text = strip_comments(css)
    found: list[Rule] = []
    at_stack: list[str] = []
    depth = 0
    head_start = 0
    i = 0
    while i < len(text):
        char = text[i]
        if char == "{":
            head = text[head_start:i].strip()
            if head.startswith("@"):
                at_stack.append(" ".join(head.split()))
                depth += 1
                head_start = i + 1
                i += 1
                continue
            close = _matching(text, i)
            body = text[i + 1:close]
            # A rule that contains a nested block is an at-rule we do not know;
            # skipping its declarations is safer than reading the inner block's
            # as its own.
            if "{" not in body:
                raw = text[head_start:i]
                found.append(Rule(
                    selector=" ".join(head.split()),
                    at_rule=" ".join(at_stack),
                    declarations=_declarations(body),
                    # The line the selector is on, not the line the block opened
                    # on: a selector list spans several and the first is the one
                    # an editor jumps to.
                    line=1 + text.count("\n", 0, head_start)
                    + (len(raw) - len(raw.lstrip())
                       and raw[:len(raw) - len(raw.lstrip())].count("\n"))))
                i = close + 1
                head_start = i
                continue
        elif char == "}":
            if at_stack and depth:
                at_stack.pop()
                depth -= 1
            head_start = i + 1
        i += 1
    return tuple(found)


def paper_only(at_rule: str) -> bool:
    """Whether a rule inside *at_rule*, as :class:`Rule` spells it, applies to
    paper and never to a screen: some ``@media`` around it has only queries for
    ``print``.

    Read from the queries and not by looking for the word, because ``@media not
    print`` names paper and applies to every screen: read as a print block, a
    ``min-width: 60ch`` on the rules paragraphs inside one was left out of every
    screen the tests read, and made the opened page 441px wide at 375.
    """
    return any(all(re.fullmatch(r"(?:only\s+)?print(?:\s+and\b.*)?",
                                query.strip(), re.I | re.S)
                   for query in queries.split(","))
               for queries in re.findall(r"@media\s+([^@]*)", at_rule))


def _matching(text: str, open_at: int) -> int:
    depth = 0
    for i in range(open_at, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    return len(text)


def is_palette(rule: Rule) -> bool:
    """Whether ``rule`` declares a palette, which is whether it is ``:root`` itself.

    One answer for the two things that ask: :func:`scopes`, which reads the
    palettes, and the check that no colour is written in anywhere else. Asked in
    two places, a rule could be a palette to one of them and an ordinary rule to
    the other, and a colour in it would be read by neither.

    Every selector in it, however qualified, and not merely mentioning it:
    ``:root[data-theme="dark"]`` is a palette, and ``:root[data-theme="dark"]
    .res.W`` is a badge. Read as a palette, the badge would be two more of them,
    one for the screen and print laid over it, each the light base's tokens under
    a dark name, and a token it declared would be let off the check on colours
    written in.
    """
    bare = rule.selector
    # The qualifiers taken off, innermost first, because they nest:
    # `:where(:not([data-theme="light"]))`.
    while True:
        shorter = re.sub(r"\[[^\[\]]*\]|\([^()]*\)", "", bare)
        if shorter == bare:
            break
        bare = shorter
    return all(re.fullmatch(r":root(?::[\w-]+)*", part.strip())
               for part in bare.split(","))


# A colour written by hand, three ways: hex; a colour function; and a name -- any
# of the 148 CSS defines, both spellings of grey among them, because `darkgray`
# and `whitesmoke` are as much what a hand types as `white` is. Not
# `transparent`, which is no colour, nor `currentColor`, which is the text's own
# and is checked where the text's colour is declared. Anchored so that part of a
# word is not a colour: `var(--text-red)` is a token and `grayscale` a filter.
_NAMED_COLOURS = frozenset("""
    aliceblue antiquewhite aqua aquamarine azure beige bisque black blanchedalmond
    blue blueviolet brown burlywood cadetblue chartreuse chocolate coral
    cornflowerblue cornsilk crimson cyan darkblue darkcyan darkgoldenrod darkgray
    darkgreen darkgrey darkkhaki darkmagenta darkolivegreen darkorange darkorchid
    darkred darksalmon darkseagreen darkslateblue darkslategray darkslategrey
    darkturquoise darkviolet deeppink deepskyblue dimgray dimgrey dodgerblue
    firebrick floralwhite forestgreen fuchsia gainsboro ghostwhite gold goldenrod
    gray green greenyellow grey honeydew hotpink indianred indigo ivory khaki
    lavender lavenderblush lawngreen lemonchiffon lightblue lightcoral lightcyan
    lightgoldenrodyellow lightgray lightgreen lightgrey lightpink lightsalmon
    lightseagreen lightskyblue lightslategray lightslategrey lightsteelblue
    lightyellow lime limegreen linen magenta maroon mediumaquamarine mediumblue
    mediumorchid mediumpurple mediumseagreen mediumslateblue mediumspringgreen
    mediumturquoise mediumvioletred midnightblue mintcream mistyrose moccasin
    navajowhite navy oldlace olive olivedrab orange orangered orchid palegoldenrod
    palegreen paleturquoise palevioletred papayawhip peachpuff peru pink plum
    powderblue purple rebeccapurple red rosybrown royalblue saddlebrown salmon
    sandybrown seagreen seashell sienna silver skyblue slateblue slategray
    slategrey snow springgreen steelblue tan teal thistle tomato turquoise violet
    wheat white whitesmoke yellow yellowgreen
""".split())
_LITERAL_COLOUR = re.compile(
    r"#[0-9a-fA-F]{3,8}\b"
    r"|(?<![\w-])(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch|color)\([^)]*\)"
    r"|(?<![\w-])(?:" + "|".join(sorted(_NAMED_COLOURS)) + r")(?![\w-])",
    re.IGNORECASE)


def literal_colours(value: str) -> list[str]:
    """Every colour written into ``value`` by hand, in the order it is written.

    A declaration's value, not a rule: a colour inside a ``box-shadow`` or a
    ``border`` is as much a colour as a ``background`` is, and a list of the
    properties that can hold one is a list that is one short the day somebody
    writes an ``outline``.
    """
    return [found.group(0) for found in _LITERAL_COLOUR.finditer(value)]


def tokens_read(value: str) -> list[str]:
    """Every custom property ``value`` reads, in the order it reads them.

    A fallback's own ``var()`` included: the browser reads it whenever the first
    is not declared, which is the case a caller is asking about.
    """
    return re.findall(r"var\(\s*(--[\w-]+)", value)


def scopes(css: str) -> tuple[Scope, ...]:
    """Every palette a reader can actually end up with, the light base first.

    A palette is any rule on ``:root`` (:func:`is_palette`). Found that way rather
    than by naming the four known selectors, so adding a fifth brings it under
    whatever checks these scopes feed instead of quietly past it.

    Print is not one palette among the others, it is an *overlay*: a page is
    printed in whichever theme it was being read in, so ``@media print`` layers
    over the dark palette as readily as over the light one. So a print palette is
    combined with each of the others rather than listed beside them -- which is the
    combination a token declared in dark and forgotten in print goes wrong in, and
    the whole reason this is worked out here rather than assumed.
    """
    roots = [r for r in rules(css) if is_palette(r)]
    if not roots:
        return ()
    base = _custom(roots[0])
    screen = []
    for rule in roots:
        if paper_only(rule.at_rule):
            continue
        tokens = dict(base)
        tokens.update(_custom(rule))
        screen.append(Scope(_scope_name(rule), tokens))
    combined = []
    for rule in roots:
        if not paper_only(rule.at_rule):
            continue
        for under in screen:
            tokens = dict(under.tokens)
            tokens.update(_custom(rule))
            combined.append(Scope(f"{_scope_name(rule)} over {under.name}", tokens))
    return tuple(screen + combined)


def _custom(rule: Rule) -> dict[str, str]:
    return {name: value for name, value in rule.declarations
            if name.startswith("--")}


def _scope_name(rule: Rule) -> str:
    return f"{rule.at_rule} {rule.selector}".strip()


def resolve(value: str, tokens: dict[str, str], seen: frozenset[str] = frozenset()
            ) -> Colour | None:
    """A declaration's value as a colour, or ``None`` if it is not one.

    ``None`` covers ``inherit``, ``none``, ``transparent``, a token this scope does
    not declare, and any syntax this reader does not know. The caller decides what
    that means; returning a default colour here would invent a fact about the page.
    """
    value = value.strip().rstrip("!important").strip()
    var = re.fullmatch(r"var\(\s*(--[\w-]+)\s*\)", value)
    if var:
        name = var.group(1)
        if name in seen or name not in tokens:       # a cycle, or not in this scope
            return None
        return resolve(tokens[name], tokens, seen | {name})
    hexed = re.fullmatch(r"#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})", value)
    if hexed:
        digits = hexed.group(1)
        if len(digits) == 3:
            digits = "".join(c * 2 for c in digits)
        return Colour(tuple(int(digits[i:i + 2], 16) for i in (0, 2, 4)))
    func = re.fullmatch(r"rgba?\(([^)]*)\)", value)
    if func:
        parts = [p.strip() for p in func.group(1).replace("/", ",").split(",")
                 if p.strip()]
        if len(parts) not in (3, 4):
            return None
        try:
            channels = tuple(_channel(p) for p in parts[:3])
            alpha = float(parts[3].rstrip("%")) / (
                100 if len(parts) == 4 and parts[3].endswith("%") else 1) \
                if len(parts) == 4 else 1.0
        except ValueError:
            return None
        return Colour(channels, min(max(alpha, 0.0), 1.0))
    return None


def _channel(part: str) -> int:
    if part.endswith("%"):
        return round(float(part[:-1]) * 255 / 100)
    return min(max(round(float(part)), 0), 255)


def luminance(rgb: tuple[int, int, int]) -> float:
    """Relative luminance, WCAG 2.1 section "relative luminance", verbatim.

    The piecewise split at 0.03928 is the spec's, not a rounding of it: the two
    branches disagree by enough at the dark end to move a ratio in the second
    decimal place, which is where a threshold lives.
    """
    def channel(eight_bit: int) -> float:
        c = eight_bit / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    red, green, blue = (channel(c) for c in rgb)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast(one: tuple[int, int, int], other: tuple[int, int, int]) -> float:
    """The contrast ratio between two opaque colours, lighter over darker.

    Order does not matter, which is why it is worked out here rather than left to
    the caller to get right: (L1 + 0.05) / (L2 + 0.05) with L1 the lighter.
    """
    first, second = luminance(one), luminance(other)
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)
