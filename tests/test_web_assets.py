"""The dashboard's browser code, checked without a browser.

There is no JavaScript engine on the machine this was built on, so none of this
code can be run by its own test suite. That is a real gap and this module is the
honest response to it rather than a pretence that it does not exist: everything
here is a structural check, and each one is aimed at a failure that would show a
club a *blank page* or a *silently wrong page* -- the two outcomes no amount of
careful Python upstream can prevent.

Four kinds of check live here.

**Well-formedness** (:mod:`jslint`). A brace that does not close makes the whole
script fail to parse, so nothing runs and the page renders empty. A browser says
so in a console the club will never open.

**The DOM contract.** ``app.js`` and ``index.html`` were written by different
agents against an agreed list of ids and classes. Nothing in either file fails
loudly if one side drops a hook: ``document.getElementById('rules-banner')``
returns ``null``, the guard skips the update, and the banner announcing which
scoring rules were assumed simply never appears. That is the worst kind of bug
this project can ship -- a number presented without the caveat that qualifies it.

**No club specifics.** The predecessor hardcoded one club's title and one club's
``localStorage`` key, so a second club's dashboard announced the first club's name
and quietly overwrote its theme choice.

**Legibility** (:mod:`csslint`). The one thing about a rendered page that can be
settled by arithmetic instead of by looking: a contrast ratio is defined exactly,
with no layout and no font in it. Every colour the stylesheet paints words in is
computed against everything those words can sit on, in each of the palettes the
file declares -- because a caveat printed at 3.24:1 is a caveat nobody reads, next
to a number they can.
"""

from __future__ import annotations

import functools
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import csslint                                            # noqa: E402
import jslint                                             # noqa: E402

WEB = ROOT / "matchcentre" / "web"
APP = WEB / "app.js"
INDEX = WEB / "index.html"
STYLES = WEB / "styles.css"
PICKER = WEB / "picker.html"
CAREERS = WEB / "careers.html"
# Every page template site.py fills, each checked by the loops below.
PAGES = ("index.html", "picker.html", "careers.html")

# Hooks both the page and the script must agree on. Each exists to carry a caveat
# rather than a number, which is why a missing one matters: the figure still
# renders, just without the words that make it honest.
#
#   why it must not silently vanish                        js name      css/html name
DOM_CONTRACT = (
    # Which scoring rules were assumed, when the club has not declared any.
    ("rules-banner", "id"),
    # Marks any value resting on a guess rather than on something printed.
    ("provenance-flag", "class"),
    # Only present in multi-season mode; the single-season page must not show it.
    ("season-picker", "id"),
    # Says whether the ladder's order stands while teams have played unequal
    # matches: a snapshot on a total, standing on the average, and a snapshot
    # there too between teams level on it on unequal counts.
    ("ladder-note", "class"),
    # Whether the season validated, and how many cards it was built from. Listed
    # here rather than left to the getElementById sweep below, which catches it
    # only for as long as renderHealth() asks for it by a literal id: the contract
    # is what the two files agreed on, not a side effect of one call style.
    #
    # This is the one hook that is both: an id the shell reserves and a class the
    # stylesheet hides while it is empty. Declaring it as the id is what makes this
    # entry say anything the sweep does not, and it is why the check below asks for
    # the spelling the kind names rather than for the word in either file.
    ("health", "id"),
    # Cards that could not be read. An unread card is missing data, not zero.
    ("rejected", "id"),
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def script_sources(source: str) -> list[str]:
    """The ``src`` of every ``<script>``, in the order the browser runs them.

    Read from the tags rather than by searching the text, because ``index.html``
    documents its own load order in a comment above the tags -- so a plain
    ``source.find("app.js")`` lands on the prose and reports an order that is the
    reverse of the real one.
    """
    found = []
    for match in re.finditer(r"<script([^>]*)>", source, re.IGNORECASE):
        src = re.search(r'\bsrc\s*=\s*["\']([^"\']+)["\']', match.group(1))
        if src:
            found.append(src.group(1))
    return found


def inline_scripts(source: str) -> list[str]:
    """Every ``<script>`` body in an HTML file that has no ``src``.

    Line numbers are kept true by replacing the markup before each script with
    blank lines, so a fault reported at line 40 is at line 40 of the HTML file.
    """
    found = []
    for match in re.finditer(r"<script([^>]*)>(.*?)</script\s*>", source,
                             re.DOTALL | re.IGNORECASE):
        if "src=" in match.group(1).lower():
            continue
        before = source[:match.start(2)]
        found.append("\n" * before.count("\n") + match.group(2))
    return found


# An `if` as the keyword and not as the end of a longer name, in code_only() text.
IF = re.compile(r"(?<![\w$])if \(")


def closing_bracket(code: str, opener: int) -> int:
    """Where the bracket at *opener* closes, counted over :func:`jslint.code_only`
    text so that a bracket in a string or a comment is not one."""
    depth = 0
    for index in range(opener, len(code)):
        if code[index] in "([{":
            depth += 1
        elif code[index] in ")]}":
            depth -= 1
            if depth == 0:
                return index
    raise AssertionError(f"the bracket at offset {opener} never closes")


def statement_end(code: str, start: int) -> int:
    """Where the statement beginning at or after *start* ends: the ``}`` closing its
    block, or its own ``;``."""
    while code[start].isspace():
        start += 1
    if code[start] == "{":
        return closing_bracket(code, start)
    depth = 0
    for index in range(start, len(code)):
        if code[index] in "([{":
            depth += 1
        elif code[index] in ")]}":
            depth -= 1
            if depth < 0:
                return index
        elif code[index] == ";" and depth == 0:
            return index
    return len(code)


def still_open(code: str, start: int, at: int) -> bool:
    """Whether the block *start* is in still holds *at*: no ``}`` between the two
    closes more than was opened between them."""
    depth = 0
    for char in code[start:at]:
        depth += {"{": 1, "}": -1}.get(char, 0)
        if depth < 0:
            return False
    return True


def reach(code: str, start: int, name: str) -> int:
    """How far what *name* begins at *start* runs: to the bracket closing its
    arguments where it is a call, and otherwise to the end of its statement."""
    after = start + len(name)
    while after < len(code) and code[after].isspace():
        after += 1
    if after < len(code) and code[after] == "(":
        return closing_bracket(code, after)
    return statement_end(code, start)


# A logical property, as the physical one it is on a page written left to right and
# top to bottom, which is the only way this one is written.
LOGICAL = {"margin-block-start": "margin-top", "margin-block-end": "margin-bottom",
           "margin-inline-start": "margin-left", "margin-inline-end": "margin-right",
           "max-inline-size": "max-width", "min-inline-size": "min-width",
           "inline-size": "width"}

# A pseudo-element in a selector, by either spelling: `::before`, and `:before` as
# CSS 2 wrote it with one colon, which browsers still read. The box it names is
# inside the element and not the element.
PSEUDO_ELEMENT = re.compile(r"::([\w-]+)|:(before|after|first-line|first-letter)\b",
                            re.I)


def sets_no_size(value: str | None) -> bool:
    """Whether a width or a floor, as :meth:`WebAssetCase.cascaded` reads it, is no
    size at all: unset, ``auto``, ``none``, or a zero in any unit.

    One reading for every test that asks, because there were three: a floor of
    ``0px`` was a floor to one of them and none to another, and ``0.0em`` a floor
    to the second.
    """
    return value in (None, "auto", "none") or bool(
        re.fullmatch(r"0*\.?0+(?:%|[a-z]*)", value))


def longhands(declarations) -> list[tuple[str, str, bool]]:
    """Each declaration as the longhands it sets, ``(name, value, important)``, in
    source order: a ``margin`` written after a ``margin-top`` overrides it, and one
    written before it does not."""
    found = []
    for name, value in declarations:
        important = re.search(r"\s*!\s*important\s*$", value)
        bare = (value[:important.start()] if important else value).strip()
        parts = bare.split()
        if name == "margin" and parts:
            top, right = parts[0], parts[1 if len(parts) > 1 else 0]
            sets = (("margin-top", top), ("margin-right", right),
                    ("margin-bottom", parts[2] if len(parts) > 2 else top),
                    ("margin-left", parts[3] if len(parts) > 3 else right))
        elif name == "margin-block" and parts:
            sets = (("margin-top", parts[0]), ("margin-bottom", parts[-1]))
        elif name in ("white-space", "text-wrap") and parts:
            # Both set whether a line may break, and a later one undoes an earlier:
            # `white-space: nowrap` and then `text-wrap: balance` breaks again.
            # `nowrap` and `pre` refuse the break, and every other value allows it.
            sets = (("text-wrap-mode",
                     "nowrap" if "nowrap" in parts or bare == "pre" else "wrap"),)
            if name == "text-wrap":
                # And how the lines share the words, which `white-space` leaves alone.
                sets += (("text-wrap-style", next(
                    (part for part in parts if part in ("balance", "pretty", "stable")),
                    "auto")),)
        else:
            sets = ((LOGICAL.get(name, name), bare),)
        found += [(longhand, part, bool(important)) for longhand, part in sets]
    return found


class WebAssetCase(unittest.TestCase):
    """Base class that skips nothing: a missing asset is a failure, not a skip."""

    def assets(self) -> dict[str, str]:
        missing = [p.name for p in (APP, INDEX, STYLES, PICKER, CAREERS)
                   if not p.is_file()]
        if missing:
            self.fail(f"missing page template(s) in {WEB}: {', '.join(missing)}")
        return {"app.js": read(APP), "index.html": read(INDEX),
                "styles.css": read(STYLES), "picker.html": read(PICKER),
                "careers.html": read(CAREERS)}

    def body(self, name: str) -> str:
        """One top-level function's source, comments and strings included.

        Searching one function rather than the whole file is what makes a check
        like "this calls that" mean anything: whole-file searches find the
        definition and pass, which is the mistake being tested for one level up.
        """
        source = self.assets()["app.js"]
        start = source.find(f"\nfunction {name}(")
        self.assertGreater(start, 0, f"app.js defines no {name}()")
        end = source.find("\nfunction ", start + 1)
        return source[start:end if end > 0 else len(source)]

    def guard_for(self, fn: str, phrase: str, *, emits: str = "appendChild",
                  exits: bool = False, disjunction: bool = False) -> str:
        """The condition every printing of *phrase* in *fn* is printed under.

        The one reader of that question in this suite. There were ten, written as each
        test needed one. They agreed about today's app.js, and item 4.21 measured
        thirty-five mutants of it getting past one or another of them -- so what they
        disagreed about is decided here, once:

        * A printing is an occurrence outside a comment, and every one is read. They
          have to agree: a reader of the first alone is satisfied by a guarded
          printing above an unguarded one, and a comment in the same words is a note
          to whoever reads the code rather than a sentence anybody is shown.
        * The ``if`` is the one whose consequent holds the phrase. The nearest one
          above it may belong to a block that has already closed, and its condition
          then says nothing about the phrase. A phrase in that ``if``'s ``else`` is
          refused rather than read, because its guard is the condition's opposite.
        * The condition runs to its own bracket, counted, and comes back with its
          comments blanked. Read to the first ``)`` it stops inside a call:
          ``!isSingleFile()`` comes back as ``!isSingleFile(``, and read to the
          phrase instead it is satisfied by the text itself. A term in a comment
          inside the brackets is a term nothing tests.
        * The condition is a conjunction. Every caller asks whether a term is in it,
          which says the phrase needs that term only while every part is joined by
          ``&&``. A ``||``, a ``?`` or a negated group anywhere in it, bracketed or
          not, makes the term one way to the phrase among others, or the way it is
          kept off. A caller whose guard is rightly a disjunction says so with
          ``disjunction=True`` and answers for each alternative itself, which lets
          ``||`` through and neither of the others. A negated group is refused even
          where it says what its terms say, as ``!(a !== b)`` does: telling the two
          apart is parsing, and no guard these tests read is written with one.
        * The phrase reaches the page through *emits*: it is inside the brackets of a
          call to it, or inside the statement it begins where it is not a call. A
          call in a comment is not a call, and a call beside the phrase hands the page
          something else while the phrase is built and dropped.

        With ``exits=True`` an ``if (...) return ...;`` ahead of the phrase, in a
        block that holds it, answers too, and what comes back is then the condition
        under which the phrase is *not* reached. Asked for rather than assumed,
        because read by default an early exit written the wrong way round comes back
        as the same words as the guard written the right way round.
        """
        body = self.body(fn)
        code = jslint.code_only(body)
        said = jslint.without_comments(body)
        printings = [found.start() for found in re.finditer(re.escape(phrase), said)]
        self.assertTrue(printings, f"{fn}() never says {phrase!r}")
        carriers = re.compile(re.escape(emits))
        guards = []
        for at in printings:
            ifs = [found.start() for found in IF.finditer(code, 0, at)]
            for opens in reversed(ifs):
                close = closing_bracket(code, opens + len("if"))
                condition = said[opens + len("if ("):close].strip()
                end = statement_end(code, close + 1)
                if at <= end:
                    break
                other = re.match(r"\s*else\b(?!\s*if\b)", code[end + 1:])
                self.assertFalse(
                    other and at <= statement_end(code, end + 1 + other.end()),
                    f"{phrase!r} is printed in the else of `if ({condition})` in "
                    f"{fn}(), so what guards it is that condition's opposite")
                if (exits and re.match(r"\s*return\b", code[close + 1:])
                        and still_open(code, end, at)):
                    break
            else:
                self.fail(f"{phrase!r} is printed under no `if` of its own in {fn}()")
            refused = r"\?|!\s*\(" if disjunction else r"\|\||\?|!\s*\("
            self.assertIsNone(
                re.search(refused, code[opens + len("if ("):close]),
                f"`if ({condition})` in {fn}() is not a plain disjunction, so its "
                f"alternatives are not each a way to {phrase!r} on their own"
                if disjunction else
                f"`if ({condition})` in {fn}() is not a conjunction, so a term in it "
                f"is one way to {phrase!r} among others and not one it needs")
            self.assertTrue(
                any(start <= at and at + len(phrase) <= reach(code, start, emits) + 1
                    for start in (found.start() for found in
                                  carriers.finditer(code, close, at + len(phrase)))),
                f"{phrase!r} is not inside what {emits} hands the page in {fn}(), "
                f"so what reaches the page is something else")
            guards.append(condition)
        self.assertEqual(len(set(guards)), 1,
                         f"{fn}() prints {phrase!r} {len(guards)} times under "
                         f"different conditions, so no one of them is its guard: "
                         f"{guards}")
        return guards[0]

    def print_rules(self) -> list[csslint.Rule]:
        """Every rule the stylesheet only applies to paper.

        On the base class because two classes below ask the same question of the print
        block -- what it keeps on the sheet, and what it does to a match card -- and a
        block found one way in one of them and another way in the other is two readers
        that can disagree about which rules are even in it.
        """
        return [rule for rule in csslint.rules(self.assets()["styles.css"])
                if csslint.paper_only(rule.at_rule)]

    def compound(self, text: str) -> dict:
        """One simple selector, as element name, classes and id.

        Pseudo-classes and pseudo-elements are dropped, so a rule for ``:hover`` is
        read as though it always held. A caller for which a pseudo-element selects a
        different box says so itself, as :meth:`cascaded` does. An element's name runs
        on through its digits, so ``h1`` is not ``h``.
        """
        bare = re.sub(r"::?[\w-]+(\([^)]*\))?", "", text)
        bare = re.sub(r"\[[^\]]*\]", "", bare)
        element = re.match(r"[a-zA-Z][a-zA-Z0-9]*", bare)
        ident = re.search(r"#([\w-]+)", bare)
        return {"el": element.group(0) if element else "",
                "cls": set(re.findall(r"\.([\w-]+)", bare)),
                "id": ident.group(1) if ident else ""}

    def selects(self, selector: str, chain: tuple[dict, ...]) -> bool:
        """Does *selector* select the last element of *chain*, read from the root down?

        On the base class because two classes below ask it: whether a print rule hides
        the words that name the sheet, and which rules reach the paragraphs behind the
        rules banner's tap. Enough of a matcher for chains a few elements long, and not
        a CSS engine. A class is a whole class, so ``.muted`` is not ``.muted-note``. A
        space is any ancestor and ``>`` the one directly above. ``+`` and ``~`` are read
        as holding, the compound before them dropped: a chain has no siblings in it,
        and which ones an element has is the script's to change.
        """
        parts, joined = [], " "
        # A compound runs over brackets, so `:nth-child(2n + 1)` is not three of them.
        for token in re.findall(r"[>+~]|(?:[^\s>+~()]|\((?:[^()]|\([^()]*\))*\))+",
                                selector.strip()):
            if token in ("+", "~"):
                joined = parts.pop()[0] if parts else " "
            elif token == ">":
                joined = ">"
            else:
                parts.append((joined, self.compound(token)))
                joined = " "

        def fits(want: dict, node: dict) -> bool:
            return ((not want["el"] or want["el"] == node["el"])
                    and want["cls"] <= node["cls"]
                    and (not want["id"] or want["id"] == node["id"]))

        def holds(index: int, at: int) -> bool:
            if not fits(parts[index][1], chain[at]):
                return False
            if index == 0:
                return True
            above = (at - 1,) if parts[index][0] == ">" else range(at)
            return any(holds(index - 1, up) for up in above if up >= 0)

        return bool(parts) and holds(len(parts) - 1, len(chain) - 1)

    def specificity(self, selector: str) -> tuple[int, int, int]:
        """Ids, then classes, attributes and pseudo-classes, then element names and
        pseudo-elements, counted in one selector.

        Counted over the text, so ``:not()`` and ``:where()`` are not given their own
        rules; the sheet uses them on neither of the chains these tests read.
        """
        return (len(re.findall(r"#[\w-]+", selector)),
                len(re.findall(r"\.[\w-]+|\[[^\]]*\]|(?<!:):(?!:)[\w-]+", selector)),
                len(re.findall(r"(?:^|[\s>+~(])[a-zA-Z][\w-]*|::[\w-]+", selector)))

    def cascaded(self, chain: tuple[dict, ...], prop: str, screen: str = "",
                 pseudo: str = "") -> tuple[str | None, csslint.Rule | None]:
        """The value the sheet gives *prop* on the last element of *chain*, or on
        the box *pseudo* names inside it, and the rule it comes from, or
        ``(None, None)`` where no rule sets it.

        Among the rules outside any at-rule and those inside *screen*, one of
        :meth:`screens`: ``!important`` first, then specificity, then the later rule,
        then the later declaration in it. Shorthands are read as the longhands they
        set (:func:`longhands`). A selector naming a pseudo-element selects a box
        inside the element and not the element, so it is read only where *pseudo*
        names that box, without its colons (``before``, ``details-content``), and a
        selector naming none only where *pseudo* is empty.
        """
        best = None
        for order, rule in enumerate(csslint.rules(self.assets()["styles.css"])):
            if rule.at_rule not in ("", screen):
                continue
            for selector in rule.selector.split(","):
                named = [(found.group(1) or found.group(2)).lower()
                         for found in PSEUDO_ELEMENT.finditer(selector)]
                if ((named[-1] if named else "") != pseudo.lower()
                        or not self.selects(selector, chain)):
                    continue
                for index, (name, value, important) in enumerate(
                        longhands(rule.declarations)):
                    rank = (important, self.specificity(selector), order, index)
                    if name == prop and (best is None or rank > best[0]):
                        best = (rank, value, rule)
        return (best[1], best[2]) if best else (None, None)

    def inherited(self, chain: tuple[dict, ...], prop: str, screen: str
                  ) -> tuple[str | None, csslint.Rule | None]:
        """The value an inherited *prop* has on the last element of *chain*, and the
        rule it comes from: the nearest element in it that the sheet gives one, or
        ``(None, None)`` where the sheet gives none of them one.

        On the base class because two classes below ask it of a line's wrap: the rules
        paragraphs behind the tap, and the blocks outside the banner."""
        for depth in range(len(chain), 0, -1):
            value, rule = self.cascaded(chain[:depth], prop, screen)
            if value:
                return value, rule
        return None, None

    def screens(self) -> list[str]:
        """Every at-rule the sheet applies to a screen, ``""`` first for the rules
        outside any: a width or a preference each, read one at a time by
        :meth:`cascaded`. ``@media not print``, which is every screen, is one
        (:func:`csslint.paper_only`)."""
        return [""] + sorted({rule.at_rule
                              for rule in csslint.rules(self.assets()["styles.css"])
                              if rule.at_rule and not csslint.paper_only(rule.at_rule)})

    def klass(self, kind: str) -> str:
        """The first class the rules banner's script puts on an element it builds
        as *kind*.

        The tie between the two files runs this way round on purpose: the sheet has to
        reach the element the page actually builds, and a class spelled as a constant
        in this file would be satisfied by two files that each agree with the constant
        and not with each other.
        """
        found = re.search(r"el\('%s', \{ class: '([\w-]+)" % kind,
                          self.body("renderRulesBanner"))
        self.assertIsNotNone(found, f"the banner's <{kind}> carries no class, so "
                                    f"nothing in the stylesheet can reach it")
        return found.group(1)

    def paragraph_classes(self) -> list[list[str]]:
        """The classes of each paragraph the banner builds, one list per paragraph.

        Every paragraph and not the first, because there are two kinds and either can
        be the long one: a rule's explanation, 143 characters for ``draws = false``,
        and the advisory, 161, which carries ``muted`` as well. A claim read off
        :meth:`klass` is a claim about the explanations, and it holds with the
        advisory's class spelled differently from theirs.

        The class is read from anywhere in the paragraph's own object of attributes,
        not only as its first key: written after the text, the advisory's class was
        out of sight and the advisory with it. A paragraph whose class cannot be read
        as one string is a failure rather than a paragraph passed over.
        """
        region = self.body("renderRulesBanner")
        said, code = jslint.without_comments(region), jslint.code_only(region)
        found = []
        for built in re.finditer(r"(?<![\w$])el\(\s*'p'\s*,\s*", said):
            self.assertEqual(code[built.end():built.end() + 1], "{",
                             f"a paragraph the banner builds is given its attributes "
                             f"as something other than an object written there, so "
                             f"which class it carries cannot be read: "
                             f"{said[built.start():built.end() + 40]!r}")
            close = closing_bracket(code, built.end())
            # How deep each character of the object is, so that a `class` inside a
            # nested `style: { ... }` is not taken for the paragraph's own.
            depth, level = [], 0
            for char in code[built.end():close + 1]:
                level += char in "([{"
                depth.append(level)
                level -= char in ")]}"
            spelled = [key for key in re.finditer(
                           r"(?<![\w$'\"])['\"]?class['\"]?\s*:\s*'([^']*)'\s*[,}]",
                           said[built.end():close + 1])
                       if depth[key.start()] == 1]
            self.assertEqual(len(spelled), 1,
                             f"a paragraph the banner builds does not carry its class "
                             f"as one string of its own, so what the sheet does with "
                             f"it cannot be read: {said[built.start():close + 1]!r}")
            found.append(spelled[0].group(1).split())
        self.assertTrue(found, "the banner builds no paragraph the sheet can reach")
        return found

    def paragraph_chain(self, classes: list[str]) -> tuple[dict, ...]:
        """Where a paragraph behind the tap sits, from the root down: ``<html>``,
        ``<body>``, the banner as index.html writes it and the hook adds to it, the
        disclosure as the script builds it, and the paragraph with *classes*.

        The banner is asserted to be a child of ``<body>``, because a wrapper
        between the two would be an ancestor a rule could reach the paragraphs
        through and this chain would not have.

        On the base class, with :meth:`paragraph_classes` and :meth:`klass`,
        because two classes below ask where these paragraphs sit: how many
        characters their lines hold, and whether anything from the root down to
        them is given a width a phone is narrower than. One chain for both, so
        the two cannot differ about which elements a rule reaches them through.
        """
        region = self.body("renderRulesBanner")
        ident, extra = re.search(r"hook\('([\w-]+)', '([\w-]+)'", region).groups()
        page = re.sub(r"<!--.*?-->", "", self.assets()["index.html"], flags=re.S)
        banner = re.search(r'<(\w+)\s[^>]*\bid="%s"[^>]*>' % re.escape(ident), page)
        self.assertIsNotNone(banner, f"index.html has no element #{ident} for the "
                                     f"banner to be built in")
        between = page[page.index("<body"):banner.start()]
        opened = [tag.lower() for tag in re.findall(r"<([a-zA-Z]\w*)[^>]*(?<!/)>", between)
                  if tag.lower() not in {"body", "br", "img", "input", "meta", "link", "hr"}]
        closed = re.findall(r"</([a-zA-Z]\w*)\s*>", between)
        self.assertEqual(len(opened), len(closed),
                         f"#{ident} is not a child of <body> in index.html, so a rule "
                         f"can reach the paragraphs through an element these tests do "
                         f"not know is there")
        named = re.search(r'\bclass="([^"]*)"', banner.group(0))
        node = lambda el, cls=(), key="": {"el": el, "cls": set(cls), "id": key}
        return (node("html"), node("body"),
                node(banner.group(1), (named.group(1).split() if named else []) + [extra],
                     ident),
                node("details", [self.klass("details")]),
                node("p", classes))

    def offset_of(self, body: str, phrase: str, what: str) -> int:
        """Where *phrase* is in *body*, asserted to be there before it is returned.

        Two tests below read one offset against another to check what a reader meets
        first. ``str.find`` answers -1 for something absent, and -1 is less than
        every real offset, so an ordering assertion on a raw ``find`` passes loudest
        when the thing it is ordering does not exist at all.
        """
        at = body.find(phrase)
        self.assertGreater(at, 0, f"{what}: nothing in the source says {phrase!r}")
        return at

    def calls_of(self, function: str) -> list[str]:
        """The argument text of every call to ``function``, parens balanced.

        :func:`jslint.find_calls` finds only single-literal calls, which is the
        right conservatism there and no use here: every one of these calls passes
        an object.

        On the base class because two classes below walk the same thirteen calls to
        ``table()`` -- one asking whether each has a name to be announced by, the
        other whether each can be taken off the page. Two readers finding the call
        sites two ways is two readers that can disagree about how many there are.
        """
        code = self.assets()["app.js"]
        found = []
        needle = rf"(?<![\w.])(?<!function ){re.escape(function)}\("
        for match in re.finditer(needle, code):
            depth, at = 0, match.end() - 1
            for index in range(at, len(code)):
                if code[index] == "(":
                    depth += 1
                elif code[index] == ")":
                    depth -= 1
                    if depth == 0:
                        found.append(code[at + 1:index])
                        break
        self.assertTrue(found, f"app.js calls no {function}(), so this check is "
                               f"not checking anything")
        return found


class TestTheAssetsExist(WebAssetCase):

    def test_every_page_asset_site_py_copies_is_present(self):
        # Checked against site.py's own constants rather than a list repeated
        # here, so the two cannot drift apart and both be self-consistent.
        from matchcentre import site
        for name in (*site.PAGE_ASSETS, site.PICKER, site.CAREERS_PAGE):
            with self.subTest(asset=name):
                path = site.WEB / name
                self.assertTrue(path.is_file(), f"{path} does not exist, and "
                                                f"site.py copies it into every "
                                                f"competition directory")
                self.assertGreater(path.stat().st_size, 0, f"{path} is empty")

    def test_no_page_asset_starts_with_an_underscore(self):
        # GitHub Pages runs Jekyll, which strips underscore-prefixed paths. An
        # asset named _app.js would 404 the moment a club published the site.
        from matchcentre import site
        for name in (*site.PAGE_ASSETS, site.PICKER, site.CAREERS_PAGE,
                     *site.GENERATED):
            with self.subTest(asset=name):
                self.assertFalse(name.startswith("_"))


class TestTheJavaScriptIsWellFormed(WebAssetCase):

    def test_app_js_brackets_balance(self):
        fault = jslint.balance(self.assets()["app.js"])
        self.assertIsNone(fault, f"app.js will not parse, so the dashboard "
                                 f"renders blank: {fault.describe() if fault else ''}")

    def test_no_literal_is_left_unclosed(self):
        for name, source in self.assets().items():
            if not name.endswith((".js", ".html")):
                continue
            for index, code in enumerate([source] if name.endswith(".js")
                                         else inline_scripts(source)):
                with self.subTest(asset=name, script=index):
                    fault = jslint.unterminated(code)
                    self.assertIsNone(fault, fault.describe() if fault else "")

    def test_inline_scripts_in_the_pages_balance(self):
        assets = self.assets()
        for name in PAGES:
            for index, code in enumerate(inline_scripts(assets[name])):
                with self.subTest(page=name, script=index):
                    fault = jslint.balance(code)
                    self.assertIsNone(fault, fault.describe() if fault else "")

    def test_no_template_literals_anywhere(self):
        # Two reasons, and the first is the one that matters to a club: the
        # dashboard should open in whatever browser is on the clubhouse laptop,
        # and ES5 is the safe floor. The second is that a backtick is the one
        # construct jslint cannot follow, so allowing them here would quietly
        # weaken every other check in this file.
        assets = self.assets()
        for name, source in assets.items():
            if name == "styles.css":
                continue
            scripts = [source] if name.endswith(".js") else inline_scripts(source)
            for index, code in enumerate(scripts):
                with self.subTest(asset=name, script=index):
                    self.assertFalse(jslint.uses_template_literals(code))

    def test_the_stylesheet_braces_balance(self):
        # CSS is not JavaScript, but an unclosed brace has the same effect: every
        # rule after it is dropped and the page renders unstyled.
        css = self.assets()["styles.css"]
        depth = 0
        for line_number, line in enumerate(css.splitlines(), start=1):
            stripped = re.sub(r"/\*.*?\*/", "", line)
            depth += stripped.count("{") - stripped.count("}")
            self.assertGreaterEqual(depth, 0, f"styles.css line {line_number}: "
                                              f"'}}' with nothing open")
        self.assertEqual(depth, 0, "styles.css has an unclosed '{'")


class TestTheLinterKnowsARegexFromADivision(unittest.TestCase):
    """The one ambiguity in reading JavaScript without a parser, checked on snippets.

    `/` is division or the start of a regular expression depending on what came
    before it, and jslint decides by looking at the previous character: after `)`, `]`,
    a digit or a letter it divides. A letter is the case that is not so simple, because
    a keyword ends in one. `return /[",\\r\\n]/.test(s)` is valid ES5 that the rule
    read as a division, which put the rest of the line inside a string and reported
    app.js as a file that "will not parse, so the dashboard renders blank" -- the loudest
    message the suite has, on a file Chrome ran without complaint.

    Snippets rather than app.js: the fault is in the reader, and a reader that agrees
    with today's app.js is not the thing being tested. What made this worth fixing
    rather than working around is which way it fails -- a linter crying wolf about a
    blank page invites the next contributor to delete a line that was right.
    """

    def test_a_regex_after_a_keyword_is_not_read_as_a_division(self):
        for snippet in (
                'return /[",\\r\\n]/.test(out) ? 1 : 2;',
                'if (typeof /x"y/ === "object") { x(); }',
                'var a = b in /"/ ? 1 : 2;',
                'switch (s) { case /"/.source: break; }',
                'throw /"/.source;',
                'void /"/;',
                'x = new RegExp(/"/);',
                'delete o[/"/.source];',
                # A comment between the keyword and the slash: the word has to be read
                # off the text with the comment already blanked out, not off the file.
                'return /* why */ /"/.source;'):
            with self.subTest(snippet=snippet):
                self.assertIsNone(
                    jslint.unterminated(snippet + "\n"),
                    f"{snippet!r} is valid ES5, and the reader took the regex for a "
                    f"division: everything after the quote inside it counts as a "
                    f"string, so the file is reported as one that will not parse")

    def test_a_division_after_a_value_is_still_a_division(self):
        # The other half of the same rule, because a fix that called every `/` a regex
        # would swallow the rest of the line from `a / b` onwards and report nothing
        # wrong with a file that is broken further down.
        for snippet, why in (
                ("var r = total / played;", "a name divided by a name"),
                ("var r = (a + b) / 2;", "a bracket divided by a number"),
                ("var r = list[0] / 2;", "an element divided by a number"),
                ("var r = 8 / 2, s = 'ok';", "a number divided by a number"),
                ("var r = returned / played;",
                 "a name that merely starts with a keyword"),
                # Two on one line is what makes the difference visible: a lone division
                # with no second slash after it falls through as one either way.
                ("var r = a / b / c, s = 'ok';", "two divisions on one line")):
            with self.subTest(snippet=snippet):
                blanked = jslint.code_only(snippet + "\n")
                self.assertIn("/", blanked,
                              f"{why}: the division was read as the start of a "
                              f"regular expression and blanked out with it")

    def test_the_keyword_is_the_whole_word_before_the_slash(self):
        # `noreturn / 2` ends in the letters of a keyword without being one. Read by
        # the last letter alone, or by any suffix match, this is a regex and the rest
        # of the file goes with it. Two slashes on the line are what make the misread
        # visible, the same reason the division test below carries a pair: a lone
        # division with no second slash after it falls through as one either way,
        # because the scanner looks for a closing slash and does not find it.
        for snippet, what in [("var r = noreturn / 2;\n", "one division"),
                              ("var r = noreturn / 2 / 3;\n", "two divisions")]:
            with self.subTest(what=what):
                blanked = jslint.code_only(snippet)
                self.assertIn("/", blanked,
                              "a name ending in the letters of a keyword is not a "
                              "keyword, and what follows it is a division")


class TestASentenceInACommentIsNotOneThePageSays(unittest.TestCase):
    """``jslint.without_comments()``, which ``guard_for()`` finds a sentence in.

    ``guard_for()`` reads a sentence where the page can say it, and the condition it
    is under where the script can test it. A comment in the same words is a note to
    whoever reads the code. Before item 4.21, five sentences were each commented out,
    block and all, and one guard was written in a comment beside the push it named,
    and every one of the six still passed the readers it replaced. So did a guard's
    term written in a comment inside its parentheses, which those readers took for
    part of the condition. The text it searches keeps every string and blanks every
    comment in place, so an offset found there is also an offset into
    ``code_only()``'s text.

    Tested on snippets rather than app.js, for the reason the class above gives. There
    is a second reason too. Today's app.js has no guarded sentence inside a comment,
    so measured against it, a ``without_comments()`` that left any one kind of
    comment in place passed every other test in this file.
    """

    def test_a_comment_is_blanked_and_a_string_is_kept_where_it_was(self):
        for kind, snippet in (
                ("a line comment", "say('kept');  // dropped\n"),
                ("a block comment", "say('kept');  /* dropped */  say(1);\n"),
                ("a block comment that never closes", "say('kept');  /* dropped\n")):
            with self.subTest(kind=kind):
                text = jslint.without_comments(snippet)
                self.assertNotIn("dropped", text,
                                 f"the words in {kind} are still there to be found, "
                                 f"so a sentence nobody is shown reads as printed")
                self.assertEqual(text.find("kept"), snippet.find("kept"),
                                 "the string was blanked with the comments, or moved, "
                                 "so a sentence the page does say cannot be found")


class TestEachValueWeWorkedOutIsExplainedAsWhatTheCodeMakesOfIt(WebAssetCase):
    """4.464 and 4.462: the page's sentences for the captains and the season.

    The captains sentence said "The captain was worked out from a roster mark
    rather than declared", calling a (C) mark a guess, though a team's captain
    comes only from the card's mark or the club's declaration: the one value of
    captains the page flags is "mixed", some declared and the rest marked. The
    season sentence said "No season label was declared, so this one was worked
    out from the cards" of a name no card holds, which is its folder's name made
    readable, or in multi-season mode for cards that sit in no season folder, the
    year on them. Read from app.js (``PROVENANCE_ADVICE``, measured in headless
    Chrome as the flag's title in /tmp/mcf/scratch/STUB/out-before-*.txt). Now
    each says what a value so flagged is, the season's as
    ``meta.season_label_from`` says it was made. Rejected: deleting the captains
    sentence and its place in ``PROVENANCE_ORDER``, which hides a flag Python
    sends; "No card that counts marks this captain with a (C), and nobody declared
    one", false of every captain so flagged (/tmp/mcf/scratch/STUB/fix/before.txt);
    and a year called its folder's name, beside the run's note that its cards sit
    in no season folder.
    """

    @staticmethod
    def joined(literal: str) -> str:
        """The text of a JavaScript string literal, or of several joined by +."""
        return "".join(piece.replace("\\'", "'") for piece in
                       re.findall(r"'((?:[^'\\]|\\.)*)'", literal))

    def advice(self) -> dict[str, str]:
        source = self.assets()["app.js"]
        block = re.search(r"\nvar PROVENANCE_ADVICE = \{(.*?)\n\};", source, re.S)
        self.assertIsNotNone(block, "app.js defines no PROVENANCE_ADVICE")
        return {key: self.joined(value) for key, value in re.findall(
            r"\n  (\w+): ((?:'(?:[^'\\]|\\.)*'[\s+]*)+)", block.group(1))}

    def test_the_captains_sentence_says_some_were_declared_and_the_rest_marked(self):
        said = self.advice()["captains"]
        self.assertEqual(said, "Some captains here were declared and the rest were "
                               "read from the (C) their cards print. Set captains "
                               "under [competitions.\"{id}\"] in config.toml to "
                               "declare every one.")
        self.assertNotIn("roster mark", said)
        self.assertNotIn("worked out", said)

    def test_the_season_sentence_says_the_name_is_its_folders(self):
        self.assertEqual(self.advice()["season"],
                         "No card printed this season's name and nobody declared "
                         "one, so this is its folder's name made readable. Name the "
                         "season under [seasons] in config.toml, or set season "
                         "under [competitions.\"{id}\"].")

    def test_a_year_on_cards_in_no_season_folder_is_said_to_be_that_year(self):
        source = self.assets()["app.js"]
        found = re.search(r"\nvar SEASON_FROM_DATE_ADVICE = ((?:'(?:[^'\\]|\\.)*'"
                          r"[\s+]*)+);", source)
        self.assertIsNotNone(found, "app.js defines no SEASON_FROM_DATE_ADVICE")
        self.assertEqual(self.joined(found.group(1)),
                         "No card printed this season's name and nobody declared "
                         "one, so this is the year on its cards, which sit in no "
                         "season folder. Name the season under [seasons] in "
                         "config.toml, or set season under [competitions.\"{id}\"].")
        # Taken for the season where the document says its name is that year.
        body = jslint.without_comments(self.body("provenanceAdvice"))
        self.assertRegex(body, r"if \(aspect === 'season' && "
                               r"meta\(\)\.season_label_from === 'date'\) \{\s*"
                               r"text = SEASON_FROM_DATE_ADVICE;\s*\}")

    def test_every_aspect_the_page_lists_has_a_sentence(self):
        source = self.assets()["app.js"]
        order = re.search(r"var PROVENANCE_ORDER = \[([^\]]*)\]", source)
        self.assertIsNotNone(order)
        self.assertEqual(sorted(self.advice()),
                         sorted(re.findall(r"'(\w+)'", order.group(1))))

    def test_the_page_flags_a_value_worked_out_and_never_an_empty_one(self):
        from matchcentre import export
        flagged = re.findall(r"v === '(\w+)'", jslint.without_comments(
            self.body("isDerived")))
        self.assertEqual(sorted(flagged), ["derived", "mixed"])
        self.assertNotIn(export.EMPTY, flagged)


class TestTheDomContractHolds(WebAssetCase):

    def test_every_agreed_hook_is_in_both_the_script_and_the_page(self):
        # The second half asks for the hook in the spelling its `kind` promises,
        # not merely for the name somewhere in the two files. Searching the
        # concatenation let one kind stand in for the other, which is not a
        # hypothetical: with `#health` deleted from index.html this test passed,
        # because `.health:empty` in the stylesheet still contains the word. The
        # two spellings are two different promises. An `id` is a slot the shell
        # reserves and app.js fills, so it has to be in the markup; a `class` is a
        # marker app.js puts on an element it creates, so it is never in the markup
        # and what must exist is the rule that makes it visible.
        assets = self.assets()
        app = assets["app.js"]
        for hook, kind in DOM_CONTRACT:
            with self.subTest(hook=hook, kind=kind):
                self.assertIn(hook, app, f"app.js never mentions {hook!r}, so "
                                         f"whatever it was to carry is silently "
                                         f"absent from the page")
                # Searched rather than asserted with assertRegex, which prints the
                # whole file it did not match and buries the sentence that says
                # what is wrong under six kilobytes of markup.
                if kind == "id":
                    found = re.search(rf'\bid\s*=\s*["\']{re.escape(hook)}["\']',
                                      assets["index.html"])
                    self.assertTrue(found, f"index.html reserves no id={hook!r}, "
                                           f"so app.js will look for it and find "
                                           f"nothing")
                else:
                    found = re.search(rf"\.{re.escape(hook)}\b", assets["styles.css"])
                    self.assertTrue(found, f"styles.css has no rule for .{hook}, so "
                                           f"app.js will mark elements with a class "
                                           f"that renders as ordinary text")

    def test_every_id_the_script_asks_for_exists_in_the_page(self):
        assets = self.assets()
        ids_in_page = set(re.findall(r'\bid\s*=\s*["\']([^"\']+)["\']',
                                     assets["index.html"]))
        wanted = jslint.find_calls(assets["app.js"], "document.getElementById")
        self.assertTrue(wanted, "app.js asks for no element by literal id, which "
                                "means this check is not checking anything")
        missing = sorted(set(wanted) - ids_in_page)
        self.assertEqual(missing, [], f"app.js calls getElementById for id(s) "
                                      f"index.html does not define: {missing}. "
                                      f"Each returns null and its update is "
                                      f"skipped in silence.")

    def test_the_picker_can_receive_its_competition_list(self):
        from matchcentre import site
        template = self.assets()["picker.html"]
        self.assertTrue("<!--COMPETITIONS-->" in template
                        or site.PICKER_DATA in template,
                        f"picker.html offers nowhere to put the competition "
                        f"list; it needs <!--COMPETITIONS--> or a reference to "
                        f"{site.PICKER_DATA}, or it renders with no links")

    def test_the_attribute_site_py_writes_a_season_on_is_one_the_picker_reads(self):
        """The one card whose season is not in its own link, checked from both ends.

        A card with a dashboard behind it carries its season in its href, which the
        picker's script parses. A declared competition nobody has played has no
        href, so ``site.py`` writes ``data-season`` on it instead and the script
        reads that -- and there is no JavaScript engine here to catch the two
        drifting apart. Either half alone fails silently: the attribute with no
        reader hides the card from every season view, the reader with no attribute
        finds nothing. So the pair is asserted, not the halves.
        """
        picker = self.assets()["picker.html"]
        source = (Path(__file__).resolve().parents[1] / "matchcentre"
                  / "site.py").read_text(encoding="utf-8")
        card = source[source.index("def _card_html("):source.index(
            "def _footer_html(")]
        self.assertIn('data-season="', card,
                      "_card_html writes no data-season on a card, so "
                      "picker.html's fallback reads nothing and a declared "
                      "competition shows in every season at once")
        # Read out of the one function that answers the question, not the file: the
        # season buttons carry data-season too, and a whole-file search would be
        # satisfied by those and pass with the card's reader deleted.
        at = picker.index("function seasonOfCard(")
        reader = picker[at:picker.index("\n  }", at)]
        self.assertIn("getAttribute('data-season')", reader,
                      "seasonOfCard never reads data-season, so the season "
                      "site.py writes on a card with no link is ignored and the "
                      "card is shown in every season view")

    def test_every_sentinel_the_picker_uses_is_one_site_py_substitutes(self):
        # A sentinel comment that site.py does not know about is never replaced,
        # so it survives into the published page as a visible artefact.
        from matchcentre import site
        template = self.assets()["picker.html"]
        used = set(re.findall(r"<!--[A-Z][A-Z-]*-->", template))
        unknown = sorted(used - set(site.PICKER_SENTINELS))
        self.assertEqual(unknown, [], f"picker.html contains sentinel(s) site.py "
                                      f"does not substitute: {unknown}")

    def test_every_sentinel_the_careers_page_uses_is_one_site_py_substitutes(self):
        # The same failure on the careers page: a sentinel _write_careers does
        # not know is printed as a comment in every multi-season site.
        from matchcentre import site
        template = self.assets()["careers.html"]
        used = set(re.findall(r"<!--[A-Z][A-Z-]*-->", template))
        self.assertEqual(sorted(used - set(site.CAREERS_SENTINELS)), [])


class TestTheDataIslandIsWiredUp(WebAssetCase):

    def test_the_page_loads_the_data_before_the_script_that_reads_it(self):
        # Order is the whole point: app.js reads window.SEASON_DATA at load, and
        # a page that loads app.js first gets undefined and renders nothing.
        loaded = script_sources(self.assets()["index.html"])
        self.assertIn("season-data.js", loaded, "index.html never loads "
                                               "season-data.js, so the page has "
                                               "no data to draw")
        self.assertIn("app.js", loaded, "index.html never loads app.js")
        self.assertLess(loaded.index("season-data.js"), loaded.index("app.js"),
                        f"index.html loads app.js before season-data.js "
                        f"({loaded}); window.SEASON_DATA would be undefined when "
                        f"it is read")

    def test_neither_script_is_deferred_out_of_that_order(self):
        # `defer` on one tag and not the other reorders them however the browser
        # likes, which reintroduces exactly the bug the ordering test rules out.
        index = self.assets()["index.html"]
        for match in re.finditer(r"<script([^>]*)>", index, re.IGNORECASE):
            attrs = match.group(1)
            if "src=" not in attrs.lower():
                continue
            with self.subTest(tag=attrs.strip()):
                self.assertNotIn("defer", attrs.lower())
                self.assertNotIn("async", attrs.lower())

    def test_the_script_reads_the_global_the_writer_writes(self):
        app = self.assets()["app.js"]
        self.assertIn("SEASON_DATA", app,
                      "app.js does not read window.SEASON_DATA, which is the "
                      "only way data reaches a page opened by double-click: a "
                      "browser refuses to fetch() a sibling file over file://")

    def test_the_page_does_not_depend_on_fetch_alone(self):
        # If fetch('season.json') is present it may only be a fallback for a
        # club that serves the folder over http. As the sole source it breaks
        # the double-click case, which is the case that matters.
        app = self.assets()["app.js"]
        if "fetch(" in app:
            self.assertIn("SEASON_DATA", app)


class TestThePageDoesNotDownloadTheSeasonItAlreadyHas(WebAssetCase):
    """The island is a blocking script, so by the time app.js runs, the data is in.

    ``season-data.js`` is a ``<script>`` at the end of the body and nothing on the
    page runs until it has been read, which means ``window.SEASON_DATA`` holds the
    whole season before the first line of app.js executes. The loader then fetched
    ``season.json`` -- the same document, written out a second time -- and preferred
    it. Measured in Chrome over http on an eight-team home-and-away season
    (``/tmp/payload.py``): 415KB of island, 414KB of JSON, so 829KB of a 1,033KB
    page went on 415KB of season, and 40% of everything downloaded was a copy of
    something already in memory.

    The fetch stays. A folder can be published with ``season.json`` and no island
    (a pipeline that writes the data where the shell is already served), and a
    missing island has to end at the page that explains itself rather than a blank
    screen. It is only no longer the first thing tried.

    Which copy was read stays visible either way: the footer prints it, because a
    reader who hand-edits ``season.json`` and sees nothing change deserves to find
    out why from the page rather than from the source.
    """

    def loader(self) -> str:
        """The loader's body, comments and string contents blanked out.

        Both names this test looks for are identifiers, and both appear in the
        comment above the function as prose; a whole-body search would find the
        prose and pass on a file where the code says the opposite.
        """
        return jslint.code_only(self.body("loadSeason"))

    def test_the_copy_the_page_already_holds_is_read_before_anything_is_requested(self):
        source = self.loader()
        island, fetched = source.find("SEASON_DATA"), source.find("fetchJson(")
        self.assertGreater(island, -1, "loadSeason() never looks at "
                                       "window.SEASON_DATA, so the page ignores "
                                       "the season the <script> tag already gave "
                                       "it")
        self.assertGreater(fetched, -1, "loadSeason() no longer fetches anything, "
                                        "so this ordering check means nothing")
        self.assertLess(island, fetched,
                        "loadSeason() requests season.json before looking at the "
                        "copy the page already holds, so every dashboard served "
                        "over http downloads its season twice -- 829KB for a "
                        "415KB season, measured on an eight-team home-and-away "
                        "one")

    def test_the_island_ends_the_load_instead_of_racing_the_request(self):
        # Textual order is not enough on its own: a check that falls through to the
        # fetch anyway costs the same bytes as doing it the other way round.
        source = self.loader()
        island, fetched = source.find("SEASON_DATA"), source.find("fetchJson(")
        self.assertIn("return", source[island:fetched],
                      f"nothing returns between the island check and the fetch, so "
                      f"the request goes out even when the season is already in "
                      f"memory: {source[island:fetched]!r}")

    def test_the_early_answer_is_still_a_promise(self):
        # boot() does loadSeason().then(...). An arm that hands back the season
        # itself rather than a promise of it is a TypeError on the next line and a
        # blank page -- and it is the arm that now runs on every normal load.
        source = self.loader()
        arm = source[source.find("SEASON_DATA"):source.find("fetchJson(")]
        self.assertIn("Promise.resolve(", arm,
                      f"the island arm of loadSeason() does not answer with a "
                      f"promise, so boot() calls .then() on a plain object: {arm!r}")

    def test_a_folder_published_without_an_island_is_still_served_by_a_request(self):
        # Raw source, not code_only: the file name is a string literal, which is
        # exactly what code_only blanks.
        source = self.body("loadSeason")
        self.assertIn("fetchJson('season.json')", source,
                      "loadSeason() no longer fetches season.json, so a folder "
                      "published with the data file and no island -- a pipeline "
                      "that writes season.json where the shell is already served "
                      "-- renders nothing")

    def test_the_footer_names_whichever_copy_was_actually_read(self):
        # Raw source again: these are the strings the footer prints. Split at the
        # fetch, so each arm is asked about its own note rather than the pair being
        # asked whether both strings exist somewhere -- two notes the wrong way
        # round is a page that reports a file it did not open.
        source = self.body("loadSeason")
        cut = source.find("fetchJson(")
        self.assertGreater(cut, 0, "loadSeason() no longer fetches anything, so "
                                   "there are no two arms to tell apart")
        held, fetched = source[:cut], source[cut:]
        self.assertIn("source: season-data.js", held,
                      "the arm that reads the copy the page already holds does "
                      "not name it, so the footer cannot say which file the "
                      "numbers came from")
        self.assertNotIn("source: season.json", held,
                         "the arm that reads the island reports season.json, so "
                         "the footer names a file the page never opened")
        self.assertIn("source: season.json", fetched,
                      "the fetched arm does not name season.json")


class TestNavigationNamesPagesRatherThanFolders(WebAssetCase):
    """No link may stop at a directory, because nothing resolves it to a page.

    ``build.py`` tells a club that double-clicking the dashboard works and that no
    web server is needed. A server turns a link to ``foo/`` into ``foo/index.html``
    by itself; opening the file directly, there is nobody to do that, and the
    browser shows a listing of the folder. Both directions of navigation were
    written that way and so both showed a file listing instead of a page.

    ``tests/test_cli.py`` catches this end to end for the links that are in the
    markup, by resolving every one against the disk. It cannot catch the ones
    ``app.js`` builds at run time, because this suite runs no JavaScript -- the
    project has no dependencies, a browser included -- so those are read as source
    instead, the same way ``tests/jslint.py`` checks the rest of the file.
    """

    def test_the_root_link_the_script_builds_names_a_page(self):
        app = self.assets()["app.js"]
        body = re.search(r"function rootHref\(\)\s*\{(.*?)\n\}", app, re.S)
        self.assertIsNotNone(body, "app.js no longer defines rootHref(), which is "
                                   "what every 'all competitions' link is set from")
        returned = re.findall(r"'((?:\.\./)+[^']*)'", body.group(1))
        self.assertTrue(returned, f"rootHref() returns no relative path: "
                                  f"{body.group(1)!r}")
        for path in returned:
            with self.subTest(returns=path):
                self.assertTrue(
                    path.endswith(".html"),
                    f"rootHref() returns {path!r}, a directory. Opened as a file "
                    f"that shows a folder listing rather than the front page; "
                    f"name the page, as in {path + 'index.html'!r}")

    def test_no_link_in_either_page_stops_at_a_directory(self):
        for name in PAGES:
            page = self.assets()[name]
            for target in re.findall(r'href="([^"]*)"', page):
                with self.subTest(page=name, href=target):
                    if target.startswith("#") or ":" in target:
                        continue        # a fragment, or an absolute URL
                    self.assertFalse(
                        target.endswith("/"),
                        f"{name} links to {target!r}, a directory, so a browser "
                        f"opening the file shows a folder listing instead of the "
                        f"page it meant to go to")

    def test_a_deeper_competition_gets_a_link_that_reaches_the_front_page(self):
        # site.py rewrites this link on the way out, because a multi-season site
        # nests one level further than the template assumes. Asserted here at the
        # string level; test_cli.py resolves the built result against the disk.
        from matchcentre import site
        page = self.assets()["index.html"]
        self.assertIn(site.HOME_LINK, page,
                      f"index.html no longer carries {site.HOME_LINK!r} verbatim, "
                      f"so site.py cannot aim it at the front page")


class TestAPageThatIsAllThereIsSendsNobodyAnywhere(WebAssetCase):
    """``build.py --single-file`` writes a dashboard with no site around it.

    The whole page -- stylesheet, script, season -- folded into one file, named after
    the competition, to attach to an email or open on a phone. ``site.py`` takes the
    ``← All competitions`` link out of the markup on the way in, and the file says so
    for itself by setting ``window.SINGLE_FILE`` above the script.

    Two things in ``app.js`` read that flag, and both are about not sending a reader
    somewhere that is not there. The season picker builds its own way out at run time
    -- an option in the ``<select>``, or an ``All seasons →`` link beside the label --
    which the markup edit cannot reach; and the footer names the file the figures were
    read from, which on a folded page is the file the reader is holding rather than a
    ``season-data.js`` nobody can open.

    Read as source, for the reason :class:`TestNavigationNamesPagesRatherThanFolders`
    gives: this suite runs no JavaScript, because the project has no dependencies and
    a browser is a dependency. ``tests/test_cli.py`` folds a real page and reads every
    ``src`` and ``href`` out of it, which catches the markup half end to end; what it
    cannot catch is a link that only exists once the script has run.
    """

    # Both ways out the picker builds, by the words a reader would click.
    WAYS_OUT = {"the last option of the select": "All seasons and competitions",
                "the link beside the season label": "All seasons →"}

    def test_every_way_out_the_script_builds_is_behind_the_flag(self):
        for what, phrase in self.WAYS_OUT.items():
            with self.subTest(what=what):
                guard = self.guard_for("fillSeasonPicker", phrase)
                self.assertIn("isSingleFile(", guard,
                              f"{phrase!r} is built whatever the page is, so a "
                              f"folded file offers a reader a front page that is "
                              f"not there")
                self.assertTrue(guard.startswith("!"),
                                f"{phrase!r} is built only *for* a folded file, "
                                f"which is the one page it cannot reach: {guard!r}")

    def test_the_footer_names_the_file_the_reader_is_actually_holding(self):
        """Which copy was read, told the way the reader can check.

        The raw body and not ``jslint.code_only()``: both notes are string literals,
        which is exactly what ``code_only`` blanks. The arms are asserted in order,
        because a swap is the one version of this bug that shows a name on both pages
        and the wrong one on each -- the folder's dashboard citing a file that is not
        in it, and the folded page citing a file that does not exist at all.
        """
        self.assertIn("note: isSingleFile() ? 'source: this file' "
                      ": 'source: season-data.js'",
                      " ".join(self.body("loadSeason").split()))

    def test_the_flag_is_the_page_saying_so_and_is_read_in_one_place(self):
        """``window.SINGLE_FILE === true``, once, in a function with a name.

        Strictly, and against ``true``: the two readers above ask it the other way
        round, so a sense error here is a link out of the one file that cannot
        follow it and no link on every page that can.

        Once, because the flag is a fact about the file and this is the project's own
        rule about a rule -- a second reader is a second answer, and the two part
        company the moment one of them is given a way of spelling it the other has
        not heard of.
        """
        code = jslint.code_only(self.assets()["app.js"])
        self.assertIn("window.SINGLE_FILE === true",
                      " ".join(jslint.code_only(self.body("isSingleFile")).split()))
        self.assertEqual(code.count("SINGLE_FILE"), 1,
                         "app.js reads window.SINGLE_FILE somewhere other than "
                         "isSingleFile(), so there are two answers to whether this "
                         "page is the whole dashboard")


class TestNoClubSpecificsInThePages(WebAssetCase):

    # The theme-related literals a generic page is allowed to contain:
    #
    #   "-theme"             the suffix, concatenated onto the club's own slug
    #   "matchcentre-theme"  the fallback for a page with no slug to build from
    #   "data-theme"         not a storage key at all: the HTML attribute the CSS
    #                        selects on, `<html data-theme="dark">`. It names
    #                        nobody and is shared by every club by design.
    #
    # Anything else is a key that names somebody. A single fixed key means two
    # clubs' dashboards served from one origin overwrite each other's light/dark
    # choice, which is a bug nobody reports because nobody can describe it.
    ALLOWED_THEME_KEYS = {"-theme", "matchcentre-theme", "data-theme"}

    def test_the_theme_key_is_derived_rather_than_named(self):
        assets = self.assets()
        for name in ("app.js", *PAGES):
            with self.subTest(asset=name):
                keys = set(re.findall(r'["\']([a-z0-9-]*-theme)["\']',
                                      assets[name]))
                self.assertEqual(sorted(keys - self.ALLOWED_THEME_KEYS), [],
                                 f"{name} hardcodes a localStorage theme key; it "
                                 f"should be built from the club's own slug")

    def test_something_actually_builds_the_key_from_the_data(self):
        # The check above passes on a page that has no theme handling at all, so
        # it needs a partner: the suffix must really be concatenated somewhere.
        app = self.assets()["app.js"]
        self.assertRegex(app, r"\+\s*'-theme'|\+\s*\"-theme\"",
                         "no code joins a slug to '-theme', so every club shares "
                         "one localStorage key again")

    def test_no_page_hardcodes_one_clubs_match_format(self):
        # D1/S1/S2/D2 was one club's four-set shape. A page that assumes it shows
        # empty columns for a club playing three sets, or drops a fifth.
        assets = self.assets()
        for name, source in assets.items():
            with self.subTest(asset=name):
                squashed = re.sub(r"[\s'\"]", "", source).upper()
                self.assertNotIn("D1,S1,S2,D2", squashed)

    def test_the_pages_name_no_club_and_no_division(self):
        # Every visible string comes from the config or the season document. A
        # heading that says "Division C" is one club's dashboard, not a tool.
        assets = self.assets()
        for name in PAGES:
            text = assets[name]
            for pattern in (r"\bDivision\s+[A-Z]\b", r"\bTuesday\s+Division\b"):
                with self.subTest(page=name, pattern=pattern):
                    self.assertEqual(re.findall(pattern, text), [])


class TestThePageCountsTheSameMatchesThePythonDoes(WebAssetCase):
    """``Match.counts_as_played``, answered once in Python and read by the page.

    The season document carries computed aggregates, and the page computes a few of
    its own on top of them: the overview tiles, and each player's per-set trail. If
    the page's idea of a match that happened differs from the exporter's, the tile
    saying "10 matches played" sits directly above a ladder whose P column adds to
    eight -- and nothing errors, because both numbers are internally consistent.

    The page used to answer it for itself, from its own list of the statuses that
    count for nothing, held in step by a test that ran the Python and then asserted
    the Python's list was three long. So a status added had two places to go, and
    the first thing it met was that assertion: a copy of the tree that made
    ``counts_as_played`` also leave out a disputed card failed ``4 != 3 : the Python
    side has changed shape``, and so did the copy that made the same edit to the page
    as well. Now export.py writes ``counts_as_played`` on every match and
    ``countsAsPlayed`` returns it, so a status added goes into model.py and nowhere
    else, and the page names no status at all.

    What the page does with a document built before the field was the decision.
    Measured by building each way a site is written with the old tree and then the
    new (/tmp/t4/scratch/y452/pairs.py): one season, one season folded into one
    file, three competitions over two seasons, a season taken away between builds,
    ``--check`` over an old site, and going back to the old tree. The page and its
    document came out of the same build every time but one: a build whose write of
    ``season.json`` or ``season-data.js`` failed, which exits 2 saying the site is
    still there and out of date, leaves the new page beside the old document. So the
    field is a new document format, and that page says to rebuild. Falling back to
    the status list when the field is missing was the alternative, and it keeps the
    copy this class exists to remove, to serve a case only a failed write produces.
    """

    # The format every season.json written before matches carried the field says.
    FORMAT_BEFORE_THE_FIELD = 1

    def excluded_statuses(self) -> set[str]:
        from matchcentre import format as fmt_mod, scoring
        from matchcentre.model import Match, MatchStatus
        comp = dict(competition="x", round="1", home="a", away="b",
                    fmt=fmt_mod.match_format("doubles-singles-doubles"),
                    rules=scoring.ruleset("games-bonus"))
        return {s.value for s in MatchStatus
                if not Match(status=s, **comp).counts_as_played}

    def season_of_every_status(self):
        """(matches, the season document built from them), one card per status.

        Every status a card can arrive with, taken from the enum rather than listed
        here, so the next one is in this season without anybody adding it. A bye is
        the exception, and not by choice: the exporter's match list is the cards, and
        a bye is a round's ``byes`` entry that no card was read for. The forfeit among
        them has a side ticked, so it decides the match. Two more rows are the cases
        the rule is weaker than ``decided`` about: a card two rubbers short, which
        decides nothing and was played, and a forfeit nobody ticked a side of.
        """
        from matchcentre import export, format as fmt_mod, scoring
        from matchcentre.competition import Club, Competition
        from matchcentre.model import Match, MatchStatus, SetResult
        comp = Competition(id="x", day="Monday", division="C",
                           fmt=fmt_mod.match_format("doubles-singles-doubles"),
                           rules=scoring.ruleset("games-bonus"))

        def card(status, n, rows=((6, 2), (6, 3), (6, 1)), **kw):
            sets = tuple(SetResult(slot=comp.fmt.slots[i], home_games=h, away_games=a,
                                   rule=comp.fmt.set_rule)
                         for i, (h, a) in enumerate(rows))
            return Match(competition="x", round=str(n), home="a", away="b",
                         fmt=comp.fmt, rules=comp.rules, sets=sets, status=status,
                         match_id=f"id{n}", **kw)

        statuses = [s for s in MatchStatus if s is not MatchStatus.BYE]
        ticked = {MatchStatus.FORFEIT: {"forfeited_by": "away"}}
        matches = [card(s, n, **ticked.get(s, {})) for n, s in enumerate(statuses, 1)]
        matches.append(card(MatchStatus.PLAYED, len(matches) + 1, rows=((6, 2),)))
        matches.append(card(MatchStatus.FORFEIT, len(matches) + 1, rows=()))
        doc = export.build_season(
            matches, competition=comp, source_dir=Path("."), generated="",
            club=Club(name="", competitions=(comp,)), cards=[], rejected=[])
        return matches, doc

    def test_the_page_has_the_predicate_at_all(self):
        self.assertIn("function countsAsPlayed(",
                      jslint.code_only(self.assets()["app.js"]))

    def test_it_reads_the_documents_answer_and_nothing_else(self):
        # The field is read in code, with the comments blanked, so a comment naming
        # it does not stand in for the read. Nothing else is read off the match: a
        # second field beside the answer is a second rule beside the rule, and
        # `!match.not_totalled` folded in would tell the page that each copy of a
        # night exported twice counted for nothing, the mistake the pipeline's
        # `test_and_both_rows_say_the_night_was_played` pins in the Python. The
        # statuses are looked for with the strings intact, because they *are* string
        # literals, in either quote, and with the comments removed, because body()
        # runs on into the next function's. Any status at all, counted or not -- a
        # list of the ones that do count is the same copy written the other way round.
        from matchcentre.model import MatchStatus
        body = jslint.without_comments(self.body("countsAsPlayed"))
        code = jslint.code_only(body)
        self.assertRegex(code, r"\.counts_as_played\b",
                         "countsAsPlayed() does not read the document's answer")
        self.assertEqual(set(re.findall(r"\.\s*([A-Za-z_$][\w$]*)", code)),
                         {"counts_as_played"},
                         "countsAsPlayed() asks the match something besides its answer")
        self.assertNotIn("[", code, "countsAsPlayed() reads the match by subscript")
        for status in MatchStatus:
            with self.subTest(status=status.value):
                self.assertIsNone(re.search(r"(['\"])" + status.value + r"\1", body),
                                  f"countsAsPlayed() still decides a {status.value} "
                                  f"match by its status")

    def test_a_match_the_document_says_nothing_about_is_not_counted(self):
        # Only a document from before the field has a match without it, and that
        # page is already under the schema-version warning. Counted, its washouts'
        # half-scored rubbers would go into tiles that look right; left out, the
        # tiles read nothing under a note saying to rebuild. Strictly `true`, which
        # is also why the Python side is held to writing a bool and not a 1.
        self.assertRegex(jslint.code_only(self.body("countsAsPlayed")),
                         r"\.counts_as_played\s*===\s*true\b"
                         r"|\btrue\s*===\s*\w+(?:\.\w+)*\.counts_as_played\b",
                         "countsAsPlayed() counts a match the document is silent on")

    def test_the_document_carries_the_rules_answer_on_every_match(self):
        # Asked of the rule itself rather than written out as expected values, and
        # compared with assertIs: the page asks `=== true`, so a 1 would count nothing.
        matches, doc = self.season_of_every_status()
        rows = {row["id"]: row for row in doc["matches"]}
        self.assertEqual(len(rows), len(matches), "a card never reached the document")
        for match in matches:
            with self.subTest(id=match.match_id, status=match.status.value):
                self.assertIs(rows[match.match_id].get("counts_as_played"),
                              match.counts_as_played)
        answers = {row.get("counts_as_played") for row in rows.values()}
        self.assertEqual(answers, {True, False},
                         "this season does not exercise both answers")

    def test_and_the_answer_moves_when_the_rule_does(self):
        # The field is the rule's answer and not a second list in export.py that
        # happens to agree with it today. Change what the rule leaves out and every
        # row has to follow, with nothing else edited: this is the status added.
        from unittest import mock
        from matchcentre.model import Match, MatchStatus
        idle = (MatchStatus.BYE, MatchStatus.CANCELLED, MatchStatus.UNPLAYED,
                MatchStatus.DISPUTED)
        with mock.patch.object(Match, "counts_as_played",
                               property(lambda m: m.status not in idle)):
            _, doc = self.season_of_every_status()
        for row in doc["matches"]:
            with self.subTest(id=row["id"], status=row["status"]):
                self.assertIs(row.get("counts_as_played"),
                              row["status"] not in {s.value for s in idle})

    def test_a_document_built_before_the_field_is_one_the_page_says_to_rebuild(self):
        # The page can tell an older document only by its number, so both sides have
        # to have moved past the one those documents carry: the exporter alone and a
        # current site warns about itself, the page alone and the older document
        # renders silently with every match counted as nothing.
        from matchcentre import export
        declared = TestThePageAndTheExporterAgreeOnTheDocumentFormat.declared(self)
        with self.subTest(side="app.js"):
            self.assertGreater(declared, self.FORMAT_BEFORE_THE_FIELD,
                               "the page reads a document without counts_as_played "
                               "as current")
        with self.subTest(side="export.py"):
            self.assertGreater(export.SEASON_FORMAT, self.FORMAT_BEFORE_THE_FIELD,
                               "a document with counts_as_played says the format of "
                               "one without it")

    def test_the_overview_tiles_go_through_it(self):
        # Matches played, sets completed and games played are all derived from this
        # one filter, and it is the tile row a club reads first.
        self.assertRegex(self.body("renderOverview"), r"countsAsPlayed\b")

    def test_a_players_own_set_trail_goes_through_it_too(self):
        # The trail is where a member checks their own season. A cancelled night
        # appearing on it is a rubber they are told they played and did not.
        self.assertRegex(self.body("setsOfPlayer"), r"countsAsPlayed\s*\(")

    def test_nothing_tests_a_match_status_by_hand_any_more(self):
        # Two hand-written filters drift; one shared answer cannot. The predicate
        # itself compares against no status now, so the whole file is searched, bye
        # included, with the comments removed and the strings kept, for a status the
        # rule leaves out used as a test: on either side of a comparison, in either
        # quote, as a `case`, or as one of a list, which is how `indexOf` asks. The
        # old search wanted `status` right before the operator and a single quote
        # after it, and a Yoda comparison, an indexOf list and a double-quoted `==`
        # all got past it. Not every quoted status, though: `'disputed'` is the chip's
        # own word on the card, so a rule that came to leave disputed cards out would
        # fail on the page's wording and have a second place to go again. Searched
        # rather than assertNotRegex'd, which would print all of app.js.
        code = jslint.without_comments(self.assets()["app.js"])
        for status in sorted(self.excluded_statuses()):
            word = r"(['\"])" + status + r"\1"
            for how, pattern in (("compared", r"[!=]==?\s*" + word),
                                 ("compared the other way round", word + r"\s*[!=]==?"),
                                 ("switched on", r"\bcase\s+" + word),
                                 ("listed", r"\[[^\[\]]*" + word + r"[^\[\]]*\]")):
                with self.subTest(status=status, how=how):
                    found = re.search(pattern, code)
                    self.assertIsNone(
                        found and found.group(0),
                        f"a hand-written {status} test, {how}, instead of "
                        f"countsAsPlayed()")


class TestThePageTakesItsRoundOrderFromTheSeasonDocument(WebAssetCase):
    """The page puts rounds in the order ``season.json`` lists them, and has no rule.

    The page had its own copy of ``round_sort_key``, which put ``GF`` above ``SF``
    for the same reason the Python did (4.37). Measured before, in headless Chrome
    on the demo season with an ``SF`` and a ``GF`` card (``/tmp/t4/scratch/y437/
    measure.py``): the Matches page ended ``Finals — GF``, ``Finals — SF``, and the
    overview's Latest round read ``SF · Wed 11 Mar 2026``, the round before the last.
    The document's ``rounds`` is written in the season's order already, and every
    round the page sorts is one of them, so the order is read from there: one rule,
    in Python, and a page that renders it. Rejected: a second finals table here,
    kept in step by a test reading this file, which is two copies of one rule and a
    suite with no JavaScript engine to run the second; and a position field on each
    round, which says again what its place in the list already says.
    """

    def test_there_is_no_second_copy_of_the_rule(self):
        source = self.assets()["app.js"]
        self.assertFalse("function roundSortKey(" in jslint.code_only(source),
                         "app.js still has a round_sort_key of its own")
        # No finals label as a string or as a key, so no table for the page to sort
        # by. Not ``GF``, which is also the Games For column; a table of finals
        # needs the others.
        found = re.search(r"""(['"])\s*(?:qf|sf|pf|semi[ -]?finals?|semis?|"""
                          r"""grand[ -]?finals?|prelim(?:inary)?[ -]?finals?)\s*\1"""
                          r"""|\b(?:qf|sf|pf)\s*:""",
                          jslint.without_comments(source), re.I)
        self.assertIsNone(found, "app.js has a finals label to sort by")

    def test_two_rounds_are_compared_by_their_places_in_the_documents_list(self):
        compare = jslint.code_only(self.body("compareRounds"))
        self.assertRegex(compare, r"return\s+roundPosition\(a\)\s*-\s*"
                                  r"roundPosition\(b\)\s*;")
        position = jslint.code_only(self.body("roundPosition"))
        self.assertRegex(position, r"\bS\.rounds\b")
        self.assertRegex(position, r"\.number\s*===")
        # A round's place is its index, counted from the front.
        self.assertRegex(position, r"return\s+i\s*;")
        for text_order in ("toLowerCase", "localeCompare", "parseInt"):
            with self.subTest(text_order=text_order):
                self.assertNotIn(text_order, compare + position)

    def test_every_list_of_rounds_on_the_page_is_put_in_that_order(self):
        # A team's or a player's matches (byRound) and the ladder history's x axis:
        # the two lists of rounds the page gathers from something other than
        # S.rounds, which is in that order already.
        for name in ("byRound", "ladderHistoryChart"):
            with self.subTest(function=name):
                self.assertRegex(jslint.code_only(self.body(name)),
                                 r"\bcompareRounds\b")

    def test_a_round_is_still_titled_as_a_number_or_as_its_name(self):
        # ``Round 3`` and ``R3`` for a numbered round and the label itself for any
        # other, which is the one thing the page still asks of a label and is not
        # an order. Whether it is a number is the document's to say
        # (TestThePageAsksTheDocumentWhetherARoundIsANumber).
        numeric = jslint.code_only(self.body("isNumericRound"))
        self.assertRegex(numeric, r"\.numbered\b")
        self.assertNotIn("roundSortKey", numeric)


class TestThePageAsksTheDocumentWhetherARoundIsANumber(WebAssetCase):
    """``Round 3`` or the label as printed, by the build's answer and not the page's.

    ``isNumericRound`` held a rule of its own, ``/^[0-9]+$/``, beside the build's
    :func:`~matchcentre.competition.round_number`, and the two disagreed: measured in
    headless Chrome (``/tmp/t7/scratch/E1/probe.py``), the demo season with its round
    5 cards printing ``５``, ``٥`` or ``𝟓``, which the build scores as a numbered
    round without a warning, read ``Latest round ５ · Wed 4 Mar 2026`` on the front
    page and ``５`` under ``Round 1`` to ``Round 4`` on ``#/matches``; and the page
    titled 4301 digits ``Round 111...``, a label F11 calls a name. The page now looks
    the label up in ``S.rounds``, as :func:`roundPosition` does, and reads the
    round's ``numbered``, which the exporter writes from the build's rule
    (TestARoundSaysWhetherTheBuildReadItsLabelAsANumber). Strictly ``true``, as
    ``countsAsPlayed`` is, so a round a document does not say is numbered is titled
    as printed. Rejected: widening the page's test to every decimal digit, a second
    copy of the rule that the suite has no JavaScript engine to hold to the first.
    """

    # The format every season.json written before the rounds carried the flag says.
    FORMAT_BEFORE_THE_FLAG = 3

    def test_it_reads_the_flag_of_the_round_the_label_names(self):
        code = re.sub(r"\s+", " ", jslint.code_only(self.body("isNumericRound")))
        self.assertRegex(code, r"\bS\.rounds\b")
        self.assertRegex(code, r"\[\s*roundPosition\(\s*label\s*\)\s*\]",
                         "the round is not the one the label names in S.rounds")
        # And a label no round has is no round: roundPosition answers the list's
        # length for it, and a flag read off `undefined` throws. That guard and the
        # document's own true are the whole of its one answer, with no condition
        # beside them.
        answers = re.findall(r"\breturn\b([^;]*);", code)
        self.assertEqual(len(answers), 1,
                         "isNumericRound answers in more than one place")
        self.assertRegex(answers[0],
                         r"^\s*(?:!!)?(\w+)\s*&&\s*\1\.numbered\s*===\s*true\s*$",
                         "the page takes a round for a number on something other "
                         "than the document's own true")

    def test_it_keeps_no_rule_of_its_own(self):
        numeric = self.body("isNumericRound")
        # No pattern: a regex is the one literal written between slashes, and the
        # function divides nothing.
        self.assertNotIn("/", jslint.without_comments(numeric))
        code = jslint.code_only(numeric)
        for rule in ("RegExp", ".test(", ".match(", "parseInt", "parseFloat",
                     "Number(", "isNaN", "isFinite", "charCodeAt", "codePointAt"):
            with self.subTest(rule=rule):
                self.assertNotIn(rule, code)
        # Nor a second field of the round asked beside its flag: whether it is a
        # final, or has been played, is the page deciding again which rounds are
        # numbers, and a final numbered by its club is one.
        guarded = re.search(r"\b(\w+)\s*&&\s*\1\s*\.\s*numbered\b", code)
        self.assertIsNotNone(guarded, "isNumericRound reads no round's flag")
        self.assertEqual(set(re.findall(rf"\b{guarded.group(1)}\s*\.\s*(\w+)", code)),
                         {"numbered"})

    def test_a_round_s_title_and_its_tick_ask_it(self):
        for fn in ("roundTick", "roundTitle"):
            with self.subTest(function=fn):
                code = jslint.code_only(self.body(fn))
                self.assertRegex(code, r"\bisNumericRound\(\s*label\s*\)\s*\?")
                self.assertNotIn("/", jslint.without_comments(self.body(fn)))

    def test_a_document_built_before_the_flag_is_one_the_page_says_to_rebuild(self):
        # As for the keys: the page can tell an older document only by its number,
        # so both sides move past the one those documents carry, together
        # (TestThePageAndTheExporterAgreeOnTheDocumentFormat). An older document
        # titles every round as its bare label: measured, the demo's format 3
        # season.json beside this page, `Latest round 5 · Wed 4 Mar 2026` and
        # `#/matches` headings `1` to `5`, under `This page reads season document
        # format 4 and the data says 3`. Left at 3, the same page says nothing.
        from matchcentre import export
        declared = TestThePageAndTheExporterAgreeOnTheDocumentFormat.declared(self)
        with self.subTest(side="app.js"):
            self.assertGreater(declared, self.FORMAT_BEFORE_THE_FLAG,
                               "the page reads a document without the flag as "
                               "current")
        with self.subTest(side="export.py"):
            self.assertGreater(export.SEASON_FORMAT, self.FORMAT_BEFORE_THE_FLAG,
                               "a document with the flag says the format of one "
                               "without it")


class TestTheLevelScorelineSentenceIsGuardedByTheData(WebAssetCase):
    """The page may say what broke a tie only where the document says one was broken.

    The sentence was printed from ``tied_on_games`` alone, which was true of every
    match nobody played and of every genuine draw, and it named sets as the tiebreak
    whatever order the club's preset actually decides in. Two of those three are
    fixed in the exporter; this is the third, and it is a structural check because
    there is no JavaScript engine here to run the branch.
    """

    # Both halves of the one sentence, each read for its own guard: a rewording that
    # moved either half out from under the check would otherwise take the check with
    # it. guard_for() reads every printing of each and fails on one it cannot find,
    # which is what the list of wordings this used to carry -- one of them retired,
    # and matched by nothing -- was standing in for.
    PHRASES = ("level on games", "decided on sets")
    # Moved by 4.427 from matchCard(), which pushed the sentence itself, to the one
    # function the match card and the overview's Tightest match tile both ask.
    WORDS = "tiebreakWords"

    def test_nothing_prints_it_without_consulting_decided_by(self):
        for phrase in self.PHRASES:
            with self.subTest(phrase=phrase):
                self.assertIn("decided_by",
                              self.guard_for(self.WORDS, phrase, emits="return"),
                              "the sentence is printed from the level scoreline "
                              "alone, which is true of a washout")

    def test_it_does_not_claim_the_match_was_decided_on_sets_by_itself(self):
        # `decided_by === 'sets'` is the only thing that licenses the words "decided
        # on sets"; a check against anything else is the old hardcoded assumption.
        self.assertRegex(self.guard_for(self.WORDS, "decided on sets", emits="return"),
                         r"decided_by\s*===\s*'sets'")

    def test_nothing_else_in_the_page_derives_the_tie_from_the_two_totals(self):
        # The exporter's predicate is the single source. A `home_games ===
        # away_games` anywhere in the page is that predicate being re-derived, and
        # re-deriving it is how the washout got its sentence in the first place.
        code = jslint.code_only(self.assets()["app.js"])
        self.assertNotRegex(code, r"home_games\s*===?\s*\w*\.?away_games")


class TestAWinRateOutOfNoDecidedSetIsADashThatSortsBelowANought(WebAssetCase):
    """A player who decided no set has a null ``win_pct``, and the page shows no figure.

    The exporter used to publish those players as ``0.0``. It now publishes ``null``.
    The page was already written for that, and this class holds it there. On the
    card-writer season in /tmp/t4/scratch/y443/measure.py, headless Chrome showed a
    muted dash in the Win % cell of the team page and the Players tab, a dash on the
    player page's Win rate tile, an empty cell from Copy and CSV, and those players
    sorted below every real 0.0% by Win %: last on the first click and first on the
    second, as a missing rating is. It had shown 0.0% in all three places, exported
    ``0.0%``, and sorted the three among the real noughts by name.

    The heading is part of the same fix. It said "Sets won as a percentage of sets
    played", and the caption prints those words under both tables. A retired set is
    played and not decided, so the words were already false for four of the eighteen
    players in the demo season. Beside a dash for someone who played one set, they
    describe a figure the page is not showing. It now says "of completed sets", which
    is what the Win rate tile has always said. Leaving the heading alone was rejected,
    because a title that contradicts the cell under it is the next mistake a club
    reports.

    Structural, because there is no JavaScript engine here to run any of it.
    """

    NULL_TEST = "p.win_pct === null || p.win_pct === undefined"
    MUTED = "{ text: '—', cls: 'muted' }"
    WORDS = "Sets won as a percentage of completed sets"

    def test_both_tables_print_it_as_the_muted_dash_every_missing_figure_gets(self):
        # The same cell the ratings beside it print for nothing recorded. Copy and
        # CSV turn that dash into an empty cell, and an unguarded pct() gives the
        # dash without the muted class.
        for fn in ("teamPlayersTable", "renderPlayers"):
            with self.subTest(function=fn):
                said = jslint.without_comments(self.body(fn))
                printing = f"{self.NULL_TEST} ? {self.MUTED} : pct(p.win_pct)"
                self.assertIn(printing, said)
                self.assertEqual(said.count("p.win_pct"), printing.count("p.win_pct"),
                                 f"{fn}() reads p.win_pct somewhere other than the "
                                 f"one guarded printing")

    def test_the_player_page_tile_prints_it_as_the_dash_the_helper_prints_for_null(self):
        # pct() prints a dash for null, which is why the tile's own guard is belt and
        # braces: the tile may keep it or leave the dash to pct(), and nothing else. A
        # guard printing '0.0%' was measured in Chrome putting the nought back on both
        # tiles, and anything else printing the rate would say "—%" or throw.
        said = jslint.without_comments(self.body("renderPlayer"))
        value = re.search(r"tile\('Win rate',\s*([^,]*),", said)
        self.assertIsNotNone(value, "renderPlayer() has no Win rate tile")
        self.assertIn(" ".join(value.group(1).split()),
                      ("pct(p.win_pct)", f"{self.NULL_TEST} ? '—' : pct(p.win_pct)"))
        rest = said.replace(self.NULL_TEST, "").replace("pct(p.win_pct)", "")
        self.assertNotIn("win_pct", rest)

    def test_a_missing_rate_sorts_below_a_real_nought(self):
        # The sort subtracts one value from the other, and null - 0 is 0. A bare
        # p.win_pct was measured in Chrome putting the three dashes among the two
        # real noughts by name, which is what the old 0.0 did. -1 is below every rate.
        # Out of the rate's own case, or the default every other missing figure takes
        # if that case is ever folded into it.
        said = jslint.without_comments(self.body("playerSortValue"))
        case = (re.search(r"case 'win_pct':\s*return ([^;]*);", said)
                or re.search(r"default:\s*return ([^;]*);", said))
        self.assertIsNotNone(case, "playerSortValue() has no win_pct case and no default")
        self.assertEqual(" ".join(case.group(1).replace("p[key]", "p.win_pct").split()),
                         f"{self.NULL_TEST} ? -1 : p.win_pct")

    def test_the_heading_says_the_rate_is_out_of_completed_sets_as_the_tile_does(self):
        app = self.assets()["app.js"]
        titles = [re.search(r"title: '([^']*)'", spec.group(0))
                  for spec in re.finditer(r"\{[^{}]*\}", app)
                  if re.search(r"label: 'Win %'", spec.group(0))]
        self.assertGreaterEqual(len(titles), 2, "the team page and the Players tab "
                                                "both head a Win % column")
        for title in titles:
            with self.subTest(title=title and title.group(1)):
                self.assertIsNotNone(title)
                self.assertEqual(title.group(1), self.WORDS)
        self.assertRegex(self.body("renderPlayer"),
                         r"tile\('Win rate',[^;]*'of completed sets'\)")


class TestTheSlotTablePrintsTheRateThePythonPublishes(WebAssetCase):
    """The player page's slot table prints each slot's ``win_pct`` and works out none.

    It printed ``pct(100 * rec.won / rec.played)``, sets won out of sets played, three
    cards below a Win rate tile printing sets won out of sets decided. So a retired set
    counted against the player in this table and nowhere else. Measured with headless
    Chrome on the demo season (/tmp/t6/scratch/m13/before.txt): 50.0% at d2 for
    Clementine Nakagawa's two won and one lost, and on 4.43's two-card season a dash on
    Foxtrot Fenwick's tile over 0.0% at s2, whose one set was the retired one. The
    Python now publishes the rate, from the function behind the tile, and the cell
    prints it as the tile prints its own, with the muted dash every missing figure gets
    for a slot with no decided set.

    A document without the field, one written before it, gets that dash in every row,
    not a figure: working it out from ``won`` and ``played`` for such a document would
    put back the number this replaces. The heading now says what the column is out of,
    in the caption's key, because a row reading 4 played, 2 won, 66.7% needs it.

    Rejected: publishing ``lost`` beside ``won`` and dividing here. That writes the
    rule a second time, in the language that got it wrong.

    Structural, because there is no JavaScript engine here to run any of it.
    """

    NULL_TEST = "rec.win_pct === null || rec.win_pct === undefined"
    MUTED = "{ text: '—', cls: 'muted' }"

    def said(self) -> str:
        return " ".join(jslint.without_comments(self.body("slotAppearanceTable")).split())

    def test_the_rate_cell_prints_the_published_rate_or_the_muted_dash(self):
        # The muted dash is the house styling for a missing figure, the one this table
        # already gave a slot never played. Copy and CSV empty any dash by its text,
        # guarded or not (measured in Chrome), so the guard is for the eye: pct() alone
        # prints the same dash unmuted. Any other mention of win_pct, a nought in the
        # default record or a fallback for an older document, is a second source.
        printing = f"{self.NULL_TEST} ? {self.MUTED} : pct(rec.win_pct)"
        said = self.said()
        self.assertIn(printing, said)
        self.assertEqual(said.count("win_pct"), printing.count("win_pct"),
                         "slotAppearanceTable() reads win_pct somewhere other than "
                         "the one guarded printing")

    def test_the_table_works_out_no_rate_of_its_own(self):
        # Code alone, with strings and comments blanked: any division left is a rate
        # the page is working out, and the one it worked out was wrong.
        code = jslint.code_only(self.body("slotAppearanceTable"))
        self.assertNotIn("/", code, "slotAppearanceTable() divides, so some rate on "
                                    "the slot table is the page's own arithmetic")

    def test_the_rate_column_says_it_is_out_of_completed_sets_as_the_tile_does(self):
        words = TestAWinRateOutOfNoDecidedSetIsADashThatSortsBelowANought.WORDS
        self.assertIn(f"{{ label: 'Win rate', title: '{words}' }}", self.said())


class TestTheSetsTilePrintsTheTeamsRateThePythonPublishes(WebAssetCase):
    """The team page's Sets tile prints the team row's ``win_pct`` and works out none.

    It printed ``num(100 * t.sets_won / (t.sets_won + t.sets_lost), 0)``, the rule
    behind every rate on the site written a second time in the page, and rounded to
    no place where ``pct()`` rounds every other rate to one. Measured with headless
    Chrome on the demo season (/tmp/t7/scratch/W2/before.txt): the Quolls' tile read
    ``53% of sets won`` for 10-9, whose rate is 52.6, beside a Players tab printing
    each of its eighteen rates to one place. The team row now carries the rate
    (TestATeamsRateOfSetsWonIsPublishedByTheRuleAPlayersIs) and the tile prints it
    through ``pct()``.

    The tile keeps its guard on the sets for "no sets yet", which is a count of sets
    and not a rate. Rejected: guarding on a null ``win_pct`` instead, which would say
    "no sets yet" of every team in a document written without the field, whatever its
    sets say, where the guard kept prints the helper's dash.

    Structural, because there is no JavaScript engine here to run any of it.
    """

    def tile(self) -> str:
        """The Sets tile's call in renderTeam(), comments blanked and strings kept."""
        said = jslint.without_comments(self.body("renderTeam"))
        code = jslint.code_only(said)
        start = said.find("tile('Sets',")
        self.assertGreaterEqual(start, 0, "renderTeam() has no Sets tile")
        depth = 0
        for end in range(start + len("tile"), len(code)):
            depth += {"(": 1, ")": -1}.get(code[end], 0)
            if depth == 0:
                return said[start:end + 1]
        self.fail("the Sets tile's call never closes")

    def test_it_prints_the_published_rate_through_the_helper_every_rate_uses(self):
        # pct() is one place and the dash for null. The tile's own num(..., 0) was
        # 53% for the Quolls' 52.6.
        self.assertIn("pct(t.win_pct) + ' of sets won'", " ".join(self.tile().split()))

    def test_it_works_out_no_rate_of_its_own(self):
        # Code alone, with strings blanked: any division left is a rate the page is
        # working out, and a nought sent to num() is the old precision.
        code = jslint.code_only(self.tile())
        self.assertNotIn("/", code, "the Sets tile divides, so its rate is the "
                                    "page's own arithmetic")
        self.assertNotIn("100", code)

    def test_a_team_that_has_decided_no_set_still_reads_no_sets_yet(self):
        # The guard counts the sets set_win_pct divides by, so the words and the
        # rate's null answer one question. Dropped, a team with no set reads "— of
        # sets won"; on matches played, a team whose one card fell short of a result
        # reads "no sets yet" beside the 2-0 it won there (measured in the Python:
        # played 0, sets 2-0, win_pct 100.0).
        self.assertRegex(" ".join(self.tile().split()),
                         r", \(t\.sets_won \+ t\.sets_lost\) \? pct\(t\.win_pct\) "
                         r"\+ ' of sets won' : 'no sets yet'\)$")

    def test_the_team_page_reads_the_rate_once_and_only_to_print_it(self):
        # A second reading is a guard or a fallback on the field, which is the page
        # deciding again what a team without a rate is shown.
        self.assertEqual(jslint.code_only(self.body("renderTeam")).count("win_pct"), 1,
                         "renderTeam() reads win_pct somewhere other than the tile")
        self.assertEqual(jslint.code_only(self.tile()).count("win_pct"), 1)


class TestAShareOfNoTeamGamesIsADashThatSortsBelowANought(WebAssetCase):
    """A null ``share_pct`` is printed as no figure, and sorted below every real one.

    The exporter published a share of a team that won no games, and of a name on no
    roster, as ``0.0``. It publishes ``null`` now. Measured on the one-card season in
    /tmp/t7/scratch/W2/before.txt with headless Chrome: the three Bravos and Hotel
    Hartigan, a name on neither roster, read 0.0% in the Share cell of both tables,
    ``0.0%`` from Copy and CSV, and sorted among the Alphas' real 0.0% by name, and
    the player page's Team share tile read ``0.0%`` over ``0 of 0 team games won``
    for Delta Dunmore and for Hotel alike, though Hotel's side won 24. The cells now
    print the muted dash every missing figure gets, which Copy and CSV empty, the
    sort puts the dashes last on one click and first on the other, as a missing
    rating is, and the tile says of a player on no roster what the players-to-check
    list says of one.

    Every reader of ``share_pct`` asks the house way, ``!== null`` and ``!==
    undefined``, as the ratings beside it do. The team caption reads the team's own
    ``share_pct`` only under a shortfall in games, which a team that won none cannot
    have (TestTheShareColumnAccountsForWhatItDoesNotAddUpTo).

    Structural, because there is no JavaScript engine here to run any of it.
    """

    HAS_SHARE = ("p.contribution && p.contribution.share_pct !== null && "
                 "p.contribution.share_pct !== undefined")
    MUTED = "{ text: '—', cls: 'muted' }"

    def said(self, fn: str) -> str:
        return " ".join(jslint.without_comments(self.body(fn)).split())

    def test_both_tables_print_it_as_the_muted_dash_every_missing_figure_gets(self):
        # An unguarded pct() gives the dash without the muted class, and a guard on
        # p.contribution alone lets the null through to it.
        for fn in ("teamPlayersTable", "renderPlayers"):
            with self.subTest(function=fn):
                said = self.said(fn)
                printing = (f"{self.HAS_SHARE} ? pct(p.contribution.share_pct) : "
                            f"{self.MUTED}")
                self.assertIn(printing, said)
                self.assertEqual(said.count("p.contribution"),
                                 printing.count("p.contribution"),
                                 f"{fn}() reads the share somewhere other than the "
                                 f"one guarded printing")

    def tile_value(self) -> str:
        """The Team share tile's value in renderPlayer(): its second argument."""
        said = jslint.without_comments(self.body("renderPlayer"))
        code = jslint.code_only(said)
        start = said.find("tile('Team share',")
        self.assertGreaterEqual(start, 0, "renderPlayer() has no Team share tile")
        start += len("tile('Team share',")
        depth = 0
        for end in range(start, len(code)):
            depth += {"(": 1, "[": 1, "{": 1, ")": -1, "]": -1, "}": -1}.get(code[end], 0)
            if depth == 0 and code[end] == ",":
                return " ".join(said[start:end].split())
        self.fail("the Team share tile has no argument after its value")

    def test_the_player_page_tile_prints_it_as_the_dash_the_helper_prints_for_null(self):
        self.assertIn(self.tile_value(),
                      ("p.contribution ? pct(p.contribution.share_pct) : '—'",
                       f"{self.HAS_SHARE} ? pct(p.contribution.share_pct) : '—'"))

    def test_the_tile_gives_a_player_on_no_roster_no_team_s_total(self):
        # "0 of 0 team games won" was measured on the page of a player whose side won
        # 24. The words are the players-to-check list's for the same player. Changed by
        # 4.456: the team's games are worded for their count, so a 1 is "1 team game".
        said = self.said("renderPlayer")
        tile = said[said.index("tile('Team share',"):]
        self.assertRegex(tile, r"^tile\('Team share', [^;]*?, !p\.team \? "
                               r"'on no team’s roster' : p\.contribution \? "
                               r"shareNum\(p\.contribution\.games_won\) \+ ' of ' \+ "
                               r"num\(p\.contribution\.team_games_won\) \+ ' team ' \+ "
                               r"plural\(p\.contribution\.team_games_won, 'game'\) \+ "
                               r"' won' : '—'\)")

    def test_a_missing_share_sorts_below_a_real_nought(self):
        # The sort subtracts one value from the other, and null - 0 is 0: a bare
        # share_pct was measured in Chrome putting the dashes among the real noughts
        # by name. -1 is below every share.
        said = " ".join(jslint.without_comments(self.body("playerSortValue")).split())
        case = re.search(r"case 'share': return ([^;]*);", said)
        self.assertIsNotNone(case, "playerSortValue() has no share case")
        self.assertEqual(case.group(1), f"{self.HAS_SHARE} ? p.contribution.share_pct : -1")


class TestAPlayersMatchesTileSaysHowManyWereForTheirOwnTeam(WebAssetCase):
    """The Matches tile keeps the whole record, and says how much of it was the team's.

    The tile was ``tile('Matches', String(p.matches), 'of ' + p.available + ' the
    team has played')``: every night the player was on court, lent nights included,
    over the nights their own team played, and for a player on no team their own
    count over itself. Measured in headless Chrome on a fictional season
    (/tmp/mcf/scratch/ROSTER/before/measure.txt): Jo Calder, lent once on her own
    side's bye, read ``5 / of 4 the team has played``; Lena Ordoy, lent once beside
    one night of her own, ``2 / of 4``, and ``2 of 4`` in the players-to-check
    Played cell; Zed Quill, on no roster, ``1 / of 1 the team has played`` beside
    ``Team share — on no team’s roster``; and in the demo Priya Halloran read ``4 /
    of 4`` for three of the Brindle Magpies' four nights and one for the Rosellas.

    The split is Python's, ``team_matches`` beside ``available``
    (TestAPlayersMatchesSayHowManyWereForTheTeamThatRegistersThem), and one function
    here reads it for both places, so the tile reads ``4 of 4 the team has played, 1
    for another side`` and the cell ``1 of 4, 1 for another side``. A document from
    before the key has no line under the figure, as an older document's regular
    fixtures print unknown. Rejected: counting the team's nights here from the
    matches, Python's rule a second time in the language the suite cannot run; and
    making the figure the team's nights alone, which the Players view's M column,
    the whole record, would contradict.

    Structural, because there is no JavaScript engine here to run any of it.
    """

    def said(self, fn: str) -> str:
        return " ".join(jslint.without_comments(self.body(fn)).split())

    def test_one_function_reads_the_split_for_the_tile_and_the_played_cell(self):
        # Neither place reads the two counts itself, so the two cannot word one
        # player two ways, and neither reads the old figure the tile was built on.
        for fn in ("renderPlayer", "playersToCheckCard"):
            with self.subTest(function=fn):
                code = jslint.code_only(self.body(fn))
                self.assertEqual(code.count("matchesOutOf(p)"), 1)
                self.assertNotRegex(code, r"\bp\.(?:available|team_matches)\b")

    def test_the_split_is_the_document_s_and_not_one_made_here(self):
        said = self.said("matchesOutOf")
        self.assertIn("var lent = p.matches - p.team_matches;", said)
        self.assertIn("played: p.team_matches, of: p.available,", said)
        for counted in ("S.matches", "fill_in_appearances", "setsOfPlayer"):
            with self.subTest(reads=counted):
                self.assertNotIn(counted, jslint.code_only(self.body("matchesOutOf")))

    def test_a_player_on_no_roster_or_a_document_without_the_key_is_out_of_nothing(self):
        # A null team_matches is a player on no team; an undefined one is an older
        # document, which cannot say which of the nights were the team's.
        self.assertIn("{ if (!p.team || p.team_matches === null || "
                      "p.team_matches === undefined) return null;",
                      self.said("matchesOutOf"))

    def test_the_nights_for_another_side_are_worded_once(self):
        self.assertIn("lent: lent ? lent + ' for ' + plural(lent, 'another side', "
                      "'other sides') : ''", self.said("matchesOutOf"))

    def test_the_tile_keeps_the_whole_record_and_words_its_foot_by_the_three_cases(self):
        said = self.said("renderPlayer")
        self.assertIn("var outOf = matchesOutOf(p);", said)
        self.assertRegex(said[said.index("tile('Matches',"):],
                         r"^tile\('Matches', String\(p\.matches\), !p\.team \? "
                         r"'on no team’s roster' : outOf && \(outOf\.lent \? "
                         r"outOf\.played \+ ' of ' \+ outOf\.of \+ "
                         r"' the team has played, ' \+ outOf\.lent : 'of ' \+ "
                         r"outOf\.of \+ ' the team has played'\)\),")

    def test_a_document_without_the_key_prints_no_line_under_the_figure(self):
        # `outOf && (...)` hands the tile null for such a document, and a tile with
        # no foot draws none, rather than an empty line or the word "null".
        self.assertIn("foot ? el('div', { class: 'foot', text: foot }) : null",
                      self.said("tile"))

    def test_the_played_cell_takes_the_same_words(self):
        # A player on no roster keeps the bare figure: the Note column beside it
        # says "on no team’s roster" already.
        said = self.said("playersToCheckCard")
        self.assertIn("var outOf = matchesOutOf(p);", said)
        self.assertIn("teamLinkBySlug(p.team), !outOf ? String(played) : outOf.lent "
                      "? outOf.played + ' of ' + outOf.of + ', ' + outOf.lent : "
                      "outOf.of > played ? played + ' of ' + outOf.of : "
                      "String(played),", said)


class TestARegisteredPlayerIsOneARosterBlockPrints(WebAssetCase):
    """The views count as registered the rows with a team, and give the rest apart.

    The overview header, the Players used tile and the Players view's caption counted
    every row of ``S.players``, which holds a row for every name a counted set row
    prints. Measured in headless Chrome on a fictional season
    (/tmp/mcf/scratch/ROSTER/before/measure.txt): ``8 scorecards read · 4 teams · 13
    registered players``, ``Players used 10 of 13`` over ``3 yet to play`` and ``13
    registered players.``, where the roster blocks register 12 and Zed Quill, on
    none of them, is the thirteenth. They read now ``12 registered players · 1 on no
    team’s roster``, ``9 of 12`` over ``3 yet to play · 1 more on no team’s
    roster``, and ``12 registered players and 1 on no team’s roster.``, and the
    view's subtitle says it lists everyone on no team's roster who played. Each new
    clause is there only where there is such a player, so the demo, whose 18 rows
    all have a team, reads as it did.

    A row has a team exactly when a roster block registers its player
    (TestARowHasATeamExactlyWhenARosterBlockRegistersItsPlayer). Rejected: dropping
    the word registered and keeping the totals, so that the squad's size, the
    figure validate and the README call registered, would be on no page; counting
    off the rows with ``fill_in_appearances``, which a registered player lent to
    another side has too; ``S.teams[].players``, a second list beside the rows the
    tile's numerator counts; a count published in season.json, a new key for what
    the page has already; and calling the rest fill-ins, which validate leaves open.

    Structural, because there is no JavaScript engine here to run any of it.
    """

    def said(self, fn: str) -> str:
        return " ".join(jslint.without_comments(self.body(fn)).split())

    def test_one_function_says_who_is_registered(self):
        self.assertIn("{ var rows = S.players || []; var registered = "
                      "rows.filter(function (p) { return !!p.team; }); return { rows: "
                      "registered, unrostered: rows.length - registered.length }; }",
                      self.said("registeredPlayers"))

    def test_neither_view_counts_every_row_as_registered(self):
        for fn in ("renderOverview", "renderPlayers"):
            with self.subTest(function=fn):
                code = jslint.code_only(self.body(fn))
                self.assertEqual(code.count("registeredPlayers()"), 1)
                self.assertNotIn("(S.players || []).length", code)

    def test_the_overview_header_names_the_rest_apart_only_where_there_are_any(self):
        self.assertIn("plural(ladder.length, 'team') + ' · ' + registered.rows.length "
                      "+ ' registered ' + plural(registered.rows.length, 'player') + "
                      "(registered.unrostered ? ' · ' + registered.unrostered + "
                      "' on no team’s roster' : ''))", self.said("renderOverview"))

    def test_the_players_used_tile_is_of_the_registered_and_names_the_rest_apart(self):
        said = self.said("renderOverview")
        self.assertIn("var appeared = registered.rows.filter(function (p) { return "
                      "p.matches > 0; }).length;", said)
        self.assertIn("tile('Players used', appeared + ' of ' + "
                      "registered.rows.length, registered.rows.length - appeared + "
                      "' yet to play' + (registered.unrostered ? ' · ' + "
                      "registered.unrostered + ' more on no team’s roster' : ''))",
                      said)

    def test_the_players_view_says_it_in_its_subtitle_and_its_caption(self):
        said = self.said("renderPlayers")
        self.assertIn("'Every registered player, including those yet to play' + "
                      "(registered.unrostered ? ', and everyone on no team’s roster "
                      "who played' : '') + '. Select a column heading to sort.'", said)
        self.assertIn("caption: registered.rows.length + ' registered ' + "
                      "plural(registered.rows.length, 'player') + "
                      "(registered.unrostered ? ' and ' + registered.unrostered + "
                      "' on no team’s roster' : '') + '. A dash means nothing "
                      "recorded yet.',", said)


class TestThePlayersToCheckList(WebAssetCase):
    """The one part of the page that exists to find a mistake in the data.

    A player recorded under two spellings splits into two half-records, and no
    other number on the page moves: the team's points, its games and its ladder
    position are all correct, because a set was played and counted either way.
    So there is nothing for a validator to fail on and nothing for a reader to
    notice -- unless the page says so. The README promises this list, which makes
    it a documented feature rather than a nicety.
    """

    def test_the_list_is_built_and_actually_placed_on_the_page(self):
        # A builder nothing calls is the failure this is aimed at: the code
        # reviews as present and the page shows nothing.
        self.assertIn("function playersToCheckCard",
                      jslint.code_only(self.assets()["app.js"]))
        # Searched inside renderPlayers() alone. Searching the whole file finds
        # the definition and calls that a pass -- which is exactly the mistake
        # being tested for, one level up.
        self.assertRegex(self.body("renderPlayers"), r"playersToCheckCard\s*\(",
                         "renderPlayers() never calls playersToCheckCard(), so "
                         "the list is built by nothing and appears nowhere")

    def test_it_reads_only_fields_the_exporter_writes(self):
        # The season document is the whole contract between Python and the page.
        # A misremembered field name reads as `undefined`, which formats as an
        # empty cell rather than as an error -- so a wrong name here produces a
        # table of blanks that looks like "nobody to check".
        import dataclasses
        from matchcentre import stats
        known = {f.name for f in dataclasses.fields(stats.PlayerStats)}
        read = set()
        for name in ("playersToCheck", "playersToCheckCard"):
            read |= set(re.findall(r"\bp\.([a-z_]+)", self.body(name)))
        # Without this the check passes on code that reads no fields at all,
        # which is also the state it would be in if the extraction broke.
        self.assertGreaterEqual(len(read), 4, f"only found {sorted(read)}; the "
                                              f"field extraction has stopped "
                                              f"seeing the code")
        self.assertEqual(sorted(read - known), [],
                         f"app.js reads player field(s) the exporter does not "
                         f"write: {sorted(read - known)}")

    def test_it_withholds_the_list_until_it_would_mean_something(self):
        # Two rounds in, every player has played at most two matches and the list
        # is every player on the page. A list that says "check everyone" is worse
        # than no list, because it trains a club to ignore it.
        body = self.body("playersToCheck")
        self.assertIn("CHECK_NEEDS", body)
        self.assertRegex(body, r"scale\s*<\s*CHECK_NEEDS",
                         "nothing compares the season's scale against the "
                         "threshold, so the list appears on a one-round season")

    def test_a_player_who_has_played_nothing_at_all_is_left_out(self):
        # Zero appearances is a squad member yet to play, which the main table
        # already shows as a row of dashes. It cannot be half of a split name --
        # a split name has at least one appearance on each side -- so including it
        # would add rows that can never be the thing the list is looking for.
        self.assertRegex(self.body("playersToCheck"), r"played\s*>=\s*1")

    def test_it_names_the_remedy_rather_than_only_the_symptom(self):
        # Every other advisory line on this page names the config key to set. A
        # list of suspicious rows with no next step is a dead end.
        body = self.body("playersToCheckCard")
        self.assertIn("[aliases]", body)
        self.assertIn("config.toml", body)

    def test_it_shows_the_rounds_that_settle_the_question(self):
        # The distinguishing evidence, and the reason this is a table rather than
        # a sentence: two spellings of one player never share a round, and two
        # real squad members who each played twice usually do.
        self.assertIn("roundsPlayedIn", self.body("playersToCheckCard"))
        self.assertIn("setsOfPlayer", self.body("roundsPlayedIn"))

    def test_it_promises_no_automatic_merge(self):
        # Deciding two names are one person is a fact about people, which this
        # program cannot check. Wording that implied otherwise would invite a
        # club to wait for something that is never going to happen.
        body = self.body("playersToCheckCard").lower()
        self.assertIn("merged", body)


class TestTheFillInNoteCountsWhatThePythonCounts(WebAssetCase):
    """One figure, printed in the wrong unit, in the note whose job is to explain it.

    ``fill_in_appearances`` is the number of distinct *matches* a player appeared in
    off their own roster: stats.py builds it from a set of the same
    ``(round, home, away)`` keys it counts as ``match_ids``. The page printed it as
    sets -- "has played 1 set outside their registered roster" on a night the player
    was used in two rubbers -- and this is the one note on a player's page that
    exists to explain an oddity, so a club reads it and can check it against the
    card. That the count itself is matches is asserted against real data in
    test_pipeline; what is asserted here is the word.
    """

    def lines_printing_the_figure(self):
        lines = [line for line in self.assets()["app.js"].splitlines()
                 if "fill_in_appearances" in line]
        self.assertTrue(lines, "app.js no longer mentions fill_in_appearances at "
                               "all, so this check has stopped seeing the code")
        return lines

    def test_the_note_prints_the_figure_as_matches(self):
        self.assertRegex(
            self.body("slotAppearanceTable"),
            r"plural\(\s*p\.fill_in_appearances\s*,\s*'match'\s*,\s*'matches'\s*\)",
            "the fill-in note does not print its figure as matches")

    def test_no_line_that_prints_the_figure_calls_it_a_set(self):
        # Aimed wider than the one note: the same mistake made anywhere else on the
        # page is the same wrong number in front of the same club, and this table
        # sits under a heading about set slots, which is what invited it.
        for line in self.lines_printing_the_figure():
            with self.subTest(line=line.strip()):
                self.assertNotRegex(line, r"'sets?'")

    def test_a_count_of_matches_is_never_pluralised_by_adding_an_s(self):
        # plural() builds its own plural by appending one, so a caller that leaves
        # the third argument off prints "2 matchs" -- on the page, to a club, in a
        # sentence nothing else will contradict.
        for line in self.assets()["app.js"].splitlines():
            if "plural(" in line and "'match'" in line:
                with self.subTest(line=line.strip()):
                    self.assertIn("'matches'", line)


class TestAFillInNoteSaysOnNoTeamsRosterOnlyOfAPlayerNoSquadRegisters(WebAssetCase):
    """The Fill-in note says who a player is, and not only that they were a fill-in.

    Both notes said every fill-in ``played outside their registered roster``: the
    match card ``Fill-in: Zed Quill played outside their registered roster.`` and
    the player page ``Zed Quill has played 1 match outside their registered
    roster.``, measured in Chrome on a season where Zed Quill is on no roster block
    and no squad (``/tmp/mcf/scratch/PNOISE/pages.py``). The Team share tile a few
    lines above on the same page says ``on no team’s roster`` of him, and so does
    the players-to-check list, so the page contradicted itself about one person. A
    player on no squad has no registered roster to be outside.

    Now both notes say ``is on no team’s roster``, in the list's words, of a player
    with no team on this page whom no other competition of the club registers
    either; the match card finds the record in ``IDX.playerBySlug``, since a match
    holds its fill-ins as slugs. A player on a team keeps the sentence, which is
    what a lent player did, and so does a player the record says is
    ``registered_elsewhere``. Quoy Darrow, on the Monday hawks' roster and on no
    team on this Tuesday page, read ``is on no team’s roster`` on both under the
    first version of this, which asked the team alone, beside the Data health
    line saying he is ``registered in monday-division-b``
    (``/tmp/mcf/scratch/PNOISE/fixup/quin-first.txt``). The figure on the page note
    is printed as before (:class:`TestTheFillInNoteCountsWhatThePythonCounts`).

    Rejected: naming the side they played for as their team, which invents a
    membership no card printed; a heading of its own saying the name may be a
    misspelling, which the players-to-check list and unknown-player already ask,
    while ``on no team’s roster`` is true either way; and the page working out
    where a player is registered from the report's findings, a rule in the
    language that renders.
    """

    NONE = "on no team’s roster"

    def said(self, fn: str) -> str:
        return " ".join(jslint.without_comments(self.body(fn)).split())

    def test_the_match_card_s_note_asks_the_player_s_record_where_they_are_registered(self):
        self.assertRegex(
            self.said("matchCard"),
            r"\(m\.fill_ins \|\| \[\]\)\.forEach\(function \(slug\) \{ "
            r"var who = IDX\.playerBySlug\[slug\]; "
            r"body\.appendChild\(note\('warning', 'Fill-in', playerName\(slug\) \+ "
            r"\(who && \(who\.team \|\| who\.registered_elsewhere\) "
            r"\? ' played outside their registered roster\.' "
            r": ' is on no team’s roster\.'\)\)\); \}\);")

    def test_the_player_page_s_note_asks_the_player_where_they_are_registered(self):
        self.assertRegex(
            self.said("slotAppearanceTable"),
            r"if \(p\.fill_in_appearances\) \{ "
            r"out\.appendChild\(note\('warning', 'Fill-in', "
            r"!p\.team && !p\.registered_elsewhere "
            r"\? p\.name \+ ' is on no team’s roster\.' "
            r": p\.name \+ ' has played ' \+ p\.fill_in_appearances \+ ' ' \+ "
            r"plural\(p\.fill_in_appearances, 'match', 'matches'\) \+ "
            r"' outside their registered roster\.'\)\); \}")

    def test_every_sentence_about_a_player_on_no_team_is_in_the_same_words(self):
        # The tile, the players-to-check list and the two notes, at least: places
        # that say one thing about one person, so one that drifts, or a straight
        # apostrophe where the others have the page's own, reads as something else.
        said = jslint.without_comments(self.assets()["app.js"])
        found = re.findall(r"on no team\S{0,2}s roster", said)
        self.assertGreaterEqual(len(found), 4, found)
        self.assertEqual(set(found), {self.NONE})


class TestThePageRoundsTheWayThePythonDoes(WebAssetCase):
    """One convention, two implementations, and no engine here to run one of them.

    A club checks this dashboard against a figure it worked out in a spreadsheet, so
    the last digit has to match. 6.225 must print 6.23, and neither language gives
    that by default: both round a half to even, and 6.225 is not stored as a half
    anyway -- the nearest double is 6.22499999999999964, so even rounding half up on
    the float rounds it down. A cent under the number on a club's own noticeboard is
    indistinguishable, to the club, from a scoring bug.

    The numeric half of this check is :mod:`test_pipeline`, against
    ``stats.round_half_up``. Here the mechanism can only be read.
    """

    def test_the_page_has_a_rounder_of_its_own(self):
        self.assertIn("function halfUp(",
                      jslint.code_only(self.assets()["app.js"]))

    def test_it_rounds_the_decimal_and_not_the_double(self):
        # `Math.round(v * 100) / 100` reads as equivalent and is not: multiplying
        # keeps 6.225 below the half and returns 6.22. Shifting the exponent on
        # String(v) -- the shortest decimal that round-trips to v -- is what makes
        # it a half again, so the string is the mechanism, not an incidental.
        body = self.body("halfUp")
        self.assertIn("String(", body)
        self.assertRegex(body, r"\+\s*'e'\s*\+",
                         "nothing shifts the exponent on the decimal string")
        self.assertRegex(body, r"\+\s*'e-'\s*\+",
                         "nothing shifts the exponent back")
        self.assertNotRegex(body, r"\*\s*(100|Math\.pow)")

    def test_every_number_the_page_prints_goes_through_it(self):
        # num() is what every table cell calls, so a toFixed() reached anywhere
        # else is a column that rounds differently from the one beside it.
        self.assertRegex(self.body("num"), r"halfUp\s*\(")
        code = jslint.code_only(self.assets()["app.js"])
        stray = sorted({line.strip() for line in code.splitlines()
                        if "toFixed(" in line and "halfUp(" not in line})
        self.assertEqual(stray, [], f"toFixed() outside num(): {stray}")

    def test_a_value_it_cannot_round_is_left_alone_rather_than_mangled(self):
        # A team with nothing decided has no average, and NaN or 1e21 through the
        # exponent trick would produce '1e21e2', which is not a number.
        body = self.body("halfUp")
        self.assertIn("isFinite", body)
        self.assertRegex(body, r"indexOf\('e'\)")


class TestTheBannerAndTheDrawAgreeWithTheExporter(WebAssetCase):
    """Two fields the season document grew, and the page has to have grown with it.

    Neither failure is visible: a field the page does not read is a line the banner
    does not print, and a field it reads under the wrong name is `undefined`, which
    renders as an empty cell. Both leave a page that looks finished.
    """

    def test_the_banner_says_what_a_bye_or_a_washout_is_worth(self):
        # It is the one rule in force that changes the points column without
        # changing anybody's position, so a reader comparing this table against an
        # association sheet has no other way to account for the difference.
        body = self.body("renderRulesBanner")
        self.assertIn("unplayed", body)
        self.assertIn("unplayed_detail", body)

    def test_the_banner_says_when_a_night_cannot_end_level(self):
        # The other rule in force that no scorecard records and that takes matches
        # out of the ladder. A club reading a ladder with a night missing from it
        # needs the reason on the same screen, not only in the terminal output of
        # whoever ran the build.
        body = self.body("renderRulesBanner")
        self.assertIn("draws", body)
        self.assertIn("draws_detail", body)

    def test_and_says_nothing_about_it_in_a_grade_where_a_draw_is_normal(self):
        # The banner drops a pair with no value and no detail, so the exporter
        # writing empty strings is what keeps the row off almost every club's page.
        # Asserted against the exporter rather than the page, because this is the
        # half of the arrangement the page cannot enforce.
        block = _rules_block()
        self.assertTrue(block["allows_draw"], "the empty competition this reads has "
                                              "stopped allowing draws, so the test "
                                              "is checking the other case")
        self.assertEqual(block["draws"], "")
        self.assertEqual(block["draws_detail"], "")

    def test_the_footnote_names_every_setting_the_banner_shows(self):
        # The footnote is the only place on the page that tells a club which config
        # keys these came from. It named three while the banner showed four, which
        # sends a club looking for a setting it has been told does not exist.
        body = self.body("renderRulesBanner")
        shown = re.findall(r"\['([A-Z][a-z_]+)',\s*r\.", body)
        self.assertGreaterEqual(len(shown), 4, f"only found {shown}; the row "
                                               f"extraction has stopped seeing "
                                               f"the code")
        footnote = re.search(r"If they are not[^']*", body)
        self.assertIsNotNone(footnote, "the banner no longer says where these "
                                       "settings come from")
        for label in shown:
            with self.subTest(setting=label):
                self.assertIn(label.lower(), footnote.group(0))

    def test_the_fields_the_banner_reads_are_fields_the_exporter_writes(self):
        # A misremembered name reads as `undefined`, which renders as an empty
        # cell: a banner with a blank row rather than an error.
        written = set(_rules_block())
        read = set(re.findall(r"\br\.([a-z_]+)", self.body("renderRulesBanner")))
        self.assertGreaterEqual(len(read), 4, f"only found {sorted(read)}; the "
                                              f"field extraction has stopped "
                                              f"seeing the code")
        self.assertEqual(sorted(read - written), [])

    def test_a_round_can_show_more_than_one_team_resting(self):
        # A whole grade rests in the same week for a public holiday. While the
        # exporter wrote `bye` and the page read `bye`, that week showed one team
        # at rest and silently swallowed the others.
        code = jslint.code_only(self.assets()["app.js"])
        self.assertRegex(code, r"r\.byes\b[\s\S]{0,80}forEach",
                         "nothing iterates a round's byes, so a second resting "
                         "team cannot appear")
        self.assertNotRegex(code, r"\br\.bye\b",
                            "the page still reads the singular field the "
                            "exporter stopped writing")


class TestANightThatCountedForNothingDoesNotReadLikeAResult(WebAssetCase):
    """A washed-out night, drawn by the same code as a win, and louder than one.

    ``matchCard`` printed the games in a ``.score`` span whose only rule is
    ``font-weight: 600`` in white, and said which night it was with the document's own
    lowercase token pushed third of four into a 12px ``--text-muted`` run: "sets 2-0 ·
    points -- / -- · cancelled · 1 note". Measured in a 360x640 viewport
    (``/tmp/p310/washrank.py``, which walks every element of every
    ``details.match > summary`` and reports those whose own computed weight is >= 600)
    over all fifteen rows of the demo: a played row and a forfeited row carry **two**
    heavy elements, the winner's name and the score; the cancelled row carried
    exactly **one**, the scoreline, because with ``result: "none"`` there is no
    winner's name to share the billing. So the one number on the page that is in no
    figure anywhere was the loudest thing on its row, and uniquely so.

    After: that row reports **zero** heavy elements, its score computes to weight 400
    in ``rgb(148, 146, 139)`` (``--text-muted``), and its run reads "cancelled --
    counts for nothing · sets 2-0 · ...". The other fourteen rows are unchanged at
    two apiece.

    A 0-0 washout hides the cost. The case that matters, and the one the README
    describes, is the night that stops after two rubbers -- which is now what the
    demo writes, because until this item it wrote the all-zero card and nobody
    checking out this repo could see the problem or the fix. It prints 12-5 in bold
    with "sets 2-0" beside it, and at a glance that is a two-rubber win.

    Not a game, not a set and not an appearance from it is counted anywhere, measured
    by building the same season with and without the card: every field of every team
    byte-identical, every numeric field of every player identical, ``head_to_head``
    identical. The one aggregate that did move was each named player's
    ``rating_history``, which gained a round-5 entry at the rating they already had.
    That was read here as the exception that shows the rule; item 4.44 found it was a
    figure fed by a roster block no check reads, and since then every field of every
    player is identical with and without the card as well.

    The fix is words as well as weight, because a weight is not a statement.
    """

    # The three nouns are the README's own list, and the sentence needs all three:
    # a team captain reads "not a game", a player checking their own record reads
    # "not an appearance", and the two are counted in different places.
    NOUNS = ("not a game", "not a set", "not an appearance")

    def test_the_card_asks_the_shared_predicate_whether_the_night_counted(self):
        # `countsAsPlayed` reads Match.counts_as_played off the document, which is what
        # produced every aggregate in the document, so a row that asks anything else
        # can contradict the ladder beside it. Two plausible wrong questions: a hand
        # comparison against 'cancelled', which misses 'unplayed'; and
        # `m.counts_for_ladder`, which export.py derives from stats.not_counted_reasons
        # -- conflict, duplicate, names, level, finals, and no washout among them --
        # so it is *true* on precisely the card this class is about, and a guard on it
        # is a silent no-op.
        # Comments blanked, because the comment above the line under test names the
        # field this test forbids -- naming it is how the source records why it is the
        # wrong question. A check that read the comments would fail on the explanation
        # of the thing it is checking for.
        code = jslint.code_only(self.body("matchCard"))
        # With the sense of the answer, not merely the call: `unscored` is the
        # negation, and dropping the `!` turns every card that did count into one
        # that says it counted for nothing -- fourteen rows wrong instead of one,
        # and nothing else in this class can see it.
        self.assertIn("var unscored = !countsAsPlayed(m)", code,
                      "matchCard() never asks whether the night counted, so it "
                      "cannot tell a washout from a win")
        self.assertNotIn("counts_for_ladder", code,
                         "matchCard() guards on counts_for_ladder, which export.py "
                         "leaves true on a cancelled card")

    def test_the_scoreline_stops_being_the_loudest_thing_on_such_a_row(self):
        # Read from the raw source, so the class the browser gets is the class under
        # test rather than a name this file repeats.
        body = self.body("matchCard")
        # re.search and assertTrue rather than assertRegex, which prints the whole
        # function it did not match and buries this sentence under three kilobytes
        # of it.
        span = re.search(r"class:\s*unscored\s*\?\s*'score score--unscored'"
                         r"\s*:\s*'score'", body)
        self.assertTrue(span, "the score span still takes one unconditional class, "
                              "so a night that counts for nothing is printed in the "
                              "weight of a win")
        demoted = [r for r in csslint.rules(self.assets()["styles.css"])
                   if "score--unscored" in r.selector]
        self.assertTrue(demoted, "no rule paints the demoted score, so the class the "
                                 "script emits changes nothing a reader sees")

        def settled(prop: str) -> str | None:
            """What the browser ends up with for *prop*, across however many rules.

            Last writer wins, which is what the cascade does with rules of equal
            specificity in one file. Read this way rather than by demanding a single
            rule: the weight and the colour written as two rules render identically,
            and a test that failed on that would be pinning the house's punctuation.
            """
            values = [r.value(prop) for r in demoted if r.value(prop)]
            return values[-1] if values else None

        where = demoted[0].where
        self.assertEqual(settled("font-weight"), "400",
                         f"{where}: the class is emitted and the weight is not "
                         f"brought down, so nothing a reader sees changes")
        self.assertEqual(settled("color"), "var(--text-muted)",
                         f"{where}: painted with something other than the token the "
                         f"suite contrast-checks across all six palettes")
        # And only that row. The easy way to pass the assertion above is to quieten
        # every scoreline, which loses the win a decided row is announcing.
        base = [r for r in csslint.rules(self.assets()["styles.css"])
                if r.selector == ".match .score"]
        self.assertEqual(len(base), 1, "the base score rule has been renamed or split")
        self.assertEqual(base[0].value("font-weight"), "600",
                         "every scoreline has been quietened, not just the ones that "
                         "count for nothing")

    def test_the_reason_leads_the_meta_run_instead_of_trailing_the_figures(self):
        # It qualifies the sets and the points that follow it, and a qualifier after
        # the figures it qualifies is read after they have been believed. On a phone
        # the whole run is on its own full-width line under the score
        # (`.match .meta { margin-left: 0; width: 100%; }` below 700px, and the
        # summary is `flex-wrap: wrap`), so leading with it costs no horizontal room.
        body = self.body("matchCard")
        reason = self.offset_of(body, "if (uncounted) bits.push(",
                                "the meta run carries no reason")
        self.assertLess(reason, self.offset_of(body, "bits.push('sets ",
                                               "the meta run has stopped saying sets"),
                        "the reason a night counts for nothing is pushed after the "
                        "figures it applies to")

    def test_such_a_row_is_never_qualified_by_the_documents_own_word_alone(self):
        # WCAG 2.2 1.4.1 as a constraint on the fix rather than a charge against what
        # was there: the weight and the colour must never be the only new signal. And
        # `cancelled`, alone and lowercase, is the scorecard's vocabulary rather than
        # a statement to a reader -- it says what happened to the night, not what
        # became of the numbers printed beside it.
        body = self.body("matchCard")
        # Pushed as the words the helper gives, which are `''` on a night that
        # counted: two kinds of night count for nothing, and only one is a status.
        self.assertIn("if (uncounted) bits.push(uncounted);", body,
                      "nothing in the meta run is guarded on the night having "
                      "counted for nothing")
        pushed = re.search(r"var uncounted = (.+?);\n", body, re.S)
        self.assertIsNotNone(pushed, "the words pushed come from nowhere this test "
                                     "can follow")
        # Followed into the helper when the words are spelled in one: the season's
        # table of every rubber says the same thing on each row of such a night, and
        # one spelling shared is the point of it.
        words = pushed.group(1)
        helper = re.fullmatch(r"(\w+)\(m\)", words.strip())
        if helper:
            words = self.body(helper.group(1))
        self.assertIn("m.status", words,
                      "the row no longer says which of the document's words it was "
                      "marked with")
        self.assertIn("counts for nothing", words,
                      "the token is pushed with no words to say what it cost")
        # The bare token that used to be the whole qualification must now be the
        # other case's business only, or the row reads "cancelled ... cancelled".
        condition = self.guard_for("matchCard", "bits.push(m.status)",
                                   emits="bits.push")
        self.assertIn("!unscored", condition,
                      f"the bare status token is still pushed for a night that "
                      f"counts for nothing, which reads twice: {condition.strip()!r}")
        # And joined to the rest of the condition by `and`, which guard_for() refuses
        # anything else for: `!unscored || ...` reads as a guard, contains the word
        # this test looks for, and pushes the bare token on exactly the rows it was
        # meant to keep it off.

    def test_the_helper_gives_the_status_only_to_a_night_that_did_not_count(self):
        # Which nights get the status, and not only the words it comes with. The
        # test above follows the helper for its words alone, since the push came to
        # ask the words and no longer `unscored`. Measured with the status given to
        # the nights that counted instead, every played card and each of its rubbers
        # was said to count for nothing, `played` first, a washout lost its words,
        # and this class passed (/tmp/t4/scratch/f6/before/status-web-assets.log).
        # With the strings kept, so the status asked for as `m['status']` counts.
        said = re.sub(r"\s+", " ",
                      jslint.without_comments(self.body("uncountedWords")))
        self.assertEqual(len(re.findall(r"\bstatus\b", said)), 1,
                         "uncountedWords() names the status more than once, or "
                         "never: it is given twice, or asked as well as given, so "
                         "no one choice says which nights get it")
        # By the sense of the choice and not as the line is spelt, which is how the
        # class of a night two cards disagree about pins it: the arms the other way
        # round under a `!` are the same helper. A `?` is read here and an `if` by
        # guard_for(), which fails a status given under neither. The `?` is read
        # from the `=` that assigns it, so its condition is the call under its `!`s
        # and nothing else, and neither arm holds a `?` of its own. Read from the
        # `!`s next to the call alone, it passed `m.id || !countsAsPlayed(m) ?
        # [m.status] : []` and the swap under `! !`, and in all of test_web_assets
        # only 4.36's test of the line as spelt failed them
        # (/tmp/t4/scratch/f6/fixup/before.log).
        choice = re.search(r"(?<![=!<>])=\s*((?:!\s*)*)countsAsPlayed\(m\)\s*\?"
                           r"([^:;?]*):([^;?]*);", said)
        if choice:
            negated, counted, uncounted = choice.groups()
            if negated.count("!") % 2:
                counted, uncounted = uncounted, counted
            self.assertRegex(uncounted, r"\bstatus\b",
                             "uncountedWords() gives the status to the nights that "
                             "counted, so every played card counts for nothing")
        else:
            condition = self.guard_for("uncountedWords", "m.status", emits="push")
            self.assertEqual(condition.strip(), "!countsAsPlayed(m)",
                             f"uncountedWords() gives the status to nights that "
                             f"counted, or asks something else: {condition!r}")

    def test_the_opened_card_says_which_of_its_numbers_are_counted_anywhere(self):
        # In the body rather than the summary, because it is three clauses and the
        # summary is one line of a list. In the quiet register of the `Source:` line
        # three statements below it, not note('warning'): a warning means a human has
        # something to do, and rain is not a data fault.
        body = self.body("matchCard")
        # Equal, not `in`: "!unscored" contains "unscored", so a check for the word
        # passes on the inversion that prints this sentence on every card except the
        # one it is about.
        # And not on a washout card of a night two cards disagree about, whose round is
        # credited nothing under either preset. Measured under `unplayed = "average"`
        # on the demo with a washed-out second round 2 card: this sentence said the
        # round was credited the team's average, and the Wombats' row read 4 counted
        # and 20.7 points, as under "zero".
        condition = self.guard_for("matchCard", "counted anywhere")
        self.assertEqual(condition.strip(), "unscored && m.not_counted !== 'conflict'",
                         f"the sentence is not guarded on the night having counted "
                         f"for nothing, or is said of a clash, whose round is worth "
                         f"nothing under either preset: {condition!r}")
        sentence = re.search(r"text: 'Nothing on this card(.+?)\}\)\);", body, re.S)
        self.assertIsNotNone(sentence, "the sentence has been reworded past what "
                                       "this test can find")
        for noun in self.NOUNS:
            with self.subTest(noun=noun):
                self.assertIn(noun, sentence.group(1),
                              f"the sentence stops short of {noun!r}, which is the "
                              f"clause one kind of reader is looking for")
        # Under the table, not over it. The table is a list of real names with real
        # games beside them -- it is what the PDF prints -- so the sentence is a note
        # on it and not a heading over it.
        self.assertLess(self.offset_of(body, "table-wrap", "the card has no set table"),
                        self.offset_of(body, "counted anywhere",
                                       "the card says nothing about what counted"),
                        "the sentence is appended above the table it is about")

    def test_the_page_never_words_the_unplayed_rule_for_itself(self):
        # The rule is `[rules] unplayed`, and what it decides -- whether a round
        # nobody played is credited the team's own average or nothing -- is a sentence
        # scoring.py owns, because the run report prints it too. The page had its own
        # pair of strings in byeRow, and they had already drifted from it in substance
        # and not merely in wording: "scored at the team's own average" never says
        # what the round counts *in* (which is the whole point of the Python
        # docstring), and "no points, by this competition's rules" never says it is
        # not a match played. Both are now the exported clause.
        # Per function rather than over the whole file, because "own average" is in
        # app.js legitimately: it is in the `Counted` column's tooltip, which explains
        # what the points average divides by. That is a different sentence about a
        # different thing, and a file-wide sweep for the phrase condemns it.
        for fn in ("byeRow", "matchCard"):
            with self.subTest(fn=fn):
                # The raw body, *not* jslint.code_only(), which blanks string literals
                # along with comments -- and a re-typed sentence is a string literal,
                # so the tidier-looking check is vacuous. It was written that way first
                # and passed against an app.js still carrying both stale wordings.
                raw = self.body(fn)
                for stale in ("own average", "no points, by this"):
                    self.assertNotIn(stale, raw,
                                     f"{fn}() words the unplayed rule itself "
                                     f"({stale!r}); scoring.unplayed_worth() is where "
                                     f"that sentence lives, and these two copies of "
                                     f"it had already drifted from it")
                code = jslint.code_only(raw)
                self.assertIn("unplayed_worth", code,
                              f"{fn}() does not read the exported clause")
                # The structural half, which no rewording slips past: a caller that
                # branches on the flag is deciding the rule again, whatever words it
                # reaches for. `average_unplayed` is the flag; the clause is the rule
                # already applied. Asked of the code, so the comment above byeRow's
                # line can name the flag it is explaining the absence of.
                self.assertNotIn("average_unplayed", code,
                                 f"{fn}() branches on the raw flag, which is how it "
                                 f"comes to have wording of its own to keep in step")

    def test_the_clause_the_page_prints_is_one_the_exporter_writes(self):
        # Passes today, and is the half the page cannot enforce: a field read under a
        # name nothing writes is `undefined`, which renders as the word "undefined"
        # in the middle of the sentence rather than as an error.
        self.assertIn("unplayed_worth", _rules_block())


class TestTheRubbersInsideSuchACardAreQuietenedWithItsRow(WebAssetCase):
    """The row above was demoted and the table it opens onto kept the weight of a win.

    The class above quietened the scoreline on the *row* of a night that counts for
    nothing. This is the card that row opens. Measured at 900px with
    :file:`/tmp/p317/cells.py`, which reports every element inside a ``details.match``
    whose **own** computed weight is 600 or more -- own and not inherited, for the reason
    that probe's predecessor gives -- on the washed-out night of the demo season, whose
    two decided rubbers are the case the README describes:

    ===================================  ================================  ==============
    element                              before                            after
    ===================================  ================================  ==============
    the summary's scoreline              already 400, ``--text-muted``     unchanged
    ``td.win``, the two decided rubbers  **600, rgb(255,255,255)**         400, unchanged
    ``td.lose``, the other six cells     400, ``--text-secondary``         unchanged
    the parse note's ``!`` and label     700                               unchanged
    ===================================  ================================  ==============

    So after the row above was fixed, those two cells were the only *figures* in the card
    still set in the weight of a result. What else the probe finds heavy in there is not a
    figure: ten ``<th>``, which are the table's furniture, and the icon and ``Parse note:``
    label of the caveat this card happens to carry, which are a warning announcing itself
    and are meant to be loud. A played card and the forfeited card carry two heavy elements
    each and four ``td.win`` at 600, as they did before and still do.

    Demoted and not unmarked. Somebody did win that rubber and the scorecard prints it,
    so the cell keeps its ``win`` class and its colour; what stops is its being the
    heaviest thing in a card whose numbers are counted nowhere. The pair is still told
    apart twice over -- ``--text-secondary`` against the body colour, and the figures
    themselves, 6 against 0 -- so nothing here is left resting on a weight.

    **The twelve links stay.** Every name on the card is a link to a player page where the
    night does not appear, which is the one thing about this card that cannot be fixed by
    quietening it: a reader will follow the link looking for the night. It is answered in
    words instead, by the sentence the class above pins -- "not a game, not a set, not an
    appearance" -- which the probe finds **27px below the table**, with "counts for
    nothing" **16px above it**. Both are emitted on the same ``unscored`` that demotes
    these cells, so a card can be quiet and silent only if that class fails too.

    **And the Status column stays about the rubber**, which is the alternative this was
    weighed against. It shows an em dash on exactly the two rows that were played to a
    finish, because ``st.status`` is ``played`` and there is nothing unusual *about the
    rubber* to report. Writing the night's business there instead would say it a third
    time, four rows deep, in the one column whose header promises the rubber's own -- and
    could not be done consistently: the other two rows already say ``Incomplete``, which
    is about the rubber, and a column reading "Not counted, Not counted, Incomplete,
    Incomplete" invites the reading that the incomplete ones were counted.
    """

    # The night, asked either way round: named as the card names it one element up, or
    # called where it is used. Which of those a function picks is its own business, and a
    # test that only knew the first would be pinning a local variable's name.
    NIGHT = r"(?:unscored|!countsAsPlayed\(m\))"
    # The winner's cell, in the shape the class above uses for the same demotion one
    # element up: a compound literal chosen on the night, so the modifier is added to the
    # class rather than replacing it. The polarity is read as written -- there is no JS
    # engine on this machine, so `? 'win X' : 'win'` is the only place the sense of the
    # answer is legible, and a card that has the two branches the wrong way round is one
    # that quietens every night except the ones that count for nothing.
    WINNER = NIGHT + r"\s*\?\s*'win ([\w-]+)'\s*:\s*'win'"

    def modifier(self) -> str:
        """The extra class a winner's cell carries on a night that counts for nothing.

        Read out of ``app.js`` rather than spelled here, so the sheet and the script are
        checked against each other rather than both against this file.
        """
        found = re.search(self.WINNER, self.body("setRow"))
        self.assertIsNotNone(found, "the winner's games cell takes one unconditional "
                                    "class, so the two decided rubbers of a washed-out "
                                    "night are printed in the weight of a win -- which, "
                                    "since the row above them was quietened, makes them "
                                    "the loudest content in the card")
        return found.group(1)

    def demoted(self) -> list:
        """The rules that paint that class."""
        name = self.modifier()
        rules = [r for r in csslint.rules(self.assets()["styles.css"])
                 if "." + name in r.selector]
        self.assertTrue(rules, f"nothing in the sheet paints .{name}, so the class the "
                               f"script emits changes nothing a reader sees")
        return rules

    def settled(self, prop: str) -> str | None:
        """What the browser ends up with for *prop*, across however many rules.

        Last writer wins, as the cascade does among rules of one specificity in one
        file. Read this way rather than by demanding a single rule, for the reason the
        class above gives: the weight and a selector written as two rules render
        identically, and failing on that would be pinning the house's punctuation.
        """
        values = [r.value(prop) for r in self.demoted() if r.value(prop)]
        return values[-1] if values else None

    def test_the_set_table_asks_the_night_the_same_way_the_card_does(self):
        # Through the shared predicate, for the reason the class above gives at length:
        # a hand comparison against 'cancelled' misses 'unplayed', and
        # `counts_for_ladder` is *true* on precisely this card. With the sense of the
        # answer, not merely the call -- without the `!` every card that did count is
        # printed as one that counted for nothing.
        code = jslint.code_only(self.body("setRow"))
        self.assertIn("!countsAsPlayed(m)", code,
                      "setRow() never asks whether the night counted, so it cannot "
                      "tell a rubber that earned something from one that did not")
        self.assertNotIn("counts_for_ladder", code,
                         "setRow() guards on counts_for_ladder, which export.py leaves "
                         "true on a cancelled card")

    def test_the_winners_cell_is_quietened_and_not_unmarked(self):
        # The wrong fix that passes a weight check: drop the `win` class on such a card
        # and mark both sides `lose`. The rubber had a winner -- that is what the PDF
        # says -- and a table that denies it is no longer the scorecard.
        body = self.body("setRow")
        self.assertRegex(body, r"st\.winner === 'home' \?",
                         "the home cell no longer takes its class from whoever won the "
                         "rubber")
        self.assertNotRegex(body, self.NIGHT + r"\s*\?\s*'lose'",
                            "a winning cell is marked as a losing one on a night that "
                            "counts for nothing, which is not what the scorecard says")
        self.assertTrue(self.modifier().startswith("win"),
                        f"the demoting class is {self.modifier()!r}, which does not read "
                        f"as a kind of win; the class above spells this one "
                        f"`score--unscored` on the element directly overhead")

    def test_the_sheet_brings_that_cell_below_the_weight_of_a_result(self):
        # Compared as numbers rather than matched against '400': what has to hold is
        # that the cell is lighter than a counted win, and any weight that is would do.
        base = [r for r in csslint.rules(self.assets()["styles.css"])
                if r.selector == ".win"]
        self.assertEqual(len(base), 1, "the base win rule has been renamed or split")
        counted = base[0].value("font-weight")
        self.assertEqual(counted, "600",
                         "every winning cell on every page has been quietened, not just "
                         "the ones on a night that counts for nothing")
        quiet = self.settled("font-weight")
        self.assertTrue(quiet and int(quiet) < int(counted),
                        f"{self.demoted()[0].where}: .{self.modifier()} is emitted and "
                        f"its weight is {quiet!r} against the {counted!r} of a counted "
                        f"win, so nothing a reader sees changes")

    def test_the_two_cells_are_still_told_apart_once_the_weight_is_gone(self):
        # What is left carrying the rubber's result: the colour of the pair, and the
        # figures. Repainting the winner's cell as well would leave the two cells
        # identical and the result in the numbers alone -- which is legible, and is also
        # the card quietly dropping something the scorecard states.
        self.assertIsNone(self.settled("color"),
                          f"{self.demoted()[0].where}: the demotion repaints the cell as "
                          f"well as lightening it, so the winner's cell and the loser's "
                          f"are now the same and only the games say which was which")
        lose = [r for r in csslint.rules(self.assets()["styles.css"])
                if r.selector == ".lose"]
        self.assertTrue(lose and lose[0].value("color"),
                        "the losing cell has stopped being painted differently, so "
                        "with the weight gone the two cells are identical")

    def test_a_name_on_such_a_card_is_still_a_link_to_the_player(self):
        # The row that filed this said the links have to stay, and this is why: the
        # reader following one is looking for the night, and finding no link at all
        # leaves them with nowhere to go and nothing said. What is owed them is the
        # sentence the class above pins, not a dead name.
        body = self.body("setRow")
        # Bounded by the helper's own last line rather than by whatever declaration
        # happens to follow it: this item adds two, and a slice that ends at the next
        # `var` would read them as part of the helper and fail on the fix itself.
        names = body[self.offset_of(body, "function names(", "the helper that prints "
                                    "the names down a side"):
                     self.offset_of(body, "return wrap;", "the end of that helper")]
        self.assertIn("playerLink(", names,
                      "the names on a card are no longer links to the players")
        for asked in ("unscored", "countsAsPlayed"):
            with self.subTest(asked=asked):
                self.assertNotIn(asked, names,
                                 f"whether a name is a link now depends on {asked}, so a "
                                 f"reader on an uncounted card is given a dead name "
                                 f"instead of a page that says the night is in nobody's "
                                 f"record")

    def test_the_status_column_stays_the_rubbers_own_business(self):
        # The alternative this item was weighed against, recorded as a test because the
        # em dash on those two rows looks like an oversight and the next reader will
        # reach for it. The column answers "what happened to this rubber, if not simply
        # played", and the night is answered twice on the card already -- 16px above the
        # table and 27px below it. A per-row answer would be a third, and could not be
        # made consistent with the `Incomplete` the same column prints two rows down.
        body = self.body("setRow")
        line = re.search(r"var status = (.+?);\n", body, re.S)
        self.assertIsNotNone(line, "setRow() no longer computes a status for the column")
        expression = line.group(1)
        # Read as "does this expression consult the night at all", not as "does it say
        # `m.`": the first way this gets reached for is a call rather than a field, and
        # `countsAsPlayed(m)` names no property. Both the match and the answer already
        # taken from it are out, since the second is only the first under another name.
        for asked, how in ((r"\bm\b", "the match"), (r"\bunscored\b", "the night")):
            with self.subTest(asked=asked):
                self.assertNotRegex(expression, asked,
                                    f"the Status column now reads from {how}, so the "
                                    f"column whose header promises the rubber's own "
                                    f"business is answering for the night: "
                                    f"{expression.strip()!r}")
        # Row 4.453 moved this: the word is now rubberStatus(st)'s, and an expression
        # that is a call passes the lines above without its answer being read. So every
        # function of app.js the expression calls is read too, all of it, and asked the
        # same question with the call this file's other checks know the night by. A
        # function handed the set alone can still reach for the match one call down.
        source = self.assets()["app.js"]
        called = sorted(set(re.findall(r"(?<![\w$.])([A-Za-z_$][\w$]*)\s*\(",
                                       jslint.code_only(expression))))
        for name in (n for n in called if f"\nfunction {n}(" in source):
            code = jslint.code_only(self.body(name))
            for asked, how in ((r"\bm\b", "the match"), (r"\bunscored\b", "the night"),
                               (r"\bcountsAsPlayed\b", "whether the night counted")):
                with self.subTest(called=name, asked=asked):
                    self.assertNotRegex(code, asked,
                                        f"{name}(), which the Status column takes its "
                                        f"word from, now reads {how}, so the column "
                                        f"whose header promises the rubber's own "
                                        f"business is answering for the night")


class TestARubberWithNoGamesOnItSaysNoScoreRatherThanUnfinished(WebAssetCase):
    """'Unfinished' of a row with no games on it, a rubber nobody started.

    The match card's Status column said 'unfinished' of every row stored as played that
    was not a finished set, which is the one set ``SetResult.unfinished_unmarked`` asks
    about. validate splits that predicate three ways, set-no-games, set-tied and
    set-unfinished, because a row with no games on it is not a contest that stopped: it
    is a rubber nobody played. The report was corrected and the page never was, and the
    cell is on every match card, and in the season's table of rubbers and its CSV
    through ``rubberRow``.

    Measured on eight cardwriter cards built at a pinned stamp and dumped in headless
    Chrome (/tmp/mcf/scratch/UNFIN/probe/probe.py): nine rows with no games read
    'Unfinished' -- the last doubles of a played card beside its set-no-games warning,
    the four rows of a card ticked Match Cancelled and the four of a whole-match forfeit
    -- and eleven cells of the table of every rubber did, those nine and the 5-3 and 4-4
    that did stop short. On the club's own season it is 95 rows: 59 on cancelled cards,
    24 on blank cards nobody entered and 12 the last rubber of a played card, beside 64
    rows with games that stopped short, of which the word is true. After, the nine read
    'No score', and the 5-3, the 4-4, the Incomplete, Retired and Forfeit rows and the
    finished rows' em dashes read as they did.

    The word is ``rubberStatus(st)``'s, one function of the set alone, which the player
    page asks too (4.454). It is read here by a reader that evaluates it as a browser
    would, since the suite runs no JavaScript and which word sits on which arm of a
    ternary is the whole of this item, and it is put rows the exporter wrote, with every
    field the page is handed. Rejected: 'Not played', true of a called-off card or a
    walkover but not shown by a played card whose last row is empty, which set-no-games
    offers only as what such a row reads like; 'No games', which reads as a score of
    nothing each, the misreading being fixed; 'Cancelled', 'Forfeit' or 'Walkover' from
    the match, which is 3.17's refusal, held by the class above; a field in the export
    naming the rows nobody played, the same refusal by another route; an em dash, which
    3.17 keeps for a rubber played to a finish; and 'Level' for a 4-4, of which
    'Unfinished' is true.
    """

    # What the reader below can read: the set's own fields, the operators the helper is
    # written in, string and number literals, and the brackets and keywords of
    # `if (...) return`.
    TOKEN = re.compile(r"\s+|'[^'\\]*'|===|!==|&&|\|\||[<>]=?|[!?:;()]|\d+"
                       r"|[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)?")
    LITERALS = {"true": True, "false": False, "null": None}
    # Loosest first, as JavaScript binds them: a ternary is looser than all of these, and
    # `!` is tighter.
    BINARY = (("||",), ("&&",), ("===", "!=="), ("<", ">", "<=", ">="))

    # Cards as the model holds them, each row (home games, away games, its box, the word
    # it is owed), put through the exporter, so that a row reaches the helper as the page
    # is handed it: its slot, label and doubles, both sides' names as the lists they are,
    # its winner, completed and margin. A helper asking any of those beyond the four the
    # rule needs is answered as a browser answers it. A box is a side whose Forfeit is
    # ticked, or `retired` or `incomplete`. The rows nobody played are an empty doubles
    # and an empty singles, named on both sides and on one, since a helper that took a
    # name or a doubles for a rubber that was started reads them as unfinished; and every
    # pair of games is asked both ways round, since a helper that asks one side's games
    # reads 0-3 as a row nobody played.
    CARDS = (
        # A played card whose last doubles nobody played: the row set-no-games warns of.
        ("played", {}, ("home", "away"),
         ((6, 2, "", ""), (6, 3, "", ""), (3, 6, "", ""), (0, 0, "", "no score"))),
        # A card ticked Match Cancelled, every row empty and every name filled in.
        ("cancelled", {}, ("home", "away"), ((0, 0, "", "no score"),) * 4),
        # A whole-match forfeit with nobody named for the side that gave the night up.
        ("forfeit", {"forfeited_by": "away"}, ("home",), ((0, 0, "", "no score"),) * 4),
        # A washout, with games on two rows when the rain came and none on the last.
        ("cancelled", {}, ("home", "away"),
         ((6, 4, "", ""), (3, 0, "", "unfinished"), (0, 3, "", "unfinished"),
          (0, 0, "", "no score"))),
        # The 9 p.m. finish, short of a set and level, beside two sets played out.
        ("played", {}, ("home", "away"),
         ((5, 3, "", "unfinished"), (6, 1, "", ""), (2, 6, "", ""),
          (4, 4, "", "unfinished"))),
        # Each box a card can tick, on a row with games and on a row without.
        ("played", {}, ("home", "away"),
         ((2, 6, "away", "forfeit"), (0, 0, "home", "forfeit"),
          (4, 2, "retired", "retired"), (0, 0, "incomplete", "incomplete"))),
    )

    @staticmethod
    def truthy(value) -> bool:
        """Whether a browser takes *value* for true.

        A list or an object always is, empty or not, where Python takes an empty one for
        false: the side of a walkover with nobody named is `[]`, and a helper that asks
        it is answered as the page would answer it.
        """
        return isinstance(value, (list, dict)) or bool(value)

    @staticmethod
    def same(one, other) -> bool:
        """``===``: a list or an object is only itself, and a number is no boolean."""
        if isinstance(one, (list, dict)) or isinstance(other, (list, dict)):
            return one is other
        kinds = {"number" if isinstance(v, (int, float)) and not isinstance(v, bool)
                 else type(v).__name__ for v in (one, other)}
        return len(kinds) == 1 and one == other

    def evaluate(self, node, row):
        """What *node*, as :meth:`expression` read it, gives for *row*."""
        kind = node[0]
        if kind == "field":
            return row.get(node[1])
        if kind == "value":
            return node[1]
        if kind == "!":
            return not self.truthy(self.evaluate(node[1], row))
        if kind == "?":
            asked = self.truthy(self.evaluate(node[1], row))
            return self.evaluate(node[2] if asked else node[3], row)
        one = self.evaluate(node[1], row)
        # `||` and `&&` give back the operand that decided, as a browser's do.
        if kind == "||":
            return one if self.truthy(one) else self.evaluate(node[2], row)
        if kind == "&&":
            return self.evaluate(node[2], row) if self.truthy(one) else one
        other = self.evaluate(node[2], row)
        if kind in ("===", "!=="):
            return self.same(one, other) == (kind == "===")
        numbers = [isinstance(v, (int, float)) and not isinstance(v, bool)
                   for v in (one, other)]
        self.assertTrue(all(numbers), f"rubberStatus() compares {one!r} {kind} "
                                      f"{other!r}, and this reader compares numbers only")
        return {"<": one < other, ">": one > other, "<=": one <= other,
                ">=": one >= other}[kind]

    def expression(self, words: list[str], param: str):
        """One expression of rubberStatus(), read by precedence as JavaScript reads it.

        A ternary, which may hold another on either arm or in brackets anywhere, then
        `||`, `&&`, the comparisons and `!`, each a node of the tuple :meth:`evaluate`
        walks. A name that is not one of the set's own fields is refused by name, and a
        word this reader has no rule for is refused as one it cannot read, so neither is
        put down to the other: a helper that reaches past its set fails on the name it
        reached for, and one written in more than this reader knows fails on that.
        """
        said, at = " ".join(words), 0

        def peek():
            return words[at] if at < len(words) else None

        def take(expected=None):
            nonlocal at
            word = peek()
            if word is None or expected is not None and word != expected:
                self.fail(f"rubberStatus() says {said!r}, which this reader cannot read: "
                          f"{word or 'its end'!r} where {expected or 'more'!r} would be")
            at += 1
            return word

        def choice():
            asked = binary(0)
            if peek() != "?":
                return asked
            take("?")
            yes = choice()
            take(":")
            return ("?", asked, yes, choice())

        def binary(level):
            if level == len(self.BINARY):
                return unary()
            left = binary(level + 1)
            while peek() in self.BINARY[level]:
                left = (take(), left, binary(level + 1))
            return left

        def unary():
            word = take()
            if word == "!":
                return ("!", unary())
            if word == "(":
                inside = choice()
                take(")")
                return inside
            if word.startswith(param + "."):
                return ("field", word[len(param) + 1:])
            if word[0] == "'":
                return ("value", word[1:-1])
            if word.isdigit():
                return ("value", int(word))
            if word in self.LITERALS:
                return ("value", self.LITERALS[word])
            if re.fullmatch(r"[A-Za-z_$][\w$.]*", word):
                self.fail(f"rubberStatus() asks {word!r}, which is not the set it was "
                          f"handed, so the word is no longer the rubber's own")
            self.fail(f"rubberStatus() says {said!r}, which this reader cannot read: "
                      f"{word!r} where a value would be")

        if not words:
            return ("value", None)
        read = choice()
        if peek() is not None:
            self.fail(f"rubberStatus() says {said!r}, which this reader cannot read: "
                      f"{peek()!r} after a whole expression")
        return read

    def rubber_status(self):
        """rubberStatus() read off its source, as a function to put rows to.

        A structural check of which word sits on which arm is satisfied by a condition
        written the wrong way round, so the helper is read token by token and evaluated:
        `if (...) return ...;` lines and a last `return`, each expression read by
        :meth:`expression`. Anything else is refused and named rather than guessed at,
        so a helper that has grown past this reader fails until the reader is taught it.
        """
        said = jslint.without_comments(self.body("rubberStatus"))
        head = re.match(r"\s*function rubberStatus\(\s*([A-Za-z_$][\w$]*)\s*\)\s*\{", said)
        self.assertIsNotNone(head, "rubberStatus() is no longer a function of the one set "
                                   "it is asked about")
        param, inside = head.group(1), said[head.end():said.rindex("}")]
        tokens, at = [], 0
        while at < len(inside):
            found = self.TOKEN.match(inside, at)
            self.assertIsNotNone(found, f"rubberStatus() says {inside[at:at + 20]!r}, which "
                                        f"this reader cannot read")
            if not found.group(0).isspace():
                tokens.append(found.group(0))
            at = found.end()

        statements, current = [], []
        for token in tokens:
            if token == ";":
                statements.append(current)
                current = []
            else:
                current.append(token)
        self.assertFalse(current, "rubberStatus() ends in a statement with no ';'")
        steps = []
        for words in statements:
            if words[:2] == ["if", "("]:
                depth = 0
                for close, word in enumerate(words):
                    depth += {"(": 1, ")": -1}.get(word, 0)
                    if depth == 0 and close > 1:
                        break
                self.assertEqual(words[close + 1:close + 2], ["return"],
                                 "an if in rubberStatus() does not return")
                steps.append((self.expression(words[2:close], param),
                              self.expression(words[close + 2:], param)))
            else:
                self.assertEqual(words[:1], ["return"], f"rubberStatus() says "
                                 f"{' '.join(words)!r}, which this reader cannot read")
                steps.append((None, self.expression(words[1:], param)))

        def rubber_status(row):
            for asked, given in steps:
                if asked is None or self.truthy(self.evaluate(asked, row)):
                    return self.evaluate(given, row)
            return None
        return rubber_status

    def rows(self) -> list[tuple[dict, str]]:
        """Every row of CARDS as the exporter writes it, beside the word it is owed."""
        from matchcentre import export, format as fmt_mod, scoring
        from matchcentre.competition import Club, Competition
        from matchcentre.model import Match, MatchStatus, SetResult, SetStatus
        comp = Competition(id="x", day="Tuesday", division="C",
                           fmt=fmt_mod.match_format("doubles-singles-doubles"),
                           rules=scoring.ruleset("games-bonus"))
        matches, owed = [], {}
        for number, (status, ticked, named, rows) in enumerate(self.CARDS, start=1):
            sets = []
            for slot, (home, away, box, word) in zip(comp.fmt.slots, rows):
                names = {side: tuple(f"{side}-{slot.key}-{n}"
                                     for n in range(1 + slot.doubles))
                         if side in named else () for side in ("home", "away")}
                sets.append(SetResult(
                    slot=slot, home_games=home, away_games=away,
                    home_players=names["home"], away_players=names["away"],
                    status=(SetStatus(box) if box in ("retired", "incomplete") else
                            SetStatus.FORFEIT if box else SetStatus.PLAYED),
                    rule=comp.fmt.set_rule,
                    home_forfeit=box == "home", away_forfeit=box == "away"))
                owed[str(number), slot.key] = word
            matches.append(Match(competition="x", round=str(number), home="a", away="b",
                                 fmt=comp.fmt, rules=comp.rules, sets=tuple(sets),
                                 status=MatchStatus(status), match_id=f"id{number}",
                                 **ticked))
        doc = export.build_season(
            matches, competition=comp, source_dir=Path("."), generated="",
            club=Club(name="", competitions=(comp,)), cards=[], rejected=[])
        found = [(row, owed[match["round"], row["slot"]])
                 for match in doc["matches"] for row in match["sets"]]
        self.assertEqual(len(found), len(owed), "the exporter wrote other rows than the "
                                                "cards above hold")
        return found

    def put(self, which):
        word = self.rubber_status()
        rows = [(row, owed) for row, owed in self.rows() if which(owed)]
        self.assertTrue(rows, "no row of the cards above is owed what this test asks")
        for row, owed in rows:
            got = word(row)
            named = [side for side in ("home", "away") if row[f"{side}_players"]]
            sides = ("neither side", f"the {named[0]} side alone" if named else "",
                     "both sides")[len(named)]
            with self.subTest(row=f"{row['slot']} {row['home_games']}-"
                                  f"{row['away_games']} {row['status']}"):
                self.assertEqual(got, owed, f"a {row['label']} at {row['home_games']}-"
                                            f"{row['away_games']} stored {row['status']!r}"
                                            f"{' and finished' if row['completed'] else ''}"
                                            f", with names on {sides}, reads {got!r}, "
                                            f"where it is owed {owed!r}")

    def test_the_match_card_takes_its_word_from_the_one_function(self):
        body = self.body("setRow")
        line = re.search(r"var status = (.+?);\n", body, re.S)
        self.assertIsNotNone(line, "setRow() no longer computes a status for the column")
        self.assertEqual(line.group(1).strip(), "rubberStatus(st)",
                         "setRow() works the Status column's word out for itself again, "
                         "so the match card and a player's list can come to give two "
                         "words for one rubber")
        # And prints it capitalised, as a player's list does: one word, spelt one way,
        # first in the cell, before the games on court beside it (4.421).
        said = jslint.without_comments(body)
        self.assertRegex(said, r"var words = \[status, onCourt\(st\)\]",
                         "the Status column's word is no longer the first of the cell")
        self.assertRegex(said,
                         r"\{\s*text\s*:\s*words\s*\?\s*upperFirst\(words\)\s*:",
                         "the match card no longer prints the Status column's word "
                         "capitalised, as a player's list prints it, so the two pages "
                         "spell one rubber's word two ways")

    def test_a_row_with_no_games_on_it_reads_no_score(self):
        self.put(lambda owed: owed == "no score")

    def test_every_other_row_keeps_the_word_it_had(self):
        self.put(lambda owed: owed != "no score")

class ResultCellCase(WebAssetCase):
    """Shared reading of the Result cell of a player's Every set played list.

    The cell's text and class, each a ternary on whether the set was decided, split at
    its own `?` and `:` over code_only text, so that a ternary inside brackets is one
    branch's business and not the cell's. Strings are kept and the space between them
    squeezed out, so a branch compares as the words it is written in.

    Each branch is held to that exact text on purpose, as this file holds the other
    branches it has no engine to run: a rewrite that keeps what the cell says fails
    here, and this is the place to rewrite the test along with it.
    """

    SQUEEZE = re.compile(r"'[^'\\]*'|\s+")

    def result_cell(self) -> dict[str, str]:
        """The cell's properties, by name: the last object of the row before 'Result'."""
        body = self.body("renderPlayer")
        code, said = jslint.code_only(body), jslint.without_comments(body)
        header = said.find("'Result'")
        self.assertGreater(header, 0, "renderPlayer() has no Result column")
        cells = [found.start() for found in re.finditer(r"\{\s*text\s*:", code[:header])]
        self.assertTrue(cells, "renderPlayer() builds no cell with words of its own")
        opens = cells[-1]
        closes = closing_bracket(code, opens)
        inside, words = code[opens + 1:closes] + ",", said[opens + 1:closes] + ","
        found, depth, start = {}, 0, 0
        for at, char in enumerate(inside):
            depth += {"(": 1, "[": 1, "{": 1, ")": -1, "]": -1, "}": -1}.get(char, 0)
            if char == "," and depth == 0:
                colon = inside.index(":", start)
                found[inside[start:colon].strip()] = self.SQUEEZE.sub(
                    lambda f: f.group(0) if f.group(0)[0] == "'" else "",
                    words[colon + 1:at])
                start = at + 1
        self.assertEqual(sorted(found), ["cls", "text"],
                         "the Result cell is no longer a text and a class")
        return found

    def branches(self, value: str, what: str) -> tuple[str, str, str]:
        """*value*'s condition, the branch for a decided set and the other one."""
        code = jslint.code_only(value)
        depth, ask, inner = 0, None, 0
        for at, char in enumerate(code):
            depth += {"(": 1, "[": 1, "{": 1, ")": -1, "]": -1, "}": -1}.get(char, 0)
            if depth or char not in "?:":
                continue
            if char == "?":
                if ask is None:
                    ask = at
                else:
                    inner += 1
            elif ask is not None and inner:
                inner -= 1
            elif ask is not None:
                parts = value[:ask], value[ask + 1:at], value[at + 1:]
                self.assertEqual(parts[0], "s.decided", f"the Result cell's {what} is no "
                                 f"longer chosen on whether the set was decided: {value!r}")
                return parts
        self.fail(f"the Result cell's {what} is no longer chosen on whether the set was "
                  f"decided: {value!r}")


class TestASetNobodyWonReadsOnThePlayersPageAsOnTheMatchCard(ResultCellCase):
    """'played', in lowercase, beside a 5-3 and a 0-0 alike.

    A player's Every set played list fell back to ``s.set.status`` for a set with no
    winner, and to 'unfinished' only where that was empty -- but export writes every
    set's status as one of SetStatus's four values, so the fallback never ran, and an
    undecided set printed 'played', a word for how its score was recorded and not for
    what became of it, or 'retired' and 'incomplete' in the lowercase the match card
    capitalises. Measured on the probe's cards across all twelve players' pages
    (/tmp/mcf/scratch/UNFIN/probe/probe.py): 18 Result cells read 'played', ten at 0-0,
    four at 4-4 and four at 5-3, four read 'incomplete' and two 'retired', of rubbers
    their match cards called 'Unfinished', 'Incomplete' and 'Retired'. On the club's own
    season it is the 69 undecided sets of counted matches, 57 with games and 12 without,
    on every named player's page of each. After: 'No score' ten times, 'Unfinished'
    eight, 'Incomplete' four and 'Retired' twice, still muted, as each match card reads.

    Rejected: keeping 'played', which says nothing true of a 5-3 or a 0-0; 'Unfinished'
    for every such set, which is the dead fallback made live and puts back on the 0-0
    rows the word 4.453 takes off them; an em dash, which leaves no reason the set has
    no result; 'No result', true but less than the match card says, and a second word
    for one rubber; and dropping a played card's 0-0 rows from the list, which the
    player's figures count among sets played and the list has to keep.
    """

    def test_a_set_nobody_won_says_what_its_match_card_says_and_stays_muted(self):
        cell = self.result_cell()
        _, _, words = self.branches(cell["text"], "text")
        self.assertEqual(words, "upperFirst(rubberStatus(s.set))",
                         "a set nobody won no longer reads its match card's word, from the "
                         "function the match card asks, so the two pages can give one "
                         "rubber two words")
        _, _, painted = self.branches(cell["cls"], "class")
        self.assertEqual(painted, "'muted'", "a set nobody won is no longer muted, so "
                         "a rubber with no result is painted like one with a result")


class TestASetAForfeitTickDecidedSaysSoBesideWonOrLost(ResultCellCase):
    """A bare Won or Lost for a set a ticked box decided.

    The Result cell said the stored status only of a set with no winner, and a forfeited
    set has one, so its match card's 'Forfeit' never reached a player's list: on the
    probe's card with two rubbers forfeited (/tmp/mcf/scratch/UNFIN/probe/probe.py), six
    cells across four players' pages read 'Won' or 'Lost' alone, among them a 2-6 'Won'
    and a 6-2 'Lost', with nothing to say why. The club's own season has 5 forfeited
    rows. After: 'Won, forfeit' and 'Lost, forfeit', in the win and lose classes they had,
    and every other decided set reads Won or Lost alone, as it did.

    The word beside the result is ``rubberStatus``'s, '' for a set played to a finish,
    rather than a test of ``s.set.status === 'forfeit'``: should 4.422 export a retired
    rubber with a winner, the side that did not retire, as its row directs, that set
    reads 'Won, retired' as its match card reads 'Retired', not a bare result. The games
    beside it are the Games cell's, which 4.421 words. Rejected: 'Won by forfeit', since
    a row ticked against both sides goes by its games and not by a forfeit; the status
    in place of the result, which loses whether the player won or lost the set; and the
    list as it was.
    """

    def test_won_or_lost_carries_the_rubbers_own_word_in_the_class_it_had(self):
        cell = self.result_cell()
        _, words, _ = self.branches(cell["text"], "text")
        self.assertEqual(words, "[s.won?'Won':'Lost',rubberStatus(s.set)]"
                                ".filter(Boolean).join(', ')",
                         "a decided set no longer reads Won or Lost with its rubber's own "
                         "word after a comma, so a set a Forfeit tick decided reads as one "
                         "won or lost on court")
        _, painted, _ = self.branches(cell["cls"], "class")
        self.assertEqual(painted, "(s.won?'win':'lose')", "a decided set no longer "
                         "takes the win or lose class, so a forfeited set's result is "
                         "painted like one nobody won")


class TestARowWithNoCardSaysSoRatherThanScheduled(WebAssetCase):
    """"scheduled" for the rest of the season, over a round that has been played.

    ``scheduledRow`` is what the matches page prints where a fixture has no card, and
    it said the same word about two unlike things: a round in November nobody has
    reached, and last Tuesday's card that nobody exported. The round heading above it
    said "not played yet" while the rows either side of it were cards from that very
    round -- the page contradicting itself on one screen.

    Which of the two it is cannot be worked out here: it needs the whole draw and the
    clock-free rule that decides whether the season went past a round, and doing that
    in a second language is how two answers to one question start to differ. Python
    decides it (``card_missing`` on the fixture) and this only renders it.

    Structural, because there is no JavaScript engine on this machine to run the
    branch. What is checked is that both wordings exist, that they are chosen by the
    exporter's field and not by anything the page could guess at, and that the round's
    own pill agrees with the rows underneath it.
    """

    FIELD = "card_missing"

    def test_the_exporter_actually_puts_the_field_on_a_fixture(self):
        # Read off export.py rather than a document built here: the two wordings below
        # are chosen by a field that has to be in the file the page reads.
        self.assertIn(f'"{self.FIELD}"',
                      (ROOT / "matchcentre" / "export.py").read_text())

    def test_the_row_has_a_word_for_each_of_the_two_things(self):
        body = self.body("scheduledRow")
        self.assertIn("scheduled", body, "the row no longer says a fixture is "
                                         "scheduled, which is right for a round "
                                         "nobody has reached")
        self.assertRegex(body, r"no card", "the row says nothing different about a "
                                           "card that should have arrived")

    def test_the_row_chooses_between_them_on_the_exporters_answer(self):
        # Not on `fx.played`, which is false for both, and not on the date, which
        # would make the page's reading of a fixture depend on the day it is opened.
        # Read as one choice with both wordings in it rather than as a guard around
        # one of them, so which way round they are is pinned as well: swapping them
        # stops this matching.
        body = self.body("scheduledRow")
        chooser = re.search(r"([\w.]+)\s*\?\s*'no card yet'\s*:\s*'scheduled'", body)
        self.assertIsNotNone(chooser, "the row's two wordings are not one choice "
                                      "between them, so whichever is printed "
                                      "unconditionally is wrong about half of them")
        self.assertIn(self.FIELD, chooser.group(1))
        # The colour is the same answer and not a second one, because a grey row
        # reading "no card yet" and an amber one reading "scheduled" are both a
        # fixture the page has told a club two things about.
        styled = re.search(r"class:\s*([\w.]+)\s*\?", body)
        self.assertIsNotNone(styled, "the row's class is not a choice, so the "
                                     "wording is the only signal and it is 12px "
                                     "of grey")
        self.assertEqual(chooser.group(1), styled.group(1))

    def test_the_round_above_the_row_does_not_contradict_it(self):
        # The heading pill read "not played yet" for any round with no cards, which
        # over rows saying a card is missing is two answers on one screen. Both
        # locals are read off their own assignments rather than named here, so
        # renaming either is a rename and not a failure.
        body = self.body("renderMatches")
        counted = re.search(r"var (\w+) = [^\n]*\." + self.FIELD + r"[^\n]*;\n", body)
        self.assertIsNotNone(counted, "renderMatches() never asks how many of the "
                                      "round's fixtures are short a card, so its "
                                      "heading cannot tell a round the season went "
                                      "past from one it has not reached")
        chosen = re.search(r"var (\w+) = [^\n]*'not played yet'[^\n]*;\n", body)
        self.assertIsNotNone(chosen, "the heading's wording is no longer a choice")
        self.assertIn(counted.group(1), chosen.group(0),
                      "the heading chooses its wording without consulting the count")
        self.assertRegex(body, r"text:\s*" + chosen.group(1) + r"\s*\}",
                         "the wording is worked out and never put in the pill")
        # And its colour off the same count, for the reason the row's is: a grey pill
        # reading "no cards yet" is the heading saying one thing and showing another.
        pill = re.search(r"class: 'pill '[^\n]*", body)
        self.assertIsNotNone(pill, "the round heading has no pill to colour")
        self.assertIn(counted.group(1), pill.group(0),
                      "the pill's colour ignores the count its words are chosen by")

    def test_a_fixture_washed_out_says_so_before_either(self):
        # 4.535: a fixture the club declared washed out has no card and none to
        # come, so it is neither scheduled nor a card missing. Python says which
        # (`washed_out`), and the row chooses on it before the other two.
        body = self.body("scheduledRow")
        chooser = re.search(r"([\w.]+)\s*\?\s*'washed out'\s*:\s*\(?\s*([\w.]+)\s*\?"
                            r"\s*'no card yet'\s*:\s*'scheduled'", body)
        self.assertIsNotNone(chooser, "a fixture washed out is said to be scheduled, "
                                      "or a card missing")
        self.assertEqual(chooser.group(1), "fx.washed_out")
        self.assertEqual(chooser.group(2), "fx.card_missing")
        # And its hover says it was declared and no card is coming, and nothing of
        # what it earns: a final's washout earns nothing (F11), and the scoring
        # line's "credited the team's own average" was false of one (the review's
        # #1).
        self.assertRegex(body, r"title: fx\.washed_out\s*\?\s*'Declared washed out: "
                               r"no card of it is coming\.'",
                         "a fixture washed out is not told why it has no card")

    def test_neither_wording_ends_in_a_separator_with_nothing_after_it(self):
        # Measured: a round declared with no date and no card to take one from
        # printed "scheduled · " and stopped, which reads as something that failed
        # to load. Both wordings share the fault and the fix, so the date is a part
        # that may be absent rather than a string glued to a separator.
        body = self.body("scheduledRow")
        self.assertNotRegex(body, r"'(?:no card yet|scheduled) · '",
                            "the wording carries its own separator, so a fixture "
                            "with no date prints one with nothing after it")
        self.assertRegex(body, r"dotted\(\[[^\]]*longDate\(\w+\.date\)\]\)",
                         "the date is joined to the wording whether or not there "
                         "is one to print")

    def test_the_words_a_missing_card_is_shown_in_are_the_warning_ones(self):
        # A fault, not furniture: the same token the notes and pills use for
        # something a club has to act on. Measured against `--warning-text` rather
        # than a literal, so a theme that retunes the palette retunes this with it.
        found = [rule for rule in csslint.rules(self.assets()["styles.css"])
                 if ".missing" in rule.selector and rule.value("color")]
        self.assertTrue(found, "styles.css never colours a row whose card is "
                               "missing, so the wording is the only difference and "
                               "it is 12px of grey")
        for rule in found:
            with self.subTest(rule=rule.where):
                self.assertIn("--warning-text", rule.value("color"))


class TestARoundWithCardsNoneOfWhichWasPlayedIsNotCalledPlayed(WebAssetCase):
    """A round whose every card counts for nothing is headed "not played".

    4.452. ``rounds[].played`` is true of a round with any card, and the Matches view
    worded and coloured a round's pill by it alone, so a washed-out round read
    ``played``, in the green pill, over cards that each say ``cancelled — counts for
    nothing``, while each of its teams' pages said the round ``was not played``.
    Measured in headless Chrome on cardwriter cards
    (/tmp/mcf/scratch/UNPLAYED/fix/before.txt): a round 2 of two Match Cancelled
    cards, one stopped at 6-1 6-0 3-0 and one with no games, was headed ``played`` in
    ``pill good``, two blank cards nobody ticked were headed the same, and a GF whose
    one card was called off was headed ``played`` and named in ``Finals played:
    GF.``. A round with one card played and one called off is headed ``played``
    still, and is.

    The exporter counts the round's cards that count as played,
    ``rounds[].cards_played`` by ``Match.counts_as_played``, and one function here
    reads it, for the heading and for the finals line. But it can count only the
    cards that are in. A round of a declared draw with one card called off and no
    card for its other fixture was headed ``not played`` in the muted pill by the
    first version of this fix, over a row saying ``no card yet`` and a report asking
    for that card (/tmp/mcf/scratch/UNPLAYED/fix/mid.txt): the missing card taken for
    an answer. That round says ``card missing``, in the amber of a card to chase, as
    a round short of every card says ``no cards yet``.

    There is no JavaScript engine on this machine, so the heading's few lines, and the
    expressions the finals line and Latest round pick their rounds by, are read by
    ``value`` below, which works out what an expression of that size comes to. The
    tests then ask what each kind of round is headed and whether it is picked, and
    not how the choice is nested, which an equivalent rewrite would change.

    Rejected: "called off", false of a round of blank cards nobody ticked; "counts
    for nothing", false under ``unplayed = "average"``, which credits the round at
    each team's average; the amber pill over a round with every card in, 4.3's colour
    for a card still to come, where nothing is to chase; the page counting
    ``countsAsPlayed`` over the round's cards itself, a second copy of the rule
    (4.112); and, over a round still short of a card, either "not played" or the
    green "played" of the base, each an answer the missing card would have to give.
    """

    ASKS = "noCardPlayed"

    class Undefined:
        """JavaScript's ``undefined``: falsy, and strictly equal to itself alone."""

        def __bool__(self):
            return False

        def __repr__(self):
            return "undefined"

    UNDEFINED = Undefined()

    # Integers, quoted strings, names and dotted names, and the operators the heading
    # is chosen with. Anything else stops the reader, and the test that met it fails,
    # so a heading rewritten past what this reads says so rather than passing unread.
    TOKEN = re.compile(r"\s*(?:(\d+)|'([^'\\]*)'"
                       r"|([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)"
                       r"|(===|!==|&&|\|\||[!?:()+,]))")

    def value(self, expression: str, names: dict, local=None):
        """What *expression* comes to, by JavaScript's rules, with *names* bound.

        *names* maps a name or a dotted name (``r.played``) to its value, and *local*
        is asked for any other. ``&&`` and ``||`` give an operand, as JavaScript's do,
        ``===`` compares type and value, and ``+`` joins as soon as either side is a
        string. Both arms of a ``? :`` are worked out, which nothing read here minds.
        """
        text, at, tokens = expression.strip(), 0, []
        while at < len(text):
            found = self.TOKEN.match(text, at)
            self.assertIsNotNone(found, f"the reader stops at {text[at:]!r} in "
                                        f"{text!r}, so what it chooses is not read")
            tokens.append(found.groups())
            at = found.end()
        tokens.append((None, None, None, None))
        pos = 0

        def peek(*ops):
            return tokens[pos][3] in ops

        def take(op):
            nonlocal pos
            self.assertTrue(peek(op), f"{op!r} expected in {text!r}")
            pos += 1

        def joined(x):
            return {True: "true", False: "false", None: "null"}.get(x, str(x)) \
                if isinstance(x, (bool, type(None))) else str(x)

        def choice():
            test = either()
            if not peek("?"):
                return test
            take("?")
            yes = choice()
            take(":")
            no = choice()
            return yes if test else no

        def either():
            left = both()
            while peek("||"):
                take("||")
                right = both()
                left = left or right
            return left

        def both():
            left = equal()
            while peek("&&"):
                take("&&")
                right = equal()
                left = left and right
            return left

        def equal():
            left = added()
            while peek("===", "!=="):
                same = tokens[pos][3] == "==="
                take(tokens[pos][3])
                right = added()
                left = (type(left) is type(right) and left == right) == same
            return left

        def added():
            left = unary()
            while peek("+"):
                take("+")
                right = unary()
                left = (joined(left) + joined(right)
                        if isinstance(left, str) or isinstance(right, str)
                        else left + right)
            return left

        def unary():
            if peek("!"):
                take("!")
                return not unary()
            return term()

        def term():
            nonlocal pos
            number, string, name, op = tokens[pos]
            if op == "(":
                take("(")
                inner = choice()
                take(")")
                return inner
            self.assertIsNone(op, f"{op!r} where a value belongs in {text!r}")
            self.assertTrue(number or string is not None or name,
                            f"{text!r} ends where a value belongs")
            pos += 1
            if number is not None:
                return int(number)
            if string is not None:
                return string
            if name in names:
                found = names[name]
            else:
                self.assertIsNotNone(local, f"{text!r} reads {name}, not given")
                found = local(name)
            if not peek("("):
                return found
            take("(")
            args = []
            while not peek(")"):
                args.append(choice())
                if peek(","):
                    take(",")
            take(")")
            return found(*args)

        result = choice()
        self.assertIsNone(tokens[pos][0] or tokens[pos][1] or tokens[pos][2]
                          or tokens[pos][3], f"{text!r} is read only in part")
        return result

    @staticmethod
    def round_(**keys):
        """A round of season.json, with *keys* and no others."""
        r = type("Round", (), {})()
        vars(r).update(keys)
        return r

    def rule(self) -> str:
        """The one expression noCardPlayed() returns."""
        code = jslint.without_comments(self.body(self.ASKS))
        found = re.search(r"\{\s*return ([^;]*);\s*\}", code)
        self.assertIsNotNone(found, f"{self.ASKS}() is no longer one expression")
        return found.group(1)

    def said(self, expression: str, r):
        """What *expression* says of the round *r*: a key it lacks is undefined, as in
        JavaScript, and noCardPlayed() is app.js's own, worked out the same way. Any
        other name stops the reader, rather than reading as undefined."""
        def key(name):
            self.assertTrue(name.startswith("r."), f"{expression!r} reads {name}, "
                                                   f"which this does not know")
            return getattr(r, name[len("r."):], self.UNDEFINED)

        return self.value(expression, {"r": r, "undefined": self.UNDEFINED,
                                       "true": True, "false": False, "null": None,
                                       self.ASKS: lambda x: self.said(self.rule(), x)},
                          key)

    def plural(self, n, one, many=UNDEFINED):
        """app.js's own plural(), read off its one line."""
        code = jslint.without_comments(self.body("plural"))
        found = re.search(r"\{ return ([^\n]*); \}", code)
        self.assertIsNotNone(found, "plural() is no longer one line that this reads")
        return self.value(found.group(1), {"n": n, "one": one, "many": many})

    def heading(self, played: bool, none_played: bool, missing: int,
                washed=False) -> tuple[str, str]:
        """The words and the pill renderMatches heads a round with.

        Given whether the round has a card (``r.played``), what ``noCardPlayed``
        says of it, and how many of its fixtures are short a card (``card_missing``),
        as the function itself is given them. Every other name the two expressions
        read is found by its own ``var`` line and worked out the same way. *washed*
        is whether a washout is declared in the round, the whole of it or a fixture
        (4.535): the exporter's flags, bound to the one line that reads them.
        """
        body = jslint.without_comments(self.body("renderMatches"))
        asked = re.search(r"var (\w+) = " + self.ASKS + r"\(r\);", body)
        self.assertIsNotNone(asked, "renderMatches() never asks whether any card of "
                                    "the round was played, so it heads it played")
        counted = re.search(r"var (\w+) = [^\n]*\.card_missing[^\n]*;\n", body)
        self.assertIsNotNone(counted, "renderMatches() never counts the round's "
                                      "fixtures short a card")
        chosen = re.search(r"var \w+ = ([^\n]*'not played yet'[^\n]*);\n", body)
        self.assertIsNotNone(chosen, "the heading's wording is no longer a choice")
        pill = re.search(r"class: ('pill ' \+ [^\n]*?) \}, \[\n", body)
        self.assertIsNotNone(pill, "the round heading has no pill to colour")
        names = {"r.played": played, asked.group(1): none_played,
                 counted.group(1): missing, "plural": self.plural,
                 "r.washed_out": washed}
        flagged = re.search(r"var (\w+) = [^\n]*\.washed_out[^\n]*;\n", body)
        if flagged:
            names[flagged.group(1)] = washed

        def local(name):
            found = re.search(r"\bvar " + re.escape(name) + r" = ([^\n]*);\n", body)
            self.assertIsNotNone(found, f"the heading reads {name}, and no line of "
                                        f"renderMatches() says what it is")
            return self.value(found.group(1), names, local)

        tone = self.value(pill.group(1), names, local)
        self.assertTrue(tone.startswith("pill "), tone)
        return self.value(chosen.group(1), names, local), tone[len("pill "):]

    # Each kind of round, as (has a card, noCardPlayed's answer, fixtures short a
    # card), and the words and pill it is headed with. The first two and the last two
    # are 4.3's, which this must not move; the one between is this item's.
    PLAYED = {"a round played": (True, False, 0),
              "a round played with a card still missing (4.3)": (True, False, 1)}
    NONE_IN = (True, True, 0)
    NO_CARD = {"a round the season went past with no card (4.3)": (False, False, 2),
               "a round not reached (4.3)": (False, False, 0)}
    UNMOVED = {"a round played": ("played", "good"),
               "a round played with a card still missing (4.3)": ("played", "good"),
               "a round the season went past with no card (4.3)": ("no cards yet",
                                                                   "warning"),
               "a round not reached (4.3)": ("not played yet", "")}

    def test_the_exporter_writes_the_count_the_page_reads(self):
        self.assertIn('"cards_played"',
                      (ROOT / "matchcentre" / "export.py").read_text())

    def test_the_page_reads_the_count_strictly_and_keeps_no_rule_of_its_own(self):
        # Strictly 0: a format 4 document built before the key lacks it, and its
        # rounds read as they did, played wherever a card has the round, rather than
        # every round with cards as not played. And a round with cards: one without is
        # still to come or gone past without one, and the heading has its own words
        # for those (4.3). Worked out for each, not matched as text, so a reading that
        # also takes an absent count for none (`|| r.cards_played === undefined`) is
        # the older document's every round headed not played, and fails here.
        rule = self.rule()
        rounds = {
            "cards, none of them played": (dict(played=True, cards_played=0), True),
            "cards, one of them played": (dict(played=True, cards_played=1), False),
            "cards, in an older document without the count": (dict(played=True), False),
            "no card": (dict(played=False, cards_played=0), False),
            "no card, in an older document": (dict(played=False), False),
        }
        for what, (keys, expected) in rounds.items():
            with self.subTest(round=what):
                self.assertIs(bool(self.said(rule, self.round_(**keys))), expected,
                              "the page reads the exporter's count loosely, so a "
                              "document without it heads every round with cards not "
                              "played, or a round with no card is one nobody played")
        for own in ("countsAsPlayed", "counts_as_played", "status", "keys", "cardsOf"):
            with self.subTest(own=own):
                self.assertNotIn(own, rule,
                                 "the page works out from the round's cards whether "
                                 "any was played, a second copy of Python's rule")

    def test_the_heading_says_not_played_over_such_a_round(self):
        # Every card in and none of them played: the team pages' words for the night.
        self.assertEqual(self.heading(*self.NONE_IN)[0], "not played",
                         "a round with every card in and none of them played is "
                         "headed as one somebody played, or in other words than the "
                         "team pages give it")
        for what, shape in {**self.PLAYED, **self.NO_CARD}.items():
            with self.subTest(round=what):
                self.assertEqual(self.heading(*shape)[0], self.UNMOVED[what][0])

    def test_its_pill_is_the_muted_one(self):
        self.assertEqual(self.heading(*self.NONE_IN)[1], "",
                         "the pill over a round nobody played is green, or amber, "
                         "rather than the muted one a round not played yet has")
        for what, shape in {**self.PLAYED, **self.NO_CARD}.items():
            with self.subTest(round=what):
                self.assertEqual(self.heading(*shape)[1], self.UNMOVED[what][1])

    def test_a_round_still_short_of_a_card_says_so_rather_than_not_played(self):
        """A round with a card missing and the rest called off says the card is missing.

        Measured (/tmp/mcf/scratch/UNPLAYED/fix/mid.txt): on a declared draw, a round 2
        of one Match Cancelled card and no card for its other fixture was headed
        ``not played`` in the muted pill, over a row saying ``no card yet`` and a
        report asking for the card, as the base had headed it ``played`` in green.
        Either is an answer the missing card would have to give. It is headed with
        what it is short of, ``card missing``, two of them ``cards missing``, in the
        amber pill a round short of every card has. Rejected: ``no cards yet``, false
        under a card that is in, and either of the answers above.
        """
        for missing, words in ((1, "card missing"), (2, "cards missing")):
            with self.subTest(missing=missing):
                self.assertEqual(self.heading(True, True, missing), (words, "warning"),
                                 "a round with a card still to come and the rest "
                                 "called off is headed as if the missing card had "
                                 "said whether anybody played")

    # 4.535: a washout the club declared, a whole round or some fixtures of one.

    def test_a_round_washed_out_is_headed_washed_out_in_the_muted_pill(self):
        """A round declared washed out, with no card or with its cards called off.

        Measured before, in headless Chrome on a fictional season with round 3 rested
        whole in the byes table, the nearest a club could come to saying it rained
        (/tmp/mcw/scratch/before/zero-byes/matches-dom.html): ``Round 3``, no date,
        ``not played yet`` in the muted pill, over ``No fixtures listed for this
        round.`` and a bye for every team. The round the club declared washed out
        is headed in the words it was declared in, in the pill of a round nobody
        played, with or without cards. Rejected: ``not played``, which a club reads as
        the cards' word, and ``called off``, false of a fixture rained off before it
        began, where no card was.
        """
        for shape in ((False, False, 0), (True, True, 0)):
            with self.subTest(card=shape[0]):
                self.assertEqual(self.heading(*shape, washed=True),
                                 ("washed out", ""))

    def test_a_card_still_missing_is_said_before_the_washout(self):
        # A fixture short of its card may be the one somebody played, whatever the
        # rest of the round did: the washout is no answer for it (4.452).
        self.assertEqual(self.heading(True, True, 1, washed=True),
                         ("card missing", "warning"))
        self.assertEqual(self.heading(False, False, 2, washed=True),
                         ("no cards yet", "warning"))

    def test_a_round_played_beside_a_fixture_washed_out_is_played(self):
        self.assertEqual(self.heading(True, False, 0, washed=True), ("played", "good"))

    def test_the_heading_reads_the_exporters_flags_and_keeps_no_rule_of_its_own(self):
        body = jslint.without_comments(self.body("renderMatches"))
        flagged = re.search(r"var (\w+) = ([^\n]*\.washed_out[^\n]*);\n", body)
        self.assertIsNotNone(flagged, "renderMatches() never asks whether the round "
                                      "has a washout declared")
        self.assertIn("r.washed_out", flagged.group(2),
                      "a round washed out whole, with no fixture to flag, is not asked")
        self.assertRegex(flagged.group(2), r"\bf\.washed_out\b",
                         "a round with one fixture washed out is not asked")
        chosen = re.search(r"var \w+ = ([^\n]*'not played yet'[^\n]*);\n", body)
        self.assertIn(flagged.group(1), chosen.group(1),
                      "the heading chooses its words without the washout")

    def test_a_round_washed_out_whole_says_so_where_its_fixtures_would_be(self):
        # No draw typed and no card: the box under the heading said "No fixtures
        # listed for this round.", which reads as a draw left out.
        code = jslint.without_comments(self.body("renderMatches"))
        self.assertRegex(code, r"r\.washed_out\s*\?\s*'The whole round was washed "
                               r"out\.'\s*:\s*'No fixtures listed for this round\.'",
                         "a round washed out whole is said to have no fixtures listed")

    def test_a_final_nobody_played_is_not_named_among_the_finals_played(self):
        # A final with a card missing and the rest called off is left out too, as one
        # with every card missing always was: the line names what the cards say was
        # played, and says nothing of the rest, where the heading says what is missing.
        # Worked out for each kind of round, as the heading is.
        code = jslint.without_comments(self.body("finalsPlayed"))
        found = re.search(r"\.filter\(function \(r\) \{\s*return ([^;]*);\s*\}\)", code)
        self.assertIsNotNone(found, "finalsPlayed() no longer picks its rounds by one "
                                    "expression that this reads")
        pick = found.group(1)
        for what, keys, named in (
                ("a final with a card played",
                 dict(is_finals=True, played=True, cards_played=1), True),
                ("a final with cards, in an older document",
                 dict(is_finals=True, played=True), True),
                ("a final whose every card was called off",
                 dict(is_finals=True, played=True, cards_played=0), False),
                ("a final the draw lists and no card has",
                 dict(is_finals=True, played=False, cards_played=0), False),
                ("a final with no card, in an older document",
                 dict(is_finals=True, played=False), False),
                ("a regular round played",
                 dict(is_finals=False, played=True, cards_played=2), False)):
            with self.subTest(round=what):
                self.assertIs(bool(self.said(pick, self.round_(**keys))), named,
                              "Finals played names a final none of whose cards was "
                              "played, or one with no card, or leaves out one played")
        self.assertIn(self.ASKS, pick,
                      "the finals line reads the count itself, a second reader of it")
        self.assertNotIn("card_missing", code,
                         "a final none of whose cards was played is named for a card "
                         "nobody has read")

    def test_the_latest_round_is_still_last_week_whether_or_not_anyone_played(self):
        # Latest round shows the last round with cards, a called-off one with its
        # cards, since that is what last week was; asked whether anybody played, it
        # would skip a washed-out last week for an older one and say nothing of it.
        # Worked out, as the heading is, for the rounds it picks among.
        code = jslint.without_comments(self.body("renderOverview"))
        at = code.find("sectionTitle('Latest round')")
        self.assertGreater(at, 0, "the overview has no Latest round")
        found = re.compile(r"\(S\.rounds \|\| \[\]\)\.filter\(function \(r\) "
                           r"\{\s*return ([^;]*);\s*\}\)").search(code, at)
        self.assertIsNotNone(found, "Latest round no longer picks the rounds it shows "
                                    "the last of by one expression that this reads")
        pick = found.group(1)
        for what, keys, shown in (
                ("a week washed out", dict(played=True, cards_played=0), True),
                ("a week played", dict(played=True, cards_played=3), True),
                ("a week with cards, in an older document", dict(played=True), True),
                ("a week with no card", dict(played=False, cards_played=0), False)):
            with self.subTest(round=what):
                self.assertIs(bool(self.said(pick, self.round_(**keys))), shown,
                              "Latest round skips a washed-out last week for an older "
                              "one, or shows a round with no card")


class TestAFixtureWashedOutIsNoFixtureStillToPlay(WebAssetCase):
    """A fixture declared washed out is not counted among those to come (4.535).

    The fixtures the exporter lists include those a club declared washed out, so the
    Matches page can draw them, and two other places read the list as the fixtures
    not yet played: the team page's ``N fixtures still to play, next: ...`` and the
    head-to-head table's column of rounds to come. Each would have counted a night
    the rain took as one still to play. Python says which fixtures those are, and
    both lists leave them out by its flag.
    """

    def filters(self, name: str) -> list[str]:
        code = jslint.without_comments(self.body(name))
        found = re.findall(r"\(S\.fixtures \|\| \[\]\)\.filter\(function \(f\) "
                           r"\{\s*return ([^;]*);\s*\}\)", code)
        self.assertTrue(found, f"{name}() no longer picks fixtures by one expression")
        return found

    def test_the_team_page_counts_none_of_them_still_to_play(self):
        picked, = self.filters("renderTeam")
        self.assertRegex(picked, r"!f\.played && !f\.washed_out",
                         "a fixture washed out is told as one the team still has "
                         "to play")

    def test_the_head_to_head_lists_none_of_them_to_come(self):
        picked, = self.filters("teamH2HTable")
        self.assertRegex(picked, r"!f\.played && !f\.washed_out",
                         "a fixture washed out is listed among the meetings to come")

    def test_the_exporter_writes_the_flag_the_page_reads(self):
        self.assertIn('"washed_out"', (ROOT / "matchcentre" / "export.py").read_text())

class TestAnUndatedMatchLeavesNoSeparatorStanding(WebAssetCase):
    """A match with no date printed its separators with nothing between them.

    Undated matches are new: a card whose date does not read is now filed by the
    weekday it prints instead of being refused, and its date is ``""``. Measured in
    headless Chrome on the six-card season printed ``Tue August 18, 2026 7:00 PM``:
    the match page's heading read "Round 1 ·  · match #2000001", every match card's
    caption began "· court 1", and the overview's latest round read "Round 3 ·".
    ``longDate('')`` is ``''`` and each line glued it to a separator.

    The three lines now go through one function that drops an absent part, and the
    team chart's tooltip says ``—`` for the date, as the caption already does for a
    missing court. Rejected: a word such as "undated" in each line, which is the
    page deciding what to call a match the build already warned about, in the
    note printed under that same card. Structural, since there is no JavaScript
    engine here; the Chrome measurement is the evidence it renders.
    """

    def test_no_date_is_glued_to_a_separator(self):
        code = self.assets()["app.js"]
        self.assertNotRegex(code, r"longDate\([^)]*\)\s*\+\s*' ·",
                            "a date is followed by a separator whether or not "
                            "there is a date")
        self.assertNotRegex(code, r"· '\s*\+\s*longDate\(",
                            "a date is preceded by a separator whether or not "
                            "there is a date")

    def test_the_joining_drops_an_empty_part(self):
        # Either spelling of the one filter; app.js uses both elsewhere.
        body = self.body("dotted")
        self.assertRegex(body, r"\.filter\((?:Boolean|function \((\w+)\) \{ return "
                               r"!!\1; \})\)\s*\.join\(' · '\)")

    def test_no_line_drops_its_empty_parts_by_a_rule_of_its_own(self):
        # dotted() was the third copy of one rule: the page's title and the brand
        # line under it each spelt the filter-and-join out in full, and the fixture
        # row built its parts up with an `if`, so a fix to one was a fix to one of
        # three. Rejected: leaving the older two, which already worked; the next
        # line to need the rule copies whichever it finds first. Read with the
        # strings in, since the separator is one.
        code = self.assets()["app.js"].replace(self.body("dotted"), "")
        inline = re.findall(r"\.filter\((?:[^()]|\([^()]*\))*\)\s*\.join\(' · '\)",
                            code)
        self.assertEqual(inline, [], "a line drops its own empty parts rather than "
                                     "going through dotted()")

    def test_every_line_with_a_date_in_it_is_joined_that_way(self):
        for fn, date in (("matchCard", "m.date"), ("renderOverview", "lastRound.date"),
                         ("renderMatch", "m.date")):
            with self.subTest(fn=fn):
                self.assertRegex(self.body(fn),
                                 rf"dotted\(\[[^\]]*longDate\({re.escape(date)}\)")

    def test_the_tooltip_says_there_is_no_date_rather_than_nothing(self):
        self.assertRegex(self.body("pointsByRoundChart"),
                         r"value:\s*longDate\(m\.date\)\s*\|\|\s*'[^']+'")


class TestOneNightHasAnAddressOfItsOwn(WebAssetCase):
    """A club sends somebody one night, and the only thing to send was the whole page.

    Every card already prints the id Match Centre printed on the scorecard, and that
    id belongs to the association rather than to this build: ``scorecard.py`` reads it
    off the page, ``export.py`` writes it through, and all 56 cards of the demo season
    carry one. So ``#/match/<id>`` is an address that survives a rebuild and the next
    round landing, which a position in the read order would not -- the cards are walked
    in directory order, and a round arriving in a folder that sorts earlier renumbers
    everything after it. That half of the claim is tested where it is decided, in
    test_pipeline.py; this class is about the page.

    Where the link goes was measured rather than chosen. The summary row is where a
    reader would look for it, and Chrome folds it into the accessible name of the
    control that opens the card -- ``DisclosureTriangle 'Quolls 12 - 5 Gnats · sets 4-0
    match #4200003'`` against ``'Quolls 12 - 5 Gnats · sets 4-0'`` without it -- and
    gives every row a second focus stop, 56 of them on the matches page. (It does not
    toggle the card, which was the other worry: a real click on a link inside a
    ``<summary>`` left ``open`` false and changed the fragment. So the reason is the
    announcement and the tab order, not a swallowed click.) It goes in the card's body
    instead, which a closed card keeps out of the tab order through
    ``content-visibility: hidden``, so a reader meets it on the night they opened.
    Walked with real Tab presses, since ``getClientRects()`` is not the measurement:
    Chrome still reports a box for a link inside a ``content-visibility: hidden``
    subtree, and the tab walk is what says it is skipped -- from the first card's
    summary, three Tab presses gave three summaries and no link. On the shipped build:
    56 cards, 56 links, every one of them in a body and none in a summary, and on the
    match's own page the card is open and carries no link back to itself.

    Rejected: a Copy-link button of the kind the export pair uses. The address is in
    the address bar the moment the reader is on the page, and the pair of buttons was
    bought at one caption each for a table a spreadsheet wants; a third control on
    every one of 56 rows is not the same trade.
    """

    def route(self) -> str:
        """The router's source."""
        return self.body("parseRoute")

    def test_the_router_reads_a_match_by_the_id_printed_on_its_card(self):
        self.assertIn("parts[0] === 'match'", self.route(),
                      "the router does not know a match route, so a link to one "
                      "lands on the overview")

    def test_the_router_hands_a_match_to_a_view_of_its_own(self):
        # The field name is read off the call rather than written down here, so
        # renaming it is a rename and not a failure -- and the point of the test is
        # that the two ends name the same field, which a hardcoded name cannot check.
        called = re.search(r"renderMatch\(main, route\.(\w+)\)", self.body("render"))
        self.assertTrue(called, "render() does not hand a match route to "
                                "renderMatch(main, route.<field>)")
        # Against the match route's own return and not the whole router: `slug` is a
        # field two other routes set, so a dispatch reading the wrong one off a route
        # that does not set it passes a file-wide search and hands the view undefined.
        line = re.search(r"if \(parts\[0\] === 'match'\)[^\n]*", self.route())
        self.assertTrue(line, "the router has no match route to read a field off")
        self.assertIn(called.group(1) + ":", line.group(0),
                      f"the match route returns no {called.group(1)!r}, which is what "
                      f"render() reads off it: {line.group(0).strip()!r}")

    def test_a_fragment_the_router_cannot_decode_is_not_a_blank_page(self):
        # Found while adding the third caller: `decodeURIComponent('%zz')` throws, so
        # a hand-typed `#/team/%` took the whole dashboard blank -- the router runs
        # inside render(), and nothing above it catches. One helper, three callers.
        helper = self.body("decodeOr")
        param = re.search(r"function decodeOr\((\w+)\)", helper)
        self.assertTrue(param, "decodeOr() takes no argument")
        for token in ("try", "catch", f"return {param.group(1)};"):
            with self.subTest(token=token):
                self.assertIn(token, helper,
                              f"decodeOr() does not {token}: a malformed escape "
                              f"throws out of the router and the page never renders")
        route = jslint.code_only(self.route())
        self.assertNotIn("decodeURIComponent", route,
                         "the router decodes a slug itself somewhere, and that is the "
                         "call that throws")
        self.assertEqual(route.count("parts[1]"), route.count("decodeOr("),
                         "not every part the router decodes goes through the helper")

    def test_an_id_no_card_prints_is_not_found_rather_than_the_first_card(self):
        body = self.body("renderMatch")
        missing = self.offset_of(body, "renderMissing(main,",
                                 "an unknown match id renders something else")
        self.assertLess(missing, self.offset_of(body, "matchCard(",
                                               "the view renders no card at all"),
                        "renderMatch() renders a card before deciding whether it "
                        "found one")
        self.assertIn("+ id +", body,
                      "the not-found sentence does not name the id back to the "
                      "reader, which is the one thing they can check against the card")
        # What the sentence is guarded by, and not only that it is there: with the
        # guard always false the view falls straight through to `found[0]`, which for
        # an id no card prints is undefined, and the page throws instead of saying so.
        # The local is read off the call that fills it, so renaming it is a rename.
        local = re.search(r"var (\w+) = matchesWithId\(", body)
        self.assertTrue(local, "renderMatch() does not ask matchesWithId() what it has")
        cond = self.guard_for("renderMatch", "prints match id", emits="renderMissing")
        for token in (local.group(1), "length"):
            with self.subTest(token=token):
                self.assertIn(token, cond,
                              f"the not-found sentence is not guarded by {token}: "
                              f"{cond.strip()!r}")

    def test_a_card_with_no_printed_id_is_addressed_by_nothing(self):
        # A card can print no id -- a club's own reprint, or a page whose identity
        # line was read without one -- and `export.py` writes "" rather than
        # inventing one. An index keyed by id keeps such a card under the empty
        # string, so `#/match/` with nothing after it would answer with it.
        finder = self.body("matchesWithId")
        param = re.search(r"function \((\w+)\)", finder)
        self.assertTrue(param, "matchesWithId() filters with no callback")
        self.assertIn(f"!!{param.group(1)}.id", finder,
                      "a card with no printed id is still a candidate, so an empty "
                      "address answers with one")

    def test_two_cards_printing_one_id_are_both_shown(self):
        # validate.py reports this as a fault to go and fix: the same night exported
        # twice, or two clubs' readings of one fixture. An index by id holds
        # whichever was written last, so answering from it shows one card and hides
        # the other without saying so.
        finder = self.body("matchesWithId")
        self.assertIn("S.matches", finder,
                      "matchesWithId() does not walk the document's own match list")
        self.assertNotIn("matchById", finder,
                         "matchesWithId() reads the index, which holds one card per "
                         "id: the second card of a disputed night would be invisible")
        body = self.body("renderMatch")
        self.assertRegex(body, r"\w+\.forEach\(function \(\w+\) \{\s*\S*matchCard\(",
                         "renderMatch() renders one of what it found rather than "
                         "every one")

    def test_more_than_one_card_for_an_address_says_so(self):
        # The phrase is the count's own, not a word from the sentence: renderMatch()
        # says "scorecard" twice, once in the wording for an id no card prints, and
        # guard_for() reads every printing and refuses two that disagree. A sentence
        # that says how many there are cannot be printed when there is one.
        cond = self.guard_for("renderMatch", "print this match id")
        self.assertIn("> 1", cond,
                      f"the sentence about two cards is not guarded by there being "
                      f"two: {cond.strip()!r}")

    def test_every_card_but_the_one_on_its_own_page_carries_its_address(self):
        cond = self.guard_for("matchCard", "'#/match/'")
        self.assertIn("m.id", cond,
                      f"a card links to a page addressed by an id it may not have: "
                      f"{cond.strip()!r}")
        # The option's name is read off the guard, and then required of the caller:
        # the link must be absent on the one page it would point at itself from, and
        # the two ends have to agree about which flag says so.
        flag = re.search(r"!o\.(\w+)", cond)
        self.assertTrue(flag, f"nothing in {cond.strip()!r} excludes the card on its "
                             f"own page, so that page offers a link to itself")
        self.assertIn(flag.group(1) + ": true", self.body("renderMatch"),
                      f"renderMatch() does not pass {flag.group(1)}, so the card on "
                      f"a match's own page links to the page it is on")

    def test_the_address_is_not_in_the_row_that_opens_the_card(self):
        # By who is handed the link rather than by where it is written: appending to
        # the summary further down the function is the same row and the same two costs,
        # and an order check reads that as the body. The summary's own local is read off
        # the call that builds it, so the test follows a rename.
        body = self.body("matchCard")
        row = re.search(r"var (\w+) = el\('summary'\)", body)
        self.assertTrue(row, "matchCard() builds no summary row")
        at = self.offset_of(body, "'#/match/'", "no card carries its own address")
        taker = re.search(r"(\w+)\.appendChild\((?!.*\.appendChild)", body[:at],
                          re.DOTALL)
        self.assertTrue(taker, "the link to a match's own page is appended to nothing")
        self.assertNotEqual(row.group(1), taker.group(1),
                            f"the link is appended to {row.group(1)}, the row that "
                            f"opens the card: there it is read out as part of that "
                            f"control's own name and takes a focus stop on every one "
                            f"of 56 rows")

    def test_the_words_on_the_link_say_where_it_goes(self):
        body = self.body("matchCard")
        at = body.find("'#/match/'")
        text = re.search(r"text: '([^']+)'", body[at:])
        self.assertTrue(text, "the link to a match's own page carries no words")
        self.assertIn("page", text.group(1).lower(),
                      f"the link reads {text.group(1)!r}, which does not say that "
                      f"pressing it goes to a page of this match")

    def test_a_card_that_printed_no_id_says_so_where_the_id_would_go(self):
        words = self.body("matchIdWords")
        self.assertIn("'match #'", words,
                      "the caption no longer prints the id Match Centre printed")
        self.assertRegex(words, r"'[^']*no match id[^']*'",
                         "a card with no id falls through to 'match #' with nothing "
                         "after it, which reads as a page that lost the number")
        card = self.body("matchCard")
        self.assertIn("matchIdWords(m)", card,
                      "the card builds the words itself, so the two spellings of a "
                      "missing id can differ")
        self.assertNotIn("'match #'", card,
                         "the card still holds its own copy of the words")

    def test_the_page_names_the_night_and_links_both_teams(self):
        body = self.body("renderMatch")
        for side in ("m.home", "m.away"):
            with self.subTest(side=side):
                self.assertIn(f"teamLinkBySlug({side}", body,
                              f"a reader arriving at one night from a link cannot "
                              f"reach {side}'s season from it")
        self.assertIn("roundTitle(m.round)", body,
                      "the page does not say which round the night was, which is how "
                      "a reader places it in the season")
        # And the address it was reached by, in the same line. A reader who followed a
        # link has an id they cannot read out of the address bar of a phone, and the one
        # check they can make is this line against the card in front of them. Through
        # the same helper as the card's caption, so a night with no printed id says the
        # same thing in both places.
        self.assertIn("matchIdWords(", body,
                      "the page does not say which match id it is, so a reader who "
                      "arrived from a link cannot tell whether it is the night they "
                      "were sent")

    def test_the_sheet_and_the_heading_are_named_the_same_thing(self):
        # The other four views name their sheet after a field, and the class that owns
        # the printed header lists those four. This one names it after a local built from
        # the two teams, so the check belongs here and is made by reading the local off
        # the call: a heading saying one thing and a printed header saying another is a
        # sheet a captain cannot file, and a name written down in the test would make
        # renaming the local a failure instead of a rename.
        body = self.body("renderMatch")
        named = re.search(r"setPageName\((\w+)\)", body)
        self.assertTrue(named, "the match's own page names no sheet, so a printout of "
                               "it says which season it is and not which night")
        local = named.group(1)
        built = re.search(r"var " + local + r" = ([^;]+);", body)
        self.assertTrue(built, f"{local} is named into the sheet without being built "
                               f"anywhere in the view")
        for side in ("m.home", "m.away"):
            with self.subTest(side=side):
                self.assertIn(side, built.group(1),
                              f"the sheet is named without {side} in it: a printed page "
                              f"of one night should say which two teams played it")
        self.assertIn(f"el('h1', {{ text: {local} }})", body,
                      f"the heading on screen is not the {local} the printed header "
                      f"carries, so the page and the sheet say different things")

    def test_the_card_on_its_own_page_is_already_open(self):
        self.assertIn("open: true", self.body("renderMatch"),
                      "the card on a match's own page is closed, so a shared link "
                      "opens a page holding one row to press")

    def test_the_way_back_is_the_list_the_card_came_from(self):
        body = self.body("renderMatch")
        at = self.offset_of(body, "class: 'crumb'", "the page has no way back")
        self.assertIn("'#/matches'", body[at:at + 200],
                      "the crumb does not go to the matches list")

    def test_what_a_finals_night_is_worth_is_said_in_one_place(self):
        # The matches list and a single match's page both carry it, and two copies of
        # one sentence is how the two came to disagree elsewhere in this file.
        sentence = "Finals do not contribute ladder points."
        self.assertEqual(self.assets()["app.js"].count(sentence), 1,
                         f"{sentence!r} is written more than once")
        for fn in ("renderMatches", "renderMatch"):
            with self.subTest(view=fn):
                self.assertIn("finalsNote()", self.body(fn),
                              f"{fn}() does not use the one function that words it")


class TestTheShareTileKeepsTheHalfItWasGiven(WebAssetCase):
    """A share of a doubles rubber is a half, and a half shown as a whole is 150%.

    ``contribution.games_won`` is the one figure in the document that can carry a
    ``.5``: a doubles pair won its games together, so the credit is split. ``num()``
    defaults to whole numbers, so printing it that way shows 35 for 34.5 -- and a
    column of those stops adding up to the team's games won, which is the fault the
    figure was changed to fix in the first place.
    """

    def test_the_tile_prints_the_credit_through_the_helper_that_keeps_halves(self):
        self.assertIn("shareNum(p.contribution.games_won)",
                      self.body("renderPlayer"))

    def test_the_helper_shows_a_decimal_only_where_there_is_one(self):
        # "34.5 of 70" and "36 of 70", not "34.5" and "36.0": the column is read
        # down, and a trailing zero on every whole number is noise in it.
        self.assertRegex(self.body("shareNum"),
                         r"num\(v,[^;]*\?\s*0\s*:\s*1\)")


class TestTheTeamPageChartsOnlyWhatTheLadderCounted(WebAssetCase):
    """A bar for a finals round would sit under a total that excludes it (F11).

    The team page's tiles, split table and form all come from the exported team row,
    which is the regular season. The points-by-round chart is drawn by the page
    itself from the match list, which is every match the team played, so it is the
    one figure on that page which could disagree with the rest of it -- and the
    disagreement it would print is a bar of points beside a points total that does
    not include them.

    Structural, because there is no JavaScript engine here to run the branch.
    """

    def chart_argument(self) -> str:
        """The name of the list ``renderTeam`` hands the chart."""
        body = self.body("renderTeam")
        at = body.find("pointsByRoundChart(")
        self.assertGreater(at, 0, "renderTeam() no longer draws the points chart")
        args = body[at + len("pointsByRoundChart("):body.find(")", at)]
        return args.split(",")[1].strip()

    def test_the_chart_is_not_handed_every_match_the_team_played(self):
        self.assertNotEqual(
            self.chart_argument(), "matches",
            "the chart is drawn from the whole match list, so a finals round gets "
            "a bar of ladder points the team's total never counted")

    def test_the_list_it_is_handed_is_filtered_on_the_documents_own_flag(self):
        # `counts_for_ladder` is the exporter's predicate. Re-deriving finals-ness
        # here from round labels is how the page and the ladder come to disagree.
        body = self.body("renderTeam")
        name = self.chart_argument()
        assign = re.search(r"var\s+" + re.escape(name) + r"\s*=\s*(.+?);\n", body,
                           re.S)
        self.assertIsNotNone(assign, f"renderTeam() never assigns {name}")
        self.assertIn("counts_for_ladder", assign.group(1))

    # A comment above may explain the same thing in the same words, and a check a
    # comment satisfies is a check of nothing: guard_for() reads the sentence where the
    # page can say it, and its condition closed at its own bracket -- the sentence
    # contains the word "finals" itself, so a slice that reached into it would be
    # satisfied by `if (false)`.
    SENTENCE = "no ladder points"

    def test_the_page_says_why_a_listed_match_is_in_no_figure_above(self):
        # Absence of evidence is never silence, on the page as well as in the
        # report: the team's record says three matches and the list below it shows
        # four, so the page has to account for the fourth -- on the page, in a node
        # that reaches the document, and not in a comment about it.
        self.guard_for("renderTeam", self.SENTENCE, emits="box.appendChild")

    def test_it_says_so_only_where_there_is_a_finals_match_to_explain(self):
        guard = self.guard_for("renderTeam", self.SENTENCE, emits="box.appendChild")
        self.assertIn("finals", guard,
                      "the sentence is printed for every team, including the ones "
                      "whose season ended at the last round")


class TestTheRoundNobodyPlayedIsMarkedInTheChartItStaysIn(WebAssetCase):
    """A washed-out round is plotted among the rounds the team earned points in.

    The chart above is handed the matches the ladder counted, which the class above
    pins. A washout is one of them: the exporter leaves ``counts_for_ladder`` **true**
    on a night nobody played -- that flag is about a card the data cannot use, and rain
    is not a data fault -- so the round arrives here, and nothing on its row said so.
    Measured at 1100px with :file:`/tmp/p318/rounds.py`, which reads every mark in the
    chart out of the drawn SVG: its accessible name, and each of its texts with the ink
    and weight it is painted in.

    On the washed-out night of the demo season, ``Round 5``, 14 matches and 14 bars:

    =========================  ===================================  ==================
    the round's own row        before                               after
    =========================  ===================================  ==================
    the bar                    no path drawn                        unchanged
    the value beside it        ``not scored``, ``#ffffff``, **600**  ``not played``, muted, 400
    the round label            ``#c3c2b7``, as every other row      muted
    its accessible name        ``Round 5: — points against …``      the words the eye gets
    the Values table's points  ``—``                                the words the eye gets
    a sentence under the plot  none                                 what the round earned
    =========================  ===================================  ==================

    So the loudest ink and the heaviest weight on that row belonged to the one round
    that was never played, ``not scored`` read as a figure that had not arrived yet
    rather than as a night nobody turned up for, and the three places the row is said in
    said it three ways: ``not scored`` to the eye, ``— points`` to a screen reader, ``—``
    in the table. The five sentences under the match list could not cover it, because
    every one of them is keyed on ``counts_for_ladder === false``.

    **Not "counts for nothing".** Under ``unplayed = "average"`` the round *is* credited,
    at the team's own average, so the words that fit both presets are "not played" and
    the rest is the exporter's clause, ``rules().unplayed_worth`` -- which is the one
    place this rule is worded, for the reason ``scoring.unplayed_worth`` gives at length:
    the page used to keep its own pair of sentences for it and they had already drifted.
    The demo season built under that preset is the measurement: the same team goes from
    55.3 points over 13 counted rounds to 59.553846 over 14, so the night nobody played
    earned it 4.25 there, and the sentence under the plot cannot say otherwise in its own
    words. It says what is counted of the card, which is nothing under either preset
    (4.451: what is on the card is not always nothing).

    **Found in the same shell while fixing this.** On the same page, ``Points average by
    team`` dims the seven teams that are not the one being read -- and measured, all eight
    values were drawn in ``#ffffff`` at 600, so a row dimmed for context kept the loudest
    number on it. ``hbarCard`` only ever dimmed the row *label*. The two single-bar value
    texts now follow ``r.dim`` as well; the diverging arms are left alone, because
    ``gamesForAgainstChart`` is the only chart that draws them and it is called with
    ``null``, so nothing there dims today.
    """

    # What `hbarCard` calls the words it prints beside a bar. Named once, because two
    # of the tests below read it off the row and one reads it back out of the table, and
    # a shell that renamed the key would otherwise satisfy half of them.
    VALUE = "valueText"

    def chart(self) -> str:
        return self.body("pointsByRoundChart")

    def region(self, opens: str, closes: str) -> str:
        """The slice of the chart between two anchors, asserted to be in that order."""
        body = self.chart()
        at = self.offset_of(body, opens, "the chart")
        end = self.offset_of(body, closes, "the chart")
        self.assertLess(at, end, f"{opens!r} no longer comes before {closes!r}, so this "
                                 f"test is reading a slice of the function backwards")
        return body[at:end]

    def field(self, where: str, name: str) -> str:
        """The expression *where* gives *name*, read to its own comma.

        Read to the comma at bracket depth zero rather than to the end of the line: two
        of these expressions are ternaries long enough to wrap, and a line-based read
        would take half of one and call it the answer. Strings and comments are masked
        by ``jslint.code_only`` before the scan so a comma inside a sentence is not a
        delimiter, and the slice is taken from the raw text so the answer is readable.
        """
        masked = jslint.code_only(where)
        at = masked.find(name + ":")
        self.assertGreater(at, 0, f"nothing in this part of the chart sets {name}")
        i, depth = at + len(name) + 1, 0
        while i < len(masked):
            ch = masked[i]
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                if depth == 0:
                    break
                depth -= 1
            elif ch == "," and depth == 0:
                break
            i += 1
        return where[at + len(name) + 1:i].strip()

    def rows(self) -> str:
        """The object one bar is built from."""
        return self.region("matches.map(", "return hbarCard(")

    def options(self) -> str:
        """What the card itself is given."""
        return self.region("return hbarCard(", "\n}")

    def night(self) -> str:
        """The name this function gives the answer to "was this round played at all"."""
        found = re.search(r"var (\w+) = !countsAsPlayed\(m\);", self.chart())
        self.assertIsNotNone(found,
                             "the chart never asks whether the round was played, so a "
                             "washout is plotted as a round that earned no points")
        return found.group(1)

    def test_the_chart_asks_whether_anybody_played_the_round(self):
        # Through the shared predicate, for the reason the card gives: a hand comparison
        # against 'cancelled' misses 'unplayed', and `counts_for_ladder` is what put this
        # round in the chart in the first place -- it is true here, which is right, and
        # it is an answer to a different question.
        code = jslint.code_only(self.chart())
        self.assertIn("!countsAsPlayed(m)", code,
                      "the chart cannot tell a round that earned no points from a round "
                      "nobody played")
        self.assertNotIn("counts_for_ladder", code,
                         "the chart re-asks the ladder's flag, which the exporter leaves "
                         "true on a washout")
        self.assertNotIn("'cancelled'", code,
                         "the chart names one status by hand, so 'unplayed' and 'bye' go "
                         "back to being plotted as rounds that earned nothing")

    def test_the_round_that_nobody_played_still_gets_a_row(self):
        # The fix this item exists to refuse. Dropping the round leaves the chart
        # agreeing with the tiles and lying about the season: fourteen rounds happened,
        # one of them to nobody, and a chart of thirteen says the fifth never came round.
        mapped = re.search(r"rows = (\w+)\.map\(", jslint.code_only(self.chart()))
        self.assertIsNotNone(mapped, "the chart no longer builds a row per match")
        self.assertEqual("matches", mapped.group(1),
                         "the chart plots a filtered list, so the round nobody played is "
                         "missing from it rather than marked in it")

    def test_the_words_beside_the_empty_bar_are_not_the_words_for_a_missing_figure(self):
        # `not scored` is what a played round with no points recorded gets, and it has to
        # stay that: a night that was played and has no figure is a thing to go and fix,
        # and a night nobody played is not. Two states, two wordings.
        rows = self.rows()
        says = self.field(rows, self.VALUE)
        assign = re.search(r"var " + re.escape(says) + r" = (.+?);\n", rows, re.S)
        self.assertIsNotNone(assign, f"the bar's value text, {says!r}, is never worked out")
        branch = assign.group(1)
        self.assertIn(self.night(), branch,
                      "the words beside the bar do not depend on whether the round was "
                      "played, so a washout still reads as a figure that has not arrived")
        self.assertIn("not scored", branch,
                      "a round that was played and has no points recorded no longer says "
                      "so, which is the one case here somebody has to go and fix")
        quiet = re.findall(r"'([^']*not [^']*)'", branch)
        self.assertEqual(len(set(quiet)), 2,
                         f"the unplayed round and the unrecorded figure are given the "
                         f"same words, {quiet}, so the chart says the same thing about a "
                         f"night nobody played and a night somebody has to chase up")

    def test_the_eye_and_the_screen_reader_are_given_the_same_words(self):
        # Measured before this item: the row read `not scored` on the screen and
        # `Round 5: — points against Brindle Bunyips` to anybody listening, because the
        # accessible name was built from `num(pts, 1)` and the visible text from a
        # different expression. Worded twice is how two things come to disagree, and the
        # one that disagreed was the one nobody can see.
        rows = self.rows()
        says = self.field(rows, self.VALUE)
        self.assertRegex(says, r"^\w+$",
                         f"the bar's value text is an expression, {says!r}, so the "
                         f"accessible name below has nothing to share with it")
        aria = self.field(rows, "aria")
        self.assertIn(says, aria,
                      "the row's accessible name is worded separately from the value "
                      "beside the bar, so the two can disagree and only one is visible")
        # Both arms, and not just the one this item added. Asked as "the figure is not
        # read a second time" rather than by counting how many times the answer appears,
        # because the name is allowed to be one sentence or two -- what it is not allowed
        # is its own reading of the points, which is where the disagreement came from.
        self.assertNotIn("pts", aria,
                         "the accessible name works the points out again in one of its "
                         "arms, which is the drift this item found: `not scored` on the "
                         "screen and `— points` to anybody listening")

    def test_the_row_is_quietened_on_the_same_answer(self):
        self.assertIn(self.night(), self.field(self.rows(), "dim"),
                      "the round's label is painted like every other round's, so the one "
                      "row in the chart that reports nothing looks like the rest")

    def test_a_quietened_rows_number_is_drawn_quietly_too(self):
        # `hbarCard` dimmed the label and left the value in `--text-primary` at 600,
        # measured on eight rows of `Points average by team`: the dimmed rows' numbers
        # were the loudest ink on them. Both single-bar branches are checked, because
        # which of the two draws a row depends on how long its bar is -- and a row with
        # no bar at all, which is this item's round, always takes the first.
        shell = self.body("hbarCard")
        at = self.offset_of(shell, "var tw = textWidth(", "the single-bar value text")
        end = self.offset_of(shell, "var hit = svg(", "the end of the row")
        blocks = [b for b in re.split(r"svg\('text',", shell[at:end])[1:]]
        self.assertEqual(len(blocks), 2,
                         f"the single-bar mode draws {len(blocks)} value texts, not the "
                         f"two this test was measured against")
        for i, block in enumerate(blocks):
            with self.subTest(block=i, drawn="weight"):
                weight = re.search(r"'font-weight': ([^,\n]+)", block)
                self.assertIsNotNone(weight, f"value text {i} is drawn at the sheet's "
                                             f"default weight, which this chart's rows "
                                             f"cannot be read against each other at")
                self.assertIn("r.dim", weight.group(1),
                              "a row dimmed for context keeps its number at the weight "
                              "of a result, so the quietest row on the chart has the "
                              "heaviest figure on it")
        # Ink, where the number is on the background: the same answer paints it. Where it
        # is *on* the bar it is left to `inkOn`, which is chosen against whatever the bar
        # is filled with -- muted grey on a series colour is a contrast question and not
        # an emphasis one, so only the weight gives way there.
        outside = re.search(r"fill: ([^,\n]+)", blocks[0])
        self.assertIn("r.dim", outside.group(1) if outside else "",
                      "a dimmed row's number is painted in full ink in the gutter, which "
                      "is where the round nobody played prints its own words")
        inside = re.search(r"fill: ([^,\n]+)", blocks[1])
        self.assertIn("inkOn", inside.group(1) if inside else "",
                      "the number drawn inside the bar stops choosing its ink against "
                      "the bar it sits on")

    def test_the_chart_says_what_the_round_earned_in_the_exporters_words(self):
        # The five sentences under the match list are keyed on `counts_for_ladder`, which
        # is true here, so none of them covers this round: the chart owes its own line.
        # And it takes the clause from the exporter rather than writing one, because the
        # page kept its own pair of sentences for this rule once and they had drifted --
        # one never said what a credited round counts *in*, the other never said the
        # round is not a match played. `scoring.unplayed_worth` quotes both.
        footer = self.field(self.options(), "footer")
        self.assertIn("rules().unplayed_worth", footer,
                      "the chart words what an unplayed round earned itself, so the "
                      "dashboard has two accounts of one rule again")
        self.assertRegex(footer, r"\.length\s*\?",
                         "the sentence is printed under every team's chart, including "
                         "the ones whose every round was played")
        for own in ("scored as nothing", "average", "credited"):
            with self.subTest(own=own):
                self.assertNotIn(own, footer,
                                 f"the sentence spells out {own!r} itself instead of "
                                 f"quoting the exporter, so one preset's clause is "
                                 f"printed under every competition's chart")

    def test_the_sentence_under_the_plot_says_nothing_about_points_itself(self):
        # This sentence was first written "was not played, so it has no points to plot",
        # which is a claim about the round rather than about the chart -- and under
        # ``unplayed = "average"`` it is false. The demo season built twice, once per
        # preset, puts Brindle Magpies on 55.3 points from 13 counted rounds under
        # "zero" and 59.553846 from 14 under "average": the washed-out round earned them
        # 4.25 there. What is the same under both is the card this plot draws from,
        # nothing on it counted, so that is what the sentence is allowed to be about
        # (4.451: the card itself is not always empty, the test below); what the round
        # was worth is the exporter's clause, which follows and is right under both.
        prose = " ".join(re.findall(r"'([^']*)'", self.field(self.options(), "footer")))
        self.assertIn("card", prose,
                      "the sentence does not say where the missing bar went, so an empty "
                      "row still reads as a round the team turned up to and earned "
                      "nothing in")
        self.assertNotIn("points", prose,
                         "the sentence says in the dashboard's own words what the round "
                         "did or did not earn, which is the second account of this rule "
                         "and the one that is wrong wherever a club credits an unplayed "
                         "round the team's own average")

    def test_the_sentence_says_what_the_match_card_says_nothing_on_it_is_counted(self):
        """The sentence says nothing on the round's card is counted, not that it is empty.

        4.451. The sentence read ``R5 was not played, so there is nothing on its card
        to plot.``, over a card that is often not empty: a night called off part way
        keeps the games played before the rain. Measured in headless Chrome
        (/tmp/mcf/scratch/UNPLAYED/before.txt): the demo's own R5 washout is
        6-0 6-5 0-0 0-0, and both its teams' footers said there was nothing on its
        card while the Values table beside them printed 12–5 and 5–12; cardwriter
        cards stopped at 6-1 6-0 3-0 put the same sentence over 15–1. It now says
        the match card's own words for that night, ``Nothing on this card is
        counted``, which is true of a card part played and of a blank one alike,
        with the exporter's clause after it as before. Rejected: two wordings chosen
        on whether the card carries games, which has the page judge a card's
        emptiness for itself; and blanking the chart's games for the round so the
        old sentence came true, which sets the chart against the card and the match
        list below it.
        """
        prose = " ".join(re.findall(r"'([^']*)'", self.field(self.options(), "footer")))
        for said in ("not played, so nothing ", "on its card ", "on their cards ",
                     "is counted"):
            with self.subTest(said=said):
                self.assertIn(said, prose,
                              "the sentence no longer says, in the match card's words, "
                              "that nothing on a round's card is counted")
        for untrue in ("there is nothing", "to plot", "is empty"):
            with self.subTest(untrue=untrue):
                self.assertNotIn(untrue, prose,
                                 "the sentence says a round's card is empty, which a "
                                 "night called off part way contradicts in the "
                                 "Values table beside it")

    def test_the_values_table_prints_what_the_bar_printed(self):
        # Before this item the plot said `not scored` and the table said `—` for the same
        # round, because each worked the figure out for itself. The table takes the
        # words from the row now -- the same string the bar was labelled with, so the
        # two cannot come apart again.
        table = self.field(self.options(), "tableRow")
        says = self.field(self.rows(), self.VALUE)
        self.assertIn("r." + self.VALUE, table,
                      "the Values table works the points cell out for itself, so the "
                      "table and the bar can print two different things about one round")
        self.assertNotIn("_points", table,
                         "the table reads the points off the match again, which is the "
                         "second expression this test exists to remove")

    def test_the_games_on_the_card_of_a_round_nobody_played_are_still_printed(self):
        """The tooltip and the Values table print the games a round nobody played has.

        4.451 kept them, as 3.10 decided: the sentence under the plot now says that
        nothing on the card is counted, and the games beside it are what the card
        prints, 12–5 for the demo's R5 and 15–1 for a card called off at 6-1 6-0 3-0
        (/tmp/mcf/scratch/UNPLAYED/fix/before.txt). Nothing else in the suite held
        them. Rejected: blanking them for such a round, so that the old sentence, that
        there was nothing on its card, came true, which sets the chart against the
        card and against the match list below it.
        """
        night = self.night()
        tip = jslint.code_only(self.region("{ name: 'Games'", "{ name: 'Result'"))
        table = jslint.code_only(self.field(self.options(), "tableRow"))
        self.assertIn("m.home_games", table, "the Values table prints no games")
        for where, code in (("the tooltip", tip), ("the Values table", table)):
            with self.subTest(where=where):
                self.assertNotRegex(code, r"\b(?:" + night + r"|dim)\b",
                                    "the games of a round nobody played are printed "
                                    "otherwise than its card prints them")


class TestTheTeamPageSaysWhichKindOfUncountedMatchEachOneIs(WebAssetCase):
    """Five reasons a listed match is in no figure above it, five sentences.

    A finals round, a card exported twice, two cards that disagree, a level card in a
    grade that cannot draw and the spare reading of a night named two ways are all
    ``counts_for_ladder: false``, and there the resemblance ends. The final needs no
    action; the copy wants a file deleted; the clash and the level night are the two
    that leave a real fixture uncounted, and until somebody fixes the data the figures
    above are missing a night of tennis; the fifth is the odd one out in the other
    direction -- the fixture *is* counted, from the card beside it, and what is
    missing is everybody's appearance. One sentence for all five would be wrong four
    times over, whichever it was.

    Structural, because there is no JavaScript engine here to run the branches.
    """

    SENTENCES = {"duplicate": "second copy", "conflict": "says something different",
                 "finals": "no ladder points", "level": "ended level",
                 "names": "not about who played"}

    def test_each_reason_is_explained_in_its_own_words(self):
        for reason, phrase in self.SENTENCES.items():
            with self.subTest(reason=reason):
                self.guard_for("renderTeam", phrase, emits="box.appendChild")

    def test_each_sentence_is_printed_only_for_the_reason_it_describes(self):
        # The count behind each guard has to be the one counted from that reason.
        # Guarding all three on `matches.length - counted.length` is how a club
        # with a grand final gets told to go and delete a file.
        body = self.body("renderTeam")
        for reason, phrase in self.SENTENCES.items():
            with self.subTest(reason=reason):
                name = self.guard_for("renderTeam", phrase, emits="box.appendChild")
                assign = re.search(r"var\s+" + re.escape(name) + r"\s*=\s*(.+?);\n",
                                   body, re.S)
                self.assertIsNotNone(assign,
                                     f"renderTeam() never assigns {name}")
                self.assertIn(f"'{reason}'", assign.group(1),
                              f"the {reason} sentence is not counted from "
                              f"not_counted === '{reason}'")

    def test_the_page_has_a_sentence_for_every_reason_the_exporter_can_write(self):
        # The list above, checked against the one place the reasons are named. A
        # fifth reason added to `stats` and not to the page is a match listed with
        # nothing to explain it -- which is how the finals sentence came to be
        # counted by exclusion (`!== 'duplicate' && !== 'conflict'`), printing "this
        # is a finals match" over a level card in a grade that cannot draw.
        from matchcentre import stats
        reasons = {value for name, value in vars(stats).items()
                   if name.startswith("NOT_COUNTED_")}
        self.assertEqual(set(self.SENTENCES), reasons,
                         "the page and stats.py disagree about how many ways a "
                         "listed match can be in none of the figures above it")

    def test_no_sentence_is_counted_by_being_none_of_the_others(self):
        # Every count is an equality against its own reason. Counted by exclusion,
        # a bucket silently adopts each new reason as it is added: the match is
        # explained, in the wrong words, and nothing fails.
        body = self.body("renderTeam")
        for reason in self.SENTENCES:
            with self.subTest(reason=reason):
                name = self.guard_for("renderTeam", self.SENTENCES[reason],
                                      emits="box.appendChild")
                assign = re.search(r"var\s+" + re.escape(name) + r"\s*=\s*(.+?);\n",
                                   body, re.S)
                self.assertIsNotNone(assign, f"renderTeam() never assigns {name}")
                self.assertNotIn("!==", jslint.code_only(assign.group(1)),
                                 f"the {reason} count is a catch-all")

    def test_the_reasons_are_read_from_the_document_and_not_re_derived(self):
        # `not_counted` is the exporter's word for it, from one function in
        # `stats`. A page that decided for itself which round labels are finals, or
        # which cards are copies, would print a sentence the ladder disagrees with.
        body = self.body("renderTeam")
        self.assertIn("not_counted", body)
        for reason in self.SENTENCES:
            with self.subTest(reason=reason):
                self.assertIn(f"'{reason}'", body)

    def test_the_clash_sentence_sends_the_reader_somewhere_that_names_the_files(self):
        # The sentence ends "The data health section names the files", which is a
        # promise about renderHealth(), not about the function making it. It is kept
        # by one line down there printing each finding's `detail`, and an F9
        # finding's detail is the list of source paths. Drop that line and the
        # sentence sends a reader to a section that tells them nothing, with the
        # names of the two files it was pointing at nowhere on the page.
        self.assertIn("data health section names the files",
                      self.body("renderTeam"))
        self.assertIn("item.detail", self.body("renderHealth"))


class TestTheCardThatCountedForTheTeamsAndForNobodyElseSaysSo(WebAssetCase):
    """``not_credited``: the second question every match row answers.

    ``not_counted`` is the ladder's question. This is the people's, and on one kind of
    card the two have different answers -- a night two cards agree about and name
    differently is counted for the teams and credited to nobody. The team page's fifth
    sentence is about the pair of cards; these are the three places a *person's*
    figures are built, and each of them has to ask the second question or print a
    figure that contradicts the one beside it.

    Structural, because there is no JavaScript engine here to run the branches.
    """

    def test_the_set_by_set_trail_leaves_out_a_card_that_credited_nobody(self):
        # The trail is built in the browser from the match rows, because the
        # document keeps per-player totals and not their per-set history. So it has
        # to skip exactly what `player_stats` skipped: the tiles above it say three
        # matches and nine sets, and a trail reading four rounds is the same page
        # disagreeing with itself in two figures a reader can count.
        body = self.body("setsOfPlayer")
        self.assertIn("not_credited", body)

    def test_it_skips_them_by_the_one_field_and_not_by_a_list_of_reasons(self):
        # Every reason that credits nobody is in that one field -- a copy, a clash
        # and a disputed name alike -- so the trail asks once. Spelled out as
        # `=== 'duplicate' || === 'conflict' || ...` it silently keeps each reason
        # added after it, and the reason added after it is always the new one.
        code = jslint.code_only(self.body("setsOfPlayer"))
        self.assertNotIn("'duplicate'", code)
        self.assertNotIn("'conflict'", code)

    def test_the_players_table_says_a_night_is_missing_from_every_row(self):
        # The Share column is the visible half: it no longer adds to a hundred,
        # because the team's games include a night no player's do. Said in the
        # caption of the table it is missing from, rather than left to the Matches
        # box further down the page.
        body = self.body("teamPlayersTable")
        name = self.guard_for("teamPlayersTable", "missing from every row",
                              emits="caption").strip()
        assign = re.search(r"var\s+" + re.escape(name) + r"\s*=\s*(.+?);\n", body, re.S)
        self.assertIsNotNone(assign,
                             f"teamPlayersTable() never assigns {name}")
        # And it is the exporter's count of those nights, which is one field of this
        # team's row. Item 3.19: the browser used to group the team's cards by night
        # for it, which is the walk `contribution_gaps` already does, written a second
        # time in a second language. What the field counts is pinned in test_pipeline.
        self.assertIn("nights_not_credited", assign.group(1),
                      f"{name} is worked out here rather than read from the team row: "
                      f"{assign.group(1)!r}")
        # And the sentence reaches the table it was built for. Composed into a
        # variable, a caption can be composed and then not passed.
        self.assertIn("caption: caption", self.body("teamPlayersTable"))

    def test_and_counts_it_from_this_team_and_not_from_the_whole_season(self):
        # Another team's disputed night is another team's caption, and the record is a
        # field of the team whose page this is -- which is what makes that true now
        # that nothing here walks the cards. A lookup into the season's teams would
        # be somebody else's row.
        body = self.body("teamPlayersTable")
        self.assertIn("t.contribution_gap", body,
                      "the caption's counts do not come from this team's own row")
        for wrong in ("S.teams", "IDX.teamBySlug", "IDX.teamsOrdered"):
            self.assertNotIn(wrong, body,
                             f"{wrong} reaches other teams' rows from a function that "
                             f"is captioning one team's table")

    def test_and_counts_the_nights_and_not_the_cards_that_describe_them(self):
        # The cause of this sentence is two cards of one night, and both carry the
        # field, so a count of rows says "2 nights" about one Tuesday. Counted where a
        # fixture is a fixture and not a file -- `contribution_gaps` walks one card per
        # fixture, and test_pipeline measures it on a season built with the pair -- so
        # what is checked here is that this function has stopped counting cards at all.
        code = jslint.code_only(self.body("teamPlayersTable"))
        # `.not_credited` and not `not_credited`: the field this sentence is now read
        # from is `nights_not_credited`, and a ban that catches the record it is
        # supposed to require is a ban that can only be satisfied by saying nothing.
        for gone in (".not_credited", "matchesOfTeam", "Object.keys",
                     "m.round", "m.home", "m.away"):
            self.assertNotIn(gone, code,
                             f"{gone} is back in teamPlayersTable(): the nights are "
                             f"being counted from the cards again, beside a shortfall "
                             f"counted from the document")

    def test_the_match_card_itself_says_the_names_on_it_were_not_credited(self):
        # The card is where the names are printed, under a `Source:` line naming
        # the PDF they were read from. Those names are right -- they are what that
        # file says -- and none of them are in anybody's record, which is only
        # honest to say next to them.
        condition = self.guard_for("matchCard", "Players not credited")
        self.assertIn("not_credited", condition)
        self.assertIn("'names'", condition)

    def test_and_says_it_in_the_line_the_card_shows_while_it_is_closed(self):
        # The team page lists these collapsed, so a reader scrolling the round
        # sees only the summary line. Without a word there, the one card in the
        # season nobody was credited for looks like all the others.
        condition = self.guard_for("matchCard", "players not credited",
                                   emits="bits.push")
        self.assertIn("not_credited", condition)

    def test_and_does_not_reuse_the_word_the_tickbox_beside_it_already_uses(self):
        # `m.disputed` is the "Match Disputed" box ticked on the scorecard -- the
        # club disputing the *result* -- and it prints "disputed" in the same line.
        # Two meanings for one word, two chips apart, is a reader's problem.
        raw = self.body("matchCard")
        self.assertEqual(raw.count("'disputed'"), 1)


class TestTheShareColumnAccountsForWhatItDoesNotAddUpTo(WebAssetCase):
    """A column that declares itself a share of the whole and adds to 96.5%.

    Item 1.2 chose to leave the Share column short rather than credit a team's games to
    somebody this table has no row for: a borrowed player is registered to another
    squad, a rubber with a name cell empty has half a rubber belonging to nobody, and
    guessing in either case puts a stranger's name against a season they were not in.
    Honest, and unreadable, because nothing said so. Measured on the shipped demo:
    brindle-quolls prints 49.6%, 33.8%, 13.1% under a caption sentence of 39 characters
    that says only "Contribution of each registered player." A reader adding that column
    up has no way to tell a design decision from a broken sum.

    Item 3.11 wrote the account and worked all of it out in the browser: the shortfall
    by re-summing the printed rows' own fields, and the reasons by walking the team's
    cards and re-deciding, in JavaScript, which rubbers pay a share -- the night, the
    slot's shape, the roster, the finals, the pair of cards that name a night
    differently. Every one of those decisions is `stats.py`'s, and two implementations
    of one question is how the two come to disagree: this one excluded finals by the
    name of the round, and nothing would have told it if the exporter's scope moved.

    So item 3.19 has the exporter write what it already knows -- a `contribution_gap`
    per team, the games no row holds and a count of each cause -- and this page print
    it. The page is unchanged by that: measured with `/tmp/p319/share.py`, the quolls'
    caption carries the same 253 characters of account it did -- 3.5% short, 9 of the
    256 games, 2 rubbers -- and the other seven teams say nothing extra. Item 3.20 left
    it alone again, re-measured on the same demo: 565 characters with the legend, and
    312 for a team whose column is whole. What these tests pin is that the sentence is
    *read* rather than derived, because a second derivation is the defect and the
    browser numbers above cannot be re-taken from this file.

    Two measurements are why two of them have the shape they do. The trigger is games
    rather than the printed percentages, because brindle-rosellas' complete column sums
    to 99.9% -- rounding noise, and enough to make a percentage trigger either fire on a
    team with nothing wrong or need a tolerance nobody can justify. And the denominator
    in the sentence is the record's own, which is the total the cells were divided by.
    It is also the total the Games column prints, because the walk takes the list the
    ladder is computed from: the mutation check for this item found that it did not, and
    that under `draws = false` the caption would have read "of the 37" beneath a row
    reading 18 -- a column announced as 205% of the team's games. That agreement is
    asserted per team in test_pipeline, so printing `t.games_won` here would be the same
    number read from a second place, correct only for as long as nobody moves either
    scope.

    One narrowing came with the change, deliberately. The caption's first sentence
    counts nights credited to nobody, and it now counts the record's, which are the
    regular season's because that is this column's scope. A disputed *finals* night is
    missing from every row of this table and is no longer named in its caption: the card
    says it itself, the round's note above it says it, and the health section names the
    pair of files, so the reader is not left without it.

    Item 3.20 is the same column in the other direction, which both of the above assume
    cannot happen. A rubber with more names on the scorecard than the format has places
    pays each of them a whole place's share, so the rows can hold more than the team ever
    won. Measured with `/tmp/p320/overfull.py`: one singles rubber carrying two names
    makes a 94-game team's column read 106.4%, `gap.games` is -6.0, and the caption said
    nothing at all, because a sentence that only knows how to fall short has nothing to
    say about a column that overshoots. And `/tmp/p320/cancel.py` is the sharper one: a
    card with two doubles rubbers a name short and one singles rubber a name over comes
    to 94 of 94 games and a column adding to exactly 100.0%, with three rubbers wrong on
    the scorecard. Under the old structure -- the causes nested inside the shortfall --
    that season's caption named none of them, so the three clauses are now beside the
    sum rather than inside it and the sum is one signed figure whose net is what a
    reader's own addition shows.

    Structural, like every check in this file: there is no JavaScript engine here, so
    the browser numbers above were taken by hand with the probe named at the top, and
    what these tests read is the source of the function that prints them.
    """

    PHRASE = "short of 100%"
    OVER = "past 100%"
    STRANGERS = "no row in this table"
    BLANKS = "short a name"
    CROWDED = "more names on the scorecard"
    POINTER = "data health section names"
    RECORD = "t.contribution_gap"
    # The record may be defaulted where it is read, and item 3.20 defaults it: two
    # fields of it are newer than documents that exist. Which of the guarding shapes
    # is used is not the claim -- that no read can throw is -- so the tests below
    # accept the read either way and check the property separately.
    READ = r"var (\w+) = " + re.escape(RECORD) + r"(?: \|\| \{\})?;"
    # Which field a local holds, however the read is written: defaulted, guarded by a
    # ternary, or bare. Whether it is any of those is a separate test, below.
    FIELD = r"var %s = [^;\n]*\b%s\.%s\b[^;\n]*;"

    def region(self) -> str:
        """The block that prints the account: from the record it reads to the table.

        Anchored on the record rather than on the sentence, because fetching it is the
        first half of the claim and the tests below have to read both -- what is
        fetched, and what it is then said to mean.

        Read raw, because most of this item is string literals and `code_only` blanks
        those -- the lesson of 3.10, where a wording check passed against a page still
        carrying the wording it forbade.
        """
        body = self.body("teamPlayersTable")
        at = self.offset_of(body, self.PHRASE, "the shortfall sentence")
        reads = list(re.finditer(self.READ, body[:at]))
        self.assertTrue(reads,
                        f"{self.PHRASE!r} is printed with no {self.RECORD} read into a "
                        f"local above it")
        end = body.find("return el(", reads[-1].start())
        self.assertGreater(end, reads[-1].start(),
                           "teamPlayersTable() returns nothing after it")
        return body[reads[-1].start():end]

    def record(self, region: str) -> str:
        """What the record is called here -- whatever it is called.

        The tests below have to name it to say what is read off it, and a local's name
        is not the claim: renaming it is the same page. So it is discovered once, from
        the read the region begins with, and the checks are written against the name
        that read gives.
        """
        read = re.match(self.READ, region)
        self.assertIsNotNone(read, f"the region does not open on a read of "
                                   f"{self.RECORD}: {region[:60]!r}")
        self.assertEqual(region.count(self.RECORD), 1,
                         f"{self.RECORD} is reached more than once, so each clause "
                         f"guards itself and one of them will not")
        return read.group(1)

    def local_for(self, region: str, field: str) -> str:
        """The local a named field of the record is read into, whatever it is called."""
        gap = self.record(region)
        found = re.search(self.FIELD % (r"(\w+)", re.escape(gap), field), region)
        self.assertIsNotNone(found, f"no local is read from {gap}.{field}")
        return found.group(1)

    def clause(self, phrase: str) -> str:
        """The condition of the `if` a given wording of the caption is under."""
        return self.guard_for("teamPlayersTable", phrase, emits="caption +=")

    def sentence(self, region: str, phrase: str) -> str:
        """The one `caption +=` statement a given wording is part of."""
        at = self.offset_of(region, phrase, "the sentence")
        opens = region.rfind("caption +=", 0, at)
        self.assertGreater(opens, 0, f"{phrase!r} is not part of a caption sentence")
        return region[opens:region.index(";", at)]

    def calls(self, code: str, fn: str) -> list[str]:
        """The argument of each `fn(...)` call in a stretch of code, unparsed."""
        return [found.group(1)
                for found in re.finditer(re.escape(fn) + r"\(([^()]*)\)", code)]

    def prints_the_negation_of(self, region: str, argument: str, what: str) -> bool:
        """Whether an argument is `-what`, directly or through a local holding it.

        Negating twice in one sentence and negating once into a local are the same
        sentence, so both are accepted -- but `var over = off` is not, which is the
        defect this is here to reject: a reader shown "-6 games more than 94".
        """
        argument = argument.strip()
        if argument == "-" + what:
            return True
        if re.fullmatch(r"\w+", argument):
            return bool(re.search(r"var %s = -%s\s*;" % (re.escape(argument),
                                                         re.escape(what)), region))
        return False

    def test_the_caption_says_the_column_falls_short_of_a_hundred(self):
        body = self.body("teamPlayersTable")
        self.offset_of(body, self.PHRASE, "the shortfall sentence")
        # And in the caption the table is given, not in a local composed and dropped.
        self.assertRegex(self.region(), r"caption\s*\+=",
                         "the shortfall sentence never reaches the caption")
        self.assertIn("caption: caption", body,
                      "teamPlayersTable() no longer passes the caption to the table")

    def test_it_speaks_after_the_sentence_about_the_night_nobody_was_credited_for(self):
        # Order is the argument: the disputed sentence says a night went to nobody, and
        # this one says how much of the column that cost. Reversed, the number arrives
        # before the thing it is a number for.
        body = self.body("teamPlayersTable")
        self.assertLess(self.offset_of(body, "missing from every row", "the night"),
                        self.offset_of(body, self.PHRASE, "the shortfall"),
                        "the shortfall sentence now comes first, so a reader meets a "
                        "percentage before the night that explains part of it")

    def test_the_shortfall_is_the_one_the_exporter_measured(self):
        code = jslint.code_only(self.region())
        self.assertIn("gap.games", code,
                      "the shortfall is not read from the field it was written in")
        # And is not also worked out here. Either sum is over the same shares, so a
        # second one agrees until the day it stops: this is the harder one to get right
        # -- `games_won` is rounded for storage and the denominator has to be taken from
        # a row rather than summed over them -- and it is the one with no test season
        # behind it, because the exporter's has nine.
        # `var credited` and not `credited`, which is a substring of the `uncredited`
        # the sentence above this one counts nights with.
        for gone in ("p.contribution", "roster.forEach", "var credited", "divides",
                     "Math.max"):
            with self.subTest(gone=gone):
                self.assertNotIn(gone, code,
                                 f"{gone} is back: the shortfall is being re-derived "
                                 f"from the printed rows beside the figure the "
                                 f"exporter wrote for it")

    def test_it_prints_the_total_the_cells_were_divided_by(self):
        code = jslint.code_only(self.region())
        self.assertIn("gap.team_games_won", code,
                      "the total in the sentence is not the one the cells divided by")
        for wrong in ("t.games_won", "t.games_for", "ladder"):
            with self.subTest(wrong=wrong):
                self.assertNotIn(wrong, code,
                                 f"{wrong} is the same total from a second place: it "
                                 f"agrees with the record only while the walk's scope "
                                 f"and the ladder's stay the same list, and when they "
                                 f"did not the sentence read 'of the 37' under a row "
                                 f"printing 18")

    def test_the_percentage_is_the_documents_own_and_not_one_divided_here(self):
        # Rounded once, by `round_display`, like every other percentage in the document
        # -- and by the same code that decided the games it is a percentage of. Divided
        # here it would be a second rounding of a second subtraction.
        code = jslint.code_only(self.region())
        self.assertIn("pct(gap.share_pct)", code,
                      "the percentage is not the document's own, printed through pct()")
        for arithmetic in ("100 *", "/"):
            with self.subTest(arithmetic=arithmetic):
                self.assertNotIn(arithmetic, code,
                                 f"{arithmetic!r} is in the block: the percentage is "
                                 f"being computed in the browser from a total and a gap "
                                 f"it also computed, and rounded a second time")

    def test_a_column_that_adds_up_says_nothing_extra(self):
        region = self.region()
        off = self.local_for(region, "games")
        condition = self.clause(self.PHRASE)
        self.assertEqual(condition, f"{off} > 0",
                         f"the shortfall sentence is guarded by {condition!r}: only a "
                         f"strictly positive shortfall is a shortfall, and >= 0 tells "
                         f"all eight demo teams they fall 0.0% short")
        self.assertNotIn("share_pct", condition,
                         "the guard is the rounded percentage, so a team four "
                         "hundredths of a game short is told it falls 0.0% short and "
                         "rosellas' complete 99.9% column is told it is short 0.1")

    def test_a_column_that_runs_past_a_hundred_says_that_instead(self):
        # The measurement at the top of this class: 106.4% of a 94-game team, and the
        # sentence above cannot describe it -- "falls -6.4% short of 100%" is a puzzle,
        # and dropping the sign to reuse the wording says the opposite of what happened.
        # So the other direction is its own sentence, on its own side of the same test.
        region = self.region()
        off = self.local_for(region, "games")
        self.assertEqual(self.clause(self.OVER), f"{off} < 0",
                         f"the overshoot sentence is not guarded on {off} being "
                         f"negative, so a column that adds up is told it overshoots")
        gap = self.record(region)
        sentence = self.sentence(region, self.OVER)
        self.assertIn("caption +=", sentence,
                      "the overshoot sentence never reaches the caption")
        code = jslint.code_only(sentence)
        # Both of the record's figures are negative in this direction, and the sentence
        # has already said which way the column went: printed as they stand, a reader is
        # shown "runs -6.4% past 100%" and "credited with -6 games more than 94".
        for fn, field in (("pct", f"{gap}.share_pct"), ("shareNum", off)):
            with self.subTest(printed=fn):
                self.assertTrue(
                    any(self.prints_the_negation_of(region, argument, field)
                        for argument in self.calls(code, fn)),
                    f"none of the {fn}() calls in the overshoot sentence prints the "
                    f"negation of {field}: {self.calls(code, fn)}")
        self.assertIn(f"{gap}.team_games_won", code,
                      "the overshoot sentence gives no total to compare against, so the "
                      "reader is told a number of games and not what it is more than")

    def test_a_team_is_never_told_both_at_once(self):
        # Chained, not two independent ifs. One subtraction has one sign, so the pair is
        # a choice; written as two tests of the same local, an editor who later widens
        # one of them to >= 0 gets a caption that says the column falls 0.0% short *and*
        # runs 0.0% past a hundred.
        region = self.region()
        short = self.offset_of(region, self.PHRASE, "the shortfall sentence")
        over = self.offset_of(region, self.OVER, "the overshoot sentence")
        between = region[short:over]
        self.assertIn("else", between,
                      "the two directions are separate ifs, so nothing but the arithmetic "
                      "stops a team being told its column is short and over at once")

    def test_half_a_game_is_printed_as_half_a_game(self):
        # A doubles rubber splits in half, so a gap arrives in halves: the one in
        # TestADoublesRubberMissingANameCreditsHalfAndKeepsTheRest is 3.0 of 94 and the
        # half of a 7-game rubber would be 3.5. `num()` rounds it to 4 and the sentence
        # then contradicts the column it is about -- in either direction, so both
        # sentences are read, and the totals they compare against are halves too.
        for phrase in (self.PHRASE, self.OVER):
            with self.subTest(phrase=phrase):
                code = jslint.code_only(self.sentence(self.region(), phrase))
                self.assertNotRegex(code, r"(?<!share)num\(",
                                    "the games are printed through num(), which rounds "
                                    "a half away")
                self.assertIn("pct(", code,
                              "the percentage is not printed through pct()")

    def test_each_cause_it_found_is_named_in_its_own_clause(self):
        # One guard per cause, never "and otherwise it must be the other one". The three
        # are independent and a season can have all of them: the cancelling card
        # measured for 3.20 has two rubbers short a name and one with a name too many.
        region = self.region()
        gap = self.record(region)
        # And each clause counts the cause it describes. Swapped, every sentence is
        # printed for every season and each names the wrong one: the quolls' two rubbers
        # with a stranger in them would read "short a name on the scorecard", which is a
        # different thing to go and look at.
        for phrase, field in ((self.STRANGERS, "rubbers_with_outsiders"),
                              (self.BLANKS, "rubbers_short_a_name"),
                              (self.CROWDED, "rubbers_overfull")):
            with self.subTest(phrase=phrase):
                counter = self.clause(phrase)
                self.assertRegex(region, self.FIELD % (
                                     re.escape(counter), re.escape(gap), field),
                                 f"{phrase!r} is printed under {counter!r}, which is "
                                 f"not {gap}.{field} -- so a season with one cause is "
                                 f"told about the other, by the other's number")
        first = self.offset_of(region, self.STRANGERS, "the first cause")
        last = self.offset_of(region, self.POINTER, "the pointer")
        self.assertNotIn("else", region[min(first, last):max(first, last)],
                         "the clauses are chained, so a season with both causes is "
                         "told about whichever the chain reaches first")

    def test_the_causes_are_named_even_when_the_games_come_out_even(self):
        # The measurement that moved them: a card with two rubbers short a name and one
        # rubber with a name too many nets to 94 of 94 games and a column adding to
        # exactly 100.0%, and three rubbers on it are wrong. Nested under the sum -- the
        # shape this had when the sum only went one way -- that season's caption named
        # none of them, and the table a club would use to find them said nothing.
        #
        # Checked as "the sum is not mentioned again", which is the property rather than
        # a brace count: the clauses below cannot be gated on a figure they never read.
        region = self.region()
        off = self.local_for(region, "games")
        sentence = self.sentence(region, self.OVER)
        after = region[region.index(sentence) + len(sentence):]
        self.assertNotRegex(after, r"\b%s\b" % re.escape(off),
                            f"{off} is read again below the two sentences about it, so a "
                            f"season whose faults cancel is told about none of them")

    def test_it_does_not_walk_the_cards_to_work_out_why(self):
        # The item itself. Each of these was one decision in the scan this replaced, and
        # each is `stats.py`'s: which nights count as played, which rubbers are in the
        # regular season, how many names a rubber of that shape is played by, who is on
        # the roster, who was borrowed. Re-deciding any of them here is a second answer
        # to a question the document already answers on the row this function is given.
        code = jslint.code_only(self.body("teamPlayersTable"))
        for gone in ("countsAsPlayed", "'finals'", "not_counted", "fill_ins",
                     "st.doubles", "? 2 : 1", "names.length", "home_players",
                     "away_players", "indexOf", "+= 1"):
            with self.subTest(gone=gone):
                self.assertNotIn(gone, code,
                                 f"{gone} is back in teamPlayersTable(): the page is "
                                 f"deciding for itself which rubbers pay a share, "
                                 f"beside a shortfall the exporter counted")

    def test_the_pointer_is_offered_only_when_there_is_something_to_point_at(self):
        # The house pattern, read off renderTeam: three of its five uncounted notes end
        # with this pointer and two do not, and the two that do not are the ones the
        # health section has nothing to name. A night nobody was credited for is named
        # there as a pair of files, not as a rubber, so on a season whose whole
        # shortfall is that night this promise would send a reader looking for rubbers
        # the section does not list.
        region = self.region()
        found = self.clause(self.POINTER)
        causes = {self.clause(self.STRANGERS), self.clause(self.BLANKS),
                  self.clause(self.CROWDED)}
        sums = re.search(r"var %s = (\w+) \+ (\w+) \+ (\w+);" % re.escape(found), region)
        self.assertIsNotNone(sums,
                             f"the pointer is offered on {found!r}, which is not the "
                             f"three causes added together -- so a season whose fault "
                             f"is one of the kinds left out is offered nothing, or a "
                             f"season with none is sent to look for rubbers")
        self.assertEqual(set(sums.groups()), causes,
                         f"the pointer counts {set(sums.groups())} and the clauses "
                         f"count {causes}")
        self.assertIn('id="health"', self.assets()["index.html"],
                      "the section the caption promises is not in the shell")

    def test_a_document_without_the_record_keeps_its_table(self):
        # `contribution_gap` is new in this version, `rubbers_overfull` is newer again,
        # and `SEASON_FORMAT` was not bumped for either, because nothing in the document
        # changed meaning -- so a season.json built before this app.js may be missing the
        # whole record or one field of it. Every read is of a local that is then zero,
        # which is already the say-nothing case; reading through a missing record instead
        # loses the whole table over a sentence about it.
        #
        # Which shape does that is not the claim, so either is accepted: the record
        # defaulted once where it is read, or each field guarded where it is read. Two
        # properties are pinned instead. No read can throw, which is what a missing
        # record does to a bare one. And no local is left undefined, which is not the
        # same thing: `if (undefined)` is quietly false, so the clause is only silent --
        # but `strangers + blanks + crowded` with one of them undefined is NaN, and then
        # a season with a cause named above it is offered no pointer to it.
        region = self.region()
        gap = self.record(region)  # read once, and asserted to be read once
        defaulted = re.match(self.READ.replace(r"(?: \|\| \{\})?", r" \|\| \{\}"), region)
        for field in ("nights_not_credited", "games", "rubbers_with_outsiders",
                      "rubbers_short_a_name", "rubbers_overfull"):
            with self.subTest(field=field):
                read = re.search(r"var \w+ = ([^;]*\b%s\.%s\b[^;]*);"
                                 % (re.escape(gap), field), region)
                self.assertIsNotNone(read, f"nothing reads {gap}.{field}")
                expression = read.group(1)
                if not defaulted:
                    self.assertRegex(expression, r"^%s\s*(\?|&&)" % re.escape(gap),
                                     f"{gap}.{field} is read off a record that may not "
                                     f"be there, and nothing has defaulted it: an older "
                                     f"document throws here and loses the table")
                self.assertTrue("|| 0" in expression
                                or re.search(r":\s*0\b", expression),
                                f"{gap}.{field} is read without a zero behind it: "
                                f"{expression!r} is undefined on a document built "
                                f"before the field existed")
        # And the two sentences about the sum are asked of the local, not of the record,
        # so a missing field is a silent caption rather than a comparison against
        # undefined -- which is false both ways and would print neither sentence anyway,
        # but only until somebody writes the third.
        for phrase in (self.PHRASE, self.OVER):
            with self.subTest(phrase=phrase):
                self.assertNotIn(gap + ".", self.clause(phrase),
                                 "the clause reaches into the record instead of the "
                                 "local it was already read into")


class TestACountOfGamesIsWordedForTheNumberItIs(WebAssetCase):
    """A count of one game read "1 games" in eight places, and its verb "belong".

    Item 4.456. The page has one function for a counted noun, ``plural``, and the
    Tightest match tile already worded its margin through it. Eight places joined a
    count of games to a literal ``' games'`` instead: the Biggest margin tile beside
    it, the head-to-head cell's hover, the spoken labels of Games for and against
    (both counts), of Strength by set slot and of a player's Singles and doubles, the
    player page's Team share tile, whose literal put "team" between the count and
    the noun, and the team page's Share caption in both directions, the first of
    which also put "belong" after its count. Measured in headless Chrome on
    fictional cardwriter cards (/tmp/mcf/scratch/WORDING/probe.py, before.txt, and
    for the tile /tmp/mcf/scratch/WORDING/fixup/probe2.py, before.txt): a 20-19
    night read "Tightest match 1 game" beside "Biggest margin 1 games"; a 24-1 night
    "Hilltop Aces: 1 games won, 24 games lost", "Hilltop Aces won 1 games, Riverside
    Rockets won 24", "Doubles 1: 1 games won, 6 lost, record 0–1", a player's
    "Doubles: 1 games won, 12 lost" and that player's Team share "0.5 of 1 team
    games won"; a first single played by a name on no roster "1 of the 12 games the
    team won belong to no row above."; and two names on that single "credited with
    1 games more than the 12 the team won."

    Read as text, as the suite runs no JavaScript: no figure is joined to a literal
    that begins ``' games'``, or one word and then ``games``, on its own line or
    across a line break, except the places where the word follows a pronoun or a
    name; every ``plural(x, 'game')`` follows the figure ``x`` it is the noun of,
    or that figure and one word; each of the eight keeps its noun; and the caption's
    verb is worded by the same function. Rejected: a games-only helper beside
    ``plural``, a second function for what one already does; a branch on ``=== 1``
    at each site, which 4.195 rejected as a second rule for one word; leaving the
    spoken labels, which are the text a screen-reader user is given in place of the
    bar; dropping the noun after the first count ("1 won, 24 lost"), which changes
    the shape of every label to avoid one word; and the tile reworded as "of the 16
    games the team won", which changes the tile on every player page to mend the
    ones whose team won one game.
    """

    # Where ' games' follows something that is not a count: a pronoun for the
    # rubbers the sentence has just counted, twice, and a player's name in the file
    # name a table downloads as.
    NOT_COUNTS = ("'their') + ' games are credited more than once.'",
                  "'their') + ' games count for the team and '",
                  "p.name + ' games differential by opponent'")
    JOINED = re.compile(r"([^\s+]+)\s*\+\s*('\s+(?:[\w-]+\s+)?games?\b[^'\n]*')")
    NOUN = re.compile(r"\bplural\(\s*([^,()]+?)\s*,\s*'game'\s*\)")
    # Each function that prints a count of games, and how many it prints.
    COUNTS = (("renderOverview", 2), ("h2hGridCard", 2), ("gamesForAgainstChart", 2),
              ("slotStrengthChart", 1), ("disciplineChart", 1), ("renderPlayer", 1),
              ("teamPlayersTable", 2))

    def joined(self) -> list[str]:
        said = jslint.without_comments(self.assets()["app.js"])
        return [f"{found.group(1)} + {found.group(2)}"
                for found in self.JOINED.finditer(said)]

    def test_the_search_reads_a_join_across_a_line_break(self):
        # The Share caption's second sentence broke its line between the count and the
        # word, and a search a line at a time does not see that one.
        self.assertRegex("shareNum(-off) +\n      ' games more than the '", self.JOINED)

    def test_the_search_reads_a_word_between_the_count_and_its_noun(self):
        # The Team share tile put "team" between them, and a search for the noun
        # straight after the count does not see that one.
        self.assertRegex("num(p.contribution.team_games_won) + ' team games won'",
                         self.JOINED)

    def test_no_count_is_joined_to_a_literal_games(self):
        joined = self.joined()
        # Seen at all, or the search below passes by reading nothing.
        for each in self.NOT_COUNTS:
            with self.subTest(not_a_count=each):
                self.assertIn(each, joined,
                              "one of the places ' games' follows a word rather "
                              "than a count is gone, so this list no longer says "
                              "what the search is finding")
        for each in joined:
            with self.subTest(joined=each):
                self.assertIn(each, self.NOT_COUNTS,
                              "a count of games is joined to the literal word, so a "
                              "count of one reads '1 games'")

    def test_every_plural_of_game_follows_the_count_it_is_the_noun_of(self):
        # plural() given one figure and printed after another says "1 game" beside a
        # 24, which is the same mistake made more quietly.
        said = jslint.without_comments(self.assets()["app.js"])
        nouns = list(self.NOUN.finditer(said))
        self.assertTrue(nouns, "nothing in app.js says plural(..., 'game')")
        for noun in nouns:
            figure = re.escape(noun.group(1).strip())
            before = said[max(0, noun.start() - 200):noun.start()]
            with self.subTest(counts=noun.group(1).strip()):
                self.assertRegex(
                    before,
                    r"(?:\b(?:num|shareNum)\(\s*%s\s*\)|\(\s*%s\s*\)|(?<![\w.$])%s)"
                    r"\s*\+\s*' (?:[\w-]+ )?'\s*\+\s*$" % (figure, figure, figure),
                    "plural(..., 'game') does not follow the figure it counts")

    def test_each_count_keeps_its_noun(self):
        # The rejected way out: "1 won, 24 lost" has no noun to get wrong.
        for fn, count in self.COUNTS:
            with self.subTest(fn=fn):
                said = jslint.without_comments(self.body(fn))
                self.assertEqual(len(self.NOUN.findall(said)), count,
                                 f"{fn}() words a different number of counts of "
                                 f"games through plural() than the {count} it prints")

    def test_the_shortfalls_verb_agrees_with_the_count_it_follows(self):
        # "1 of the 12 games the team won belong to no row above." Read in that one
        # sentence: "half of it belongs to nobody" further down is right as it stands.
        said = jslint.without_comments(self.body("teamPlayersTable"))
        at = self.offset_of(said, "short of 100%", "the shortfall sentence")
        sentence = said[said.rfind("caption +=", 0, at):said.index(";", at)]
        literal = re.search(r"'[^'\n]*\sbelongs?\b[^'\n]*'", sentence)
        self.assertIsNone(literal and literal.group(0),
                          "the caption's verb is a literal, so it is plural whatever "
                          "its count")
        first = re.search(r"shareNum\((\w+)\)\s*\+\s*' of the '", sentence)
        self.assertIsNotNone(first, "the shortfall sentence no longer begins with its "
                                    "count of games")
        self.assertRegex(sentence,
                         r"plural\(\s*%s\s*,\s*'belongs'\s*,\s*'belong'\s*\)"
                         % re.escape(first.group(1)),
                         "the verb is not worded for the count it follows")


class TestTheSeasonsOwnTotalsCountEachNightOnce(WebAssetCase):
    """``not_totalled``: the third question, and the front page's tiles ask it.

    Matches played, sets completed, games played, the tightest night and the biggest
    margin are the six figures a club reads first, and the page counts all of them
    itself from the match rows -- so it counts a card exported twice twice, and reads
    "16 of 15 in the draw" with a progress meter past its own end. Two cards that
    disagree add a night of sets to a season the ladder below says nobody played.

    Neither field it already had answers this. ``counts_for_ladder`` is false on a
    final and on a level night, which are tennis that was played and belong in these
    totals; ``not_credited`` is set on the kept card of a night named two ways, whose
    scoreline is the night. So the exporter says which rows the aggregates were built
    from, and the page asks that.

    Structural, because there is no JavaScript engine here to run the filter.
    """

    def played_list(self) -> str:
        """The expression ``renderOverview`` filters the match rows with."""
        body = self.body("renderOverview")
        assign = re.search(r"var\s+played\s*=\s*(.+?);\n", body, re.S)
        self.assertIsNotNone(assign, "renderOverview() never assigns played")
        return assign.group(1)

    def test_the_tiles_count_the_rows_the_document_says_were_counted(self):
        # Asked as a negation, and checked as one: the field carries a reason, so a
        # filter keeping the rows that *have* one counts the copies and nothing else.
        self.assertRegex(self.played_list(), r"!\s*\w+\.not_totalled\b")

    def test_and_not_the_ones_the_ladder_counted(self):
        # The wrong fix, and the one an exclusion list arrives at: filtering these
        # totals by the ladder's field drops a grand final out of the sets the season
        # says were played, and a club whose grade cannot draw loses every level
        # night from its games total as well.
        self.assertNotIn("counts_for_ladder", self.played_list())
        self.assertNotIn("not_counted", self.played_list())

    def test_nor_the_ones_anybody_was_credited_for(self):
        # The other wrong fix, one card wide: the kept reading of a night named two
        # ways credited nobody and is still the night both teams played.
        self.assertNotIn("not_credited", self.played_list())

    def test_it_asks_the_one_field_and_not_a_list_of_reasons(self):
        # Spelled out as `!== 'duplicate' && !== 'conflict'`, the totals silently
        # adopt whatever reason is added next -- which is how this page came to count
        # a copy of a night in the first place.
        code = jslint.code_only(self.body("renderOverview"))
        self.assertNotIn("'duplicate'", code)
        self.assertNotIn("'conflict'", code)
        self.assertNotIn("'names'", code)

    def test_the_tile_counts_the_filtered_list_and_the_meter_is_handed_none_of_it(self):
        # Unfiltered, the tile prints "16 of 15". The progress box was handed the same
        # count until 4.194, and now prints the document's count of the regular
        # rounds' fixtures, which keeps one card per fixture as this list does.
        body = self.body("renderOverview")
        self.assertNotIn("seasonProgress(played.length", body)
        self.assertIn("tile('Matches played', String(played.length)", body)

    def test_nothing_in_the_view_counts_from_the_unfiltered_list_instead(self):
        # The filter is only worth having if it is the one door into the match rows.
        # A second `S.matches` here is the next total counted the old way.
        self.assertEqual(self.body("renderOverview").count("S.matches"), 1)


class TestTheTightestMatchSaysWhenItWasLevelOnGamesAndDecidedOnSets(WebAssetCase):
    """The tile printed "0 games" over a match one side won, and nothing said how.

    Item 4.427. Under a scheme decided on games and then sets, a night level on games
    is won on sets, and its games margin is 0 (export.py). ``renderOverview`` takes the
    smallest margin among the decided matches, which is right -- a night level on
    games and separated on sets is the tightest result there is -- and the tile
    printed the margin, the two teams and the round, never ``decided_by``. The
    qualifier was the match card's alone. Measured in headless Chrome on fictional
    cardwriter cards (/tmp/mcf/scratch/WORDING/fixup/probe2.py, before.txt): Riverside
    Rockets v Hilltop Aces 6-0 3-6 3-6 0-0, 12-12 and sets 1-2, beside a 21-15 night,
    builds with no error and is published with ``tied_on_games`` true and
    ``decided_by`` 'sets'; the tile read "Tightest match 0 games" over "Riverside
    Rockets v Hilltop Aces, round 1", while the Matches view's line for the same
    night read "sets 1–2 · points 3.2 / 5.2 · level on games, decided on sets". The
    owner's export has two played cards level on games and decided on sets, and in
    each competition whose tightest match is one of them the tile read the same.

    So the foot adds the card's words, and a match decided on games is told nothing
    new. One function, ``tiebreakWords``, says them, and the match card and the tile
    both ask it: written into the tile as well, the words and their test were two
    copies of one rule, which two edits can part. Read as text, as the suite runs no
    JavaScript: the words are written once in the page, in that function, under the
    test the match card always used, term for term; the card pushes what it says of
    its match; and the tile's foot names the two sides and the round and then what
    the function says of the match the tile chose, through ``dotted``, the page's one
    rule for a line of parts, which drops the empty answer. Rejected: the condition
    written out again in the tile, which a test could only hold equal to the card's;
    "won on sets", a second wording for one fact; leaving matches decided on sets out
    of the tile, which would name a 1-game night tighter than a level one; keeping
    cards with an F6 error out of it, which parts the tiles from the ladder beside
    them; and naming the winner instead, which leaves "0 games" beside a winner
    unexplained and drops the two sides a tightest match is about.
    """

    PHRASE = "level on games, decided on sets"
    WORDS = "tiebreakWords"

    def tile_call(self, label: str) -> str:
        """The arguments of the one ``tile(...)`` call with *label*, comments out."""
        body = self.body("renderOverview")
        code = jslint.code_only(body)
        said = jslint.without_comments(body)
        at = self.offset_of(said, f"tile('{label}'", f"the {label} tile")
        opens = at + len("tile")
        return said[opens + 1:closing_bracket(code, opens)]

    def selected(self) -> str:
        """What the Tightest match tile calls the match it is about."""
        found = re.match(r"\s*'Tightest match',\s*(\w+)\s*\?",
                         self.tile_call("Tightest match"))
        self.assertIsNotNone(found, "the Tightest match tile no longer prints a "
                                    "match it chose")
        return found.group(1)

    @staticmethod
    def top_level(text: str, sep: str) -> list[str]:
        """*text* split at each *sep* outside every bracket and string."""
        code = jslint.code_only(text)
        parts, depth, start = [], 0, 0
        for index, char in enumerate(code):
            depth += {"(": 1, "[": 1, "{": 1, ")": -1, "]": -1, "}": -1}.get(char, 0)
            if depth == 0 and code.startswith(sep, index):
                parts.append(text[start:index].strip())
                start = index + len(sep)
        return parts + [text[start:].strip()]

    def test_the_words_are_written_once_in_the_one_function_both_ask(self):
        said = jslint.without_comments(self.assets()["app.js"])
        self.assertEqual(said.count(self.PHRASE), 1,
                         "the words are written in more than one place, so the tile "
                         "and the match card can say different things of one night")
        self.assertIn(self.PHRASE, jslint.without_comments(self.body(self.WORDS)),
                      f"the words are not {self.WORDS}()'s")
        card = jslint.without_comments(self.body("matchCard"))
        match = re.search(r"function matchCard\((\w+)", card).group(1)
        held = re.search(r"var (\w+) = %s\(%s\);" % (self.WORDS, match), card)
        self.assertIsNotNone(held, f"the match card no longer asks {self.WORDS}() "
                                   f"about its match")
        self.assertRegex(card, r"if \(%s\) bits\.push\(%s\);"
                               % (held.group(1), held.group(1)),
                         "the match card does not print what the function says, or "
                         "prints it where it says nothing")

    def test_the_function_asks_what_the_match_card_always_asked(self):
        # Term for term: a night decided on sets that was not level on games is not
        # "level on games", and a draw level on every basis was decided by nothing.
        body = jslint.without_comments(self.body(self.WORDS))
        match = re.search(r"function %s\((\w+)\)" % self.WORDS, body).group(1)
        guard = self.guard_for(self.WORDS, self.PHRASE, emits="return")
        self.assertEqual({term.strip() for term in guard.split("&&")},
                         {f"{match}.tied_on_games", f"{match}.decided_by === 'sets'"},
                         "the words are said of nights the match card never said "
                         "them of, or not of all of them")
        self.assertEqual(re.findall(r"\breturn\b([^;]*);", body),
                         [f" '{self.PHRASE}'", " ''"],
                         "the function says something other than the words, or "
                         "nothing, of the nights it is not asked about")

    def test_the_words_join_the_foot_that_names_the_match(self):
        name = self.selected()
        foot = self.top_level(self.tile_call("Tightest match"), ",")[-1]
        joined = re.fullmatch(r"%s \? dotted\(\[(.*)\]\)\s*: 'no decided matches'"
                              % name, foot, re.S)
        self.assertIsNotNone(joined, "the Tightest match tile's foot is no longer a "
                                     "line of parts joined through dotted()")
        parts = self.top_level(joined.group(1), ",")
        self.assertEqual(len(parts), 2, "the foot is not the match and then its words")
        self.assertRegex(parts[0], r"^teamShort\(%s\.home\) \+ ' v ' \+ "
                                   r"teamShort\(%s\.away\)" % (name, name),
                         "the foot no longer begins with the two sides of the match")
        self.assertEqual(parts[1], f"{self.WORDS}({name})",
                         "the foot does not end with what the function says of the "
                         "match the tile chose")


class TestARubberScoredOtherwiseThanItPrintedSaysItsGamesOnCourt(WebAssetCase):
    """The pages read 4.421's fields: the games printed, and who played.

    A rubber ticked Forfeit against one side is scored a love set to the other, and
    a set one side retired from is the other's at the games to win it (4.421,
    4.422), so its games scored are not the games played. 0.1.0's match page
    printed the games scored alone, which were then the games printed, and its
    status cell "Forfeit"; now the cell says "Forfeit, 2–6 on court", and a
    player's Every set played list says "0–6 (6–2 on court)", from the export's
    ``printed_home_games`` and ``printed_away_games``. A rubber nobody played is in
    no player's figures (``Match.nobody_played``), so ``setsOfPlayer`` skips it by
    the export's ``nobody_played``; and a night no rubber was played on is a match
    played but has no sets, games or margin to count, so the overview's Sets
    completed, Games played, Tightest and Biggest tiles leave it out by
    ``no_rubber_played``, and Games played sums the games printed; Sets completed
    leaves out a rubber nobody played on a night that had play, by
    ``nobody_played``. Measured by the tiles' arithmetic over the season the probe
    builds (/tmp/mcf/scratch/RUBBER/probe/before3.txt): on the base a walkover over
    blank rows was the season's Tightest match, at 0 games, a night of rows all
    ticked Forfeit added 4 sets completed that nobody played, and a night with two
    such rows beside two played added 2; scored 24-0 and left in, either whole
    night would be the Biggest margin, 24 games nobody played. Rejected: the
    rule written again here, as a status test, which is the second copy the
    export's fields exist to remove.

    Structural, because there is no JavaScript engine here to run the branches.
    """

    def code(self, fn: str) -> str:
        """*fn* with its comments and strings blanked."""
        return jslint.code_only(self.body(fn))

    def said(self, fn: str) -> str:
        """*fn* with its comments blanked and its strings kept."""
        return jslint.without_comments(self.body(fn))

    def test_on_court_reads_the_games_printed_off_the_export(self):
        code = self.code("onCourt")
        self.assertIn("st.printed_home_games", code)
        self.assertIn("st.printed_away_games", code)
        self.assertIn("' on court'", self.said("onCourt"))

    def test_on_court_says_nothing_of_a_row_scored_as_it_printed(self):
        self.assertRegex(self.said("onCourt"),
                         r"home\s*===\s*st\.home_games\s*&&\s*"
                         r"away\s*===\s*st\.away_games\)\)\s*return\s*''")

    def test_on_court_says_a_rubber_nobody_played_was_not_played(self):
        self.assertEqual(self.guard_for("onCourt", "'not played'", emits="return"),
                         "st.nobody_played")

    def test_on_court_reads_the_games_from_the_side_asked(self):
        self.assertRegex(self.said("onCourt"),
                         r"side\s*===\s*'away'\s*\?\s*num\(away\)")

    def test_the_match_pages_status_cell_says_the_games_on_court(self):
        code = self.said("setRow")
        self.assertRegex(code, r"\[status,\s*onCourt\(st\)\]\.filter\(Boolean\)"
                               r"\.join\(', '\)")
        self.assertRegex(code, r"text:\s*words\s*\?\s*upperFirst\(words\)")

    def test_a_players_set_list_says_the_games_on_court_beside_its_own(self):
        code = self.code("renderPlayer")
        self.assertIn("onCourt(s.set, s.side)", code)
        self.assertRegex(code, r"num\(s\.gamesAgainst\)\s*\+\s*\(court\s*\?")

    def test_a_players_set_list_leaves_out_a_rubber_nobody_played(self):
        self.assertRegex(self.code("setsOfPlayer"),
                         r"if\s*\(st\.nobody_played\)\s*return;")
        self.assertNotIn("'forfeit'", self.said("setsOfPlayer"))

    def test_the_overview_counts_the_tennis_of_the_nights_a_rubber_was_played_on(self):
        code = self.said("renderOverview")
        self.assertRegex(code, r"var\s+rubbers\s*=\s*played\.filter\(function\s*"
                               r"\(x\)\s*\{\s*return\s*!x\.no_rubber_played;\s*\}\)")
        self.assertIn("rubbers.forEach(", code)
        self.assertNotIn("played.forEach(", code.replace("rubbers.forEach(", ""))
        # Still a match played: the night happened.
        self.assertIn("tile('Matches played', String(played.length)", code)

    def test_sets_completed_leave_out_a_rubber_nobody_played_on_a_night_with_play(self):
        # Two rubbers nobody came for beside two played (the probe's "two 0-0
        # nobody came"): the night stays in, its two love sets nobody played do
        # not, by the same field the player pages read.
        self.assertRegex(self.code("renderOverview"),
                         r"filter\(function\s*\(st\)\s*\{\s*return\s*st\.completed"
                         r"\s*&&\s*!st\.nobody_played;\s*\}\)")

    def test_games_played_are_the_games_printed(self):
        code = self.code("renderOverview")
        self.assertIn("gamesPlayed += (x.printed_home_games || 0) + "
                      "(x.printed_away_games || 0)", code)
        self.assertNotIn("(x.home_games || 0)", code)
        self.assertIn("num(gamesPlayed / rubbers.length, 1)", code)

    def test_the_share_caption_says_why_a_rubber_nobody_played_is_in_no_row(self):
        self.assertIn("gap.rubbers_nobody_played", self.code("teamPlayersTable"))
        self.assertEqual(self.guard_for("teamPlayersTable",
                                        "won by a forfeit with nobody on court",
                                        emits="caption").strip(), "nobody")
        # Nothing to correct, so the data health section is not pointed at.
        self.assertRegex(self.code("teamPlayersTable"),
                         r"var found = strangers \+ blanks \+ crowded;")


class TestTheProgressBoxNamesTheFinalsPlayedApartFromTheRounds(WebAssetCase):
    """The progress box counts the regular rounds and says which finals were played.

    ``meta.rounds_played`` counts the regular rounds with cards now (4.69), as the
    season's length does, so a final with cards is in neither number. Said nowhere
    else, the box would read ``Round 2 of 2`` over a season whose final had been
    played, and a club would look for the final it knows it played. Measured in
    headless Chrome on the demo with a round 3 declared a final
    (/tmp/t6/scratch/m10/before.out): ``Season progress | Round 3 of 2 | 8 of 9
    fixtures played · 100% of the declared rounds``. Now the finals are named on a
    line of their own, from the flags season.json's rounds carry, which Python set.

    And the box says why there is no percentage. ``rounds_total`` is null where
    nobody declared a length and, from 4.72, where F3 refused the one declared;
    ``provenance.rounds_total`` still says ``declared`` in the second, so the page
    reads the two together rather than telling a club that typed a length that
    nobody declared one.

    Rejected: a ``meta.finals_played`` key, which would put a line of difference in
    every committed document for a list the rounds already carry; and the finals the
    draw declares that nobody has played, which would read as played.
    """

    def test_the_finals_named_are_the_rounds_flagged_finals_that_have_cards(self):
        code = jslint.code_only(self.body("finalsPlayed"))
        self.assertIn("S.rounds", code)
        self.assertIn("r.is_finals && r.played", code)
        self.assertIn("roundTitle(r.number)", code)

    def test_they_are_said_on_a_line_of_their_own_and_only_when_there_are_any(self):
        self.assertEqual(
            self.guard_for("finalsFoot", "'Finals played: '", emits="return",
                           exits=True),
            "!finals.length")

    def test_both_shapes_of_the_box_carry_the_line(self):
        # With a length and without one: a box that names them in one shape only
        # says a final was played on the seasons somebody gave a length.
        code = jslint.code_only(self.body("seasonProgress"))
        self.assertIn("var finals = finalsFoot();", code)
        returns = [found.start() for found in re.finditer(r"return box;", code)]
        self.assertEqual(len(returns), 2, code)
        for at in returns:
            with self.subTest(at=at):
                self.assertTrue(
                    code[:at].rstrip().endswith("if (finals) box.appendChild(finals);"),
                    code[max(0, at - 200):at])

    def test_a_length_f3_refused_is_not_called_undeclared(self):
        self.assertEqual(
            self.guard_for("noLengthReason", "More regular rounds have",
                           emits="return"),
            "provenanceOf('rounds_total') === 'declared'")
        self.assertEqual(
            self.guard_for("noLengthReason", "has not been declared", emits="return",
                           exits=True),
            "provenanceOf('rounds_total') === 'declared'")

    def test_the_box_without_a_length_gives_that_reason(self):
        code = jslint.without_comments(self.body("seasonProgress"))
        # 4.468 moved this: the foot's matches are played, as the tiles' are.
        self.assertRegex(code, r"' played\. ' \+\s*noLengthReason\(\)")
        self.assertNotIn("has not been declared", code)


class TestTheProgressBoxsFootCountsTheFixturesOfTheRoundsItCounts(WebAssetCase):
    """The foot prints the document's count of the regular rounds' fixtures (4.194).

    The box counts the regular rounds (4.69), and its foot was handed the page's own
    count of every card and every fixture, finals and all. Measured in headless
    Chrome on the suite's cards, a draw dating rounds 1, 2 and 3 with 3 a final, and
    a semi-final's card (/tmp/t7/scratch/W1/before.txt): ``Round 2 of 2`` over ``7 of
    7 fixtures played · 100% of the declared rounds`` and ``Finals played: Round 3,
    SF.``, where the regular rounds hold 4 of the 7. The box is now handed no count:
    it reads ``meta.regular_fixtures`` beside ``rounds_played``, all three of them
    Python's. The Matches played tile keeps the whole draw, ``of 7 in the draw``.

    And one match is one match in both shapes of the box (4.195). The shape with a
    length wrote ``matchesPlayed + ' matches scored · '``, so one card with
    ``rounds_total = 5`` read ``1 matches scored · 20% of the declared rounds``.

    Rejected: working the regular fixtures out here from ``S.rounds`` and the match
    rows, which is Python's rule a second time in the language the suite cannot run;
    and keeping the whole season's count in the shapes with no declared draw, where
    the foot says ``7 matches scored`` beside ``3 rounds`` and ``Finals played:
    SF.``, the final in one count and named apart from the other.
    """

    def foot(self) -> str:
        return jslint.without_comments(self.body("seasonProgress"))

    def counted(self) -> str:
        """The name seasonProgress reads the document's count into."""
        found = re.search(r"var (\w+) = m\.regular_fixtures \|\| \{\};",
                          jslint.code_only(self.body("seasonProgress")))
        self.assertIsNotNone(found, "seasonProgress() never reads "
                                    "meta.regular_fixtures, or reads it without "
                                    "an object behind it for a document without it")
        return found.group(1)

    def test_the_box_is_handed_no_count_of_the_page_s_own(self):
        self.assertEqual(re.findall(r"seasonProgress\(([^()]*)\)",
                                    jslint.code_only(self.body("renderOverview"))),
                         ["drawDeclared"])
        box = jslint.code_only(self.body("seasonProgress"))
        for page_count in ("S.matches", "S.fixtures", ".length", ".filter("):
            with self.subTest(count=page_count):
                self.assertNotIn(page_count, box)

    def test_the_foot_prints_the_regular_fixtures_the_document_counted(self):
        name = self.counted()
        self.assertRegex(self.foot(),
                         rf"drawDeclared\s*\?\s*num\({name}\.played\) \+ ' of ' \+ "
                         rf"num\({name}\.total\) \+ ' fixtures played · '")

    def test_a_document_without_the_count_prints_a_dash_and_not_a_nought(self):
        # The key is new in format 4, after the format's first builds, and num()
        # prints a dash for a value the document does not have. `|| 0` reads that
        # silence as no fixtures played out of none.
        name = self.counted()
        code = jslint.code_only(self.body("seasonProgress"))
        reads = list(re.finditer(rf"\b{name}\.(played|total)\b", code))
        self.assertGreaterEqual(len(reads), 4)
        for found in reads:
            with self.subTest(at=found.start()):
                self.assertRegex(code[:found.start()], r"\b(?:num|plural)\($",
                                 code[found.start() - 60:found.end() + 10])
                self.assertNotRegex(code[found.end():], r"^\)?\s*\|\|")

    def test_one_match_is_one_match_in_both_shapes_of_the_box(self):
        name = self.counted()
        code = self.foot()
        # 4.468 moved this: the foot's matches are played, as the tiles' are, and the
        # count is read in front of that word in both shapes.
        scored = [found.start()
                  for found in re.finditer(r"' played(?:\. | · )'", code)]
        self.assertEqual(len(scored), 2, "the box does not say what was played in "
                                         "both of its shapes")
        for at in scored:
            with self.subTest(at=at):
                self.assertRegex(code[:at], rf"num\({name}\.played\) \+ ' ' \+ "
                                            rf"plural\({name}\.played, 'match', "
                                            rf"'matches'\) \+\s*$")
        self.assertNotIn("matches scored", code)

    def test_the_tile_below_it_still_counts_the_whole_draw(self):
        body = jslint.without_comments(self.body("renderOverview"))
        self.assertIn("var scheduled = (S.fixtures || []).length;", body)
        self.assertIn("drawDeclared ? 'of ' + scheduled + ' in the draw'", body)
        self.assertNotIn("regular_fixtures", body)


class TestTheWordForALevelNightInAGradeThatCannotDraw(WebAssetCase):
    """"Draw" is the wrong word where the competition has no draw.

    The match table on a team's page words each row from ``result``, and the
    exporter writes ``"draw"`` there for any level card -- which is the scoreline,
    and true. Under ``[rules] draws = false`` the ladder counts no draw, the banner
    says "no draw" and the report calls the card a result nobody recorded, so a row
    reading "Draw" is the page contradicting three things it prints itself.

    "Level" is the word the rest of the project uses for it: level scoreline, no
    result. Read off the reason the exporter already wrote beside the row rather
    than re-derived from the rules block, so one document cannot produce a row
    worded one way and a sentence below it worded the other.

    Structural, because there is no JavaScript engine here to run the branches.
    """

    def words(self) -> str:
        """The function as written -- strings intact, because they are the point."""
        return self.body("resultWord")

    def code(self) -> str:
        """The same function with its comments and strings blanked."""
        return jslint.code_only(self.words())

    def test_a_level_card_is_not_worded_as_a_draw(self):
        self.assertIn("'level'", self.words(),
                      "resultWord() never looks at why the ladder left the match "
                      "out, so it words a level night as a draw")
        self.assertIn("'Level'", self.words())

    def test_it_asks_the_document_rather_than_the_rules_block(self):
        # Comments and strings blanked: a comment naming either field proves nothing
        # about which one the branch reads.
        code = self.code()
        self.assertIn("not_counted", code, "the row's own reason is not read")
        self.assertNotIn("allows_draw", code,
                         "worded from the rules instead of from the row, this says "
                         "Level beside every drawn match in the competition")

    def test_a_draw_is_still_a_draw_where_the_grade_has_one(self):
        # The common case, and the one a wrong branch here would quietly reword:
        # most grades draw, and their level nights are results.
        self.assertIn("'Draw'", self.words())


class TestTheLadderShowsWhatItsAverageDividedBy(WebAssetCase):
    """Points ÷ P is not Avg under a ruleset that credits an unplayed round.

    ``P`` counts the matches a team's result was decided in -- W, L and D add up to
    it -- and the average divides by those plus every round the ruleset credited the
    team its own average. Both figures are right, neither is the other's divisor,
    and a club checking the one column its ladder is ordered on against a
    calculator gets a different number with nothing on the page to explain it. The
    divisor is in every team's row in the document, under ``counted``, and was read
    nowhere.

    Shown only where the two differ, which is only under ``unplayed = "average"``
    and only once a round has actually gone unplayed. A column repeating ``P`` for
    every team is one more column to read and nothing to learn from it.

    Structural, because there is no JavaScript engine here to run the branch.
    """

    GUARD = "averagedRoundsCounted"

    def test_the_document_still_carries_the_divisor_the_page_reads(self):
        # Read off the dataclass rather than a document built here: if the exporter
        # renames this field, every one of these columns fills with dashes and no
        # test below would notice.
        from dataclasses import fields
        from matchcentre.stats import TeamStats
        names = {f.name for f in fields(TeamStats)}
        self.assertIn("counted", names)
        self.assertIn("played", names)

    def test_the_ladder_reads_it(self):
        self.assertRegex(self.body("ladderTable"), r"\bt\.counted\b")

    def test_the_page_does_not_add_the_divisor_up_for_itself(self):
        # played + averaged_matches is the ledger's own arithmetic. Repeating it
        # here is how a column and the average above it come to disagree about
        # their own divisor -- invisibly, both halves being self-consistent.
        self.assertNotIn("averaged_matches", self.body("ladderTable"))

    def test_whether_to_show_it_is_decided_from_the_two_figures_themselves(self):
        self.assertIn(f"function {self.GUARD}(",
                      jslint.code_only(self.assets()["app.js"]))
        body = self.body(self.GUARD)
        self.assertRegex(body, r"\bt\.counted\b")
        self.assertRegex(body, r"\bt\.played\b")

    def test_it_is_not_decided_from_the_ruleset_flag(self):
        # `average_unplayed` says what a club's rules do with a night off, not
        # whether any team has had one. A season with the flag set and no washout
        # in it yet would grow a column repeating P for every team.
        self.assertNotIn("average_unplayed", self.body(self.GUARD))

    def held_in(self) -> str:
        """The name ``ladderTable`` holds the answer under, asked for once."""
        body = self.body("ladderTable")
        assign = re.search(r"var\s+(\w+)\s*=\s*" + self.GUARD + r"\(\)", body)
        self.assertIsNotNone(assign, f"ladderTable() never calls {self.GUARD}()")
        self.assertEqual(body.count(self.GUARD + "()"), 1,
                         "asked twice, so the head and the cells can be given "
                         "different answers to the same question")
        return assign.group(1)

    def test_the_heading_and_the_cells_are_hidden_by_the_same_answer(self):
        # Thirteen headings over twelve cells puts every figure from Avg rightwards
        # under the wrong one, which is worse than the column missing: GF would
        # read as the average and Form as the games differential.
        body = self.body("ladderTable")
        name = self.held_in()
        at = body.find("var rows")
        self.assertGreater(at, 0, "ladderTable() no longer builds rows")
        self.assertIn(name, body[:at], "the heading is not hidden with the cells")
        self.assertIn(name, body[at:], "the cells are not hidden with the heading")

    def head(self) -> str:
        """The heading row's source, closed at the row build below it."""
        body = self.body("ladderTable")
        at = body.find("var rows")
        self.assertGreater(at, 0, "ladderTable() no longer builds rows")
        return body[:at]

    def test_the_divisor_sits_between_the_total_and_the_average(self):
        # Read left to right: Points, Counted, Avg is the division written out in
        # the order a reader checks it in.
        head = self.head()
        for label in ("'Points'", "'Counted'", "'Avg'"):
            with self.subTest(label=label):
                self.assertIn(label, head)
        self.assertLess(head.find("'Points'"), head.find("'Counted'"))
        self.assertLess(head.find("'Counted'"), head.find("'Avg'"))

    SENTENCE = "plus the rounds"

    def test_the_table_says_in_words_what_the_extra_column_is(self):
        # A title attribute is a hover: it is nothing at all on a phone, and
        # nothing on the printout a club pins to the clubhouse wall. The words are
        # the column's own now, and `columnKey()` prints every column's words under
        # the table that carries them -- see
        # TestEveryAbbreviatedHeadingSaysWhatItStandsFor. Which is why what is
        # checked here is the spec and not a sentence: this function had both, the
        # same explanation twice, and a caption that said it twice under one table.
        head = self.head()
        spec = re.search(r"\{ label: 'Counted',(.*?)\}", head, re.S)
        self.assertIsNotNone(spec, "the extra column is never explained in words")
        words = "".join(re.findall(r"'([^']*)'", spec.group(1)))
        self.assertIn(self.SENTENCE, words,
                      "the extra column is never explained in words")
        self.assertIn("divides by", words,
                      "the words name the column without saying what it is "
                      "the divisor of")
        added = re.search(r"\.concat\((\w+) \? \[\{ label: 'Counted'", head)
        self.assertIsNotNone(added, "the column is no longer added by a list the "
                                    "guard chooses between, so the words are "
                                    "printed under every ladder")
        self.assertEqual(added.group(1), self.held_in())

    def test_the_chart_table_prints_the_divisor_its_caption_promises(self):
        # "Points average = points ÷ matches counted" is printed under that chart's
        # data table -- which is the whole chart as far as a screen reader is
        # concerned -- over columns that listed Points and Played. Head and row
        # asked for separately: a heading with no figure under it is the promise
        # made twice and kept neither time.
        body = self.body("pointsAverageChart")
        head = body[body.find("tableHead:"):body.find("tableCaption:")]
        self.assertIn("'Counted'", head, "the chart's data table has no such column")
        row = body[body.find("tableRow:"):]
        self.assertRegex(row, r"\bt\.counted\b",
                         "the column is headed and never filled")

    def test_the_bar_a_reader_hovers_names_it_too(self):
        # The tooltip is where the figures behind one bar are read, and it listed
        # the played count beside an average that does not divide by it.
        body = self.body("pointsAverageChart")
        tip = body[body.find("tip:"):body.find("return hbarCard")]
        self.assertIn("'Counted'", tip)
        self.assertRegex(tip, r"\bt\.counted\b")

    def test_the_charts_extra_column_comes_and_goes_with_the_ladders(self):
        # One question, one answer, in both places: a chart table that carries the
        # column where the ladder does not is a figure with no ladder column to
        # check it against, and the reverse leaves the caption unverifiable in the
        # one case it was written for.
        self.assertIn(self.GUARD + "()", self.body("pointsAverageChart"))

    def test_the_team_page_says_what_its_own_average_divided_by(self):
        # The ladder-position tile prints the average, and the Points tile splits
        # the total into earned and averaged -- which are points, not matches, so
        # the divisor is nowhere on the one page a club opens to check a team.
        body = self.body("renderTeam")
        self.assertRegex(body, r"\bt\.counted\b")
        # And only where it is not the count in the tile beside it: "over 5
        # counted" against a team that played five is a second name for a figure
        # already on the page.
        self.assertRegex(body, r"t\.counted\s*>\s*t\.played")


class TestEveryAbbreviatedHeadingSaysWhatItStandsFor(WebAssetCase):
    """``GF`` was explained in a hover, which a phone does not have.

    Ten tables head their columns with letters -- ``P W L D GF GA Diff`` on the
    ladder and again on the home-and-away split, ``M C S D W–L Share`` on a team,
    ``G`` twice on a match card, ``Met`` on an opponent list, twelve of them on the
    players table -- and the words behind them lived in the heading cell's ``title``
    attribute. A title is a hover: nothing at all on a phone, nothing on the
    printout pinned to the clubhouse wall, and nothing to a keyboard either -- on
    the sortable players table the tab stop is the button *inside* the cell and the
    title is on the cell. Sixteen of those columns did not carry even that.

    So the words are printed under the table, in the caption that is already there,
    assembled by ``columnKey()`` from the same head the cells are built from. Two
    alternatives rejected: a visually hidden expansion inside each heading, which is
    announced on every one of fourteen columns and pastes into anything copied out
    of the table while still printing nothing; and a ``<details>`` glossary, which
    is a hover replaced by a click, and closed on paper.

    Assembled rather than written into each caption by hand, because the one column
    that *was* explained in words -- ``Counted``, on the ladder -- had the sentence
    and the title spelled out twice in one function. Two wordings of one column is
    how they come to disagree.

    Structural, because there is no JavaScript engine here to run any of it.
    """

    def specs(self) -> dict[str, set[str]]:
        """Every heading spec in app.js: the letters, and the words behind them.

        Read from the source rather than :func:`jslint.code_only`, which blanks
        exactly the string literals this is after. A spec with no title of its own
        answers with the empty string rather than going missing, so a column that
        lost its words fails the check below instead of skipping it.
        """
        found: dict[str, set[str]] = {}
        for obj in re.finditer(r"\{[^{}]*\}", self.assets()["app.js"], re.S):
            label = re.search(r"label: '([^']*)'", obj.group(0))
            if not label:
                continue
            title = re.search(r"title: ((?:'[^']*'\s*\+\s*)*'[^']*')", obj.group(0))
            words = "".join(re.findall(r"'([^']*)'", title.group(1))) if title else ""
            found.setdefault(label.group(1), set()).add(words)
        return found

    def constant(self, name: str) -> str:
        """One top-level list constant's source. ``body()`` only finds functions."""
        source = self.assets()["app.js"]
        at = source.find(f"\nvar {name} = [")
        self.assertGreater(at, 0, f"app.js defines no {name}")
        end = source.find("\n];", at)
        self.assertGreater(end, at, f"{name} is no longer a list")
        return source[at:end]

    def only_as_labels(self, where: str, source: str, labels) -> None:
        """No heading in ``source`` is a bare string where it should be a spec.

        A bare ``'GF'`` in a list of headings is a column whose letters are all the
        page ever says about it: there is no title to hover and nothing for
        ``columnKey()`` to print. As a spec's own ``label`` it has words beside it.
        """
        for label in labels:
            for at in (m.start() for m in
                       re.finditer(re.escape(f"'{label}'"), source)):
                line = source[:at].count("\n") + 1
                with self.subTest(where=where, label=label, at=line):
                    self.assertTrue(
                        source[:at].endswith("label: "),
                        f"{where} prints '{label}' as a bare heading (line {line} "
                        f"of the slice read): a column headed with letters and no "
                        f"spec has no words for the caption to carry, and a hover "
                        f"is nothing on a phone")

    def col_specs(self) -> dict:
        """The shared specs, by the name each is referenced under: gamesFor -> 'GF'."""
        source = self.assets()["app.js"]
        at = source.find("\nvar COL = {")
        self.assertGreater(at, 0, "app.js no longer collects the shared columns")
        end = source.find("\n};", at)
        self.assertGreater(end, at, "COL is no longer an object")
        found = dict(re.findall(r"(\w+): \{ label: '([^']*)'", source[at:end]))
        self.assertTrue(found, "COL holds no specs")
        return found

    def heads_explained(self, where: str, source: str, labels) -> None:
        """Every column in ``source`` headed by one of ``labels`` carries words.

        Either as its own spec or by referencing a shared one, since a table whose
        columns are all shared mentions none of the letters itself -- and a check
        that only rejected bare letters would pass such a head by saying nothing
        about it.
        """
        shared = {label for name, label in self.col_specs().items()
                  if f"COL.{name}" in source}
        for label in labels:
            if label in shared:
                continue
            with self.subTest(where=where, label=label):
                self.assertIn(f"label: '{label}'", source,
                              f"{where} has no words for its '{label}' column: it "
                              f"is neither a spec of its own nor one of the shared "
                              f"columns in COL")
        self.only_as_labels(where, source, labels)

    def test_the_caption_every_table_prints_carries_its_own_key(self):
        # Through the one function all thirteen tables pass, and built from the head
        # it is about to print: a column cannot arrive without its words, and the
        # words cannot outlive the column.
        self.assertRegex(self.body("table"), r"tableCaption\(o\.caption, head[,)]",
                         "table() builds its caption from the sentence alone, so the "
                         "headings it prints under it are not described by it")
        self.assertIn("columnKey(head)", self.body("tableCaption"),
                      "the caption no longer asks the head what it stands for")
        # A table with no sentence of its own still prints its key: the two games
        # differential charts head columns GF, GA and Diff and have no caption but
        # their chart's heading.
        self.assertRegex(self.body("tableCaption"),
                         r"if \(!sentence && !key[^)]*\) return null",
                         "a caption is built only where there is a sentence, so a "
                         "table named by its chart heading loses its key")

    def test_the_hover_and_the_printed_words_are_one_string(self):
        # One source, not two. A title attribute and a caption sentence written
        # separately are the ladder's Counted column again, which had both.
        self.assertIn("setAttribute('title', h.title)", self.body("headCell"),
                      "the heading has lost its hover, which is still the quickest "
                      "answer for a reader who has a pointer")
        # The printed line built from the title itself, not from a second list keyed
        # by the label: any separator, but those two strings. A lookup beside the
        # hover is the ladder's Counted column again, where the words were written
        # once for the pointer and once for the page.
        self.assertRegex(self.body("columnKey"), r"h\.label\s*\+[^;]*h\.title",
                         "the key is written from something other than the words "
                         "the hover shows, so the two can disagree")

    def test_the_grid_that_builds_its_own_headings_uses_the_same_hand(self):
        # One column per team, each headed by an abbreviation of a name, and the
        # full name was in a title. Its own `th` and its own caption are how it came
        # to be the one table the fix would have missed.
        body = self.body("h2hGridCard")
        for call in ("headCell(", "tableCaption("):
            with self.subTest(call=call):
                self.assertIn(call, body,
                              f"the head-to-head grid does not build its headings "
                              f"with {call}), so its team abbreviations are "
                              f"explained by a hover and nothing else")

    def test_a_heading_that_is_already_its_own_explanation_is_left_alone(self):
        # A club whose team names are short has `short === name`, and a key reading
        # "Gnats — Gnats" once per team is noise where the ladder's GF is
        # information. Headings with no words at all are skipped for the same
        # reason: Team, Points and Form say themselves.
        # Either order of the comparison: the two spellings say the same thing, and a
        # check that accepted only one would fail a rewrite rather than a regression.
        body = self.body("columnKey")
        self.assertRegex(body, r"h\.title === h\.label|h\.label === h\.title",
                         "a short name that is the whole name is printed as its own "
                         "expansion")
        self.assertIn("!h.title", body,
                      "a heading with no words behind it puts an empty expansion "
                      "in the key")

    def test_two_columns_under_one_heading_are_explained_once(self):
        # A match card heads the two games columns `G` and `G`, one each side of the
        # net, so the same expansion is in the head twice.
        self.assertIn("if (seen[part]) return;", self.body("columnKey"),
                      "the key repeats an expansion the head repeats")
        self.assertEqual(self.body("matchCard").count("label: 'G'"), 2,
                         "the match card no longer heads two columns the same, so "
                         "the check above is checking nothing")

    def test_the_box_that_scrolls_a_wide_table_is_still_named_by_its_sentence(self):
        # The caption is also the accessible name of the region that scrolls a table
        # too wide for the screen, and a name with a twelve-column key on the end of
        # it is not a name. Two elements in the one caption, so the sentence can be
        # pointed at on its own.
        caption = self.body("tableCaption")
        for cls in ("caption-text", "table-key"):
            with self.subTest(cls=cls):
                self.assertIn(cls, caption,
                              f"the caption no longer keeps its {cls} apart, so "
                              f"the sentence and the key cannot be told apart")
        self.assertIn("caption .caption-text", self.body("scrollRegionName"),
                      "the scrolling box is named by the whole caption, so a screen "
                      "reader is given the key as part of the table's name")

    # Measured against the source rather than assumed (`only_as_labels` reports the
    # line): each of these appears in app.js only ever as a heading's own label, so
    # a bare one anywhere in the file is a column with no words. The letters left
    # out are the ones that are also something else -- `W` and `L` are a form run's
    # own letters, `M` and `L` are SVG path commands, `P` prefixes a ladder-position
    # tooltip, `C` is a captain's own cell, `#` heads every route, and `Counted`,
    # `Sets`, `Won`, `Points` and `Form` are words a chart labels an axis or a tile
    # with -- and they are checked inside the heads they appear in instead.
    LETTERS = ("D", "G", "GF", "GA", "Diff", "Avg", "Met", "W–L", "Win %",
               "Share", "S", "S W-L", "D W-L", "S rating", "D rating")

    def test_no_table_on_the_page_heads_a_column_with_letters_it_leaves_bare(self):
        self.only_as_labels("app.js", self.assets()["app.js"], self.LETTERS)

    def test_the_ladder_explains_the_letters_in_its_own_row_of_headings(self):
        body = self.body("ladderTable")
        at = body.find("var rows")
        self.assertGreater(at, 0, "ladderTable() no longer builds rows")
        self.heads_explained("the ladder's head", body[:at],
                             ("#", "P", "W", "L", "D", "GF", "GA", "Diff", "Avg",
                              "Form"))

    def test_the_home_and_away_split_explains_the_same_seven(self):
        # The ladder's seven columns again, on the team page, and every one of them
        # was bare there -- the table a club reads to see whether a team travels.
        body = self.body("renderTeam")
        at = body.find("table(['Split'")
        self.assertGreater(at, 0, "the team page no longer splits home from away")
        end = body.find("], [", at)
        self.assertGreater(end, at, "the split table's head no longer closes")
        self.heads_explained("the split table's head", body[at:end],
                             ("P", "W", "L", "D", "GF", "GA", "Diff"))

    def test_the_team_page_explains_the_letters_over_its_squad(self):
        # Eleven abbreviations over three or four rows of names, and `M` among them --
        # which is not checked over the whole file, because `M` and `L` are also the
        # move and line commands of every SVG path the charts draw.
        body = self.body("teamPlayersTable")
        at = body.find("table(['Player'")
        self.assertGreater(at, 0, "the team page no longer lists its players")
        end = body.find("rows, { caption", at)
        self.assertGreater(end, at, "the squad table's head no longer closes")
        self.heads_explained("the squad table's head", body[at:end],
                             ("C", "M", "Sets", "W–L", "Win %", "GF", "GA", "Diff",
                              "Share", "S", "D"))

    def test_the_partner_and_opponent_tables_explain_the_same_five(self):
        # One table on a player's page, the same head for who they play with and who
        # they play against, and `Sets` again -- a word the charts label an axis with,
        # so the file-wide check leaves it alone.
        body = self.body("pairTable")
        at = body.find("table([kind")
        self.assertGreater(at, 0, "the player page no longer tabulates pairings")
        end = body.find("rows, { caption", at)
        self.assertGreater(end, at, "the pairing table's head no longer closes")
        self.heads_explained("the pairing table's head", body[at:end],
                             ("Sets", "W–L", "GF", "GA", "Diff"))

    PLAYER_LETTERS = ("M", "Sets", "Won", "Win %", "GF", "GA", "Diff", "S W-L",
                      "D W-L", "Share", "S rating", "D rating")

    def test_the_sortable_players_table_explains_all_twelve(self):
        # Where the hover was least use: the tab stop is the button inside the cell,
        # so a keyboard reader never reaches the cell the title is on. Six of these
        # had no title either -- Sets, Won, Win %, Diff and both ratings.
        cols = self.constant("PLAYER_COLS")
        self.only_as_labels("PLAYER_COLS", cols, self.PLAYER_LETTERS)
        # This list is specs already, every one of them, so the check above cannot
        # see the half of the defect that was here: a spec with a label and no
        # words. Read each one's own braces rather than the block, or a single title
        # anywhere in it would answer for all fourteen.
        for spec in re.finditer(r"\{[^{}]*\}", cols):
            label = re.search(r"label: '([^']*)'", spec.group(0))
            if not label or label.group(1) not in self.PLAYER_LETTERS:
                continue
            with self.subTest(label=label.group(1)):
                self.assertIn("title: '", spec.group(0),
                              f"the {label.group(1)} column carries no words at "
                              f"all: nothing to hover and nothing for the caption "
                              f"to print")

    # Every abbreviation the page prints, and a phrase from the words behind it.
    # The inventory is the point: a column added with letters and no words fails
    # the checks above, and one whose words are quietly reworded fails this.
    WORDS = {
        "#": "Ladder position", "P": "Matches played", "W": "Matches won",
        "L": "Matches lost", "D": "Matches drawn", "G": "Games won in the set",
        "GF": "Games for", "GA": "Games against", "Diff": "Games differential",
        "Avg": "Points average", "Counted": "plus the rounds", "Form": "oldest first",
        "M": "Matches played", "C": "Captain", "Sets": "Sets played",
        "Won": "Sets won", "Win %": "Sets won as a percentage", "Met": "Meetings played",
        "Share": "Share of the team", "S": "Singles rating", "D W-L": "Doubles record",
        "S W-L": "Singles record", "S rating": "Singles rating",
        "D rating": "Doubles rating", "W–L": "won–lost",
    }

    def test_the_words_behind_every_abbreviation_are_in_the_page(self):
        specs = self.specs()
        for label, expected in sorted(self.WORDS.items()):
            with self.subTest(label=label):
                self.assertIn(label, specs,
                              f"'{label}' heads a column and is never a spec with "
                              f"words in it")
                self.assertTrue(
                    any(expected in words for words in specs[label]),
                    f"'{label}' is explained as {sorted(specs[label])}, which does "
                    f"not say {expected!r}")

    def test_the_letters_that_mean_two_things_are_worded_where_they_are_used(self):
        # `D` is a drawn match on a ladder and a doubles rating on a team page;
        # `W–L` is sets on a player's row and meetings on a team's. One dictionary
        # keyed by the letters would have to pick one of each, which is why the
        # words travel with the column and only the columns that really are the same
        # column share a spec.
        specs = self.specs()
        self.assertEqual(specs["D"], {"Matches drawn", "Doubles rating"})
        self.assertEqual(specs["W–L"], {"Sets won–lost", "Meetings won–lost"})

    def test_a_heading_has_one_wording_wherever_it_is_printed(self):
        # Diff is a column on seven of these tables, GF and GA on four, Sets on
        # three. Worded at each site they drift, and a reader comparing a team's page
        # with the ladder is then comparing two sentences instead of one column. The
        # columns the tables share are one spec in COL; the players table keeps its
        # own list, because its columns are sort keys and types as well, so what is
        # asked of it is that it says the same thing rather than that it says it in
        # the same literal.
        #
        # This is also what catches a column that lost its words in one place only:
        # a spec with no title answers with the empty string, which is a second
        # wording.
        specs = self.specs()
        for label in ("#", "P", "W", "L", "G", "GF", "GA", "Diff", "Avg", "Form",
                      "M", "C", "Sets", "Won", "Win %", "Met", "Share", "S",
                      "S W-L", "D W-L", "S rating", "D rating"):
            with self.subTest(label=label):
                self.assertEqual(len(specs.get(label, ())), 1,
                                 f"'{label}' is explained as "
                                 f"{sorted(specs.get(label, ()))} -- one column, "
                                 f"more than one wording")
        self.assertGreaterEqual(self.assets()["app.js"].count("COL.gamesDiff"), 5,
                                "the shared spec is declared and barely used, so "
                                "the tables are still wording it themselves")

    def test_the_ladder_no_longer_explains_its_extra_column_twice(self):
        # The sentence appended to the caption by hand is what `columnKey()` now
        # prints from the column's own title. Both, and the caption says it twice.
        body = self.body("ladderTable")
        for words in ("plus the rounds", "divides by"):
            with self.subTest(words=words):
                self.assertEqual(body.count(words), 1,
                                 f"{words!r} is written twice in ladderTable(): "
                                 f"once as the column's words and once as a "
                                 f"sentence the caption grows, which is the same "
                                 f"explanation printed twice under one table")

    def test_the_key_prints_on_a_line_of_its_own(self):
        # Two spans in one caption: run together they read as a sentence that turns
        # into a list of letters halfway through.
        found = [rule for rule in csslint.rules(self.assets()["styles.css"])
                 if ".table-key" in rule.selector and rule.value("display")]
        self.assertTrue(found, "styles.css never lays the key out, so it runs on "
                               "from the caption's sentence")
        for rule in found:
            with self.subTest(rule=rule.where):
                self.assertEqual(rule.value("display"), "block")


class TestAMeetingThatReachedNoResultIsStillAMeeting(WebAssetCase):
    """The grid said "not played yet" about a card listed on the page above it.

    A card can be played and reach no result: rain at 8pm with nobody ticking Match
    Cancelled, or a forfeit with neither side's box ticked. That night is a meeting
    -- its games are on the card and in both teams' games columns, which ask only
    whether the match was played -- and the pairing record it was dropped from is
    what the grid, its row totals and every team's opponent table are drawn from.

    The counting is fixed in Python, where the record is built; what is checked here
    is that the page says the difference out loud. The cells count such a meeting and
    the W-L beside them cannot, so a reader adding a row up is short by it with
    nothing on the page to say why.

    Structural, because there is no JavaScript engine here to run the branch.
    """

    GUARD = "anyUndecidedMeetings"

    def test_the_document_carries_the_count_the_page_reads(self):
        # Read off the dataclass rather than a document built here: if the exporter
        # renames this field the sentences below are printed about nothing.
        from dataclasses import fields
        from matchcentre.stats import HeadToHead
        names = {f.name for f in fields(HeadToHead)}
        self.assertIn("undecided", names)
        self.assertIn("played", names)

    def test_the_records_the_renderers_read_carry_it(self):
        # h2hFor is the one place a record becomes something a renderer can read, so
        # a field it drops is a field no renderer can show.
        self.assertRegex(self.body("h2hFor"), r"\brec\.undecided\b")

    def test_a_blank_cell_still_means_a_fixture_that_has_not_happened(self):
        # The dot and its hover are right for a fixture nobody has played and were
        # reached whenever the pairing had no *result*. The guard is the played
        # count, and the two must not be run together again.
        guard = self.guard_for("h2hGridCard", "not played yet", emits="setAttribute",
                               disjunction=True)
        self.assertIn("played", guard)
        self.assertNotIn("decided", guard)
        # A disjunction by rights -- no record, or a record of nothing played -- so
        # each way to the dot has to be about the pairing's own record: an
        # alternative that is not puts the dot over a result.
        record = re.search(r"var (\w+) = h2hFor\(", self.body("h2hGridCard"))
        self.assertIsNotNone(record, "h2hGridCard() no longer asks h2hFor()")
        for alternative in guard.split("||"):
            with self.subTest(alternative=alternative.strip()):
                self.assertRegex(alternative, r"\b%s\b" % re.escape(record.group(1)),
                                 f"the dot is also printed on {alternative.strip()!r}, "
                                 f"which says nothing about whether the pairing met")

    def test_whether_to_say_it_is_decided_from_the_records_themselves(self):
        self.assertIn(f"function {self.GUARD}(",
                      jslint.code_only(self.assets()["app.js"]))
        # `> 0` rather than truthiness, so a document exported before this field
        # existed leaves the sentences out instead of printing them about nothing.
        self.assertRegex(self.body(self.GUARD), r"\.undecided\s*>\s*0")

    def held_in(self, fn: str) -> str:
        """The name one renderer holds the answer under, asked for once."""
        body = self.body(fn)
        assign = re.search(r"var\s+(\w+)\s*=\s*" + self.GUARD + r"\(", body)
        self.assertIsNotNone(assign, f"{fn}() never calls {self.GUARD}()")
        self.assertEqual(body.count(self.GUARD + "("), 1,
                         f"{fn}() asks twice, so the heading and the cells can be "
                         f"given different answers to the same question")
        return assign.group(1)

    CAPTION = "counts the decided meetings only"

    def test_the_grid_says_in_words_why_its_row_total_is_short(self):
        # In the caption, not only in a cell's hover: a hover is nothing on a phone
        # and nothing on the printout pinned to the clubhouse wall, and this is the
        # sentence that makes the row add up.
        guard = self.guard_for("h2hGridCard", self.CAPTION, emits="caption +=")
        self.assertIn(self.held_in("h2hGridCard"), guard)

    def test_the_grid_is_asked_about_every_pairing_in_it(self):
        # One answer for the whole table, from the same records the cells are drawn
        # from: a caption that came and went by row would be a sentence about a row.
        body = self.body("h2hGridCard")
        call = re.search(self.GUARD + r"\(([^)]*)\)", body)
        self.assertIsNotNone(call, "the grid never asks")
        held = re.escape(call.group(1).strip())
        self.assertRegex(body, r"var\s+" + held + r"\s*=\s*\[\]")
        self.assertRegex(body, held + r"\.push\(")

    def test_the_grid_no_longer_promises_completed_meetings_only(self):
        # "Every completed meeting, games for and against" was true of the figures
        # and false of the fixture list beside them, and it is what made the blank
        # cells read as deliberate. Read off the sub-caption's own line rather than
        # the whole function, which quotes the old wording in a comment.
        body = self.body("h2hGridCard")
        at = body.find("class: 'sub'")
        self.assertGreater(at, 0, "the grid has lost its sub-caption")
        line = body[at:body.find("\n", at)]
        self.assertNotIn("completed", line)
        self.assertIn("meeting", line)

    TITLE = "no result on the card"

    def test_the_hovered_cell_says_that_night_decided_nothing(self):
        # Asked of the one pairing under the cursor rather than of the table's
        # answer: the games shown in this cell are the ones in question.
        guard = self.guard_for("h2hGridCard", self.TITLE, emits="title +=")
        self.assertIn("undecided", guard)

    def team_head(self) -> str:
        """The opponent table's heading row, from the return statement down."""
        body = self.body("teamH2HTable")
        at = body.find("return el(")
        self.assertGreater(at, 0, "teamH2HTable() no longer returns a table")
        return body[at:]

    def test_the_opponent_table_has_a_column_for_such_meetings(self):
        # Met counts them and W-L cannot, which on a two-team row reads 1 met and
        # 0-0: a line a club reads as a match nobody won rather than a match
        # nothing was decided in.
        self.assertIn("'No result'", self.team_head())

    def test_the_column_sits_between_the_result_and_the_games(self):
        # Read left to right: met, what was decided, what was not, then the games
        # -- which include the night in the column to their left. W–L is the shared
        # spec rather than a literal here, so the head is read for the name it calls
        # the column by.
        head = self.team_head()
        for label in ("COL.meetingsWonLost", "'No result'", "'Games'"):
            with self.subTest(label=label):
                self.assertIn(label, head)
        self.assertLess(head.find("COL.meetingsWonLost"), head.find("'No result'"))
        self.assertLess(head.find("'No result'"), head.find("'Games'"))

    def test_the_column_is_filled_from_the_record(self):
        # A heading with no figure under it is the sentence made and not kept.
        body = self.body("teamH2HTable")
        cells = body[body.find("cells: ["):body.find("return el(")]
        self.assertRegex(cells, r"\brec\.undecided\b")

    def test_the_heading_and_the_cells_are_hidden_by_the_same_answer(self):
        # Seven headings over six cells puts Games, Diff and To come each one
        # column to the left of their own heading. Read off the cells themselves
        # rather than everything above the table, which holds the answer's own
        # assignment and would pass with the cells gone.
        body = self.body("teamH2HTable")
        name = self.held_in("teamH2HTable")
        at = body.find("return el(")
        cells = body[body.find("cells: ["):at]
        self.assertIn(name, cells, "the cells are not hidden with the heading")
        self.assertIn(name, body[at:], "the heading is not hidden with the cells")

    def test_the_column_is_asked_of_this_teams_own_opponents(self):
        # Season-wide would put a column of dashes on the page of a team whose every
        # night reached a result.
        body = self.body("teamH2HTable")
        call = re.search(self.GUARD + r"\(([^)]*)\)", body)
        self.assertIsNotNone(call, "the team page never asks")
        held = re.escape(call.group(1).strip())
        self.assertRegex(body, r"var\s+" + held + r"\s*=\s*others\.map\(")

    def test_the_opponent_table_says_which_columns_count_it(self):
        # Three columns and the meeting is in two of them. Named in the caption
        # because the alternative is a reader deciding which one is wrong.
        guard = self.guard_for("teamH2HTable", "neither side of", emits="caption +=")
        self.assertIn(self.held_in("teamH2HTable"), guard)


class TestAChartWithNothingToDrawSaysSoInsteadOfDrawingItsFloor(WebAssetCase):
    """A chart with nothing to draw showed the floor it divides by as its scale.

    Item 4.455. The head-to-head grid's ``maxGames`` started at 1 so that the fill
    never divides by nothing, and the same 1 was the top of the grid's colour key,
    printed as "1 games" and drawn whatever the grid held. And three diverging charts
    built over a fixed list -- Games for and against (a row per ladder team),
    Strength by set slot (a row per slot of the format, with a zero record for a slot
    nobody played) and a player's Singles and doubles -- hand ``hbarCard`` rows that
    are all 0, which it draws, since its empty text is for no rows at all: each drew
    its rows at 0 on an axis of 1 · 0 · 1 under "Games", and their own empty texts
    never showed. Measured in headless Chrome on fictional cardwriter cards
    (/tmp/mcf/scratch/WORDING/probe.py, before.txt): a round of two Match Cancelled
    cards between four teams drew four rows of "0 games won, 0 games lost" and a
    four-by-four grid of dots over "0 games [ramp] 1 games", and each team page four
    0/0 slots; one away walkover over four 0-0 rows drew 0–0 cells over the same key
    and the zero rows on the overview, both team pages and every player's page; a
    competition of one grand final won 24-10 drew Games for and against at 0 and 0,
    since a team's figures are the regular season wholesale (stats.py, F11), and the
    two team pages four 0/0 slots each; and a rostered reserve who had not played
    after two rounds drew Singles and doubles at 0 and 0 above "No opponent faced
    yet.".

    So the grid keeps its floor as the divisor only and keys the colours from its own
    largest figure, counted from 0, drawing the key only where a cell has a colour;
    and ``hbarCard`` takes ``zeroIsEmpty``, under which rows that are every one 0 are
    no rows, before the table and the plot are built, for the three charts over a
    fixed list and no other. Their texts say what their figures count: a team's are the regular
    season's, and a player's count every match, finals and all. The player's says
    games and not sets: a last double left at 0-0 when play stopped credits the two
    named on it a set played (stats.py), and on a fictional card where it was the one
    set two players and a reserve were named on, each page's Sets tile read "0–0, 1
    played", where "No sets played yet." would have stood beside it; the same is true
    of a walkover's rows until 4.289 lands. Read as text, as the suite runs no
    JavaScript. Rejected: one rule in ``hbarCard`` for every chart,
    since a 0 in Games differential by opponent says a player is level with that
    opponent, and Points earned by round keeps a washout's row on purpose; dropping
    zero rows one at a time, which takes a team still at 0 after a bye out of a chart
    of the whole ladder; moving ``niceMax``'s floor, which still draws an axis for
    nothing; mending only the plural, which leaves a key for colours no cell uses;
    rewording the caption's "Blank" for the dot, which is the blank mark; a hover of
    its own for a pairing whose one fixture was called off, which is no meeting; and
    leaving out the colour legend above the empty text, which states no figure and
    already stands above Games differential by opponent's "No opponent faced yet.".
    """

    FIXED_LISTS = ("gamesForAgainstChart", "slotStrengthChart", "disciplineChart")
    EMPTY = {"gamesForAgainstChart": "No regular-season games counted yet.",
             "slotStrengthChart": "No regular-season set played yet.",
             "disciplineChart": "No games won or lost yet."}

    @staticmethod
    def terms(condition: str) -> list[str]:
        """A condition's terms, split at the ``&&`` outside every bracket, so that a
        callback's own ``&&`` stays inside the term that calls it."""
        code = jslint.code_only(condition)
        terms, depth, start = [], 0, 0
        for index, char in enumerate(code):
            depth += {"(": 1, "[": 1, "{": 1, ")": -1, "]": -1, "}": -1}.get(char, 0)
            if depth == 0 and code.startswith("&&", index):
                terms.append(condition[start:index].strip())
                start = index + 2
        return terms + [condition[start:].strip()]

    def key_top(self) -> str:
        """The figure the grid's colour key tops out at, whatever it is called."""
        said = jslint.without_comments(self.body("h2hGridCard"))
        found = re.search(r"text: (\w+) \+ ' ' \+ plural\(\1, 'game'\)", said)
        self.assertIsNotNone(found, "the top of the grid's key is not worded through "
                                    "plural(), so it reads '1 games'")
        return found.group(1)

    def test_the_keys_top_is_the_grids_largest_figure_counted_from_nought(self):
        top = self.key_top()
        code = jslint.code_only(self.body("h2hGridCard"))
        self.assertRegex(code, r"var %s = 0;" % top,
                         "the key's top starts above 0, so a grid with no games in it "
                         "is keyed to a game")
        self.assertRegex(code, r"\b%s = Math\.max\(%s, \w+\.gamesFor\)" % (top, top),
                         "the key's top is not the largest figure a cell prints")
        # And given no other value: a floor put back under it after the loop keys a
        # grid of dots to "1 game" again, with both of the lines above still there.
        assigned = re.findall(r"(?<![\w$.])%s\s*(?:[-+*/%%&|^]|<<|>>>?)?=(?!=)"
                              r"|(?:\+\+|--)\s*%s\b|(?<![\w$.])%s\s*(?:\+\+|--)"
                              % (top, top, top), code)
        self.assertEqual(len(assigned), 2,
                         "the key's top is given a value other than 0 and the largest "
                         "figure a cell prints")

    def test_the_fill_alone_keeps_the_floor_it_divides_by(self):
        # A walkover's 0–0 cell is a record with games of 0: divided by a top of 0, its
        # fill is NaN and the cell has no colour at all.
        top = self.key_top()
        code = jslint.code_only(self.body("h2hGridCard"))
        self.assertRegex(code, r"\.gamesFor / Math\.max\(1, %s\)" % top,
                         "the fill divides by the key's top with no floor under it")
        self.assertNotRegex(code, r"/ %s\b" % top,
                            "something divides by the key's top unguarded")

    def test_the_key_is_drawn_only_over_a_cell_with_a_colour(self):
        top = self.key_top()
        said = jslint.without_comments(self.body("h2hGridCard"))
        scale = re.search(r"var (\w+) = el\('div', \{ class: 'scale-legend' \}", said)
        self.assertIsNotNone(scale, "the grid has no colour key")
        guard = self.guard_for("h2hGridCard", f"appendChild({scale.group(1)})")
        self.assertEqual(guard, f"{top} > 0",
                         "the key is drawn under something other than the grid having "
                         "a game to colour")

    def test_the_bar_chart_asks_before_it_builds_the_table_or_the_plot(self):
        guard = self.guard_for("hbarCard", "rows = []", emits="rows =")
        terms = self.terms(guard)
        self.assertIn("opts.zeroIsEmpty", terms,
                      "the rows are emptied whether or not the chart asked for it")
        every = [term for term in terms if term.startswith("rows.every(")]
        self.assertEqual(len(every), 1, "the rows are emptied without asking whether "
                                        "every one of them is 0")
        # guard_for has refused a `||` anywhere in the condition, the callback's
        # included, so a row is 0 here only where both of its figures are.
        for field in ("value", "neg"):
            with self.subTest(field=field):
                self.assertRegex(every[0], r"!\s*\w+\.%s\b" % field,
                                 f"a row is taken for 0 without reading its {field}")
        code = jslint.code_only(self.body("hbarCard"))
        at = self.offset_of(code, "rows = []", "the emptying")
        for later, what in (("rows.map(", "the table's rows"),
                            ("chartCard(", "the card and its plot")):
            with self.subTest(before=what):
                self.assertLess(at, self.offset_of(code, later, what),
                                f"{what} are built before the rows are emptied")

    def test_the_charts_over_a_fixed_list_ask_for_it_and_no_other_does(self):
        for fn in self.FIXED_LISTS:
            with self.subTest(fn=fn):
                self.assertRegex(jslint.code_only(self.body(fn)),
                                 r"\bzeroIsEmpty: true\b",
                                 f"{fn}() draws a row of 0 for every entry of its list")
        # Every other chart's 0 can be a result: a player level with an opponent in
        # Games differential by opponent, a washout's row kept on purpose in Points
        # earned by round, a player level on games in Games differential by player,
        # a team on no points yet in Points average by team.
        source = self.assets()["app.js"]
        asking = {fn for fn in re.findall(r"\nfunction (\w+)\(", source)
                  if fn != "hbarCard"
                  and re.search(r"\bzeroIsEmpty\b", jslint.code_only(self.body(fn)))}
        self.assertEqual(asking, set(self.FIXED_LISTS),
                         "a chart whose rows of 0 say something drops them")

    def test_their_empty_texts_say_what_their_figures_count(self):
        for fn, text in self.EMPTY.items():
            with self.subTest(fn=fn):
                said = jslint.without_comments(self.body(fn))
                found = re.findall(r"\bempty: '([^']*)'", said)
                self.assertEqual(found, [text])


class TestTheGridAndTheLadderCountDifferentMeetings(WebAssetCase):
    """Two figures on one page, worked out over different matches, saying so.

    Finals are out of the ladder (F11) and in the head-to-head record, and both are
    right about their own question: a semi-final is a night two teams met, and is not
    a round of the season being ranked. But nothing on the page said so, and the
    demo season is the proof that it has to. The aces and the rockets met twice;
    over the pair the aces lead on games 30-24, and the one meeting the ladder counts
    was a 24-6 loss. The grid shows 1-1 and +6 under a table that has those two
    teams first and last.

    Louder where the ruleset breaks ties on ``head_to_head``, which every preset here
    does: there the difference did not merely look odd, it decided a position, and a
    club checking that position against the grid is checking the wrong matches.

    Structural, because there is no JavaScript engine here to run the branch. What
    the Python counts is checked in test_pipeline.
    """

    GUARD = "finalsMeetings"
    SENTENCE = "ranks the regular season only"
    LOUD = "regular-season meetings"

    def test_the_document_carries_the_count_the_page_reads(self):
        # Read off the dataclass rather than a document built here: if the exporter
        # renames this field every sentence below is printed about nothing.
        from dataclasses import fields
        from matchcentre.stats import HeadToHead
        self.assertIn("finals", {f.name for f in fields(HeadToHead)})

    def test_the_records_the_renderers_read_carry_it(self):
        # h2hFor is the one place a record becomes something a renderer can read, so
        # a field it drops is a field no caption can explain. It was dropped, and
        # both captions counted `undefined`.
        self.assertRegex(self.body("h2hFor"), r"\bfinals:\s*rec\.finals\b")

    def test_an_older_document_is_read_as_having_no_finals_rather_than_NaN(self):
        # `|| 0` at the one reader, like `undecided` beside it: a document exported
        # before the field existed leaves the sentence out instead of adding
        # `undefined` into a total and printing "NaN meetings below were finals".
        self.assertRegex(self.body("h2hFor"), r"rec\.finals\s*\|\|\s*0")
        self.assertRegex(self.body(self.GUARD), r"rec\.finals\s*\|\|\s*0")

    def test_the_count_is_summed_and_not_answered_yes_or_no(self):
        # The number is what makes the sentence checkable against the Matches list:
        # "one meeting below was a final" sends a club to one row, "some were finals"
        # sends them through the season.
        self.assertIn("reduce(", self.body(self.GUARD))

    def test_the_sentence_is_written_once_for_both_tables(self):
        # The grid and a team's own page are the same fact about the same pairings.
        # Worded twice is how they come to disagree -- the reason the run report and
        # the rules banner share theirs.
        self.assertIn("function finalsMeetingsNote(",
                      jslint.code_only(self.assets()["app.js"]))
        # Counted in the source and not in `code_only()`, which blanks the string
        # literals this is looking for.
        self.assertEqual(self.assets()["app.js"].count(self.SENTENCE), 1,
                         "the sentence is spelled out in more than one place")

    def test_it_is_not_printed_where_no_final_has_been_played(self):
        # Not keyed on the season having a finals round: a note about a difference
        # that has not arisen is a line every club pays for so that one of them is
        # told something true.
        body = self.body("finalsMeetingsNote")
        self.assertRegex(body, r"if\s*\(!count\)\s*return\s*''")

    def test_the_grid_says_it(self):
        self.assertIn("finalsMeetingsNote(", self.body("h2hGridCard"))

    def test_the_grid_counts_each_pairing_once_and_not_once_per_cell(self):
        # The grid draws every pairing twice, once from each team's side. Summed over
        # the cells, one semi-final reads as two.
        body = self.body("h2hGridCard")
        call = re.search(self.GUARD + r"\(([^)]*\n?[^)]*)\)", body)
        self.assertIsNotNone(call, "the grid never asks")
        self.assertNotIn("records", call.group(1),
                         "the grid counts its cells, so every final is doubled")
        self.assertRegex(body, r"pairings\[h2hKey\(")

    def test_the_opponent_table_says_it(self):
        # Asked of this team's own opponents, which is what the rows are.
        body = self.body("teamH2HTable")
        self.assertIn(self.GUARD + "(records)", body)
        self.assertIn("finalsMeetingsNote(", body)

    def test_the_caption_carries_it_rather_than_a_hover(self):
        # A hover is nothing on a phone and nothing on the printout pinned to the
        # clubhouse wall. Both tables add it to the caption they already build.
        for fn in ("h2hGridCard", "teamH2HTable"):
            with self.subTest(fn=fn):
                body = self.body(fn)
                at = body.find("finalsMeetingsNote(")
                self.assertGreater(at, 0)
                line = body[body.rfind("\n", 0, at) + 1:body.find("\n", at)]
                self.assertIn("caption", line)

    def test_the_louder_half_is_asked_of_the_ruleset_in_force(self):
        # A hand-written `ladder_by` need not break ties on head-to-head, and where
        # it does not the difference is a curiosity rather than something that moved
        # a position.
        code = jslint.code_only(self.assets()["app.js"])
        self.assertIn("function laddersTiebreakIsHeadToHead(", code)
        # `>= 0`, not `> 0`. A hand-written `ladder_by` may lead on head-to-head --
        # nothing forbids it, and a grade settled on results between the two teams
        # before anything else is a real convention -- and `> 0` is the club whose
        # tiebreak is first being told nothing about it.
        self.assertRegex(self.body("laddersTiebreakIsHeadToHead"),
                         r"ladder_by.*indexOf\('head_to_head'\)\s*>=\s*0")
        self.assertIn("laddersTiebreakIsHeadToHead()",
                      self.body("finalsMeetingsNote"))
        self.assertIn(self.LOUD, self.body("finalsMeetingsNote"))

    def test_the_ladder_points_at_the_grid_where_its_own_basis_says_so(self):
        # "then head to head" in the basis line is the phrase a club takes to the
        # grid to check a position with, so that line is where the difference has to
        # be named.
        body = self.body("ladderHeadToHeadNote")
        self.assertIn("laddersTiebreakIsHeadToHead()", body)
        self.assertIn(self.GUARD + "(", body)
        self.assertIn("regular-season meetings only", body)
        # Both halves of the sentence. Naming what the tiebreak used without naming
        # where the other answer is leaves a club told that two figures differ and
        # not told which of the two it is looking at.
        self.assertIn("grid on this page", body)
        self.assertIn("ladderHeadToHeadNote()", self.body("ladderNote"))

    def test_the_ladder_note_needs_both_a_tiebreak_and_a_final(self):
        # Either alone says nothing true: a grade with no finals has no difference
        # to report, and a ruleset that never compares two teams' meetings has
        # nothing the grid could be mistaken for.
        body = self.body("ladderHeadToHeadNote")
        self.assertRegex(body, r"if\s*\(!laddersTiebreakIsHeadToHead\(\)\)"
                               r"\s*return\s*''")
        self.assertRegex(body, r"if\s*\(!" + self.GUARD + r"\([^)]*\)\)"
                               r"\s*return\s*''")


class TestThePageAndTheExporterAgreeOnTheDocumentFormat(WebAssetCase):
    """``SEASON_FORMAT``, written down in two languages, checked against itself.

    ``app.js`` is copied into every competition's directory byte for byte, so the
    number cannot be injected from Python the way the club's name is: the two
    declarations have to be separate, and the only thing that can hold them
    together is a test.

    Both ways of drifting mislead a club about its own numbers. Bump the exporter
    alone and every freshly built dashboard -- data and page written by the same
    build run, so in fact perfectly in step -- opens under a warning that its
    figures may mean something else. Bump the page alone and a genuinely older
    document renders in silence, which is the failure the marker exists to prevent.
    """

    def declared(self) -> int:
        """The number ``app.js`` declares."""
        # Read from code with the comments blanked: the paragraph above the
        # declaration names the constant too, and a file that had lost the
        # declaration but kept the comment would otherwise pass.
        found = re.search(r"\bvar\s+SEASON_FORMAT\s*=\s*(\d+)\s*;",
                          jslint.code_only(self.assets()["app.js"]))
        self.assertIsNotNone(found, "app.js declares no SEASON_FORMAT")
        return int(found.group(1))

    def test_the_page_declares_the_number_the_exporter_stamps(self):
        from matchcentre import export
        self.assertEqual(self.declared(), export.SEASON_FORMAT,
                         "app.js and export.py name different document formats, "
                         "so every dashboard built from this tree warns about "
                         "data that is in fact current")

    def test_the_exporter_stamps_the_document_with_its_own_constant(self):
        # Read off a built document rather than from the key name repeated here,
        # because a marker the exporter forgets to write is indistinguishable to
        # the page from one that predates the marker.
        from matchcentre import export
        self.assertEqual(_season_doc().get("format"), export.SEASON_FORMAT,
                         "the season document carries no format marker, or not "
                         "the constant the page compares against")

    def test_the_number_is_an_integer_on_the_python_side_too(self):
        # `!==` in JavaScript is type-strict, and the page declares a numeric
        # literal. A marker exported as the string "1" would disagree with a page
        # that is in step with it, and the warning would name two numbers that
        # look identical.
        from matchcentre import export
        self.assertIsInstance(export.SEASON_FORMAT, int)
        self.assertNotIsInstance(export.SEASON_FORMAT, bool)

    def test_the_page_checks_the_documents_number_against_its_own(self):
        # In the health block, because that is the one part of the page a club
        # reads to find out what is wrong with the numbers above it.
        code = jslint.code_only(self.body("renderHealth"))
        self.assertRegex(code, r"S\.format\s*!==\s*SEASON_FORMAT"
                               r"|SEASON_FORMAT\s*!==\s*S\.format",
                         "renderHealth never compares the document's format "
                         "marker with the one this page was written for")

    def test_a_document_naming_no_format_is_not_read_as_agreement(self):
        # A document with no marker was built before the marker existed, which
        # makes it older than format 1 rather than current. Read as agreement it
        # renders in silence, and silence is the one answer this project does not
        # give (F1: absence is never evidence).
        code = jslint.code_only(self.body("renderHealth"))
        self.assertNotRegex(code, r"S\.format\s*!==\s*undefined\s*&&",
                            "an absent format marker is waved through")
        self.assertRegex(code, r"S\.format\s*===\s*undefined",
                         "the page never distinguishes a document that names no "
                         "format from one that names a different one")

    def note_call(self) -> str:
        """The schema-version note as written, strings intact.

        Strings intact because the sentence a club reads *is* string literals, and
        :func:`jslint.code_only` would blank the only thing under test.
        """
        body = self.body("renderHealth")
        at = body.find("Schema version")
        self.assertGreater(at, 0, "the page has no schema-version note at all")
        opens = body.rfind("note(", 0, at)
        self.assertGreater(opens, 0, "the schema-version wording is not in a note, "
                                     "so it is not marked as anything")
        return body[opens:body.find("));", at)]

    def test_the_warning_names_both_numbers_and_the_remedy(self):
        # Both, because a club told only that something is out of step cannot tell
        # which side to fix; and the remedy, because a warning whose reader has
        # nothing to do about it is a dead end. The document's number is required
        # to be interpolated rather than merely mentioned -- describing it in
        # prose ("a different one") is what makes the note unactionable.
        sentence = self.note_call()
        self.assertIn("SEASON_FORMAT", sentence,
                      "the note omits the format this page was written for")
        self.assertRegex(sentence, r"\+\s*S\.format\b",
                         "the note never prints the format the data claims")
        self.assertIn("build.py", sentence,
                      "the note does not say how to bring the two back in step")

    def test_the_mismatch_is_marked_as_a_warning_rather_than_an_aside(self):
        # Every figure on the page is downstream of the document's shape, so a
        # mismatch is a caveat over the whole dashboard. Styled as a muted aside it
        # reads as trivia beside numbers it in fact contradicts.
        self.assertRegex(self.note_call(), r"^note\(\s*'warning'",
                         "the schema-version note is not raised as a warning")


class TestAPageBesideADocumentOfAnotherFormatDrawsNoViewOfIt(WebAssetCase):
    """Under the Schema version note the page draws no view of the season (4.68).

    The note said some values may be missing or mean something else, and every view
    was drawn beneath it anyway. Measured in headless Chrome on the demo's document
    rewritten to each older shape (/tmp/t7/scratch/W1/before-format.txt): at format
    2 the overview drew 0 match cards and the match list said ``scheduled`` 15
    times, the season's played nights read as not played; with format 1 or none the
    overview said ``no matches yet`` over six tiles, a team's page said ``counts for
    nothing`` 5 times and ``not played`` 16 times, and every one of those is a
    missing key read as a verdict. A format 5 document drew every view as if it
    were 4, and the page cannot know what a later format changed. The same test
    that raises the note now draws one short view in place of any of them, and
    that view points at the note rather than saying it again.

    Rejected: withholding per view only what each works out for itself, the tiles,
    the trails and the cards' verdicts. Each view would carry its own list of the
    keys it needs, to be revisited at every format, and a list out of date reads
    an absent key as evidence where no list was needed.
    """

    VIEWS = ("renderTeam", "renderPlayer", "renderMatch", "renderMatches",
             "renderPlayers", "renderOverview")

    def test_the_views_are_withheld_under_the_notes_own_condition(self):
        self.assertEqual(self.guard_for("render", "renderWithheld(main)",
                                        emits="renderWithheld"),
                         self.guard_for("renderHealth", "'Schema version'"))

    def test_every_view_is_drawn_only_in_the_gates_else(self):
        # The gate is the first `if` of the chain render() already dispatches the
        # views on. An early `return` after it would draw the same page, and is
        # not the form this function is written in.
        code = jslint.code_only(self.body("render"))
        calls = [found.start() for found in
                 re.finditer(rf"\b(?:{'|'.join(self.VIEWS)})\(main\b", code)]
        self.assertEqual(len(calls), len(self.VIEWS))
        gate = code.find("renderWithheld(main);")
        self.assertGreater(gate, 0, "render() never withholds the views")
        self.assertLess(gate, min(calls))
        chain = code[gate + len("renderWithheld(main);"):
                     statement_end(code, max(calls)) + 1]
        self.assertRegex(chain, r"^(?:\s*else(?: if \([^()]*\))? \w+\(main(?:, "
                                r"route\.\w+)?\);)+$",
                         "a view is drawn outside the else of the gate, so it is "
                         "drawn under the note as well")

    def test_the_withheld_view_reads_nothing_of_the_document(self):
        code = jslint.code_only(self.body("renderWithheld"))
        called = set(re.findall(r"(?<![\w$])(\w+)\s*\(", code))
        self.assertLessEqual(called - {"function", "renderWithheld"},
                             {"el", "appendChild"})
        self.assertNotRegex(code, r"(?<![\w$.])(?:S|IDX)\b")

    def test_the_withheld_view_points_at_the_note_and_does_not_restate_it(self):
        said = jslint.without_comments(self.body("renderWithheld"))
        self.assertIn("Schema version", said)
        for restated in ("SEASON_FORMAT", "build.py", "format"):
            with self.subTest(restated=restated):
                self.assertNotIn(restated, said)

    def test_the_note_says_the_season_is_not_shown(self):
        body = jslint.without_comments(self.body("renderHealth"))
        at = body.find("'Schema version'")
        sentence = body[at:body.find("));", at)]
        self.assertIn("The season is not shown", sentence)
        self.assertIn("build.py", sentence)


# ------------------------------------------------------------- legibility ----
# What the words in this stylesheet can be sitting on when a rule does not say.
# Three, because the page nests cards inside cards: a footnote can be on the page
# itself, on a card, or in a table header, and the stylesheet does not record
# which -- so each one is a real possibility and the worst of them is the one
# that matters.
NEUTRAL_SURFACES = ("--page", "--surface-1", "--surface-2")


# Keyed on the file's text, so an edit invalidates it and a test that ran after one
# cannot read a stale parse. Cached at all because the classes below ask for the
# same parse from a dozen methods, and one of them sweeps ten thousand steps.
@functools.lru_cache(maxsize=4)
def _parsed(css: str) -> tuple[tuple[csslint.Scope, ...], tuple[csslint.Rule, ...]]:
    return csslint.scopes(css), csslint.rules(css)


class ContrastCase(WebAssetCase):
    """Shared reading for the colour checks: which colour goes on what.

    The pairings are worked out from the rules rather than from the token table
    alone, because the stylesheet states some of them: ``.res.L`` declares its own
    tint and its own text colour in one block, and a check that only knew the
    tokens would have had to guess at the one place the file is explicit.
    """

    def scopes(self) -> tuple[csslint.Scope, ...]:
        found, _ = _parsed(self.assets()["styles.css"])
        self.assertTrue(found, "styles.css declares no :root palette at all, so "
                               "nothing below is checking anything")
        return found

    def painting_rules(self) -> tuple[csslint.Rule, ...]:
        """Every rule that paints words in a colour of its own.

        ``color: inherit`` is not one: it names no colour, so there is nothing to
        measure and the colour it ends up with is measured where it was declared.
        """
        _, parsed = _parsed(self.assets()["styles.css"])
        return tuple(rule for rule in parsed
                     if rule.value("color") not in (None, "inherit"))

    def background(self, rule: csslint.Rule) -> str | None:
        """What one rule declares behind its words, however it is spelt.

        Asked here once for every check that asks it: ``background`` and
        ``background-color`` paint the same thing, and a check that read only one
        of them would call a badge bare that the browser tints.
        """
        return rule.value("background") or rule.value("background-color")

    def backdrops(self, rule: csslint.Rule, tokens: dict[str, str]
                  ) -> list[tuple[str, tuple[int, int, int]]]:
        """Everything the words in one rule can be sitting on, in one palette.

        Three cases, and the middle one is why :class:`csslint.Colour` keeps its
        alpha: an opaque background is the only answer, a *translucent* one is not
        an answer at all until something is behind it, and no background at all
        means any surface the page has.
        """
        surfaces = [(name, csslint.resolve(f"var({name})", tokens))
                    for name in NEUTRAL_SURFACES]
        surfaces = [(name, colour.rgb) for name, colour in surfaces if colour]
        declared = self.background(rule)
        painted = csslint.resolve(declared, tokens) if declared else None
        if painted is None:
            return surfaces
        if painted.opaque:
            return [(declared, painted.rgb)]
        return [(f"{declared} over {name}", painted.over(rgb))
                for name, rgb in surfaces]

    def pairings(self) -> list[tuple[str, csslint.Rule, str, str, float]]:
        """Every (palette, rule, colour, backdrop) the page can render, measured.

        Returned rather than asserted on so that the failure message can be one
        list instead of the first item in it: a palette change moves dozens of
        these at once, and a check that stopped at the first would take dozens of
        runs to work through.
        """
        found = []
        for scope in self.scopes():
            for rule in self.painting_rules():
                declared = rule.value("color")
                colour = csslint.resolve(declared, scope.tokens)
                if colour is None:
                    continue
                for behind, rgb in self.backdrops(rule, scope.tokens):
                    found.append((scope.name, rule, declared, behind,
                                  csslint.contrast(colour.rgb, rgb)))
        return found

    def text_tokens(self) -> set[str]:
        """The tokens whose job is to paint words, read off the names.

        By pattern rather than by a list kept here, so that adding a role to the
        stylesheet brings it under the check without a test edit -- and so that the
        naming convention is the thing being enforced, which is what makes the
        check complete: a colour that paints words and is not named as one is the
        hole every failure below came through.
        """
        tokens = self.scopes()[0].tokens
        return {name for name in tokens
                if name.startswith("--text-") or name.endswith("-text")}


class TestTheContrastArithmeticIsTheSpecs(unittest.TestCase):
    """:mod:`csslint`'s colour maths, against values it did not compute.

    Every check below this one is a number compared with 4.5, so the number has to
    be right, and a formula tested against itself would be. These are published
    pairs -- the greys WCAG's own understanding documents use as the boundary
    cases, and the primaries whose ratios are quoted in every contrast tool -- so
    they pin the linearisation, the three coefficients and both 0.05 terms
    independently of anything in this project.
    """

    # colour, backdrop, the ratio the spec gives, and what each one pins
    REFERENCE = (
        # The two ends. 21:1 is only reachable if both 0.05 terms are present.
        ("#000000", "#ffffff", 21.00, "black on white is the maximum"),
        ("#ffffff", "#ffffff", 1.00, "a colour on itself is no contrast"),
        # The canonical pair either side of the AA line: #767676 is the lightest
        # grey that passes on white and #777777 is the first that does not, which
        # is a tenth of a step and pins the exponent.
        ("#767676", "#ffffff", 4.54, "the lightest grey that passes AA on white"),
        ("#777777", "#ffffff", 4.48, "one step lighter, and it fails"),
        ("#595959", "#ffffff", 7.00, "the AAA grey on white"),
        # The primaries, which pin the coefficients: blue is weighted least, so
        # blue on white is the highest of the three and red the lowest.
        ("#0000ff", "#ffffff", 8.59, "blue carries 0.0722 of the luminance"),
        ("#ff0000", "#ffffff", 4.00, "red carries 0.2126"),
        ("#ffff00", "#000000", 19.56, "red and green together carry 0.9278"),
    )

    def rgb(self, text: str) -> tuple[int, int, int]:
        colour = csslint.resolve(text, {})
        self.assertIsNotNone(colour, f"csslint cannot read {text}")
        return colour.rgb

    def test_every_published_pair_comes_out_at_its_published_value(self):
        for colour, backdrop, expected, pins in self.REFERENCE:
            with self.subTest(pins=pins):
                self.assertAlmostEqual(
                    csslint.contrast(self.rgb(colour), self.rgb(backdrop)),
                    expected, places=2,
                    msg=f"{colour} on {backdrop} should be {expected}:1")

    def test_the_ratio_does_not_depend_on_which_colour_is_named_first(self):
        # Worked out inside `contrast` rather than left to each caller, because a
        # caller that got it backwards would report a ratio below 1 and every
        # threshold check would fail for a reason that is not about the colours.
        for colour, backdrop, expected, pins in self.REFERENCE:
            with self.subTest(pins=pins):
                self.assertAlmostEqual(
                    csslint.contrast(self.rgb(backdrop), self.rgb(colour)),
                    expected, places=2)

    def test_the_darkest_colours_go_through_the_specs_linear_branch(self):
        # WCAG linearises piecewise and the split is at 0.03928. Dropping the
        # linear branch and using the power curve everywhere is the easy mistake,
        # and it is not a rounding difference: #030303 on white is 20.62 with the
        # split and 20.46 without, which is two tenths at the end of the scale.
        self.assertAlmostEqual(
            csslint.contrast(self.rgb("#030303"), self.rgb("#ffffff")),
            20.62, places=2,
            msg="near-black is being linearised with the power curve, so every "
                "ratio in the dark palette is slightly wrong")

    def test_a_translucent_colour_is_not_a_colour_until_it_is_composited(self):
        # A result badge's tint, the win's, as the palette declares it. Kept as
        # alpha until a caller says what is behind it, because compositing it
        # over an assumed white here would report a contrast the dark palette
        # never renders.
        tint = csslint.resolve("rgba(12, 163, 12, 0.14)", {})
        self.assertFalse(tint.opaque, "an alpha of 0.14 was read as opaque")
        self.assertEqual(tint.over((244, 243, 240)), (212, 232, 208))
        self.assertEqual(tint.over((35, 35, 34)), (32, 53, 31))

    def test_the_threshold_is_the_specs_small_text_figure(self):
        # Written down here because it is the one number in this file that comes
        # from outside it, and lowering it is the cheapest way to make every check
        # below pass without changing a colour.
        self.assertEqual(csslint.AA_SMALL, 4.5)

    def test_a_value_that_is_not_a_colour_is_not_guessed_at(self):
        # `None` rather than a default, so a rule whose background this reader
        # cannot parse falls back to being checked against every surface -- which
        # is stricter -- instead of being checked against an invented one.
        for value in ("none", "inherit", "transparent", "currentColor",
                      "var(--nothing-declares-this)", "0 1px 2px black"):
            with self.subTest(value=value):
                self.assertIsNone(csslint.resolve(value, {}))


class TestEveryColourTheDashboardPrintsWordsInIsLegible(ContrastCase):
    """WCAG AA, computed from the stylesheet, in every palette it declares.

    The dashboard's own muted grey used to fail this at 3.24:1 against a table
    header, and it is not decoration: the ladder position, every match's points
    and status, every tile's footnote and every "derived rather than declared"
    caveat are painted in it. A caveat nobody can read is worse than no caveat,
    because the number beside it is perfectly legible.

    Four things made it possible to ship that and not know. There is no browser
    here to look at. The palette is declared four times and only one of them is
    the one you are looking at. Print inherits from whichever theme was on screen,
    so a token fixed in dark and forgotten in print comes out wrong on paper. And
    a colour named for a *state* -- amber for a warning -- gets used for the word
    as well as for the dot beside it, which is a colour chosen to stand out
    against a surface being asked to be read on one.

    The single threshold is 4.5:1, WCAG's small-text figure, and it is applied to
    text of every size on purpose. The exemption for large text would let three or
    four figures on this page through at 3:1, but those figures are painted with
    the same tokens as the 12px footnotes under them -- so the smallest use is the
    binding one, and a per-size threshold would only be a way of letting the
    footnotes fail.
    """

    def test_every_colour_in_every_palette_clears_aa_on_everything_it_sits_on(self):
        under = [(scope, rule, colour, behind, ratio)
                 for scope, rule, colour, behind, ratio in self.pairings()
                 if ratio < csslint.AA_SMALL]
        if under:
            worst = sorted(under, key=lambda row: row[4])[:12]
            lines = [f"  {ratio:5.2f}:1  {colour} on {behind}\n"
                     f"            {rule.where}\n"
                     f"            palette: {scope}"
                     for scope, rule, colour, behind, ratio in worst]
            self.fail(f"{len(under)} colour pairing(s) under "
                      f"{csslint.AA_SMALL}:1, worst first:\n" + "\n".join(lines))

    def test_the_palettes_the_check_found_are_all_of_them(self):
        # A guard on the check rather than on the stylesheet: a reader that
        # silently found one palette would pass this file today and stop covering
        # dark mode, and nothing above would say so. Six is three on screen --
        # light, the OS preference, the in-page toggle -- times the two states
        # print can be reached from.
        names = [scope.name for scope in self.scopes()]
        self.assertEqual(len(names), 6, f"expected six palettes, found: {names}")
        self.assertTrue(any("prefers-color-scheme: dark" in n for n in names))
        self.assertTrue(any('data-theme="dark"' in n for n in names))

    def test_a_page_printed_out_of_dark_mode_is_checked_against_paper(self):
        # The combination, not the two palettes separately. A club that reads its
        # dashboard in dark mode and prints the ladder for the noticeboard gets
        # print's white surfaces with whatever dark declared and print did not
        # re-declare, and a pale dark-mode red on white paper is invisible.
        printed = [scope for scope in self.scopes()
                   if "print" in scope.name and "dark" in scope.name]
        self.assertTrue(printed, "no palette in this check is print over dark, so "
                                 "a token fixed for the screen and forgotten for "
                                 "paper would pass")
        for scope in printed:
            with self.subTest(palette=scope.name):
                self.assertEqual(scope.tokens["--page"], "#ffffff",
                                 "printing from dark mode keeps a dark page")

    def test_the_rules_the_check_found_are_the_ones_that_paint_words(self):
        # The other half of the same guard. The count is deliberately loose -- this
        # is not a census of the stylesheet -- but a reader that returned a handful
        # would make the check above pass vacuously.
        rules = self.painting_rules()
        self.assertGreater(len(rules), 40,
                           f"only {len(rules)} rule(s) found to paint words; the "
                           f"stylesheet has far more than that")
        selectors = {rule.selector for rule in rules}
        for expected in (".res.W", ".res.L", ".flag-bad", "body"):
            self.assertIn(expected, selectors,
                          f"{expected} paints words and is not being checked")

    def test_every_surface_the_stylesheet_declares_is_one_words_are_checked_on(self):
        # The third guard, and the one that decides how strict the check is: the
        # worst surface is the binding one, so a surface left out of the list is a
        # contrast nobody measures. Read back off the palette rather than trusted,
        # so adding a surface token to the stylesheet brings it under the check
        # instead of quietly past it.
        declared = {name for name in self.scopes()[0].tokens
                    if name == "--page" or name.startswith("--surface-")}
        self.assertEqual(declared, set(NEUTRAL_SURFACES),
                         "the surfaces words can sit on and the surfaces they are "
                         "checked against have drifted apart")

    def test_every_rule_that_paints_words_is_measured_in_every_palette(self):
        # The last way this check could pass vacuously: a reader that returned no
        # backdrop for a rule would drop it from the measurement entirely, and
        # nothing above counts what was measured. Every rule that names a colour
        # has to come out of `pairings` at least once per palette it resolves in.
        measured = {(scope, rule.where) for scope, rule, _, _, _ in self.pairings()}
        expected = set()
        for scope in self.scopes():
            for rule in self.painting_rules():
                if csslint.resolve(rule.value("color"), scope.tokens):
                    expected.add((scope.name, rule.where))
        self.assertEqual(expected - measured, set(),
                         "these rules name a colour and were measured against "
                         "nothing")

    def test_a_tinted_badge_is_measured_against_its_own_tint(self):
        # `.res.W` and `.res.L` paint their words on a tint of their own, over
        # whatever the row is. Measuring them against the neutral surfaces
        # instead would report a contrast neither badge has, in the more
        # flattering direction. Asked of what the tint composites to, not of how
        # it is spelt: this used to look for `rgba` in the backdrop's name, which
        # held only while the tint was written into the rule by hand, and failed
        # on the token that replaced it by saying the badge was not checked
        # against its tint -- above a list of its tint over each surface.
        light = self.scopes()[0]
        badge = [rule for rule in self.painting_rules()
                 if rule.selector == ".res.L"]
        self.assertEqual(len(badge), 1, "styles.css has no single .res.L rule")
        tint = csslint.resolve(self.background(badge[0]) or "none", light.tokens)
        # A question about this reader, so asked only of a tint it can read. A
        # badge with no tint, or one nothing here can read, is reported once, with
        # the reason, by TestEveryColourARulePaintsComesFromThePalette.
        if tint is None:
            return
        behind = self.backdrops(badge[0], light.tokens)
        surfaces = [csslint.resolve(f"var({name})", light.tokens).rgb
                    for name in NEUTRAL_SURFACES]
        self.assertEqual({rgb for _, rgb in behind},
                         {tint.over(rgb) for rgb in surfaces},
                         f".res.L is being checked against {behind}, which is "
                         f"not its tint over each surface a row can be")


class TestANumberInsideAFilledBarIsLegibleWhateverTheFill(ContrastCase):
    """``inkOn``, the one place the page sets words over a data colour.

    Everything else on the dashboard is text on a surface, which the stylesheet
    settles. This is text on a *fill*: the number inside a bar, the score inside a
    heat cell. The fill can be a diverging pole, a step of the sequential ramp, or
    a hue generated at runtime from a team's slug -- so there is no list of
    backgrounds to check, and the rule has to hold for every luminance there is.

    Which makes it checkable without a browser after all. The function is one
    comparison against one threshold, so the comparison *and* its three constants
    are read out of ``app.js`` -- nothing about the rule is written down twice --
    and the arithmetic is done here, over the whole range rather than over the
    palette: a check against today's six ramp steps would have passed the moment a
    seventh was added.

    It used to pick white for any fill below 0.42 luminance, which put a 600-weight
    number on the middle of the blue ramp at 2.50:1.
    """

    def constants(self) -> tuple[float, str, str]:
        """``INK_CUT``, ``INK_DARK``, ``INK_LIGHT`` as ``app.js`` declares them.

        Read from the source with its strings intact, because two of the three
        *are* string literals and :func:`jslint.code_only` would blank the values
        under test. The paragraph above the declarations quotes both the old
        threshold and the near-black that cannot be used, so the pattern is
        anchored on ``var NAME =`` rather than on the numbers.
        """
        cached = getattr(self, "_ink_constants", None)
        if cached is not None:
            return cached
        code = self.assets()["app.js"]
        found = {}
        for name in ("INK_CUT", "INK_DARK", "INK_LIGHT"):
            match = re.search(r"\bvar\s+" + name + r"\s*=\s*'?([^;']+)'?\s*;", code)
            self.assertIsNotNone(match, f"app.js declares no {name}, so the ink "
                                        f"rule is a literal nothing can check")
            found[name] = match.group(1).strip()
        self._ink_constants = (float(found["INK_CUT"]), found["INK_DARK"],
                               found["INK_LIGHT"])
        return self._ink_constants

    def test_the_function_decides_by_those_constants_rather_than_by_a_literal(self):
        # Otherwise the three tests below check three numbers the running page
        # does not use.
        code = jslint.code_only(self.assets()["app.js"])
        found = re.search(r"function inkOn\([^)]*\)\s*\{([^}]*)\}", code)
        self.assertIsNotNone(found, "app.js defines no inkOn()")
        body = found.group(1)
        for name in ("INK_CUT", "INK_DARK", "INK_LIGHT"):
            self.assertIn(name, body, f"inkOn does not use {name}")
        self.assertNotRegex(body, r"#[0-9a-fA-F]{3,6}|\d*\.\d+",
                            "inkOn still has a colour or a threshold written into "
                            "it, so the constants above it are decoration")

    def comparison(self) -> tuple[str, str, str]:
        """The operator ``inkOn`` compares with, and which ink is on which side.

        Read out of the source for the same reason the constants are. The sweep
        below needs a model of the function, and a model written by hand here is a
        second implementation that can agree with the spec while the page does not:
        swapping the two branches leaves all three constants correct and makes
        every number on the page unreadable, and a sweep that decided the direction
        for itself would never see it.
        """
        cached = getattr(self, "_ink_rule", None)
        if cached is not None:
            return cached
        code = jslint.code_only(self.assets()["app.js"])
        found = re.search(
            r"function inkOn\([^)]*\)\s*\{\s*return\s+luminance\([^)]*\)\s*"
            r"(<=|>=|<|>)\s*(\w+)\s*\?\s*(\w+)\s*:\s*(\w+)\s*;\s*\}", code)
        self.assertIsNotNone(found, "inkOn is not one comparison between one "
                                    "threshold and two inks any more, so the "
                                    "sweep below is modelling a function that is "
                                    "not there")
        operator, threshold, then, otherwise = found.groups()
        self.assertEqual(threshold, "INK_CUT",
                         f"inkOn compares against {threshold}, not the constant "
                         f"the tests below are checking")
        self._ink_rule = (operator, then, otherwise)
        return self._ink_rule

    def ink_for(self, luminance: float) -> str:
        cut, dark, light = self.constants()
        operator, then, otherwise = self.comparison()
        above = {"<": luminance < cut, "<=": luminance <= cut,
                 ">": luminance > cut, ">=": luminance >= cut}[operator]
        chosen = then if above else otherwise
        self.assertIn(chosen, ("INK_DARK", "INK_LIGHT"),
                      f"inkOn can return {chosen}, which is neither ink")
        return {"INK_DARK": dark, "INK_LIGHT": light}[chosen]

    def test_whatever_the_fill_is_the_ink_it_picks_clears_aa(self):
        # Swept rather than sampled at the palette, because the team hues are
        # generated from slugs at runtime and a club can add a team. Ten thousand
        # steps is fine enough to land either side of any threshold in the legal
        # window, and because the direction is read from the source too, this also
        # catches the comparison being turned round -- which a test of the three
        # constants alone would not.
        cut, dark, light = self.constants()
        inks = {}
        for value in (dark, light):
            colour = csslint.resolve(value, {})
            self.assertIsNotNone(colour, f"inkOn can return {value!r}, which is "
                                         f"not a colour csslint can read")
            inks[value] = csslint.luminance(colour.rgb)
        worst = (10.0, None)
        for step in range(10001):
            level = step / 10000
            # The ratio for a fill at this luminance, from the luminances directly:
            # there is no one hex for an arbitrary luminance, and the ratio needs
            # nothing else.
            ink = inks[self.ink_for(level)]
            lighter, darker = max(level, ink), min(level, ink)
            ratio = (lighter + 0.05) / (darker + 0.05)
            if ratio < worst[0]:
                worst = (ratio, level)
        self.assertGreaterEqual(
            worst[0], csslint.AA_SMALL,
            f"a fill at luminance {worst[1]} gets ink at {worst[0]:.2f}:1")

    def window(self, dark: str, light: str) -> tuple[float, float]:
        """The fills each ink clears AA on: dark from ``low`` up, light to ``high``.

        Worked out rather than written down. Against a fill of luminance ``f``, an
        ink of luminance ``i`` gives ``(f + 0.05) / (i + 0.05)`` while the fill is
        the lighter of the two, so a dark ink clears the threshold from
        ``AA * (i + 0.05) - 0.05`` upwards; a light ink gives the reciprocal and
        clears it up to ``(i + 0.05) / AA - 0.05``. A black-or-white rule needs a
        threshold between the two, and there is only a between if the dark ink is
        dark enough -- which is the whole argument for the constants below.
        """
        low = csslint.AA_SMALL * (
            csslint.luminance(csslint.resolve(dark, {}).rgb) + 0.05) - 0.05
        high = (csslint.luminance(csslint.resolve(light, {}).rgb) + 0.05) \
            / csslint.AA_SMALL - 0.05
        return low, high

    def test_the_threshold_is_inside_the_window_where_both_choices_work(self):
        # Stated as the window rather than as the one value, because the value is a
        # judgement inside it and the window is the fact. Below 0.175 the dark ink
        # fails on the fills just above the threshold; above 0.1833 white fails on
        # the fills just below it -- and both figures are quoted in the comment
        # over `inkOn`, so they are confirmed here rather than restated.
        cut, dark, light = self.constants()
        low, high = self.window(dark, light)
        self.assertAlmostEqual(low, 0.175, places=4)
        self.assertAlmostEqual(high, 0.1833, places=4)
        self.assertGreaterEqual(cut, low)
        self.assertLessEqual(cut, high)

    def test_the_dark_ink_is_black_rather_than_the_pages_near_black(self):
        # #0b0b0b is the page's ink everywhere else and it cannot be used here:
        # its own luminance eats enough of the ratio that the window closes. Worth
        # a test because #0b0b0b is the obvious thing to reach for, and worth
        # proving rather than asserting because "no threshold works" is a claim
        # about every threshold.
        cut, dark, light = self.constants()
        self.assertEqual(csslint.luminance(csslint.resolve(dark, {}).rgb), 0.0,
                         f"the dark ink is {dark}, which is not black; no "
                         f"threshold makes both branches clear AA")
        self.assertEqual(csslint.resolve(light, {}).rgb, (255, 255, 255))
        low, high = self.window("#0b0b0b", light)
        self.assertGreater(low, high,
                           f"#0b0b0b would leave a window of [{low}, {high}], so "
                           f"the argument in app.js for pure black is wrong")

    def test_every_fill_the_stylesheet_actually_declares_clears_it_too(self):
        # The sweep above proves the rule; this names the palette, so a failure
        # says which token to look at rather than which luminance.
        cut, dark, light = self.constants()
        for scope in self.scopes():
            for name, value in sorted(scope.tokens.items()):
                if not (name.startswith("--seq-") or name.startswith("--div-")
                        or name.startswith("--series-") or name == "--deemph"):
                    continue
                fill = csslint.resolve(f"var({name})", scope.tokens)
                if fill is None:
                    continue
                chosen = csslint.resolve(self.ink_for(csslint.luminance(fill.rgb)), {})
                with self.subTest(fill=name, palette=scope.name):
                    self.assertGreaterEqual(
                        csslint.contrast(fill.rgb, chosen.rgb), csslint.AA_SMALL,
                        f"a number inside {name} ({value}) is unreadable")


class TestOnlyAColourNamedForWordsIsUsedToPaintWords(ContrastCase):
    """``color:`` may name a text role or inherit, and nothing else.

    This is what makes the check above complete rather than merely true today.
    Every failure it found came in the same way: a token chosen to be a *mark* --
    the amber dot beside a warning, the red rule under a rejected card -- reused
    for the words next to it. A mark only has to be visible, which WCAG settles at
    3:1 against its surroundings; a word has to be read. Amber at 1.65:1 against a
    table header is a perfectly good dot.

    So the split is the rule, and the names carry it: ``--warning`` is the dot,
    ``--warning-text`` is the word, and the stylesheet already worked this way for
    one of the four before anyone checked -- ``--good`` beside ``--success-text``.
    """

    def test_no_rule_paints_words_with_a_colour_written_in_by_hand(self):
        # A literal would still be checked for contrast, so this is not about
        # legibility: it is about the file's own promise, stated in its opening
        # comment, that colour is declared once by role. A hex in the middle of
        # the file is a colour the dark palette cannot override.
        loose = [rule for rule in self.painting_rules()
                 if not rule.value("color").startswith("var(")]
        self.assertFalse(loose, "colour written straight into a rule: "
                                + "; ".join(r.where for r in loose))

    def test_every_colour_that_paints_words_is_named_as_a_text_colour(self):
        roles = self.text_tokens()
        self.assertIn("--text-muted", roles, "the token names have changed shape, "
                                             "so this check is looking for nothing")
        wrong = [(rule, rule.value("color")) for rule in self.painting_rules()
                 if rule.value("color")[4:-1] not in roles]
        if wrong:
            self.fail("these rules paint words with a colour not named for words, "
                      "so nothing holds it to a readable contrast:\n"
                      + "\n".join(f"  {value} at {rule.where}"
                                  for rule, value in wrong))

    def test_the_status_colours_that_carry_marks_are_still_there_for_marks(self):
        # The point of the split is that both halves exist. Deleting `--warning`
        # and pointing the dot at `--warning-text` would pass the check above and
        # lose the thing the dot is for -- a state you can pick out at a glance.
        tokens = self.scopes()[0].tokens
        for mark, word in (("--good", "--success-text"),
                           ("--warning", "--warning-text"),
                           ("--critical", "--critical-text")):
            with self.subTest(mark=mark):
                self.assertIn(mark, tokens, f"{mark} is gone, so the marks and "
                                            f"the words are one colour again")
                self.assertIn(word, tokens)
                self.assertNotEqual(tokens[mark], tokens[word],
                                    f"{mark} and {word} are the same colour, so "
                                    f"one of the two is doing the wrong job")

    def test_a_mark_only_colour_is_not_quietly_a_text_colour_by_name(self):
        # `--critical` must not end up matching the `-text` pattern that
        # `text_tokens` reads, or the check above would wave it through on the
        # strength of its name while it fails on its value.
        self.assertFalse({"--good", "--warning", "--serious", "--critical",
                          "--focus", "--axis", "--grid", "--deemph"}
                         & self.text_tokens())


class TestTheTwoDarkPalettesAreOnePalette(ContrastCase):
    """Declared twice on purpose, so they have to be checked against each other.

    The stylesheet says why it repeats itself: the media query covers the OS
    preference and the ``[data-theme]`` scope covers the in-page toggle, and both
    must win over the light base. Nothing about that arrangement makes them agree,
    and a colour fixed in one is the cheapest possible bug -- the dashboard looks
    right until someone presses the toggle.
    """

    def dark_palettes(self) -> list[csslint.Scope]:
        found = [scope for scope in self.scopes()
                 if "dark" in scope.name and "print" not in scope.name]
        self.assertEqual(len(found), 2, f"expected two dark palettes, found "
                                        f"{[s.name for s in found]}")
        return found

    def test_the_media_query_and_the_toggle_declare_the_same_colours(self):
        first, second = self.dark_palettes()
        differ = {name for name in set(first.tokens) | set(second.tokens)
                  if first.tokens.get(name) != second.tokens.get(name)}
        self.assertFalse(differ, "the OS-preference palette and the toggle "
                                 "palette disagree about: "
                                 + ", ".join(sorted(differ)))

    def test_neither_of_them_is_missing_a_colour_the_light_palette_has(self):
        # Layered over the light base, so a token neither dark scope overrides is
        # present with its light value. That is the cascade working; what this
        # rules out is a *palette* that has stopped holding a role at all, which
        # would make `resolve` return None and quietly excuse it from the check.
        light = self.scopes()[0]
        for scope in self.dark_palettes():
            with self.subTest(palette=scope.name):
                self.assertEqual(set(scope.tokens), set(light.tokens))


class TestEveryColourARulePaintsComesFromThePalette(ContrastCase):
    """Nothing writes a colour in by hand but a palette declaring its tokens.

    The stylesheet's opening comment promises that colour is declared once, by
    role, as custom properties, and the one test holding it to that read only
    ``color:``. Four colours were written straight into rules under it: the two
    result badges' tints, ``rgba(12, 163, 12, 0.14)`` and ``rgba(208, 59, 59,
    0.14)``; the tooltip's shadow; and ``#fff`` behind a printed page. None of them
    was illegible -- measured in headless Chrome, the letter on a badge clears
    5.42:1 on a card in light and 5.52:1 in dark, and worked out from the declared
    values it clears 4.98:1 on the worst surface in any palette -- so the contrast
    check measured every one and passed every one. A magenta wash behind a win
    and a magenta tooltip shadow passed all of this file as well. That is the
    case: a literal is legible only for as long as nobody changes the palette
    around it, and it is the one colour on the page a dark theme cannot reach. A
    token has a failure of its own that a literal did not, which is to be read
    and never declared, so that is checked here too.

    Each mistake has one test to report it, split by cause: a colour written in
    is the first test's, a token nobody declares the third's, a badge tinted
    wrong or not at all the fourth's. Two causes are left where they already
    were. ``color:`` is TestOnlyAColourNamedForWordsIsUsedToPaintWords', which
    holds it to more than this does, and a tint too strong to read a letter on is
    the legibility check's, which measures the letter on it.

    Rejected: reading ``background`` as well as ``color``. That catches the
    badges and misses the shadow, and a list of the properties that can hold a
    colour is one short the day somebody writes an ``outline``.
    """

    def test_only_a_palette_declaring_a_token_writes_a_colour_in_by_hand(self):
        # Every property but `color`, which the class that reads it holds to a
        # token named for words, not merely a token, so reading it here as well
        # would report one hand-typed colour twice. And a palette is let off for
        # its tokens, not wholesale: a colour a `:root` block writes into a
        # property is one every other palette has to remember to restate, and
        # nothing checks that they do.
        _, parsed = _parsed(self.assets()["styles.css"])
        found = [f"  {colour} in {prop} at {rule.where}"
                 for rule in parsed
                 for prop, value in rule.declarations
                 if prop != "color"
                 and not (prop.startswith("--") and csslint.is_palette(rule))
                 for colour in csslint.literal_colours(value)]
        if found:
            self.fail("colour written straight into a rule, where no palette can "
                      "reach it:\n" + "\n".join(found))

    def test_the_reader_finds_a_colour_however_a_hand_writes_one(self):
        # The guard on the check above, which a reader that finds nothing passes.
        # The first three are the four this sheet had, spelt as it spelt them --
        # the badges spelt theirs alike -- then the names a hand reaches for next,
        # in any case and past the seventeen CSS 2.1 had, and a shadow of two
        # layers, which is two colours in one value.
        for value, expected in (
                ("rgba(208, 59, 59, 0.14)", ["rgba(208, 59, 59, 0.14)"]),
                ("0 4px 14px rgba(11, 11, 11, 0.18)", ["rgba(11, 11, 11, 0.18)"]),
                ("#fff", ["#fff"]),
                ("1px solid White", ["White"]),
                ("0 4px 14px darkgray", ["darkgray"]),
                ("whitesmoke", ["whitesmoke"]),
                ("inset 0 0 0 1px #ddd, 0 4px 14px rgba(11, 11, 11, 0.18)",
                 ["#ddd", "rgba(11, 11, 11, 0.18)"])):
            with self.subTest(value=value):
                self.assertEqual(csslint.literal_colours(value), expected)
        # And nothing in what is not a colour anybody chose: a token, the absence
        # of one, the text's own, a token whose name has a colour in it, and a
        # filter whose name begins with one.
        for value in ("var(--loss-wash)", "1px solid var(--border)", "transparent",
                      "currentColor", "none", "inherit", "var(--text-red)",
                      "grayscale(1)"):
            with self.subTest(value=value):
                self.assertEqual(csslint.literal_colours(value), [])

    def test_every_token_a_rule_reads_is_one_the_palette_declares(self):
        # The other half of a colour coming from the palette: a literal cannot go
        # missing and a token can. Misspell one, or delete the line declaring it,
        # and the browser drops the property it was in -- the tooltip floats with
        # no shadow -- and nothing in this file resolves a shadow to notice. Asked
        # of the light palette alone, because every other one is laid over it and
        # so declares everything it does. Every property but `color` again, for
        # the same reason: a word colour read under a name nobody declares is not
        # a name for words, and the class that reads `color:` says so.
        _, parsed = _parsed(self.assets()["styles.css"])
        declared = self.scopes()[0].tokens
        reads = [(name, prop, rule) for rule in parsed
                 for prop, value in rule.declarations if prop != "color"
                 for name in csslint.tokens_read(value)]
        # Loose on purpose, as the legibility check's count of rules is: a reader
        # that found no reads would pass, and this sheet has 73.
        self.assertGreater(len(reads), 50, f"only {len(reads)} token read(s) "
                                           f"found in the stylesheet")
        missing = [f"  {name} in {prop} at {rule.where}"
                   for name, prop, rule in reads if name not in declared]
        if missing:
            self.fail("a rule reads a token no palette declares, so the browser "
                      "drops what it was in:\n" + "\n".join(missing))

    def test_each_result_badge_is_tinted_with_its_own_states_mark_colour(self):
        # Two tokens where there were two literals, and the slip that comes with
        # them is the wrong one of the two. The letter carries the meaning and the
        # tint only agrees with it, so a tint that disagrees -- a win washed red --
        # is the one thing it can get wrong, and a contrast ratio cannot see it:
        # swapped, the two letters still clear 4.70:1 on each other's tints.
        #
        # Every rule for a badge, the ones inside an at-rule as well, is asked in
        # every palette, which is how the legibility check asks of every rule:
        # stricter than the cascade, in the safe direction. A tint that does not
        # resolve is reported here only if nothing else explains it, because a
        # colour written in and a token nobody declares are the two checks
        # above's. And a tint is refused for being none, not for being too much:
        # one too strong to read a letter on is the legibility check's, which
        # measures the letter on it.
        _, parsed = _parsed(self.assets()["styles.css"])
        wrong: dict[str, set[str]] = {}
        for selector, mark in ((".res.W", "--good"), (".res.L", "--critical")):
            badge = [rule for rule in parsed if rule.selector == selector]
            for rule in badge:
                declared = self.background(rule)
                if declared is None:
                    if not rule.at_rule:
                        wrong[f"  {rule.where} declares no tint, so its letter "
                              f"sits on the bare row"] = set()
                    continue
                for scope in self.scopes():
                    tint = csslint.resolve(declared, scope.tokens)
                    own = csslint.resolve(f"var({mark})", scope.tokens)
                    if tint is None:
                        if (csslint.literal_colours(declared)
                                or set(csslint.tokens_read(declared))
                                - set(scope.tokens)):
                            continue
                        line = (f"  {rule.where} is tinted `{declared}`, which "
                                f"this reader cannot resolve, so the legibility "
                                f"check measures its letter on the bare row")
                    elif own is not None and (tint.rgb != own.rgb
                                              or tint.alpha == 0):
                        line = (f"  {rule.where} is tinted {tint.rgb} at an alpha "
                                f"of {tint.alpha:g}, which is not a tint of {mark} "
                                f"{own.rgb}")
                    else:
                        continue
                    wrong.setdefault(line, set()).add(scope.name)
        if wrong:
            total = len(self.scopes())
            self.fail("a result badge is not tinted with its own state's mark "
                      "colour:\n"
                      + "\n".join(line + (f" (in {len(where)} of {total} "
                                           f"palettes)" if where else "")
                                  for line, where in wrong.items()))


class TestTheFirstTabStopReachesTheContentWithoutLosingThePage(WebAssetCase):
    """The skip link, on a page that keeps its route in the fragment it targets.

    A skip link points at the id of the region it skips to; that is the whole of
    the mechanism, and there is no other href that works. On the dashboard that
    puts it in direct conflict with the router, which reads *every* fragment as a
    route and answers a fragment it cannot parse by rendering the Overview. So the
    browser's own jump to ``#view`` used to throw the page away: from Matches,
    Tab then Enter gave a reader the Overview, scrolled to the top, with a URL
    that no longer said where they had been. The first tab stop on the page was
    the one control that lost your place -- and only for the readers who have no
    choice but to use it.

    The fix cannot be a different href, so it is to stop the jump and move focus
    by hand. What these tests hold is both halves of that: the markup still names
    a real, focusable region, and the script still refuses the navigation.
    """

    FOCUSABLE = re.compile(r"<(?:a|button|input|select|textarea)\b[^>]*>", re.I)

    def markup(self, page: str) -> str:
        """``page`` with its comments removed.

        The page templates carry long explanatory comments, some of them showing
        the very markup under discussion, so a search of the raw file finds an
        example and passes.
        """
        return re.sub(r"<!--.*?-->", "", self.assets()[page], flags=re.S)

    def skip_link(self, page: str) -> tuple[str, str]:
        """``page``'s skip link: the whole opening tag, and its href."""
        found = re.search(r'<a\b[^>]*\bclass="[^"]*\bskip-link\b[^"]*"[^>]*>',
                          self.markup(page))
        self.assertIsNotNone(found, f"{page} has no .skip-link, so a keyboard "
                                    f"reader tabs through the whole topbar "
                                    f"before reaching anything they came for")
        tag = found.group(0)
        href = re.search(r'\bhref="([^"]*)"', tag)
        self.assertIsNotNone(href, f"{page}'s skip link has no href, so it is "
                                   f"not a link and nothing focuses it")
        return tag, href.group(1)

    def fragment_links(self) -> list[tuple[str, str]]:
        """Every fragment href the dashboard can offer, as (where, href)."""
        found = [("index.html", href) for href
                 in re.findall(r'\bhref="(#[^"]*)"', self.markup("index.html"))]
        # app.js builds its hrefs as string literals, so they are read from the
        # source and not from code_only(), which would blank them.
        found += [("app.js", href) for href
                  in re.findall(r"\bhref:\s*'(#[^']*)'", self.assets()["app.js"])]
        self.assertTrue(found, "no fragment link found in either the page or the "
                               "script, which means this check is not checking "
                               "anything")
        return found

    def test_the_skip_link_is_the_first_focusable_element_on_each_page(self):
        # A skip link that is not first is a skip link nobody reaches in time.
        for page in PAGES:
            with self.subTest(page=page):
                markup = self.markup(page)
                start = markup.find("<body")
                self.assertGreater(start, 0, f"{page} has no <body>")
                first = self.FOCUSABLE.search(markup, start)
                self.assertIsNotNone(first, f"{page} has nothing focusable in "
                                            f"its body")
                self.assertIn("skip-link", first.group(0),
                              f"the first focusable element in {page} is "
                              f"{first.group(0)!r}, not the skip link")

    def test_each_skip_link_names_a_region_of_its_own_page_that_can_take_focus(self):
        for page in PAGES:
            with self.subTest(page=page):
                markup = self.markup(page)
                _, href = self.skip_link(page)
                self.assertTrue(href.startswith("#"), f"{page}'s skip link "
                                                      f"points at {href!r}, "
                                                      f"which leaves the page")
                target = href[1:]
                tag = re.search(rf'<\w+\b[^>]*\bid="{re.escape(target)}"[^>]*>',
                                markup)
                self.assertIsNotNone(tag, f"{page}'s skip link points at "
                                          f"#{target}, which is not an id in "
                                          f"the page, so the press does nothing")
                self.assertIn('tabindex="-1"', tag.group(0),
                              f"#{target} cannot hold focus, so skipping to it "
                              f"moves the scroll and leaves the focus ring in "
                              f"the topbar: the next Tab starts over from the "
                              f"top, which is what the reader was escaping")

    def test_the_router_would_read_the_dashboards_own_skip_target_as_no_route(self):
        # The reason the handler below has to exist, kept as a test so that a
        # future rewrite of the router cannot quietly make it look unnecessary.
        _, href = self.skip_link("index.html")
        self.assertFalse(href.startswith("#/"), f"index.html's skip link points "
                                                f"at {href!r}, which is a route; "
                                                f"a skip link has to name an "
                                                f"element id")
        # Read from the source rather than code_only(), which blanks the event
        # name and the view name this is about.
        self.assertIn("'hashchange'", self.body("boot"),
                      "boot() no longer re-renders when the fragment changes; if "
                      "the router has stopped reading it, say so here rather "
                      "than deleting the test")
        # The *last* return, not any of them: every branch above it matches a
        # route, and a skip target matches none of them, so the fall-through is
        # the one line that decides what the press does.
        returns = re.findall(r"return \{ view: '(\w+)'", self.body("parseRoute"))
        self.assertTrue(returns, "parseRoute() returns no view at all")
        self.assertEqual(returns[-1], "overview",
                         "parseRoute() no longer falls through to the overview; "
                         "if an unparseable fragment now means something else, "
                         "this class's reasoning needs rewriting, not deleting")

    def test_the_dashboards_skip_link_moves_focus_instead_of_navigating(self):
        handler = self.body("initSkipLink")
        # `.focus(` rather than `.focus()`: what it is called with is the sticky
        # bar's business, held in the class that offsets for the bar, and all this
        # one asks is that focus is what moves.
        for token in (".skip-link", "'click'", "getAttribute('href')",
                      "preventDefault()", ".focus("):
            with self.subTest(token=token):
                self.assertIn(token, handler,
                              f"initSkipLink() never mentions {token}: without "
                              f"all five it is not a click handler that reads "
                              f"its own target off the link, stops the jump and "
                              f"moves focus itself")
        self.assertNotIn("location.hash", jslint.code_only(handler),
                         "initSkipLink() sets the fragment, which is the bug: "
                         "the router hears it and renders the overview")

    def test_the_handler_gives_up_only_when_there_is_nothing_to_focus(self):
        # The polarity is read out of the source rather than written down here.
        # Both guards invert into something that still contains every token the
        # test above looks for: `if (target) return` leaves a handler that does
        # nothing whenever it has something to do, which is the original bug back
        # again with a working-looking implementation in front of it.
        code = jslint.code_only(self.body("initSkipLink"))
        guards = re.findall(r"if \(!(\w+)\)\s*\{?\s*return", code)
        self.assertIn("link", guards, "initSkipLink() no longer gives up when "
                                      "the page has no skip link, so it binds a "
                                      "listener to null and the script stops "
                                      "there, taking the rest of boot() with it")
        focused = re.search(r"(\w+)\.focus\(", code)
        self.assertIsNotNone(focused, "initSkipLink() focuses nothing")
        self.assertIn(focused.group(1), guards,
                      f"initSkipLink() calls {focused.group(1)}.focus() without "
                      f"a `if (!{focused.group(1)})` guard reading the same way "
                      f"round: either it focuses something it never checked for, "
                      f"or it gives up precisely when there is something to "
                      f"focus and lets the browser navigate after all")

    def test_the_skip_link_is_wired_up_before_the_season_is_loaded(self):
        boot = jslint.code_only(self.body("boot"))
        wired = boot.find("initSkipLink()")
        self.assertGreater(wired, -1, "boot() never calls initSkipLink(), so the "
                                      "handler is defined, never bound, and the "
                                      "browser navigates as it always did")
        loaded = boot.find("loadSeason(")
        self.assertGreater(loaded, -1, "boot() no longer loads the season, so "
                                       "this ordering check means nothing")
        self.assertLess(wired, loaded,
                        "initSkipLink() is wired inside or after the load, so a "
                        "dashboard whose season.json is missing has no working "
                        "skip link -- and the page that says why it is missing "
                        "is the one a reader most needs to reach")

    def test_no_other_link_on_the_dashboard_is_a_fragment_the_router_cannot_read(self):
        _, skip = self.skip_link("index.html")
        stray = sorted({f"{where}: {href}" for where, href in self.fragment_links()
                        if href != skip and not href.startswith("#/")})
        self.assertEqual(stray, [], f"fragment link(s) that are not routes: "
                                    f"{stray}. The router reads each one as a "
                                    f"route it cannot parse and answers with the "
                                    f"overview, so an in-page anchor has to stop "
                                    f"the jump the way initSkipLink() does.")

    def test_the_picker_needs_no_handler_because_nothing_there_reads_the_fragment(self):
        picker = self.assets()["picker.html"]
        for token in ("hashchange", "location.hash"):
            with self.subTest(token=token):
                self.assertNotIn(token, picker,
                                 f"picker.html mentions {token}, so its skip "
                                 f"link now has the conflict index.html had and "
                                 f"needs the same handler")


class TestATableTooWideForTheScreenCanBeReachedAndNamed(WebAssetCase):
    """Thirteen tables on this page can scroll sideways, and a div does the scrolling.

    `.table-wrap` is `overflow-x: auto`, and on a phone most of them are over the
    width. A div that scrolls is not reachable from a keyboard: there is nothing
    to focus, so the arrow keys have nothing to scroll, and every column past the
    right edge is not merely awkward to read but unreadable, with nothing saying it
    is there. The fix is a tab stop on the wrapper, which immediately creates the
    other half of the problem -- a focusable div a screen reader can only announce
    as a div -- so the two halves are held here together, along with the two things
    that make the fix cheap: the name is text already on the screen, and the tab
    stop exists only while the table really is too wide.
    """

    def marked(self) -> str:
        """The body of the pass that marks one wrapper, comments and strings kept."""
        return self.body("markScrollRegion")

    def test_the_class_that_scrolls_is_the_class_the_pass_looks_for(self):
        # What makes the fix cover all thirteen wrappers, and the fourteenth:
        # neither end of it names a table, a view or a call site. The stylesheet
        # makes one class scroll and the script marks that same class.
        scrolling = [rule for rule in csslint.rules(self.assets()["styles.css"])
                     if ".table-wrap" in rule.selector
                     and rule.value("overflow-x") == "auto"]
        self.assertTrue(scrolling, "styles.css no longer makes .table-wrap scroll "
                                   "sideways; if the class has been renamed, the "
                                   "pass in app.js is now marking nothing")
        self.assertIn("querySelectorAll('.table-wrap')",
                      self.body("markScrollRegions"),
                      "the pass does not sweep .table-wrap, so whichever tables "
                      "it does reach are the ones somebody remembered")

    def wrap_attributes(self) -> list[str]:
        """The attribute object every scrolling wrapper is built with.

        The class name is a string literal at each site, so it is found in the
        source rather than in ``code_only()``; the object around it is taken by
        matching its braces, so a nested one does not end the search early.
        """
        code = self.assets()["app.js"]
        found = []
        for match in re.finditer(r"'table-wrap'", code):
            opened = code.rfind("{", 0, match.start())
            self.assertGreater(opened, 0, "a .table-wrap built outside an "
                                          "attribute object")
            depth = 0
            for index in range(opened, len(code)):
                if code[index] == "{":
                    depth += 1
                elif code[index] == "}":
                    depth -= 1
                    if depth == 0:
                        found.append(code[opened:index + 1])
                        break
        self.assertTrue(found, "app.js builds no .table-wrap, so this check is not "
                               "checking anything")
        return found

    def test_no_wrapper_decides_any_of_this_for_itself(self):
        # The pass takes attributes away again when a table fits, so a site that
        # sets its own is not a second opinion: it is one the pass will silently
        # overrule, on a resize, in whichever direction it happens to run.
        for attrs in self.wrap_attributes():
            for token in ("tabindex", "role", "aria-label"):
                with self.subTest(site=attrs[:48], token=token):
                    self.assertNotIn(token, attrs,
                                     f"a .table-wrap is built with {token} of its "
                                     f"own, which the marking pass owns")

    def test_it_is_a_tab_stop_only_while_the_table_is_wider_than_its_box(self):
        # Which way round the comparison reads is parsed rather than assumed, for
        # the reason inkOn's is (see TestANumberInsideAFilledBar...): flipping it
        # leaves a pass that still mentions both measurements and both attributes,
        # offering the tab stop to every table that fits and to no table that
        # needs one.
        found = re.search(r"if \(wrap\.scrollWidth - wrap\.clientWidth "
                          r"(<=|>=|<|>) (\d+)\) \{(.*?)\n  \}", self.marked(),
                          re.S)
        self.assertIsNotNone(found, "markScrollRegion() no longer decides by "
                                    "comparing the table's width with its box's, "
                                    "so whether it is a tab stop is no longer a "
                                    "question about the table")
        operator, slack, gives_up = found.groups()
        self.assertIn(operator, ("<", "<="),
                      f"the branch that gives up is taken when scrollWidth is "
                      f"{operator} clientWidth, which is the table that overflows "
                      f"-- the one case this pass exists for")
        self.assertLessEqual(int(slack), 2,
                             f"{slack} pixels of slack: a table overflowing by "
                             f"less than that is silently left unreachable")
        for token in ("removeAttribute('tabindex')", "return"):
            with self.subTest(token=token):
                self.assertIn(token, gives_up,
                              f"the fits-in-its-box branch does not {token}: a "
                              f"table that fits after a resize keeps a keystroke "
                              f"that scrolls nothing, thirteen of them on a wide "
                              f"screen, and the reader they cost the most is the "
                              f"one this is for")
        for token in ("removeAttribute('role')",
                      "removeAttribute('aria-labelledby')"):
            with self.subTest(token=token):
                self.assertIn(token, gives_up,
                              f"the fits-in-its-box branch does not {token}: a "
                              f"table that fits after a resize stays a landmark in "
                              f"the list a screen reader jumps between, pointing "
                              f"at a box that no longer scrolls")
        after = self.marked()[found.end():]
        self.assertIn("setAttribute('tabindex', '0')", after,
                      "nothing outside that branch is made focusable, so the "
                      "table that is too wide is still the one nobody can scroll")

    def test_the_name_is_words_already_on_the_screen(self):
        # Neither source is copy written for a screen reader: a chart's heading and
        # a table's caption are both visible, so what is announced is what a
        # sighted reader beside them is looking at, and there is no second place
        # for either to drift.
        name = self.body("scrollRegionName")
        self.assertIn("figcaption h3", name, "a chart's values table is named by "
                                             "the chart's own heading; without it "
                                             "the eight chart tables are nameless")
        # The sentence, not the caption it sits in. A caption now also holds the
        # Copy and CSV buttons and the key, and aria-labelledby reads the element it
        # points at whole: naming a region after the caption was measured in the
        # browser as announcing "CopyCSVM -- Matches played . Sets -- Sets played",
        # which is the two button faces and every column spec read aloud.
        self.assertIn("caption .caption-text", name,
                      "a table's own caption sentence is the name of the region "
                      "that scrolls it")
        self.assertNotRegex(name, r"querySelector\(\s*'caption'\s*\)",
                            "a branch here names the region after the whole caption "
                            "element, so what a screen reader announces begins with "
                            "the faces of the two buttons in it")
        self.assertLess(
            self.offset_of(name, "return heading", "the heading is never returned"),
            self.offset_of(name, "caption .caption-text",
                           "no caption sentence is read"),
            "the heading has stopped coming first: where a chart table has both, "
            "its caption is a note about the arithmetic and the heading is the name "
            "that tells it apart from the other tables on the page")

    def test_the_landmark_is_only_claimed_when_it_has_a_name(self):
        # An unnamed region is announced as "region" and nothing else, which is
        # worse than no role at all: it is a landmark that says to go and look and
        # will not say at what.
        # Read from the source, not code_only(): the role and the attribute name
        # are string literals, which is exactly what code_only() blanks.
        guard = re.search(r"if \((\w+)\)\s*\{(.*?)\n  \}", self.marked(), re.S)
        self.assertIsNotNone(guard, "markScrollRegion() no longer guards anything, "
                                    "so the region role is set whether or not a "
                                    "name was found")
        body = guard.group(2)
        self.assertIn("'region'", body,
                      "the region role is set outside the guard that found a name")
        self.assertIn("aria-labelledby", body,
                      "the name is attached outside the guard that found it")
        self.assertRegex(body, r"setAttribute\('aria-labelledby', name\.id\)",
                         "the name is attached by something other than the id of "
                         "the element the words are in, so what is announced is "
                         "not what is on the screen")
        # An id given out once would leave every region on the page labelled by
        # whichever element the browser found first.
        self.assertRegex(body, r"if \(!name\.id\) name\.id = '[\w-]+' \+ "
                               r"\(\+\+\w+\);",
                         "the generated id is not a fresh one behind a guard that "
                         "leaves an existing id alone")

    def test_the_pass_runs_at_each_moment_the_answer_can_change(self):
        for function, why in (
                ("render", "a view that has just been built has not been "
                           "measured, and scrollWidth is 0 until it is in the "
                           "document"),
                ("onResize", "the window is the thing the table is too wide for"),
                ("tableToggle", "a table kept hidden until it is asked for -- a "
                                "chart's values, every rubber on the matches page "
                                "-- measures as fitting while it is hidden")):
            with self.subTest(function=function):
                self.assertIn("markScrollRegion", self.body(function),
                              f"{function}() does not re-mark the wrappers: {why}")

    def test_every_table_the_page_builds_has_a_name_to_be_known_by(self):
        # The two sources the pass looks for are only a pair of places to look. What
        # makes one of them always answer is here: every table is built with a
        # caption, and every chart figure is built with a heading.
        for arguments in self.calls_of("table"):
            with self.subTest(call=arguments[:60]):
                self.assertIn("caption", arguments,
                              "a table built with no caption has no name of its "
                              "own; the ones inside chart figures borrow the "
                              "figure's heading, and this one has nothing to "
                              "borrow")
        self.assertIn("el('h3', { text: opts.title })", self.body("chartCard"),
                      "a chart figure no longer carries its own title as a "
                      "heading, so the eight values tables inside them have "
                      "nothing to be named by")


class TestATableCanLeaveThePageWithoutBeingRetyped(WebAssetCase):
    """A dashboard full of tables and no way to get one of them into a spreadsheet.

    A treasurer wanting the ladder in a spreadsheet, or a captain emailing the players
    list, had one route: drag a mouse over 24 rows and hope the columns survive. They
    do not. The sort arrow comes away with the heading it decorates, so the column
    arrives called `Player▲`; the em dash the page prints where nothing was recorded
    arrives as a character; and the minus sign it draws is U+2212, which no importer
    reads as a number. So a Copy button and a CSV button in each table's caption.

    Both read the rows out of the document rather than rebuilding them from the season.
    That is the whole reason to trust them: what leaves the page is what is on it, in
    the order the reader sorted it into, and there is no second copy of the arithmetic
    to drift from the first. Rebuilding from `SEASON_DATA` was the alternative and
    would have been easier to write -- and would have shipped a file that disagrees
    with the screen the moment a column is added to one and not the other.

    What a spreadsheet makes of this page's characters was measured, not assumed: each
    file was written, opened with LibreOffice, saved, and the saved document read back
    for `office:value-type`, which is the spreadsheet's own answer to "is this a
    number". Across the 34 exporting tables of a twelve-team season that came to 41 em
    dashes becoming empty cells and 86 U+2212 becoming an ASCII minus; every numeric
    column of the 24-player table arrived as a number, percentages as percentages, and
    W-L records as text, which is what they are.
    """

    def test_every_table_but_the_set_rows_on_a_card_can_be_taken_off_the_page(self):
        # Walked from the call sites rather than counted, because what this guards is a
        # table added later with no way out of it -- and a count would pass that.
        without = [args for args in self.calls_of("table") if "csv:" not in args]
        for arguments in without:
            with self.subTest(call=arguments[:60]):
                # The set rows inside a match card are the deliberate exception. There
                # are 56 of those cards on the matches page, all collapsed, so the pair
                # would put 112 buttons on one page to export three rows each -- and
                # those three rows are the one table a reader reads whole on the screen.
                # Every rubber of the season leaves that page in one table of its own
                # instead, which has the pair: TestEveryRubberOfTheSeasonLeavesThePage-
                # InOneTable.
                self.assertIn("setRow(", arguments,
                              "this table is built with no `csv:` name, so it has "
                              "neither a Copy button nor a CSV one and the only way "
                              "into a spreadsheet is a mouse and some hope")
        self.assertEqual(len(without), 1,
                         f"{len(without)} tables have no way off the page; the set "
                         f"rows on a match card are meant to be the only one")

    def test_the_pair_is_built_for_the_table_it_is_going_to_sit_in(self):
        built = self.body("table")
        self.assertIn("tableTools(t, o.csv)", built,
                      "the pair is built for something other than the table being "
                      "built here, so the buttons in one table's caption read another "
                      "table's rows -- or the table they were handed no longer exists")
        self.assertRegex(built, r"o\.csv \? tableTools\(t, o\.csv\) : null",
                         "every table gets the pair whether or not its call site asked "
                         "for one, which puts 112 buttons on the matches page")

    def test_a_chart_on_a_page_there_are_many_of_exports_under_whose_it_is(self):
        # A file name is read in a downloads folder, away from the page it came off.
        # `strength-by-set-slot.csv` there is a file about one of twelve teams and does
        # not say which, and the second one downloaded arrives as a copy of the first.
        for builder, whose in (("slotStrengthChart", "t.name"),
                               ("pointsByRoundChart", "t.name"),
                               ("disciplineChart", "p.name"),
                               ("opponentDiffChart", "p.name")):
            with self.subTest(chart=builder):
                self.assertRegex(self.body(builder), r"tableCsv: " + re.escape(whose),
                                 f"{builder}() exports under its own heading, which "
                                 f"every page of its kind shares, so the file does "
                                 f"not say whose figures are in it")
        # And the four charts there is only one of keep taking their heading, which is
        # what makes this hook worth having rather than a name written out eight times.
        for builder in ("hbarCard", "ladderLineCard"):
            with self.subTest(card=builder):
                self.assertIn("csv: opts.tableCsv || opts.title", self.body(builder),
                              f"{builder}() no longer falls back to the chart's own "
                              f"heading, so a chart that does not override the name "
                              f"exports under nothing")

    def test_what_leaves_the_page_is_read_off_the_page(self):
        rows = self.body("exportRows")
        for reads in ("querySelectorAll('thead th')", "querySelectorAll('tbody tr')"):
            with self.subTest(reads=reads):
                self.assertIn(reads, rows,
                              f"exportRows() does not {reads}: the file is built from "
                              f"something other than the table in front of the reader")
        # Nothing in the export path may reach for the season. A row rebuilt from
        # `S.players` is a row in the order the JSON happened to hold, not the order the
        # reader sorted the table into, and a column added to the page and not here is a
        # file that disagrees with the screen and says nothing about it.
        for function in ("cellText", "exportCell", "exportRows", "tableText",
                         "tableCsv", "tableTsv"):
            with self.subTest(function=function):
                self.assertNotRegex(self.body(function), r"SEASON_DATA|(?<![\w.])S\.",
                                    f"{function}() reads the season rather than the "
                                    f"table, so what leaves the page is a second copy "
                                    f"of it that can disagree with the first")

    def test_the_sort_arrow_does_not_leave_with_the_heading_it_decorates(self):
        cell = self.body("cellText")
        self.assertIn("class: 'arrow'", self.assets()["app.js"],
                      "the sort arrow is no longer a span of its own, so the cleaning "
                      "below is looking for something that is not there")
        self.assertIn("querySelectorAll('.arrow')", cell,
                      "the arrow leaves with the heading, so the column arrives called "
                      "'Player▲' -- which nothing in a spreadsheet can look up")
        # On a clone, because the alternative is to take the arrow off the page: the
        # cell handed in here is the one the reader is looking at.
        self.assertIn("cell.cloneNode(true)", cell,
                      "the cell itself is cleaned rather than a copy of it, so "
                      "exporting a table strips the sort arrow off the screen")

    def test_a_cell_a_spreadsheet_would_run_as_a_formula_arrives_as_text(self):
        # Measured: `=SUM(1+1)` pasted into LibreOffice arrives as the number 2 with
        # the formula still attached. Player names come off somebody's scorecard and
        # team names out of a config file, so a cell that reads as a formula is a cell
        # somebody else wrote.
        cell = self.body("exportCell")
        self.assertIn(r"/^[=+\-@]/.test(out)", cell,
                      "nothing checks for a cell that opens the way a formula opens, "
                      "so a spreadsheet runs what a club typed into a config file")
        self.assertIn(r"!/^[+\-]?[0-9]/.test(out)", cell,
                      "the check has no exemption for a number, so `+80` and `-66` "
                      "are quoted out of being numbers -- and a games differential "
                      "column that will not sum is the column this feature is for")
        self.assertIn("out = ' ' + out", cell,
                      "the guard is something other than a leading space; measured, a "
                      "space leaves `=SUM(1+1)` text and leaves `+80` a number, while "
                      "the apostrophe the same advice usually recommends ends up "
                      "inside the cell, visible, part of the name")

    def test_the_two_characters_this_page_prints_that_a_spreadsheet_misreads(self):
        app = self.assets()["app.js"]
        placeholder = re.search(r"var NO_VALUE = '(.+?)';", app)
        self.assertIsNotNone(placeholder, "app.js no longer names the character it "
                                          "prints where there is nothing to print")
        self.assertEqual(placeholder.group(1), "—",
                         "NO_VALUE is not the em dash the tables print, so the cells "
                         "that hold it go out as that character and a spreadsheet "
                         "counts them as text among the numbers")
        cell = self.body("exportCell")
        self.assertRegex(cell, r"=== NO_VALUE\)\s*\{?\s*return ''",
                      "the em dash leaves as itself: 41 cells of a twelve-team season "
                      "arrive as text in columns of numbers, and an average over the "
                      "column is then an average over nothing")
        self.assertIn("replace(/−/g, '-')", cell,
                      "the minus sign the page draws is U+2212, which was measured "
                      "arriving as text; 86 cells of a twelve-team season are negative "
                      "differentials, and text is not something to sort or total")

    def test_the_clipboard_gets_tabs_and_the_file_gets_commas(self):
        self.assertIn("tableText(t, '\\t')", self.body("tableTsv"),
                      "the clipboard is sent something other than tabs, which is what "
                      "a spreadsheet splits a paste on -- 14 columns arrive in one")
        self.assertIn("tableText(t, ',')", self.body("tableCsv"),
                      "the file is not comma separated, whatever its name says")
        self.assertIn("'\\ufeff' +", self.body("tableCsv"),
                      "the CSV has lost its byte-order mark: LibreOffice was measured "
                      "ignoring it and it is what Excel reads UTF-8 by, so without it "
                      "a name with an accent in it arrives broken")
        text = self.body("tableText")
        self.assertIn("if (!csv) return out", text,
                      "the clipboard text is quoted like a file, so a paste of "
                      "`\"Cordwainer, Bartholomew\"` puts the quote marks on screen")
        self.assertIn(r'/[",\r\n]/.test(out)', text,
                      "the file quotes on something other than the three characters "
                      "that need it, so a name with a comma in it becomes two columns")
        self.assertIn(r"""out.replace(/"/g, '""')""", text,
                      "a quote mark inside a quoted field is not doubled, so the field "
                      "ends early and the rest of the row shifts left")

    def test_the_file_is_named_after_the_competition_as_well_as_the_table(self):
        name = self.body("exportFileName")
        self.assertIn("meta().label", name,
                      "the file is named after the table alone, and `ladder.csv` in a "
                      "downloads folder is a file about nothing in particular")
        self.assertIn("slugifyName(", name,
                      "the name is not slugged, so a competition called "
                      "'Wednesday Division 3' becomes a file name with spaces in it")
        self.assertIn("|| 'table'", name,
                      "a name that slugs to nothing -- every character of it dropped "
                      "-- leaves the browser saving a file called '.csv'")
        self.assertIn("+ '.csv'", name, "the download has no extension, so the reader "
                                       "double-clicks it and nothing opens")

    def test_a_refused_clipboard_says_so_and_names_the_button_that_works(self):
        copy = self.body("copyText")
        # Three routes, in the order of how much each is allowed to do.
        # navigator.clipboard is defined on a file:// page -- measured, because a page
        # with no origin is exactly where a permission-gated API would be missing.
        self.assertIn("navigator.clipboard && navigator.clipboard.writeText", copy,
                      "the modern route is taken without checking it is there, and on "
                      "the browser that lacks it the button throws and does nothing")
        self.assertRegex(copy, r"writeText\(text\)\.then\([^,]*,\s*fallback\)",
                         "a refusal is not caught, so a browser that says no leaves "
                         "the reader with a button that looks broken")
        self.assertRegex(copy, r"try \{[^}]*execCommand\('copy'\)",
                         "the old route is called outside a try, and execCommand "
                         "throws rather than returning false in some browsers")
        refused = re.search(r"say\(done \? 'Copied' : '([^']*)'\)", copy)
        self.assertIsNotNone(refused, "nothing is said when the copy fails, which is a "
                                      "button that looks broken and a reader who "
                                      "pastes whatever was on the clipboard before")
        self.assertIn("CSV", refused.group(1),
                      f"the failure says {refused.group(1)!r} without pointing at the "
                      f"button beside it that needs no clipboard at all")

    def test_what_the_buttons_did_is_announced_and_then_taken_back(self):
        tools = self.body("tableTools")
        self.assertIn("role: 'status'", tools,
                      "what happened is put on the screen with nothing to announce "
                      "it, so a reader who cannot see the caption is not told")
        self.assertIn("'aria-live': 'polite'", tools,
                      "the announcement is not polite, so it interrupts whatever the "
                      "reader was having read to them")
        self.assertRegex(tools, r"said\.textContent = '';\s*\}, SAID_MS\)",
                         "'Copied' stays in the caption for the life of the page, so a "
                         "reader arriving at the table later is told about a copy "
                         "somebody else made")
        self.assertIn("if (timer) clearTimeout(timer)", tools,
                      "the timers stack, so the second copy's message is cleared by "
                      "the first copy's timer while it is still the newest thing said")

    def test_the_buttons_are_named_after_their_own_table(self):
        tools = self.body("tableTools")
        for word in ("text: 'Copy'", "text: 'CSV'"):
            with self.subTest(button=word):
                self.assertIn(word, tools,
                              f"the visible face is no longer {word}: the pair sits in "
                              f"a caption, and a caption has room for a word")
        # The name a screen reader reads is the table's, because a page with 21 tables
        # on it is a page with 21 buttons called Copy, and a list of those is no use to
        # anybody. Spelling it on the face instead was rejected: measured at 360px, the
        # caption of the players table is already two lines.
        signature = re.search(r"function tableTools\(\w+, (\w+)\)", tools)
        self.assertIsNotNone(signature, "tableTools() is no longer told which table it "
                                        "is for, so it has no name to announce")
        named = re.findall(r"'aria-label': '[^']*' \+ " + signature.group(1), tools)
        self.assertEqual(len(named), 2,
                         f"{len(named)} of the two buttons build their accessible name "
                         f"from the table's own name, so the rest are announced as the "
                         f"same two words as every other table's")

    def test_the_buttons_come_before_the_words_they_float_beside(self):
        # styles.css floats the pair into the caption, and a float is wrapped by the
        # text that follows it in the source: appended after the sentence, the buttons
        # would sit under the words instead of beside them, and the caption would be
        # two lines on every table.
        cap = self.body("tableCaption")
        # The parameter's own name, read off the signature, so that renaming it is a
        # rename and not a failure.
        signature = re.search(r"function tableCaption\(\w+, \w+, (\w+)\)", cap)
        self.assertIsNotNone(signature, "tableCaption() is no longer handed the pair "
                                        "of buttons, so no caption has them in it")
        given = signature.group(1)
        self.assertLess(
            self.offset_of(cap, f"cap.appendChild({given})", "the pair is never added"),
            self.offset_of(cap, "'caption-text'", "the caption has no sentence"),
            "the buttons are appended after the sentence they float beside, so they "
            "drop below it and cost a line on every table that has one")
        # A chart's values table has no caption sentence of its own and may have no key
        # either -- the two games differential charts head columns GF, GA and Diff.
        # Without the third term here that caption is not built at all and the table
        # cannot be exported.
        self.assertIn(f"!{given}", self.guard_for("tableCaption", "el('caption')",
                                                  emits="var cap", exits=True),
                      "a caption is built only where there is a sentence or a key, so "
                      "a chart's values table with neither loses its two buttons")

    def test_the_pair_floats_to_the_edge_of_the_screen_not_of_the_table(self):
        floats = [rule for rule in csslint.rules(self.assets()["styles.css"])
                  if ".table-tools" in rule.selector and rule.value("float")
                  and not rule.at_rule]
        self.assertTrue(floats,
                        "the pair no longer floats into the caption, so it takes a row "
                        "of its own above every table; floated, it was measured costing "
                        "no height at 320px, 700px or 1280px and 18px on one table of "
                        "twenty at 360px")
        for rule in floats:
            with self.subTest(where=rule.where):
                # This was measured the other way round first. A caption is as wide as
                # its table, and a table that overflows is as wide as its contents
                # inside a box that scrolls sideways -- so `float: right` pins the
                # buttons to the far edge of the table rather than of the screen. At
                # 320px that put them off screen on 18 of the 20 tables here, 16 at
                # 360px and 390px, and 3 even at 700px. Left: none, at any width.
                self.assertEqual(rule.value("float"), "left",
                                 "the pair floats right, which is the edge of the "
                                 "table and not the edge of the screen: on a phone "
                                 "that is 18 of 20 tables whose buttons are parked "
                                 "past the right edge of a box the reader has to "
                                 "scroll sideways to find them in")

    def test_the_copy_the_browser_is_holding_is_let_go_of(self):
        download = self.body("downloadText")
        self.assertIn("URL.revokeObjectURL(url)", download,
                      "the blob is never revoked, so every CSV a reader downloads is a "
                      "copy of that table held until the tab closes")
        # When, rather than whether, is deliberately not pinned: Chrome here was
        # measured downloading the file whether the URL was revoked in the same turn as
        # the click or ten seconds after it, so a test asserting a delay would be
        # asserting something this machine cannot show.
        self.assertIn("document.body.appendChild(a)", download,
                      "the link is clicked while detached from the document; measured, "
                      "that is enough for Chrome and was not always enough for Firefox")
        self.assertIn("document.body.removeChild(a)", download,
                      "the link is left in the page, so a reader tabbing through the "
                      "table finds an invisible stop with nothing to say for itself")


class TestAFormColumnLeavesThePageAsTheResultsItShows(WebAssetCase):
    """The ladder's Form column went into a spreadsheet as one word, `LLLDW`.

    On the screen the column is five badges, a gap between each and a hover on each
    saying Win, Loss or Draw. The export reads a cell's `textContent`, and five spans
    with no text between them come out as their letters run together. Measured through
    the buttons on a seven-team season with a bye every round, two drawn matches, a
    forfeit and a washout, Copy and CSV both gave `LLLDW` for the team that met all
    four: the bye and the washout absent, the forfeit a loss, the draw a D. So the
    letters are not ambiguous -- every result is one letter and nothing that was not
    decided has one -- but the cell is five results that cannot be taken apart again.
    LibreOffice's split on spaces made one column of `LLLDW` and five of `L L L D W`,
    and both arrived as text either way, which is what they are.

    So the run carries its results as data, a letter per result with a space between,
    and the export reads that where it reads the cell. Walking every exporting table on
    every route of both seasons (174 of 224 tables, then 202 of 270), the Form cells
    were the only cells that left the page as anything other than what the screen
    showed, and the only ones whose hover said what the text did not.

    Two ways round it were measured and rejected. `innerText` puts the layout's line
    breaks back and gave the letters a line apart on the live page -- and `LWWWW` on
    the copy the export reads, and `LWWWW` again with the table hidden, so it is an
    answer that depends on what the reader happens to have open. The words the hovers
    hold, `Loss Win Win Win Win`, were the other: a file that disagrees with the
    screen it came off, in a table whose own key already says what W, L and D are.
    """

    def test_the_run_carries_its_results_as_the_letters_on_the_badges(self):
        run = self.body("formRun")
        # From the season's own list rather than the badges, because the badges are
        # what is being read the wrong way; and separated, because the separation is
        # the whole of the defect.
        self.assertIn("'data-export': form.join(' ')", run,
                      "the run does not carry its results with a space between them, "
                      "so the ladder's Form column goes out as `LLLDW` -- five results "
                      "no spreadsheet can split back into five")
        # A team with nothing decided prints the dash every other empty cell prints,
        # and that leaves as an empty cell by the rule that already covers it.
        self.assertLess(
            self.offset_of(run, "if (!form || !form.length) return",
                           "a team with no decided match is not caught"),
            self.offset_of(run, "'data-export'", "the run carries no export value"),
            "the export value is joined before the run knows it has a form, and "
            "`form.join` on a ladder row with no form list throws -- taking the "
            "whole ladder with it rather than printing a dash")

    def test_the_export_reads_what_the_cell_says_it_is_before_reading_its_text(self):
        cell = self.body("cellText")
        found = self.offset_of(cell, "copy.querySelectorAll('[data-export]')",
                               "the export does not look for a part of the cell that "
                               "says what it is, so the Form column leaves as its "
                               "letters run together")
        read = self.offset_of(cell, "copy.textContent", "the cell's text is not read")
        self.assertLess(found, read,
                        "the text is read before the substitution, so the value is put "
                        "in after the only thing that would have taken it out")
        # The part's own value, read between finding the part and reading the text. How
        # the value takes the part's place is not pinned: replacing the node with a text
        # node and setting the node's text were measured leaving the same `L W W W W`.
        self.assertIn("getAttribute('data-export')", cell[found:read],
                      "the part is found and its own value is not what goes out, so the "
                      "cell leaves as nothing, or as something the part does not carry")
        # On the clone, for the reason the sort arrow is taken off the clone: the cell
        # handed in here is the one the reader is looking at, and replacing its badges
        # with a string would take the colours off the ladder the moment it was copied.
        self.assertNotIn("cell.querySelectorAll('[data-export]')", cell,
                         "the badges on the screen are replaced rather than those on "
                         "a copy, so pressing Copy strips the ladder's Form column")

    def test_the_value_goes_through_the_same_guard_as_every_other_cell(self):
        # One reader, in the one function every cell and heading goes through, so a
        # value a club's config put in a data attribute meets the formula guard and
        # the dash rule exactly as a value on the screen does.
        app = self.assets()["app.js"]
        readers = app.count("getAttribute('data-export')")
        self.assertEqual(readers, 1,
                         f"a cell's export value is read in {readers} places; one is "
                         f"the rule, none is a value nobody reads, and two are two "
                         f"answers to what a cell leaves as")
        for function in ("exportCell", "exportRows", "tableText"):
            with self.subTest(function=function):
                self.assertNotIn("data-export", self.body(function),
                                 f"{function}() reads the export value itself, past "
                                 f"or beside cellText(), so the one rule about what "
                                 f"a cell leaves as is two")
        self.assertIn("row.push(cellText(cell))", self.body("exportRows"),
                      "a body cell is read some other way than through cellText(), "
                      "so the export value never reaches the file")


class TestEveryRubberOfTheSeasonLeavesThePageInOneTable(WebAssetCase):
    """The matches page held every rubber of the season and no way to take one off it.

    4.1 gave every table a Copy and a CSV button except the set rows inside a match
    card, and said the list of cards exported the season. It did not: measured on the
    demo season the matches page is 15 tables, none of them exporting, holding 60 set
    rows; on a seven-team season, 21 tables, none exporting, 84 rows. A captain wanting
    who played which rubber all season had fifteen cards to open and retype.

    So one table of every rubber, built from the same walk over the rounds that builds
    the cards -- the rows are the cards on the page and in their order -- and kept off
    the screen until asked for, because it more than doubles the page: measured at
    1280px, the demo season's matches page is 1585px tall with it shut and 3860px open.
    Each row is the row the card prints, through `setRow()`, with the night it belongs
    to in front of it and two things after it that a row loses when it leaves its card.
    Who won, because the card says it with a weight and a colour and a file keeps
    neither: read from the season, which has the answer for every set. Measured, a
    games comparison disagrees with it once in each season, on a 5-4 rubber that was
    retired and went to nobody; the other case the games cannot see, a rubber nobody
    came for that is 0-0 and was won, is pinned where the season is written, in
    test_pipeline. And what became of the night, because a washout's rubbers were won
    and count for nothing, and on the card only the sentence above them says so.

    The date leaves as a date. LibreOffice read `Wed 19 Aug 2026` and `19 Aug 2026` as
    text and `2026-08-19` as a date, and a column of text sorts Fri before Wed. Printing
    the ISO form on the screen was rejected: every other date on the page is written
    the long way.

    And the file is named for the season as well as the competition. Measured on an
    archive of five seasons of one division, every season's ladder downloaded as
    `wednesday-division-3-ladder.csv`: the label and the season's label were the same
    in all five, and only the season's key told them apart.

    Two cards that claim one night and disagree about it are counted nowhere, and
    this table read `Played` on their rows, as each card's own summary was silent.
    The card was where that was owed first, and the Night column now says what the
    summary says, from the same function: see
    `TestANightTwoCardsDisagreeAboutSaysSoOnEveryCardOfIt`.
    """

    def test_the_matches_page_has_one_table_of_every_rubber_to_take_away(self):
        tables = [args for args in self.calls_of("table") if "csv: 'all rubbers'" in args]
        self.assertEqual(len(tables), 1,
                         f"{len(tables)} tables export as 'all rubbers'; the matches "
                         f"page is meant to have one, and a second is a second file "
                         f"of the same name")
        self.assertIn("csv: 'all rubbers'", self.body("rubbersCard"),
                      "the table of every rubber is built somewhere other than the "
                      "card that holds it")
        self.assertIn("rubbersCard(shown)", self.body("renderMatches"),
                      "the matches page does not build the table of every rubber, "
                      "so 60 set rows are on it and none can leave it")

    def test_its_rows_are_the_cards_the_page_shows_in_the_order_it_shows_them(self):
        body = self.body("renderMatches")
        # Every card the page appends is a night the table holds, and nothing else is:
        # a table built from `S.matches` instead is a list in whatever order the file
        # holds and with whichever cards the page chose not to show.
        cards = body.count("box.appendChild(matchCard(")
        self.assertGreater(cards, 0, "the matches page appends no match cards")
        self.assertEqual(body.count("shown.push("), cards,
                         f"the page appends {cards} kinds of card and remembers "
                         f"{body.count('shown.push(')} of them, so the table leaves "
                         f"out nights the page shows")
        self.assertLess(
            body.rfind("shown.push("),
            self.offset_of(body, "rubbersCard(shown)", "the table is not built"),
            "the table is built before the walk has finished, so it holds some of "
            "the rounds and not the rest")
        # Directly after the heading, and not merely somewhere other than the foot:
        # measured, `insertBefore(every, head)` puts the button above the page's own
        # title.
        self.assertIn("main.insertBefore(every, head.nextSibling)", body,
                      "the table is not put directly under the heading where a reader "
                      "arrives -- at the foot of a page as long as the season, or above "
                      "the title of the page it belongs to")
        # And in that order in the file. Between being built and being handed to the
        # table the rows are only added to and counted, because the caption says they
        # are in the order they are listed: measured, a `rows.reverse()` there opens
        # the file on the last night of the season.
        used = sorted(set(re.findall(r"\brows\.(\w+)", self.body("rubbersCard"))))
        self.assertEqual(used, ["length", "push"],
                         f"the rows are put through {used} on their way to the table, "
                         f"so the file is not in the order of the cards it lists")

    def test_each_row_is_the_row_the_card_prints_with_its_night_in_front(self):
        row = self.body("rubberRow")
        self.assertIn(".concat(setRow(m, st))", row,
                      "a row of the season's table is built some other way than the "
                      "card builds its own, so the file and the card can disagree "
                      "about a rubber's players, games or status")
        # In front of the card's row and in the order the headings name them. The
        # winner's column names both teams as well, so a row that dropped its Home or
        # Away cell would still mention both -- and every column after it would sit
        # under the wrong heading.
        front = row[:row.find(".concat(setRow(m, st))")]
        night = ("longDate(m.date)", "m.round", "m.id", "teamName(m.home)",
                 "teamName(m.away)")
        at = [front.find(reads) for reads in night]
        for reads, where in zip(night, at):
            with self.subTest(reads=reads):
                self.assertGreaterEqual(where, 0,
                                        f"a row does not say {reads} before the "
                                        f"card's own cells, so out of its card it "
                                        f"is a set score nobody can place")
        self.assertEqual(at, sorted(at),
                         "a row gives its night in another order than the headings "
                         "Date, Round, Match id, Home, Away, so the file's columns "
                         "are under each other's names")
        cards = self.body("rubbersCard")
        self.assertIn("rows.push(rubberRow(m, st))", cards,
                      "the rows are built without the one function that builds them")
        # Every set of every card the page shows, and nothing asked of the card first:
        # a washout's rubbers are the rows the Night column exists to label. Measured,
        # an `if (!countsAsPlayed(m)) return;` here leaves the demo season's button
        # reading `All 56 rubbers` and the washout out of a file that says it is all
        # of them.
        walk = re.search(r"matches\.forEach\(function \(m\) \{(.*?)\n  \}\);", cards,
                         re.S)
        self.assertIsNotNone(walk, "the table is not built by a walk over the cards")
        self.assertEqual(
            walk.group(1).strip(),
            "(m.sets || []).forEach(function (st) { rows.push(rubberRow(m, st)); });",
            "the walk over the cards does something besides taking every set of each, "
            "so some cards' rubbers are not in the table of every rubber")

    def test_who_won_a_rubber_is_the_seasons_answer_and_not_the_games(self):
        row = self.body("rubberRow")
        # Both sides, each to its own team, and nobody for the rest: a winner written
        # as the other side's name passes any check that only asks the column is
        # filled in, and the third arm is the one the retirement lands in. Measured,
        # `teamName(m.away)` in place of that `null` gives 4200009's retired 5-4
        # rubber to the side that was behind, and both of the washout's unfinished
        # ones to the Rosellas.
        self.assertIn("st.winner === 'home' ? teamName(m.home) "
                      ": st.winner === 'away' ? teamName(m.away) : null",
                      re.sub(r"\s+", " ", row),
                      "a rubber is not written as the home team's when the season gives "
                      "it the home side, the away team's when it gives it the away "
                      "side, and nobody's when it gives it to neither")
        # Decided in Python, which has the forfeit ticks and the retirements: 5-4
        # retired goes to nobody, and a 0-0 rubber nobody came for goes to somebody.
        for games in ("home_games", "away_games"):
            with self.subTest(reads=games):
                self.assertNotIn(games, row,
                                 f"the row reads {games} for itself, so who won is "
                                 f"worked out on the page from the games -- which "
                                 f"gives a retired rubber to whoever was ahead")

    def test_a_night_that_counts_for_nothing_says_so_on_every_row_of_it(self):
        row = self.body("rubberRow")
        # Asked as the card asks it, in the card's words, and the right way round.
        # Measured, the two arms swapped put `Played — counts for nothing` on the 52
        # rubbers of the season's ordinary nights, and a bare `Cancelled` on the
        # washout's. The words are `''` for a night that counts, so the status is
        # what is left; `m.status || ...` in front is never anything else.
        self.assertIn("upperFirst(uncountedWords(m) || m.status)",
                      re.sub(r"\s+", " ", row),
                      "a row does not say what its night was when it happened and "
                      "the card's words for a night that counts for nothing when it "
                      "did not, so a washout's two won rubbers leave the page as two "
                      "ordinary results -- or every ordinary one as counting for "
                      "nothing")
        # One spelling, shared with the card's own summary.
        self.assertIn("uncountedWords(m)", self.body("matchCard"),
                      "the card's summary spells a night that counts for nothing "
                      "for itself, so the page has two wordings of one fact")

    def test_the_date_leaves_as_a_date_and_is_shown_as_every_other_date_is(self):
        row = self.body("rubberRow")
        self.assertIn("text: longDate(m.date)", row,
                      "the date is shown some other way than every other date on "
                      "the page")
        # The whole ISO date and nothing done to it: measured, `m.date.slice(0, 7)`
        # leaves every row of the demo season as `2026-02` or `2026-03`.
        self.assertIn("'data-export': m.date })", row,
                      "the date leaves as it is shown, which LibreOffice was "
                      "measured reading as text -- a column that sorts Fri before "
                      "Wed and filters on nothing -- or as some part of the date")

    def test_a_file_has_columns_that_can_be_told_apart_out_of_their_table(self):
        cards = self.body("rubbersCard")
        # The card's own set table heads both games columns `G` and says which side
        # is which by where they sit beside the team names. A file has no beside.
        self.assertNotIn("label: 'G'", cards,
                         "two columns of the file are called G, and in a "
                         "spreadsheet nothing says which is whose")
        # Every heading, in the order the row fills them: the night, then the card's
        # own row as `setRow` builds it, then the two a row loses off its card.
        heads = ("'Date'", "'Round'", "'Match id'", "'Home'", "'Away'", "'Set'",
                 "'Home players'", "'Home games'", "'Away games'", "'Away players'",
                 "'Status'", "'Winner'", "'Night'")
        at = [cards.find(heading) for heading in heads]
        for heading, where in zip(heads, at):
            with self.subTest(heading=heading):
                self.assertGreaterEqual(where, 0,
                                        f"the table has no {heading} column")
        self.assertEqual(at, sorted(at),
                         "the headings are in another order than the row fills them, "
                         "so a column of the file is named for its neighbour")

    def test_the_table_waits_to_be_asked_for_and_is_measured_when_it_is(self):
        cards = self.body("rubbersCard")
        self.assertIn("hidden: true", cards,
                      "a table as long as every card on the page together is on the "
                      "screen from the start, above the cards it repeats")
        # The card is what is hidden and the wrapper inside it is what scrolls, and it
        # is the wrapper that has to be measured once it is on the screen. Measured on
        # the demo season at 1280px: 1968px of table in 1191px of wrapper, a tab stop
        # when the button re-marks the wrapper and none when it re-marks the card.
        self.assertIn("tableToggle(box, wrap,", cards,
                      "the table is shown by something other than the one button "
                      "that re-measures what it shows, or that button is handed the "
                      "card to measure rather than the wrapper that scrolls")
        self.assertIn("markScrollRegion(wrap)", self.body("tableToggle"),
                      "the button re-measures the box it shows rather than the "
                      "wrapper inside it, so a table wider than the screen opens "
                      "with no tab stop and cannot be scrolled from the keyboard")
        self.assertIn("tableToggle(", self.body("chartCard"),
                      "a chart's values are shown by a button of their own, so the "
                      "page has two ways of putting a hidden table on the screen")
        self.assertIn("if (!rows.length) return null", cards,
                      "a season with no rubbers yet is offered a table of none")

    def test_the_button_says_whether_the_table_is_open(self):
        # Measured on the demo season: `All 60 rubbers in one table` and
        # aria-expanded false, then `Hide the table of every rubber` and true, then
        # back. The chart's Values button said it the same way before it shared this.
        toggle = self.body("tableToggle")
        self.assertIn("box.hidden = !show", toggle,
                      "the button does not turn the table the other way from how it "
                      "found it")
        self.assertIn("toggle.setAttribute('aria-expanded', show ? 'true' : 'false')",
                      toggle,
                      "a screen reader is told the table is open or shut whichever "
                      "it is")
        self.assertIn("toggle.textContent = show ? open : shut", toggle,
                      "the button keeps one set of words whichever way it has "
                      "turned the table")

    def test_a_season_of_an_archive_exports_under_its_own_name(self):
        self.assertIn("[meta().label, seasonKey(), name]", self.body("exportFileName"),
                      "the file is named for the competition and the table and not "
                      "the season, so five seasons' ladders arrive under one name")
        key = self.body("seasonKey")
        # rootHref asked the mode as well as the key before it asked seasonKey(), and
        # the rule moved with it: only an archive's front page is two folders up.
        # Measured, a build of one season writes the key as '' even when its section
        # declares one, so this is the rule kept rather than a case the demo shows.
        self.assertIn("m.seasons_mode === 'multi'", key,
                      "the rule rootHref had, that only an archive's front page is "
                      "two folders up, was lost on its way into seasonKey()")
        self.assertIn("m.season_key", key, "the season is named by something other "
                                           "than its key")
        # Not the season's label: measured, it was the same in all five seasons of
        # the archive, and it is what the key is un-slugged into when none is declared.
        self.assertNotIn("season_label", key,
                         "the season is named by its label, which five seasons of "
                         "one archive were measured sharing")
        root = self.body("rootHref")
        self.assertIn("seasonKey()", root,
                      "the way back to the front page decides for itself which "
                      "season of an archive this is, so there are two answers")
        self.assertNotIn("m.season_key", root,
                         "the way back to the front page reads the season key itself "
                         "rather than asking seasonKey()")


class TestANightTwoCardsDisagreeAboutSaysSoOnEveryCardOfIt(WebAssetCase):
    """Two cards, one night, two results, and a page that showed the night as played.

    ``season.json`` marks both cards ``not_counted: conflict``, and each keeps
    ``status: played``, a result and a winner on every rubber, because each card on
    its own was played. ``countsAsPlayed`` asks the status, so to the page they were
    two ordinary nights. Measured on the demo season with a second round 2 Brindle
    Wombats v Brindle Bunyips card whose last rubber reads 6-2 for 6-0
    (``/tmp/t4/scratch/y436/measure``, headless Chrome at 1280px):

    * **the same match id**, as a re-export gives it: ``#/matches`` showed one card,
      whichever was read last, reading ``sets 3–1 · points 6.3 / 3.5`` and nothing
      else. Its four rubbers read ``Played`` in the table of every rubber, with
      winners. The other reading was on no page but the one for its match id.
    * **a different id**: both cards, 16 where the demo has 15, both as silent, and 64
      rubbers in the table where the season has 60, eight of them one night's and
      all ``Played``.

    So the words for a night in no figure anywhere now know two kinds of night: the
    one that did not happen, which is a status, and the one two cards disagree about,
    which is the exporter's ``not_counted``. The card's summary says it first and
    the table's Night column says it in the same words, from the same function, with
    both reasons on a card that has both. The opened card says it as a warning,
    because somebody has a file to remove, and names the other files from
    ``other_cards``. And the matches page shows every card of such a night together,
    in the order the build read them, which is the order the finding and the data
    health section list them, however the fixture comes to find one of them.

    Rejected: one card, naming the other file. Which card is the page's to show is
    which reading is right, the guess the build refuses. The page would print one
    score as the night's, and take the other reading's rubbers out of a table that
    says it holds every rubber on the cards. Not demoted as a washout is, either:
    each played card of such a night has its own winners, so its reading keeps its
    weight and the words carry the rest.
    """

    def test_a_clash_is_one_of_the_nights_that_count_for_nothing(self):
        words = re.sub(r"\s+", " ",
                       jslint.without_comments(self.body("uncountedWords")))
        condition = self.guard_for("uncountedWords", "cards disagree",
                                   emits="why.push")
        # The repeats' own field, which says `conflict` of exactly the cards
        # `not_counted` does and is the one the copies' words are asked of too.
        self.assertEqual(condition.strip(), "m.not_totalled === 'conflict'",
                         "the words for a clash are not given exactly when the "
                         "exporter says two cards disagree about the night")
        # One ending for every reason the night counts for nothing, so a clash is
        # called what a washout is called. And nothing for a night that is in the
        # figures, which is how both callers know there is nothing to say. The other
        # way round is every played night of the season reading "played — counts
        # for nothing".
        self.assertIn("return why.length ? why.join(', ') + ' — counts for "
                      "nothing' : '';", words,
                      "a clash is called something other than what a washout is "
                      "called, or a night that counted is given words")

    def test_a_washout_card_of_such_a_night_still_says_it_was_called_off(self):
        """One card of a clash can be the washout, and it is both kinds of night.

        Measured with the second round 2 card cancelled after two sets and an id of
        its own: before, it read ``cancelled — counts for nothing``, and the other
        card nothing. With the clash's words in place of its status, both cards read
        ``cards disagree — counts for nothing``, and ``cancelled`` was nowhere on the
        card or on its rows in the table of every rubber, so nothing on the page said
        which card of the night was the washout. Now it reads ``cancelled, cards
        disagree — counts for nothing``, and its rows ``Cancelled, cards disagree —
        counts for nothing``.

        Rejected: the clash's reason alone, as the one that decides whether anything
        counts. The status is the card's own account of the night, and the rubbers'
        Status column says it only of the rubbers that were not played.
        """
        words = re.sub(r"\s+", " ",
                       jslint.without_comments(self.body("uncountedWords")))
        self.assertIn("var why = countsAsPlayed(m) ? [] : [m.status];", words,
                      "the status is left out of the words on a card of a clash, or "
                      "put into them on a night that counted")
        # Added to the status, after it, as the washout's words have always begun.
        self.assertIn("why.push('cards disagree');", words,
                      "the clash's words replace the status, or go in front of it")

    def test_the_cards_summary_says_it_before_the_figures(self):
        body = self.body("matchCard")
        code = jslint.code_only(body)
        self.assertIn("var uncounted = uncountedWords(m);", code,
                      "the summary does not ask the one function what the night "
                      "counts for")
        # Guarded on the words and not on `unscored`, which is the status's answer
        # alone and false on both cards of a clash.
        self.assertIn("if (uncounted) bits.push(uncounted);", body,
                      "the summary says the night counts for nothing only when a "
                      "status says so, so a clash is silent")
        self.assertLess(self.offset_of(body, "if (uncounted) bits.push(uncounted);",
                                       "the summary says nothing about the night"),
                        self.offset_of(body, "bits.push('sets ",
                                       "the summary has stopped saying sets"),
                        "the clash is said after the sets and points it makes "
                        "worthless, which is after they have been believed")

    def warning(self):
        body = self.body("matchCard")
        warning = re.search(r"note\('warning', 'Not counted',(.+?)\)\);\n", body, re.S)
        self.assertIsNotNone(warning, "a clash is not a warning, so nobody is told "
                                      "there is a file to remove")
        return warning.group(1)

    def test_the_opened_card_names_the_other_files_and_what_to_do(self):
        condition = self.guard_for("matchCard", "'Not counted'")
        self.assertEqual(condition.strip(), "m.not_counted === 'conflict'",
                         "the warning is not given exactly on a card two cards "
                         "disagree about")
        body = self.body("matchCard")
        text = self.warning()
        self.assertIn("var others = otherCards(m).map(function (x) { return "
                      "x.source; });", jslint.code_only(body),
                      "the files are not the ones the exporter names")
        # In the sentence every note about another card says them in, which is
        # where a document naming none is told nothing rather than that they are
        # nothing (TestAnOpenedCardOfARepeatedNightNamesTheOtherCardsOfIt).
        self.assertIn(" othersSaid +", text,
                      "the card says another card disagrees and not which file it is")
        self.assertIn("has left the data folder", text,
                      "the card does not say what brings the night back")
        # Under the table, like the washout's sentence: it is a note on the rubbers,
        # not a heading over them.
        self.assertLess(self.offset_of(body, "table-wrap", "the card has no set table"),
                        self.offset_of(body, "'Not counted'", "no warning"))

    def test_three_cards_of_one_night_are_not_spoken_of_as_two(self):
        """The warning as it read for three cards that all disagreed.

        Measured with a ``(1).pdf`` reading 6-2 and a ``(2).pdf`` reading 6-3 beside
        the original's 6-0, each with an id of its own: it said ``none of them is
        counted`` and ``The other cards are`` with both files, and ended ``The night
        is counted once the card that is wrong leaves the data folder``. Taking one
        file out leaves two cards that still disagree, and the night still counted
        nowhere, so the ending is worded for any number of wrong cards.

        Rejected: a sentence for each count. ``plural`` already turns on the number
        of other cards, and a third reading is the same fact as a second.
        """
        text = re.sub(r"\s+", " ", self.warning())
        self.assertIn("plural(others.length, 'neither', 'none of them')", text,
                      "three cards are told that neither is counted, or two that "
                      "none of them is")
        self.assertIn("plural(others.length, 'The other card is ', "
                      "'The other cards are ')",
                      re.sub(r"\s+", " ",
                             jslint.without_comments(self.body("matchCard"))),
                      "two files are named as though they were one, or one as though "
                      "it were two")
        self.assertIn("once every card that is wrong has left", text,
                      "three cards that give three readings are told that taking "
                      "one out brings the night back")

    def test_the_matches_page_shows_every_card_of_such_a_night_at_its_fixture(self):
        walk = jslint.code_only(self.body("renderMatches"))
        # Every card of the night drawn and counted into `shown`, the card each time
        # and not one an id found: drawn as that one it is the one card twice, and the
        # other reading's rubbers are the table's without their card on the page. The
        # fixture's own list of its cards, in the order the build read them, which the
        # exporter writes (test_pipeline.TestEveryCardIsFoundByAKeyNoOtherCardHas).
        self.assertIn("var cards = cardsOf(f.keys);", walk,
                      "a fixture shows the one card its match id finds, which for "
                      "a re-exported clash is whichever was read last")
        self.assertRegex(walk, r"cards\.forEach\(function \((\w+)\) \{\s*"
                               r"box\.appendChild\(matchCard\(\1\)\);\s*"
                               r"shown\.push\(\1\);",
                         "a fixture does not draw each of its cards, and count it")

    def test_the_other_cards_are_found_by_their_places_in_the_season(self):
        """``other_cards`` gives places in ``matches``, and one function reads them.

        A withheld build withholds a file name holding a word of a player's name
        whole, so every such card's ``source`` reads the same. Measured before on
        the review's night filed under its players (``/tmp/t4/scratch/f1/p4``),
        headless Chrome on ``#/matches``: with names 3 cards and ``All 12 rubbers
        in one table``, and ``--players numbered`` 4 cards and 16 rubbers, the
        round 2 card beside round 1's two, whose notes read ``The other card is
        (withheld: it named a player).`` Rejected: numbering the withheld sources,
        which tells two files apart and not which card is which; joining on the
        match id, which a re-export shares and a card may not print.
        """
        found = jslint.code_only(self.body("otherCards"))
        self.assertIn("var all = S.matches || [];", found,
                      "the places are read in something other than the season's "
                      "own list of cards")
        self.assertIn("return (m.other_cards || []).map(function (at) "
                      "{ return all[at]; })", found,
                      "the other cards are not the cards at the places given")
        # A place the list does not hold is nobody's, rather than a hole the note
        # prints as `undefined`.
        self.assertIn(".filter(function (x) { return x !== undefined; });", found)
        for name in ("matchCard",):
            with self.subTest(function=name):
                code = jslint.code_only(self.body(name))
                self.assertIn("otherCards(m)", code)
                self.assertNotIn("other_cards", code,
                                 f"{name} reads the field itself, a second place "
                                 "the rule is written down")


class TestEveryCardOnThePageIsFoundByAKeyNoOtherCardHas(WebAssetCase):
    """The page found a card by the match id it printed, one card per id.

    ``IDX.matchById`` held the last card written under each id, a fixture looked up
    the id of its first card, the front page's Latest round the ids of the round's
    cards that had one, and ``#/matches`` walked the round's ids again for a card no
    fixture's id found. Measured on the demo season, headless Chrome at 1280 and
    360px, the same at both (``/tmp/t6/scratch/m1/probe``):

    * the round 2 Brindle Wombats v Brindle Bunyips card printing no id: 14 cards
      and 56 rubber rows on ``#/matches`` where the demo has 15 and 60, the fixture
      reading ``scheduled · Wed 11 Feb 2026``, and the build ``all 15 card(s)
      scored``. A second card for it, neither printing one: 14 cards again, the
      clash on no part of the page;
    * a second card for a round 5 fixture under the first one's id, with another
      score: the front page showed the ``.pdf`` card twice, both naming that file,
      and the ``(1).pdf`` card nowhere. A copy under the same id: the copy twice
      there and once on ``#/matches``, the card the ladder counted on neither;
    * a round 2 card printing another fixture's id: that fixture's card shown twice,
      once under the other fixture, and the card itself nowhere.

    So each card is found by ``key``, which the exporter makes and no other card
    has, through the lists of keys the fixture and the round carry, by one function.
    The printed id stays the address of a night's own page. A document written before
    the key has none, and the page finds no card in it: measured, a format 2
    ``season.json`` from the tree before this beside this page, 0 cards on
    ``#/matches`` and 15 fixtures reading ``scheduled`` under rounds reading
    ``played``, and nothing on the page saying why, since both said format 2. So the
    key is format 3, as ``counts_as_played`` was format 2, and that page says to
    rebuild. Rejected: falling back to the match id where a key is missing, a second
    rule for finding a card, kept for a pair only a failed write leaves.
    """

    # The format every season.json written before the cards carried a key says.
    FORMAT_BEFORE_THE_KEY = 2

    def test_the_index_holds_each_card_by_its_key_and_none_by_its_id(self):
        self.assertIn("idx.matchByKey[m.key] = m;",
                      jslint.code_only(self.body("indexSeason")),
                      "the page's index of cards is not by the key each card has")
        self.assertNotRegex(jslint.code_only(self.assets()["app.js"]),
                            r"\bmatchById\b", "the page keeps an index by printed id")
        # Every reader moved, not only the index: a page that still asks for a card
        # by the id a fixture or a round lists finds none for a card without one.
        # Asked of the two walks by name, so `player.matches` and the like are not
        # taken for a round's list of ids.
        for fn, gone in (("renderMatches", r"\bf\.match_id\b"),
                         ("renderMatches", r"\br\.matches\b"),
                         ("renderOverview", r"\blastRound\.matches\b")):
            with self.subTest(function=fn, reads=gone):
                self.assertNotRegex(jslint.code_only(self.body(fn)), gone,
                                    f"{fn} still finds a card by a printed id, "
                                    "which a card may not have and two may share")

    def test_a_document_built_before_the_key_is_one_the_page_says_to_rebuild(self):
        # As for `counts_as_played`: the page can tell an older document only by its
        # number, so both sides move past the one those documents carry, together
        # (TestThePageAndTheExporterAgreeOnTheDocumentFormat). Left where they were,
        # the older document renders its played nights as `scheduled`, with nothing
        # on the page to say why.
        from matchcentre import export
        declared = TestThePageAndTheExporterAgreeOnTheDocumentFormat.declared(self)
        with self.subTest(side="app.js"):
            self.assertGreater(declared, self.FORMAT_BEFORE_THE_KEY,
                               "the page reads a document without keys as current")
        with self.subTest(side="export.py"):
            self.assertGreater(export.SEASON_FORMAT, self.FORMAT_BEFORE_THE_KEY,
                               "a document with keys says the format of one "
                               "without them")

    def test_one_function_turns_a_list_of_keys_into_its_cards(self):
        found = re.sub(r"\s+", " ", jslint.code_only(self.body("cardsOf")))
        self.assertIn("return (keys || []).map(function (key) { return "
                      "IDX.matchByKey[key]; })", found,
                      "the cards are not the ones the keys name, in the keys' order")
        # A key the season does not hold is nobody, rather than a card drawn from
        # `undefined`, which throws and leaves the rest of the round unwritten.
        self.assertIn(".filter(function (x) { return x !== undefined; });", found)

    def test_the_latest_round_shows_every_card_the_round_lists(self):
        overview = jslint.code_only(self.body("renderOverview"))
        self.assertRegex(overview,
                         r"cardsOf\(lastRound\.keys\)\.forEach\(function \((\w+)\) "
                         r"\{\s*box\.appendChild\(matchCard\(\1\)\);",
                         "the front page's latest round does not show each card of "
                         "the round by its own key")

    def test_a_fixture_with_no_card_is_the_one_drawn_as_scheduled(self):
        # Asked of the cards the fixture lists, so a played card that printed no id
        # is drawn as itself and not as `scheduled` above nothing.
        condition = self.guard_for("renderMatches", "scheduledRow(f)")
        self.assertEqual(condition.strip(), "!cards.length",
                         "a fixture is drawn as scheduled for something other than "
                         "having no card")

    def test_the_fixtures_are_the_one_walk_of_the_cards(self):
        """The round's second walk, for a card no fixture's id found, is gone.

        The exporter lists every card under exactly one fixture, by key
        (test_pipeline.TestEveryCardIsFoundByAKeyNoOtherCardHas), so a second walk
        finds nothing the first did not, or shows a card twice. Measured before, the
        second walk was how a clash whose first card printed no id reached the page
        at all, one card of it; and the one card twice where a card printed another
        fixture's id. Rejected: keeping it as a net, which is a second rule for which
        cards a round has, beside the exporter's.
        """
        walk = jslint.code_only(self.body("renderMatches"))
        self.assertEqual(walk.count("matchCard("), 1,
                         "the matches page draws cards from more than one walk")
        self.assertNotIn("shown.indexOf", walk,
                         "the page skips a card it has shown, so a walk that shows "
                         "some twice is still there")


class TestACopyOfANightSaysSoOnItsCardAndOnEveryRowOfIt(WebAssetCase):
    """A night on two cards is counted from one, and the other read as a result.

    The exporter's ``not_totalled`` is ``duplicate`` on a card that is the same night
    exported again, ``names`` on the second of two that agree about the score and not
    about who played, and ``conflict`` on every card of a night they disagree about.
    The page gave words to the third alone. Measured on the demo season with the
    round 5 Brindle Bunyips v Brindle Kookaburras card re-exported under id 42000149
    (``/tmp/t6/scratch/m1/probe``, headless Chrome at 1280 and 360px): exit 0, 16
    cards on ``#/matches``, both summaries ``sets 4–0 · points 6.4 / 3.4``, and 64
    rows in the table of every rubber, 56 of them ``Played``, where the demo has 60
    and 52: the copy's four read ``Played`` beside a night the season counts once.

    So the words for a night in no figure say which card is the copy, from the same
    field, ``Copy of another card — counts for nothing`` on the card and on each of
    its rows, and ``Reading not used`` for the card of a night named two ways that was
    not used. Words that claim no order, because the card kept is the one read first
    and ``Rockets-Aces (1).pdf`` reads before ``Rockets-Aces.pdf``: ``second copy``
    was said of the file the club exported first, beside a ``Source:`` line naming it.
    Rejected: ``not_counted`` or ``not_credited``, each of which also holds a card
    whose rubbers are in the totals: a level night, a final, and the kept card of a
    night named two ways, whose rows would read as counting for nothing; and hiding
    the copy, which is a card the table says it holds every rubber of, and which
    the page had shown in place of the card the ladder counted.
    """

    def test_each_reason_a_card_is_in_no_total_has_words(self):
        from matchcentre import stats
        words = jslint.without_comments(self.body("uncountedWords"))
        said = dict(re.findall(r"if \(m\.not_totalled === '(\w+)'\) "
                               r"why\.push\('([^']+)'\);", words))
        # Every answer `stats.not_totalled_reasons` gives but "", each with words of
        # its own.
        self.assertEqual(set(said), {stats.NOT_COUNTED_CONFLICT,
                                     stats.NOT_COUNTED_DUPLICATE,
                                     stats.NOT_COUNTED_NAMES},
                         "a card the season's totals leave out reads as a result")
        self.assertEqual(len(set(said.values())), len(said),
                         "two reasons are given the same words")
        self.assertEqual(said[stats.NOT_COUNTED_DUPLICATE], "copy of another card")
        self.assertEqual(said[stats.NOT_COUNTED_NAMES], "reading not used")

    def test_the_words_are_asked_of_the_repeats_alone(self):
        words = jslint.code_only(self.body("uncountedWords"))
        for other in ("not_counted", "not_credited", "counts_for_ladder"):
            with self.subTest(field=other):
                self.assertNotIn(other, words,
                                 f"the words ask {other}, which also holds a card "
                                 "whose rubbers are in the totals")


class TestAnOpenedCardOfARepeatedNightNamesTheOtherCardsOfIt(WebAssetCase):
    """A copy's opened card named no other file, though the document lists them.

    ``other_cards`` is on every card of a night more than one card claims, and the
    opened card read it only in the warning for a night the cards disagree about. A
    copy said ``copy of another card — counts for nothing`` in its summary and
    nothing in its body, and the note on a night named two ways said ``Another card
    for this fixture`` without saying which. Measured before on the demo with the
    round 5 Brindle Bunyips v Brindle Kookaburras card again as
    ``bunyips-v-kookaburras (1).pdf`` under id 42000149
    (``/tmp/t7/scratch/DUP/runs/before/copy-id``, headless Chrome at 1280 and 360px):
    the copy's summary ``copy of another card — counts for nothing · sets 4–0 ·
    points 6.4 / 3.4``, no note in its body, and nothing on it naming the
    ``(1).pdf``; and with a round 2 card named two ways, both of its cards' notes
    reading ``Another card for this fixture shows the same result with a different
    player in it`` and naming no file.

    So the sentence naming the other cards is built once, from ``otherCards``, and
    every note about another card says it: the clash's, one for the copy, and the
    night named two ways. Rejected: each note wording the list for itself, which is
    how three notes come to name the files three ways; and the list on every card
    that has other cards, the one the season counted from included, a card with
    nothing wrong on it.
    """

    SENTENCE = ("var othersSaid = others.length ? plural(others.length, "
                "'The other card is ', 'The other cards are ') + "
                "others.join('; ') + '. ' : '';")

    def said(self) -> str:
        return re.sub(r"\s+", " ", jslint.without_comments(self.body("matchCard")))

    def a_note(self, label: str) -> str:
        found = re.search(r"note\('warning', '" + re.escape(label) +
                          r"',(.+?)\)\);", self.said())
        self.assertIsNotNone(found, f"the opened card has no {label!r} note")
        return found.group(1)

    def test_the_other_cards_are_named_in_one_sentence_built_once(self):
        said = self.said()
        self.assertEqual(said.count(self.SENTENCE), 1,
                         "the other cards are not named in one sentence, for any "
                         "number of them, or it says so of a document naming none")
        # Before the first note that says it, and so under no guard of theirs: built
        # inside the clash's, it is `undefined` on every other card.
        self.assertLess(said.index(self.SENTENCE),
                        said.index("if (m.not_counted === 'conflict')"),
                        "the sentence is built for a clash alone")

    def test_a_copy_says_it_is_one_and_names_the_others(self):
        condition = self.guard_for("matchCard", "'Copy'")
        self.assertEqual(condition.strip(), "m.not_totalled === 'duplicate'",
                         "the note is not given exactly on the card the season's "
                         "totals leave out as a copy")
        text = self.a_note("Copy")
        self.assertIn(" othersSaid +", text, "the copy names no other card")
        self.assertIn("counted once", text,
                      "the copy does not say the night is still counted")
        # The sentence a club deletes the file on, so it is pinned whole: a copy
        # is left out of every figure, and taking it away moves none of them.
        self.assertTrue(text.rstrip().endswith(
            "'Every figure on the page stays as it is when this file leaves the "
            "data folder.'"), text)

    def test_both_cards_of_a_night_named_two_ways_name_each_other(self):
        self.assertIn(" othersSaid +", self.a_note("Players not credited"),
                      "a card named two ways says another card differs and not "
                      "which file it is")


class TestALevelCardSaysWhyItsPointsAreBlank(WebAssetCase):
    """A card the ladder cannot use because it came out level said so on one page.

    Measured on the demo season with ``draws = false`` and the round 2 Brindle
    Wombats v Brindle Bunyips card at 6-4, 4-6, 6-4, 4-6 (``/tmp/t6/scratch/m1/
    probe``, headless Chrome at 1280 and 360px): exit 1, ``not_counted`` ``level``,
    and on ``#/matches`` the summary ``sets 2–2 · points — / —`` and nothing in the
    opened card saying why, where the team page's ``Level`` and the data health
    section give it.

    So the summary says it beside the points, from the document's ``not_counted``.
    Its rubbers still read ``Played``, since they are in the totals and the players'
    records: the words for a night that counts for nothing are not asked
    (TestACopyOfANightSaysSoOnItsCardAndOnEveryRowOfIt). Rejected: a warning in the
    opened card, which says a second time what the data health section says with
    the file and the setting.
    """

    # `this competition`, as the page's `Draws:` line and the team page say it.
    PHRASE = "level, and this competition has no draws — not in the ladder"

    def test_the_summary_says_it_of_a_level_card_alone(self):
        condition = self.guard_for("matchCard", self.PHRASE, emits="bits.push")
        self.assertEqual(condition.strip(), "m.not_counted === 'level'",
                         "the reason is given to a card that is not level in a grade "
                         "without draws, or not to one that is")

    def test_straight_after_the_points_it_explains(self):
        body = jslint.without_comments(self.body("matchCard"))
        points = self.offset_of(body, "bits.push('points ", "the summary has no points")
        said = self.offset_of(body, self.PHRASE, "the summary gives no reason")
        between = body[body.index(";", points) + 1:body.rfind("if", 0, said)]
        self.assertLess(points, said)
        self.assertEqual(between.strip(), "",
                         "something comes between the blank points and their reason")


class TestAPrintedSheetKeepsEveryColumnAndSaysWhoseSeasonItIs(WebAssetCase):
    """A sheet of paper cannot be scrolled sideways, and cannot be asked whose it is.

    Two silent losses, both measured on A4 -- 720 CSS px of content, read off a printed
    page with `pdftotext -bbox` rather than assumed -- from a twelve-team season.

    The columns. `.table-wrap` scrolls sideways and cells stay on one line, which is
    what lets a fourteen-column table be read on a phone (the class above). Printed,
    that pair is a clip with nothing on the sheet to say it clipped: the head-to-head
    grid came out nine columns short, eight opponents and the meetings total, 943px past
    the edge; the players table six short, from Diff through to D rating; a player's
    match history three short, Result among them; and the ladder without its Form
    column. Twenty columns gone from five routes, and a printed page has no scrollbar
    and no tab stop to reveal them.

    The name. The topbar was hidden, and it is the only place the club is ever written:
    boot() fills #brand-title with the competition and #brand-sub with club, season and
    build stamp, because index.html ships neutral wording that site.py copies into every
    competition's directory. So the overview named the competition only through its own
    heading, and the matches, players, team and player sheets named nothing at all. Pin
    one to the clubhouse noticeboard and it is an anonymous sheet of numbers.

    Both fixes are in the print block, and both are cheap: let a cell fold, including
    inside a word, and print the bar while hiding the furniture around it. Measured
    after: not one column past the edge on any of the 94 tables of those five routes,
    and the competition and the club on sheet one of every one of them. The cost is
    named rather than discovered -- 26 headings on the overview break mid-word
    ("Kookabu / rras") -- which is what item 3.7's printed key is for: the letters are
    spelled out in prose directly above the table. Wrapping at spaces only was measured
    too and still lost seven columns, so `overflow-wrap: anywhere` is load-bearing and
    is pinned on its own line.
    """

    # index.html's bar: <header class="topbar"> <div class="brand"> <strong
    # id="brand-title"> and <small id="brand-sub">. Written here as well as there, so
    # test_the_words_a_sheet_keeps_are_the_ones_the_script_writes_there holds the two
    # together: a selector list checked against a page that has since moved its own
    # words would pass while the sheet came out blank.
    BRAND = ({"el": "header", "cls": {"topbar"}, "id": ""},
             {"el": "div", "cls": {"brand"}, "id": ""})
    NAMES = ({"el": "strong", "cls": set(), "id": "brand-title"},
             {"el": "small", "cls": set(), "id": "brand-sub"})

    # What a sheet cannot use. #theme-toggle is named rather than left to .controls
    # because picker.html keeps its own outside that div, and the picker's bar prints
    # for the same reason this one does.
    # .table-tools is each table's Copy and CSV pair and whatever they last said.
    # Measured by taking it out of the print rule and printing all three views: Copy
    # and CSV appear on one sheet of the overview, one of the players list and three
    # of a team page, and the sheet count does not change -- so what it costs is two
    # words of ink on paper offering a clipboard.
    # .match-link is each card's link to its own page. On screen it is how one night
    # gets an address that can be sent to somebody; on paper it is an underlined
    # sentence naming a fragment nobody can follow, and the card it leads to is the
    # card it is printed underneath. Measured: hiding it changes no sheet count on the
    # matches view (28 sheets either way), so what it costs is one line of ink per card
    # -- 56 lines on the demo season -- pointing at the page it is already on.
    FURNITURE = (".tabs", ".controls", ".table-toggle", ".table-tools", ".skip-link",
                 ".home-link", ".match-link", "#theme-toggle", "#tooltip")

    def taken_off_the_sheet(self) -> list[tuple[str, csslint.Rule]]:
        """Every selector a print rule takes off the paper, with the rule it came from.

        Two ways of taking it off, because a fix that only closed one door would be
        reopened by the other: `display: none` and `visibility: hidden`. csslint keeps
        the `!important` on the value it returns, so the comparison is a prefix.
        """
        gone = []
        for rule in self.print_rules():
            hides = ((rule.value("display") or "").startswith("none")
                     or (rule.value("visibility") or "").startswith("hidden"))
            if hides:
                gone += [(part.strip(), rule) for part in rule.selector.split(",")]
        return gone

    def hides_the_name(self, selector: str) -> bool:
        """Whether hiding `selector` takes the competition or the club off the sheet.

        True for the bar itself, for anything it is inside, and for the two elements
        the script writes into -- hiding an ancestor hides the words as thoroughly as
        hiding the words.

        ``>`` is read here as a loose descendant, which is the safe direction for this
        question over a chain that starts at the bar: it can only find more rules that
        hide the name, never fewer. And a pseudo-class is read as holding, since
        `:hover` cannot happen on paper and a rule for it is one more that might hide.
        """
        loose = selector.replace(">", " ")
        for name in self.NAMES:
            chain = self.BRAND + (name,)
            if any(self.selects(loose, chain[:depth + 1])
                   for depth in range(len(chain))):
                return True
        return False

    def test_no_print_rule_takes_the_words_that_name_the_sheet_off_it(self):
        # The sweep is over every print rule, not over the one line that used to hide
        # the bar: hiding `header`, or `.brand strong`, or setting visibility on the
        # bar lower down the block, loses the same sheet by another spelling.
        for selector, rule in self.taken_off_the_sheet():
            with self.subTest(selector=selector):
                self.assertFalse(
                    self.hides_the_name(selector),
                    f"{rule.where} hides {selector} when printing, and that is where "
                    f"the competition, the club and the season are written: a printed "
                    f"sheet would be numbers with nothing saying whose they are")

    def test_the_furniture_a_sheet_cannot_use_is_still_taken_off_it(self):
        # The other half of printing the bar: the views, the jump-to lists, the theme
        # button and the skip link were hidden only as a side effect of hiding the bar
        # they sit in. A dead tab strip printed beside the club's name is worse than
        # either, and the tooltip and the Values buttons are ink spent on nothing.
        hidden = {selector for selector, _ in self.taken_off_the_sheet()}
        for part in self.FURNITURE:
            with self.subTest(furniture=part):
                self.assertIn(part, hidden,
                              f"nothing hides {part} when printing, so it prints; "
                              f"{part} is a control, and paper takes no input")

    def test_print_stops_the_wrapper_clipping_what_a_sheet_cannot_scroll(self):
        unclipped = [rule for rule in self.print_rules()
                     if ".table-wrap" in rule.selector
                     and (rule.value("overflow") or rule.value("overflow-x") or
                          "").startswith("visible")]
        self.assertTrue(unclipped,
                        "no print rule unclips .table-wrap, so every table wider than "
                        "the paper is cut at the page edge with nothing saying so: "
                        "measured on A4, nine of the head-to-head grid's fifteen "
                        "columns and six of the players table's fifteen")
        # A wrapper hidden instead of unclipped loses no columns, because it loses the
        # whole table. Cheaper to pass, and the opposite of the point.
        for selector, rule in self.taken_off_the_sheet():
            with self.subTest(selector=selector):
                self.assertNotIn(selector, (".table-wrap", "table", ".table-wrap table"),
                                 f"{rule.where} keeps the tables off the sheet "
                                 f"altogether, which is not the same as fitting them "
                                 f"on it")

    def test_print_lets_a_cell_fold_because_a_sheet_has_no_sideways_scroll(self):
        cells = [rule for rule in self.print_rules()
                 if any(part.strip() in ("th", "td")
                        for part in rule.selector.split(","))]
        folding = [rule for rule in cells if rule.value("white-space")]
        self.assertTrue(folding and folding[-1].value("white-space") == "normal",
                        "print leaves cells on one line, and a line that does not "
                        "wrap sets the table's width from the longest name in it: "
                        "the twelve-team grid then wants 1589px of a 720px sheet")
        anywhere = [rule for rule in cells
                    if (rule.value("overflow-wrap") or "").startswith("anywhere")]
        self.assertTrue(anywhere,
                        "cells may wrap but only at spaces, which was measured and is "
                        "not enough: the grid still lost five columns and the players "
                        "table two, because the width is set by 'Kookaburras' and no "
                        "space appears inside a word")

    def test_the_screen_keeps_its_cells_on_one_line(self):
        # Passes before the fix as well as after, and is here to catch the cheap
        # version of it: relaxing the screen's own rule instead of the print block.
        # That would refold every table on every phone and make the scroll regions the
        # class above marks -- and their tab stops -- point at nothing.
        screen = [rule for rule in csslint.rules(self.assets()["styles.css"])
                  if not csslint.paper_only(rule.at_rule) and rule.value("white-space")
                  and any(part.strip() in ("th", "td")
                          for part in rule.selector.split(","))]
        self.assertTrue(screen, "no rule outside @media print sets white-space on "
                                "table cells any more, so the phone layout has "
                                "quietly changed underneath this")
        self.assertEqual("nowrap", screen[-1].value("white-space"),
                         "table cells now wrap on screen too; the sideways scroll and "
                         "the tab stop on .table-wrap exist because they do not")

    def test_the_words_a_sheet_keeps_are_the_ones_the_script_writes_there(self):
        # Ties the selectors above to the page and the script: the print rules can only
        # keep the club's name on paper while the bar is still what carries it. Read
        # from the raw source, because the ids and the join are string literals.
        page = self.assets()["index.html"]
        bar = page[page.find('<header class="topbar">'):page.find("</header>")]
        self.assertIn('<div class="brand">', bar,
                      "the topbar no longer holds .brand, and the print rules that "
                      "keep a sheet's name on it are written against that chain")
        for identifier in ("brand-title", "brand-sub"):
            with self.subTest(element=identifier):
                self.assertIn(f'id="{identifier}"', bar,
                              f"#{identifier} has moved out of the topbar, so printing "
                              f"the bar no longer prints what it used to say")
        boot = self.body("boot")
        self.assertIn("getElementById('brand-sub')", boot,
                      "boot() no longer writes the line under the title, which is the "
                      "only line that carries the club")
        self.assertIn("m.club", boot,
                      "nothing writes the club's name into the page, so no sheet can "
                      "print it however the print rules are written")


class TestAPrintedSheetHoldsTheMatchCardsTheReaderOpened(WebAssetCase):
    """A printed card shows its sets when the reader opened it, and not otherwise.

    The print block shipped ``.match-body { display: block !important; }``, which reads
    as a promise that a printed card shows its sets whether or not the reader opened it.
    It never kept that promise. A ``<details>`` without ``open`` hides its content
    through ``::details-content``, whose ``content-visibility: hidden`` is not a
    ``display`` any selector inside it can override, so the line was ink in the file and
    nothing on the paper. Measured with Chrome's ``--print-to-pdf`` read back by this
    project's own PDF reader, over an eight-team season of 56 cards: not one set table on
    any sheet of any of the five routes, and deleting the line changed not one word at
    not one coordinate on not one of the 5, 5, 4, 5 and 9 sheets those routes print.

    So the question was whether to keep the promise or drop it, and paper settled it.
    ``details.match::details-content { content-visibility: visible }`` keeps it in one
    declaration -- and takes ``#/matches`` from 5 sheets to 30, a team's page from 5 to
    11 and the overview from 5 to 6. Thirty sheets is not a printout anybody asked for,
    and the reader who wants one night's sets opens that night and prints six. Dropped,
    then: what prints is what the reader chose, which is measured to work -- a card
    carrying ``open`` prints its table, and printed the same table before the deletion
    and after it.

    Rejected: keeping the promise on the overview alone, whose four latest-round cards
    cost one extra sheet. A card that prints differently depending on which page it is
    on is not a rule a reader can predict, and the next person to measure it would file
    this row again.

    Item 3.22 put a second ``<details>`` on the page -- the rules banner's explanations
    -- and the sheet does force *that* one open, measured, because paper takes no taps
    and the banner is the caveat every number on the sheet rests on. So what the second
    test below pins is the **scope** rather than the declaration: one line of a caveat is
    not 25 sheets of set tables, and the way a reader ends up with the 25 is a rule that
    opens whatever it finds -- ``details::details-content``, or one naming the card's own
    class.
    """

    # Read off matchCard() rather than written here. The print block names two elements
    # the renderer builds, and a rule checked against a class name app.js has stopped
    # using would pass while the sheet came out wrong.
    CARD = "el('details', { class: 'match' })"
    BODY = "el('div', { class: 'match-body' })"

    def card_classes(self) -> tuple[str, str]:
        """The class on a card, and the class on the half a closed card hides."""
        card = self.body("matchCard")
        for source in (self.CARD, self.BODY):
            self.assertIn(source, card,
                          f"matchCard() no longer builds {source}, so the print rules "
                          f"this class is about are written against an element that is "
                          f"not on the page any more")
        return "match", "match-body"

    def test_the_print_block_sets_no_display_on_the_half_a_closed_card_hides(self):
        # Both directions of the same declaration are wrong, which is why any value
        # fails rather than any particular one: `none` would throw away the card the
        # reader did open, and nothing else can reveal the one they did not.
        _, hidden_half = self.card_classes()
        for rule in self.print_rules():
            for part in rule.selector.split(","):
                if hidden_half not in part:
                    continue
                with self.subTest(selector=part.strip()):
                    self.assertIsNone(
                        rule.value("display"),
                        f"{rule.where} sets display on the half of a card a closed "
                        f"<details> hides, and display is not the mechanism hiding it: "
                        f"measured, the same five routes print the same words at the "
                        f"same coordinates with the declaration and without it")

    def test_the_print_block_does_not_open_every_card_on_the_readers_behalf(self):
        # Guards the other repair of the line above -- the one that works. It is one
        # declaration and it costs 25 sheets on the route a captain is most likely to
        # print, so it needs to be a decision somebody takes on purpose.
        #
        # Which is a question about what the rule reaches, not about the property: the
        # banner's disclosure is forced open on paper deliberately (item 3.22), and a
        # rule doing that has to name the one thing it opens. The last compound of the
        # selector is where that is decided -- `#view details::details-content` names
        # something and still opens all 56 cards -- so that is the part read.
        card, _ = self.card_classes()
        for rule in self.print_rules():
            opens = ("::details-content" in rule.selector
                     or (rule.value("content-visibility") or "").startswith("visible"))
            if not opens:
                continue
            for part in rule.selector.split(","):
                target = part.split("::")[0].strip()
                chosen = re.split(r"[\s>+~]+", target)[-1] if target else ""
                with self.subTest(selector=part.strip()):
                    self.assertNotIn(
                        f".{card}", target,
                        f"{rule.where} reaches inside every match card when printing, "
                        f"which does work and takes #/matches from 5 sheets to 30, a "
                        f"team's page from 5 to 11 and the overview from 5 to 6, "
                        f"measured on a 56-card season")
                    self.assertRegex(
                        chosen, r"[.#][\w-]+",
                        f"{rule.where} turns content-visibility back on for every "
                        f"<details> it finds when printing, so every card prints its "
                        f"set table whether the reader wanted it or not: 30 sheets for "
                        f"the matches page instead of 5")

    def test_a_card_the_reader_opened_is_not_cut_in_half_by_a_page_break(self):
        # Load-bearing precisely because what prints is the reader's choice: a chosen
        # card that arrives in two pieces is the same loss as one that does not arrive.
        # Measured with all 56 cards forced open, each card read from its table caption
        # to its `Source:` line: with the rule, none of the 56 spans a break; without
        # it, 14 do -- and the document is one sheet shorter, which is what the rule
        # costs.
        card, _ = self.card_classes()
        whole = [rule for rule in self.print_rules()
                 if any(part.strip().endswith(f".{card}")
                        for part in rule.selector.split(","))
                 and (rule.value("break-inside") or "").startswith("avoid")]
        self.assertTrue(whole,
                        f"no print rule keeps a .{card} on one sheet, and the card the "
                        f"reader opened is the only thing on the sheet they opened it "
                        f"for: measured, 14 of 56 cards were split across a page break "
                        f"without it")

    # The one view that may open a card, because the card is the whole page: a link to
    # #/match/<id> that arrives holding one closed row to press has sent the reader
    # somewhere they still have to look for the night. It shows the cards for one id, so
    # the sheet count it can reach is one, not thirty.
    THE_PAGE_THAT_IS_A_CARD = "renderMatch"

    def test_no_list_of_cards_opens_them_before_the_reader_asks(self):
        # The JavaScript half of the same promise. `matchCard(m, { open: true })` is a
        # supported call, and a list view that used it would print the 30 sheets the print
        # block is careful not to ask for -- and hand a reader on a phone 56 open cards
        # to scroll past. The definition's own line is skipped: that is the parameter,
        # not a caller. The exception is named as a place and not as a spelling of the
        # option, so a second view cannot take it by writing `open: true` the same way.
        source = self.assets()["app.js"]
        page = self.body(self.THE_PAGE_THAT_IS_A_CARD)
        start = source.find(page)
        self.assertNotEqual(-1, start,
                            f"{self.THE_PAGE_THAT_IS_A_CARD}() is not in app.js as read")
        calls = [(hit.start(), hit.group(1))
                 for hit in re.finditer(r"matchCard\(([^)]*)\)", source)
                 if not hit.group(1).startswith("m, opts")]
        self.assertTrue(calls, "nothing calls matchCard() any more, so the print rules "
                               "for a match card are about an element no view builds")
        opened = []
        for at, text in calls:
            if "open" not in text:
                continue
            opened.append(text)
            with self.subTest(call=text):
                self.assertTrue(
                    start <= at < start + len(page),
                    f"matchCard({text}) opens a card the reader did not open, and a view "
                    f"that lists cards lists all of them: 56 on #/matches, which is 30 "
                    f"printed sheets")
        self.assertLessEqual(len(opened), 1,
                             f"{len(opened)} call sites open a card, and the exception is "
                             f"for one: the page whose whole content is the card asked "
                             f"for. A second is a list of open cards again, wherever it "
                             f"is written: {opened}")


class TestEverySheetOfAPrintoutNamesTheClubAndTheSeason(WebAssetCase):
    """Sheet four of a printout says whose season it is, the same as sheet one.

    The class above puts the club, the competition and the season on sheet one, by
    printing the topbar. Sheets two onwards carried nothing: measured over the five
    routes of an eight-team season, 5 of 28 sheets named the season and 23 were numbers
    with nothing saying whose. A ladder pinned to the clubhouse noticeboard is four
    sheets, and three of them are anonymous.

    CSS cannot fix that in Chrome, and this was measured at length rather than assumed.
    `position: fixed` is the only mechanism that repeats an element on every sheet -- it
    does repeat, 5 of 5 -- but its containing block is the page *area*, which is where
    the flow content starts, so the band lands on the content: 42 content words inside
    it. `@page { margin-top: 1in }` then moves the band and the content down together and
    reserves nothing, at a cost of one extra sheet on two routes. `top: -0.75in`,
    `margin-top: -0.75in` and `transform: translateY(-0.75in)` each dumped the band at
    the *bottom* of the sheet (y 718-744 of 792), and `bottom: 100%` put it back at the
    top of the page area. `body { display: table }` with the bar as a
    `table-header-group` did not repeat at all, 1 of 5, with or without a fixed table
    layout. CSS Paged Media margin boxes -- `@page { @top-center { content: ... } }` --
    print nothing.

    What already repeats on every sheet is the browser's own print header, which prints
    `document.title`, and which Chrome's print dialog has ticked by default (its
    `--print-to-pdf` needs `--no-pdf-header-footer` to switch off). So the fix is in the
    title, and `app.js` was already writing one per route -- five separate wordings of
    `what — competition`, none of which named the club or the season. Folded into one
    function that composes all four parts, measured on paper: every sheet of every route
    now carries `Matches · Wednesday Division 3 · BRINDLE CREEK TENNIS CLUB INC ·
    Season 2` across the top, in 8pt, centred in a 467pt band. Chrome ellipsises the tail
    past about 110 characters (73 characters ended at x=502 of a band ending at 586; 143
    was cut mid-word at 586), which is why the order runs from what the sheet cannot tell
    you to what it nearly can: the season goes last because every match sheet is covered
    in dates.
    """

    # The one function that writes it, the parts it composes, and the four views whose
    # own name is a field or a word this file can name. The fifth is renderMatch(),
    # whose name is a local built from the two teams: it is checked in
    # TestOneNightHasAnAddressOfItsOwn, by reading the local off the call rather than
    # writing its name down here. The sixth caller is the router, which names the season
    # and nothing else -- see the ordering test below.
    NAMER = "setPageName"
    PARTS = ("m.label", "m.club", "m.season_label")
    VIEWS = {"renderTeam": "t.name", "renderPlayer": "p.name",
             "renderMatches": "'Matches'", "renderPlayers": "'Players'"}

    def namer(self) -> str:
        """The source of the one function that composes the repeated name."""
        return self.body(self.NAMER)

    def test_one_function_writes_the_name_the_browser_repeats(self):
        # Five call sites wrote five wordings of one thing, which is how the club and the
        # season came to be missing from all five: there was no one place to add them to.
        # The whole file is searched, because a second writer anywhere is a sixth wording
        # -- over the code with its comments and strings blanked, since the paragraph
        # above the function that does write it says the name of the property too.
        source = self.assets()["app.js"]
        writers = [hit.start() for hit in
                   re.finditer(r"document\.title", jslint.code_only(source))]
        self.assertEqual(1, len(writers),
                         f"{len(writers)} places set document.title, and that is the one "
                         f"string the browser repeats on every printed sheet: "
                         f"{[source[at:at + 60] for at in writers]}")
        body = self.namer()
        start = source.find(body)
        self.assertTrue(start <= writers[0] < start + len(body),
                        f"the title is set outside {self.NAMER}(), so a route can name a "
                        f"sheet without the club or the season being on it")

    def test_the_repeated_name_carries_the_competition_the_club_and_the_season(self):
        body = self.namer()
        for part in self.PARTS:
            with self.subTest(part=part):
                self.assertIn(part, body,
                              f"{self.NAMER}() does not use {part}, so no printed sheet "
                              f"past the first says it: the topbar prints on sheet one "
                              f"only, and the browser's header is the only thing that "
                              f"repeats")

    def test_the_part_a_sheet_can_least_afford_to_lose_comes_first(self):
        # Chrome cuts the tail, not the head, and it cut a 143-character title mid-word.
        # So the order is a decision and not an accident: the view's own name leads,
        # because nothing else on the sheet repeats it; the season goes last, because
        # every match sheet is covered in dates.
        body = self.namer()
        order = [self.offset_of(body, part, f"{self.NAMER}() never says {part}")
                 for part in self.PARTS]
        self.assertEqual(sorted(order), order,
                         f"{self.NAMER}() composes the name as "
                         f"{self.PARTS} out of order; printed, the tail is what Chrome "
                         f"drops, and it should be dropping the season rather than the "
                         f"club")
        name = re.search(r"function " + self.NAMER + r"\((\w+)\)", body)
        self.assertIsNotNone(name, f"{self.NAMER}() takes no argument, so a sheet cannot "
                                   f"say which page of the season it is")
        self.assertLess(self.offset_of(body, name.group(1) + ",",
                                       "the page's own name is never composed in"),
                        order[0],
                        "the page's own name comes after the competition, so a narrow "
                        "print header shows the season's name on every sheet and never "
                        "which page it is")

    def test_a_document_that_names_no_club_gets_no_dangling_separator(self):
        # The old wordings were `'Matches — ' + (meta().label || '')`, which prints
        # `Matches — ` with a trailing dash when a hand-edited season file has no label.
        # Four parts joined the same way is four times that, and a club that fills in no
        # season name has an empty one in the middle of every sheet's header.
        # The dropping is dotted()'s, and tested there once.
        body = self.namer()
        self.assertRegex(body, r"= dotted\(\[\w+, ",
                         f"{self.NAMER}() joins the parts without dropping the empty "
                         f"ones, so a document with no club in it prints ' ·  · ' on "
                         f"every sheet")

    def test_every_view_names_its_own_sheet_through_that_one_function(self):
        for fn, lead in self.VIEWS.items():
            with self.subTest(view=fn):
                self.assertIn(f"{self.NAMER}({lead})", self.body(fn),
                              f"{fn}() does not name its sheet as {self.NAMER}({lead}), "
                              f"so its printed header says which season it is and not "
                              f"which page")

    def test_a_route_that_finds_nothing_still_names_the_season(self):
        # renderTeam(), renderPlayer() and renderMatch() answer an unknown slug or id with
        # renderMissing() and return before they name anything. The router names the
        # season first, so the sheet is still the club's; and that call is the overview's
        # own title, which is why it passes no page name.
        body = self.body("render")
        first = self.offset_of(body, f"{self.NAMER}('')",
                               "the router names no season of its own")
        for fn in ("renderTeam(", "renderPlayer(", "renderMatch(", "renderMatches(",
                   "renderPlayers(", "renderOverview("):
            with self.subTest(view=fn):
                self.assertLess(first, self.offset_of(body, fn, f"render() never calls {fn}"),
                                f"render() names the page after calling {fn}, and the two "
                                f"views that can find nothing set no name at all: a "
                                f"mistyped team's sheet would carry whichever page the "
                                f"reader was on before")

    def test_the_print_block_does_not_try_to_repeat_the_bar_itself(self):
        # The trap this row walked into first, left as a test because the next person to
        # read "sheets two onwards are anonymous" will reach for the same rule, and it
        # looks like it works: the bar does appear on every sheet.
        for rule in self.print_rules():
            if ".topbar" not in rule.selector:
                continue
            with self.subTest(rule=rule.where):
                self.assertNotIn("fixed", rule.value("position") or "",
                                 f"{rule.where} fixes the bar to repeat it, and a fixed "
                                 f"element sits in the page area where the content "
                                 f"starts: measured, the bar printed on all 5 sheets "
                                 f"with 42 content words underneath it, and an "
                                 f"@page margin moves both of them down together")


class TestJumpingToATeamOrPlayerWaitsToBeAsked(WebAssetCase):
    """Choosing something in a list is not the same as asking to be taken there.

    The topbar's "Jump to a team or player" is a `<select>`, and it navigated on
    `change`. Arrowing through a closed `<select>` fires `change` on every
    keypress, so a keyboard reader looking for the twentieth player was taken to
    nineteen pages on the way there -- nineteen re-renders, nineteen scrolls to
    the top, nineteen history entries, and the page they were reading gone before
    they had finished choosing. Back was then nineteen presses from where they
    started. A reader with a mouse, who sees the popup and commits once, never met
    any of it.

    WCAG 2.2 3.2.2 (On Input, Level A) draws the line for everybody rather than
    for the keyboard alone: changing the setting of a control must not change the
    context unless the reader was told beforehand that it would. So the list now
    only chooses, and a button does the going.
    """

    LISTENER = re.compile(r"(\w+)\.addEventListener\('(\w+)',")

    def listeners(self, source: str) -> list[tuple[str, str, str]]:
        """``(object, event, the rest of the call)`` for each listener bound here.

        The handler is taken by balancing the parentheses of the
        ``addEventListener`` call, so a handler containing one does not end the
        search early.
        """
        found = []
        for match in self.LISTENER.finditer(source):
            at = source.index("addEventListener(", match.start()) \
                 + len("addEventListener")
            depth = 0
            for index in range(at, len(source)):
                if source[index] == "(":
                    depth += 1
                elif source[index] == ")":
                    depth -= 1
                    if depth == 0:
                        found.append((match.group(1), match.group(2),
                                      source[match.end():index]))
                        break
        return found

    def picker_handlers(self, event: str) -> list[str]:
        """Every handler the control binds to one event of the list itself."""
        return [handler for obj, name, handler
                in self.listeners(self.body("fillPicker"))
                if obj == "picker" and name == event]

    def inner_functions(self, source: str) -> dict[str, str]:
        """Each function declared inside ``source``, by name, braces balanced."""
        found = {}
        for match in re.finditer(r"function (\w+)\([^)]*\) \{", source):
            depth, at = 0, match.end() - 1
            for index in range(at, len(source)):
                if source[index] == "{":
                    depth += 1
                elif source[index] == "}":
                    depth -= 1
                    if depth == 0:
                        found[match.group(1)] = source[at:index + 1]
                        break
        return found

    def jump(self) -> tuple[str, str]:
        """The name and body of the one thing in the control that changes route.

        Found by what it does rather than by what it is called, because the whole
        of this item is about which events reach it.
        """
        inner = self.inner_functions(self.body("fillPicker"))
        movers = {name: body for name, body in inner.items()
                  if name != "fillPicker" and "location.hash" in body}
        self.assertEqual(len(movers), 1, f"the control should change the route in "
                                        f"exactly one place, and it has "
                                        f"{len(movers)}: {sorted(movers)}")
        return movers.popitem()

    def go_button(self) -> re.Match:
        found = re.search(r'<button[^>]*id="entity-go"[^>]*>(.*?)</button>',
                          self.assets()["index.html"], re.S)
        self.assertIsNotNone(found, "index.html has no #entity-go, so the only way "
                                    "out of the list is the one that fires on "
                                    "every arrow keypress")
        return found

    def test_choosing_in_the_list_does_not_take_the_reader_anywhere(self):
        name, _ = self.jump()
        for event in ("change", "input"):
            for handler in self.picker_handlers(event):
                with self.subTest(event=event):
                    code = jslint.code_only(handler)
                    self.assertNotIn("location", code,
                                     f"the list's {event} handler moves the page "
                                     f"itself: a closed <select> fires {event} on "
                                     f"every arrow keypress, so choosing with a "
                                     f"keyboard visits every option on the way")
                    self.assertNotIn(name + "(", code,
                                     f"the list's {event} handler calls {name}(), "
                                     f"which is the same navigation one call "
                                     f"further away")

    def test_the_going_waits_for_a_control_that_says_go(self):
        button = self.go_button()
        self.assertIn('type="button"', button.group(0),
                      "a <button> with no type is a submit button; there is no "
                      "form here, so what it does next is the browser's guess")
        # Entities blanked before looking for a letter: `&nbsp;` is not whitespace
        # in the source and is not a name in the browser either, so a button
        # containing only one reads as unlabelled to everybody.
        self.assertRegex(re.sub(r"&[#\w]+;", " ", button.group(1)), r"[A-Za-z]",
                         "the button has no words in it, so what it commits to is "
                         "anybody's guess")
        name, _ = self.jump()
        presses = [handler for obj, event, handler
                   in self.listeners(self.body("fillPicker")) if event == "click"]
        self.assertTrue(presses, "nothing in the control is wired to a press, so "
                                 "the list can be read and never acted on")
        self.assertTrue(any(name in handler for handler in presses),
                        f"no press calls {name}(), so the button is there and the "
                        f"jump is unreachable")

    def test_the_button_follows_the_list_it_commits(self):
        # Tab order, which is document order here: list, then the thing that acts
        # on it. A commit button before its own control is reached before there is
        # anything to commit.
        page = self.assets()["index.html"]
        self.assertLess(page.index('id="entity-picker"'),
                        page.index('id="entity-go"'),
                        "the Go button comes before the list it commits")
        self.assertLess(page.index('id="entity-go"'),
                        page.index('id="theme-toggle"'),
                        "another control sits between the list and its Go button, "
                        "so a reader who has chosen has to tab past it")

    def test_a_keyboard_reader_can_commit_without_leaving_the_list(self):
        # Two keystrokes for a mouse reader's one click would be the fix charging
        # its cost to the reader it is for. Enter reaches this handler only while
        # the list is closed -- an open popup consumes its own keys -- which is
        # exactly the case that had no way to commit.
        handlers = self.picker_handlers("keydown")
        self.assertTrue(handlers, "the list answers no keys of its own, so "
                                  "committing means tabbing away to the button")
        handler = handlers[0]
        guard = re.search(r"if \(e\.key (!==|===) 'Enter'\)", handler)
        self.assertIsNotNone(guard, "the list's keydown handler does not single "
                                    "out Enter, so it answers every key a reader "
                                    "presses while choosing")
        self.assertEqual(guard.group(1), "!==",
                         "the guard is inverted: Enter is now the one key that "
                         "does nothing, and every other key commits")
        self.assertIn("return", handler[guard.end():],
                      "nothing is given up on, so the handler runs for keys it "
                      "just decided were not Enter")
        self.assertIn("preventDefault()", handler,
                      "Enter is left to the browser as well, which in a form is a "
                      "submit and here is a second unasked-for thing happening")
        name, _ = self.jump()
        self.assertIn(name + "()", handler,
                      f"Enter does not reach {name}(), so the key that looks like "
                      f"a commit is not one")

    def test_only_one_thing_changes_the_route(self):
        # Both ways in go through the same function, so "where does this take me"
        # has one answer and the two cannot drift.
        self.assertEqual(self.body("fillPicker").count("location.hash"), 1,
                         "the control writes the route in more than one place")

    def test_nothing_is_jumped_to_until_something_is_chosen(self):
        name, body = self.jump()
        found = re.search(r"if \(!(\w+)\) return;", body)
        self.assertIsNotNone(found, f"{name}() does not give up when nothing is "
                                    f"chosen, so Enter on the prompt itself goes "
                                    f"to #/: a route the router reads as the "
                                    f"overview, arrived at by accident")
        self.assertRegex(body, rf"var {found.group(1)} = picker\.value;",
                         f"the guard is on something other than what the list "
                         f"says, so {name}() can refuse a real choice or accept "
                         f"the prompt")
        self.assertIn("disabled", self.go_button().group(0),
                      "the Go button ships enabled, so the first thing a reader "
                      "can press on a page they have chosen nothing on does "
                      "nothing, and says nothing about why")

    def test_the_button_says_whether_there_is_anywhere_to_go(self):
        # Read off the list every time rather than remembered beside it: the value
        # changes from two directions -- the reader choosing, and the router
        # syncing it on each render -- and a second copy of "is anything chosen"
        # would be wrong in whichever direction it was not updated from.
        self.assertRegex(self.body("markJumpReady"),
                         r"if \((\w+)\) \1\.disabled = !picker\.value;",
                         "the Go button's state is not taken from the list's own "
                         "value, or the element it is set on is not the one that "
                         "was found")
        self.assertIn("markJumpReady(picker)", self.body("markTabs"),
                      "the render that syncs the list to the route leaves the "
                      "button as it was, so it offers a jump the list no longer "
                      "has, or refuses one it does")
        self.assertTrue(any("markJumpReady" in handler
                            for handler in self.picker_handlers("change")),
                        "choosing in the list does not update the button, so the "
                        "reader's choice is made and nothing says it can be acted "
                        "on")

    def test_the_route_the_jump_builds_is_one_the_router_reads(self):
        # The two halves of the option's value are a vocabulary shared with the
        # router, in a file where neither end mentions the other. Nothing about a
        # wrong prefix is visible until a reader presses Go and lands on the
        # overview.
        code = self.body("fillPicker")
        prefixes = set(re.findall(r"value: '(\w+):'", code))
        self.assertEqual(prefixes, {"team", "player"},
                         f"the list offers {sorted(prefixes)}; the jump builds its "
                         f"route out of whatever is in front of the colon")
        route = self.body("parseRoute")
        for prefix in sorted(prefixes):
            with self.subTest(prefix=prefix):
                self.assertIn(f"parts[0] === '{prefix}'", route,
                              f"the router does not know the view {prefix!r}, so "
                              f"choosing one lands on the overview instead")
        self.assertIn("'#/' + bits[0] + '/' + bits[1]", self.jump()[1],
                      "the route is no longer built out of the two halves of the "
                      "chosen value")

    def test_a_button_that_cannot_be_pressed_does_not_look_pressable(self):
        # `.btn` paints its own background and ink, which overrides the greying a
        # browser would have done for it, so a disabled button looks exactly like a
        # live one unless the stylesheet says otherwise -- and the reader finds out
        # by pressing it.
        rules = csslint.rules(self.assets()["styles.css"])
        live = {rule.value("color") for rule in rules
                if ".btn" in rule.selector and ":" not in rule.selector
                and rule.value("color")}
        off = [rule for rule in rules if ".btn:disabled" in rule.selector
               and rule.value("color")]
        self.assertTrue(live, "no rule paints a live .btn, so this check has "
                              "nothing to compare against")
        self.assertTrue(off, "nothing says what a disabled .btn looks like, so Go "
                             "before a choice is made looks like Go after one")
        self.assertNotIn(off[0].value("color"), live,
                         f"a disabled .btn is painted in {off[0].value('color')}, "
                         f"which is what a live one is painted in")
        self.assertTrue([rule for rule in rules
                         if ".btn:disabled:hover" in rule.selector],
                        "the hover lift still reaches a disabled button, so it "
                        "answers the pointer as though it could be pressed")


class TestTheStickyTopbarDoesNotCoverWhatAScrollLandsOn(WebAssetCase):
    """The topbar sticks to the top of the page, and the page scrolls underneath it.

    Measured in Chrome at real widths -- `/tmp/barcheck.py`, which renders the page
    inside an iframe because headless Chrome will not open a window narrower than
    485px and the widths that matter here are narrower than that. The bar is 82px tall
    at 1280x900, 128px at 700 where its three rows wrap to two, and 187px at 360x640:
    29% of a phone screen, permanently, spent on words the reader has already read.

    And every scroll the browser performs itself lands its target at the top of the
    page, which is where the bar is. At 360, an anchor jump put a scrolling table
    wrapper's top exactly 186px behind the bar, Shift+Tab onto a control above the
    screen put the same wrapper 186px behind it, and "Skip to content" landed on a
    `main` whose first heading was 169px behind it; at 1280 an anchor jump lands 82px
    behind it. A reader with a mouse nudges the wheel and finds what they were sent
    to. A reader on a keyboard is told they have arrived somewhere they cannot see,
    which is what WCAG 2.2 2.4.11 (Focus Not Obscured (Minimum), Level AA) is about.
    The wrapper in those measurements is one of the tab stops the fix above this one
    added, so the two interlock: a new place for a keyboard to land is only worth
    having if landing there shows you something.

    Two halves, held together here because they answer the same complaint at
    different widths. Above the phone breakpoint the bar stays where it is and the
    page is told to leave its height clear -- measured from the bar, since no constant
    is right at all three of 82, 128 and 187. Below it the bar stops sticking
    altogether: it is the first thing in the document, so it is a flick of a thumb or
    a press of Home away, which is a cheaper way to reach three rows of chrome than
    paying 29% of every screenful for them.
    """

    def offset(self) -> str:
        """The body of the pass that sets the offset, comments and strings kept."""
        return self.body("markStickyOffset")

    def arms(self) -> tuple[str, str]:
        """The offset's value while the bar sticks, and its value when it does not.

        Taken in that order whichever way round the source asks the question. A test
        that assumed `=== 'sticky'` would pass a file that had inverted the comparison
        and swapped the arms with it, which is the same mistake made twice and left
        unpinned.
        """
        source = self.offset()
        asked = re.search(r"var (\w+) = getComputedStyle\(\w+\)\.position "
                          r"(===|!==) 'sticky';", source)
        self.assertIsNotNone(asked, "markStickyOffset() does not ask the stylesheet "
                                    "whether the bar is still sticky, so it is "
                                    "either repeating the breakpoint or offsetting "
                                    "for a bar that has scrolled away")
        name, operator = asked.groups()
        set_ = re.search(rf"scrollPaddingTop =\s*{name} \? (.+?) : (.+?);",
                         source, re.S)
        self.assertIsNotNone(set_, f"markStickyOffset() does not choose the offset "
                                   f"on {name}, so the stylesheet's answer is read "
                                   f"and then not used")
        first, second = set_.groups()
        return (first, second) if operator == "===" else (second, first)

    def test_what_a_scroll_is_asked_to_clear_is_the_bar_it_is_measured_from(self):
        # 82px wide-screen, 128px where the bar wraps, 187px on a phone, and taller
        # again with a long club name in it: a number in the file would be wrong at
        # three of those four. `.topbar` is a string literal, so it is looked for in
        # the raw source rather than in code_only().
        source = self.offset()
        self.assertIn("querySelector('.topbar')", source,
                      "markStickyOffset() does not find the bar, so whatever it is "
                      "measuring is not the thing in the way")
        # On the scrollport, which is the root element: scroll-padding set on the body
        # is read by nothing, and the failure is silent in both directions.
        self.assertIn("document.documentElement.style.scrollPaddingTop", source,
                      "the offset is not set on the element that scrolls")
        # The guard has to read the same way round as the measurement, or the pass
        # gives up precisely when there is a bar to measure.
        guard = re.search(r"if \(!(\w+)\) return;", source)
        self.assertIsNotNone(guard, "markStickyOffset() measures a bar it has not "
                                    "checked for, so a page without one throws here "
                                    "and takes the rest of the render with it")
        self.assertIn(f"{guard.group(1)}.getBoundingClientRect", source,
                      f"markStickyOffset() guards {guard.group(1)} and measures "
                      f"something else")
        stuck, _ = self.arms()
        self.assertRegex(stuck, r"getBoundingClientRect\(\)\.height|offsetHeight",
                         "the offset is not measured from the bar's own height")
        self.assertIn("'px'", stuck, "a scroll padding without a unit is not a "
                                     "length, and a browser drops it")
        self.assertNotRegex(stuck, r"\d\d",
                            f"the offset is written down rather than measured: "
                            f"{stuck.strip()}")

    def test_the_bar_it_measures_is_the_only_thing_that_sticks_to_the_top(self):
        # Two ends of one claim. The stylesheet makes exactly one thing stick, so
        # there is nothing else overlapping the top of the page that this offset does
        # not account for; and the selector the script measures is that same thing,
        # so renaming the bar cannot leave the offset measuring nothing.
        rules = csslint.rules(self.assets()["styles.css"])
        stuck = [rule for rule in rules if rule.value("position") == "sticky"]
        self.assertEqual(len(stuck), 1,
                         f"{len(stuck)} rules stick to the top of the page; the "
                         f"offset is measured from one of them: "
                         f"{[rule.where for rule in stuck]}")
        asked = re.search(r"querySelector\('([^']+)'\)", self.offset())
        self.assertEqual(stuck[0].selector, asked.group(1),
                         f"the script measures {asked.group(1)} and the stylesheet "
                         f"sticks {stuck[0].selector}")
        self.assertEqual(stuck[0].value("top"), "0",
                         "the sticky bar is not pinned to the top of the page, so "
                         "the top is not where it covers")
        self.assertEqual(stuck[0].at_rule, "",
                         f"the bar only sticks inside {stuck[0].at_rule}, so the "
                         f"offset is measured for a width it is not needed at")

    def test_the_offset_goes_when_the_bar_stops_sticking(self):
        # Below the phone breakpoint the bar scrolls away with everything else. A page
        # still leaving 187px clear at the top would open every jump with a gap that
        # nothing is in, which is the same defect the other way up.
        _, loose = self.arms()
        self.assertIn(loose.strip(), ("''", '""', "'0px'", "'0'"),
                      f"a bar that no longer sticks still costs {loose.strip()} of "
                      f"empty space at the top of every scroll")

    def test_it_is_remeasured_wherever_the_document_is(self):
        # The same two call sites as the scrolling-table pass above, for the same two
        # reasons: the bar can only be measured once it is laid out, and its height
        # changes with the width of the window -- three rows on a phone, two at 700,
        # one at 1280.
        for name in ("render", "onResize"):
            with self.subTest(caller=name):
                self.assertIn("markStickyOffset()", self.body(name),
                              f"{name}() does not remeasure the bar, so the offset "
                              f"is whatever the last shape of the window made it")

    def test_the_skip_link_asks_for_the_alignment_the_offset_is_measured_for(self):
        # focus() scrolls by "nearest", and `main` is taller than the screen, so
        # nearest means no scroll at all: measured at 360, the press left main's top
        # at the very top of the page with its first heading 169px behind the bar.
        # scroll-padding-top applies to the scroll, not to the focus, so the scroll is
        # asked for in its own right -- and the focus is told not to do its own, which
        # is also why the two can be written in either order.
        link = self.body("initSkipLink")
        self.assertRegex(link, r"\.focus\(\{\s*preventScroll: true\s*\}\)",
                         "the skip link's focus() does its own scrolling, which is "
                         "the nearest-edge scroll that landed the reader on a heading "
                         "behind the bar")
        # An empty call, or `block: 'start'` written out: the same alignment, and the
        # one scroll-padding-top offsets. `nearest` is what focus() already did.
        self.assertRegex(link, r"\.scrollIntoView\(\s*(\)|\{[^)]*block: 'start')",
                         "nothing scrolls the skip link's target to the top of the "
                         "page, so the reader is focused on content the page has "
                         "either not moved to or moved only as far as it had to")

    def test_a_phone_does_not_spend_a_third_of_its_screen_on_a_bar_that_never_leaves(self):
        rules = csslint.rules(self.assets()["styles.css"])
        narrow = [rule for rule in rules if ".topbar" in rule.selector
                  and "max-width" in rule.at_rule]
        # `static` or `relative`: the two that scroll away with the page. The check is
        # which of those the bar is given rather than the word `static`, because the
        # property being asked about is whether the bar leaves -- and `fixed` would
        # answer this test's spelling while covering the content permanently.
        unstuck = [rule for rule in narrow
                   if rule.value("position") in ("static", "relative")]
        self.assertTrue(unstuck,
                        f"the bar still sticks at phone widths, where it is 187px of "
                        f"a 640px screen: "
                        f"{[(rule.where, rule.value('position')) for rule in narrow]}")
        # The breakpoint the rest of the narrow layout already uses, not a second one
        # for a reader of this file to find: the sheet has one max-width block.
        conditions = {rule.at_rule for rule in rules if "max-width" in rule.at_rule}
        self.assertEqual(conditions, {unstuck[0].at_rule},
                         f"the bar is unstuck at its own breakpoint, {conditions} "
                         f"being the widths the rest of the layout changes at")
        # One class each, so specificity is equal and source order is what decides.
        base = [rule for rule in rules if rule.value("position") == "sticky"]
        self.assertGreater(unstuck[0].line, base[0].line,
                           "the rule that unsticks the bar comes before the rule "
                           "that sticks it, so it loses the cascade and does nothing")

    def test_what_a_phone_scrolls_past_is_still_the_first_thing_in_the_document(self):
        # What makes a bar that scrolls away cheap to get back to, and what keeps it
        # in reach of a reader who does not scroll at all: it is still the second
        # thing on the page, the skip link still steps over it, and the views it holds
        # are still the first navigation a screen reader meets.
        html = self.assets()["index.html"]
        order = [html.index(needle) for needle in
                 ('class="skip-link"', 'class="topbar"', 'class="tabs"',
                  '<main id="view"')]
        self.assertEqual(order, sorted(order),
                         "the topbar's order in the document has changed, so a "
                         "reader who scrolls up to reach it may not find it there")


class TestThePageDoesNotScrollSidewaysOnAPhone(WebAssetCase):
    """A phone's screen is 320-390px wide. The season dashboard's document was 945px.

    Measured in Chrome at real widths -- `/tmp/widecheck.py`, the iframe method the
    sticky-bar tests above use, because headless Chrome will not open a window
    narrower than about 500px. Against 320, 360 and 390 the document came back 945px
    on every route of the dashboard; against 700, 953px. Eight boxes stuck out past
    the document's own right edge and all eight were inside the rules banner, each
    line held unbreakable by `.rules-banner > * { white-space: nowrap; }` while the
    banner itself was told to wrap. The widest is the trailing advisory sentence at
    933px, and 945 is that plus the 12px gutter. Five of the banner's six lines were
    wider than the 336px content column at 360 -- 555, 525, 515 and 388 against a
    151px "Sets:" line -- so the one a club is asked to check, "Unplayed: zero -- a
    bye or a washout is scored as nothing", ran 207px past the screen.

    What that costs is not a clipped word. It is 585px of pannable emptiness to the
    right of every page at 360, a horizontal scrollbar on the whole document, five of
    six banner lines cut mid-word, and content sitting off-centre whenever the reader
    has panned: two-dimensional scrolling, which WCAG 2.2 SC 1.4.10 Reflow (Level AA)
    asks for the absence of at 320px. The content column itself is unharmed -- the
    hero card measures 336px at 360 both before and after -- so nothing on the page
    looks broken, which is how this survived being looked at.

    The fix is two declarations and neither is behind a breakpoint. The advisory is
    933px wide, so every viewport up to roughly 973 was affected and the sheet's one
    `max-width` block stops at 700; a media query would have left 701-973 as they
    were. It is `overflow-wrap: anywhere` rather than `break-word` or nothing at all
    because only `anywhere` shrinks a box's min-content width: with a 53-character
    set label in the data -- the shape a club's own scorecard can carry -- deleting
    the declaration or spelling it `break-word` still left the document 382px at both
    320 and 360, and only `anywhere` reached 320 (`/tmp/tokencheck.py`).

    The price is height. The banner goes from 138px to 250px at 360 and 269px at 320,
    and the chrome above the first content grows from 522px to 635px of a 640px
    screen. That is a real regression in what a reader sees before scrolling, and it
    was filed as its own improvement on the assumption that the treatment was to
    shorten the trailing advisory -- an editorial decision with its own alternatives
    rather than a property in this sheet.

    Item 3.22 paid it back and the assumption was wrong: of the six lines making up the
    250px -- 38, 19, 38, 38, 38 and 56 -- the advisory is the 56 and the 38s are four
    rules whose explanation wraps their line to two. The split is names on the screen,
    explanations behind one ``<details>``, which takes the banner to 115px at 360 and
    136 at 320 and the chrome to 499px. The class below is that item, and the test at
    the foot of this one -- that nothing hides the banner to make it fit -- is what
    stopped the cheap version of the same saving.
    """

    def banner_rules(self) -> tuple[csslint.Rule, ...]:
        """Every rule that names the banner, by either the class or the id.

        Both spellings, because the section carries both and a line break forbidden
        through `#rules-banner` would overflow exactly as far as one forbidden
        through `.rules-banner` while being invisible to a check that read only the
        class.
        """
        return tuple(rule for rule in csslint.rules(self.assets()["styles.css"])
                     if ".rules-banner" in rule.selector
                     or "#rules-banner" in rule.selector)

    def breaks_long_words(self, rule: csslint.Rule) -> bool:
        """Does this rule's wrapping ask for a box narrower than its longest word?

        `overflow-wrap: anywhere` and `word-break: break-all` both shrink the
        min-content width, which is the measurement a flex line is laid out against.
        `overflow-wrap: break-word` breaks the word on screen but leaves min-content
        at the whole word, and was measured leaving the document 382px at 320.
        """
        return (rule.value("overflow-wrap") == "anywhere"
                or rule.value("word-break") in ("break-all", "break-word"))

    def test_the_banners_lines_are_allowed_to_break(self):
        # `nowrap` and `pre` are the two values that refuse a break. The check is
        # which value the banner is given rather than the absence of the word
        # `nowrap`, because `pre` overflows by the same 625px while reading as a
        # note about whitespace.
        held = [rule for rule in self.banner_rules()
                if rule.value("white-space") in ("nowrap", "pre")]
        self.assertEqual(
            [rule.where for rule in held], [],
            "the rules banner still forbids a line break, so its longest line -- "
            "933px of advisory at every width -- is what the document is as wide as")

    def test_a_word_too_long_for_the_screen_is_broken_and_not_pushed_past_it(self):
        wrapping = [rule for rule in self.banner_rules()
                    if rule.value("overflow-wrap") or rule.value("word-break")]
        self.assertTrue(wrapping,
                        "no rule tells the banner what to do with a word wider than "
                        "the screen, and a 53-character set label -- which a club's "
                        "own scorecard can carry -- left the document 382px at 320")
        # Every such rule, not just the first: a `break-word` sitting anywhere in the
        # sheet is a fix that was written, measured against English prose, and does
        # nothing for the one input that needed it.
        weak = [rule for rule in wrapping if not self.breaks_long_words(rule)]
        self.assertEqual(
            [(rule.where, rule.value("overflow-wrap"), rule.value("word-break"))
             for rule in weak], [],
            "the banner's wrapping breaks a long word on screen but still asks the "
            "flex line for the whole word's width, which is the 382px document")

    def test_the_break_is_not_kept_for_the_widths_someone_thought_of(self):
        # 700 overflowed by 253px and 700 is not a phone. The overflow is a property
        # of a 933px sentence, not of a screen size, so it is fixed unconditionally
        # and the sheet's `max-width: 700px` block is not where it goes.
        unconditional = [rule for rule in self.banner_rules()
                         if self.breaks_long_words(rule) and not rule.at_rule]
        self.assertTrue(
            unconditional,
            f"the banner only breaks its long words inside an at-rule, so the widths "
            f"it does not cover keep the overflow: "
            f"{[rule.where for rule in self.banner_rules() if self.breaks_long_words(rule)]}")

    def test_each_of_the_banners_lines_still_gets_a_row_of_its_own(self):
        # The break and the wrap are one fix in two declarations. With `flex-wrap:
        # nowrap` beside the break the document stops overflowing -- 320 stays 320 --
        # and the six lines are laid out 62, 37, 35, 34, 26 and 13px wide, every one
        # of them broken mid-word into a column narrower than a word. So a check on
        # the document's width alone would have called that fixed.
        wrapping = [rule for rule in self.banner_rules() if rule.value("flex-wrap")]
        self.assertTrue(wrapping, "the banner is a flex container that never says "
                                  "whether its lines may wrap, so one row is what "
                                  "the six of them share")
        self.assertEqual(
            [(rule.where, rule.value("flex-wrap")) for rule in wrapping
             if rule.value("flex-wrap") != "wrap"], [],
            "a rule stops the banner wrapping, which shrinks its six lines into "
            "columns of 62, 37, 35, 34, 26 and 13px at 320 rather than widening the "
            "page -- narrower than the words in them")

    def test_the_meter_beside_the_hero_sets_no_floor_of_its_own(self):
        floors = [rule for rule in csslint.rules(self.assets()["styles.css"])
                  if ".progress" in rule.selector
                  and not sets_no_size(rule.value("min-width"))]
        # Below SC 1.4.10's 320px, and said plainly: with the banner fixed the page
        # already reflows at 320, where the meter's 260px floor fits the 264px it is
        # given. At 280 -- a Galaxy Fold's cover screen, or a 320px phone at 115%
        # page zoom -- the bar was laid out 260px wide inside a 256px card body, its
        # right edge 21px past the card's. The floor is dropped rather than lowered
        # because `flex: 1 1 260px` already asks for the same 260px as a basis, and a
        # basis and a floor differ in exactly one case: when there is not room, which
        # is the case the floor gets wrong. Measured after: 222px at 280, and
        # unchanged at 613px (1000) and 893px (1280). A lower floor is rejected too,
        # and not because 260 is the number: the meter's own minimum is 222px at the
        # narrowest width measured, so any floor under that never binds at any width
        # and is a constraint on the layout that the layout does not have.
        self.assertEqual(
            [(rule.where, rule.value("min-width")) for rule in floors], [],
            "the progress meter refuses to be narrower than a fixed width, so below "
            "that width it hangs out of the card it is in")

    def test_the_rules_paragraphs_and_what_holds_them_set_no_fixed_width_or_floor(self):
        # A cap only narrows, and a width or a floor beside it is what widens. Laid
        # out on the opened banner in headless Chrome over its DevTools pipe
        # (`/tmp/t7/scratch/CSS-A/probe/probe118.py`), `min-width: 60ch` beside the
        # 60ch cap made the paragraphs 429px in the 351px banner of a 375px screen
        # and the document 441px wide at 390, 375, 360 and 320; so did `width: 78ch`,
        # `min-inline-size: 60ch` and `inline-size: 78ch`, `width: 150%` and `width:
        # max-content`, and every one passed all 436 tests of this module.
        # On the disclosure the floor was the same 441, and `width: 78ch` on the
        # banner a document of 558 before the tap.
        #
        # So nothing from the root down to a paragraph is given a width or a floor
        # in a length, whatever the unit, and nor is a box the sheet makes inside
        # one of them: `.rules-more::details-content`, the box the disclosure keeps
        # what it hides in, with a floor of 60ch made the document 441 at 375 and
        # 320 before the tap (`probe118c.py` beside the probe). Not in `ch`, which a
        # reader's type makes wider: `min-width: 40ch`, a floor under the cap and the
        # same page at the sheet's 12.5px, was 366px under Chrome's 16px minimum font
        # and the document 378 at 375, 360 and 320. And not in px, em, rem or vw,
        # because which of those fits is a question about the face and the screen
        # that only laying the page out answers. A percentage of at most 100% is a
        # share of a box no wider than the page, though it can be a narrower one, and
        # `width: 100%` measured the same page; auto, none and a zero in any unit are
        # no width at all. Every screen the sheet names and not the phone's alone: a
        # floor written for dark mode was the same 441 at 375 in dark, and one inside
        # `@media not print`, which is every screen, 441 at 375 and 320.
        #
        # Each declaration is reported once, with every box it reaches, and not once
        # for each paragraph and screen it was found through.
        sheet = self.assets()["styles.css"]
        boxes = [""] + sorted({(found.group(1) or found.group(2)).lower()
                               for rule in csslint.rules(sheet)
                               for found in PSEUDO_ELEMENT.finditer(rule.selector)})
        refused: dict[str, list[str]] = {}
        for screen in self.screens():
            for classes in self.paragraph_classes():
                chain = self.paragraph_chain(classes)
                for depth, node in enumerate(chain, start=1):
                    who = "".join([node["el"]] + [f"#{node['id']}"] * bool(node["id"])
                                  + sorted("." + name for name in node["cls"]))
                    for box in boxes:
                        for prop in ("width", "min-width"):
                            value, rule = self.cascaded(chain[:depth], prop, screen,
                                                        box)
                            share = re.fullmatch(r"(\d*\.?\d+)%", value or "")
                            if sets_no_size(value) or share and float(
                                    share.group(1)) <= 100:
                                continue
                            reached = refused.setdefault(
                                f"{prop}: {value} from {rule.where}", [])
                            if who + "::" * bool(box) + box not in reached:
                                reached.append(who + "::" * bool(box) + box)
        self.assertEqual(
            refused, {},
            "something from the root down to a paragraph behind the rules tap, or a "
            "box the sheet makes inside one of them, is given a width or a floor in a "
            "length, so however narrow the phone, the page is at least that wide; "
            "each is listed with the boxes it reaches. What is at stake, with the "
            "banner opened at 375: min-width: 60ch or width: 78ch on the paragraphs "
            "made the document 441px, the same floor on the disclosure or its "
            "::details-content 441, width: 78ch on the banner 558 before the tap, and "
            "min-width: 40ch, under the cap, 378 under a 16px minimum font")

    def test_the_rules_paragraphs_and_what_holds_them_are_given_no_inline_style(self):
        # The test above reads the sheet, and a style on the element itself wins over
        # every rule in it that is not `!important`. Laid out the same way
        # (`probe118c.py`), the paragraphs built with `style: { 'min-width': '60ch' }`
        # made the opened document 441px wide at 375 and 320, and the disclosure built
        # with it 441 before the tap; `host.style.minWidth = '60ch'` on the banner,
        # or `style="min-width: 60ch"` on it in index.html, 429. Each of those passed
        # all 436 tests of this module, and `style="min-width: 400px"` on <body>, 401
        # at 375 and 400 at 320, failed only one that looks for `<body>` as written.
        # So what builds the banner, renderRulesBanner() and the hook() it is built
        # in, styles nothing, and index.html writes no style on <html>, <body> or the
        # banner. Any style and not only a width, because which
        # property widens a box is the question the test above answers for the
        # sheet, and here the sheet is where every one of them already is.
        found = []
        for fn in ("renderRulesBanner", "hook"):
            region = self.body(fn)
            for text, spelled in (
                    (jslint.code_only(region), r"\b(?:style|cssText)\b"),
                    (jslint.without_comments(region), r"['\"](?:style|cssText)['\"]")):
                found += [f"{fn}(): {region[hit.start():hit.start() + 40].strip()!r}"
                          for hit in re.finditer(spelled, text)]
        ident = re.search(r"hook\('([\w-]+)'", self.body("renderRulesBanner")).group(1)
        page = re.sub(r"<!--.*?-->", "", self.assets()["index.html"], flags=re.S)
        for opening in (r"<html\b[^>]*>", r"<body\b[^>]*>",
                        r"<\w+\s[^>]*\bid=\"%s\"[^>]*>" % re.escape(ident)):
            found += [f"index.html: {tag.group(0)}"
                      for tag in re.finditer(opening, page, re.I)
                      if re.search(r"\sstyle\s*=", tag.group(0), re.I)]
        self.assertEqual(
            found, [],
            "the rules banner, the paragraphs behind its tap, or what holds them are "
            "given a style of their own, which wins over the sheet and which no test "
            "of the sheet reads. What is at stake, at 375: min-width: 60ch set on the "
            "paragraphs by the script made the opened document 441px wide, on the "
            "disclosure 441 before the tap, and on the banner 429")

    def test_the_columns_that_have_to_stay_on_one_line_still_do(self):
        rules = csslint.rules(self.assets()["styles.css"])
        # 392 boxes at 360 are wider than the box that holds them, and that is
        # correct: their nearest scrolling ancestor is the table wrapper, which a
        # reader swipes and which the tests above gave a name and a tab stop. Only 8
        # had no such ancestor. What tells the 8 from the 392 apart is the wrapper,
        # so banning `white-space: nowrap` from the sheet would have fixed this one
        # by putting a doubles pair's two names on four lines in a 27px column.
        cells = [rule for rule in rules
                 if re.search(r"(^|[\s,])t[hd]([\s,]|$)", rule.selector)
                 and rule.value("white-space") == "nowrap"]
        self.assertTrue(cells, "table cells may now wrap, so a column is as wide as "
                               "its longest word rather than its longest value, and "
                               "the fix for 8 overflowing boxes was applied to 392 "
                               "that were already reachable")
        takes = [rule for rule in rules if ".table-wrap" in rule.selector
                 and (rule.value("overflow-x") or rule.value("overflow")) == "auto"
                 and not rule.at_rule]
        self.assertTrue(takes,
                        f"nothing takes the overflow of a table too wide to fit, so "
                        f"the cells that stay on one line widen the document "
                        f"instead: {[rule.where for rule in rules if '.table-wrap' in rule.selector]}")

    def test_the_rules_are_not_hidden_to_make_them_fit(self):
        # The banner costs 250px of a 640px screen once its lines wrap, so the cheap
        # next move is to stop showing it there. It is the only place the page says
        # which preset produced every number on it, and a club running the wrong one
        # can only find that out by reading it -- on the phone as much as the desk.
        # `:empty` is not this: a banner with nothing in it is hidden on purpose, and
        # for the accessibility-tree reason the rule above it gives.
        hidden = [rule for rule in self.banner_rules()
                  if ":empty" not in rule.selector
                  and (rule.value("display") == "none"
                       or rule.value("visibility") == "hidden")]
        self.assertEqual(
            [rule.where for rule in hidden], [],
            "the rules banner is hidden somewhere, so on those screens the scoring "
            "preset, set rule and match format behind every number on the page are "
            "not stated anywhere")


class TestAFrontPageCardBreaksALongSectionNameRatherThanWideningThePage(WebAssetCase):
    """A greyed card naming ``[competitions.tuesday_div_3_grade_b_summer_2027_old]``.

    A card with no results that reads like another now names its section, and a
    section's name is whatever a club typed: underscores are as common as hyphens
    and give a browser nowhere to break the line. Measured at 320px in headless
    Chrome (``/tmp/t4/scratch/w425/fix/chrome320.sh``, the iframe method the class
    above uses), the front page's document was 396px wide, the card's right edge at
    396 and its small print 350px across, where the same page without the name was
    320 with the card ending at 308: 76px of sideways scrolling on a phone, from one
    clause. With the small print breaking anywhere the document is 320 again.

    Unconditionally, as the banner's is, because the width is a property of a name
    and not of a screen. With the rule kept to the sheet's ``max-width: 700px``
    block, 320 was mended and 1024 was not: three cards to a row there, and the
    third card's small print 350px across inside a 282px box, the document 1053px
    wide. ``anywhere`` rather than ``break-word`` for the reason measured above, and
    measured again here: with ``break-word`` the document stayed 396px at 320, as
    with no rule at all, because only ``anywhere`` shrinks the width a line is laid
    out against. Rejected: breaking the name at its underscores in the markup, which
    changes the words a club searches the report and the file for.
    """

    def test_the_small_print_under_a_card_breaks_a_word_wider_than_the_card(self):
        rules = [rule for rule in csslint.rules(self.assets()["styles.css"])
                 if ".pick-facts" in rule.selector and rule.value("overflow-wrap")]
        anywhere = [rule for rule in rules if not rule.at_rule
                    and (rule.value("overflow-wrap") or "").startswith("anywhere")]
        self.assertTrue(anywhere,
                        "a card's small print breaks only at spaces, or only at some "
                        "widths, so a section named with underscores is as wide as "
                        "its name: 396px of a 320px screen, measured")
        self.assertEqual(
            [(rule.where, rule.value("overflow-wrap")) for rule in rules
             if not rule.value("overflow-wrap").startswith("anywhere")], [],
            "a rule gives a card's small print a wrap that breaks a long name on "
            "screen but still lays the card out as wide as the whole name")


class TestTheRulesAreNamedOnScreenAndExplainedBehindOneTap(WebAssetCase):
    """The banner that stopped the sideways scroll took 39% of the phone screen.

    Letting each of the banner's lines break is what took the document from 945px to
    360 on a 360px phone, and wrapping costs height: the banner went from 138px to
    250px at 360 and 269px at 320, and ``#view`` -- the first content on the page --
    started 635px down a 640px screen. Five pixels of dashboard above the fold.

    The class above filed that as editorial, to be paid back by shortening the closing
    advisory, and the diagnosis was wrong. Measured line by line with
    :file:`/tmp/bannerh.py`, the six lines of the 250 are 38, 19, 38, 38, 38 and 56px:
    the advisory everyone would have cut is the 56, and the 38s are the four rules
    whose *explanation* wraps their line to two. Cutting the advisory in half pays back
    28px of 113.

    So the banner is in two parts, and the split is the whole of this item. The five
    names are what a club recognises its own competition by -- "Scoring: games-bonus",
    "Sets: first-to-6" -- and they stay on screen always, one 19px line each. What each
    one *means*, and the sentence naming the five config keys that set them, are what a
    reader needs once, and they go behind one ``<details>``. Same probe, same tree,
    after::

        320x640  banner 269 -> 136px   chrome above the content 704 -> 571px
        360x640  banner 250 -> 115px (39% -> 18% of the screen)   635 -> 499px
        390x844  banner 250 -> 115px                             630 -> 495px
        700x900  banner 156 ->  75px                             430 -> 350px
        1280x900 banner  96 ->  55px                             291 -> 251px

    141px of dashboard at 360 where there were 5, the document still exactly the
    viewport's width at all five widths, and the lines now 19, 19, 19, 19, 38 and 19 --
    one per rule, the 38 the wrapping list of set names, and the 19 the disclosure.
    Opened it is 327px at 360 and the document is still 360 (:file:`/tmp/p322/banner.py`).

    Three alternatives were measured and rejected. Hiding the banner under some width
    is what the class above forbids and pins, and it is the same thing this does not
    do: the names -- the part a club checks -- are never behind the tap. One disclosure
    per rule puts five summary lines back where four explanation lines were, so the
    saving would be one line rather than 135px. And ``<details>`` rather than a scripted
    toggle, because a scripted one has to be given a role, a tab stop and a key handler
    the browser already has, and cannot be forced open for paper.

    Paper is where the split has its own defect. A closed ``<details>`` hides its
    content through ``::details-content``, whose ``content-visibility: hidden`` nothing
    inside it can override -- the same fact item 3.14 measured on the match cards. So
    the print block forces it back and drops the summary, and that claim is checked on
    real paper rather than argued: :file:`/tmp/p322/printcheck.py` prints the demo
    overview with Chrome and reads the sheets back through this project's own
    :mod:`matchcentre.pdf`, looking for each sentence over all five::

        variant     scoring explanation  unplayed explanation  advisory  "What these mean"
        shipped                       1                     1         1                  0
        plain                         0                     0         0                  1
        nosummary                     1                     1         1                  1

    ``plain`` is both print declarations deleted: not one of the three sentences
    reaches the sheet, and what does reach it is an instruction to press something that
    cannot be pressed. ``nosummary`` is the summary's rule alone deleted, which is the
    same instruction beside the words it points at. (The advisory reads as missing until
    the ``fi`` ligature is folded -- ``config.toml`` comes back out of a PDF spelled
    with U+FB01, which is a property of the sheet and not of the page.)

    Structural, like everything in this file: there is no JavaScript engine here, so
    every screen and paper number above was taken by hand with the probes named, and
    what these tests read is the source of the function and of the sheet.
    """

    def region(self) -> str:
        return self.body("renderRulesBanner")

    def balanced(self, code: str, opens: int) -> str:
        """What is inside the bracket at *opens*, brackets within it balanced.

        Every argument these tests read is an ``el()`` call holding further ``el()``
        calls, so a non-greedy match to the first ``)`` returns the innermost child
        and a greedy one runs to the end of the function.
        """
        depth = 0
        for at in range(opens, len(code)):
            if code[at] == "(":
                depth += 1
            elif code[at] == ")":
                depth -= 1
                if depth == 0:
                    return code[opens + 1:at]
        self.fail("a bracket in renderRulesBanner() never closes")

    def appends(self, code: str | None = None) -> list[tuple[str, str]]:
        """Every ``x.appendChild(y)`` in a stretch of the function, as ``(x, y)``.

        Which element a line is appended to is the whole question here -- the banner
        itself or the disclosure -- and it is a question about the receiver, which is
        why these are read as pairs rather than searched for as text.
        """
        code = self.region() if code is None else code
        found = [(match.group(1), self.balanced(code, match.end() - 1))
                 for match in re.finditer(r"(\w+)\.appendChild\(", code)]
        self.assertTrue(found, "nothing is appended to the banner at all, so these "
                               "tests are reading the wrong function")
        return found

    def host(self) -> str:
        """The local the banner element itself is held in, whatever it is called."""
        found = re.search(r"var (\w+) = hook\(", self.region())
        self.assertIsNotNone(found, "the banner no longer fetches its own element")
        return found.group(1)

    def local_holding(self, kind: str) -> str:
        """The local the function's one ``el('<kind>', ...)`` is assigned to.

        Read out of the code rather than named here, because the name of a local is
        the reader's to change and none of the claims below are about it.
        """
        found = re.search(r"var (\w+) = el\('%s'" % kind, self.region())
        self.assertIsNotNone(found, f"the banner builds no <{kind}> of its own")
        return found.group(1)

    def banner_class(self) -> str:
        """The class the banner's own element carries, from the hook that fetches it."""
        found = re.search(r"hook\('[\w-]+', '([\w-]+)'", self.region())
        self.assertIsNotNone(found, "the banner's hook no longer names a class")
        return found.group(1)

    def expand(self, argument: str) -> str:
        """An append's argument, a bare local replaced by everything put into it.

        The line carrying a rule's name is built into a local and then appended, so
        the text of the append alone says nothing about what is in it. Both halves of
        a local are gathered -- what it was built from and whatever was appended to it
        afterwards -- because which of those two an element's children go in is a
        style this file has no business pinning.
        """
        argument = argument.strip()
        if not re.fullmatch(r"\w+", argument):
            return argument
        built = re.search(r"var %s = ([^;]*);" % re.escape(argument),
                          self.region(), re.S)
        return (built.group(1) if built else "") + "".join(
            inner for who, inner in self.appends() if who == argument)

    def appended_to(self, who: str, code: str | None = None) -> list[str]:
        """Everything appended to a given local, each argument expanded."""
        return [self.expand(argument) for host, argument in self.appends(code)
                if host == who]

    def loop(self) -> str:
        """The body of the one loop over the rules, sliced by its own brackets.

        Anchored after the array of rules rather than on the first ``forEach`` in the
        function, so a loop added above it does not silently become the one these
        tests read.
        """
        region = self.region()
        rows = self.offset_of(region, "'Scoring'", "the rules the banner names")
        at = region.find(".forEach(", rows)
        self.assertGreater(at, 0, "nothing loops over the rules the banner names")
        return self.balanced(region, region.index("(", at))

    def label(self) -> str:
        """The expression a rule's name is labelled with, punctuation set aside.

        Taken from the code so that two places can be asked whether they label the
        same way without this file naming the loop's own variable. Trailing
        punctuation comes off, because ``bits[0] + ': '`` on the line and
        ``bits[0] + ' -- '`` inside the disclosure are the same label twice and which
        separator reads better is not a claim this file has any business making.
        ``<b>`` as well as ``<strong>``, for the same reason the sheet styles both.
        """
        found = re.search(r"el\('(?:strong|b)', \{ text: (.*?) \}\)", self.loop())
        self.assertIsNotNone(found, "the rules in the banner are no longer labelled")
        text = found.group(1).strip()
        core = re.match(r"(.+?)\s*\+\s*'[^']*'\s*$", text)
        return (core.group(1) if core else text).strip()

    def font_px(self, chain: tuple[dict, ...], screen: str) -> float:
        """The size the last element of *chain* is set in, in px, inherited from the
        nearest element in it that the sheet sizes."""
        size, rule = self.inherited(chain, "font-size", screen)
        if not size:
            self.fail("nothing in the sheet sizes the banner's type, so there is no em "
                      "here to measure a gap by")
        px = re.fullmatch(r"(\d*\.?\d+)px", size)
        self.assertIsNotNone(px, f"{rule.where} sets the type in {size!r} and not in "
                                 f"px, so there is no em here to measure a gap by")
        return float(px.group(1))

    def test_the_name_of_every_rule_is_on_the_screen_without_a_tap(self):
        # The half a club recognises its competition by, and the half that is worth
        # 39% of a phone screen only if it is the wrong preset. Item 3.12's tests
        # forbid hiding the banner by width; putting the names behind a disclosure is
        # the same page with a control on it, and this is where that is checked --
        # by which element the labelled line is appended to.
        #
        # The disclosure is itself appended to the banner, and what is inside it is not
        # on the screen -- which is the whole distinction this test exists to make, so
        # it is drawn here rather than left to the expansion above: a label found inside
        # the thing a reader has to open is the failure, not the pass.
        on_screen = [argument for argument in self.appended_to(self.host())
                     if "el('details'" not in argument]
        self.assertTrue(
            any(self.label() in argument for argument in on_screen),
            f"no line the banner itself shows carries a rule's label, so which "
            f"scoring preset produced every number on the page is something a reader "
            f"finds only after opening something: {on_screen}")

    def test_nothing_but_the_explanations_went_behind_the_tap(self):
        # The other direction, and the one a well-meant edit takes: a banner still
        # 115px tall because a name went in with the sentence explaining it. What may
        # be in there is the control's own label and the explanations, and the
        # explanations are known by the class the sheet styles them through.
        more = self.local_holding("details")
        inside = self.appended_to(more)
        self.assertTrue(inside, "the disclosure the banner builds is empty")
        stray = [argument for argument in inside
                 if self.klass("p") not in argument and "'summary'" not in argument]
        self.assertEqual(
            stray, [],
            f"something that is not an explanation is inside the disclosure, so it "
            f"is on the page only for a reader who opens it: {stray}")

    def test_what_each_name_means_is_one_tap_away_rather_than_a_line_of_its_own(self):
        # Four of the banner's six lines were 38px instead of 19 because each name
        # dragged its explanation onto the line with it. This is the half of the
        # split the height came from, so it is asserted where it happens: inside the
        # loop over the rules, the explanation goes to the disclosure.
        more = self.local_holding("details")
        loop = self.loop()
        self.assertTrue(self.appended_to(more, loop),
                        "the loop over the rules puts nothing in the disclosure, so "
                        "every explanation is back on the line beside its name -- "
                        "250px of a 640px phone, 39% of the screen")
        # And the disclosure reaches the banner, which is the one way this can be
        # written correctly and still put nothing on the page: an element built, filled
        # and never appended renders exactly as an element that was never built.
        self.assertTrue(any("el('details'" in argument
                            for argument in self.appended_to(self.host())),
                        "the disclosure is built and never put in the banner, so what "
                        "each of the rules means is on no page at all")
        loose = [self.expand(argument) for who, argument in self.appends(loop)
                 if who != more and self.klass("p") in self.expand(argument)]
        self.assertEqual(loose, [], f"an explanation is appended somewhere other "
                                    f"than the disclosure: {loose}")

    def test_an_explanation_read_on_its_own_says_which_rule_it_is_about(self):
        # "A bye or a washout is scored as nothing" is four rules away from the word
        # Unplayed once it is read behind a tap, and the rules are not in an order
        # anybody memorised. So each explanation is labelled again inside, with the
        # same label expression the visible line uses -- the same words, not a second
        # spelling of them.
        more = self.local_holding("details")
        inside = self.appended_to(more, self.loop())
        unlabelled = [argument for argument in inside if self.label() not in argument]
        self.assertEqual(
            unlabelled, [],
            f"an explanation inside the disclosure does not name the rule it "
            f"explains, so it is a sentence about one of five things: {unlabelled}")

    def test_one_tap_uncovers_all_of_it(self):
        # One disclosure per rule is the shape that suggests itself, and it puts five
        # 19px summary lines where the four 38px explanation lines were: 95px against
        # the 135px this saved at 360. The count is the fix, so the count is checked.
        built = re.findall(r"el\('details'", self.region())
        self.assertEqual(
            len(built), 1,
            f"the banner builds {len(built)} disclosures, so each one costs the page "
            f"a line of its own and the height they were meant to save comes back")

    def test_the_tap_is_a_control_the_browser_and_the_printer_both_understand(self):
        # A `<div>` with a click handler is the same thing to look at and a different
        # thing to use: no role, no tab stop, no key handler, and nothing `@media
        # print` can force open -- which is the next test. The label is checked for
        # having words in it because a marker with nothing beside it is a triangle a
        # reader is invited to guess about.
        more = self.local_holding("details")
        opener = [argument for argument in self.appended_to(more)
                  if "'summary'" in argument]
        self.assertTrue(opener, "the banner's disclosure has no <summary>, so what "
                                "opens it is whatever the browser does with a "
                                "<details> that has no label")
        self.assertRegex(opener[0], r"text: '[^']{3,}",
                         "the control that opens the explanations is unlabelled")

    def test_it_starts_closed_because_closed_is_the_whole_saving(self):
        # Open, the banner is 327px at 360 -- worse than the 250px this item was
        # filed against. Both spellings are checked: the attribute at build time and
        # the property set afterwards, which is what "open it for first-time readers"
        # looks like when somebody writes it.
        built = re.search(r"el\('details', \{([^}]*)\}", self.region())
        self.assertIsNotNone(built, "the disclosure is no longer built with el()")
        self.assertNotIn("open", built.group(1),
                         "the banner's disclosure is built open, so the page opens "
                         "with 327px of banner on a 640px phone")
        self.assertNotRegex(self.region(), r"\.open\s*=\s*true",
                            "the banner opens its own disclosure, so the split "
                            "saves nothing on the screen that needed it")

    def test_the_sentence_naming_the_settings_went_behind_the_tap_with_them(self):
        # Three lines of the old 250px, and the one sentence on the page a club acts
        # on -- but only once, and only if a preset is wrong. Inside the disclosure it
        # is a line a reader opens; the test that it still names every setting the
        # banner shows is in TestTheBannerAndTheDrawAgreeWithTheExporter, which reads
        # the whole function and so is indifferent to where the sentence sits.
        more = self.local_holding("details")
        advisory = [argument for argument in self.appended_to(more)
                    if "config.toml" in argument]
        self.assertTrue(advisory, "the sentence telling a club which file to change "
                                  "is not in the disclosure, so it is either back on "
                                  "the screen at three lines or gone")

    def test_the_explanations_reach_a_sheet_that_cannot_be_tapped(self):
        # Measured, not reasoned: with this declaration deleted, none of the three
        # sentences looked for appear on any of the five sheets. `content-visibility`
        # on `::details-content` is the only thing that reaches them -- a closed
        # `<details>` hides its content there, and no `display` inside it overrides
        # that, which is the fact item 3.14 measured on the match cards.
        cls = self.klass("details")
        forced = [rule for rule in self.print_rules()
                  if "::details-content" in rule.selector
                  and f".{cls}" in rule.selector
                  and rule.value("content-visibility") == "visible"]
        self.assertTrue(
            forced,
            f"nothing forces the banner's disclosure open for paper, so a printed "
            f"ladder carries the five rule names and not one word of what they mean: "
            f"{[rule.where for rule in self.print_rules() if f'.{cls}' in rule.selector]}")

    def test_the_sheet_does_not_carry_the_instruction_to_tap_it(self):
        # With the explanations forced onto the sheet and this rule deleted, "What
        # these mean" printed above them: a heading that is a control everywhere else
        # and is nothing here. Scoped to the banner, because the match cards are
        # <details> too and the print block keeps their summaries deliberately -- a
        # bare `summary { display: none }` would take a night's result off the sheet
        # with the card closed.
        cls, banner = self.klass("details"), self.banner_class()
        gone = [rule for rule in self.print_rules()
                if "summary" in rule.selector
                and (f".{cls}" in rule.selector or f".{banner}" in rule.selector)
                and (rule.value("display") == "none"
                     or rule.value("visibility") == "hidden")]
        self.assertTrue(
            gone,
            "the printed banner still says 'What these mean' above the words it "
            "means, which on paper is an instruction to press something")

    def test_the_words_are_never_hidden_only_the_control_that_opens_them(self):
        # The cheap way to buy the same height is to stop rendering the explanations,
        # and it is a one-line edit that looks like the fix: a `display: none` on the
        # disclosure reads as "this is the collapsed state". A `<details>` a reader
        # can open is the whole difference between qualifying a number and dropping
        # the qualification, and the summary's own rule on paper is the one exception
        # -- the words are there, the control is what has no meaning on a sheet.
        cls, detail = self.klass("details"), self.klass("p")
        blind = [rule for rule in csslint.rules(self.assets()["styles.css"])
                 if (f".{cls}" in rule.selector or f".{detail}" in rule.selector)
                 and "summary" not in rule.selector
                 and (rule.value("display") == "none"
                      or rule.value("visibility") == "hidden"
                      or rule.value("content-visibility") == "hidden")]
        self.assertEqual(
            [rule.where for rule in blind], [],
            "what each rule means is hidden rather than disclosed, so on those "
            "screens or sheets the page states its presets and explains none of them")

    def test_the_control_does_not_move_out_from_under_the_finger_that_taps_it(self):
        # The banner is a wrapping flex row and the disclosure is one more item in
        # it, so without a full basis it sits at the end of whichever line has room.
        # Measured with `/tmp/p322/flexcheck.py`: at 700 the summary shares the last
        # name's line (both tops 151px) and takes its own line the moment it is opened
        # (top 172), and at 1280 the same, 89 to 109 -- the label moves 21px under the
        # pointer that pressed it and the explanations open where it used to be. The
        # price is 20px of banner at those widths and nothing at all on a phone: 115px
        # at 360 and 136 at 320 either way, because there the names already fill their
        # lines. Any of the three spellings that give it the line will do.
        cls = self.klass("details")
        own_line = [rule for rule in csslint.rules(self.assets()["styles.css"])
                    if f".{cls}" in rule.selector and "summary" not in rule.selector
                    and not rule.at_rule
                    and ("100%" in (rule.value("flex") or "")
                         or rule.value("flex-basis") == "100%"
                         or rule.value("width") == "100%")]
        self.assertTrue(
            own_line,
            "the disclosure is laid out as one more item on the banner's flex line, "
            "so it sits beside the last rule name and moves to its own row when a "
            "reader opens it")

    def test_what_the_rules_mean_is_read_at_a_measure_and_not_across_the_screen(self):
        # The banner is a flex row as wide as the page, and nothing else in it ends a
        # line. Measured on the opened demo banner (`/tmp/t4/scratch/w421/measure422.py`
        # for the boxes, `measure422e.py` beside it for the words) with this cap
        # deleted, the advisory is one line of 161 characters running 933px, at 1280
        # and at 1920 alike, and with `draws = false` set the draws explanation above
        # it is another, of 143 characters and 825px. Capped at 60ch, the advisory is
        # three lines of at most 69 characters and the draws explanation two of at most
        # 72, at every desktop width and at 700; a phone's banner is narrower than the
        # cap, so there it changes nothing (327px opened at 375 and 360 either way). A
        # line's characters are the ones it shows, not the space it breaks at.
        #
        # In `ch` because the cap is a count of characters and the reader decides how
        # big a character is. With Chrome's minimum font size at 16px, 429px -- the
        # width 60ch comes to at the sheet's 12.5px -- puts the scoring and format
        # explanations on two lines each, the format's second line `to 6)`, for 48px
        # more banner, where 60ch keeps every line as it was; at 20px it is 90px more
        # (`/tmp/t4/scratch/y455/pipe455.py`, which drives headless Chrome over its
        # DevTools pipe so that 375 is a 375px viewport).
        #
        # No wider than 60, because a `ch` is not a character. It is the width of a
        # zero, 7.16px here, and at 78ch -- the cap this test let through while it
        # asked for 40 to 80 -- the demo's advisory is a line of 92 characters and its
        # unplayed explanation one of 84, over the 80 of WCAG 1.4.8. The banner's text
        # is narrower than a zero: a line of a format's slot labels, `Singles 1,
        # Singles 2, ...`, is as little as 0.71ch a character in each of the four faces
        # measured (Noto Sans, which system-ui is here; Liberation Sans and Nimbus
        # Sans, which have Arial's and Helvetica's metrics; DejaVu Sans), so no measure
        # in `ch` is 80 characters by arithmetic, and the widest that keeps to 80 was
        # found by laying the paragraphs out. At 60ch every paragraph laid out does, in
        # all four faces: the 100 of every scoring preset with and without its draw,
        # set rule, preset format under each set rule, unplayed mode, the draws
        # sentence and the advisory, at most 79 characters; the 1022 formats of one to
        # nine slots, each under each of the eight set rules, at most 79; and formats
        # of ten to sixteen slots, all singles, all doubles or alternating, at most 80
        # (`/tmp/t4/scratch/y455/measure455.py`). At 61ch, in DejaVu Sans, four singles
        # under either tiebreak rule are a line of 82, and in Noto Sans twelve singles
        # under `pro-10` are 81; at 65ch eighteen of the 100 are over 80, up to 85,
        # and in Liberation Sans the advisory itself is 82 -- which is why rewording
        # the demo's two long sentences, the other way to 80, would not have been
        # enough either.
        #
        # No narrower than 40, because a cap with a digit or a point out of place is
        # still a number in `ch` (`/tmp/t4/scratch/w421/fixup/measure422g.py` and
        # `measure422h.py`): at 7.8ch the advisory is 22 lines of at most 10 characters
        # and the opened banner 1092px at 1280. At 40ch it is 286px, in lines of at
        # most 48 characters: about what a 320px phone gives the same paragraphs,
        # 296px or 41ch, in lines of at most 52. Under 40, a desktop reads the caveat
        # in a column narrower than the narrowest phone the page is measured on.
        #
        # Read through the cascade, per screen the sheet names, from the root down to
        # the paragraph. Another rule can reach these paragraphs, and whether it wins
        # is specificity before order: `.rules-more p { max-width: none; }` lifts the
        # cap whether it is written before `.rules-detail` or after it (0,1,1 against
        # 0,1,0), 173px of opened banner either way and the advisory one line of 161.
        # A class that only starts the same, `.muted-note`, reaches none of them, and
        # with it added the opened banner measured the same to the pixel.
        for screen in self.screens():
            for classes in self.paragraph_classes():
                width, rule = self.cascaded(self.paragraph_chain(classes), "max-width",
                                            screen)
                cap = re.fullmatch(r"(\d*\.?\d+)ch", width or "")
                with self.subTest(screen=screen or "every screen",
                                  paragraph=" ".join(classes)):
                    self.assertTrue(
                        cap and 40 <= float(cap.group(1)) <= 60,
                        f"<p class=\"{' '.join(classes)}\"> behind the tap gets "
                        f"{f'max-width: {width} from {rule.where}' if rule else 'no max-width from any rule'}"
                        f"{' ' + screen if screen else ''}, where a measure from 40ch to "
                        f"60ch is asked for. What is at stake, from the demo season "
                        f"at 1280: uncapped, the advisory is one line of 161 "
                        f"characters; at 78ch it is a line of 92, and at 61ch a "
                        f"format can be a line of 82, where 80 is the most; at 7.8ch "
                        f"it is 22 lines of 10; and a cap in px re-wraps when a "
                        f"reader's minimum font size is larger")

    def test_nothing_else_the_paragraphs_are_given_lets_a_line_past_the_cap(self):
        # A max-width is one of five things that decide how many characters a line
        # holds, and the test above reads only that one. Each of the other four took
        # a line past 80 with the 60ch still in place, measured on the opened banner
        # at 1280 with the probe above (the variants are in `measure455.py` beside
        # it, and the lines as a reader sees them, paragraphs together, in
        # `/tmp/t4/scratch/y455/fix/visual.py`):
        #
        # - A min-width wider than the cap wins over it. `min-width: 78ch` beside the
        #   60ch is the old page to the character, the advisory 92 and 24 of the 100
        #   over 80, and at 375 a document 570px wide. `min-width: max-content` is the
        #   advisory's one line of 161, and a document of 945 at 375.
        # - The lines a reader sees keep to the cap only while each paragraph is a
        #   block-level box of its own. `display: inline` or `contents` runs the
        #   paragraphs on into lines of 201 characters, 210 under `draws = false`, and
        #   `inline-block` or `inline-flex` sets them side by side, in lines of 100 and
        #   155, or 156 and 206.
        # - `white-space: nowrap` or `pre`, or `text-wrap: nowrap`, refuses the break:
        #   the advisory is one line of 161 again, and the document 945px at 375.
        # - A negative spacing puts more characters into each ch, and the 60ch was
        #   found at each face's own. `letter-spacing: -0.06em` puts the demo's
        #   advisory at 82 and 22 of the 100 over 80, up to 89. Even -0.01em puts two
        #   of them at 82, and `word-spacing: -0.2em` puts 16 over, up to 86.
        #
        # The last two are inherited, so they are read from the nearest element the
        # sheet gives one: `white-space: nowrap` on `.rules-more`, the disclosure, is
        # the same 161, and the letter-spacing on `.rules-banner` the same 82. What
        # is let through measured the same page or shorter lines: a floor no wider
        # than the cap (`min-width: 40ch`, the same page at 1280, 700 and 375), which
        # the phone class refuses for what it does under a larger font, as it
        # refuses every floor, and a zero in any unit, `0px` as much as `0`;
        # `display: flow-root`, `white-space: pre-line`, `white-space: nowrap` with a
        # `text-wrap: balance` after it, which breaks the line again, and a spacing
        # that only opens the text out (`letter-spacing: 0.02em`, lines of at most 69
        # and a banner of 248px). So is `display: table`, the same page, and `flex` or
        # `grid`, which set the bold label apart from its sentence but keep every line
        # of the 100 to 78. `display: none` hides the words, which the test that they
        # are never hidden refuses.
        for screen in self.screens():
            for classes in self.paragraph_classes():
                chain = self.paragraph_chain(classes)
                width, _ = self.cascaded(chain, "max-width", screen)
                cap = re.fullmatch(r"(\d*\.?\d+)ch", width or "")
                undone = []
                floor, rule = self.cascaded(chain, "min-width", screen)
                under = re.fullmatch(r"(\d*\.?\d+)ch", floor or "")
                if not sets_no_size(floor) and not (
                        cap and under and float(under.group(1)) <= float(cap.group(1))):
                    undone.append(f"min-width: {floor} from {rule.where}, which wins "
                                  f"over the max-width")
                box, rule = self.cascaded(chain, "display", screen)
                if box not in (None, "block", "flow-root", "list-item", "flex", "grid",
                               "table", "none"):
                    undone.append(f"display: {box} from {rule.where}, which is not a "
                                  f"block-level box of its own, so the paragraphs run "
                                  f"on or sit side by side")
                mode, rule = self.inherited(chain, "text-wrap-mode", screen)
                if mode == "nowrap":
                    undone.append(f"no line break, from {rule.where}")
                for spacing in ("letter-spacing", "word-spacing"):
                    value, rule = self.inherited(chain, spacing, screen)
                    if value and value.startswith("-"):
                        undone.append(f"{spacing}: {value} from {rule.where}, which "
                                      f"puts more characters into each ch")
                with self.subTest(screen=screen or "every screen",
                                  paragraph=" ".join(classes)):
                    self.assertEqual(
                        undone, [],
                        f"<p class=\"{' '.join(classes)}\"> behind the tap is capped "
                        f"at {width or 'nothing'}{' ' + screen if screen else ''}, and "
                        f"something else it is given lets a line run past the cap. "
                        f"What is at stake, from the demo season at 1280 with the "
                        f"60ch in place: a min-width of 78ch gives back the "
                        f"advisory's line of 92, display: inline runs the paragraphs "
                        f"on into lines of 201, nowrap makes the advisory one line of "
                        f"161, and a letter-spacing of -0.06em a line of 82")

    def test_the_lines_behind_the_tap_do_not_bring_a_paragraphs_gap_with_them(self):
        # They are <p>s, and a paragraph the sheet says nothing about is given the
        # browser's 1em above and below. Measured with the same probe and this margin
        # deleted, the five paragraphs stand 31px apart instead of 24 and the opened
        # banner is 280px instead of 230 at 1280, 300 instead of 250 at 700 and 377
        # instead of 327 at 360 -- 50px at every width, a fifth of the banner, spent
        # between five paragraphs of one caveat. Both edges, because a top margin alone
        # collapses into the browser's bottom one: `margin-top: 5px` on its own measured
        # 272. Under an em and not a number, because how close they sit is the sheet's
        # to choose: no margin at all measured 205px, each paragraph still opening on
        # its rule's name in bold. Read through the cascade, as the measure is above:
        # `.rules-more p { margin: 1em 0; }` after the rule measured 280px at 1280,
        # the same as no margin, while `.rules-detail-x { margin: 1em 0; }` and
        # `.health .muted { margin: 0 0 1em; }` reach none of these paragraphs, and
        # with either added the opened banner measured the same as without it.
        def under_an_em(value: str | None, em: float) -> bool:
            found = re.fullmatch(r"(\d*\.?\d+)(px|em)?", value or "")
            if not found:
                return False
            if found.group(2) is None:
                return float(found.group(1)) == 0
            return float(found.group(1)) < (1 if found.group(2) == "em" else em)

        for screen in self.screens():
            for classes in self.paragraph_classes():
                chain = self.paragraph_chain(classes)
                em = self.font_px(chain, screen)
                kept = []
                for side in ("top", "bottom"):
                    value, rule = self.cascaded(chain, f"margin-{side}", screen)
                    if not under_an_em(value, em):
                        kept.append(f"margin-{side}: {value} from {rule.where}" if rule
                                    else f"margin-{side}: the browser's 1em, which no "
                                         f"rule overrides")
                with self.subTest(screen=screen or "every screen",
                                  paragraph=" ".join(classes)):
                    self.assertEqual(
                        kept, [],
                        f"<p class=\"{' '.join(classes)}\"> behind the tap keeps a "
                        f"paragraph's gap of an em or more"
                        f"{' ' + screen if screen else ''}, where the type is {em:g}px. "
                        f"What is at stake, from the demo season: the browser's 1em on "
                        f"both edges is 50px more opened banner at 360, 700 and 1280, "
                        f"spent between the lines of one caveat")

    def test_no_paragraph_behind_the_tap_ends_on_a_word_or_two_of_its_own(self):
        # 4.119. The cap ends a line where the next word will not fit, so a paragraph's
        # last line is as short as whatever was left over. Measured on the opened banner
        # with /tmp/t6/scratch/m15/p119/pipe119.py (4.55's probe over the DevTools pipe,
        # the paragraphs generated from this tree): under `draws = false` the scoring
        # explanation's second line is `sets`, at 1280 and at 700, and at 375 the demo's
        # format ends on `6)`. 24 of the 100 paragraphs the banner can show end on a
        # line of 15 characters or fewer, the shortest 4, as do 391 of the 4080 formats
        # of one to eight slots, the shortest 2, and 44 of the 4320 longer ones, 3.
        # With `text-wrap: balance`, none of them does, in any of the four faces, at
        # 1280, 700 or 375; each keeps its number of lines, so the opened banner is the
        # 230px and 291px it was at 1280, 250 and 312 at 700 and 327 and 389 at 375, and
        # no face's longest line is longer than it was: in Noto Sans 75 in the 100,
        # where it was 79, and 80 past eight slots, where it was 80.
        # Rejected: `text-wrap: pretty`, which moves a word down onto the shortest
        # (`sets` becomes `then sets`) and leaves the 24, 391 and 44 under 16, and a
        # rewording, which reaches three sentences and none of the formats.
        #
        # Read through the cascade and inherited, as the wrap mode is above: balance on
        # the disclosure is balance on the paragraphs, and `.rules-more p { text-wrap:
        # pretty; }` after the rule wins on specificity and gives back the short lines.
        for screen in self.screens():
            for classes in self.paragraph_classes():
                style, rule = self.inherited(self.paragraph_chain(classes),
                                             "text-wrap-style", screen)
                with self.subTest(screen=screen or "every screen",
                                  paragraph=" ".join(classes)):
                    self.assertEqual(
                        style, "balance",
                        f"<p class=\"{' '.join(classes)}\"> behind the tap is wrapped "
                        f"{f'{style} from {rule.where}' if rule else 'as the browser wraps it, no rule balancing its lines'}"
                        f"{' ' + screen if screen else ''}. What is at stake, from the "
                        f"demo season: the draws = false scoring explanation ends on "
                        f"`sets` alone at 1280, and 24 of the 100 paragraphs the "
                        f"banner can show on a line of 15 characters or fewer")

    def test_the_balance_is_given_to_those_paragraphs_and_to_nothing_else_on_the_page(self):
        # 4.119 as well. The test above reads the balance as the paragraphs inherit it,
        # so `body { text-wrap: balance; }` in place of the rule passed it and balanced
        # the whole page. Measured with that rule added to the demo's sheet
        # (/tmp/t6/scratch/m15/fix/page119.py, the lines of every shown element outside
        # the disclosure in the six views, at 1280, 700 and 375): 66 of them break their
        # lines another way, the ladder's caption at 1280, and at 375 eleven on the front
        # page, the topbar's club line and the health strip's summary among them. The
        # balance was measured on none of those. So every selector of a rule that
        # balances lines names the disclosure or its paragraphs; `body`, `p`, `.muted`
        # and the banner are each more of the page than was measured.
        #
        # 4.214 measured ten kinds of those blocks one at a time, and the class below
        # gives each the wrap it was measured under, by the one selector it was measured
        # through. So a selector may also be one of those, written as the class below
        # writes it and with its wrap; `.health` for `.health summary`, `.view-head p`
        # for `#view .view-head p`, `.rules-banner > *` for `.rules-banner > span` and
        # balance for the tile's foot are still refused.
        #
        # And the browser's wrap is read too, where it lands on a kind: the class below
        # reads each kind through a chain that leaves out what sits between the host
        # and the block, so `.tiles .foot { text-wrap: wrap; }`, through the tiles'
        # grid, gave the foot back its `2` and passed it. A rule whose selector ends on
        # an element a kind's wrap is given to or inherited through, whatever the
        # selector says above that, is refused unless it is the kind's own.
        own = {self.klass("details")} | set.intersection(
            *(set(classes) for classes in self.paragraph_classes()))
        blocks = TestEachKindOfBlockThatEndedOnAWordOrTwoIsWrappedAsItWasMeasured.BLOCKS
        measured = {(selector, style) for _, selector, style, *_ in blocks}
        # The last compound of a selector, as `selects` reads one.
        last = re.compile(r"(?:[^\s>+~()]|\((?:[^()]|\([^()]*\))*\))+")
        carried = []
        for what, selector, style, _, built, _ in blocks:
            nodes = [self.compound(compound) for _, compound in built]
            subject = last.findall(selector)[-1]
            # From the element the kind's own selector reaches down to the block,
            # which inherits the wrap from it.
            first = next((index for index, node in enumerate(nodes)
                          if self.selects(subject, (node,))), 0)
            carried.append((what, selector, style, nodes[first:]))
        for rule in csslint.rules(self.assets()["styles.css"]):
            styles = [value for name, value, _ in longhands(rule.declarations)
                      if name == "text-wrap-style"]
            if not styles:
                continue
            for selector in rule.selector.split(","):
                # A class inside `:not()` is one the rule is not for, so what is in
                # brackets is taken off, innermost first.
                bare = selector
                while re.search(r"\([^()]*\)", bare):
                    bare = re.sub(r"\([^()]*\)", "", bare)
                if (own & set(re.findall(r"\.([\w-]+)", bare))
                        or (" ".join(selector.split()), styles[-1]) in measured):
                    continue
                subject = (last.findall(selector) or [""])[-1]
                # A pseudo-element is a box inside the element, as `cascaded` reads it.
                reached = [] if PSEUDO_ELEMENT.search(subject) else [
                    f"{what}, measured {style} through `{theirs}`"
                    for what, theirs, style, nodes in carried
                    if any(self.selects(subject, (node,)) for node in nodes)]
                with self.subTest(rule=rule.where, selector=selector.strip()):
                    if styles[-1] != "auto":
                        self.fail(
                            f"{rule.where} wraps {selector.strip()!r} {styles[-1]}, "
                            f"which is more of the page than the paragraphs behind the "
                            f"tap and the blocks measured with that wrap: balanced "
                            f"from `body`, 66 elements outside the banner break their "
                            f"lines another way across the demo's six views, where "
                            f"the kinds measured rewrap 33")
                    self.assertEqual(
                        reached, [],
                        f"{rule.where} gives {selector.strip()!r} the browser's wrap, "
                        f"and what that ends on is where a kind's wrap is given or "
                        f"inherited, whatever is above it on the page. What is at "
                        f"stake, from the demo season: through the tiles' grid, "
                        f"`.tiles .foot` gives a tile's foot back its `2` and the "
                        f"class below does not see it, its chain having no grid")


class TestEachKindOfBlockThatEndedOnAWordOrTwoIsWrappedAsItWasMeasured(WebAssetCase):
    """Outside the rules banner, blocks on the demo's pages ended on a word or two.

    Read line by line in headless Chrome over its DevTools pipe
    (/tmp/t7/scratch/CSS-B/probe/probe214c.py, 4.119's page119.py reading every line
    under each element too), every shown element with text of its own in the demo's
    six views at 1280, 700 and 375: 32 ended on a last line of 15 characters or fewer,
    29 of them at 375. A tile's foot ended on ``2`` (``Brindle Magpies, round`` /
    ``2``), the health strip's summary on ``club's word`` in every view, the match
    page's heading on ``Wombats``, a chart's line on ``axis.``, a match's facts on
    ``— · 1 note``, and at 700 the matches page's lead on ``been.``. 4.119 balanced the
    paragraphs behind the tap and nothing else, because nothing else was measured.

    Each kind was measured on its own, balanced and pretty, through the one selector
    below that reaches it (/tmp/t7/scratch/CSS-B/probe/variants214.py), and then all
    together: on the demo, under ``draws = false``, with the ladder complete, on the
    page a missing season.json shows, on a two-season site's views, picker and
    careers page, and on the demo given nine teams and a declared draw with byes and
    fixtures to come (/tmp/t7/scratch/CSS-B/fx/nine, probed with fx/probe/probe214d.py).
    With the wraps below, the demo's six views end on 10 such lines where they ended on
    32, 1 at 700 and 9 at 375, and the nine teams' on 10 where they ended on 33. 33
    elements of the demo's break their lines another way, each in as many lines as it
    had, no view on any site is a pixel taller or wider, and no line newly ends at a
    hyphen inside a word or leaves one of 15 or fewer between two others.

    The banner's lines are reached as they were measured, ``> span``: ``> *`` laid them
    out the same, but reached the disclosure too and balanced 4.119's paragraphs a
    second time, so that their own rule could go and nothing would fail. ``.match
    .meta`` reaches a bye's line and a fixture's as well as a played match's facts; at
    375 the nine teams' byes ended on ``match played``, and balanced they do not, and
    the fixtures' lines are one line either way.

    Left as they are, because each is as even as its words allow or is better so:

      a tile's foot    pretty and not balance. Balanced, ``Brindle Kookaburras`` / ``v
                       Brindle`` / ``Magpies, round 2`` broke a team's name to leave 9
                       characters between two lines of 19 and 16; pretty gives ``v
                       Brindle Magpies,`` / ``round 2``. Each of the five it leaves at
                       15 or fewer is within 3 of the most even split into as many
                       lines, and three are that split
      the match's h1   balanced, though on the demo it is still 15: ``Brindle Magpies
                       v`` / ``Brindle Wombats``, the most even, where it was ``Brindle
                       Magpies v Brindle`` / ``Wombats``. Given nine teams it ended on
                       ``Galahs``, and balanced it is ``Brindle Bunyips`` / ``v Brindle
                       Galahs``
      the table key    nothing. Balanced it cleared 3 and broke inside ``D W-`` /
                       ``L``, ``Games for–`` / ``against`` and ``won–`` / ``lost`` at
                       700 and 375, which nothing on the base does, and pretty cleared
                       1 and split ``W —`` from ``Matches won``
      a card's Source  nothing. A file's path, broken at a hyphen whichever wrap it is
                       given
      careers leads    nothing, which is why the view heads are reached through
                       ``#view``. Balanced, a two-season site's careers page cleared
                       its two, ``ranking.`` at 1280 one of them, but that is one page
                       of one site, and a one-season site's lead was not read at all

    Rejected: ``body { text-wrap: balance; }``, which rewraps 66 of the demo's
    elements and was measured on none of them, and pretty across the page, which
    4.119 measured leaving its short lines.
    """

    # Every function that writes a view's head into #view.
    VIEWS = ("renderOverview", "renderTeam", "renderPlayer", "renderMatches",
             "renderMatch", "renderPlayers", "renderMissing", "showLoadFailure")

    # What each kind is, the selector the sheet reaches it through and the wrap it
    # gives it there, the element index.html holds it in, the elements the script
    # builds it as, from the outermost down, each with every function that builds
    # it, and what was shown without the wrap.
    BLOCKS = (
        ("the rules banner's lines", ".rules-banner > span", "balance", "rules-banner",
         ((("renderRulesBanner",), "span"),),
         "on the demo at 375 the line of sets played ends on `Doubles 2` in every view"),
        ("the health strip's summary", ".health summary", "balance", "health",
         ((("provenanceBlock",), "details.note"), (("provenanceBlock",), "summary")),
         "on the demo at 375 it ends on `club's word` in every view"),
        ("the ladder's note", ".ladder-note", "balance", "view",
         ((("ladderNote",), "p.ladder-note.ladder-note--incomplete"),
          (("ladderNote",), "span")),
         "on the demo at 700 and at 375 the front page's ends on `then name.`"),
        ("a view's heading", "#view .view-head h1", "balance", "view",
         ((VIEWS, "div.view-head"), (VIEWS, "h1")),
         "on the demo given nine teams, at 375 the match page's ends on `Galahs`"),
        ("a view's lead", "#view .view-head p", "balance", "view",
         ((VIEWS, "div.view-head"), (VIEWS, "p")),
         "on the demo at 700 the matches page's ends on `been.`"),
        ("a tile's foot", ".tile .foot", "pretty", "view",
         ((("tile",), "div.card.tile"), (("tile",), "div.foot")),
         "on the demo at 375 two of the front page's end on `2` and `1`"),
        ("a chart's line under its title", ".chart-card .sub", "balance", "view",
         ((("chartCard", "h2hGridCard"), "figure.card.chart-card"),
          (("chartCard", "h2hGridCard"), "figcaption"),
          (("chartCard", "h2hGridCard"), "p.sub")),
         "on the demo at 375 the front page's ends on `axis.`"),
        ("a match's line of facts", ".match .meta", "balance", "view",
         ((("matchCard",), "details.match"), (("matchCard",), "summary"),
          (("matchCard",), "span.meta")),
         "on the demo at 375 a cancelled match's ends on `— · 1 note`"),
        ("a bye's or a fixture's line", ".match .meta", "balance", "view",
         ((("byeRow", "scheduledRow"), "div.match"),
          (("byeRow", "scheduledRow"), "div.summary"),
          (("byeRow", "scheduledRow"), "span.meta")),
         "on the demo given nine teams, at 375 six byes end on `match played`"),
        ("a table's caption", "caption .caption-text", "balance", "view",
         ((("table",), "table"), (("tableCaption",), "caption"),
          (("tableCaption",), "span.caption-text")),
         "on the demo at 375 two on the player page end on `together.` and `player.`"),
    )

    def chain(self, host: str, built) -> tuple[dict, ...]:
        """Where a block sits, from the root down: ``<html>`` and ``<body>`` as
        index.html writes them, the element with the id *host* in it, and each
        compound of *built*.

        Not everything between the host and the block is in it: a tile's grid and
        the card a list of matches sits in are left out, so a rule that reaches the
        block through one of them is out of this chain's sight, and so out of the
        wrap test's. 4.119's scope test above reads those instead: a rule giving a
        wrap, the browser's too, whose selector ends on an element a kind's wrap is
        given to or inherited through, whatever the selector says above it, is
        refused unless it is the kind's own or names the rules banner's disclosure
        or its paragraphs."""
        page = re.sub(r"<!--.*?-->", "", self.assets()["index.html"], flags=re.S)

        def written(pattern: str, what: str) -> dict:
            tag = re.search(pattern, page)
            self.assertIsNotNone(tag, f"index.html has no {what} for a block to sit in")
            named = re.search(r'\bclass="([^"]*)"', tag.group(0))
            ident = re.search(r'\bid="([^"]*)"', tag.group(0))
            return {"el": tag.group(1).lower(),
                    "cls": set(named.group(1).split()) if named else set(),
                    "id": ident.group(1) if ident else ""}
        return (written(r"<(html)\b[^>]*>", "<html>"),
                written(r"<(body)\b[^>]*>", "<body>"),
                written(r'<(\w+)\s[^>]*\bid="%s"[^>]*>' % re.escape(host), f"#{host}"),
                *(self.compound(compound) for _, compound in built))

    def built_as(self, function: str, element: str) -> list[set[str]]:
        """The classes *function* builds each ``<element>`` with, one set for each
        class it can carry.

        Read from anywhere in the object of attributes written in the call, not only
        as its first key, and each string of a ternary as a class of its own, so
        ``fx.card_missing ? 'meta missing' : 'meta'`` is two. An element built with
        no attributes, or none naming a class, has none. One whose attributes are
        not an object written in the call, or whose class is joined together from
        parts, cannot be read here and gives nothing.
        """
        region = self.body(function)
        said, code = jslint.without_comments(region), jslint.code_only(region)
        found = []
        for call in re.finditer(r"(?<![\w$])el\(\s*'%s'\s*" % re.escape(element), said):
            at = call.end()
            if code[at] == ")":
                found.append(set())
                continue
            if code[at] != ",":
                continue
            at += 1
            while code[at].isspace():
                at += 1
            if code[at] != "{":
                continue
            close = closing_bracket(code, at)
            # How deep each character of the object is, as paragraph_classes reads
            # it, so that a `class` in a nested `style: { ... }` is not the element's.
            depth, level = [], 0
            for char in code[at:close + 1]:
                level += char in "([{"
                depth.append(level)
                level -= char in ")]}"
            keys = [key for key in re.finditer(
                        r"(?<![\w$'\"])['\"]?class['\"]?\s*:", said[at:close + 1])
                    if depth[key.start()] == 1]
            if not keys:
                found.append(set())
                continue
            start = end = at + keys[0].end()
            while end < close and not (code[end] == "," and depth[end - at] == 1):
                end += 1
            if "+" not in code[start:end]:
                found += [set(text.split())
                          for text in re.findall(r"'([^']*)'", said[start:end])]
        return found

    def test_each_kind_is_wrapped_as_it_was_measured_on_every_screen(self):
        # Read through the cascade and inherited, as 4.119's paragraphs are: a rule
        # after the one that gives the wrap, or a more specific one, gives the short
        # line back, and so does the wrap written for a phone alone, which leaves 700
        # and 1280 with the matches page's `been.` and the ladder's `then name.`.
        for what, _, style, host, built, stake in self.BLOCKS:
            chain = self.chain(host, built)
            for screen in self.screens():
                got, rule = self.inherited(chain, "text-wrap-style", screen)
                how = (f"{got} from {rule.where}" if rule else
                       "as the browser wraps it, no rule giving it a wrap")
                with self.subTest(block=what, screen=screen or "every screen"):
                    self.assertEqual(
                        got, style,
                        f"{what} is wrapped {how}"
                        f"{' ' + screen if screen else ''}, where {style} is what "
                        f"was measured. What is at stake: {stake}")

    def test_each_kind_is_built_where_the_test_above_looks_for_it(self):
        # The chains above are this file's, so a class renamed in app.js leaves them
        # true of a page nobody builds: with `foot` renamed `tile-foot` in tile(), no
        # rule reaches the foot and the test above passes. So each element of each
        # chain is one that every function building the kind builds, with the classes
        # the chain gives it: h2hGridCard's line as well as chartCard's, a fixture's
        # as well as a bye's, and every view's head. Where it sits in the function is
        # not read; the probe laid the page out.
        for what, _, _, _, built, _ in self.BLOCKS:
            for functions, compound in built:
                want = self.compound(compound)
                for function in functions:
                    made = self.built_as(function, want["el"])
                    with self.subTest(block=what, element=compound, function=function):
                        self.assertIn(
                            want["cls"], made,
                            f"{function}() builds no <{want['el']}> with the classes "
                            f"{sorted(want['cls']) or 'none'}, which is where {what} "
                            f"was measured, so the sheet's wrap for it may reach "
                            f"nothing: it builds <{want['el']}> with "
                            f"{[sorted(c) for c in made]}")

    def test_every_line_the_banner_builds_is_one_its_rule_reaches(self):
        # The sheet reaches the banner's lines as `.rules-banner > span`, which is how
        # they were measured, so a line built as a <div> keeps the browser's wrap and
        # at 375 the line of sets played ends on `Doubles 2` again. The test above
        # cannot see it: renderRulesBanner still builds a <span>, for each rule's line.
        # So every element the function puts in the banner is read, through the
        # variable or the function that builds it, and each is a <span> or the
        # disclosure, whose paragraphs 4.119 balances by their own rule.
        (_, selector, _, _, _, _), = [block for block in self.BLOCKS
                                      if block[3] == "rules-banner"]
        line = self.compound(selector.split()[-1])["el"]
        region = self.body("renderRulesBanner")
        said, code = jslint.without_comments(region), jslint.code_only(region)
        host = re.search(r"var (\w+) = hook\(", code)
        self.assertIsNotNone(host, "renderRulesBanner no longer takes the banner from "
                                   "hook(), so what it puts in it cannot be read")

        def made(expression: str, where: str) -> list[str]:
            called = re.match(r"el\(\s*'(\w+)'", expression)
            if called:
                return [called.group(1)]
            named = re.fullmatch(r"(\w+)", expression)
            if named:
                given = re.search(r"var %s = " % named.group(1), said)
                self.assertIsNotNone(given, f"{where} is given {named.group(1)}, which "
                                            f"the function does not build")
                start = given.end()
                return made(said[start:statement_end(code, start)].strip(), where)
            function = re.match(r"(\w+)\(", expression)
            self.assertIsNotNone(function, f"{where} is given {expression!r}, which "
                                           f"cannot be read as an element")
            returned = re.findall(r"(?<![\w$])return el\(\s*'(\w+)'",
                                  jslint.without_comments(self.body(function.group(1))))
            self.assertTrue(returned, f"{where} is given what {function.group(1)}() "
                                      f"returns, and it returns no element built there")
            return returned

        found = []
        for call in re.finditer(r"(?<![\w$.])%s\.appendChild\(" % host.group(1), code):
            close = closing_bracket(code, call.end() - 1)
            where = f"the banner's line at offset {call.start()} of renderRulesBanner"
            found += [(tag, said[call.start():close + 1])
                      for tag in made(said[call.end():close].strip(), where)]
        self.assertIn(line, [tag for tag, _ in found],
                      f"renderRulesBanner puts no <{line}> in the banner, which is what "
                      f"`{selector}` reaches")
        for tag, written in found:
            with self.subTest(line=written):
                self.assertIn(
                    tag, (line, "details"),
                    f"renderRulesBanner puts a <{tag}> in the banner, which "
                    f"`{selector}` does not reach, so it keeps the browser's wrap. "
                    f"What is at stake, from the demo season: at 375 the line of sets "
                    f"played ends on `Doubles 2` in every view")


class TestTheCaveatsAboveTheContentCanBeFoundAndSayWhatTheyAre(WebAssetCase):
    """Three strips qualify every number below them, and two could not be found.

    Between the topbar and the content sit the rules banner (which scoring scheme,
    which set rule, which match format produced the ladder), the data-health
    summary (whether the season validated, and how many cards were scored) and the
    refused-card block (a scorecard was found and not read, so every figure is
    short a night). Nothing on the page is more worth reaching first, and nothing
    on the page carries a heading -- the views are the only navigation, and the
    skip link steps over all three on its way to ``#view``, correctly, because
    that is what a skip link is for.

    So the only way to offer them is the landmark list, and two of the three were
    not in it. Measured in Chrome 151 with :file:`/tmp/p39/ax39.py`, which reads
    the browser's own accessibility tree rather than the markup -- the markup is
    what is in question::

        #rules-banner  role=generic  name=''
        #health        role=generic  name='Data health'
        #rejected      role=region   name='Scorecards that could not be read'

    Six landmarks on a clean season, seven when a card had been refused, and
    neither of the first two among them. A bare ``<div>`` maps to role ``generic``
    (HTML-AAM), and ``generic`` is the one role ARIA 1.2 lists ``aria-label`` as
    *prohibited* on -- so ``<div id="health" aria-label="Data health">`` wrote a
    name onto an element with no role to hang it on, and the banner had no name at
    all. ``#rejected``, a ``<section>`` with a name, was already the shape the
    other two needed; the fix is that shape, twice.

    Four of these six tests failed before it. The other two are guards, each
    pinned by a mutation nothing else in the class catches: a name can be moved to
    the wrong strip without any structural check noticing, and a strip can be
    moved inside the element ``render()`` empties, which is what "make the skip
    link reach it" looks like when somebody tries it.
    """

    # Named by id, not by class, because the id is the page's own handle on these
    # three -- it is what the script fetches them by -- and a class is shared by
    # whatever else wants the look. `.health` was on the <noscript> note further
    # down the page until that note moved into <main>, and for as long as it was, a
    # class-based search here bound silently to a different element with different
    # words. The next class two elements share will be just as quiet.
    STRIPS = ("rules-banner", "health", "rejected")
    # HTML-AAM's two mappings from "an element carrying a name" to "an entry in a
    # landmark list": a named <section> is role `region`, a body-level <aside> is
    # `complementary`. Either answers this; a <div> is `generic`. Both spellings
    # are accepted because which of the two reads better for a strip that
    # qualifies the content rather than standing beside it is a judgement, and a
    # test that settled it would be inventing a requirement.
    NAMEABLE = ("section", "aside")
    LANDMARK_ROLES = ("region", "complementary")
    # Every function that writes into one of the strips. showLoadFailure() is in
    # the list because it is the second writer into #health -- the no-data path --
    # and so the likeliest place a second opinion about the name gets bolted on.
    WRITERS = ("renderRulesBanner", "renderHealth", "renderRejected",
               "showLoadFailure")

    def markup(self) -> str:
        """``index.html`` with its comments removed.

        The page carries long comments, some of them quoting the very markup under
        discussion, so a search of the raw file finds the example and passes.
        """
        return re.sub(r"<!--.*?-->", "", self.assets()["index.html"], flags=re.S)

    def strip_tag(self, strip: str) -> tuple[str, str]:
        """``#strip``'s opening tag, and the element name it is written with."""
        found = re.search(r'<(\w+)\b[^>]*\bid="%s"' % re.escape(strip) + r'[^>]*>',
                          self.markup())
        self.assertIsNotNone(found, f"index.html ships no #{strip}, so the caveat "
                                    f"it carries is silently absent from the page")
        return found.group(0), found.group(1).lower()

    def attribute(self, tag: str, name: str) -> str | None:
        # Read out of the raw attribute because an accessible name is a string
        # literal in the markup: there is no visible heading on any of these
        # strips to derive one from, which is itself the reason it is authored.
        found = re.search(r'\b%s="([^"]*)"' % re.escape(name), tag)
        return found.group(1) if found else None

    def function_body(self, name: str) -> str:
        """One function's own source, without the next function's doc comment.

        :meth:`WebAssetCase.body` slices to the next top-level ``function``, so it
        carries the comment block written above that one. This class asks whether a
        word appears *in* a function, and a comment four lines below the closing
        brace would answer that wrongly in both directions.
        """
        source = self.body(name)
        end = source.find("\n}\n")
        return source if end < 0 else source[:end + 2]

    def test_each_strip_is_an_element_a_name_can_be_attached_to(self):
        for strip in self.STRIPS:
            with self.subTest(strip=strip):
                tag, element = self.strip_tag(strip)
                role = self.attribute(tag, "role")
                self.assertTrue(element in self.NAMEABLE
                                or role in self.LANDMARK_ROLES,
                                f"#{strip} ships as <{element}> with role={role!r}. "
                                f"A bare <div> is role `generic`, which ARIA 1.2 "
                                f"lists aria-label as prohibited on, so whatever "
                                f"name it carries reaches nobody and the strip is "
                                f"in no landmark list -- measured in Chrome 151 as "
                                f"role=generic, absent from a 6-entry list. "
                                f"`group` and `status` are nameable and are not "
                                f"landmarks, so neither is an answer either.")

    def test_each_strip_says_in_words_which_question_it_answers(self):
        # Both halves matter, and the second only once they are all landmarks: a
        # <section> with no name is `generic` again, so an empty name deletes the
        # landmark; and a list offering two entries under one name is a list that
        # cannot be chosen from.
        named: dict[str, list[str]] = {}
        for strip in self.STRIPS:
            tag, _ = self.strip_tag(strip)
            label = (self.attribute(tag, "aria-label") or "").strip()
            self.assertTrue(label, f"#{strip} carries no accessible name, so it is "
                                   f"role `generic` however it is spelled and "
                                   f"appears in no landmark list")
            named.setdefault(label, []).append(strip)
        shared = {label: strips for label, strips in named.items() if len(strips) > 1}
        self.assertEqual(shared, {}, f"two strips answer to one name, so a reader "
                                     f"choosing from the list cannot tell which is "
                                     f"which: {shared}")

    def test_the_health_strips_name_is_the_words_its_own_detail_already_uses(self):
        # The one strip that has words of its own to borrow: its disclosure prints
        # "Data health detail (...)". Pinning the name to them keeps what is
        # announced the same as what a sighted reader beside them is looking at,
        # and makes rewording either side alone a failure rather than a drift.
        label = self.attribute(self.strip_tag("health")[0], "aria-label")
        self.assertIn(label, self.function_body("renderHealth"),
                      f"the health strip is announced as {label!r} and its own "
                      f"detail says something else, so the words a screen reader "
                      f"offers are in no other place on the page")

    def test_a_strip_that_ships_empty_is_hidden_until_it_has_something_in_it(self):
        # Computed from both files rather than listed here, so a fourth named strip
        # is covered with no test to edit. An empty named landmark is a signpost
        # with no writing on it: worse than no signpost, because a reader chooses
        # it and arrives nowhere.
        hidden = set()
        for rule in csslint.rules(self.assets()["styles.css"]):
            # `display: none`, not `visibility: hidden`: only removing the box
            # takes the node out of the accessibility tree, and hidden would leave
            # the strip in the landmark list with the height it had.
            if (rule.value("display") or "").startswith("none"):
                hidden.update(part.strip() for part in rule.selector.split(",")
                              if part.strip().endswith(":empty"))
        self.assertTrue(hidden, "no rule hides an :empty element, so this check is "
                                "not checking anything")
        checked = 0
        for found in re.finditer(r"<(\w+)\b([^>]*)>\s*</\1\s*>", self.markup()):
            attributes = found.group(2)
            if "aria-label" not in attributes:
                continue
            ident = re.search(r'\bid="([^"]*)"', attributes)
            styled = re.search(r'\bclass="([^"]*)"', attributes)
            # Either spelling counts: the sheet hides this group by class in one
            # place and by id in another, and which was used is not the question.
            keys = {f"#{ident.group(1)}:empty"} if ident else set()
            keys |= {f".{name}:empty" for name in (styled.group(1).split()
                                                   if styled else ())}
            checked += 1
            self.assertTrue(keys & hidden,
                            f"{found.group(0)!r} ships empty and carries a name, "
                            f"and no rule hides it while it is empty, so a page "
                            f"that never received data offers a landmark with "
                            f"nothing in it. Hidden while empty: {sorted(hidden)}")
        self.assertTrue(checked, "no element in index.html ships empty with a name, "
                                 "so this check is not checking anything")

    def test_no_strip_is_inside_the_mount_point_the_router_empties(self):
        # Two things at once. The strips stay above the content, where a caveat is
        # read before the number it qualifies; and none of them is inside the
        # element render() calls clear() on, where the first render would delete it
        # and no reader would ever see it. That is what "move them into main so the
        # skip link reaches them" costs, and this is where it fails loudly.
        markup = self.markup()
        mount = markup.index('<main id="view"')
        for strip in self.STRIPS:
            with self.subTest(strip=strip):
                self.assertLess(markup.index(self.strip_tag(strip)[0]), mount,
                                f"#{strip} now sits inside or after <main id="
                                f'"view">, which render() empties on every route')

    def test_nothing_in_the_script_names_a_strip_a_second_time(self):
        # The name lives in the markup, once. A second copy in the script is not
        # belt and braces: it is the copy that silently overrules the markup on
        # whichever render ran last, which is the drift the scrolling tables
        # already have a test forbidding.
        for name in self.WRITERS:
            with self.subTest(function=name):
                source = self.function_body(name)
                for attribute in ("aria-label", "role"):
                    self.assertNotIn(attribute, source,
                                     f"{name}() sets {attribute!r} on a strip the "
                                     f"page already names, so the two can disagree "
                                     f"and the script wins")


class TestThePageWithoutJavaScriptPutsItsOneNoteWhereAReaderLooks(WebAssetCase):
    """With scripting off the note is the whole page, so it belongs in the content.

    Every number on this page arrives from a script, so with scripting off the
    ``<noscript>`` note is not a caveat on the content: it *is* the content. It shipped
    between the caveat strips and ``<main>``, which is to say in no landmark at all.
    Measured in Chrome 151 with :file:`/tmp/p316/ax316.py`, which reads the browser's own
    accessibility tree over CDP with ``Emulation.setScriptExecutionDisabled``::

        noscript .health         role=none name=None (ignored)
        note's nearest landmark  body -- no landmark
        #view text               0 characters
        landmark list            banner, main, contentinfo, navigation

    Two readers, two different pages. One going top to bottom reads the note, because a
    collapsed generic's text is still in document order; one navigating by region is
    offered a ``main`` with nothing in it, and so is anybody pressing the page's own skip
    link, whose whole purpose is to reach the content.

    The fix is where the note sits, not a name on the box around it. Rejected, and both
    were measured or ruled out before this shape was chosen: naming the existing ``<div>``
    (role ``generic``, which ARIA 1.2 lists ``aria-label`` as prohibited on -- the same
    mistake the class above this one fixed), and ``role="alert"``, which announces once
    over whatever the reader is doing and leaves nothing in any list to navigate back to.
    A named ``<section>`` beside ``main`` would be a landmark and would still leave the
    skip link landing on an empty one. Moving the note inside ``main``, on the same
    instrument::

        note's nearest landmark  main#view
        skip link's target       221 characters, holds the note

    Three of these five tests failed before that move. The other two are guards, each
    pinned by a mutation nothing else here catches: the element holding the content can
    be swapped for one that is no landmark at all, and a second thing can be parked
    inside the mount point, where ``render()`` deletes it on the first route -- which is
    what this fix looks like when it is applied to something that is not a ``<noscript>``.
    """

    # Body-level elements that are neither landmarks nor content. Just the one: <script>
    # renders nothing under any conditions. <noscript> is deliberately not here -- with
    # scripting off it renders, which is this row's whole complaint. The skip link is
    # allowed separately below, being first in the document on purpose.
    ALLOWED = ("script",)
    LANDMARKS = ("header", "main", "footer", "nav", "aside")

    def markup(self) -> str:
        """``index.html`` with its comments removed.

        The page's comments quote its own markup, at length, so a search of the raw
        file can find the example and pass on it.
        """
        return re.sub(r"<!--.*?-->", "", self.assets()["index.html"], flags=re.S)

    def note(self) -> tuple[int, str]:
        """Where the ``<noscript>`` note starts, and the opening tag of its own box."""
        markup = self.markup()
        at = markup.find("<noscript")
        self.assertGreater(at, 0, "index.html ships no <noscript> note, so a reader with "
                                  "scripting off is handed a blank page and no reason")
        end = markup.find("</noscript>", at)
        self.assertGreater(end, at, "the <noscript> note is never closed")
        box = re.search(r"<(\w+)\b[^>]*>", markup[at + len("<noscript>"):end])
        self.assertIsNotNone(box, "the note carries no element of its own")
        return at, box.group(0)

    def holder(self) -> tuple[str, str, int, int]:
        """The element the page's own skip link points at.

        Its tag name, its opening tag, where its content starts and where it ends. Found
        through the link rather than written down here, because "where the content is"
        and "where Skip to content lands" have to be one element, and naming it twice is
        how the two come to be different elements.
        """
        markup = self.markup()
        link = re.search(r'<a class="skip-link"[^>]*href="#([\w-]+)"', markup)
        self.assertIsNotNone(link, "index.html ships no skip link, and the note being "
                                   "inside whatever that link reaches is the whole of "
                                   "what this class is checking")
        found = re.search(r'<(\w+)\b[^>]*\bid="%s"[^>]*>' % re.escape(link.group(1)),
                          markup)
        self.assertIsNotNone(found, f"the skip link points at #{link.group(1)} and "
                                    f"nothing in the page carries that id, so it skips "
                                    f"to nowhere before any of this matters")
        element = found.group(1)
        closed = markup.find("</%s>" % element, found.end())
        # Not `> found.end()`: an element that ships empty closes at exactly the
        # character its content would have started at, and "the content is empty" is the
        # finding two of these tests exist to report rather than a reason to stop here.
        self.assertNotEqual(-1, closed,
                            f"the skip link's target <{element}> is never closed")
        return element, found.group(0), found.end(), closed

    def test_the_note_is_inside_what_the_page_s_own_skip_link_reaches(self):
        at, _ = self.note()
        element, _, opens, closes = self.holder()
        # `opens` is the first character of the element's content, and the note is
        # allowed to be exactly that character: it is the first thing inside.
        self.assertTrue(opens <= at < closes,
                        f"the <noscript> note sits outside <{element}>, the element the "
                        f"skip link reaches, and with scripting off that note is the only "
                        f"content on the page: measured in Chrome 151, the note's own node "
                        f"is role=none ignored, its nearest landmark is the body, and the "
                        f"skip link's target holds 0 characters")

    def test_the_element_it_went_into_is_the_one_that_means_the_content(self):
        # The other half, and `main` rather than any landmark that happens to be
        # nameable: ARIA defines exactly one role whose meaning is "this is the
        # document's content", and a named `region` or a `complementary` says "beside the
        # content" instead -- which is what the caveat strips above are, and what this
        # note is not. It is also the argument against the rejected fix of a named
        # <section> put beside `main`: that section would be a landmark of its own and
        # would still leave the skip link, and the list's `main`, arriving at nothing.
        # The class above addresses this same element as <main id="view"> when it checks
        # that no strip is inside it, so a second spelling accepted here would be two
        # tests holding two views of one element.
        element, tag, _, _ = self.holder()
        self.assertEqual("main", element,
                         f"the skip link reaches {tag!r}. A <div> is in no landmark list "
                         f"at all, and a named <section> is a region beside the content "
                         f"rather than the content, so putting the note inside either "
                         f"answers the reader who presses the link and not the one "
                         f"reading the list of regions")

    def test_the_note_is_dressed_as_content_and_not_as_a_strip(self):
        # Tied to the script rather than to a class name written here: the note is a
        # block inside the mount point now, and the blocks the script builds in there are
        # what a reader's eye is trained on. `.health`, which this box used to carry, is
        # a full-bleed band with a bottom border and no sides -- inside a centred, padded
        # <main> it reaches neither edge and reads as a rule across the middle of the
        # page. One shared class is the claim, not every class: a second, modifying one
        # is a judgement about how the note should look, and not this test's to make.
        _, box = self.note()
        classes = re.search(r'class="([^"]*)"', box)
        self.assertIsNotNone(classes, f"the note's box {box!r} carries no class, so it "
                                      f"is unstyled text in the middle of the content")
        source = self.assets()["app.js"]
        names = classes.group(1).split()
        self.assertTrue([name for name in names
                         if "el('div', { class: '%s' })" % name in source],
                        f"the note's box is a {'.'.join([''] + names)}, and the script "
                        f"builds no block inside the mount point with any of those "
                        f"classes: whatever they are styled for, it is not a block of "
                        f"content in the middle of a centred, padded <main>")

    def test_nothing_but_the_note_ships_inside_the_mount_point(self):
        # The cost of putting anything in here, and the reason the caveat strips above
        # stayed where they are: render() calls clear() on this element before it draws
        # anything, so whatever else ships inside it is deleted the moment a script runs.
        # A <noscript> is the one thing that can afford that -- with scripting on it
        # renders nothing anyway -- and this is where the second thing fails loudly.
        _, _, inside, closes = self.holder()
        held = self.markup()[inside:closes]
        left = re.sub(r"<noscript>.*?</noscript>", "", held, flags=re.S).strip()
        self.assertEqual("", left,
                         f"this ships inside the element render() empties, so a reader "
                         f"sees it only until the first route draws, and a reader with "
                         f"scripting off sees it instead of the note: {left[:200]!r}")

    def test_nothing_else_is_left_outside_a_landmark_either(self):
        # The general form of this row's complaint, so the next orphan fails here rather
        # than being found by measuring the accessibility tree a second time.
        markup = self.markup()
        body = markup[markup.index("<body>") + len("<body>"):markup.index("</body>")]
        depth, orphans = 0, []
        for found in re.finditer(r"<(/?)(\w+)\b([^>]*)>", body):
            closing, element, attributes = found.group(1), found.group(2).lower(), found.group(3)
            if closing:
                depth -= 1
                continue
            if depth == 0 and element not in self.LANDMARKS:
                named = element == "section" and "aria-label" in attributes
                skip = element == "a" and "skip-link" in attributes
                if not (named or skip or element in self.ALLOWED):
                    orphans.append(found.group(0))
            if not attributes.endswith("/") and element not in (
                    "br", "hr", "img", "input", "link", "meta"):
                depth += 1
        self.assertEqual([], orphans,
                         f"these sit at body level in no landmark, so a reader "
                         f"navigating by region is never offered them: {orphans}")


class TestThePickerPutsTheStatusInPlaceAndSaysNothingItself(WebAssetCase):
    """The front page's one line about the last build: where it goes and how.

    The words are site.py's, in ``build-status.js``; the page only puts them in
    place. So the element is empty and hidden in the markup -- a page with
    scripting off, or with the file missing, shows nothing, which is what it showed
    before -- and the script sets text, never markup, because the file is the one
    thing a failed build writes and a sentence arriving as HTML is a page anybody
    who can write that file can rewrite.
    """

    TAG = '<p id="build-status" class="build-status" hidden></p>'

    def picker(self) -> str:
        return self.assets()["picker.html"]

    def script(self) -> str:
        """The inline script, with its comments blanked and its strings kept."""
        found = inline_scripts(self.picker())
        self.assertEqual(len(found), 1, "the front page has one inline script")
        return jslint.without_comments(found[0])

    def test_the_line_is_the_first_thing_in_main(self):
        # First, above the season buttons and the list, because it qualifies
        # everything under it.
        page = self.picker()
        opened = re.search(r"<main\b[^>]*>\s*", page)
        self.assertIsNotNone(opened, "the front page has no <main>")
        self.assertTrue(page.startswith(self.TAG, opened.end()),
                        f"the first thing in <main> is not the status line: "
                        f"{page[opened.end():opened.end() + 80]!r}")
        self.assertLess(opened.end(), page.index('id="season-picker"'))

    def test_the_file_is_loaded_after_the_picker_data_and_before_the_script(self):
        # Neither deferred nor async: the inline script reads it as it runs, and a
        # file that arrives after that is a failure the page never shows.
        page = self.picker()
        tags = [(m.group(1), m.start()) for m in
                re.finditer(r"<script([^>]*)>", page, re.IGNORECASE)]
        attrs = [attr.strip() for attr, _ in tags]
        self.assertEqual(attrs, ['src="picker-data.js"', 'src="build-status.js"', ""],
                         "the front page's scripts are not picker-data.js, then "
                         "build-status.js, then the inline one, with nothing else "
                         "on either tag")
        # And the page's own description of itself names the file.
        opened = page.index("<!--\n")
        header = page[opened:page.index("-->", opened)]
        self.assertIn("build-status.js", header)

    def test_the_sentence_goes_in_as_text(self):
        code = self.script()
        start = code.index("statusLine")
        self.assertIn("statusLine.textContent = status.text", code)
        self.assertNotIn("innerHTML", code[start:])

    def test_only_a_level_it_knows_becomes_a_class(self):
        # A level is joined onto a class name, and the file it comes from is data:
        # "fail extra-class" would otherwise be two classes, one of them anybody's.
        code = self.script()
        # Brackets counted where strings are blanked, offsets read where they are
        # not: the two texts are the same length.
        blank = jslint.code_only(code)
        self.assertEqual(code.count("statusLine.className"), 1)
        at = code.index("statusLine.className")
        opener = code.rfind("if (", 0, at)
        self.assertGreater(opener, 0, "the status class is set outside any if")
        close = closing_bracket(blank, opener + 3)
        condition = code[opener + 3:close + 1]
        self.assertIn("Object.prototype.hasOwnProperty.call(LEVELS, status.level)",
                      condition)
        self.assertNotIn("||", condition)
        self.assertGreater(statement_end(blank, close + 1), at,
                           "the status class is set after the if that checks it")

    def test_the_line_is_shown_only_where_it_is_filled(self):
        # Hidden in the markup, so a script that fills it and never unhides it says
        # nothing, which is the failure this line exists to report gone quiet. And
        # unhidden only inside the check that there is a sentence: a build that
        # worked writes no text, and an empty line shown for it is a 16px gap above
        # every club's list.
        code = self.script()
        blank = jslint.code_only(code)
        shown = "statusLine.removeAttribute('hidden')"
        self.assertEqual(code.count(shown), 1)
        opener = code.rfind("if (", 0, code.index("statusLine.textContent"))
        close = closing_bracket(blank, opener + 3)
        self.assertIn("typeof status.text === 'string'", code[opener:close])
        self.assertLess(close, code.index(shown))
        self.assertLess(code.index(shown), statement_end(blank, close + 1),
                        "the line is unhidden outside the if that fills it")

    def test_the_line_is_filled_before_the_seasons_section_can_return(self):
        # The seasons section returns early on every single-season site, which is
        # most of them; a status filled after that return is filled on none.
        code = self.script()
        seasons = code.index("var nav = document.getElementById('season-picker')")
        early = code.index("return", seasons)
        self.assertLess(code.index("statusLine.textContent"), early)
        self.assertEqual(code.find("return", 0, seasons), -1,
                         "something before the seasons section returns first")


class TestTheStatusIsPaintedWithTextColoursOnly(ContrastCase):
    """The line is words, coloured with the two colours already named for words.

    No background and no border: a filled banner above the list would read as the
    page's heading, and a fill is a second colour for the contrast checks to
    answer for on every palette. The fail level is bold as well as red, so the
    colour is never the only signal.
    """

    COLOURS = {".build-status-ok": "var(--text-muted)",
               ".build-status-fail": "var(--critical-text)"}

    def declared(self) -> dict[str, dict[str, str]]:
        found: dict[str, dict[str, str]] = {}
        _, parsed = _parsed(self.assets()["styles.css"])
        for rule in parsed:
            if ".build-status" in rule.selector:
                found.setdefault(rule.selector, {}).update(rule.declarations)
        return found

    def test_each_level_is_one_text_colour(self):
        declared = self.declared()
        for selector, colour in self.COLOURS.items():
            with self.subTest(selector=selector):
                self.assertEqual(declared.get(selector, {}).get("color"), colour)

    def test_nothing_is_filled_or_outlined(self):
        declared = self.declared()
        self.assertEqual(sorted(declared),
                         [".build-status", *sorted(self.COLOURS)])
        for selector, properties in declared.items():
            for name in properties:
                with self.subTest(selector=selector, property=name):
                    self.assertFalse(name.startswith(("background", "border",
                                                      "outline", "box-shadow")))


def _season_doc() -> dict:
    """A whole season document, from an empty competition.

    Empty because every caller here is asking which keys exist and what a fixed
    value is, not what the figures come to; and taken from the exporter itself so
    that nothing about its output is written down twice.
    """
    from matchcentre import export, format as fmt_mod, scoring
    from matchcentre.competition import Club, Competition
    comp = Competition(id="x", day="Monday", division="C",
                       fmt=fmt_mod.match_format("doubles-singles-doubles"),
                       rules=scoring.ruleset("games-bonus"))
    return export.build_season(
        [], competition=comp, source_dir=Path("."), generated="",
        club=Club(name="", competitions=(comp,)),
        cards=[], rejected=[])


def _rules_block() -> dict:
    """The ``rules`` block of a season document, from an empty competition.

    Taken from the exporter's own output rather than from a list of names repeated
    here, so the page and the document cannot drift apart and both be
    self-consistent.
    """
    return _season_doc()["meta"]["rules"]


class TestTheHealthPillCountsTheErrorsTheBuildSaysAreTheCompetitionsOwn(WebAssetCase):
    """The first pill counts the competition's own errors, from the document.

    Every document carries every refused card as an F4 error, and ``renderHealth``
    counted ``errors.length``. So a season with nothing wrong but two refused cards
    read ``2 validation errors`` beside ``2 cards seen and not scored``, which is
    one card read as two mistakes. Measured on the demo with two refused PDFs
    (``/tmp/t6/scratch/m11/measure.py``): 2 errors in the document, both F4. The
    build now counts which errors are the competition's own, by
    ``export.own_findings``, and writes the number as ``validation.own_errors``.
    The page renders that number and counts nothing itself. A document written
    before the field existed falls back to ``errors.length``, the count it always
    showed. Rejected: filtering out F4 in app.js, a second copy of the rule in the
    language that only renders.
    """

    def said(self) -> str:
        return jslint.without_comments(self.body("renderHealth"))

    def pill(self) -> str:
        """From the first pill's state to the end of the call that appends it."""
        said = self.said()
        start = said.index("var state")
        end = said.index("]));", said.index("'pill ' + state"))
        return said[start:end]

    def test_the_count_is_the_documents_and_an_older_document_keeps_its_own(self):
        self.assertRegex(self.said(),
                         r"var own = typeof v\.own_errors === 'number'"
                         r" \? v\.own_errors : errors\.length;")

    def test_the_pill_its_colour_and_its_mark_count_nothing_else(self):
        pill = self.pill()
        self.assertNotIn("errors.length", pill)
        state = pill[:pill.index(";")]
        self.assertEqual(" ".join(state.split()),
                         "var state = own ? 'critical' : warnings.length ? "
                         "'warning' : 'good'")
        self.assertIn("(own ? '✕ ' :", pill)

    def test_refused_cards_and_no_errors_are_not_called_clean(self):
        # The refusals have their own pill, in red, and the list under it. This one
        # does not say ``Validated clean`` of a season two cards are missing from.
        self.assertIn("rejected.length ? 'No errors in the cards scored' : "
                      "'Validated clean'", " ".join(self.pill().split()))

    def test_a_subject_is_printed_only_where_the_build_says_it_was_not_said(self):
        self.assertEqual(self.guard_for("renderHealth", "' [' + item.subject"),
                         "item.subject && !item.subject_said")


class TestTheHealthDetailCountsTheErrorsThePillCountsAndTheRefusedCardsApart(
        WebAssetCase):
    """The data health detail's summary counts what the pill counts, and names the rest.

    With the pill counting the competition's own errors, the disclosure under it
    still counted ``errors.length`` and opened on any error. Measured in headless
    Chrome at 1280px and 360px (``/tmp/t6/scratch/m11/fix/measure_page.py``): the
    suite's season with one refused card read ``✓ No errors in the cards scored``
    above ``Data health detail (1 error, 0 warnings)``, open on load, and the demo
    with two refused PDFs read ``! Validated with 6 warnings`` above ``(2 errors, 6
    warnings)``. The summary now counts ``own`` and names the list's other errors,
    the refused cards, apart, as the report's own line does; and only the
    competition's own errors open the list, since the ``seen and not scored`` list
    under it shows every refusal already, never behind a disclosure. Now ``(0
    errors, 0 warnings and 1 card not scored)``, closed, and ``(0 errors, 6
    warnings and 2 cards not scored)``. A document without ``own_errors`` has
    ``own`` equal to ``errors.length`` and nothing to name apart, so it reads as it
    did. Rejected: taking the cards named apart from ``rejected.length``, which on
    such a document counts each refusal twice again; and leaving F4 out of the
    list, which is the separate question of telling a refusal in one place or two.
    """

    def summary(self) -> str:
        said = jslint.without_comments(self.body("renderHealth"))
        start = said.index("el('summary'")
        return " ".join(said[start:said.index("}));", start)].split())

    def test_the_summary_counts_the_competitions_errors_and_the_refused_cards_apart(
            self):
        self.assertEqual(
            self.summary(),
            "el('summary', { text: 'Data health detail (' + inWords([own + ' ' + "
            "plural(own, 'error') + ', ' + warnings.length + ' ' + "
            "plural(warnings.length, 'warning')].concat(refused ? "
            "refusedWords(refused, 'card', ' not scored') : [])) + ')'")
        self.assertIn("var refused = errors.length - own;",
                      jslint.without_comments(self.body("renderHealth")))

    def test_only_the_competitions_own_errors_open_it_by_themselves(self):
        self.assertEqual(self.guard_for("renderHealth", "'open'",
                                        emits="setAttribute"), "own")


class TestTheDataHealthListShowsItsErrorsItsWarningsOrBothAndCountsThemAll(
        WebAssetCase):
    """The data health detail can show its errors alone, its warnings alone, or both.

    The detail listed every error and then every warning in one list, and nothing
    on the page showed one kind. Measured in headless Chrome
    (``/tmp/mcf/scratch/FILTER/probe.py``) on a fictional season with three errors
    of its own, one refused card and eight warnings: twelve items, open on load
    because of the competition's own errors, 398px of list at 1280 wide and 1061px
    at 360, with the season's own content starting 829px and 2048px down. The
    errors come first, so the warnings are what a club reads past to reach the
    page. On the club's own cards there are 18 errors and 267 warnings over 17
    dashboards, up to 3 errors and 38 warnings on one.

    There is now a fieldset of three native radios between the summary and the
    list: Both, which is checked every time the page opens, and the errors and the
    warnings, each counted in the summary's words. Each item carries its kind from
    the loop's own ``isError``, and a choice writes one attribute on the list,
    which the stylesheet reads. So paper ignores the choice, and a printed list is
    the whole list. Whatever is chosen, no count changes. The Errors option shows
    the refused cards' F4 errors with the competition's own, because they are
    errors, and says them apart as the summary does. Its count is therefore the
    summary's, never a sum the pill does not show. A list of one kind gets no
    control.

    Rejected:

    * ``hidden`` on each item, which is a write per item and a state that print
      would have to undo item by item.
    * Keeping the choice in ``localStorage``, as the theme is kept, or in the
      address, whose fragment belongs to the router. Either would let a choice
      made on another day hide an error.
    * Counted labels such as ``Errors (4)``, which beside ``3 validation errors``
      counts a refused card as one of the competition's errors again.
    """

    def said(self, fn: str) -> str:
        return " ".join(jslint.without_comments(self.body(fn)).split())

    def handler(self) -> str:
        """The change handler's body, from its opening brace to its closing one."""
        said = self.said("healthFilter")
        start = said.index("change: function () {")
        return said[start:said.index("}", start) + 1]

    def test_the_choice_is_offered_only_where_the_list_holds_both_kinds(self):
        # A list of errors alone, or of warnings alone, would offer two choices
        # that show the same list and a third that shows nothing.
        self.assertEqual(self.guard_for("renderHealth", "healthFilter(list"),
                         "errors.length && warnings.length")

    def test_it_sits_between_the_summary_and_the_list_it_narrows(self):
        said = self.said("renderHealth")
        summary = said.index("det.appendChild(el('summary'")
        offered = said.find("det.appendChild(healthFilter(list")
        listed = said.index("det.appendChild(list);")
        self.assertGreater(offered, 0, "renderHealth() offers no choice of what "
                                       "the list shows")
        self.assertLess(summary, offered, "the choice is above the summary that "
                                          "counts what it chooses between")
        self.assertLess(offered, listed, "the choice is below the list it "
                                         "narrows, where a long list hides it")

    def test_each_item_carries_its_kind_from_the_loops_own_test(self):
        self.assertIn("var isError = i < errors.length; var li = el('li', "
                      "{ 'data-kind': isError ? 'error' : 'warning' });",
                      self.said("renderHealth"))

    def test_it_is_a_fieldset_of_native_radios_under_a_legend(self):
        said = self.said("healthFilter")
        self.assertIn("el('fieldset', { class: 'health-filter' }, "
                      "[el('legend', { text: 'Show' })])", said)
        # The label holds its radio and then its counted words, which are the
        # radio's name to a screen reader: words beside the label name nothing.
        self.assertIn("box.appendChild(el('label', null, [el('input', { type: "
                      "'radio', name: 'health-show', value: choice[0], checked: "
                      "choice[0] === 'both', on: { change: function () { "
                      "list.setAttribute('data-show', choice[0]); } } }), "
                      "choice[1]]));", said)
        self.assertEqual(re.findall(r"\['(\w+)', ", said),
                         ["both", "errors", "warnings"])
        # Native elements carry their own names and their own keys, arrows included.
        self.assertNotIn("aria-", said)
        self.assertNotIn("keydown", said)

    def test_each_option_counts_what_it_shows_in_the_summarys_words(self):
        said = self.said("healthFilter")
        self.assertIn("['both', 'Both']", said)
        self.assertIn("['errors', inWords([own + ' ' + plural(own, 'error')]"
                      ".concat(refused ? refusedWords(refused, 'card', "
                      "' not scored') : []))]", said)
        self.assertIn("['warnings', warned + ' ' + plural(warned, 'warning')]", said)
        self.assertIn("healthFilter(list, own, refused, warnings.length)",
                      self.said("renderHealth"))
        # The refused cards' clause is written twice, because the summary's own
        # test pins it inline. Read here off the summary, so the option's words
        # can only change with the line they quote.
        quoted = re.search(r"\.concat\(refused \? refusedWords\(refused, [^)]*\) : "
                           r"\[\]\)", self.said("renderHealth"))
        self.assertIsNotNone(quoted, "the summary says no refused card apart")
        self.assertIn(quoted.group(0), said)

    def test_the_page_opens_on_both_every_time(self):
        self.assertIn("checked: choice[0] === 'both',", self.said("healthFilter"))
        # Nothing is hidden until a choice is made: the list's attribute is written
        # in the handler and nowhere else.
        for fn in ("healthFilter", "renderHealth"):
            with self.subTest(function=fn):
                said = self.said(fn)
                self.assertEqual(said.count("data-show"),
                                 self.handler().count("data-show")
                                 if fn == "healthFilter" else 0)
                code = jslint.code_only(self.body(fn))
                for memory in ("localStorage", "sessionStorage", "cookie",
                               "location", "history"):
                    self.assertNotIn(memory, code,
                                     f"{fn}() reads or keeps {memory}, so a "
                                     f"choice made on another day can hide an "
                                     f"error on this one")

    def test_choosing_writes_one_attribute_on_the_list_and_no_word(self):
        self.assertEqual(self.handler(), "change: function () { "
                         "list.setAttribute('data-show', choice[0]); }")

    def test_the_stylesheet_hides_the_other_kind_on_every_screen(self):
        # Outside every @media, so a choice narrows the list at 360 wide as at 1280.
        screen = {rule.selector: rule for rule in csslint.rules(
                      self.assets()["styles.css"]) if not rule.at_rule}
        hides = '.health ul[data-show="errors"] > li[data-kind="warning"], ' \
                '.health ul[data-show="warnings"] > li[data-kind="error"]'
        self.assertTrue(hides in screen, "no rule for every screen hides the kind "
                                         "not chosen, so a choice changes nothing "
                                         "on the page")
        self.assertEqual(screen[hides].value("display"), "none")

    def test_the_control_shows_on_every_screen(self):
        screen = [rule for rule in csslint.rules(self.assets()["styles.css"])
                  if not csslint.paper_only(rule.at_rule)]
        self.assertEqual([rule.value("display") for rule in screen
                          if rule.selector == ".health-filter"], ["flex"],
                         "the choice is not drawn as one row, legend first")
        self.assertEqual([rule.where for rule in screen
                          if "health-filter" in rule.selector
                          and rule.value("display") == "none"], [],
                         "a screen rule hides the choice or its radios, so the "
                         "list cannot be narrowed")

    def test_a_printed_list_is_the_whole_list_and_carries_no_control(self):
        printed = {rule.selector: rule for rule in self.print_rules()}
        whole = ".health ul[data-show] > li[data-kind]"
        self.assertTrue(whole in printed, "a list printed after a choice prints "
                                          "only the kind chosen, to a reader who "
                                          "never chose")
        self.assertEqual(printed[whole].value("display"), "list-item")
        self.assertTrue(".health-filter" in printed, "the choice prints, and paper "
                                                     "takes no input")
        self.assertEqual(printed[".health-filter"].value("display"), "none")


class TestThePageCountsAFolderItCouldNotListAsAFolderAndNotACard(WebAssetCase):
    """A folder the build could not list is on the refused list, and said as one.

    Measured before (``/tmp/t7/scratch/FOLDERS/probe.py``): a card folder at mode
    000 was in no document at all, and the page read ``12 of 12 cards scored``
    with no refusal on it. Once the reader refuses the folder, a page that
    counted ``rejected.length`` would say ``1 card seen and not scored`` and ``1
    scorecard seen and not scored`` over an entry that is a folder, and tell the
    club to run the doctor on it, which answers that a directory is not a file.
    The build counts the folders among the refusals as ``meta.folders_unlisted``;
    the page says them apart in the report's words, in the pill, the list's
    heading and the detail's summary, and says what a folder needs under the list.
    A document without the count has no folder among its refusals, since no
    build before it refused one. Rejected: counting ``folder`` entries in app.js,
    a second count of what the build has counted.
    """

    def said(self, fn: str) -> str:
        return " ".join(jslint.without_comments(self.body(fn)).split())

    def test_the_folders_are_the_builds_count_and_the_rest_are_cards(self):
        self.assertIn("var folders = meta().folders_unlisted || 0;",
                      self.said("refusedApart"))
        self.assertIn("{ cards: n - folders, folders: folders }",
                      self.said("refusedApart"))
        self.assertEqual(self.guard_for("refusedWords", "' not opened'",
                                        emits="push"), "apart.folders")
        self.assertEqual(self.guard_for("refusedWords", "plural(apart.cards, card)",
                                        emits="push"), "apart.cards")

    def test_the_pill_and_the_lists_heading_count_in_those_words(self):
        self.assertIn("'✕ ' + inWords(refusedWords(rejected.length, 'card', ' seen "
                      "and not scored'))", self.said("renderHealth"))
        self.assertIn("note('critical', inWords(refusedWords(rejected.length, "
                      "'scorecard', ' seen and not scored'))",
                      self.said("renderRejected"))

    def test_the_clauses_are_joined_as_the_report_joins_a_list(self):
        """``a``, ``a and b``, ``a, b and c``: ``scope.in_words``, in ES5.

        Measured in headless Chrome (``/tmp/t7/scratch/FOLDERS/measure_page.py``)
        with a card cut and a folder shut beside it, the clauses joined by a
        comma: the pill read ``✕ 1 card seen and not scored, 1 folder not
        opened`` and the summary ``(0 errors, 4 warnings and 1 card not scored, 1
        folder not opened)``, a comma splice after an *and* the report does not
        write. Now each count is a clause, and one rule joins them. Rejected:
        ``' and '`` between the two counts, which reads ``4 warnings and 1 card
        not scored and 1 folder not opened`` in the summary.
        """
        self.assertEqual(self.said("inWords"), "function inWords(items) { if "
                         "(items.length < 2) return items.join(''); return "
                         "items.slice(0, -1).join(', ') + ' and ' + "
                         "items[items.length - 1]; }")
        self.assertIn("return said;", self.said("refusedWords"))

    def test_the_doctor_is_offered_for_files_and_permission_for_folders(self):
        self.assertIn("var apart = refusedApart(rejected.length)",
                      self.said("renderRejected"))
        self.assertEqual(self.guard_for("renderRejected", "python3 tools/doctor.py",
                                        emits="push"), "apart.cards")
        self.assertEqual(self.guard_for("renderRejected", "'Each folder below",
                                        emits="push"), "apart.folders")


class TestTheListOfCardsNotScoredPrintsEachRefusedCardsDetail(WebAssetCase):
    """A refused card's detail is printed in ``seen and not scored``, where F4 points.

    F4 in the Data health list gives a refused card and its reason, and no detail,
    and ends ``The list of cards not scored gives its detail.`` That sentence is a
    promise about renderRejected, and nothing held it to it. Measured through
    ``build.py`` on the demo season with three refused cards
    (/tmp/t7/scratch/RF/measure.py): each detail is on the page once, in the list,
    and F4's items give none. With the line that prints it deleted from app.js, all
    466 tests of this file and of the report's and pipeline's classes for the
    detail passed (/tmp/t7/scratch/RF-review/M1.txt), and the page sent a reader to
    a list with no detail in it. Rejected: counting the detail in the page's text,
    which needs a browser the suite does not run; and asking only that
    ``r.detail`` is in the function, which a detail built and never appended
    passes, as does `` ()`` printed for a refusal that has none.
    """

    def test_each_detail_is_appended_to_its_item_where_there_is_one(self):
        self.assertEqual(self.guard_for("renderRejected", "' (' + r.detail + ')'"),
                         "r.detail")


class TestARefusedCardsPathWrapsOnlyWhenItIsWiderThanTheList(WebAssetCase):
    """A refused card's line is running text, so its path breaks only at the list's end.

    ``.rejected-item`` was a flex row, which made the cross, the path, the reason
    and the detail a column each, the path's as narrow as the rest let it be.
    Measured in headless Chrome on the demo season with four refused cards
    (/tmp/t7/scratch/RF/css_probe.py, and css_width.py for a path's width in its
    item). At 1280, in a list 1240px wide, ``data/truncated.pdf``, 130px on one
    line, broke over 2 lines, ``data/trunca`` and ``ted.pdf``, with the reason in a
    column beside it. The same path broke over 4 lines at 700, and over 9 of two
    letters each at 375. A path 679px wide broke over 3 lines at 1280.

    Now the item is a block of running text, and the cross an inline box before
    it, with the flex row's 8px gap as its margin. The path is one inline box that
    may break anywhere, so it breaks only where it is wider than the rest of the
    line. Measured the same way after: at 1280 each of the four paths is on one
    line, and at 700, 375 and 360 so are the three narrower than the list, while
    the 679px path breaks over 2 lines in a list 661px wide and over 3 in one of
    336px or 321px.

    Rejected: keeping the flex row and giving the path ``flex: none``, which never
    lets a path wider than the list wrap, so that it runs off a phone's screen. Also
    rejected: a grid of the cross beside the rest, which still puts the reason in a
    column of its own.
    """

    ITEM = ({"el": "html", "cls": set(), "id": ""},
            {"el": "body", "cls": set(), "id": ""},
            {"el": "section", "cls": {"rejected-list"}, "id": "rejected"},
            {"el": "p", "cls": {"rejected-item"}, "id": ""})
    PATH = ITEM + ({"el": "code", "cls": set(), "id": ""},)

    def test_the_item_is_a_block_of_running_text_on_every_screen(self):
        for screen in self.screens():
            with self.subTest(screen=screen or "every screen"):
                box, rule = self.cascaded(self.ITEM, "display", screen)
                self.assertIn(box, (None, "block"), rule and rule.where)

    def test_the_cross_is_an_inline_box_with_a_gap_after_it(self):
        crosses = [rule for rule in csslint.rules(self.assets()["styles.css"])
                   if "print" not in rule.at_rule
                   and any("::before" in selector and self.selects(selector, self.ITEM)
                           for selector in rule.selector.split(","))]
        self.assertTrue(crosses, "no rule draws the cross before a refused card")
        for rule in crosses:
            with self.subTest(rule=rule.where):
                self.assertIn(rule.value("display"), (None, "inline"))
        gaps = [rule.value("margin-right") for rule in crosses
                if rule.value("margin-right")]
        self.assertTrue(gaps, "nothing keeps the cross off the path")
        self.assertNotIn(gaps[-1], ("0", "0px"))

    def test_the_path_is_one_inline_box_that_may_break_anywhere(self):
        for screen in self.screens():
            with self.subTest(screen=screen or "every screen"):
                box, rule = self.cascaded(self.PATH, "display", screen)
                self.assertIn(box, (None, "inline"), rule and rule.where)
                wrap, _ = self.cascaded(self.PATH, "overflow-wrap", screen)
                self.assertEqual(wrap, "anywhere")
                # `white-space` is read as the longhand it sets, and is inherited, so
                # the nearest element of the path's chain that sets it decides.
                for depth in range(len(self.PATH), 0, -1):
                    mode, rule = self.cascaded(self.PATH[:depth], "text-wrap-mode",
                                               screen)
                    if mode:
                        break
                self.assertNotEqual(mode, "nowrap", rule and rule.where)


class TestThePageSaysWhenPlayersAreWithheld(WebAssetCase):
    """A withheld page says so on the health strip, the panel and each player's page.

    The labels read as names -- ``Brindle Magpies #3`` is somebody -- so a reader
    who is not told has no way to know the page is withholding anything, or what a
    label stands for. Each sentence is under the document's own ``players`` key, so
    a names page, which has none, prints none of them. Rejected: a pair in the rules
    banner, which is for the rules that change a figure, and withholding changes
    none.
    """

    def test_the_health_strip_and_the_panel(self):
        self.assertEqual(self.guard_for("renderHealth", "'names withheld'"),
                         "m.players")
        self.assertEqual(self.guard_for("renderHealth", "m.players_detail"),
                         "m.players")

    def test_the_player_page(self):
        self.assertEqual(self.guard_for("renderPlayer", "meta().players_detail"),
                         "meta().players")

    def test_the_rules_banner_is_untouched(self):
        shown = re.findall(r"\['([A-Z][a-z_]+)',\s*r\.", self.body("renderRulesBanner"))
        self.assertEqual(shown, ["Scoring", "Sets", "Unplayed", "Draws", "Format"])


class TestPlayerListsSortNumbersAsNumbers(WebAssetCase):
    """``Brindle Magpies #10`` sorts after ``#9`` in every list of players.

    Before, the four comparisons of a player's name compared as text, and as text
    ``#10`` comes between ``#1`` and ``#2``. Only those four: the ladder's tiebreak
    between two teams on one position compares teams' names, which withholding
    never changes, so it is pinned as it was. Rejected: every ``localeCompare`` of
    a ``.name``, which reorders the ladder's ties.
    """

    def test_the_four_comparisons(self):
        for fn, count in (("renderPlayers", 2), ("playersToCheck", 1),
                          ("fillPicker", 1)):
            with self.subTest(fn=fn):
                code = jslint.code_only(self.body(fn))
                calls = re.findall(r"localeCompare\(([^;]*?)\)(?=[\s;:)])", code)
                self.assertEqual(len(calls), count, calls)
                self.assertTrue(all(call.endswith("undefined, { numeric: true }")
                                    for call in calls), calls)

    def test_and_not_the_ladder(self):
        code = jslint.code_only(self.body("indexSeason"))
        ladder = code[code.index("idx.ladder = "):]
        ladder = ladder[:ladder.index("});")]
        self.assertIn("return String(a.name).localeCompare(String(b.name));", ladder)


class TestAOneFileDashboardDoesNotLinkToTheCareersPage(WebAssetCase):
    """The player page links its careers anchor only where the page can be there.

    The careers page is written at the site root (in either mode since 4.430,
    which moved the first two tests below: before it, only in multi-season mode),
    and a one-file dashboard travels without the site around it, so a link from
    one is a link to nothing. Nor by a build that withholds players' names, which writes no
    careers page (a withheld label belongs to one season), and whose documents are
    the only ones that carry ``meta.players``. The guard used to ask the mode and
    the file alone, so a withheld multi-season player page would link a careers
    page no withheld build writes. The link is built from ``rootHref()``, the one
    reader of how deep a dashboard sits, and names the page and the anchor by the
    constants site.py and careers.py write them under.
    """

    def test_the_link_is_only_made_in_a_site_page(self):
        # Moved by 4.430: was test_the_link_is_only_made_in_a_multi_season_site_page,
        # asserting the mode was in the guard. Every build printing names now writes
        # the page, so the mode is no longer one of the ways the link is kept off.
        guard = self.guard_for("careersHref", "'careers.html'", emits="return",
                               exits=True, disjunction=True)
        self.assertIn("isSingleFile()", guard)
        self.assertNotIn("seasons_mode", guard)

    def test_nor_on_a_page_that_withholds_players_names(self):
        guard = self.guard_for("careersHref", "'careers.html'", emits="return",
                               exits=True, disjunction=True)
        terms = [term.strip() for term in guard.split("||")]
        self.assertIn("meta().players", terms)
        # Moved by 4.430: three terms before it, the mode's the third.
        self.assertEqual(len(terms), 2, guard)

    def test_the_link_is_made_from_the_root_link_and_the_written_names(self):
        from matchcentre import careers, site
        body = jslint.without_comments(self.body("careersHref"))
        self.assertIn("rootHref()", body)
        self.assertIn(f"'{site.CAREERS_PAGE}'", body)
        self.assertIn(f"'#{careers.ANCHOR_PREFIX}'", body)

    def test_the_player_page_shows_the_link_only_when_there_is_one(self):
        body = jslint.without_comments(self.body("renderPlayer"))
        held = re.search(r"var (\w+) = careersHref\(", body)
        self.assertIsNotNone(held, "renderPlayer() never asks careersHref()")
        guard = self.guard_for("renderPlayer", "Career across seasons",
                               emits="push")
        self.assertEqual(guard, held.group(1))


class TestASingleSeasonPlayerPageLinksItsEntryOnTheSeasonsPlayersPage(WebAssetCase):
    """A player page in a single-season site links the season's players page too.

    Before 4.430 ``careersHref`` returned '' unless ``meta.seasons_mode`` was
    'multi', because only a multi-season build wrote the careers page; measured on
    the fictional three-competition season (/tmp/mcf/scratch/CLUB/measure/
    before.txt), none of its 19 player pages, 17 people's, had a way to the rest
    of a player's season. Now a single-season build that prints names writes the page as the
    season's players, so the link is kept off by the one-file dashboard and by
    withheld names alone, and is worded for the page it opens: "Career across
    seasons" where it lists seasons, "Season across competitions" where it lists
    one season's competitions. Rejected: "All competitions this season", which
    beside the topbar's "All competitions" reads as a second way to the front page.
    """

    def test_the_mode_is_not_one_of_the_ways_the_link_is_kept_off(self):
        guard = self.guard_for("careersHref", "'careers.html'", emits="return",
                               exits=True, disjunction=True)
        self.assertEqual(sorted(term.strip() for term in guard.split("||")),
                         ["isSingleFile()", "meta().players"])
        self.assertNotIn("seasons_mode", jslint.code_only(self.body("careersHref")))

    def test_the_link_is_worded_for_the_page_it_opens(self):
        body = jslint.without_comments(self.body("renderPlayer"))
        held = re.search(r"var (\w+) = careersHref\(", body)
        self.assertIsNotNone(held, "renderPlayer() never asks careersHref()")
        worded = re.findall(r"text: meta\(\)\.seasons_mode === 'multi'\s*\?\s*"
                            r"'([^']*)'\s*:\s*'([^']*)'", body)
        self.assertEqual(worded, [("Career across seasons →",
                                   "Season across competitions →")])
        for phrase in worded[0]:
            with self.subTest(phrase=phrase):
                self.assertEqual(self.guard_for("renderPlayer", phrase, emits="push"),
                                 held.group(1))


class TestTheCareersTopbarDoesNotCoverWhatAnAnchorLandsOn(WebAssetCase):
    """On the careers page the topbar scrolls away with the page.

    Every season page links a player's anchor there, and a sticky topbar would sit
    over the heading the link lands on.
    """

    def test_the_careers_topbar_is_static(self):
        self.assertIn('<body class="careers">', self.assets()["careers.html"])
        found = [rule for rule in csslint.rules(self.assets()["styles.css"])
                 if rule.selector == "body.careers .topbar" and not rule.at_rule]
        self.assertTrue(found, "styles.css never un-sticks the careers topbar")
        self.assertEqual(found[-1].value("position"), "static")


class TestAPairNamingOnePlayerMarksThePlaceNobodyFilled(WebAssetCase):
    """A doubles side naming one player shows the place left over as not recorded.

    ``names()`` in ``setRow`` marked a side 'not recorded' only where its list was
    empty, so a pair naming one player above Match Centre's 'Unspecified player'
    showed the one name alone, as a full side reads. Measured in headless Chrome
    on the match page of a fictional card, Riverside Rockets v Hilltop Aces
    played 6-1, 6-2, 6-3, 6-4 with the away pairs naming one player beside the
    placeholder (/tmp/mcf/scratch/UNSPEC/probe/page.py, case
    played-away-placeholder): "Doubles 1 Ana Fernandez + Bo Nakamura | 6 | 1 |
    Dev Raman | —", Doubles 2 the same, beside "Singles 2 ... not recorded" for
    the placeholder alone; the card with those cells blank read the same. Now
    each place the side's list leaves over of its rubber's, two for a doubles
    rubber and one for singles, follows the names as ' + not recorded', the
    mark an empty side already carries, and an empty side keeps its one mark.
    Marked by the slot, so a blank second cell is marked too: half that rubber
    is credited to nobody either way. Rejected: exporting each side's
    placeholder count so the page marks only a placeholder, two keys on every
    set of every season.json for a distinction the page draws for no empty
    cell; a word of its own, 'not entered', which makes one placeholder read
    two ways on one card; and a mark per place on an empty doubles side, which
    doubles the mark on every washout's and forfeit's rows.
    """

    MARK = r"el\('span',\s*\{\s*class:\s*'muted',\s*text:\s*'not recorded'\s*\}\)"
    # The places left over: counted up from the list's length, on a list that
    # names somebody, to the rubber's places, read off st.doubles, which every set
    # of season.json carries.
    LOOP = (r"for\s*\(\s*var\s+(\w+)\s*=\s*\(list\s*\|\|\s*\[\]\)\.length\s*;"
            r"\s*\1\s*&&\s*\1\s*<\s*\(st\.doubles\s*\?\s*2\s*:\s*1\)\s*;"
            r"\s*\1\+\+\s*\)\s*\{((?:[^{}]|\{[^{}]*\})*)\}")

    def names(self) -> str:
        """``names()`` inside ``setRow``, its comments taken out."""
        body = jslint.without_comments(self.body("setRow"))
        start = body.find("function names(")
        self.assertGreater(start, 0, "setRow defines no names()")
        end = body.find("return wrap;", start)
        self.assertGreater(end, start, "names() returns no wrap")
        return body[start:end]

    def test_each_place_left_over_follows_the_names_as_not_recorded(self):
        names = self.names()
        found = re.search(self.LOOP, names)
        self.assertIsNotNone(found, "names() marks no place a side's list leaves "
                                    "over of its rubber's, so a pair naming one "
                                    "player reads as a full side")
        # After the names, which the forEach lays out: marked before them, the
        # place left over would lead the pair's one name and run into it.
        laid = names.find("});", names.find(".forEach("))
        self.assertGreater(names.find(".forEach("), 0, "names() lays out no names")
        self.assertGreater(found.start(), laid,
                           "names() marks the places left over before the names")
        self.assertRegex(found.group(2),
                         r"^\s*wrap\.appendChild\(document\.createTextNode\("
                         r"' \+ '\)\);\s*wrap\.appendChild\(" + self.MARK
                         + r"\);\s*$")

    def test_an_empty_side_keeps_its_one_mark(self):
        names = self.names()
        self.assertEqual(len(re.findall(self.MARK, names)), 2)
        self.assertRegex(names, r"if\s*\(!list\s*\|\|\s*!list\.length\)\s*"
                                r"wrap\.appendChild\(" + self.MARK + r"\);")


# --- 4.428, 4.438, 4.439: the ladder's state, its leader, and the rounds played ---


class TestTheOverviewsHeroCrownsTheLeaderTheDocumentNames(WebAssetCase):
    """The hero's figure is ``meta.leader``'s, and with none it says why (4.438).

    It read ``ladder[0]``, the top row whatever the ladder's state, where the front
    page crowned a team only on a settled ladder. Measured on the base with round
    3's Rockets v Wanderers missing (/tmp/mcf/scratch/EMPTY/before.txt, prog): the
    hero ``6.30 | Riverside Rockets · 12.6 points from 2 matches`` over a front-page
    card naming no leader; and on a round rained off, ``— | Hilltop Aces · 0.0 points
    from 0 matches``, the first name in the alphabet crowned on nothing. Python names
    the leader once, for both, and the hero says why it names none. Rejected:
    crowning on both surfaces, which with five of the owner's ten divisions of five
    teams, a bye every round, names a leader on unequal counts all season against
    F10; and the page asking ``ladder_complete`` itself, a second copy of the rule.

    From 4.523 Python names the top of a ladder in progress ordered on the
    average, unless its top row is level on it with a team on another count, so
    the hero names it too. The in-progress reason is reached for one ordered on
    a total and for a document from before 4.523; one whose top row is level on
    the average with a team on another count is given the document's reason for
    that, ``meta.leader_withheld``. 4.523's review measured the hero giving
    ``No leader while teams have played unequal numbers of matches``, the
    sentence the maintainer reported, there instead, beside a division led on
    unequal counts (/tmp/mcf/lead/scratch/review-pages-r2/mixed.txt).
    Rejected: the hero inferring the tie from ``meta.ordered_on_average`` and
    a null leader, a second copy of Python's rule.
    """

    def hero(self) -> str:
        return jslint.without_comments(self.body("renderOverview"))

    def test_it_reads_the_leader_python_named_and_not_the_top_row(self):
        code = jslint.code_only(self.body("renderOverview"))
        self.assertNotIn("ladder[0]", code)
        self.assertIn("var top = m.leader ? teamOf(m.leader.slug) : null;", code)
        self.assertIn("text: top ? num(m.leader.value, 2) : '—'", self.hero())

    def test_with_no_leader_its_foot_says_why(self):
        self.assertRegex(self.hero(), r"text: top\s*\?\s*top\.name \+[^:]*"
                                      r":\s*noLeaderReason\(m\) \}")

    def test_a_ladder_in_progress_with_no_leader_says_the_counts_are_unequal(
            self):
        # 4.458 moved this: the reason is the document's sentence for what F10
        # compared, played or counted, and not played whatever it compared; a
        # document without the sentence falls through to No leader named.
        self.assertEqual(
            self.guard_for("noLeaderReason", "'No leader while ' + m.ladder_detail",
                           emits="return"),
            "m.ladder_state === 'in-progress' && m.ladder_detail")

    def test_a_top_row_level_across_counts_says_so_and_not_the_counts(self):
        self.assertEqual(
            self.guard_for("noLeaderReason",
                           "'No leader while ' + m.leader_withheld",
                           emits="return"),
            "m.ladder_state === 'in-progress' && m.leader_withheld")
        code = jslint.without_comments(self.body("noLeaderReason"))
        self.assertLess(code.index("m.leader_withheld"),
                        code.index("m.ladder_detail"))

    def test_a_ladder_not_started_says_nothing_has_been_counted(self):
        self.assertEqual(
            self.guard_for("noLeaderReason",
                           "'No regular-season match has been counted yet'",
                           emits="return"),
            "m.ladder_state === 'empty'")


class TestALadderNotStartedIsNotCalledOneInProgress(WebAssetCase):
    """The ladder's note and its history chart say nothing was counted (4.428).

    Measured on the base with round 1 rained off on both courts
    (/tmp/mcf/scratch/EMPTY/before.txt, wash1): ``In progress — teams have played
    unequal numbers of matches, so this order is a snapshot and not a standing.``
    over four teams on 0 matches, and ``Ladder position by round`` drawing R1 at 1, 2,
    3 and 4. The document places nobody now and puts no such round on the chart, so
    the chart's empty text is the one shown, and the note reads ``meta.ladder_state``.
    Rejected: the note working the state out from the rows' ``counted``, which is
    Python's rule (F10) a second time.
    """

    def test_the_note_says_no_match_has_been_counted(self):
        self.assertEqual(
            self.guard_for("ladderNote", "No regular-season match has been counted "
                           "yet", emits="return"),
            "m.ladder_state === 'empty'")

    def test_and_says_it_before_the_unequal_numbers_could_be(self):
        code = jslint.without_comments(self.body("ladderNote"))
        empty = code.index("m.ladder_state === 'empty'")
        # 4.458 moved this: the unequal numbers are the document's sentence now.
        self.assertLess(empty, code.index("m.ladder_detail"))
        self.assertNotIn("unequal", code[empty:code.index("No regular-season")
                                       + 200])

    def test_the_history_chart_waits_for_a_counted_match_and_not_a_round(self):
        code = jslint.without_comments(self.body("ladderHistoryChart"))
        self.assertIn("empty: 'Positions appear once a regular-season match has been "
                      "counted.'", code)


class TestATeamWithNoPlaceIsADashWhereverItsPlaceIsSaid(WebAssetCase):
    """A null ``position`` is said as ``—``, never as ``null`` (4.428).

    A ladder that has not started places nobody, so its rows' ``position`` is null,
    and ``String(null)`` is the word ``null`` on the page. The ladder's ``#`` column
    and the team page's tile said ``—`` already; the points-average chart's tooltip
    said ``String(t.position)``. No document reaches that one today: the chart draws
    only teams with an average, and a team with one has counted a match, so its
    ladder has started and placed it. It is guarded as the other two are all the
    same, so that nothing rests on that. Rejected: leaving it to that reasoning,
    which a change to who is given an average would undo with no test failing.
    """

    GUARD = "t.position === null || t.position === undefined ? '—' : "

    def test_every_position_said_as_text_has_a_dash_for_none(self):
        code = " ".join(jslint.without_comments(self.assets()["app.js"]).split())
        said = [match.start() for match in re.finditer(r"String\(t\.position\)", code)]
        self.assertTrue(said, "app.js no longer says a position as text")
        for at in said:
            with self.subTest(said=code[at - 80:at + 20]):
                self.assertTrue(code[:at].endswith(self.GUARD))


class TestTheProgressBoxSaysItsRoundCountAsACount(WebAssetCase):
    """``3 of 10 rounds played``, label and meter alike, not ``Round 3 of 10`` (4.439).

    ``meta.rounds_played`` counts the regular rounds with cards (4.69), and the
    label put that count in the shape of a round's label. Measured on the base with
    cards for rounds 2, 3 and 5 of ten (/tmp/mcf/scratch/EMPTY/before.txt, gap):
    ``Season progress | Round 3 of 10``, where the latest card is round 5's, over a
    meter whose aria-label already said ``3 of 10 rounds played``. Now one string
    says both. Rejected: the latest round's label, which a label is text and no
    ordinal, and which hides the round with no card.
    """

    def test_the_label_and_the_meter_say_one_count(self):
        code = jslint.without_comments(self.body("seasonProgress"))
        said = re.search(r"var (\w+) = playedRounds \+ ' of ' \+ total \+ ' ' \+ "
                         r"plural\(total, 'round'\) \+ ' played';", code)
        self.assertIsNotNone(said, code)
        self.assertIn(f"el('span', {{ text: {said[1]} }})", code)
        self.assertIn(f"'aria-label': {said[1]} }}", code)
        self.assertNotIn("'Round ' + playedRounds", code)



# --- 4.458, 4.468: what the ladder's state compared, and what the foot counts -------


class TestTheLadderNoteAndTheHeroSayWhatPythonCompared(WebAssetCase):
    """The note and the hero read ``meta.ladder_detail`` while in progress (4.458).

    Both said ``teams have played unequal numbers of matches`` and ``No leader until
    every team has played the same number of matches`` of F10's state, which is
    asked of counted matches. Measured on the base in headless Chrome
    (/tmp/mcf/scratch/LADDER/before2.txt, mirror-average): ``In progress — teams have
    played unequal numbers of matches`` over a P column of 1 for every team, whose
    counts were 2, 2, 1 and 1. Python words the state once, for the report, F10 and
    both pages. A document written before the key has no sentence: the note then
    says it is in progress and no more, and the hero ``No leader named``. Rejected:
    the page choosing the words from the rows' ``counted`` and ``played``, which is
    F10's rule a second time in the language the suite cannot run.
    """

    def test_the_note_reads_the_document_s_sentence(self):
        code = jslint.without_comments(self.body("ladderNote"))
        # The snapshot's own branch: 4.523's tie branch is spelt the same, and
        # satisfied this with the snapshot's sentence dropped (round 2 of the
        # fixes, review-tests-r2).
        snapshot = code[code.rindex("' In progress'"):]
        self.assertIn("so this order is a snapshot", snapshot)
        self.assertIn("' In progress' + (m.ladder_detail ? ' — ' + m.ladder_detail "
                      ": '') +", snapshot)
        self.assertNotIn("unequal numbers", code)

    def test_the_hero_reads_it_too(self):
        code = jslint.without_comments(self.body("noLeaderReason"))
        self.assertNotIn("played the same number", code)
        self.assertIn("'No leader while ' + m.ladder_detail", code)


class TestTheProgressFootCountsMatchesPlayedAndTheChipCardsScored(WebAssetCase):
    """The foot says matches played; the chip keeps cards scored (4.468).

    ``scored`` is F4's word for a card: read, and given a result or a reason, a
    washout among them. The foot counted matches with a result in the same word, so
    a card rained off was scored in the chip and not in the foot. Measured on the
    base in headless Chrome (/tmp/mcf/scratch/LADDER/before2.txt, foot-nolength, the
    suite's six cards and a washout): ``6 matches scored`` over ``7 of 7 cards
    scored``. Every other count of matches on the page says played. Rejected:
    rewording the chip, whose ``scored`` is the report's and F4's; and counting
    cards in the foot, which puts the washout back in a count of the rounds' matches
    beside the percentage of rounds played.
    """

    def test_both_shapes_of_the_foot_say_matches_played(self):
        foot = jslint.without_comments(self.body("seasonProgress"))
        said = re.findall(r"plural\(\w+\.played, 'match', 'matches'\) \+\s*' (\w+)",
                          foot)
        self.assertEqual(said, ["played", "played"])
        self.assertNotIn("' scored", foot)

    def test_the_chip_keeps_cards_scored(self):
        self.assertIn("' ' + plural(m.cards_seen || 0, 'card') + ' scored'",
                      jslint.without_comments(self.body("renderHealth")))


# --- 4.523: a ladder ordered on the average is led on unequal counts -------


class TestTheLadderNoteSaysAnOrderOnTheAverageStands(WebAssetCase):
    """The note's sentence for a ladder in progress, by what it is ordered on.

    An order on the average divides each team's points by its own counted
    matches, so while teams have counted unequal numbers its order stands
    wherever the average decides it, and the note said otherwise. Measured on
    the base in headless Chrome (/tmp/mcf/lead/scratch/build/before.txt, demo,
    five-byes and six-cancel): ``! In progress — teams have played unequal
    numbers of matches, so this order is a snapshot and not a standing.`` over
    a ladder ordered on the average. The document says whether the order
    stands on the counts it has, ``meta.order_stands``, from
    ``stats.order_stands``: true where the ladder is ordered on the average
    and no two teams are level on it on unequal counts. The note reads it:
    the counts in the document's sentence, and that the order stands, with no
    warning mark. Where two teams are level so, the note says that much is a
    snapshot
    (``TestTheLadderNoteSaysATieOnTheAverageAcrossCountsIsASnapshot``).
    A ladder ordered on a total, and a document from a release before the
    keys, keep the snapshot sentence. Rejected: the page reading
    ``rules.ladder_by[0]`` itself, a second copy of the rule in the language
    the suite cannot run (4.112).
    """

    def note(self) -> str:
        return jslint.without_comments(self.body("ladderNote"))

    def test_the_order_stands_only_where_the_document_says_so(self):
        # 4.523's review moved this: an order on the average stands where
        # no two teams are level on it on unequal counts, which is Python's
        # meta.order_stands and not the ruleset's meta.ordered_on_average.
        self.assertEqual(
            self.guard_for("ladderNote", "so the order stands",
                           emits="return"),
            "m.order_stands")

    def test_it_says_the_counts_in_the_document_s_sentence(self):
        code = self.note()
        branch = code[code.index("if (m.order_stands)"):
                      code.index("so the order stands")]
        # The whole clause: the guard alone holds the key's name, and passed
        # with the sentence dropped (4.523's review, review-tests-r2, J5).
        self.assertIn("(m.ladder_detail ? ' — ' + m.ladder_detail : '')",
                      " ".join(branch.split()))
        self.assertNotIn("unequal", branch)
        self.assertNotIn("ladder-note--incomplete", branch)
        self.assertNotIn("'!'", branch)
        # And the order it is on, as the snapshot gives it: the sentence
        # passed with basis dropped (mutate646).
        tail = code[code.index("so the order stands"):
                    code.index("if (m.ordered_on_average)")]
        self.assertIn("unplacedNote() + ' ' + basis }", " ".join(tail.split()))

    def test_a_ladder_not_started_is_said_first(self):
        code = self.note()
        self.assertLess(code.index("m.ladder_state === 'empty'"),
                        code.index("m.order_stands"))

    def test_any_other_ladder_in_progress_is_still_a_snapshot(self):
        # A document from a release before the keys has neither order_stands
        # nor ordered_on_average, so it reaches the sentence it was shown
        # before this row.
        code = self.note()
        self.assertLess(code.index("so the order stands"),
                        code.index("so this order is a snapshot and not a "
                                   "standing"))

    def test_the_page_asks_no_rule_of_its_own(self):
        # The note reads ladder_by for its "Ordered by" line, and asks nothing
        # of its first key.
        note = self.note()
        for asked in ("ladder_by[0]", "'points_average'"):
            with self.subTest(asked=asked):
                self.assertNotIn(asked, note)
        # The hero names whom meta.leader names, whatever the order.
        for fn in ("noLeaderReason", "renderOverview"):
            code = jslint.code_only(self.body(fn))
            for asked in ("ladder_by", "ordered_on_average", "order_stands"):
                with self.subTest(fn=fn, asked=asked):
                    self.assertNotIn(asked, code)


class TestTheLadderNoteSaysATieOnTheAverageAcrossCountsIsASnapshot(
        WebAssetCase):
    """The note where two teams are level on the average on unequal counts.

    Between two teams level on the average the next key decides, and under
    games-bonus that is points, a total, which the team that has counted more
    leads. Measured on 4.523's first draft
    (/tmp/mcf/lead/scratch/fix-r1/before.txt, tie and tiebelow): the note said
    ``so the order stands`` with no warning mark over the Rockets on 6.40 from
    2 above the Spinners on 6.40 from 1. Python says whether the order stands
    on these counts, ``meta.order_stands``, and where it does not on a ladder
    ordered on the average the note says the tie goes to the next thing the
    ladder is ordered by, named from ``rules.ladder_by`` as F10 names it, so
    there the order is a snapshot, with the warning mark. Round 2 of the
    fixes took out ``the next key decides between them``, the code's word
    for it and no other page string's (review-pages-r2). Rejected: the page
    finding the level teams in the rows itself, a second copy of Python's
    rule.
    """

    PHRASE = "the tie goes to ' + order[1]"

    def note(self) -> str:
        return jslint.without_comments(self.body("ladderNote"))

    def test_it_is_said_of_a_ladder_ordered_on_the_average(self):
        self.assertEqual(self.guard_for("ladderNote", self.PHRASE,
                                        emits="return"),
                         "m.ordered_on_average")

    def test_after_the_order_that_stands_and_before_the_snapshot(self):
        code = self.note()
        self.assertLess(code.index("so the order stands"),
                        code.index(self.PHRASE))
        self.assertLess(code.index(self.PHRASE),
                        code.index("so this order is a snapshot and not a "
                                   "standing"))

    def test_with_the_warning_mark_and_the_document_s_sentence(self):
        code = self.note()
        branch = code[code.index("if (m.ordered_on_average)"):
                      code.index(self.PHRASE)]
        self.assertIn("ladder-note--incomplete", branch)
        self.assertIn("'!'", branch)
        # The whole clause, as the order that stands is held (J7).
        self.assertIn("(m.ladder_detail ? ' — ' + m.ladder_detail : '')",
                      " ".join(branch.split()))
        # And the order it is on, which the sentence passed without
        # (mutate646).
        tail = code[code.index(self.PHRASE):
                    code.index("so this order is a snapshot and not a "
                               "standing")]
        self.assertIn("unplacedNote() + ' ' + basis }", " ".join(tail.split()))

    def test_it_names_the_next_thing_the_ladder_is_ordered_by(self):
        code = " ".join(self.note().split())
        self.assertIn("'with different numbers of matches counted, the tie "
                      "goes to ' + order[1] + ', so there the order is a "
                      "snapshot.'", code)
        self.assertNotIn("next key", code)


class TestTheLadderNoteSaysATeamWithNoMatchCountedIsPlacedLast(WebAssetCase):
    """Where the order on the average stands, a team on nought is said apart.

    A team with no match counted has no average, and the ladder places it
    last for want of one, by name among others on none (4.428), not on its
    record. Measured on 4.523's first draft
    (/tmp/mcf/lead/scratch/review-pages-r1/after.txt, six teams in round 1,
    one card cancelled): ``so the order stands`` over ``5 | Ashgrove Owls |
    0 | ... | —`` and ``6 | Fernlea Voles | 0 | ... | —``, while F10 said of
    them that they are placed last. The note now says so too, in a sentence
    of its own, asked of the rows' averages as ``averagedRoundsCounted`` asks
    of their counts. Rejected: a document key of its own, for a fact the rows
    already carry.
    """

    def test_both_sentences_on_the_average_say_it(self):
        code = jslint.without_comments(self.body("ladderNote"))
        stands = code[code.index("if (m.order_stands)"):
                      code.index("if (m.ordered_on_average)")]
        level = code[code.index("if (m.ordered_on_average)"):
                     code.index("so this order is a snapshot")]
        for name, branch in (("stands", stands), ("level", level)):
            with self.subTest(branch=name):
                self.assertIn("unplacedNote()", branch)
        self.assertNotIn("unplacedNote()",
                         code[code.index("so this order is a snapshot"):])

    def test_it_is_asked_of_the_rows_with_no_average(self):
        code = jslint.code_only(self.body("unplacedNote"))
        self.assertIn("IDX.ladder", code)
        self.assertIn("points_average === null", code)
        self.assertNotIn("counted", code)
        self.assertNotIn("ladder_by", code)

    def test_in_the_singular_and_the_plural_and_not_at_all_with_none(self):
        code = jslint.without_comments(self.body("unplacedNote"))
        self.assertIn("A team with no match counted has no average yet, so it "
                      "is placed last until it has one.", code)
        self.assertIn("Teams with no match counted have no average yet, so "
                      "they are placed last until they have one.", code)
        self.assertRegex(code, r"if \(!none\) return '';")
        # Which goes with which count, and the space before each: the two
        # swapped, or run on from the sentence before, passed the above
        # (4.523's review, review-tests-r2, J1 and J8).
        self.assertRegex(code, r"return none === 1\s*\?\s*' A team with no "
                               r"match[^']*'\s*:\s*' Teams with no match")



class TestAFinishedSeasonsLadderIsCalledFinal(WebAssetCase):
    """4.536: the pill, the note and the hero say final where Python says it is.

    Measured before on a season with every round in and a washout under ``zero``
    (/tmp/mcw/finals, ``TestASeasonOverOnUnequalCountsIsAFinalLadder``'s season):
    the pill read ``ladder in progress`` in the warning colour and the note ``In
    progress — teams have played unequal numbers of matches``. The page reads
    ``meta.ladder_final`` and works nothing out: the season is over by Python's
    rule (``stats.season_unfinished``). Rejected: the page asking whether
    ``rounds_played`` has reached ``rounds_total``, which is half that rule.
    """

    def test_the_pill_says_final_ladder_under_ladder_final(self):
        self.assertEqual(self.guard_for("renderHealth", "'final ladder'"),
                         "m.ladder_final")

    def test_in_progress_is_the_pill_only_where_it_is_not_final(self):
        code = jslint.without_comments(self.body("renderHealth"))
        final = code.index("if (m.ladder_final)")
        progress = code.index("'ladder in progress'")
        self.assertLess(final, progress)
        self.assertRegex(code[final:progress], r"\}\s*else if \(!m\.ladder_complete\)")

    def test_the_final_pill_is_not_a_warning(self):
        code = jslint.without_comments(self.body("renderHealth"))
        final = code.index("if (m.ladder_final)")
        self.assertRegex(code[final:code.index("'final ladder'")],
                         r"el\('span', \{ class: 'pill' \}")

    def test_the_note_says_final_under_ladder_final(self):
        self.assertEqual(self.guard_for("ladderNote", "'Final — every regular round '",
                                        emits="return"), "m.ladder_final")

    def test_the_note_names_unequal_counts_only_where_they_are(self):
        code = jslint.without_comments(self.body("ladderNote"))
        final = code[code.index("if (m.ladder_final)"):]
        final = final[:final.index("if (m.ladder_complete)")]
        self.assertIn("(m.ladder_complete || !m.ladder_detail ? '' : ', and ' + "
                      "m.ladder_detail)", final)

    def test_the_hero_heads_a_final_ladder_so(self):
        code = jslint.without_comments(self.body("renderOverview"))
        self.assertRegex(code, r"m\.ladder_final \? 'Top of the final ladder'\s*"
                               r": 'Ladder leader'\) \+ ' — points average'")


class TestThePlacingsAreDrawnAsPythonDecidedThem(WebAssetCase):
    """4.537: ``S.placings`` on the front page, the Matches page and the team page.

    The page decides nothing: who is paired, what placed each pair and why a pair
    waits are the document's, in ``placings`` (``stats.placings``), and the page
    says them. Its words for each outcome are the report's in the page's voice.
    """

    def test_the_front_page_has_a_placings_section_under_the_format_alone(self):
        self.assertEqual(self.guard_for("renderOverview", "sectionTitle('Placings')"),
                         "S.placings")

    def test_every_outcome_python_gives_has_words_on_the_page(self):
        from matchcentre import stats
        words = re.search(r"var PLACING_WORDS = \{(.*?)\};", self.assets()["app.js"],
                          re.S)
        self.assertIsNotNone(words, "app.js has no PLACING_WORDS")
        self.assertEqual(sorted(re.findall(r"'([a-z-]+)':", words.group(1))),
                         sorted(stats.PLACING_OUTCOMES))

    def test_before_the_pairs_are_set_it_says_what_they_wait_on(self):
        self.assertEqual(self.guard_for("placingsCard", "' Not yet: '"), "!p.set")
        code = jslint.without_comments(self.body("placingsCard"))
        self.assertIn("p.waiting ? ' Not yet: ' + p.waiting + '.' : ''", code)

    def test_a_row_opens_the_night_that_placed_it_by_its_printed_id(self):
        code = jslint.without_comments(self.body("placingsCard"))
        self.assertIn("var card = x.key ? IDX.matchByKey[x.key] : null;", code)
        self.assertIn("href: card && card.id ? '#/match/' + encodeURIComponent(card.id)"
                      " : null", code)

    def test_the_team_left_unpaired_is_a_row_of_its_own(self):
        self.assertEqual(self.guard_for("placingsCard", "'No match: keeps '",
                                        emits="push"), "p.unpaired")

    def test_the_matches_page_heads_the_week_as_placing_matches(self):
        code = jslint.without_comments(self.body("renderMatches"))
        self.assertIn("(S.placings ? 'Placing matches — ' : 'Finals — ')", code)

    def test_an_empty_placing_week_says_the_pairs_once_they_are_set(self):
        code = jslint.without_comments(self.body("renderMatches"))
        self.assertIn("S.placings && S.placings.set ? 'The final ladder pairs ' +",
                      code)

    def test_the_team_page_says_its_placing(self):
        code = jslint.without_comments(self.body("renderTeam"))
        self.assertIn("var placing = teamPlacing(t.slug);", code)
        self.assertEqual(self.guard_for("renderTeam", "{ class: 'sub', text: placing }"),
                         "placing")

    def test_a_team_s_placing_waits_for_the_pairs(self):
        code = jslint.without_comments(self.body("teamPlacing"))
        self.assertLess(code.index("if (!p || !p.set) return null;"),
                        code.index("p.unpaired"))

    def test_the_place_in_words_is_python_s_rule(self):
        # scope.ordinal: the teens take th, and 1, 2 and 3 their own.
        code = jslint.without_comments(self.body("ordinal"))
        self.assertIn("var teen = place % 100 >= 11 && place % 100 <= 13;", code)
        self.assertIn("var suffix = { 1: 'st', 2: 'nd', 3: 'rd' }[place % 10];", code)
        self.assertIn("return place + (teen || !suffix ? 'th' : suffix);", code)


if __name__ == "__main__":
    unittest.main(verbosity=2)
