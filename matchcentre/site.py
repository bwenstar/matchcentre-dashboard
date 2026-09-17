"""Write the site tree: one self-contained directory per competition, plus a picker.

Every competition directory is a *complete copy* of the dashboard -- its own
``index.html``, ``app.js``, ``styles.css``, ``season.json`` and
``season-data.js``. Copied rather than shared by reference, so a directory can be
zipped, emailed and opened on a machine with no web server and no internet, which
is the same constraint that makes ``season-data.js`` exist:

    a browser refuses to ``fetch()`` a sibling file over ``file://``, and
    double-clicking ``index.html`` has to work.

So the season document is written twice: once as JSON for anything that wants to
read the data, and once wrapped as ``window.SEASON_DATA = {...};`` for the page
itself.

Nothing in this module knows the name of a club, a season, a theme or a colour.
The predecessor's picker hardcoded one club's title and one club's
``localStorage`` key, which meant a second club's dashboard announced the first
club's name. Every such string here comes from the config, the club or the season
documents, and the page templates come from ``matchcentre/web/``.

The picker template is filled in by sentinel comment rather than by ``format()``
or ``%``, because an HTML document is full of braces and percent signs. The
sentinels a ``picker.html`` may contain are listed in :data:`PICKER_SENTINELS`;
all are optional except that the template must offer *some* way to receive the
competition list -- either ``<!--COMPETITIONS-->`` or a reference to
``picker-data.js`` -- or the picker would be written as an empty page, which is
the kind of silent nothing this project refuses to produce.
"""

from __future__ import annotations

import html
import json
import shutil
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from . import __version__, export
from .competition import Club, Competition
from .config import Config, default_title
from .slugs import slugify

__all__ = ["write_site", "write_competition", "SiteError", "PAGE_ASSETS",
           "PICKER", "GENERATED", "PICKER_SENTINELS", "WEB"]

# Where the page templates live. Another module's files, read-only from here.
WEB = Path(__file__).resolve().parent / "web"

# Copied into every competition directory. Not named with a leading underscore:
# GitHub Pages and Jekyll strip underscore-prefixed paths, which would 404 the
# whole dashboard the moment a club published it.
PAGE_ASSETS = ("index.html", "app.js", "styles.css")

PICKER = "picker.html"

# What this module writes into a competition directory. Anything else found there
# belongs to a human and the run refuses rather than overwriting it.
GENERATED = PAGE_ASSETS + ("season.json", "season-data.js")

# The picker's data island, written only when the template asks for it.
PICKER_DATA = "picker-data.js"

# The "All competitions" link in index.html, as the template ships it: correct for
# a single-season site, where a competition sits one level under the root. In
# multi-season mode it is one level deeper, and this exact string is rewritten on
# the way out.
#
# Rewritten here rather than left to the browser because the depth is known at
# this point. app.js retargets the link too, but markup that is only corrected by
# script is wrong for as long as the script takes to run, and stays wrong if it
# never runs at all -- and the file the wrong version points at is a directory,
# which a browser opening a file:// page renders as a folder listing.
HOME_LINK = 'class="home-link" href="../index.html"'

# Sentinel comments a picker.html may contain. Every one is optional and any that
# is absent is simply not substituted, so a template can render as much or as
# little of this server-side as it likes.
PICKER_SENTINELS = ("<!--TITLE-->", "<!--SUBTITLE-->", "<!--COMPETITIONS-->",
                    "<!--FOOTER-->", "<!--GENERATED-->", "<!--THEME-KEY-->")

_DAY_ORDER = ("monday", "tuesday", "wednesday", "thursday", "friday",
              "saturday", "sunday")


class SiteError(Exception):
    """The site cannot be written. Always names the file at fault."""


# --- one competition --------------------------------------------------------


