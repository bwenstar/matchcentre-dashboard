#!/usr/bin/env python3
"""Write the README's screenshots, from the demo club's fictional season.

    python3 tools/screenshots.py                  # into docs/screenshots/
    python3 tools/screenshots.py --browser /usr/bin/chromium

This is a maintainer's tool, not something a club ever needs. It exists because a
dashboard project with no picture in its README is a dashboard project nobody
tries, and because the obvious way to get those pictures -- screenshot the
dashboard you happen to have open -- publishes a real club's players, teams and
ladder to GitHub. So the pictures are built instead: :mod:`tools.demo` invents a
club, its teams, its players and their ratings, ``build.py`` turns those into a
dashboard, and a headless browser photographs it. Nobody in the images exists.

Two properties worth keeping:

*The images are reproducible.* The demo's seed is fixed and the generated
timestamp is pinned, so re-running this on another machine produces the same
season, the same ladder and the same numbers. A screenshot that changed every
time it was regenerated would make every commit that touched it unreviewable.

*Nothing is required to view the result.* A browser is needed to take a
screenshot and there is no way around that, which is the one thing in this
project that reaches outside the standard library -- so it is confined to this
file, it is looked for rather than assumed, and its absence is explained rather
than raised. ``build.py``, ``tools/demo.py`` and ``tools/doctor.py`` are
unaffected and still need nothing but Python.

The window heights below are per view and were tuned by eye. A headless browser
photographs the window, not the document, so a height that is too small cuts the
ladder off and one that is too large pads the picture with empty background.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    # The only things this tool imports from the project, and the reason it imports
    # anything at all: `--version` has to answer with the same line the other three
    # commands answer with, the browsers it looked for are listed as the other
    # tools list, and its arguments are refused as theirs are. Everything else here
    # is deliberately run as a subprocess, so that what is photographed is what a
    # club runs.
    sys.path.insert(0, str(ROOT))

from matchcentre import version_line                          # noqa: E402
from matchcentre.cli import Parser                            # noqa: E402
from matchcentre.scope import in_words                        # noqa: E402

# Fixed so the season, and therefore every number in every image, is the same
# season next time. The date is pinned for the same reason: it is printed in the
# dashboard's header and its footer.
SEED = 7
GENERATED = "2026-03-01T09:00:00"
TEAMS = 6
ROUNDS = 3

WIDTH = 1280

# Browsers that can photograph a page from the command line, in the order they
# are tried. Each is asked for its own name; whichever answers first is used.
BROWSERS = ("google-chrome", "google-chrome-stable", "chromium",
            "chromium-browser", "chrome", "brave-browser", "microsoft-edge")

# One entry per image: the file it writes, the route it photographs, how tall to
# make the window, and what the picture is of. `slug` routes are filled in from
# the built season, so a shot is never of a page that does not exist.
#
# Each height is chosen to end just after a section rather than at a round number,
# so that no picture stops half way down a table -- which reads as a rendering bug
# in a README rather than as a cropped photograph. They depend on this demo's size
# (6 teams, 18 players, 3 rounds); change SEED, TEAMS or ROUNDS and they need
# retuning, which is why both live in this one file.
VIEWS: tuple[tuple[str, str, int, str], ...] = (
    ("ladder.png", "#/", 1710, "the ladder, with the rules that produced it"),
    ("matches.png", "#/matches", 1140, "every match, with its set scores"),
    ("players.png", "#/players", 1880, "every player's record"),
    ("team.png", "#/team/{team}", 2170, "one team: form, split and rubbers"),
    ("player.png", "#/player/{player}", 2180, "one player's season"),
)


def main(argv: list[str] | None = None) -> int:
    ap = Parser(prog="tools/screenshots.py", description=__doc__)
    ap.add_argument("--out", type=Path, default=ROOT / "docs" / "screenshots",
                    help="where to write the images (default docs/screenshots)")
    ap.add_argument("--browser", default=None, metavar="PATH",
                    help="the browser to photograph with; found automatically "
                         "if not given")
    ap.add_argument("--width", type=int, default=WIDTH,
                    help=f"window width in pixels (default {WIDTH})")
    ap.add_argument("--keep", action="store_true",
                    help="leave the built demo site in place and say where")
    ap.add_argument("--version", action="version", version=version_line(),
                    help="print this build and the Python running it, then stop")
    args = ap.parse_args(argv)

    browser = args.browser or _find_browser()
    if not browser:
        print("No browser found to photograph the dashboard with.", file=sys.stderr)
        print(f"  Looked for: {in_words(BROWSERS)}", file=sys.stderr)
        print("  Install one, or pass --browser /path/to/it. Nothing else in "
              "this project needs a browser;", file=sys.stderr)
        print("  this tool only writes the README's pictures.", file=sys.stderr)
        return 2

    work = Path(tempfile.mkdtemp(prefix="matchcentre-screenshots-"))
    try:
        site = _build_demo(work)
        routes = _routes(site)
        args.out.mkdir(parents=True, exist_ok=True)
        written = []
        for name, route, height, caption in VIEWS:
            target = args.out / name
            url = f"file://{site / 'index.html'}{route.format(**routes)}"
            if not _shoot(browser, url, target, args.width, height):
                print(f"error: {browser} wrote no image for {name}",
                      file=sys.stderr)
                return 1
            written.append((target, caption))
        print(f"wrote {len(written)} image(s) with {browser}:")
        for target, caption in written:
            size = target.stat().st_size
            print(f"  {_relative(target):<34}{size:>8,} bytes  {caption}")
        print()
        print("Every name, team and rating in these images is invented by "
              "tools/demo.py.")
        return 0
    finally:
        if args.keep:
            print(f"kept the built demo under {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)


def _find_browser() -> str | None:
    for name in BROWSERS:
        found = shutil.which(name)
        if found:
            return found
    return None


def _build_demo(work: Path) -> Path:
    """A demo season, built into ``work``, and the competition directory in it.

    Run as subprocesses rather than by importing, so that what is photographed is
    what the two documented commands produce -- and so a failure in either one is
    reported as that command failing.

    Both run *inside* ``work`` with relative paths, for two reasons. The dashboard
    prints the folder it was built from in its footer, and an absolute path would
    put this machine's throwaway temp directory into a published picture -- a
    different one every run, which would undo the pinned seed. And relative paths
    are what a club's own run records, so the footer in the screenshot says what
    theirs will say. The explicit ``--config`` keeps the run from reading or
    writing the config.toml of whoever is taking the screenshots.
    """
    data, site = work / "data", work / "site"
    _run((sys.executable, str(ROOT / "tools" / "demo.py"),
          "--out", "data", "--seed", str(SEED),
          "--teams", str(TEAMS), "--rounds", str(ROUNDS)), cwd=work)
    _run((sys.executable, str(ROOT / "build.py"),
          "--data", "data", "--out", "site",
          "--config", "config.toml", "--generated", GENERATED), cwd=work)
    competitions = sorted(p for p in site.iterdir()
                          if p.is_dir() and (p / "season.json").is_file())
    if not competitions:
        raise SystemExit(f"error: no competition dashboard was written under "
                         f"{site}")
    return competitions[0]


def _routes(site: Path) -> dict[str, str]:
    """A real team slug and a real player slug out of the built season.

    Read from the document rather than guessed, and chosen by who played most, so
    that the team and player pictures show a full page instead of the emptiest one
    in the competition.
    """
    season = json.loads((site / "season.json").read_text(encoding="utf-8"))
    teams = season.get("teams") or []
    players = season.get("players") or []
    if not teams or not players:
        raise SystemExit("error: the demo season has no teams or no players to "
                         "photograph")
    busiest = max(players, key=lambda p: (p.get("sets_played", 0), p["slug"]))
    return {"team": teams[0]["slug"], "player": busiest["slug"]}


def _shoot(browser: str, url: str, target: Path, width: int,
           height: int) -> bool:
    """Photograph one URL. Returns whether an image arrived.

    ``--user-data-dir`` points at a throwaway directory on purpose: without it a
    headless run shares the profile of whatever browser window the maintainer has
    open, which fails outright on some builds and silently reuses that profile's
    settings on the rest. ``--virtual-time-budget`` is what waits for the page's
    own JavaScript to render the ladder; the alternative is a fixed sleep and a
    picture that is sometimes of an empty page.
    """
    target.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix="matchcentre-profile-") as profile:
        base = (browser, "--headless", "--disable-gpu", "--hide-scrollbars",
                f"--user-data-dir={profile}", "--virtual-time-budget=8000",
                f"--window-size={width},{height}",
                f"--screenshot={target}", url)
        for command in (base, (*base[:1], "--no-sandbox", *base[1:])):
            subprocess.run(command, capture_output=True, text=True, timeout=600)
            if target.is_file() and target.stat().st_size:
                return True
    return False


def _run(command: tuple[str, ...], *, cwd: Path) -> None:
    done = subprocess.run(command, capture_output=True, text=True, timeout=1800,
                          cwd=cwd)
    if done.returncode != 0:
        raise SystemExit(f"error: {Path(command[1]).name} exited "
                         f"{done.returncode}\n{done.stdout}{done.stderr}")


def _relative(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


if __name__ == "__main__":
    raise SystemExit(main())
