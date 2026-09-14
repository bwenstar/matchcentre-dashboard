#!/usr/bin/env python3
"""Prototype: write a scorecard PDF the existing parser can read.

Proves the geometry can be regenerated from scratch with the standard library,
which is what a demo-data generator and a real-PDF-free test suite both need.
"""
from __future__ import annotations

import sys

PAGE_W, PAGE_H = 595.0, 842.0

# Measured off a real card (top-left origin, y grows downward).
COLUMNS = (26.4, 61.6, 250.6, 281.1, 313.4, 344.0, 533.0, 568.1)
BANDS = ((126.7, 174.0), (174.0, 221.2), (221.2, 268.5), (268.5, 315.7))
ROW_H = 47.26
BOX = 8.07
HAIR = 0.58

# Widths as a fraction of the em, coarse Helvetica-ish classes.
_NARROW = set("iljtfr.,;:'!|()[] ")


def _w(ch: str) -> float:
    if ch == " ":
        return 0.278
    if ch in _NARROW:
        return 0.30
    if ch.isdigit():
        return 0.556
    if ch.isupper():
        return 0.667
    return 0.556


def text_width(s: str, size: float) -> float:
    return sum(_w(c) for c in s) * size


_TOUNICODE = b"""/CIDInit /ProcSet findresource begin
12 dict begin begincmap
/CMapName /Synthetic def
/CMapType 2 def
1 begincodespacerange
<20> <7E>
endcodespacerange
1 beginbfrange
<20> <7E> <0020>
endbfrange
endcmap
CMapName currentdict /CMap defineresource pop
end
end"""


class Card:
    def __init__(self) -> None:
        self.ops: list[str] = []

    # --- painting ------------------------------------------------------
    def rect(self, x: float, y: float, w: float, h: float) -> None:
        """Filled rect, given top-left origin coordinates."""
        self.ops.append(f"0 0 0 rg {x:.2f} {PAGE_H - y - h:.2f} "
                        f"{w:.2f} {h:.2f} re f")

    def text(self, s: str, x: float, baseline: float, size: float) -> None:
        if not s:
            return
        hexed = "".join(f"{b:02X}" for b in s.encode("latin-1", "replace"))
        self.ops.append(f"BT 0 0 0 rg /F1 {size:.2f} Tf "
                        f"{x:.2f} {PAGE_H - baseline:.2f} Td <{hexed}> Tj ET")

    def centred(self, s: str, cx: float, baseline: float, size: float) -> None:
        self.text(s, cx - text_width(s, size) / 2.0, baseline, size)

    def words(self, s: str, x: float, baseline: float, size: float) -> None:
        """One Tj per word with its own Td, as the real producer does."""
        cursor = x
        for word in s.split():
            self.text(word, cursor, baseline, size)
            cursor += text_width(word, size) + _w(" ") * size

    def checkbox(self, x: float, y: float, *, ticked: bool = False) -> None:
        """Four hairlines, plus a solid fill when ticked."""
        self.rect(x, y, BOX, HAIR)                 # top
        self.rect(x, y + BOX - HAIR, BOX, HAIR)    # bottom
        self.rect(x, y, HAIR, BOX)                 # left
        self.rect(x + BOX - HAIR, y, HAIR, BOX)    # right
        if ticked:
            self.rect(x, y, BOX, BOX)

    # --- output --------------------------------------------------------
    def to_pdf(self) -> bytes:
        content = "\n".join(self.ops).encode("latin-1")
        objects = {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Kids [ 3 0 R ] /Count 1 >>",
            3: (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_W:.0f} "
                f"{PAGE_H:.0f}] /Resources << /Font << /F1 5 0 R >> >> "
                f"/Contents 4 0 R >>").encode("ascii"),
            4: _stream(content),
            5: (b"<< /Type /Font /Subtype /TrueType /BaseFont /AAAAAA+Card"
                b" /FirstChar 32 /LastChar 126 /Widths [ "
                + " ".join(str(int(round(_w(chr(c)) * 1000)))
                           for c in range(32, 127)).encode("ascii")
                + b" ] /FontDescriptor 7 0 R /ToUnicode 6 0 R >>"),
            6: _stream(_TOUNICODE),
            7: b"<< /Type /FontDescriptor /FontName /AAAAAA+Card /MissingWidth 556 >>",
        }
        out = bytearray(b"%PDF-1.4\n")
        offsets = {}
        for num in sorted(objects):
            offsets[num] = len(out)
            out += f"{num} 0 obj\n".encode("ascii") + objects[num] + b"\nendobj\n"
        start = len(out)
        top = max(objects) + 1
        out += f"xref\n0 {top}\n".encode("ascii")
        out += b"0000000000 65535 f \n"
        for num in range(1, top):
            out += (f"{offsets.get(num, 0):010d} 00000 "
                    f"{'n' if num in offsets else 'f'} \n").encode("ascii")
        out += (f"trailer\n<< /Size {top} /Root 1 0 R >>\nstartxref\n"
                f"{start}\n%%EOF\n").encode("ascii")
        return bytes(out)