def write_competition(out_dir: str | Path, season: dict) -> list[Path]:
    """Write one self-contained competition directory.

    Returns every path written, in the order written, so the caller can report
    the tree it produced without walking it back off disk.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    depth = _root_depth(season)
    for name in PAGE_ASSETS:
        source = WEB / name
        if not source.is_file():
            raise SiteError(
                f"missing page template {source}: it is copied into every "
                f"competition directory and the dashboard cannot open without it")
        if name == "index.html" and depth != 1:
            (out / name).write_text(_pointed_home(source, depth), encoding="utf-8")
        else:
            shutil.copy2(source, out / name)
        written.append(out / name)

    export.write_season(out / "season.json", season)
    written.append(out / "season.json")

    (out / "season-data.js").write_text(_season_data_js(season), encoding="utf-8")
    written.append(out / "season-data.js")
    return written


def _root_depth(season: dict) -> int:
    """How many levels up the front page sits from this competition's directory.

    One in single-season mode (``site/<competition>/``) and two in multi-season
    mode (``site/<season>/<competition>/``), which is the same nesting
    :func:`_competition_dir` lays out, read from the same two fields.
    """
    meta = season.get("meta", {}) if isinstance(season, dict) else {}
    multi = meta.get("seasons_mode") == "multi" and meta.get("season_key")
    return 2 if multi else 1


def _pointed_home(source: Path, depth: int) -> str:
    """``index.html``, with its home link aimed *depth* levels up at the picker.

    A template that no longer carries the link verbatim stops the run rather than
    shipping a page whose only route home is a folder listing.
    """
    page = source.read_text(encoding="utf-8")
    if page.count(HOME_LINK) != 1:
        raise SiteError(
            f"{source} does not contain exactly one {HOME_LINK!r}: it is the "
            f"link back to the front page, and a competition nested {depth} "
            f"levels down needs it rewritten before the page will work")
    return page.replace(HOME_LINK,
                        f'class="home-link" href="{"../" * depth}index.html"')


def _season_data_js(season: dict) -> str:
    """``season.json`` wrapped as a script.

    The reason it exists, kept where the next reader will find it: a browser
    refuses to ``fetch()`` a sibling file over ``file://``, so a dashboard opened
    by double-clicking ``index.html`` -- which is how a club opens it -- can only
    receive its data through a ``<script>`` tag.

    Same sort order as ``season.json`` so the two files stay diffable against
    each other and neither churns between runs.
    """
    body = json.dumps(season, indent=2, sort_keys=True, ensure_ascii=False,
                      allow_nan=False)
    return ("// Generated by build.py -- do not edit.\n"
            "// A copy of season.json as a script, because a browser will not\n"
            "// fetch() a sibling file over file:// and this page has to open by\n"
            "// double-click.\n"
            f"window.SEASON_DATA = {body};\n")


# --- the whole tree ---------------------------------------------------------


def write_site(out_dir: str | Path, *, seasons: Sequence[dict], club: Club,
               config: Config, generated: str | None = None) -> list[Path]:
    """Write every competition directory plus the picker at the root.

    Both the template check and the stranger-file check happen up front, for the
    whole tree, rather than as each directory is written. A run that got three
    ladders onto disk and then refused the fourth would leave the picker linking
    to a dashboard that is not there.
    """
    out = Path(out_dir)
    _require_templates()

    targets = [(_competition_dir(out, season, multi=config.multi_season), season)
               for season in seasons]

    strangers = [complaint for directory, _ in targets
                 for complaint in _strangers_in(directory)]
    if strangers:
        raise SiteError("\n".join(strangers))

    written: list[Path] = []
    for directory, season in targets:
        written.extend(write_competition(directory, season))
    written.extend(_write_picker(out, seasons, club=club, config=config,
                                 generated=generated))
    return written


def _require_templates() -> None:
    if not WEB.is_dir():
        raise SiteError(f"{WEB} does not exist; it holds the page templates "
                        f"({', '.join((*PAGE_ASSETS, PICKER))}) that every "
                        f"dashboard is built from")
    missing = [name for name in (*PAGE_ASSETS, PICKER)
               if not (WEB / name).is_file()]
    if missing:
        raise SiteError("missing page template(s): "
                        + ", ".join(str(WEB / name) for name in missing))


def _competition_dir(out: Path, season: dict, *, multi: bool) -> Path:
    """``site/<competition-id>/``, or ``site/<season>/<competition-id>/``.

    The competition id already carries the season key in multi mode, so the
    season directory is redundant as an identifier. It is here because a club
    keeping five years of seasons wants to see five folders, not fifty.
    """
    meta = season.get("meta", {})
    comp_id = str(meta.get("competition", ""))
    if not comp_id or not _safe_segment(comp_id):
        raise SiteError(f"season document has an unusable competition id "
                        f"{comp_id!r}; it becomes a directory name")
    if not multi:
        return out / comp_id
    key = str(meta.get("season_key", ""))
    if key and not _safe_segment(key):
        raise SiteError(f"season document has an unusable season key {key!r}; "
                        f"it becomes a directory name")
    return (out / key / comp_id) if key else (out / comp_id)


def _safe_segment(value: str) -> bool:
    """Whether a string may be used as one path segment.

    Ids come from :func:`~matchcentre.competition.competition_id`, which slugs
    them, so this can only fail if something upstream stopped doing that. Checked
    anyway: the failure it would otherwise produce is a write outside ``out_dir``.
    """
    return value not in ("", ".", "..") and not any(c in value for c in "/\\:")


def _strangers_in(out_dir: Path) -> list[str]:
    """Complaints about files in a competition directory this tool did not write.

    A generated directory is cheap to rebuild; a page someone hand-edited in it
    is not. Note the asymmetry: the five files in :data:`GENERATED` *are*
    overwritten without asking, because they are this tool's own output. Dotfiles
    are ignored -- an editor's swap file or a ``.DS_Store`` is not a reason to
    refuse to build a season.
    """
    if not out_dir.exists():
        return []
    if not out_dir.is_dir():
        return [f"{out_dir} is a file; the dashboard needs that name for a "
                f"directory"]
    strangers = sorted(p.name for p in out_dir.iterdir()
                       if p.name not in GENERATED
                       and not p.name.startswith("."))
    if not strangers:
        return []
    return [f"{out_dir} holds file(s) this tool did not write "
            f"({', '.join(strangers)}); move them aside or build into a "
            f"different --out directory"]


# --- the picker -------------------------------------------------------------


def _write_picker(out: Path, seasons: Sequence[dict], *, club: Club,
                  config: Config, generated: str | None) -> list[Path]:
    """The landing page: every competition the club runs, linked when it has data.

    Every competition is listed, including any that has no cards yet, because
    from the outside "this competition has no results yet" and "this tool has
    never heard of it" look identical and only one of them is something the
    reader can wait out. Only the ones with a dashboard behind them are links.
    """
    template = (WEB / PICKER).read_text(encoding="utf-8")
    by_id = {str(s.get("meta", {}).get("competition", "")): s for s in seasons}

    entries = _entries(club, by_id, multi=config.multi_season)
    stamp = generated or max((s["meta"]["generated"] for s in seasons), default="")

    filled = template
    values = {
        "<!--TITLE-->": html.escape(_title(club, config)),
        "<!--SUBTITLE-->": html.escape(_subtitle(entries, config)),
        "<!--COMPETITIONS-->": _sections_html(entries, multi=config.multi_season),
        "<!--FOOTER-->": _footer_html(stamp),
        "<!--GENERATED-->": html.escape(stamp),
        "<!--THEME-KEY-->": html.escape(_theme_key(club, config)),
    }
    for sentinel in PICKER_SENTINELS:
        filled = filled.replace(sentinel, values[sentinel])

    wants_data = PICKER_DATA in template
    if "<!--COMPETITIONS-->" not in template and not wants_data:
        raise SiteError(
            f"{WEB / PICKER} offers nowhere to put the competition list: it must "
            f"contain the sentinel <!--COMPETITIONS--> or load {PICKER_DATA}. "
            f"Writing it as it stands would produce a picker with no links on it")

    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    (out / "index.html").write_text(filled, encoding="utf-8")
    written.append(out / "index.html")

    if wants_data:
        # Only written when the template asks for it, so the tree never carries a
        # file nothing reads.
        (out / PICKER_DATA).write_text(
            _picker_data_js(entries, club=club, config=config, generated=stamp),
            encoding="utf-8")
        written.append(out / PICKER_DATA)

    # A self-contained picker.html carries its own CSS, but one that links the
    # dashboard's stylesheet needs a copy beside it: the root is not a
    # competition directory and would otherwise 404.
    for name in PAGE_ASSETS:
        if name != "index.html" and name in template:
            shutil.copy2(WEB / name, out / name)
            written.append(out / name)
    return written


def _title(club: Club, config: Config) -> str:
    """The site's own heading, from the config, never from this file.

    The fallback comes from :func:`matchcentre.config.default_title` rather than
    being written out again here, so that the heading on a first run and the
    ``title =`` line that same run writes into ``config.toml`` cannot disagree.
    """
    return config.title or default_title(club.name)


def _theme_key(club: Club, config: Config) -> str:
    """A ``localStorage`` key unique to this club's site.

    Derived rather than hardcoded because two clubs' dashboards can end up served
    from one origin -- a shared Pages site, or a folder holding both -- and a
    single fixed key means either one silently overwrites the other's light/dark
    choice. That is a bug nobody reports, because nobody can describe it.
    """
    for candidate in (club.name, config.title):
        if candidate:
            try:
                return f"{slugify(candidate)}-theme"
            except ValueError:
                continue
    return "matchcentre-theme"


def _subtitle(entries: Sequence[dict], config: Config) -> str:
    """One line of arithmetic under the title. Says nothing about seasons in single mode."""
    live = sum(1 for e in entries if e["has_data"])
    total = len(entries)
    if not total:
        return "No competitions found yet"
    body = f"{live} of {total} competition{'' if total == 1 else 's'} with results"
    if config.multi_season:
        keys = {e["season_key"] for e in entries if e["season_key"]}
        if keys:
            body += f" across {len(keys)} season{'' if len(keys) == 1 else 's'}"
    return body


def _entries(club: Club, by_id: dict[str, dict], *, multi: bool) -> list[dict]:
    """One plain-data record per competition, ordered the way the picker shows them.

    Plain dicts rather than objects because these feed both the server-rendered
    HTML and ``picker-data.js``, and the two must not be able to disagree.
    """
    known = {comp.id: comp for comp in club.competitions}
    # A competition with a season document but no entry in the club is a bug
    # upstream rather than a reason to lose a dashboard, so it is listed too.
    ids = sorted(set(known) | set(k for k in by_id if k))
    entries = [_entry(known.get(comp_id), by_id.get(comp_id), comp_id,
                      multi=multi)
               for comp_id in ids]
    entries.sort(key=_entry_sort_key)
    return entries


def _entry(comp: Competition | None, season: dict | None, comp_id: str, *,
           multi: bool) -> dict:
    meta = (season or {}).get("meta", {})
    played = meta.get("rounds_played", 0)
    total = meta.get("rounds_total")
    label = (comp.display_label if comp else "") or meta.get("label") or comp_id
    entry = {
        "id": comp_id,
        "label": label,
        "day": (comp.day if comp else "") or meta.get("day", ""),
        "division": (comp.division if comp else "") or meta.get("division", ""),
        "href": _href(comp_id, meta, multi=multi),
        "has_data": season is not None,
        "teams": meta.get("teams_total", len(comp.teams) if comp else 0),
        "cards": meta.get("cards_scored", 0),
        "rounds_played": played,
        # Null unless declared (F3). The card shows a bar only when it is not,
        # because "3 rounds played" is a fact and "30%" needs a denominator.
        "rounds_total": total,
        "percent": (min(100, round(100 * played / total))
                    if total else None),
        "ladder_complete": meta.get("ladder_complete", False),
        "draw_source": meta.get("draw_source", ""),
        "errors": len((season or {}).get("validation", {}).get("errors", ())),
        "warnings": len((season or {}).get("validation", {}).get("warnings", ())),
        "rejected": meta.get("cards_rejected", 0),
        "generated": meta.get("generated", ""),
        "leader": _leader(season),
    }
    # Season fields exist only in multi mode: in single mode the picker must not
    # mention seasons at all, and the surest way to honour that is to give the
    # renderer nothing to mention.
    if multi:
        entry["season_key"] = meta.get("season_key", "") or (
            comp.season_key if comp else "")
        entry["season_label"] = meta.get("season_label", "") or (
            comp.season if comp else "")
    else:
        entry["season_key"] = ""
        entry["season_label"] = ""
    return entry


def _href(comp_id: str, meta: dict, *, multi: bool) -> str:
    """The link from the front page to one competition's dashboard.

    Names ``index.html`` rather than ending at the directory. A web server would
    fill that in; a browser opening the file directly will not, and shows a
    directory listing instead of the dashboard. This tool promises that
    double-clicking works, so the file is named.
    """
    key = meta.get("season_key", "") if multi else ""
    return f"{key}/{comp_id}/index.html" if key else f"{comp_id}/index.html"


def _leader(season: dict | None) -> dict | None:
    """The team currently on top, with the number its position rests on.

    Only shown when the ladder is a ladder: with unequal games played the top row
    is a snapshot, and the picker says so instead of crowning anyone (F10).
    """
    if season is None or not season.get("meta", {}).get("ladder_complete"):
        return None
    top = next((t for t in season.get("teams", ()) if t.get("position") == 1),
               None)
    if top is None:
        return None
    value = top.get("points_average")
    if value is None:
        value = top.get("points")
    if value is None:
        return None
    return {"name": top.get("short") or top.get("name") or top.get("slug", ""),
            "value": value}


def _entry_sort_key(entry: dict) -> tuple:
    """Newest season first, then the week as it runs, then division.

    Reverse-lexical on the season key puts ``2026-winter`` above ``2025-summer``
    without this module having to know what a club calls its seasons.
    """
    day = entry["day"].strip().casefold()
    day_rank = _DAY_ORDER.index(day) if day in _DAY_ORDER else len(_DAY_ORDER)
    return (_reverse_key(entry["season_key"]), day_rank, entry["day"],
            entry["division"], entry["id"])


def _reverse_key(value: str) -> tuple[int, ...]:
    """A sort key that orders strings descending inside an ascending tuple."""
    return tuple(-ord(c) for c in value)


# --- picker rendering -------------------------------------------------------


def _sections_html(entries: Sequence[dict], *, multi: bool) -> str:
    """The competition list: grouped by season then day, or by day alone."""
    if not entries:
        return ('    <p class="empty">No scorecards have been read yet. Copy '
                'your exported PDFs into <code>data/</code> and run '
                '<code>python3 build.py</code> again.</p>')
    # Grouped on the season key and day, not on the heading text. Measured on a
    # two-season tree in this session: both seasons' labels came off the cards as
    # "Season 2", so grouping by heading merged them and the picker announced four
    # competitions in one season where two seasons had run two each. The key is the
    # season's identity; the label is only what it is called.
    groups: list[tuple[tuple[str, str], str, list[dict]]] = []
    for entry in entries:
        ident = (entry["season_key"] if multi else "", entry["day"])
        if groups and groups[-1][0] == ident:
            groups[-1][2].append(entry)
        else:
            groups.append((ident, _heading(entry, multi=multi), [entry]))

    # Two groups left showing the same heading would be indistinguishable, so the
    # key that makes them different is shown as well. Only when it happens: a key
    # like "2025-summer" next to a label the club chose is noise the rest of the
    # time.
    repeats = {heading for heading in
               (h for _, h, _ in groups)
               if [h for _, h, _ in groups].count(heading) > 1}

    out: list[str] = []
    for ident, heading, members in groups:
        if heading and heading in repeats and ident[0]:
            heading = f"{heading} ({ident[0]})"
        live = sum(1 for e in members if e["has_data"])
        count = (f'<span class="count">{live} of {len(members)} with results'
                 f'</span>')
        cards = "\n".join(_card_html(e) for e in members)
        head = (f'    <h2>{html.escape(heading)}{count}</h2>\n'
                if heading else f'    <h2>{count}</h2>\n')
        out.append(f'{head}    <div class="picks">\n{cards}\n    </div>')
    return "\n".join(out)


def _heading(entry: dict, *, multi: bool) -> str:
    day = entry["day"]
    if multi:
        season = entry["season_label"] or entry["season_key"]
        return f"{season} - {day}".strip(" -") if (season or day) else ""
    return day


def _card_html(entry: dict) -> str:
    """One competition's card. A link when it has data, inert when it does not."""
    name = html.escape(entry["label"])
    teams = f"{entry['teams']} team{'' if entry['teams'] == 1 else 's'}"

    if not entry["has_data"]:
        return ('      <div class="pick pick-empty">\n'
                f'        <span class="pick-top"><span class="pick-name">{name}'
                '</span><span class="tag tag-quiet">No results yet</span></span>\n'
                f'        <span class="pick-facts">{teams} &middot; awaiting '
                'scorecards</span>\n'
                '      </div>')

    lines: list[str] = []
    played = entry["rounds_played"]
    if entry["percent"] is not None:
        tag = f"Round {played} of {entry['rounds_total']}"
    else:
        tag = f"{played} round{'' if played == 1 else 's'} played"
    lines.append(f'        <span class="pick-top">'
                 f'<span class="pick-name">{name}</span>'
                 f'<span class="tag">{html.escape(tag)}</span></span>')
    if entry["percent"] is not None:
        # Only ever drawn against a declared round count. A bar whose denominator
        # was the number of rounds played would read 100% every week (F3).
        lines.append('        <span class="bar" aria-hidden="true">'
                     f'<span style="width:{entry["percent"]}%"></span></span>')

    leader = entry["leader"]
    if leader:
        lines.append('        <span class="pick-lead"><strong>'
                     f'{html.escape(str(leader["name"]))}</strong> lead on '
                     f'{leader["value"]:.2f}</span>')
    elif entry["cards"]:
        lines.append('        <span class="pick-lead">Ladder in progress '
                     '&mdash; teams have played unequal numbers of matches'
                     '</span>')

    facts = f"{teams} &middot; {entry['cards']} card" \
            f"{'' if entry['cards'] == 1 else 's'}"
    if entry["generated"]:
        facts += f" &middot; {html.escape(_pretty_date(entry['generated']))}"
    lines.append(f'        <span class="pick-facts">{facts}</span>')

    if entry["draw_source"] and entry["draw_source"] != "declared":
        lines.append('        <span class="flag">Rounds read from the cards '
                     '&mdash; no draw declared</span>')
    if entry["errors"]:
        lines.append(f'        <span class="flag flag-bad">{entry["errors"]} '
                     f'validation error'
                     f'{"" if entry["errors"] == 1 else "s"}</span>')
    if entry["rejected"]:
        lines.append(f'        <span class="flag flag-bad">{entry["rejected"]} '
                     f'card{"" if entry["rejected"] == 1 else "s"} could not be '
                     f'read</span>')

    body = "\n".join(lines)
    return (f'      <a class="pick" href="{html.escape(entry["href"])}">\n'
            f'{body}\n      </a>')


