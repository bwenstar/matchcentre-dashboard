#!/usr/bin/env python3
"""Run build.py for a scheduler: build only when something it reads has changed,
and keep what each build printed in reports/.

    python3 tools/scheduled.py                   # what cron or Task Scheduler runs
    python3 tools/scheduled.py --print-schedule  # the line to give it

Every relative path given here, and every default, is taken from the project
folder and not from wherever the scheduler starts: cron starts in a home folder
and Task Scheduler in C:\\Windows\\System32, and neither is where the cards are.

A run first notes the size and time of every file build.py would read -- the
scorecard folder, the settings file, the program itself -- and of the dashboard
folder it left behind. When none of them has moved since the last build it stops
there, having read no card. Otherwise it runs build.py, which reads every card
exactly as it does when a person runs it, and keeps the whole report in a file
in reports/ named by the date and time it ran, so the newest is last. A report
that says exactly what the one before it said is not written again.

It prints one line, and exits with build.py's status: 0 a clean build, 1 a build
with problems to look at, 2 one that could not run. A run that found nothing new
exits with the status of the build it skipped, so a scheduler that alerts on a
failure keeps alerting until it is fixed. When a build printed players' names in a
dashboard that withheld them before it, the line ends by saying where and goes to
stderr, past a cron line's >/dev/null, and the exit is at least 1. A skip of that
build says the same, until a build finds nothing withheld.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import build as build_mod                                       # noqa: E402
from matchcentre import config as config_mod                    # noqa: E402
from matchcentre.cli import Parser                              # noqa: E402
from matchcentre import pasteable, scorecard, site, status, version_line  # noqa: E402

RECORD = "last-run.json"
FORMAT = 2
INTERVAL = 15
REPORT_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2}_\d{6})(?:_(\d+))?\.txt$")
DEFAULT_REPORTS = "reports"
DEFAULT_KEEP = 30
DEFAULT_TIMEOUT = 10.0
# subprocess.CREATE_NO_WINDOW, which only exists on Windows. Spelt out so the
# function that uses it can be tested anywhere.
CREATE_NO_WINDOW = 0x08000000
# The longest a build is waited for, whatever --timeout says. The wait ends in a
# poll() that takes milliseconds as a C int, so --timeout 50000000 was an
# OverflowError after the build had started rather than a long wait. A week is
# past any build this program will ever run, and far past the next scheduled one.
LONGEST_WAIT = 7 * 24 * 60 * 60

NO_RECORD = "there is no record of an earlier build"
INPUT_REASONS = (("folders", "it was given different folders"),
                 ("program", "the program changed"),
                 ("config", "the settings file changed"),
                 ("data", "the scorecard folder changed"))
SITE_REASON = "the dashboard folder changed"


class Ran(NamedTuple):
    """One run of build.py as a child: how it ended and everything it printed."""
    code: int | None             # None when it was stopped or never started
    output: str
    stopped: bool
    started: bool
    seconds: float


# --- the command line -------------------------------------------------------


def main(argv: list[str] | None = None, *, root: Path = ROOT) -> int:
    """Build if anything moved, keep the report, print one line.

    *root* is the project folder: where build.py is, and what every relative path
    is taken from. It is a parameter so that the tests can run this against a
    folder of their own holding a stand-in build.py; build.py is always the one
    in *root*, because that is the one whose files the fingerprint covers.
    """
    try:
        args = _parse_args(argv)
    except SystemExit as exc:            # --help, --version, or a refused flag
        return exc.code if isinstance(exc.code, int) else 2
    windows = os.name == "nt"
    root = Path(root)
    data = _resolve(args.data, build_mod.DEFAULT_DATA, root)
    out = _resolve(args.out, build_mod.DEFAULT_OUT, root)
    config = _resolve(args.config, config_mod.DEFAULT_PATH, root)
    reports = _resolve(args.reports, DEFAULT_REPORTS, root)
    keep = DEFAULT_KEEP if args.keep is None else args.keep
    timeout = DEFAULT_TIMEOUT if args.timeout is None else args.timeout

    # Before --print-schedule as well as before a build: a line that would be
    # refused on every run is a line that fails every fifteen minutes into
    # nobody's mailbox.
    refused = _refusal(reports, out=out, data=data)
    if refused:
        for line in refused:
            _say("stderr", line)
        return 2

    if args.print_schedule:
        unusable = _unusable(reports)
        if unusable:
            _say("stderr", unusable)
            return 2
        extra = [*_given_paths(args, data=data, out=out, config=config,
                               reports=reports),
                 *(("--keep", str(args.keep)) if args.keep is not None else ()),
                 *(("--timeout", f"{args.timeout:g}")
                   if args.timeout is not None else ())]
        _say("stdout", schedule_text(
            windows=windows, python=_scheduler_python(sys.executable, windows),
            script=str(root / "tools" / "scheduled.py"), extra=extra,
            mac_protected=_mac_protected(root, data, out, config, reports)
        ).rstrip("\n"))
        return 0

    # The inputs before the build, the site after it: a card that lands while the
    # build is running is then a difference the next run sees.
    inputs = fingerprint(data=data, config=config, out=out, root=root)
    record = read_record(reports)
    why = reasons(record, inputs, site_digest(out))
    if not why:
        when = site.pretty_date(record["finished"], with_time=True)
        back = record["names_back"]
        code = wrapper_exit(record["exit"], back)
        _say_line(f"nothing has changed since the build at {when}: exit {code}, "
                  f"report {reports / record['report']}", back, until="that build")
        return code

    try:
        reports.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _say("stderr", f"error: {reports}: cannot be created — {exc}")
        return 2

    started = datetime.now()
    withheld = withheld_dashboards(out)
    argv_child = [_child_python(sys.executable, windows), str(root / "build.py"),
                  *_passthrough(args)]
    ran = _run_build(argv_child, cwd=root, timeout=timeout * 60, windows=windows)
    if ran.stopped:
        status.record_failure(out, "too-long", timeout)
    elif ran.code not in (0, 1, 2):
        # Ended on a signal, or never started (code None): build.py reached no line
        # that could record it, and a clean line left over from the build before
        # would say the figures are current.
        status.record_failure(out, "unfinished")
    back = names_back(withheld, out)
    code = wrapper_exit(ran.code, back)
    header = _header(started=started, why="; ".join(why), argv=argv_child, ran=ran,
                     timeout=timeout, windows=windows, back=back)
    try:
        report, wrote = write_report(reports, started=started, header=header,
                                     body=_body(ran, timeout))
    except OSError as exc:
        _say("stderr", f"error: {reports}: cannot be written — {exc}")
        return 2
    if wrote:
        prune_reports(reports, keep)

    try:
        write_record(reports, {
            "exit": ran.code,
            "finished": datetime.now().isoformat(timespec="seconds"),
            "format": FORMAT,
            "inputs": inputs,
            "names_back": back,
            "report": report.name,
            "site": site_digest(out),
        })
    except OSError as exc:
        # The build happened and its report is kept, so this is not a failure of
        # the run. It costs the next run a build it might have skipped, and says so.
        _say("stderr", f"warning: {reports / RECORD}: cannot be written — {exc}")

    tail = f"report {report}" if wrote else f"same report as {report}"
    if ran.stopped:
        said = f"stopped a build that ran past {status.minutes(timeout)}"
    elif not ran.started:
        said = "could not start build.py"
    else:
        said = f"built because {'; '.join(why)}"
    _say_line(f"{said}: exit {code}, {tail}", back, until="this run")
    return code


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    ap = Parser(prog="tools/scheduled.py", description=__doc__)
    ap.add_argument("--data", metavar="DIR",
                    help="passed to build.py; a relative path is taken from the "
                         "project folder (default: data/)")
    ap.add_argument("--out", metavar="DIR",
                    help="passed to build.py; a relative path is taken from the "
                         "project folder (default: site/)")
    ap.add_argument("--config", metavar="FILE",
                    help=f"passed to build.py; a relative path is taken from the "
                         f"project folder (default: {config_mod.DEFAULT_PATH})")
    ap.add_argument("--reports", metavar="DIR",
                    help="where each build's report is kept; a relative path is "
                         "taken from the project folder (default: reports/)")
    ap.add_argument("--keep", metavar="N", type=int,
                    help=f"how many reports to keep, newest last; at least 2 "
                         f"(default: {DEFAULT_KEEP})")
    ap.add_argument("--timeout", metavar="MINUTES", type=float,
                    help=f"stop a build that runs longer than this "
                         f"(default: {DEFAULT_TIMEOUT:g})")
    ap.add_argument("--print-schedule", action="store_true",
                    help="print the line to give cron or Task Scheduler, then stop")
    ap.add_argument("--version", action="version", version=version_line(),
                    help="print this build and the Python running it, then stop")
    args = ap.parse_args(argv)
    # Checked here rather than as argparse types, whose refusals come out as
    # "argument --keep: --keep must ..." -- the flag named twice in one line.
    # An empty path is the project folder (Path("") is "."), which is what a
    # script passes when its variable came out empty: refused before anything
    # is made or printed, while it can still be told from a "." typed on purpose.
    for name, kind in (("data", "folder"), ("out", "folder"), ("config", "file"),
                       ("reports", "folder")):
        if getattr(args, name) == "":
            ap.error(f"--{name} is empty: name a {kind}, or leave it out")
    if args.keep is not None and args.keep < 2:
        ap.error("--keep must be at least 2: the report before the newest is the "
                 "one to compare it with")
    if args.timeout is not None and not math.isfinite(args.timeout):
        ap.error(f"--timeout must be a number of minutes, not {args.timeout:g}")
    if args.timeout is not None and args.timeout <= 0:
        ap.error("--timeout must be more than 0 minutes")
    return args


def _resolve(given: str | None, default: str, root: Path) -> Path:
    """A path the scheduler was given, or the default, taken from *root*."""
    path = Path(given if given is not None else default)
    return path if path.is_absolute() else root / path


def _overlaps(a: Path, b: Path) -> bool:
    """Whether one folder is the other or holds it, in either direction."""
    a, b = a.resolve(), b.resolve()
    return a == b or a in b.parents or b in a.parents


def _refusal(reports: Path, *, out: Path, data: Path) -> list[str] | None:
    """What to say when --reports overlaps the published folder or the cards."""
    for flag, other, why in (
            ("--out", out, "A report names every player, and --out is the folder "
                           "that gets published."),
            ("--data", data, "That folder is read on every build.")):
        if _overlaps(reports, other):
            return [f"error: --reports {reports} and {flag} {other} overlap.",
                    build_mod.fill(f"{why} Keep the reports in a folder of their "
                                   f"own beside it.")]
    return None


def _unusable(reports: Path) -> str | None:
    """The error a run would meet making or writing in *reports*, found without either.

    For ``--print-schedule``, which creates nothing: a line every run of which
    would stop at the ``mkdir`` or at the first report is not a line to hand a
    scheduler. The run itself still asks the operating system, and says its errno.
    """
    nearest = reports
    while not os.path.lexists(nearest) and nearest.parent != nearest:
        nearest = nearest.parent
    verb = "written" if nearest == reports else "created"
    if not nearest.is_dir():
        return f"error: {reports}: cannot be created — {nearest} is not a folder"
    if not os.access(nearest, os.W_OK | os.X_OK):
        return (f"error: {reports}: cannot be {verb} — this account may not write "
                f"in {nearest}")
    return None


def _given_paths(args: argparse.Namespace, **resolved: Path) -> list[str]:
    """The path flags given on this command line, as absolute paths."""
    return [part for name, path in resolved.items()
            if getattr(args, name) is not None
            for part in (f"--{name}", str(path))]


def _passthrough(args: argparse.Namespace) -> list[str]:
    """build.py's own flags, as typed and only when typed.

    As typed because build.py runs in the project folder, so it resolves them to
    the paths this resolved, and its report then names them as the club wrote
    them. Nothing else is passed: an unattended build never prunes, never
    rewrites the settings, and always prints the whole report.
    """
    return [part for name in ("data", "out", "config")
            if getattr(args, name) is not None
            for part in (f"--{name}", getattr(args, name))]


def _say(stream_name: str, text: str) -> None:
    """Print to stdout or stderr, if there is one.

    Under ``pythonw.exe``, which is how Task Scheduler runs this without a window,
    both are None, and a print would end the run with an exception nobody sees.
    """
    stream = getattr(sys, stream_name)
    if stream is not None:
        print(text, file=stream)


def _say_line(line: str, back: str, *, until: str) -> None:
    """Print the one line, ending by saying where names are back when *back* does.

    That line is on stderr: the cron line ends ``>/dev/null``, which throws stdout
    away and keeps stderr for cron to mail. *until* is the build that put them
    back, ``this run`` or, on a skip, ``that build``.
    """
    if back:
        line += (f"; players' names are back in {back}, which withheld them until "
                 f"{until}")
    _say("stderr" if back else "stdout", line)


# --- what a build reads, and what it left -----------------------------------


def _stat_lines(base: Path, paths) -> list[str]:
    """One line per path: where it is under *base*, its size and its time."""
    lines = []
    for path in paths:
        try:
            name = path.relative_to(base).as_posix()
        except ValueError:
            name = str(path)
        try:
            found = path.stat()
        except OSError:
            lines.append(f"{name}\tgone\n")
        else:
            lines.append(f"{name}\t{found.st_size}\t{found.st_mtime_ns}\n")
    return lines


def _digest(lines) -> str:
    return hashlib.sha256("".join(lines).encode("utf-8", "surrogatepass")
                          ).hexdigest()


def _data_lines(data: Path) -> list[str]:
    """The scorecard folder as the reader lists it, every file and not only PDFs.

    The reader's own list, so that what is watched cannot drift from what is read:
    a stray file changes the report, because the report names it. A folder in it
    that cannot be listed is watched by its own name where its files would be,
    because the report refuses it by that name.
    """
    found = scorecard.files_under(data)
    return ["unlisted\n"] if found is None else _stat_lines(data, found)


def _program_lines(root: Path) -> list[str]:
    """build.py, the package beside it (templates included), and the Python."""
    package = root / "matchcentre"
    files = sorted((p for p in package.rglob("*")
                    if p.is_file() and "__pycache__" not in p.parts
                    and p.suffix != ".pyc"), key=str) if package.is_dir() else []
    return [*_stat_lines(root, [root / "build.py", *files]),
            f"python\t{platform.python_version()}\n",
            f"executable\t{sys.executable}\n"]


def _config_line(config: Path) -> str:
    try:
        found = config.stat()
    except OSError:
        return "absent"
    return f"{found.st_size}\t{found.st_mtime_ns}"


def fingerprint(*, data: Path, config: Path, out: Path, root: Path) -> dict[str, str]:
    """A digest of each thing a build reads, taken before the build."""
    return {
        "config": _digest([_config_line(config)]),
        "data": _digest(_data_lines(data)),
        "folders": _digest(f"{path}\n" for path in (data, out, config)),
        "program": _digest(_program_lines(root)),
    }


def site_digest(out: Path) -> str:
    """A digest of what is under --out: a site deleted or edited is rebuilt."""
    if not out.is_dir():
        return _digest(["missing"])
    try:
        files = sorted((p for p in out.rglob("*") if p.is_file()), key=str)
    except OSError:
        return _digest(["unlisted"])
    return _digest(_stat_lines(out, files))


def withheld_dashboards(out: Path) -> list[Path]:
    """Every dashboard under *out* whose own document says it withheld names.

    Read off each ``season.json`` by :func:`site.players_of`, the rule build.py's
    report reads the same documents by, before the build and again after it: the
    report's ``players`` line says when names went back on, and this is how the
    one line can say it too without reading the report's prose, which wraps.
    """
    return [folder for folder in site._dashboards_under(out)
            if site.players_of(site._stale_meta(folder)) != "names"]


def names_back(withheld: list[Path], out: Path) -> str:
    """Where the build printed names that were withheld before it; empty for nowhere.

    A document that no longer reads says nothing either way, so only one that
    reads and says ``names`` counts. One dashboard is named, and more are counted.
    Kept in the record, so that a skip can say it again.
    """
    back = [folder for folder in withheld
            if (meta := site._stale_meta(folder)) and site.players_of(meta) == "names"]
    if not back:
        return ""
    return str(back[0]) if len(back) == 1 else f"{len(back)} dashboard(s) under {out}"


def wrapper_exit(code: int | None, back: str) -> int:
    """The wrapper's exit, for a build and for a skip of one: one rule for both.

    build.py's 0, 1 or 2, anything else a 2, and at least 1 when names are back
    where a dashboard withheld them. That is the owner's rule: under a scheduler
    the line reaches nobody, and the exit is what cron and Task Scheduler show.
    """
    status = code if code in (0, 1, 2) else 2
    return max(status, 1) if back else status


def reasons(record: dict | None, inputs: dict[str, str], site: str) -> list[str]:
    """Why this run has to build, in words; nothing means it need not.

    The whole skip rule. A build that could not run, or did not finish, is never
    skipped: a permission refused or a drive that did not mount moves no file,
    so a skip would repeat that failure until a card happened to change. A Python
    traceback is exit 1, as a build with problems to look at is, and is skipped
    like one until something moves; the fix for it is a change to the program,
    and the program is one of the things watched.
    """
    if record is None:
        return [NO_RECORD]
    why = []
    if record["exit"] is None:
        why.append("the last build did not finish")
    elif record["exit"] not in (0, 1):
        why.append("the last build could not run")
    why.extend(words for key, words in INPUT_REASONS
               if inputs[key] != record["inputs"][key])
    if site != record["site"]:
        why.append(SITE_REASON)
    return why


# --- running it -------------------------------------------------------------


def _child_python(executable: str, windows: bool) -> str:
    """``python.exe`` beside a ``pythonw.exe``: the child needs real streams."""
    path = Path(executable)
    if windows and path.name.lower() == "pythonw.exe":
        console = path.with_name("python.exe")
        if console.is_file():
            return str(console)
    return executable


def _scheduler_python(executable: str, windows: bool) -> str:
    """``pythonw.exe`` beside the Python, for a task that opens no window."""
    if windows:
        quiet = Path(executable).with_name("pythonw.exe")
        if quiet.is_file():
            return str(quiet)
    return executable


def _popen_options(windows: bool) -> dict:
    return {"creationflags": CREATE_NO_WINDOW} if windows else {}


def _run_build(argv: list[str], *, cwd: Path, timeout: float,
               windows: bool) -> Ran:
    """Run build.py, stopping it at *timeout* seconds; never raises for it."""
    env = dict(os.environ, PYTHONUTF8="1")
    begun = time.monotonic()
    try:
        child = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 env=env, **_popen_options(windows))
    except OSError as exc:
        return Ran(None, f"build.py could not be started: {exc}\n", False, False,
                   time.monotonic() - begun)
    stopped = False
    try:
        output, _ = child.communicate(timeout=min(timeout, LONGEST_WAIT))
    except subprocess.TimeoutExpired:
        child.kill()
        output, _ = child.communicate()
        stopped = True
    text = (output or b"").decode("utf-8", errors="replace").replace("\r\n", "\n")
    return Ran(None if stopped else child.returncode, text, stopped, True,
               time.monotonic() - begun)


def _took(seconds: float) -> str:
    whole = round(seconds)
    if whole < 120:
        return "1 second" if whole == 1 else f"{whole} seconds"
    return f"{round(seconds / 60)} minutes"


def _posix_line(argv: list[str]) -> str:
    """A command for a POSIX shell, each word quoted by the build's one rule.

    shlex quoted these, a second rule beside the one the build, the demo and the
    fetcher print their lines with: ``'"'"'`` for a quote where they print
    ``'\\''``. A Windows line keeps its own rule, since cmd reads neither, and
    this one is POSIX whatever machine prints it, so it says so to ``pasteable``.
    """
    return " ".join(pasteable(part, windows=False) for part in argv)


def _header(*, started: datetime, why: str, argv: list[str], ran: Ran,
            timeout: float, windows: bool, back: str = "") -> str:
    command = subprocess.list2cmdline(argv) if windows else _posix_line(argv)
    code = wrapper_exit(ran.code, back)
    if ran.stopped:
        ended = f"2 (build.py was stopped after {status.minutes(timeout)})"
    elif not ran.started:
        ended = "2 (build.py could not be started)"
    elif ran.code not in (0, 1, 2):
        ended = f"2 (build.py ended with status {ran.code})"
    elif code != ran.code:
        ended = f"{code} (build.py exited {ran.code}, but players' names are back)"
    else:
        ended = str(ran.code)
    return (f"tools/scheduled.py ran build.py at {started:%Y-%m-%d %H:%M:%S}\n"
            f"  because   {why}\n"
            f"  command   {command}\n"
            f"  took      {_took(ran.seconds)}\n"
            f"  exit      {ended}\n")


def _body(ran: Ran, timeout: float) -> str:
    """What build.py printed, with a last line when it did not end by itself."""
    text = ran.output
    if not ran.started:
        return text
    if text and not text.endswith("\n"):
        text += "\n"
    if ran.stopped:
        text += f"-- build.py was stopped here, after {status.minutes(timeout)} --\n"
    elif ran.code not in (0, 1, 2):
        text += f"-- build.py ended here, with status {ran.code} --\n"
    return text


# --- reports/ ----------------------------------------------------------------


def _report_key(name: str) -> tuple[str, int]:
    """Newest last: the time in the name, then the collision number (_2, _10)."""
    found = REPORT_NAME.match(name)
    return (found.group(1), int(found.group(2) or 1))


def _reports_in(reports: Path) -> list[Path]:
    """The files here named as this tool names a report, oldest first."""
    try:
        found = [p for p in reports.iterdir()
                 if REPORT_NAME.match(p.name) and p.is_file()]
    except OSError:
        return []
    return sorted(found, key=lambda p: _report_key(p.name))


def newest_report(reports: Path) -> Path | None:
    found = _reports_in(reports)
    return found[-1] if found else None


def _body_of(report: Path) -> str | None:
    """A kept report's body: everything after the header's closing empty line."""
    try:
        text = report.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    header, gap, body = text.partition("\n\n")
    return body if gap else None


def write_report(reports: Path, *, started: datetime, header: str,
                 body: str) -> tuple[Path, bool]:
    """Keep this run's report, unless the newest one already says exactly it.

    Returns the report that holds this run's output and whether it was written
    now. Two runs whose output differs only in the header -- when, why, how long
    -- are one report: a build failing the same way every fifteen minutes would
    otherwise fill the folder in a day and push out the last good report.
    """
    newest = newest_report(reports)
    if newest is not None and _body_of(newest) == body:
        return newest, False
    stamp = f"{started:%Y-%m-%d_%H%M%S}"
    number = 1
    while True:
        path = reports / (f"{stamp}.txt" if number == 1 else f"{stamp}_{number}.txt")
        try:
            with open(path, "x", encoding="utf-8", newline="\n") as handle:
                handle.write(f"{header}\n{body}")
        except FileExistsError:
            number += 1
            continue
        return path, True


def prune_reports(reports: Path, keep: int) -> None:
    """Keep the newest *keep* reports and delete older ones; nothing else is touched."""
    for path in _reports_in(reports)[:-keep]:
        try:
            path.unlink()
        except OSError as exc:
            _say("stderr", f"warning: could not remove old report {path}: {exc}")


def _usable(record) -> bool:
    """Whether a record read back is one this version wrote, whole."""
    if not isinstance(record, dict):
        return False
    inputs = record.get("inputs")
    code = record.get("exit")
    try:
        datetime.fromisoformat(record.get("finished"))
    except (TypeError, ValueError):
        return False
    return (type(record.get("format")) is int and record["format"] == FORMAT
            and (code is None or type(code) is int)
            and isinstance(inputs, dict)
            and sorted(inputs) == sorted(key for key, _ in INPUT_REASONS)
            and all(isinstance(value, str) for value in inputs.values())
            and isinstance(record.get("site"), str)
            and isinstance(record.get("names_back"), str)
            and isinstance(record.get("report"), str)
            and REPORT_NAME.match(record["report"]) is not None)


def read_record(reports: Path) -> dict | None:
    """What the last build saw, or None for anything that is not a usable record.

    None costs one build. Anything else, trusted wrongly, could skip one.
    """
    try:
        record = json.loads((reports / RECORD).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return record if _usable(record) else None


def write_record(reports: Path, record: dict) -> None:
    """Replace the record in one step, so a run stopped half way leaves the old one."""
    path, spare = reports / RECORD, reports / f"{RECORD}.tmp"
    try:
        spare.write_text(json.dumps(record, sort_keys=True, indent=2) + "\n",
                         encoding="utf-8", newline="\n")
        os.replace(spare, path)
    except OSError:
        try:
            spare.unlink()
        except OSError:
            pass
        raise


# --- --print-schedule --------------------------------------------------------


POSIX_TEXT = """\
To build the dashboard every {interval} minutes, give cron this line:

