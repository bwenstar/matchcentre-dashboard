"""The dashboard's browser code, checked without a browser.

There is no JavaScript engine on the machine this was built on, so none of this
code can be run by its own test suite. That is a real gap and this module is the
honest response to it rather than a pretence that it does not exist: everything
here is a structural check, and each one is aimed at a failure that would show a
club a *blank page* or a *silently wrong page* -- the two outcomes no amount of
careful Python upstream can prevent.

Three kinds of check live here.

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
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import jslint                                             # noqa: E402

WEB = ROOT / "matchcentre" / "web"
APP = WEB / "app.js"
INDEX = WEB / "index.html"
STYLES = WEB / "styles.css"
PICKER = WEB / "picker.html"

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
    # Says the ladder is a snapshot because teams have played unequal matches.
    ("ladder-note", "class"),
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


class WebAssetCase(unittest.TestCase):
    """Base class that skips nothing: a missing asset is a failure, not a skip."""

    def assets(self) -> dict[str, str]:
        missing = [p.name for p in (APP, INDEX, STYLES, PICKER) if not p.is_file()]
        if missing:
            self.fail(f"missing page template(s) in {WEB}: {', '.join(missing)}")
        return {"app.js": read(APP), "index.html": read(INDEX),
                "styles.css": read(STYLES), "picker.html": read(PICKER)}

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


class TestTheAssetsExist(WebAssetCase):

    def test_every_page_asset_site_py_copies_is_present(self):
        # Checked against site.py's own constants rather than a list repeated
        # here, so the two cannot drift apart and both be self-consistent.
        from matchcentre import site
        for name in (*site.PAGE_ASSETS, site.PICKER):
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
        for name in (*site.PAGE_ASSETS, site.PICKER, *site.GENERATED):
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
        for name in ("index.html", "picker.html"):
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


class TestTheDomContractHolds(WebAssetCase):

    def test_every_agreed_hook_is_in_both_the_script_and_the_page(self):
        assets = self.assets()
        app = assets["app.js"]
        markup = assets["index.html"] + assets["styles.css"]
        for hook, kind in DOM_CONTRACT:
            with self.subTest(hook=hook, kind=kind):
                self.assertIn(hook, app, f"app.js never mentions {hook!r}, so "
                                         f"whatever it was to carry is silently "
                                         f"absent from the page")
                self.assertIn(hook, markup, f"neither index.html nor styles.css "
                                            f"mentions {hook!r}, so app.js will "
                                            f"look for it and find nothing")

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

    def test_every_sentinel_the_picker_uses_is_one_site_py_substitutes(self):
        # A sentinel comment that site.py does not know about is never replaced,
        # so it survives into the published page as a visible artefact.
        from matchcentre import site
        template = self.assets()["picker.html"]
        used = set(re.findall(r"<!--[A-Z][A-Z-]*-->", template))
        unknown = sorted(used - set(site.PICKER_SENTINELS))
        self.assertEqual(unknown, [], f"picker.html contains sentinel(s) site.py "
                                      f"does not substitute: {unknown}")


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
        for name in ("index.html", "picker.html"):
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
        for name in ("app.js", "index.html", "picker.html"):
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
        for name in ("index.html", "picker.html"):
            text = assets[name]
            for pattern in (r"\bDivision\s+[A-Z]\b", r"\bTuesday\s+Division\b"):
                with self.subTest(page=name, pattern=pattern):
                    self.assertEqual(re.findall(pattern, text), [])


class TestThePageCountsTheSameMatchesThePythonDoes(WebAssetCase):
    """``Match.counts_as_played``, mirrored in JavaScript, and kept in step with it.

    The season document carries computed aggregates, and the page computes a few of
    its own on top of them: the overview tiles, and each player's per-set trail. If
    the page's idea of a match that happened differs from the exporter's, the tile
    saying "10 matches played" sits directly above a ladder whose P column adds to
    eight -- and nothing errors, because both numbers are internally consistent.

    The list of statuses is taken from the Python by running it, not repeated here,
    so a status added to one side cannot pass by being forgotten on the other.
    """

    def excluded_statuses(self) -> set[str]:
        from matchcentre import format as fmt_mod, scoring
        from matchcentre.model import Match, MatchStatus
        comp = dict(competition="x", round="1", home="a", away="b",
                    fmt=fmt_mod.match_format("doubles-singles-doubles"),
                    rules=scoring.ruleset("games-bonus"))
        return {s.value for s in MatchStatus
                if not Match(status=s, **comp).counts_as_played}

    def test_the_page_has_the_predicate_at_all(self):
        self.assertIn("function countsAsPlayed(",
                      jslint.code_only(self.assets()["app.js"]))

    def test_it_leaves_out_exactly_the_statuses_the_python_leaves_out(self):
        # Read from the source with its strings intact: the status names *are*
        # string literals, so code_only() would blank the only thing under test.
        body = self.body("countsAsPlayed")
        excluded = self.excluded_statuses()
        self.assertEqual(len(excluded), 3, "the Python side has changed shape")
        for status in excluded:
            with self.subTest(status=status):
                self.assertIn(f"'{status}'", body,
                              f"the page still counts a {status} match")
        for status in {"played", "forfeit", "disputed"}:
            with self.subTest(status=status):
                self.assertNotIn(f"'{status}'", body,
                                 f"the page has stopped counting a {status} match")

    def test_the_overview_tiles_go_through_it(self):
        # Matches played, sets completed and games played are all derived from this
        # one filter, and it is the tile row a club reads first.
        self.assertRegex(self.body("renderOverview"), r"countsAsPlayed\b")

    def test_a_players_own_set_trail_goes_through_it_too(self):
        # The trail is where a member checks their own season. A cancelled night
        # appearing on it is a rubber they are told they played and did not.
        self.assertRegex(self.body("setsOfPlayer"), r"countsAsPlayed\s*\(")

    def test_nothing_tests_a_match_status_by_hand_any_more(self):
        # Two hand-written filters drift; one shared predicate cannot. Any new
        # comparison against these status strings outside countsAsPlayed() is that
        # drift starting again.
        predicate = self.body("countsAsPlayed")
        rest = self.assets()["app.js"].replace(predicate, "")
        for status in self.excluded_statuses() - {"bye"}:
            with self.subTest(status=status):
                self.assertNotRegex(
                    rest, r"status\s*[!=]==?\s*'" + status + r"'",
                    f"a hand-written {status} test outside countsAsPlayed()")


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


def _rules_block() -> dict:
    """The ``rules`` block of a season document, from an empty competition.

    Taken from the exporter's own output rather than from a list of names repeated
    here, so the page and the document cannot drift apart and both be
    self-consistent. Empty because the only thing under test is which keys exist.
    """
    from matchcentre import export, format as fmt_mod, scoring
    from matchcentre.competition import Club, Competition
    comp = Competition(id="x", day="Monday", division="C",
                       fmt=fmt_mod.match_format("doubles-singles-doubles"),
                       rules=scoring.ruleset("games-bonus"))
    return export.build_season(
        [], competition=comp, source_dir=Path("."), generated="",
        club=Club(name="", competitions=(comp,)),
        cards=[], rejected=[])["meta"]["rules"]


if __name__ == "__main__":
    unittest.main(verbosity=2)
