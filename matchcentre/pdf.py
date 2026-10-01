"""Minimal PDF text-and-rectangle extractor, stdlib only.

This tool runs on hosts with no package index, so pdfminer / pypdf / pdfplumber
are all out. That is affordable because the input is not "PDF in general": a Match
Centre scorecard is one HTML template rendered by ``wkhtmltopdf`` — classic PDF 1.4
with a plain cross-reference table, no object streams, no encryption, and a single
Flate-compressed content stream.

Three things the cards need that a naive extractor gets wrong:

* Glyphs are emitted one per ``Tj``, each preceded by a ``Td`` that is *relative*
  to the previous text-line position. Treating those as absolute puts every glyph
  at x=0.
* The tick state of a checkbox is not in the text layer at all. An empty box is
  four hairline ``re f`` rectangles; a ticked box is one filled square. So the
  content stream has to be interpreted, not just string-scanned, and filled
  rectangles must be mapped through the same graphics-state stack as the text.
* Text is not always hex. Some producers write literal ``( )`` strings, whose
  escapes must be *decoded* rather than copied through (see :func:`_read_literal`),
  and their documents are multi-page (see :func:`extract`).

What this module refuses is as important as what it reads. Its predecessor
returned ``""`` for every glyph of a font with no ``/ToUnicode``, and located
objects by scanning for ``N G obj``, which cannot see inside a compressed object
stream. Both produced an *empty page* rather than an error, and an empty page is
indistinguishable downstream from a card that simply had nothing on it. So:

* a font with no ``/ToUnicode`` falls back to ``/Encoding``, its ``/Differences``,
  the named base encodings and finally Latin-1, and a glyph that still cannot be
  decoded is left out rather than guessed at;
* a page that draws text and yields no readable glyph raises :class:`PdfError`;
* object streams, cross-reference streams, encryption and undecodable form
  XObjects raise :class:`PdfError` naming what to do about it.

Coordinates coming out of :func:`extract` are page points with a **top-left origin
and y growing downward**, after ``/Rotate`` has been applied, so reading order is
``sorted(key=(y, x))``.
"""

from __future__ import annotations

import re
import unicodedata
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from .scope import in_words

__all__ = ["PdfError", "Word", "Rect", "Page", "extract", "page_count",
           "group_lines", "parse_cmap", "Matrix", "IDENTITY", "mat_mul",
           "mat_apply"]

# --- geometry -----------------------------------------------------------

# A PDF matrix [a b 0 / c d 0 / e f 1], flattened.
Matrix = tuple[float, float, float, float, float, float]
IDENTITY: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def mat_mul(m: Matrix, n: Matrix) -> Matrix:
    """``m × n`` — m applied first, then n."""
    a1, b1, c1, d1, e1, f1 = m
    a2, b2, c2, d2, e2, f2 = n
    return (
        a1 * a2 + b1 * c2,
        a1 * b2 + b1 * d2,
        c1 * a2 + d1 * c2,
        c1 * b2 + d1 * d2,
        e1 * a2 + f1 * c2 + e2,
        e1 * b2 + f1 * d2 + f2,
    )


def mat_apply(m: Matrix, x: float, y: float) -> tuple[float, float]:
    a, b, c, d, e, f = m
    return (x * a + y * c + e, x * b + y * d + f)


class PdfError(Exception):
    """The file is not the kind of PDF this extractor can read.

    Says what is wrong and names neither the file nor the page, which every reader
    that prints one prints beside it already, the page numbered from 1. For a
    feature that is refused rather than absent it says what a human can do to get a
    readable file instead.
    """


# --- public value types -------------------------------------------------


@dataclass(frozen=True, slots=True)
class Word:
    """A run of glyphs with no intervening space, in page coordinates."""

    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    size: float          # rendered cap size in points, not the raw /Tf operand
    bold: bool
    baseline: float = 0.0

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2.0

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2.0

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    def __str__(self) -> str:  # pragma: no cover - debugging aid
        return f"{self.text!r}@({self.x0:.1f},{self.y0:.1f})"


@dataclass(frozen=True, slots=True)
class Rect:
    """An axis-aligned rectangle painted by the content stream."""

    x0: float
    y0: float
    x1: float
    y1: float
    filled: bool

    @property
    def w(self) -> float:
        return self.x1 - self.x0

    @property
    def h(self) -> float:
        return self.y1 - self.y0

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2.0

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2.0

    def contains(self, x: float, y: float, pad: float = 0.0) -> bool:
        return (self.x0 - pad <= x <= self.x1 + pad
                and self.y0 - pad <= y <= self.y1 + pad)

    def overlaps(self, other: Rect) -> float:
        """Area of intersection with ``other``."""
        dx = min(self.x1, other.x1) - max(self.x0, other.x0)
        dy = min(self.y1, other.y1) - max(self.y0, other.y0)
        return dx * dy if dx > 0 and dy > 0 else 0.0


@dataclass(frozen=True, slots=True)
class Page:
    width: float
    height: float
    words: tuple[Word, ...]
    rects: tuple[Rect, ...]
    source: str = ""
    # Where this page sat in the document it came from. Carried on the Page so a
    # caller holding one can bound a loop over the rest without reopening the
    # file, and so a warning about a bad page can name which page it was.
    index: int = 0              # 0-based, in page-tree order
    page_count: int = 1
    # The box each image is painted into, an image XObject drawn by `Do` or an
    # inline image, as a filled rect, since an image paints the whole of it. Where
    # a picture is and nothing of what it shows: it gives no word and no rule.
    images: tuple[Rect, ...] = ()

    def lines(self, tol: float = 2.0) -> list[list[Word]]:
        """Words grouped into lines by baseline, each line sorted left to right."""
        return group_lines(self.words, tol=tol)

    def text(self, tol: float = 2.0) -> str:
        return "\n".join(" ".join(w.text for w in line) for line in self.lines(tol))

    def words_in_band(self, top: float, bottom: float,
                      x0: float = float("-inf"),
                      x1: float = float("inf")) -> list[Word]:
        """Words whose vertical centre falls in ``[top, bottom]``, reading order."""
        hits = [w for w in self.words
                if top <= w.cy <= bottom and x0 <= w.cx <= x1]
        hits.sort(key=lambda w: (round(w.baseline, 1), w.x0))
        return hits

    def find(self, text: str) -> list[Word]:
        return [w for w in self.words if w.text == text]

    def square_fills(self, min_side: float = 2.5, max_side: float = 20.0,
                     tol: float = 0.35) -> list[Rect]:
        """Filled rectangles that are plausibly ticked checkboxes.

        The template's table rules are also ``re f`` fills, but they are hairlines;
        a tick is a square of a few points. Squareness plus a size window separates
        the two without hardcoding the template's scale factor. ``tol`` is how far
        from square a candidate may be, as a fraction of its longer side.
        """
        out = []
        for r in self.rects:
            if not r.filled:
                continue
            w, h = r.w, r.h
            if not (min_side <= w <= max_side and min_side <= h <= max_side):
                continue
            if abs(w - h) > tol * max(w, h):
                continue
            out.append(r)
        return out


# --- object layer -------------------------------------------------------

_OBJ_RE = re.compile(rb"(?<![0-9])(\d+)\s+(\d+)\s+obj\b")
_STREAM_RE = re.compile(rb"stream(?:\r\n|\r|\n)")
_REF_RE = re.compile(rb"^\s*(\d+)\s+0\s+R\s*$")


@dataclass(slots=True)
class _Obj:
    body: bytes                 # everything between "obj" and "stream"/"endobj"
    stream: bytes | None = None  # raw, still encoded