*/{interval} * * * * {command} >/dev/null

Run:  EDITOR=nano crontab -e
{paste}
"""

PASTE = ("Paste the line at the end, on a line of its own, then press Ctrl-O, "
         "Enter and Ctrl-X. If the computer says crontab is not found, it has no "
         "cron yet: install your system's cron package (called cron or cronie).")

MAC = ("On a Mac, cron is not allowed into Documents, Desktop or Downloads, and "
       "this schedule uses a folder in one of them. Move it somewhere else, or give "
       "cron Full Disk Access: the README section \"Running it on a schedule\" has "
       "the steps.")

WINDOWS_TEXT = """\
In Task Scheduler, create a task whose action is "Start a program", and fill in:

  Program/script:  {python}
  Add arguments:   {arguments}
  Start in:        leave it empty

Then, in the task's Properties:

  Triggers     edit the trigger, tick "Repeat task every", choose {interval} minutes,
               and set "for a duration of" to Indefinitely
  Settings     leave "If the task is already running" at "Do not start a new
               instance"
  Conditions   untick "Start the task only if the computer is on AC power"
  General      if the computer sits at the sign-in screen, choose "Run whether
               user is logged on or not"

The README section "Running it on a schedule" has every step.
"""


def _quoted(argument: str) -> str:
    """One Task Scheduler argument, always in double quotes.

    A backslash that ends the argument would escape the closing quote, so a
    trailing run of them is doubled, which is how Windows reads them back.
    """
    trailing = len(argument) - len(argument.rstrip("\\"))
    return f'"{argument}{chr(92) * trailing}"'


def schedule_text(*, windows: bool, python: str, script: str, extra: list[str],
                  mac_protected: bool) -> str:
    """What --print-schedule prints: the cron line, or the Task Scheduler fields."""
    if windows:
        return WINDOWS_TEXT.format(
            python=python, interval=INTERVAL,
            arguments=" ".join(_quoted(part) for part in [script, *extra]))
    # cron ends a command at an unescaped %, and turns the rest into its input.
    command = _posix_line([python, script, *extra]).replace("%", "\\%")
    text = POSIX_TEXT.format(interval=INTERVAL, command=command,
                             paste=build_mod.fill(PASTE, ""))
    if mac_protected:
        text += "\n" + build_mod.fill(MAC, "") + "\n"
    return text


def _mac_protected(*paths: Path) -> bool:
    """Whether macOS keeps cron out of any folder or file this schedule uses.

    Every one of them and not only the project folder: cards kept in Downloads
    are refused to cron just as a project kept there is.
    """
    if sys.platform != "darwin":
        return False
    home = Path.home()
    return any(path == home / name or home / name in path.parents
               for path in paths
               for name in ("Documents", "Desktop", "Downloads"))


if __name__ == "__main__":
    raise SystemExit(main())