def _stream(data: bytes) -> bytes:
    return (b"<< /Length " + str(len(data)).encode("ascii") + b" >>\nstream\n"
            + data + b"\nendstream")


# ---------------------------------------------------------------------------


def build(*, club_line="UQ Tennis Club Tuesday Night Fixtures 2026",
          venue="University", court="01", when="Tue 18/08/2026 7:00 PM",
          division="C", ball="Yellow Ball", rnd="1", match_id="3665611",
          home="UQ IGUANAS", away="UQ ICBC", verb="DEFEATS",
          rows=(("1&2", ("Guillaume Evrard", "Sri Krishna Dharmapuri"),
                 ("Becky Zhong", "Zixuan Yang"), 6, 2),
                ("1", ("Guillaume Evrard",), ("Becky Zhong",), 6, 3),
                ("2", ("Sri Krishna Dharmapuri",), ("Zixuan Yang",), 6, 0),
                ("1&2", ("Guillaume Evrard", "Sri Krishna Dharmapuri"),
                 ("Becky Zhong", "Zixuan Yang"), 6, 0)),
          home_roster=(("Guillaume Evrard", 3.82, 3.97, False),
                       ("Jonathan Ellis", 1.82, 1.82, True),
                       ("Sri Krishna Dharmapuri", 4.32, 4.15, False)),
          away_roster=(("Becky Zhong", 2.99, 2.56, False),
                       ("Zixuan Yang", 2.00, 1.86, True),
                       ("Wanlin Chen", 1.63, 1.00, False)),
          winner="home") -> bytes:
    c = Card()
    mid = (COLUMNS[0] + COLUMNS[-1]) / 2.0

    # --- header block ---
    c.words("THE UNIVERSITY OF", 80.95, 28.73, 7.49)
    c.words("QUEENSLAND TENNIS", 80.95, 39.10, 7.49)
    c.words("CLUB INC", 80.95, 49.47, 7.49)
    c.text("www.uqtc.org.au", 80.95, 59.85, 7.49)
    c.text("0733714974", 92.18, 71.95, 6.34)
    c.text("admin@uqtc.org.au", 92.18, 84.05, 6.34)
    c.words(when, 466.98, 28.73, 7.49)
    c.words(f"{venue} {court}", 526.93, 39.10, 6.34)
    c.text("Court:", 471.42, 59.85, 7.49)
    c.centred(club_line, mid, 57.54, 7.49)
    c.centred("Season 2", mid, 67.91, 7.49)
    c.centred(f"Division {division} / {ball} / Round {rnd} / #{match_id}",
              mid, 78.29, 7.49)

    # --- banner ---
    c.words(home, 60.78, 104.22, 10.37)
    away_w = text_width(away, 10.37)
    c.words(away, 568.1 - away_w, 104.22, 10.37)
    c.centred(verb, mid, 103.07, 7.49)

    # --- set table ---
    c.text("Games", 253.34, 116.32, 6.34)
    c.text("SET", 290.54, 116.32, 7.49)
    c.text("Games", 319.64, 116.32, 6.34)
    for index, (marker, hp, ap, hg, ag) in enumerate(rows):
        top, bottom = BANDS[index]
        for x in COLUMNS:                       # the rules the parser reads
            c.rect(x, top, HAIR, ROW_H)
        c.rect(COLUMNS[0], top, COLUMNS[-1] - COLUMNS[0], HAIR)
        c.centred(f"R{index + 1}", (COLUMNS[0] + COLUMNS[1]) / 2, top + 20.7, 8.64)
        c.centred(marker, (COLUMNS[0] + COLUMNS[1]) / 2, top + 31.7, 6.34)
        c.centred(f"R{index + 1}", (COLUMNS[6] + COLUMNS[7]) / 2, top + 20.7, 8.64)
        c.centred(marker, (COLUMNS[6] + COLUMNS[7]) / 2, top + 31.7, 6.34)
        for offset, name in zip((13.8, 28.8)[:len(hp)], hp):
            c.words(name, 114.7, top + offset, 7.49)
        for offset, name in zip((13.8, 28.8)[:len(ap)], ap):
            c.words(name, 412.2, top + offset, 7.49)
        c.centred(str(hg), (COLUMNS[2] + COLUMNS[3]) / 2, top + 25.9, 7.49)
        c.centred("S1", (COLUMNS[3] + COLUMNS[4]) / 2, top + 25.9, 7.49)
        c.centred(str(ag), (COLUMNS[4] + COLUMNS[5]) / 2, top + 25.9, 7.49)
        # status labels, each with its box to the left
        for label, lx in (("Forfeit", 78.65), ("Retired", 115.84),
                          ("Incomplete", 155.61)):
            c.text(label, lx, top + 43.8, 6.34)
            c.checkbox(lx - 10.7, top + 37.45)
        for label, lx in (("Forfeit", 415.04), ("Retired", 452.23),
                          ("Incomplete", 492.00)):
            c.text(label, lx, top + 43.8, 6.34)
            c.checkbox(lx - 10.7, top + 37.45)

    # --- totals ---
    hs = sum(1 for _, _, _, hg, ag in rows if hg > ag)
    as_ = sum(1 for _, _, _, hg, ag in rows if ag > hg)
    hgames = sum(hg for *_, hg, _ in rows)
    agames = sum(ag for *_, ag in rows)
    c.text("Sets", 204.70, 326.67, 6.34)
    c.text("Games", 247.16, 326.67, 6.34)
    c.text("Games", 325.84, 326.67, 6.34)
    c.text("Sets", 376.74, 326.67, 6.34)
    c.centred("TOTAL", mid, 334.16, 6.34)
    c.centred(str(hs), 211.5, 339.92, 6.34)
    c.centred(str(hgames), 258.2, 339.92, 6.34)
    c.centred(str(agames), 336.9, 339.92, 6.34)
    c.centred(str(as_), 383.6, 339.92, 6.34)

    c.words("Match Winner", 40.61, 334.74, 7.49)
    c.checkbox(26.42, 328.39, ticked=winner == "home")
    c.words("Match Winner", 502.67, 334.74, 7.49)
    c.checkbox(560.64, 328.39, ticked=winner == "away")
    c.words("Match Forfeited", 40.61, 347.99, 7.49)
    c.checkbox(26.42, 341.65)
    c.words("Match Forfeited", 495.76, 347.99, 7.49)
    c.checkbox(560.64, 341.65)
    c.words("Match Cancelled", 264.42, 358.36, 7.49)
    c.checkbox(250.02, 352.03)
    c.words("Match Disputed", 264.42, 371.62, 7.49)
    c.checkbox(250.02, 365.28)

    # --- signatures and rosters ---
    c.words("Home Team", 26.21, 411.96, 7.49)
    c.words("Away Team", 359.54, 411.96, 7.49)
    c.words("Signed by:", 26.21, 422.33, 7.49)
    c.words("Signed by:", 359.54, 422.33, 7.49)
    c.text("Players", 26.21, 462.67, 7.49)
    c.text("Rating", 187.76, 462.67, 7.49)
    c.text("Players", 357.61, 462.67, 7.49)
    c.text("Rating", 523.51, 462.67, 7.49)
    for index, roster in ((0, home_roster), (1, away_roster)):
        nx, rx = (26.21, 167.41) if index == 0 else (357.61, 503.15)
        for row, (name, s, d, cap) in enumerate(roster):
            base = 476.50 + row * 10.375
            c.words(name + (" (C)" if cap else ""), nx, base, 7.49)
            c.words(f"{s:.2f} (S) {d:.2f} (D)", rx, base, 7.49)
    c.words("Fill-ins (Home Team)", 67.22, 522.61, 7.49)
    c.words("Fill-ins (Away Team)", 429.86, 522.61, 7.49)
    c.centred("Comments", mid, 569.29, 7.49)
    return c.to_pdf()


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/synth-card.pdf"
    with open(out, "wb") as fh:
        fh.write(build())
    print("wrote", out)