class _Doc:
    """Just enough of a PDF document to reach the page's content and fonts.

    Objects are located by scanning for ``N G obj`` rather than by following the
    cross-reference table: the producers seen here write a well-formed table, but
    scanning also survives the byte-offset drift that any later re-save would
    introduce. Its one blind spot is an object living inside a compressed object
    stream, which no amount of scanning can find — :func:`_open` refuses such a
    file up front rather than letting it come back as a blank page.
    """

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.objs: dict[int, _Obj] = {}
        self._scan()

    def _scan(self) -> None:
        marks = [(m.start(), int(m.group(1)), m.end()) for m in _OBJ_RE.finditer(self.data)]
        for i, (_, num, body_start) in enumerate(marks):
            limit = marks[i + 1][0] if i + 1 < len(marks) else len(self.data)
            chunk = self.data[body_start:limit]
            sm = _STREAM_RE.search(chunk)
            if sm is None:
                end = chunk.find(b"endobj")
                self.objs[num] = _Obj(body=chunk if end < 0 else chunk[:end])
                continue
            body = chunk[: sm.start()]
            raw = chunk[sm.end():]
            end = raw.rfind(b"endstream")
            self.objs[num] = _Obj(body=body, stream=raw if end < 0 else raw[:end])

    # -- dictionary helpers (regex-level; the template's dicts are flat) --
    def body(self, num: int) -> bytes:
        obj = self.objs.get(num)
        if obj is None:
            raise PdfError(f"missing object {num}")
        return obj.body

    def resolve(self, value: bytes) -> bytes:
        """Follow ``N 0 R`` indirection, once."""
        m = _REF_RE.match(value)
        return self.body(int(m.group(1))) if m else value

    def entry(self, body: bytes, key: str) -> bytes | None:
        """Raw text of ``/key`` in a dictionary body, indirection unresolved."""
        m = re.search(rb"/" + key.encode() + rb"(?![A-Za-z0-9])", body)
        if m is None:
            return None
        rest = body[m.end():]
        rest = rest.lstrip(b" \r\n\t")
        if rest[:1] == b"<" and rest[1:2] == b"<":
            return _balanced(rest, b"<<", b">>")
        if rest[:1] == b"[":
            return _balanced(rest, b"[", b"]")
        if rest[:1] == b"/":
            m2 = re.match(rb"/[^\s/\[\]<>()]*", rest)
            return m2.group(0) if m2 else None
        m2 = re.match(rb"\d+\s+\d+\s+R", rest)
        if m2:
            return m2.group(0)
        m2 = re.match(rb"[^/\[\]<>\r\n]*", rest)
        return m2.group(0).strip() if m2 else None

    def get(self, body: bytes, key: str) -> bytes | None:
        raw = self.entry(body, key)
        return None if raw is None else self.resolve(raw)

    def stream(self, num: int) -> bytes:
        """Decoded stream bytes for object ``num``."""
        obj = self.objs.get(num)
        if obj is None or obj.stream is None:
            raise PdfError(f"object {num} has no stream")
        raw = obj.stream
        length = self.get(obj.body, "Length")
        if length is not None and length.strip().isdigit():
            n = int(length.strip())
            if n <= len(raw):
                raw = raw[:n]
        filt = self.entry(obj.body, "Filter") or b""
        if b"FlateDecode" in filt:
            try:
                return zlib.decompress(raw)
            except zlib.error:
                # Truncated or over-long /Length; salvage what inflates.
                d = zlib.decompressobj()
                return d.decompress(raw)
        if b"Filter" in obj.body and b"FlateDecode" not in filt:
            raise PdfError(f"unsupported stream filter {filt!r} on object {num}")
        return raw

    def refs(self, raw: bytes) -> list[int]:
        return [int(n) for n in re.findall(rb"(\d+)\s+0\s+R", raw)]


def _balanced(buf: bytes, open_tok: bytes, close_tok: bytes) -> bytes:
    """Slice ``buf`` from its leading ``open_tok`` to the matching ``close_tok``."""
    depth = 0
    i = 0
    step = len(open_tok)
    while i < len(buf):
        if buf[i:i + step] == open_tok:
            depth += 1
            i += step
        elif buf[i:i + len(close_tok)] == close_tok:
            depth -= 1
            i += len(close_tok)
            if depth == 0:
                return buf[:i]
        else:
            i += 1
    return buf


# --- glyph names and simple-font encodings ------------------------------
#
# A simple font (Type1 or TrueType) says what its byte codes mean either in a
# /ToUnicode CMap, or in an /Encoding — a named base table, optionally overridden
# by a /Differences array of glyph *names*. Only /ToUnicode was honoured before,
# and a font without one lost all of its text silently. The tables below are what
# it takes to read the other two forms, kept to the printable range plus the
# punctuation and accents a scorecard actually prints.

# Adobe glyph names for codes 32..126, in code order. This single tuple gives both
# directions: the ASCII part of every base encoding, and the name -> character
# map that /Differences needs.
_ASCII_GLYPH_NAMES: tuple[str, ...] = tuple(
    ("space exclam quotedbl numbersign dollar percent ampersand quotesingle "
     "parenleft parenright asterisk plus comma hyphen period slash "
     "zero one two three four five six seven eight nine "
     "colon semicolon less equal greater question at").split()
    + [chr(c) for c in range(0x41, 0x5B)]
    + "bracketleft backslash bracketright asciicircum underscore grave".split()
    + [chr(c) for c in range(0x61, 0x7B)]
    + "braceleft bar braceright asciitilde".split()
)

# Names outside the ASCII range that a Latin-script document actually uses.
# Accented letters are not listed: they follow the ``<base><accent>`` pattern and
# are composed below, which is both shorter and less error-prone than 200 rows.
_NAMED_GLYPHS: dict[str, str] = {
    "quoteleft": "‘", "quoteright": "’",
    "quotedblleft": "“", "quotedblright": "”",
    "quotesinglbase": "‚", "quotedblbase": "„",
    "guilsinglleft": "‹", "guilsinglright": "›",
    "guillemotleft": "«", "guillemotright": "»",
    "endash": "–", "emdash": "—", "minus": "−",
    "bullet": "•", "periodcentered": "·", "ellipsis": "…",
    "dagger": "†", "daggerdbl": "‡",
    "section": "§", "paragraph": "¶",
    "fraction": "⁄", "perthousand": "‰", "florin": "ƒ",
    "currency": "¤", "cent": "¢", "sterling": "£",
    "yen": "¥", "Euro": "€", "euro": "€",
    "copyright": "©", "registered": "®", "trademark": "™",
    "degree": "°", "plusminus": "±", "multiply": "×",
    "divide": "÷", "logicalnot": "¬", "brokenbar": "¦",
    "onehalf": "½", "onequarter": "¼", "threequarters": "¾",
    "onesuperior": "¹", "twosuperior": "²", "threesuperior": "³",
    "exclamdown": "¡", "questiondown": "¿",
    "ordfeminine": "ª", "ordmasculine": "º", "mu": "µ",
    "fi": "ﬁ", "fl": "ﬂ",
    "AE": "Æ", "ae": "æ", "OE": "Œ", "oe": "œ",
    "Oslash": "Ø", "oslash": "ø", "germandbls": "ß",
    "Lslash": "Ł", "lslash": "ł", "dotlessi": "ı",
    "Thorn": "Þ", "thorn": "þ", "Eth": "Ð", "eth": "ð",
    "Scaron": "Š", "scaron": "š", "Zcaron": "Ž",
    "zcaron": "ž", "Ydieresis": "Ÿ",
    "nbspace": " ", "softhyphen": "­",
    # The standalone accents, which are glyphs in their own right.
    "acute": "´", "dieresis": "¨", "macron": "¯",
    "cedilla": "¸", "circumflex": "ˆ", "caron": "ˇ",
    "breve": "˘", "dotaccent": "˙", "ring": "˚",
    "ogonek": "˛", "tilde": "˜", "hungarumlaut": "˝",
}

# Suffixes that turn a base letter into an accented one: ``eacute`` -> e + U+0301,
# composed to U+00E9 by NFC. Covers every accented Latin glyph name without
# listing any of them.
_ACCENT_MARKS: dict[str, str] = {
    "grave": "̀", "acute": "́", "circumflex": "̂",
    "tilde": "̃", "macron": "̄", "breve": "̆",
    "dotaccent": "̇", "dieresis": "̈", "ring": "̊",
    "hungarumlaut": "̋", "caron": "̌", "cedilla": "̧",
    "ogonek": "̨",
}

_UNI_NAME_RE = re.compile(r"^uni([0-9A-Fa-f]{4,6})$")
_U_NAME_RE = re.compile(r"^u([0-9A-Fa-f]{4,6})$")

_ASCII_BY_NAME: dict[str, str] = {
    name: chr(32 + i) for i, name in enumerate(_ASCII_GLYPH_NAMES)
}


def _glyph_char(name: str) -> str:
    """The character a glyph name stands for, or ``""`` if it stands for nothing.

    ``""`` is a real answer, not a failure to try: a subset font that names its
    glyphs ``/g90`` or ``/cid17`` is telling us its glyph *indices*, which say
    nothing about which letter was drawn. Returning a guess there is how a card
    acquires text nobody printed.
    """
    name = name.split(".", 1)[0]        # "/a.sc" is a variant of "/a"
    if not name:
        return ""
    if name in _ASCII_BY_NAME:
        return _ASCII_BY_NAME[name]
    if name in _NAMED_GLYPHS:
        return _NAMED_GLYPHS[name]
    if len(name) > 1 and name[1:] in _ACCENT_MARKS and name[0].isalpha():
        return unicodedata.normalize("NFC", name[0] + _ACCENT_MARKS[name[1:]])
    m = _UNI_NAME_RE.match(name) or _U_NAME_RE.match(name)
    if m:
        code = int(m.group(1), 16)
        if 0 < code <= 0x10FFFF:
            return chr(code)
    return ""