def _footer_html(generated: str) -> str:
    """The front page's footer, naming the build that wrote it.

    The version is on both this page and each competition's page rather than only
    on one, because a club that emails a single competition folder has sent the
    dashboard without this page attached.
    """
    foot = (f"Generated by <code>build.py</code> {html.escape(__version__)} "
            f"from the club's own scorecards")
    if generated:
        foot += f" &middot; {html.escape(_pretty_date(generated, with_time=True))}"
    return foot


def _picker_data_js(entries: Sequence[dict], *, club: Club, config: Config,
                    generated: str) -> str:
    """The picker's data island, for a template that renders client-side.

    Same records the server-rendered HTML is built from, so the two cannot drift.
    """
    payload = {
        "title": _title(club, config),
        "subtitle": _subtitle(entries, config),
        "seasons_mode": config.seasons_mode,
        "theme_key": _theme_key(club, config),
        "generated": generated,
        "competitions": list(entries),
    }
    body = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False,
                      allow_nan=False)
    return ("// Generated by build.py -- do not edit.\n"
            f"window.PICKER_DATA = {body};\n")


def _pretty_date(stamp: str, *, with_time: bool = False) -> str:
    """``2026-09-08T16:54:07`` -> ``8 Sep``, or ``8 Sep, 4:54pm``.

    Hand-rolled rather than ``strftime``: ``%-d`` is a glibc extension, and
    ``%d`` prints "08 Sep", which reads like a machine wrote it.
    """
    try:
        when = datetime.fromisoformat(stamp)
    except ValueError:
        return stamp
    out = f"{when.day} {when:%b}"
    if with_time:
        hour = when.hour % 12 or 12
        out += f", {hour}:{when:%M}{'pm' if when.hour >= 12 else 'am'}"
    return out