def _encoding_from_codec(codec: str) -> dict[int, str]:
    """A base encoding table built by decoding one byte at a time.

    WinAnsiEncoding and MacRomanEncoding *are* code pages Python already ships
    (``cp1252`` and ``mac_roman``), so asking the codec is both shorter and more
    trustworthy than transcribing 224 rows by hand. Codes the code page leaves
    undefined are simply absent, which is what a reader should do with them.
    """
    table: dict[int, str] = {}
    for code in range(32, 256):
        try:
            ch = bytes([code]).decode(codec)
        except UnicodeDecodeError:
            continue
        if ch and unicodedata.category(ch) != "Cc":
            table[code] = ch
    return table


# The high half of StandardEncoding (ISO 32000-1 Annex D.2), by glyph name so the
# characters come from one table only. Its low half is ASCII except for two codes,
# applied below.
_STANDARD_HIGH_NAMES: dict[int, str] = {
    0xA1: "exclamdown", 0xA2: "cent", 0xA3: "sterling", 0xA4: "fraction",
    0xA5: "yen", 0xA6: "florin", 0xA7: "section", 0xA8: "currency",
    0xA9: "quotesingle", 0xAA: "quotedblleft", 0xAB: "guillemotleft",
    0xAC: "guilsinglleft", 0xAD: "guilsinglright", 0xAE: "fi", 0xAF: "fl",
    0xB1: "endash", 0xB2: "dagger", 0xB3: "daggerdbl", 0xB4: "periodcentered",
    0xB6: "paragraph", 0xB7: "bullet", 0xB8: "quotesinglbase",
    0xB9: "quotedblbase", 0xBA: "quotedblright", 0xBB: "guillemotright",
    0xBC: "ellipsis", 0xBD: "perthousand", 0xBF: "questiondown",
    0xC1: "grave", 0xC2: "acute", 0xC3: "circumflex", 0xC4: "tilde",
    0xC5: "macron", 0xC6: "breve", 0xC7: "dotaccent", 0xC8: "dieresis",
    0xCA: "ring", 0xCB: "cedilla", 0xCD: "hungarumlaut", 0xCE: "ogonek",
    0xCF: "caron", 0xD0: "emdash", 0xE1: "AE", 0xE3: "ordfeminine",
    0xE8: "Lslash", 0xE9: "Oslash", 0xEA: "OE", 0xEB: "ordmasculine",
    0xF1: "ae", 0xF5: "dotlessi", 0xF8: "lslash", 0xF9: "oslash",
    0xFA: "oe", 0xFB: "germandbls",
}

_WINANSI_ENCODING = _encoding_from_codec("cp1252")
_MACROMAN_ENCODING = _encoding_from_codec("mac_roman")
_LATIN1_ENCODING = _encoding_from_codec("latin-1")
_STANDARD_ENCODING: dict[int, str] = (
    {32 + i: chr(32 + i) for i in range(95)}
    # StandardEncoding puts typographic quotes where ASCII has the apostrophe
    # and the grave accent.
    | {0x27: "’", 0x60: "‘"}
    | {code: _glyph_char(name) for code, name in _STANDARD_HIGH_NAMES.items()}
)

_BASE_ENCODINGS: dict[str, dict[int, str]] = {
    "WinAnsiEncoding": _WINANSI_ENCODING,
    "MacRomanEncoding": _MACROMAN_ENCODING,
    "StandardEncoding": _STANDARD_ENCODING,
    # MacExpertEncoding is a symbol set; there is no honest Latin mapping for it,
    # so it resolves to no table and its codes go undecoded rather than wrong.
    "PDFDocEncoding": _WINANSI_ENCODING,
}


def _differences(raw: bytes) -> dict[int, str]:
    """Parse an ``/Encoding`` ``/Differences`` array into ``{code: glyph name}``.

    The array is a sequence of ``code /name /name ...`` runs: each number restarts
    the code counter, each name consumes one code. Names are kept, not resolved,
    so the caller can tell "explicitly remapped to something I cannot read" from
    "not mentioned" — the first must mask the base encoding, the second must not.
    """
    out: dict[int, str] = {}
    code = 0
    for tok in re.findall(rb"\d+|/[^\s/\[\]<>(){}%]*", raw):
        if tok[:1] == b"/":
            out[code] = tok[1:].decode("latin-1")
            code += 1
        else:
            code = int(tok)
    return out


# --- fonts --------------------------------------------------------------


@dataclass(slots=True)
class _Font:
    name: str
    bold: bool
    base: str = ""                      # /BaseFont, for diagnostics only
    to_unicode: dict[int, str] = field(default_factory=dict)
    encoding: dict[int, str] = field(default_factory=dict)
    # Codes whose /Differences entry named a glyph with no known character. They
    # must not fall through to the base encoding or to Latin-1: the font said
    # "this code is not what the base table says", and a guess would be fiction.
    masked: frozenset[int] = frozenset()
    widths: dict[int, float] = field(default_factory=dict)   # /1000 em
    default_width: float = 0.5
    ascent: float = 0.9
    descent: float = -0.24
    code_bytes: int = 1
    composite: bool = False

    def decode(self, code: int) -> str:
        """A code as text, or ``""`` when nothing in the font explains it.

        Order: the /ToUnicode CMap, then the /Encoding table (a /Differences entry
        over a named base), then Latin-1 for a single-byte code. The last step is a
        guess, but a defensible one — every base encoding agrees with Latin-1 over
        the printable ASCII range, which is all a scorecard needs. It is not
        offered for a composite font, whose codes are CIDs: those are glyph indices
        into an embedded font program, and reading one as a letter is invention.
        """
        ch = self.to_unicode.get(code)
        if ch is None:
            ch = self.encoding.get(code)
        if ch is None and code not in self.masked and not self.composite:
            ch = _LATIN1_ENCODING.get(code)
        if not ch:
            return ""
        # Some CMaps map the space glyph to U+0009, and some to one of the
        # fixed-width spaces; normalise every flavour of blank so that word
        # splitting has exactly one rule to apply.
        return " " if ch.isspace() else ch

    def width(self, code: int) -> float:
        return self.widths.get(code, self.default_width)

    @property
    def label(self) -> str:
        return f"/{self.name}" + (f" ({self.base})" if self.base else "")


_HEX_RE = re.compile(rb"<([0-9A-Fa-f\s]*)>")


def _hex_str(tok: bytes) -> bytes:
    h = re.sub(rb"[\s<>]", b"", tok)
    if len(h) % 2:
        h += b"0"
    return bytes.fromhex(h.decode("ascii"))


def _utf16(raw: bytes) -> str:
    try:
        return raw.decode("utf-16-be")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def parse_cmap(text: bytes) -> tuple[dict[int, str], int | None]:
    """Parse a ToUnicode CMap into ``{code: str}`` plus the code byte width.

    Handles ``bfchar`` and both ``bfrange`` forms — ``<lo> <hi> <dst>`` (dst
    incremented across the range) and ``<lo> <hi> [<d> <d> ...]``.

    The width is ``None`` when the CMap declares no ``codespacerange``, which is
    the caller's cue to infer it from the font type instead of assuming two bytes:
    reading a single-byte font two bytes at a time turns its text into garbage.
    """
    mapping: dict[int, str] = {}
    code_bytes: int | None = None

    csr = re.search(rb"begincodespacerange(.*?)endcodespacerange", text, re.S)
    if csr:
        codes = _HEX_RE.findall(csr.group(1))
        if codes:
            code_bytes = max(1, len(re.sub(rb"\s", b"", codes[0])) // 2)

    for block in re.findall(rb"beginbfchar(.*?)endbfchar", text, re.S):
        toks = _HEX_RE.findall(block)
        for i in range(0, len(toks) - 1, 2):
            src = int.from_bytes(_hex_str(toks[i]), "big")
            mapping[src] = _utf16(_hex_str(toks[i + 1]))

    for block in re.findall(rb"beginbfrange(.*?)endbfrange", text, re.S):
        # Tokens are either <hex> or [ ... ]; walk them so the two forms of the
        # third operand can be told apart.
        for lo_raw, hi_raw, dst in _bfrange_rows(block):
            lo = int.from_bytes(_hex_str(lo_raw), "big")
            hi = int.from_bytes(_hex_str(hi_raw), "big")
            if isinstance(dst, list):
                for off, item in enumerate(dst):
                    if lo + off > hi:
                        break
                    mapping[lo + off] = _utf16(_hex_str(item))
            else:
                base = _hex_str(dst)
                # The last two bytes of the destination are what increments; a
                # one-byte destination has no high half to keep.
                if len(base) >= 2:
                    head, tail = base[:-2], base[-2:]
                else:
                    head, tail = b"", base
                start = int.from_bytes(tail, "big")
                for off in range(hi - lo + 1):
                    mapping[lo + off] = _utf16(
                        head + (start + off).to_bytes(2, "big"))
    return mapping, code_bytes


_BF_TOK_RE = re.compile(rb"<[0-9A-Fa-f\s]*>|\[[^\]]*\]")


def _bfrange_rows(block: bytes):
    toks = _BF_TOK_RE.findall(block)
    i = 0
    while i + 2 < len(toks) + 1 and i + 2 <= len(toks):
        lo, hi = toks[i], toks[i + 1]
        if not lo.startswith(b"<") or not hi.startswith(b"<"):
            i += 1
            continue
        if i + 2 >= len(toks):
            break
        third = toks[i + 2]
        if third.startswith(b"["):
            yield lo, hi, _HEX_RE.findall(third)
        else:
            yield lo, hi, third
        i += 3


def _parse_w_array(raw: bytes) -> dict[int, float]:
    """Parse a CIDFont ``/W`` array into ``{cid: width/1000}``.

    Both published forms: ``c [w w w]`` and ``c_first c_last w``.
    """
    widths: dict[int, float] = {}
    toks = re.findall(rb"\[|\]|-?[\d.]+", raw)
    # Drop the outer brackets so nesting is unambiguous.
    if toks and toks[0] == b"[":
        toks = toks[1:]
    if toks and toks[-1] == b"]":
        toks = toks[:-1]
    i = 0
    while i < len(toks):
        if toks[i] == b"[" or toks[i] == b"]":
            i += 1
            continue
        first = int(float(toks[i]))
        i += 1
        if i < len(toks) and toks[i] == b"[":
            i += 1
            cid = first
            while i < len(toks) and toks[i] != b"]":
                widths[cid] = float(toks[i]) / 1000.0
                cid += 1
                i += 1
            i += 1  # skip ]
        elif i + 1 < len(toks):
            last = int(float(toks[i]))
            w = float(toks[i + 1]) / 1000.0
            i += 2
            if last - first < 65536:
                for cid in range(first, last + 1):
                    widths[cid] = w
        else:
            break
    return widths


def _parse_widths_array(raw: bytes, first_char: int) -> dict[int, float]:
    """Parse a simple font's ``/Widths`` array into ``{code: width/1000}``.

    The simple-font form of ISO 32000-1 Table 111: one number per code, running
    from ``/FirstChar`` to ``/LastChar`` with no run-length or nesting syntax. A
    Type0 font's ``/W`` is a different shape and is handled by
    :func:`_parse_w_array`.
    """
    widths: dict[int, float] = {}
    for offset, tok in enumerate(re.findall(rb"-?[\d.]+", raw)):
        try:
            widths[first_char + offset] = float(tok) / 1000.0
        except ValueError:
            continue
    return widths


def _load_encoding(doc: _Doc, body: bytes) -> tuple[dict[int, str], frozenset[int]]:
    """Resolve a simple font's ``/Encoding`` into ``{code: char}`` plus masked codes.

    ``/Encoding`` is either a name (a base table) or a dictionary that may carry
    ``/BaseEncoding`` and ``/Differences``. A code named by ``/Differences`` whose
    glyph name resolves to nothing is *masked* rather than left to the base table:
    the font has said that code is not what the base says it is.
    """
    raw = doc.entry(body, "Encoding")
    if raw is None:
        return {}, frozenset()
    # Either form may arrive indirectly, so resolve first and then decide which it
    # is; a bare name resolves to itself.
    enc_body = doc.resolve(raw).strip()
    if enc_body[:1] == b"/":
        name = enc_body.split()[0][1:].decode("latin-1")
        return dict(_BASE_ENCODINGS.get(name, {})), frozenset()

    base_name = doc.entry(enc_body, "BaseEncoding") or b""
    # With no /BaseEncoding the spec says to use the font program's built-in
    # encoding, which is not readable from here. StandardEncoding is the
    # documented substitute and agrees with the rest over printable ASCII.
    table = dict(_BASE_ENCODINGS.get(base_name[1:].decode("latin-1"),
                                     _STANDARD_ENCODING))
    diffs_raw = doc.entry(enc_body, "Differences")
    masked: set[int] = set()
    if diffs_raw:
        for code, glyph in _differences(diffs_raw).items():
            ch = _glyph_char(glyph)
            if ch:
                table[code] = ch
            else:
                table.pop(code, None)
                masked.add(code)
    return table, frozenset(masked)


def _load_fonts(doc: _Doc, resources: bytes) -> dict[str, _Font]:
    fonts: dict[str, _Font] = {}
    fdict = doc.get(resources, "Font")
    if not fdict:
        return fonts
    for name, num in re.findall(rb"/([A-Za-z0-9]+)\s+(\d+)\s+0\s+R", fdict):
        body = doc.body(int(num))
        base = doc.entry(body, "BaseFont") or b""
        subtype = doc.entry(body, "Subtype") or b""
        kids = doc.entry(body, "DescendantFonts")
        composite = subtype == b"/Type0" or bool(kids)
        font = _Font(name=name.decode(),
                     bold=b"Bold" in base or b"bold" in base,
                     base=base.lstrip(b"/").decode("latin-1"),
                     composite=composite)

        declared_bytes: int | None = None
        tu = doc.entry(body, "ToUnicode")
        if tu:
            ref = _REF_RE.match(tu)
            if ref:
                try:
                    font.to_unicode, declared_bytes = parse_cmap(
                        doc.stream(int(ref.group(1))))
                except PdfError:
                    pass

        # A composite font's codes are two bytes wide under every /Encoding this
        # reader meets (Identity-H and the CJK CMaps alike); a simple font's are
        # one. Defaulting to two, as the predecessor did, read every simple font
        # in byte pairs and turned its text into garbage, so the only way to get
        # two is to be composite or to say so in the CMap's codespace range.
        if declared_bytes is not None:
            font.code_bytes = declared_bytes
        else:
            font.code_bytes = 2 if composite else 1

        if not composite:
            font.encoding, font.masked = _load_encoding(doc, body)

        # Widths and vertical metrics live on the descendant CIDFont for Type0.
        desc_body = body
        if kids:
            nums = doc.refs(doc.resolve(kids) if _REF_RE.match(kids) else kids)
            if nums:
                desc_body = doc.body(nums[0])
        warr = doc.entry(desc_body, "W")
        if warr:
            font.widths = _parse_w_array(doc.resolve(warr))
        else:
            # Simple font (Type1/TrueType): /FirstChar + /Widths. Without this the
            # every-glyph-is-half-an-em fallback below is used instead, and any
            # string drawn as a single Tj drifts: a producer that lays out whole
            # words that way relies on the font's own advances, and half-em
            # guesses detach the tail of a word and stack it back over the head.
            # A card whose producer emits one glyph per Tj with its own Td is
            # unaffected, because that overrides the advance on every character.
            widths = doc.get(desc_body, "Widths")
            first = doc.get(desc_body, "FirstChar")
            if widths and first and first.strip().lstrip(b"+-").isdigit():
                font.widths = _parse_widths_array(widths, int(first))
        dw = doc.get(desc_body, "DW")
        if dw and dw.strip().replace(b".", b"", 1).isdigit():
            font.default_width = float(dw) / 1000.0
        fd = doc.get(desc_body, "FontDescriptor")
        if fd:
            asc = doc.get(fd, "Ascent")
            dsc = doc.get(fd, "Descent")
            if asc:
                font.ascent = float(asc) / 1000.0
            if dsc:
                font.descent = float(dsc) / 1000.0
            missing = doc.get(fd, "MissingWidth")
            if missing and missing.strip().replace(b".", b"", 1).isdigit():
                font.default_width = float(missing) / 1000.0
            flags = doc.get(fd, "Flags")
            if flags and flags.strip().isdigit() and int(flags) & (1 << 18):
                font.bold = True
        fonts[font.name] = font
    return fonts


# --- content stream interpreter -----------------------------------------

_TOKEN_RE = re.compile(
    rb"""
      (?P<comment>%[^\r\n]*)
    | (?P<dict><<|>>)
    | (?P<array>\[|\])
    | (?P<hexstr><[0-9A-Fa-f\s]*>)
    | (?P<name>/[^\s/\[\]<>(){}%]*)
    | (?P<num>[-+]?(?:\d+\.\d*|\.\d+|\d+))
    | (?P<op>[A-Za-z'"*][A-Za-z0-9'"*]*)
    | (?P<lparen>\()
    """,
    re.X,
)


# The named escapes of ISO 32000-1 §7.3.4.2, keyed by the byte after the
# backslash. ``\(``, ``\)`` and ``\\`` stand for themselves and so are absent.
_LITERAL_ESCAPES = {
    0x6E: 0x0A,   # n -> LF
    0x72: 0x0D,   # r -> CR
    0x74: 0x09,   # t -> TAB
    0x62: 0x08,   # b -> BS
    0x66: 0x0C,   # f -> FF
}


def _read_literal(buf: bytes, i: int) -> tuple[bytes, int]:
    r"""Read a ``(...)`` literal string starting just after the open paren.

    The escapes have to be *decoded*, not copied through. A producer that writes
    every string as hex never exposes this, but a literal-string producer does,
    and the failure is silent: a subset font numbers its glyphs from 1, so codes
    10 and 13 are ordinary letters, and copying ``\n`` through as the letter ``n``
    feeds the font a code it has no glyph for. The letter then vanishes, and a
    word missing a letter still looks like a word — worse than a wrong one,
    because nothing downstream can tell.

    One spec case is left undone on purpose: an *unescaped* end-of-line inside a
    string, which §7.3.4.2 folds to a single LF. No literal string in any of the
    PDFs this was developed against contains one, and folding CR LF into one byte
    would re-align every glyph code after it in a 2-byte-encoded string — an
    unverifiable change with a corrupting failure mode. Do it when a producer
    turns up that needs it.
    """
    out = bytearray()
    depth = 1
    n = len(buf)
    while i < n:
        c = buf[i]
        if c == 0x5C:  # backslash
            i += 1
            if i >= n:
                break                      # unterminated string; give up cleanly
            nxt = buf[i]
            if 0x30 <= nxt <= 0x37:        # \ddd: one, two or three octal digits
                code = 0
                for _ in range(3):
                    if i >= n or not 0x30 <= buf[i] <= 0x37:
                        break
                    code = code * 8 + (buf[i] - 0x30)
                    i += 1
                out.append(code & 0xFF)    # high-order overflow is ignored
                continue
            if nxt == 0x0D:                # line continuation: emits nothing.
                i += 1
                if i < n and buf[i] == 0x0A:
                    i += 1                 # CR LF is one line ending, not two
                continue
            if nxt == 0x0A:
                i += 1
                continue
            out.append(_LITERAL_ESCAPES.get(nxt, nxt))
            i += 1
            continue
        if c == 0x28:
            depth += 1
        elif c == 0x29:
            depth -= 1
            if depth == 0:
                return bytes(out), i + 1
        out.append(c)
        i += 1
    return bytes(out), i


# An `EI` that could end an inline image's bytes: after white space, and before
# white space, a delimiter or the end of the stream. The bytes are anything, so
# one among them looks the same; `_inline_end` says which ends the image.
_INLINE_END_RE = re.compile(rb"(?<=\s)EI(?=[\s%/\[\]<>(){}]|\Z)")
# The `EI` where the length an image's dictionary gives says its bytes end.
_INLINE_AT_RE = re.compile(rb"\s*EI(?=[\s%/\[\]<>(){}]|\Z)")
# A `BI` the same way, where the next inline image would begin.
_INLINE_BEGIN_RE = re.compile(rb"(?<=\s)BI(?=[\s%/\[\]<>(){}]|\Z)")
# The byte after an operator: white space or a delimiter.
_OPERATOR_END_RE = re.compile(rb"[\s%/\[\]<>(){}]")
_SPACE_RE = re.compile(rb"\s*")

# The 73 operators of a content stream, ISO 32000-1 Annex A.
_OPERATORS = frozenset(b"""
    b B b* B* BDC BI BMC BT BX c cm CS cs d d0 d1 Do DP EI EMC ET EX f F f* G g gs
    h i ID j J K k l m M MP n q Q re RG rg ri s S SC sc SCN scn sh T* Tc Td TD Tf
    Tj TJ TL Tm Tr Ts Tw Tz v w W W* y ' "
    """.split())
# Those that can come after an image: all but the two that only an image's own
# dictionary and bytes come before.
_AFTER_AN_IMAGE = _OPERATORS - {b"ID", b"EI"}
# Words the operator pattern matches that are objects, and so operands.
_KEYWORDS = frozenset((b"true", b"false", b"null"))
# How far past a candidate `EI` its content is read, so that an image full of
# candidates costs a bounded look at each.
_INLINE_LOOKAHEAD = 1024
# Where no token matches, what one the look's end cuts off begins as: a hex string
# or `<<` not yet closed, or the first `>` of `>>`.
_CUT_RE = re.compile(rb"(?:<[0-9A-Fa-f\s]*|>)\Z")


def _inline_length(dictionary: bytes) -> int | None:
    """The ``/L`` or ``/Length`` an inline image's dictionary gives, if a count."""
    tokens = list(_tokenize(dictionary))
    for (kind, key), (vkind, value) in zip(tokens, tokens[1:]):
        if (kind == "name" and key in ("L", "Length") and vkind == "num"
                and value >= 0 and value == int(value)):
            return int(value)
    return None


def _reads_as_content(buf: bytes, at: int) -> bool | None:
    """Whether what follows ``at`` reads as content that can follow an image.

    That is operands, then an operator other than ``ID`` and ``EI``, which follow
    only an image's own dictionary and bytes, so an ``EI`` among an image's bytes
    followed by its real one is not taken for the end; or the stream's end, with
    no operand left waiting. A byte no token begins, a word no operator is, and
    operands the stream ends among, a string left open included, say no. The look
    reads the lookahead's bytes at most, and None says they ran out before the
    stream did, in the middle of a token or not, with nothing yet decided.
    """
    window = buf[at:at + _INLINE_LOOKAHEAD]
    whole = at + _INLINE_LOOKAHEAD >= len(buf)
    operands = False
    j = 0
    while True:
        j = _SPACE_RE.match(window, j).end()
        if j >= len(window):
            return not operands if whole else None
        m = _TOKEN_RE.match(window, j)
        if m is None:
            return None if not whole and _CUT_RE.match(window, j) else False
        j = m.end()
        if m.lastgroup == "comment":
            continue
        if m.lastgroup == "lparen":
            _, j = _read_literal(window, j)
        elif m.lastgroup == "op" and m.group() not in _KEYWORDS:
            if j == len(window) and not whole:
                return None                # the word may run on past the look
            return (m.group() in _AFTER_AN_IMAGE
                    and (j == len(window) or _OPERATOR_END_RE.match(window, j)
                         is not None))
        operands = True


def _inline_end(buf: bytes, i: int, length: int | None) -> int | None:
    """Where the inline image whose bytes follow ``ID`` at ``i`` ends, past its EI.

    With a ``length``, counted from the byte after ``ID``'s white space, the image
    ends there if EI is next. Otherwise it ends at the first candidate EI whose
    following bytes read as content; failing that, at the first whose look ran out
    undecided; and failing that, at the first. No candidate past a ``BI`` after the
    one before it is tried, since an EI there ends another image. None where there
    is no EI at all.
    """
    if length is not None:
        start = i + 1 if buf[i:i + 1].isspace() else i
        at = _INLINE_AT_RE.match(buf, start + length)
        if at:
            return at.end()
    first = undecided = last = None
    for end in _INLINE_END_RE.finditer(buf, i):
        if last is not None and _INLINE_BEGIN_RE.search(buf, last, end.start()):
            break
        content = _reads_as_content(buf, end.end())
        if content:
            return end.end()
        if content is None and undecided is None:
            undecided = end.end()
        if first is None:
            first = end.end()
        last = end.end()
    return first if undecided is None else undecided


def _tokenize(buf: bytes):
    i = 0
    begun = None
    while i < len(buf):
        m = _TOKEN_RE.search(buf, i)
        if m is None:
            return
        i = m.end()
        if m.lastgroup == "comment":
            continue
        if m.lastgroup == "op" and m.group() == b"BI":
            begun = i
        if m.lastgroup == "op" and m.group() == b"ID":
            # An inline image's bytes, passed over whole: a `(` among them read as
            # content opens a string that swallows the rest of the stream. With no
            # `EI` the stream ends inside the image, and nothing is left to read.
            yield ("op", "ID")
            length = None if begun is None else _inline_length(buf[begun:m.start()])
            begun = None
            end = _inline_end(buf, i, length)
            if end is None:
                return
            i = end
            yield ("op", "EI")
            continue
        if m.lastgroup == "lparen":
            s, i = _read_literal(buf, i)
            yield ("str", s)
            continue
        if m.lastgroup == "num":
            yield ("num", float(m.group()))
        elif m.lastgroup == "hexstr":
            yield ("str", _hex_str(m.group()[1:-1]))
        elif m.lastgroup == "name":
            yield ("name", m.group()[1:].decode("latin-1"))
        elif m.lastgroup in ("array", "dict"):
            yield ("delim", m.group().decode("ascii"))
        else:
            yield ("op", m.group().decode("ascii"))


@dataclass(slots=True)
class _Glyph:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    baseline: float
    size: float
    bold: bool
    run: int          # index of the BT/ET text object that emitted it


@dataclass(slots=True)
class _GState:
    ctm: Matrix = IDENTITY


@dataclass(slots=True)
class _Trace:
    """What the interpreter saw, for deciding whether an empty page is an error.

    A page that painted no text is normal. A page that *asked* for text and got
    none back is a font this reader could not decode, and it must not be handed
    on as a blank page — that is the failure the /Encoding fallbacks exist to fix,
    and this is how the last of it gets reported instead of hidden.
    """

    shown: int = 0                  # show-text operators carrying at least one byte
    undecodable: set[str] = field(default_factory=set)   # font labels that failed


_MAX_FORM_DEPTH = 8


class _Interpreter:
    """Runs content streams, collecting glyphs and painted rectangles.

    A class rather than a plain function because a page's marks are not always in
    one stream: a ``Do`` naming a ``/Form`` XObject paints a nested stream with its
    own resources and its own ``/Matrix``, and those marks belong to this page.
    The accumulators, the font cache and the text-object counter are shared across
    that recursion — the counter especially, since it is what keeps a form's words
    from being joined to a neighbouring word on the page.

    Coordinates come out in the page's display space: top-left origin, y growing
    downward, ``/Rotate`` already applied. That is all folded into the base matrix
    by :func:`_base_matrix`, so nothing here needs to know the page height.
    """

    def __init__(self, doc: _Doc) -> None:
        self.doc = doc
        self.glyphs: list[_Glyph] = []
        self.rects: list[Rect] = []
        self.images: list[Rect] = []
        self.trace = _Trace()
        self.run = -1
        self._fonts: dict[bytes, dict[str, _Font]] = {}
        self._active: set[int] = set()      # XObjects on the current stack

    def fonts_for(self, resources: bytes) -> dict[str, _Font]:
        cached = self._fonts.get(resources)
        if cached is None:
            cached = _load_fonts(self.doc, resources)
            self._fonts[resources] = cached
        return cached

    def run_stream(self, content: bytes, resources: bytes, base: Matrix,
                   depth: int = 0) -> None:
        fonts = self.fonts_for(resources)
        gs = _GState(ctm=base)
        stack: list[_GState] = []
        pending: list[tuple[float, float, float, float]] = []  # `re` in user space
        pending_ctm: list[Matrix] = []

        tm: Matrix = IDENTITY
        tlm: Matrix = IDENTITY
        font: _Font | None = None
        size = 0.0
        char_sp = 0.0
        word_sp = 0.0
        hscale = 1.0
        leading = 0.0
        rise = 0.0
        operands: list = []

        def flush_path(filled: bool) -> None:
            for (rx, ry, rw, rh), ctm in zip(pending, pending_ctm):
                xs, ys = [], []
                for px, py in ((rx, ry), (rx + rw, ry),
                               (rx, ry + rh), (rx + rw, ry + rh)):
                    tx, ty = mat_apply(ctm, px, py)
                    xs.append(tx)
                    ys.append(ty)
                self.rects.append(Rect(min(xs), min(ys), max(xs), max(ys), filled))
            pending.clear()
            pending_ctm.clear()

        def show(raw: bytes) -> None:
            nonlocal tm
            if raw:
                self.trace.shown += 1
            if font is None:
                return
            step = max(1, font.code_bytes)
            for i in range(0, len(raw) - step + 1, step):
                code = int.from_bytes(raw[i:i + step], "big")
                ch = font.decode(code)
                w = font.width(code)
                if not ch:
                    self.trace.undecodable.add(font.label)
                trm = mat_mul((size * hscale, 0.0, 0.0, size, 0.0, rise),
                              mat_mul(tm, gs.ctm))
                if ch and not ch.isspace():
                    xs, ys = [], []
                    for px, py in ((0.0, font.descent), (w, font.descent),
                                   (0.0, font.ascent), (w, font.ascent)):
                        tx, ty = mat_apply(trm, px, py)
                        xs.append(tx)
                        ys.append(ty)
                    _, base_y = mat_apply(trm, 0.0, 0.0)
                    # Rendered size: the length of the transformed em vertical.
                    ex, ey = mat_apply(trm, 0.0, 1.0)
                    sx, sy = mat_apply(trm, 0.0, 0.0)
                    rendered = ((ex - sx) ** 2 + (ey - sy) ** 2) ** 0.5
                    self.glyphs.append(_Glyph(text=ch, x0=min(xs), y0=min(ys),
                                              x1=max(xs), y1=max(ys),
                                              baseline=base_y, size=rendered,
                                              bold=font.bold, run=self.run))
                elif ch:
                    self.glyphs.append(_Glyph(text=" ", x0=0, y0=0, x1=0, y1=0,
                                              baseline=0, size=0, bold=font.bold,
                                              run=self.run))
                adv = (w * size + char_sp
                       + (word_sp if code == 32 and step == 1 else 0.0)) * hscale
                tm = mat_mul((1.0, 0.0, 0.0, 1.0, adv, 0.0), tm)

        for kind, val in _tokenize(content):
            if kind != "op":
                if kind != "delim":
                    operands.append(val)
                elif val == "[":
                    operands.append("[")
                elif val == "]":
                    operands.append("]")
                continue

            op = val
            try:
                if op == "q":
                    stack.append(_GState(ctm=gs.ctm))
                elif op == "Q":
                    if stack:
                        gs = stack.pop()
                elif op == "cm" and len(operands) >= 6:
                    m = tuple(float(v) for v in operands[-6:])
                    gs = _GState(ctm=mat_mul(m, gs.ctm))      # type: ignore[arg-type]
                elif op == "re" and len(operands) >= 4:
                    x, y, w, h = (float(v) for v in operands[-4:])
                    if w < 0:
                        x, w = x + w, -w
                    if h < 0:
                        y, h = y + h, -h
                    pending.append((x, y, w, h))
                    pending_ctm.append(gs.ctm)
                elif op in ("f", "F", "f*", "b", "b*", "B", "B*"):
                    flush_path(True)
                elif op in ("S", "s"):
                    flush_path(False)
                elif op in ("n", "W", "W*"):
                    # Clipping / no-op path end: discard without painting.
                    if op == "n":
                        pending.clear()
                        pending_ctm.clear()
                elif op == "Do" and operands:
                    self._do_xobject(operands[-1], resources, gs.ctm, depth)
                elif op == "EI":
                    # Recorded at its end, so that an image that never ends is not.
                    self.images.append(_image_box(gs.ctm))
                elif op == "BT":
                    tm = tlm = IDENTITY
                    self.run += 1
                elif op == "ET":
                    pass
                elif op == "Tf" and len(operands) >= 2:
                    font = fonts.get(str(operands[-2]))
                    size = float(operands[-1])
                elif op == "Tm" and len(operands) >= 6:
                    tm = tlm = tuple(          # type: ignore[assignment]
                        float(v) for v in operands[-6:])
                elif op in ("Td", "TD") and len(operands) >= 2:
                    tx, ty = float(operands[-2]), float(operands[-1])
                    if op == "TD":
                        leading = -ty
                    tlm = mat_mul((1.0, 0.0, 0.0, 1.0, tx, ty), tlm)
                    tm = tlm
                elif op == "T*":
                    tlm = mat_mul((1.0, 0.0, 0.0, 1.0, 0.0, -leading), tlm)
                    tm = tlm
                elif op == "TL" and operands:
                    leading = float(operands[-1])
                elif op == "Tc" and operands:
                    char_sp = float(operands[-1])
                elif op == "Tw" and operands:
                    word_sp = float(operands[-1])
                elif op == "Tz" and operands:
                    hscale = float(operands[-1]) / 100.0
                elif op == "Ts" and operands:
                    rise = float(operands[-1])
                elif op == "Tj" and operands:
                    if isinstance(operands[-1], bytes):
                        show(operands[-1])
                elif op == "'" and operands:
                    tlm = mat_mul((1.0, 0.0, 0.0, 1.0, 0.0, -leading), tlm)
                    tm = tlm
                    if isinstance(operands[-1], bytes):
                        show(operands[-1])
                elif op == '"' and len(operands) >= 3:
                    word_sp, char_sp = float(operands[-3]), float(operands[-2])
                    tlm = mat_mul((1.0, 0.0, 0.0, 1.0, 0.0, -leading), tlm)
                    tm = tlm
                    if isinstance(operands[-1], bytes):
                        show(operands[-1])
                elif op == "TJ":
                    for item in operands:
                        if isinstance(item, bytes):
                            show(item)
                        elif isinstance(item, float) and font is not None:
                            adv = -item / 1000.0 * size * hscale
                            tm = mat_mul((1.0, 0.0, 0.0, 1.0, adv, 0.0), tm)
            except (ValueError, TypeError):
                pass  # a malformed operator must not abort the whole page
            operands = []

    def _do_xobject(self, name: object, resources: bytes, ctm: Matrix,
                    depth: int) -> None:
        """Paint an XObject named by ``Do``, recursing into a form.

        A form XObject is how a producer other than the one this reader grew up on
        puts a whole page inside a reusable stream. Ignoring ``Do`` gives back a
        page with no words and no rules, which downstream cannot tell from an
        empty card, so a form is *interpreted*: its own ``/Matrix`` premultiplies
        the current transformation, and its own ``/Resources`` supply its fonts.
        An image carries neither words nor grid lines, so only the box it is
        painted into is recorded. Anything else, or a form that cannot be
        decoded, raises.

        The form's ``/BBox`` is *not* applied as a clip, on the same grounds as the
        interpreter's other clipping paths: reporting a mark that a viewer would
        have hidden is a visible surplus, while dropping one is a silent loss.
        """
        if not isinstance(name, str) or not name:
            return
        xdict = self.doc.get(resources, "XObject")
        if not xdict:
            return
        m = re.search(rb"/" + re.escape(name.encode("latin-1"))
                      + rb"(?![A-Za-z0-9])\s*(\d+)\s+\d+\s+R", xdict)
        if m is None:
            return
        num = int(m.group(1))
        body = self.doc.body(num)
        subtype = self.doc.entry(body, "Subtype") or b""
        if subtype == b"/Image":
            self.images.append(_image_box(ctm))
            return
        if subtype != b"/Form" and not self.doc.entry(body, "BBox"):
            raise PdfError(
                f"XObject /{name} (object {num}) has subtype "
                f"{subtype.decode('latin-1') or 'none'}, which this reader cannot "
                f"interpret; anything it draws would be missing from the page")
        if num in self._active or depth >= _MAX_FORM_DEPTH:
            raise PdfError(
                f"form XObject /{name} (object {num}) nests into itself or more "
                f"than {_MAX_FORM_DEPTH} deep; refusing rather than reading a "
                f"partial page")
        content = self.doc.stream(num)      # PdfError here is the right answer
        matrix = _matrix_entry(self.doc, body) or IDENTITY
        inner = self.doc.get(body, "Resources") or resources
        self._active.add(num)
        try:
            self.run_stream(content, inner, mat_mul(matrix, ctm), depth + 1)
        finally:
            self._active.discard(num)


def _image_box(ctm: Matrix) -> Rect:
    """The box an image is painted into: the unit square, through ``ctm``."""
    xs, ys = zip(*(mat_apply(ctm, x, y)
                   for x, y in ((0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (1.0, 1.0))))
    return Rect(min(xs), min(ys), max(xs), max(ys), True)


def _matrix_entry(doc: _Doc, body: bytes) -> Matrix | None:
    """A form XObject's ``/Matrix``, or ``None`` when it has none."""
    raw = doc.entry(body, "Matrix")
    if not raw:
        return None
    nums = [float(n) for n in re.findall(rb"-?\d*\.?\d+", raw)]
    if len(nums) != 6:
        return None
    return (nums[0], nums[1], nums[2], nums[3], nums[4], nums[5])


# --- glyph -> word -> line ---------------------------------------------


def _to_words(glyphs: list[_Glyph]) -> list[Word]:
    """Join adjacent glyphs into words.

    Breaks on: an explicit space glyph, a change of text object, a baseline
    shift, or a horizontal gap wider than a third of a space.
    """
    words: list[Word] = []
    buf: list[_Glyph] = []

    def emit() -> None:
        if not buf:
            return
        words.append(Word(
            text="".join(g.text for g in buf),
            x0=min(g.x0 for g in buf), y0=min(g.y0 for g in buf),
            x1=max(g.x1 for g in buf), y1=max(g.y1 for g in buf),
            size=max(g.size for g in buf),
            bold=any(g.bold for g in buf),
            baseline=buf[0].baseline,
        ))
        buf.clear()

    for g in glyphs:
        if g.text == " " and g.size == 0.0:
            emit()
            continue
        if buf:
            prev = buf[-1]
            gap = g.x0 - prev.x1
            # A space is about 0.32 em in the sans faces these templates use, and
            # a third of that is comfortably above inter-letter kerning yet below
            # any real word gap.
            if (g.run != prev.run
                    or abs(g.baseline - prev.baseline) > 0.6
                    or gap > 0.32 * max(g.size, prev.size) * 0.85
                    or gap < -0.6 * max(g.size, prev.size)):
                emit()
        buf.append(g)
    emit()
    return words


def group_lines(words: tuple[Word, ...] | list[Word], tol: float = 2.0) -> list[list[Word]]:
    """Group words into lines by baseline proximity, then sort left to right."""
    out: list[list[Word]] = []
    for w in sorted(words, key=lambda w: (w.baseline, w.x0)):
        if out and abs(out[-1][0].baseline - w.baseline) <= tol:
            out[-1].append(w)
        else:
            out.append([w])
    for line in out:
        line.sort(key=lambda w: w.x0)
    return out


# --- entry point --------------------------------------------------------


def extract(path: str | Path, *, page: int = 0) -> Page:
    """Read one page of ``path`` into positioned words and painted rectangles.

    ``page`` is a 0-based index in page-tree order, defaulting to the first page —
    a scorecard has only one, so the common call reads exactly the page a caller
    means. It exists because the other PDFs a club keeps beside its cards (a draw,
    a rules sheet, a team list) are *not* single-page, and the part that matters
    is often not on page 1: reading only the first page of those is not a degraded
    answer, it is the wrong division.

    Keyword-only, so ``extract(path)`` cannot silently become ``extract(path, 2)``
    if the signature ever grows again. Use :func:`page_count` before looping, or
    read ``Page.page_count`` off a page already in hand.
    """
    p, doc = _open(path)
    order = _page_objects(doc)
    if not 0 <= page < len(order):
        raise PdfError(f"no page {page}; the document has {len(order)} "
                       f"page(s), numbered from 0")
    body = doc.body(order[page])

    media = _inherited(doc, body, "MediaBox") or b"[0 0 595 842]"
    nums = [float(n) for n in re.findall(rb"-?[\d.]+", media)][:4]
    if len(nums) != 4:
        nums = [0.0, 0.0, 595.0, 842.0]
    box_w, box_h = abs(nums[2] - nums[0]), abs(nums[3] - nums[1])
    rotation = _rotation(doc, body)
    # A quarter-turn swaps the page's proportions. Reporting the unrotated pair
    # would leave every caller measuring an A4 card against a landscape page.
    width, height = (box_h, box_w) if rotation in (90, 270) else (box_w, box_h)

    resources = _inherited(doc, body, "Resources") or b""

    contents_raw = doc.entry(body, "Contents")
    if not contents_raw:
        raise PdfError("the page has no /Contents")
    chunks = []
    for num in doc.refs(contents_raw):
        chunks.append(doc.stream(num))
    if not chunks:
        raise PdfError("the page's /Contents resolved to no streams")
    content = b"\n".join(chunks)

    # The MediaBox origin may be non-zero, and /Rotate may turn the page; both are
    # folded into one base matrix that lands marks straight in display space.
    base = _base_matrix(min(nums[0], nums[2]), min(nums[1], nums[3]),
                        box_w, box_h, rotation)
    interp = _Interpreter(doc)
    interp.run_stream(content, resources, base)
    words = _to_words(interp.glyphs)
    words.sort(key=lambda w: (round(w.baseline, 1), w.x0))
    if not words and interp.trace.shown:
        blame = in_words(sorted(interp.trace.undecodable)) or "no font at all"
        raise PdfError(
            f"the page draws text but not one glyph could be decoded to a "
            f"character ({interp.trace.shown} show-text operators); the fonts this "
            f"reader could not read are {blame}. A page reported as empty would be "
            f"scored as a card with nothing on it, so it is refused instead")
    return Page(width=width, height=height, words=tuple(words),
                rects=tuple(interp.rects), source=str(p),
                index=page, page_count=len(order), images=tuple(interp.images))


def page_count(path: str | Path) -> int:
    """How many pages ``path`` has, without interpreting any content stream.

    Cheap enough to call before an :func:`extract` loop: it scans the object
    table and walks the page tree, but decompresses nothing.
    """
    _, doc = _open(path)
    return len(_page_objects(doc))


def _base_matrix(x0: float, y0: float, w: float, h: float,
                 rotation: int) -> Matrix:
    """User space -> display space: origin at the MediaBox corner, y down, rotated.

    The unrotated case is the flip this reader has always applied,
    ``Y = h - y``. The three quarter-turns are the same flip composed with the
    rotation the viewer would apply, so that a mark lands where a reader of the
    printed page would point at it.
    """
    shift: Matrix = (1.0, 0.0, 0.0, 1.0, -x0, -y0)
    if rotation == 90:
        turn: Matrix = (0.0, 1.0, 1.0, 0.0, 0.0, 0.0)
    elif rotation == 180:
        turn = (-1.0, 0.0, 0.0, 1.0, w, 0.0)
    elif rotation == 270:
        turn = (0.0, -1.0, -1.0, 0.0, h, w)
    else:
        turn = (1.0, 0.0, 0.0, -1.0, 0.0, h)
    return mat_mul(shift, turn)


def _rotation(doc: _Doc, body: bytes) -> int:
    """A page's ``/Rotate``, inherited if need be, normalised to 0/90/180/270.

    An exported card is normally unrotated, but a scan or a re-save can carry a
    quarter-turn, and ignoring it transposes the whole geometry: the column rules
    the grid is read from become horizontal, and the card is refused for a reason
    that has nothing to do with what is wrong with it. A value that is not a
    multiple of 90 is malformed; there is no safe way to guess which way the page
    faces, so it is refused.
    """
    raw = _inherited(doc, body, "Rotate")
    if raw is None:
        return 0
    m = re.match(rb"[-+]?\d+", raw.strip())
    if m is None:
        return 0
    value = int(m.group()) % 360
    if value % 90:
        raise PdfError(f"the page has /Rotate {m.group().decode()}, "
                       f"which is not a multiple of 90; the page geometry cannot "
                       f"be placed")
    return value


# Feature sniffs, each on the raw bytes because each one is about a structure the
# object scanner cannot see past. ``/Encrypt`` is matched only where it means what
# it says — as a trailer entry pointing at a dictionary — so that the pattern can
# be looked for across the whole file instead of in the last 2 kB.
_ENCRYPT_RE = re.compile(rb"/Encrypt\s*(?:\d+\s+\d+\s+R|<<)")
_OBJSTM_RE = re.compile(rb"/Type\s*/ObjStm(?![A-Za-z])")
_XREFSTM_RE = re.compile(rb"/Type\s*/XRef(?![A-Za-z])")
_CLASSIC_XREF_RE = re.compile(rb"(?:^|[\r\n])\s*xref[\r\n \t]")


def _open(path: str | Path) -> tuple[Path, _Doc]:
    """Read and object-scan a PDF, rejecting the shapes this reader cannot handle.

    Every rejection here is a structure that the ``N G obj`` scan would otherwise
    walk straight past, producing a page with no text and no rectangles. That page
    would then be scored as a card that happened to be blank. Refusing with a
    reason a human can act on is the whole point.
    """
    p = Path(path)
    try:
        data = p.read_bytes()
    except OSError as exc:
        raise PdfError(f"cannot read: {exc.strerror or exc}") from exc
    if not data.startswith(b"%PDF"):
        raise PdfError("not a PDF")
    # The old check looked only at the last 2 kB, which misses an /Encrypt in the
    # first of several trailers in an incrementally updated file — and an
    # encrypted file read as plaintext yields compressed-looking garbage, not an
    # error.
    if _ENCRYPT_RE.search(data):
        raise PdfError(f"the file is encrypted (/Encrypt in the trailer); "
                       f"this reader has no decryption. Re-export or re-save it "
                       f"without a password or permissions password")
    if _OBJSTM_RE.search(data):
        raise PdfError(
            f"objects are packed into a compressed object stream "
            f"(/Type /ObjStm). This reader finds objects by scanning for "
            f"'N G obj' in the file, so objects inside such a stream are "
            f"invisible to it and the page would come out blank. Re-save it "
            f"without object streams, for example "
            f"'qpdf --object-streams=disable --force-version=1.4 in.pdf out.pdf', "
            f"or re-export the card from Match Centre")
    if _XREFSTM_RE.search(data) and not _CLASSIC_XREF_RE.search(data):
        raise PdfError(
            f"the cross-reference is a stream (/Type /XRef) with no classic "
            f"'xref' table. Without one this reader cannot tell which definition "
            f"of an object number is current, and a stale or missing object shows "
            f"up as missing words rather than as an error. Re-save it as PDF 1.4, "
            f"for example 'qpdf --object-streams=disable --force-version=1.4 "
            f"in.pdf out.pdf'")
    return p, _Doc(data)


def _page_objects(doc: _Doc) -> list[int]:
    """Object numbers of every page, in the document's own page order.

    Order comes from Catalog -> Pages -> /Kids, depth first, because /Kids *is*
    the page order; object numbers agree with it only by the generator's habit,
    and any re-save is free to renumber. A /Pages node may nest, so the walk
    recurses, and it remembers what it has visited rather than trusting /Count —
    a file whose kid points back at an ancestor must yield a short page list, not
    hang. When there is no reachable catalog at all it falls back to a scan for
    ``/Type /Page`` in file order, on the same reasoning as before: a page is
    more useful than an exception.
    """
    order: list[int] = []
    seen: set[int] = set()

    def walk(num: int) -> None:
        if num in seen:
            return
        seen.add(num)
        try:
            body = doc.body(num)
        except PdfError:
            return
        kids = doc.entry(body, "Kids")
        if kids and re.search(rb"/Type\s*/Pages(?![A-Za-z])", body):
            for kid in doc.refs(doc.resolve(kids)):
                walk(kid)
        elif re.search(rb"/Type\s*/Page(?![sA-Za-z])", body):
            order.append(num)

    for obj in doc.objs.values():
        if b"/Type" in obj.body and b"/Catalog" in obj.body:
            root = doc.entry(obj.body, "Pages")
            refs = doc.refs(root) if root else []
            if refs:
                walk(refs[0])
                if order:
                    return order

    for num, obj in doc.objs.items():
        if re.search(rb"/Type\s*/Page(?![sA-Za-z])", obj.body):
            order.append(num)
    if not order:
        raise PdfError("no page object found")
    return order


def _inherited(doc: _Doc, body: bytes, key: str, *, depth: int = 8) -> bytes | None:
    """A page attribute, falling back to the /Parent chain the way the spec allows.

    Cards from the Match Centre template carry their own /MediaBox, /Resources and
    (implicitly) /Rotate, so nothing there depends on this. It matters because any
    page of any document can be asked for: the spec lets a page tree hoist all
    three onto an interior /Pages node, and a page whose /Resources is only
    findable through its parent would come back with no fonts and therefore no
    text. ``depth`` bounds a /Parent loop in a damaged file.
    """
    for _ in range(depth):
        value = doc.get(body, key)
        if value is not None:
            return value
        parent = doc.entry(body, "Parent")
        refs = doc.refs(parent) if parent else []
        if not refs:
            return None
        try:
            body = doc.body(refs[0])
        except PdfError:
            return None
    return None
