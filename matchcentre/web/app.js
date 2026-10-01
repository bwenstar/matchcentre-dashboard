/* The dashboard for one competition: everything on screen, drawn from season.json.
 *
 * Reads one document — season.json, whose shape export.py fixes — and renders
 * five views from it. Every number here is either a field of that file or
 * arithmetic over its matches. Nothing about a competition is written into this
 * file, which is a hard requirement rather than a style preference: site.py copies
 * this one file, byte for byte, into every competition directory it writes, so a
 * literal club name, division letter, team, player, round count or set slot would
 * appear on the dashboards of every club that ran the tool. The predecessor of
 * this project learned that the expensive way.
 *
 * What this file exists to prevent, beyond the above:
 *
 * 1. A number that is not evidence looking exactly like one that is. The
 *    predecessor's progress meter read 100% at round three of ten, because a null
 *    round total was treated as "as many rounds as have been played". Here,
 *    meta.rounds_total === null means the meter and every percentage derived from
 *    it are not drawn at all (F3), meta.ladder_complete === false labels the
 *    ladder a snapshot rather than a standing (F10), and meta.provenance marks
 *    every derived identity with a visible flag and a one-line instruction for
 *    correcting it (F7).
 * 2. A scorecard that was seen and not scored disappearing. validation.rejected
 *    is rendered in the health strip as prominently as an error, because a
 *    missing card leaves a plausible ladder that is simply wrong (F4).
 * 3. The competition's own rules being invisible. The scoring scheme, the set
 *    rule and the match format each silently reorder the ladder when they are
 *    wrong, so #rules-banner spells all three out where they cannot be missed.
 *
 * Three deliberate choices worth knowing before editing:
 *
 * 1. Colour is read back out of CSS (readPalette) rather than duplicated as hex
 *    literals. styles.css owns the palette in one place for both modes; the SVG
 *    layer needs concrete values for luminance maths and tooltip keys, so it asks
 *    the cascade instead of keeping a second copy that could drift.
 * 2. Categorical hue is bound to the TEAM, and chosen by hashing the team's slug
 *    into a ring of measured hues (see teamColor). Hiding a series in a legend or
 *    selecting a team therefore never repaints the others, and the ladder's own
 *    order never decides a colour.
 * 3. Charts are drawn at measured pixel width, not scaled through a viewBox, so
 *    axis text stays 11px at phone width instead of shrinking to nothing. That is
 *    why every chart registers a draw() and why resize re-runs them.
 */
'use strict';

/* ============================================================== utilities == */

/* Bumped by export.py when a key changes meaning, or when this page comes to need a
 * key an older document lacks. Checked rather than assumed, so a dashboard opened
 * beside an older or newer season.json says so instead of rendering blanks. 2 is the
 * first format whose matches carry counts_as_played, 3 the first whose cards, fixtures
 * and rounds carry the key this page finds a card by, 4 the first whose rounds say
 * whether the build read their label as a number. A key this page of format 4 reads
 * and an older build of it did not write, meta.regular_fixtures, it prints as
 * unknown, a dash through num(), rather than bumping the format a second time. */
var SEASON_FORMAT = 4;

/* How many categorical hues styles.css names. Read, not chosen, here: the series
 * ramp is used for single-series charts and legend keys. Team hues are not taken
 * from it — see teamColor. */
var PALETTE_SERIES = 6;
var ROW_H = 26;                  // one row of a horizontal bar chart
var BAR_MAX = 18;                // bar thickness cap (spec allows 24; 18 suits these bands)
var GAP = 2;                     // the surface gap, and the surface ring width
var FONT_TICK = '11px system-ui, -apple-system, "Segoe UI", sans-serif';
var FONT_LABEL = '12px system-ui, -apple-system, "Segoe UI", sans-serif';
var FONT_VALUE = '600 12px system-ui, -apple-system, "Segoe UI", sans-serif';

var SVGNS = 'http://www.w3.org/2000/svg';

function el(tag, attrs, kids) {
  var node = document.createElement(tag);
  applyAttrs(node, attrs);
  append(node, kids);
  return node;
}

function svg(tag, attrs, kids) {
  var node = document.createElementNS(SVGNS, tag);
  applyAttrs(node, attrs);
  append(node, kids);
  return node;
}

function applyAttrs(node, attrs) {
  if (!attrs) return;
  Object.keys(attrs).forEach(function (k) {
    var v = attrs[k];
    if (v === null || v === undefined || v === false) return;
    if (k === 'text') { node.textContent = String(v); return; }
    if (k === 'html') { throw new Error('refusing innerHTML'); }
    if (k === 'on') {
      Object.keys(v).forEach(function (evt) { node.addEventListener(evt, v[evt]); });
      return;
    }
    if (k === 'style' && typeof v === 'object') {
      Object.keys(v).forEach(function (p) { node.style.setProperty(p, v[p]); });
      return;
    }
    if (k === 'class') { node.setAttribute('class', v); return; }
    if (v === true) { node.setAttribute(k, ''); return; }
    node.setAttribute(k, String(v));
  });
}

function append(node, kids) {
  if (kids === null || kids === undefined) return;
  if (!Array.isArray(kids)) kids = [kids];
  kids.forEach(function (kid) {
    if (kid === null || kid === undefined || kid === false) return;
    node.appendChild(typeof kid === 'object' ? kid : document.createTextNode(String(kid)));
  });
}

function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

var _measure = document.createElement('canvas').getContext('2d');
function textWidth(str, font) {
  _measure.font = font || FONT_LABEL;
  return _measure.measureText(String(str)).width;
}

function num(v, digits) {
  if (v === null || v === undefined || (typeof v === 'number' && !isFinite(v))) return '—';
  var d = digits === undefined ? 0 : digits;
  return halfUp(Number(v), d).toFixed(d);
}

/* Round a half away from zero, on the decimal a reader would have typed.
 *
 * toFixed() rounds the stored double, and a decimal ending in a half is normally
 * not stored as one: the nearest double to 6.225 is 6.22499999999999964, so
 * toFixed(2) gives 6.22 where the spreadsheet every association keeps its ladder
 * in prints 6.23. A cent under the number on a club's own noticeboard is
 * indistinguishable, to the club, from a scoring bug.
 *
 * String(v) is the shortest decimal that round-trips to v, so shifting the
 * exponent on *that string* rounds the decimal rather than the double. Python's
 * side of this is stats.round_half_up, and the two must agree: build.py prints
 * this ladder to a terminal and this prints the same ladder to a page.
 *
 * A value already in exponent form (1e-7, 1e21) is left alone — 'e' twice over is
 * not a number, and nothing on a tennis ladder reaches either end of that range. */
function halfUp(v, digits) {
  if (!isFinite(v)) return v;
  var sign = v < 0 ? -1 : 1, text = String(Math.abs(v));
  if (text.indexOf('e') !== -1 || text.indexOf('E') !== -1) return v;
  var lifted = Number(text + 'e' + digits);
  if (!isFinite(lifted)) return v;
  return sign * Number(Math.round(lifted) + 'e-' + digits);
}

function signed(v, digits) {
  if (v === null || v === undefined) return '—';
  var s = num(Math.abs(v), digits);
  if (Number(v) > 0) return '+' + s;
  if (Number(v) < 0) return '−' + s;
  return s;
}

/* Through num() rather than toFixed() so that every figure on the page rounds the
 * one way — see halfUp() above. */
function pct(v) { return v === null || v === undefined ? '—' : num(v, 1) + '%'; }

/* A share of a team's games, which is a half whenever a doubles rubber is in it.
 *
 * One decimal place only when there is one to print: the column is mostly whole
 * numbers, and "18.0 of 70" reads as a measurement of something where "18 of 70"
 * reads as the count it is. Goes through num() like every other figure on the
 * page, so it rounds the way the Python does. */
function shareNum(v) {
  if (v === null || v === undefined) return '—';
  return num(v, Number(v) === Math.round(Number(v)) ? 0 : 1);
}

function plural(n, one, many) { return n === 1 ? one : (many || one + 's'); }

function upperFirst(text) { return text.charAt(0).toUpperCase() + text.slice(1); }

function longDate(iso) {
  if (!iso) return '';
  var parts = String(iso).split('-');
  if (parts.length !== 3) return String(iso);
  var d = new Date(Number(parts[0]), Number(parts[1]) - 1, Number(parts[2]));
  if (isNaN(d.getTime())) return String(iso);
  var months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  var days = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  return days[d.getDay()] + ' ' + d.getDate() + ' ' + months[d.getMonth()] + ' ' + d.getFullYear();
}

/* One line of parts, joined the way the page joins them, with an absent part dropped
 * rather than joined. A card whose date did not read is filed undated by the weekday
 * it prints, and gluing its empty date to a separator printed "Round 1 ·  · match
 * #2000001", which reads as something that failed to load. Every line that can be
 * short a part comes through here, so that there is one rule for it and not a copy
 * per line.
 */
function dotted(parts) {
  return parts.filter(function (part) { return !!part; }).join(' · ');
}

function niceMax(v) {
  if (!(v > 0)) return 1;
  var exp = Math.floor(Math.log(v) / Math.LN10);
  var pow = Math.pow(10, exp);
  var f = v / pow;
  var step = f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10;
  return step * pow;
}

/* Clean tick values. `integer` is for count axes (games, sets) where a 0.5 tick
 * would be nonsense — an all-zero team would otherwise get 0.0 / 0.5 / 1.0. */
function ticks(max, count, integer) {
  var want = count || 4;
  var raw = max / want;
  var exp = Math.floor(Math.log(raw) / Math.LN10);
  var pow = Math.pow(10, exp);
  var f = raw / pow;
  var step = (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * pow;
  if (integer) step = Math.max(1, Math.round(step));
  var out = [];
  for (var t = 0; t <= max + step / 1000; t += step) out.push(Math.round(t * 1000) / 1000);
  return out;
}

/* SVG text has no text-overflow, so long category labels get trimmed by
 * measurement. The untrimmed name stays reachable in the tooltip and the table. */
function ellipsize(str, maxWidth, font) {
  var s = String(str);
  if (maxWidth <= 0 || textWidth(s, font) <= maxWidth) return s;
  var cut = s;
  while (cut.length > 1 && textWidth(cut + '…', font) > maxWidth) {
    cut = cut.slice(0, -1);
  }
  return cut + '…';
}

/* Rounded on the data end only, square at the baseline (mark spec). */
function barPath(x, y, w, h, r, end) {
  var rr = Math.max(0, Math.min(r, w, h / 2));
  if (w <= 0.4) return 'M' + x + ' ' + y + 'h' + Math.max(w, 0.4) + 'v' + h + 'h' + -Math.max(w, 0.4) + 'z';
  if (end === 'left') {
    return 'M' + (x + w) + ' ' + y + 'h' + -(w - rr) +
           'a' + rr + ' ' + rr + ' 0 0 0 ' + -rr + ' ' + rr +
           'v' + (h - 2 * rr) +
           'a' + rr + ' ' + rr + ' 0 0 0 ' + rr + ' ' + rr +
           'h' + (w - rr) + 'z';
  }
  if (end === 'top') {
    return 'M' + x + ' ' + (y + h) + 'v' + -(h - rr) +
           'a' + rr + ' ' + rr + ' 0 0 1 ' + rr + ' ' + -rr +
           'h' + (w - 2 * rr) +
           'a' + rr + ' ' + rr + ' 0 0 1 ' + rr + ' ' + rr +
           'v' + (h - rr) + 'z';
  }
  return 'M' + x + ' ' + y + 'h' + (w - rr) +
         'a' + rr + ' ' + rr + ' 0 0 1 ' + rr + ' ' + rr +
         'v' + (h - 2 * rr) +
         'a' + rr + ' ' + rr + ' 0 0 1 ' + -rr + ' ' + rr +
         'h' + -(w - rr) + 'z';
}

/* ================================================================ palette == */

var PAL = {};

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function readPalette() {
  var p = {
    surface: cssVar('--surface-1'),
    surface2: cssVar('--surface-2'),
    text: cssVar('--text-primary'),
    text2: cssVar('--text-secondary'),
    muted: cssVar('--text-muted'),
    grid: cssVar('--grid'),
    axis: cssVar('--axis'),
    deemph: cssVar('--deemph'),
    divPos: cssVar('--div-pos'),
    divNeg: cssVar('--div-neg'),
    divMid: cssVar('--div-mid'),
    series: [],
    seq: []
  };
  for (var i = 1; i <= PALETTE_SERIES; i++) p.series.push(cssVar('--series-' + i));
  for (var j = 1; j <= 6; j++) p.seq.push(cssVar('--seq-' + j));
  // Which mode the cascade actually settled on, asked of the surface rather than
  // of matchMedia: a data-theme override, the OS preference and a print
  // stylesheet all end up here, and the team hue has to sit against whatever
  // surface won.
  p.dark = luminance(p.surface || '#ffffff') < 0.5;
  PAL = p;
  TEAM_COLORS = {};
  return p;
}

function hexToRgb(hex) {
  var h = String(hex).replace('#', '').trim();
  if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
  var n = parseInt(h, 16);
  if (isNaN(n)) return [128, 128, 128];
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

/* Relative luminance, used to pick ink-or-white for text set inside a filled cell
 * — the one place the spec allows text over a data colour — and to ask the
 * cascade whether we are in light or dark mode. */
function luminance(hex) {
  var c = hexToRgb(hex).map(function (v) {
    var s = v / 255;
    return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
}

/* Ink for a number set inside a filled bar or a heat cell. The fill can be any
 * colour — a diverging pole, a step of the ramp, or a hue generated at runtime
 * from a team's slug — so the choice has to hold for every luminance, not for the
 * palette as it stands today.
 *
 * Both constants are forced, and the arithmetic is short enough to state. Ink
 * this dark gives (L + 0.05) / 0.05 against a fill of luminance L, so it needs
 * L >= 0.175 to reach 4.5:1; white gives 1.05 / (L + 0.05), so it needs
 * L <= 0.1833. INK_CUT has to sit in that window, which is why it is not the 0.42
 * it used to be: at 0.42, every fill between 0.1833 and 0.42 got white text, and
 * the third step of the ramp came out at 2.50:1 under a 600-weight number.
 *
 * INK_DARK has to be pure black for the window to exist at all. At #0b0b0b the
 * dark branch does not reach 4.5:1 until L >= 0.190, past where the white branch
 * has already fallen below it — no threshold works, so the near-black used
 * everywhere else in the page cannot be used here. The two branches meet at
 * 4.58:1, which is the best any black-or-white rule can do. */
var INK_CUT = 0.179;
var INK_DARK = '#000000';
var INK_LIGHT = '#ffffff';

function inkOn(hex) { return luminance(hex) > INK_CUT ? INK_DARK : INK_LIGHT; }

/* --- generated team hues ---------------------------------------------------
 *
 * A team's colour comes from its slug, never from its draw code or its ladder
 * position. The predecessor indexed a six-colour palette by draw code; codes are
 * handed out by ordering the discovered teams, so one added team — or one card
 * that failed to parse — renumbered them and every colour on the dashboard moved.
 *
 * The first design here was a free hash: hue = hash % 360. It is maximally stable
 * and it is unreadable. Measured over six real team slugs, its closest pair came
 * out at OKLab ΔE 4.3 for normal vision and 0.4 under deuteranopia — the same
 * colour twice, in a ladder table, implying a relationship that does not exist.
 * So the hash chooses from a ring of hues that were searched for maximum
 * worst-case separation instead:
 *
 *   worst pair over the whole ring, ΔE (OKLab x100), normal / deutan-protan
 *     light  19.4 / 9.5      dark  16.4 / 8.7
 *   contrast against the chart surface: 3.01-7.52 light, 3.33-5.65 dark
 *
 * measured with the dataviz skill's validator in this session, and asserted
 * against this file's own output. Targets are ΔE 15 normal, 8 CVD, 3:1 contrast.
 * Tritan separation is 4.6 and does not meet that target; nothing on this
 * dashboard is keyed by colour alone, which is the condition for accepting it.
 *
 * Six is not an arbitrary size. The same search over chroma 0.13 to 0.16 reached
 * only 15.2 / 7.0 light and 12.7 / 6.7 dark for eight entries, and 10.0 / 0.6 for
 * twelve: past six, a seventh entry cannot clear the floor in both modes. Teams
 * past the sixth therefore share a hue with one earlier team and are told apart by
 * a dash pattern (see teamDash), which is honest — it says "these two are not
 * distinguished by colour" instead of offering two colours that differ only on
 * paper.
 *
 * What the reader gets, exactly: for any competition of six teams or fewer every
 * team has its own colour and the set on screen is the measured ring above. Past
 * six, colour narrows to a hue family and the dash, the legend, the direct label
 * on every line and the values table carry identity.
 *
 * Stability, stated precisely because it is the reason for all of this: a team's
 * colour depends on its own slug and, only when two slugs prefer the same ring
 * entry, on the slugs that sort before it. Adding a team leaves every
 * earlier-sorting team untouched, and every later one whose preferred entry is
 * still free.
 */
var TEAM_RING = [
  { hue: 30, tier: 0 }, { hue: 80, tier: 2 }, { hue: 170, tier: 1 },
  { hue: 245, tier: 2 }, { hue: 290, tier: 0 }, { hue: 335, tier: 2 }
];
var TEAM_L_LIGHT = [0.46, 0.56, 0.66];
var TEAM_L_DARK = [0.55, 0.61, 0.66];
var TEAM_C = 0.16;

// Dash patterns for the second, third and fourth team to land on one hue. A
// competition would need more than 24 teams in one division to run past these.
var TEAM_DASHES = [null, '7 4', '2 3', '11 3 2 3'];

var TEAM_COLORS = {};            // slug -> hex, cleared whenever the palette is re-read

/* FNV-1a, 32-bit, written with shifts because Math.imul is not ES5. The
 * multiplier 16777619 is 2^24 + 2^8 + 2^7 + 2^4 + 2 + 1, so the sum of shifts is
 * the multiply; >>> 0 puts every intermediate back into 32 unsigned bits. */
function hashString(text) {
  var h = 0x811c9dc5;
  var s = String(text);
  for (var i = 0; i < s.length; i++) {
    h = (h ^ s.charCodeAt(i)) >>> 0;
    h = (h + (h << 1) + (h << 4) + (h << 7) + (h << 8) + (h << 24)) >>> 0;
  }
  return h >>> 0;
}

function srgbFromLinear(v) {
  var c = v <= 0.0031308 ? 12.92 * v : 1.055 * Math.pow(v, 1 / 2.4) - 0.055;
  return Math.max(0, Math.min(1, c));
}

function hex2(v) {
  var s = Math.round(v * 255).toString(16);
  return s.length === 1 ? '0' + s : s;
}

/* OKLCH -> sRGB hex (Ottosson's matrices). Out-of-gamut hues have their chroma
 * walked down rather than their channels clipped: clipping shifts the hue, which
 * would make two teams' colours converge for a reason the reader cannot see. */
function oklchHex(L, C, hueDeg) {
  var rad = hueDeg * Math.PI / 180;
  var ca = Math.cos(rad), cb = Math.sin(rad);
  for (var step = 0; step < 24; step++) {
    var c = C * (1 - step * 0.05);
    var a = c * ca, b = c * cb;
    var l_ = L + 0.3963377774 * a + 0.2158037573 * b;
    var m_ = L - 0.1055613458 * a - 0.0638541728 * b;
    var s_ = L - 0.0894841775 * a - 1.2914855480 * b;
    var l = l_ * l_ * l_, m = m_ * m_ * m_, s = s_ * s_ * s_;
    var r = 4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s;
    var g = -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s;
    var bl = -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s;
    var lo = -0.0005, hi = 1.0005;
    if (r >= lo && r <= hi && g >= lo && g <= hi && bl >= lo && bl <= hi) {
      return '#' + hex2(srgbFromLinear(r)) + hex2(srgbFromLinear(g)) + hex2(srgbFromLinear(bl));
    }
    if (step === 23) {
      return '#' + hex2(srgbFromLinear(r)) + hex2(srgbFromLinear(g)) + hex2(srgbFromLinear(bl));
    }
  }
  return PAL.deemph || '#808080';
}

/* Hand out ring entries: each slug asks for the entry its hash names, and a slug
 * whose entry is taken walks forward to the least-used one. Called once per load
 * with the teams in slug order, so the answer does not depend on ladder position,
 * draw codes or the order cards happened to be read in.
 *
 * With six teams or fewer this always ends with every team on its own entry, so
 * no two teams on screen share a colour. */
function assignTeamSlots(slugs) {
  var n = TEAM_RING.length;
  var depth = {};                // ring entry -> how many teams already hold it
  var out = {};
  slugs.forEach(function (slug) {
    var start = hashString(slug) % n;
    var ring = start, lap = depth[start] || 0;
    for (var i = 1; i < n && lap > 0; i++) {
      var probe = (start + i) % n;
      var d = depth[probe] || 0;
      if (d < lap) { ring = probe; lap = d; }
    }
    depth[ring] = lap + 1;
    out[slug] = { ring: ring, lap: lap };
  });
  return out;
}

function teamSlotOf(slug) {
  var rec = (IDX.teamSlot || {})[slug];
  if (rec) return rec;
  // A slug the season's team list does not contain — an opponent named on a card
  // and nowhere else — still needs a colour, and the ring entry its hash asks
  // for is the honest one: it may collide, and it will not move.
  return { ring: hashString(slug) % TEAM_RING.length, lap: 0 };
}

/* The team's own colour. Takes a slug, because a slug is the team's identity: a
 * draw code is optional display metadata and may be absent. */
function teamColor(slug) {
  if (!slug) return PAL.deemph;
  var cached = TEAM_COLORS[slug];
  if (cached) return cached;
  var entry = TEAM_RING[teamSlotOf(slug).ring];
  var tiers = PAL.dark ? TEAM_L_DARK : TEAM_L_LIGHT;
  var hex = oklchHex(tiers[entry.tier], TEAM_C, entry.hue);
  TEAM_COLORS[slug] = hex;
  return hex;
}

/* null for the first team on a hue, an SVG stroke-dasharray for the rest. Two
 * teams that share a hue must differ somewhere the eye can find, and lightness
 * cannot do it: the twelve-entry variant measured ΔE 0.6 under deuteranopia and
 * dropped below the 3:1 contrast floor. */
function teamDash(slug) {
  return TEAM_DASHES[teamSlotOf(slug).lap % TEAM_DASHES.length];
}

/* A legend or tooltip key is a block, not a stroke, so a dashed team reads as
 * bands of its colour. background-color and background-image are set separately:
 * the shorthand would reset the image depending on which lands last. */
function keyStyle(color, dash) {
  if (!dash) return { background: color };
  var parts = String(dash).split(' ');
  var on = Number(parts[0]) || 6;
  var off = Number(parts[1]) || 4;
  return {
    'background-color': color,
    'background-image': 'repeating-linear-gradient(90deg, ' + color + ' 0 ' + on +
      'px, transparent ' + on + 'px ' + (on + off) + 'px)'
  };
}

/* ============================================================== app state == */

var S = null;                    // the season document
var IDX = {};                    // derived lookups
var CHARTS = [];                 // {figure, draw} for the current view
var SOURCE_NOTE = '';
var THEME_KEY = 'matchcentre-theme';
var PLAYER_SORT = { key: 'name', dir: 1 };

function indexSeason(season) {
  var idx = {
    teamBySlug: {}, playerBySlug: {}, matchByKey: {},
    teamsOrdered: [], ladder: [], slots: []
  };
  var teams = (season.teams || []).slice();
  teams.forEach(function (t) { idx.teamBySlug[t.slug] = t; });
  // Ordered by slug, which is the one ordering that is stable: it does not move
  // when a result lands (as ladder order does) and it does not depend on draw
  // codes, which are optional display metadata and may be absent entirely.
  // Plain sort(), not localeCompare: slugs are [a-z0-9-] only, so code-point
  // order is what Python's sorted() gave the head_to_head keys, and the two
  // orderings must agree.
  idx.teamsOrdered = teams.slice().sort(function (a, b) {
    return String(a.slug) < String(b.slug) ? -1 : String(a.slug) > String(b.slug) ? 1 : 0;
  });
  (season.players || []).forEach(function (p) { idx.playerBySlug[p.slug] = p; });
  // By the key the exporter gives each card, which no other card has. Not by the
  // printed match id: a card may print none and two may print one, and an index by
  // it held one card per id, so the page showed one card twice and another nowhere.
  (season.matches || []).forEach(function (m) { idx.matchByKey[m.key] = m; });
  idx.ladder = teams.slice().sort(function (a, b) {
    if (a.position !== b.position) return (a.position || 99) - (b.position || 99);
    return String(a.name).localeCompare(String(b.name));
  });
  idx.slots = discoverSlots(season);
  // Ring entries are handed out over the slug ordering above, and cached colours
  // are keyed by slug, so both are rebuilt together or neither is.
  idx.teamSlot = assignTeamSlots(idx.teamsOrdered.map(function (t) { return t.slug; }));
  TEAM_COLORS = {};
  return idx;
}

/* The match format's set slots, as data. The predecessor hardcoded four of them
 * in one club's order; a format is two slots or six or anything else, and
 * meta.rules.slots is where export.py puts the one the cards described.
 *
 * The fallback reads the slots back off the sets themselves, in the order the
 * card's rows produced them. It exists for a document written before meta.rules
 * did, or one whose meta a human edited; it is still evidence from the cards, and
 * it is never a hardcoded list of slot names. */
function discoverSlots(season) {
  var declared = ((season.meta || {}).rules || {}).slots || [];
  if (declared.length) {
    return declared.map(function (s) {
      return { key: s.key, label: s.label || s.key, doubles: !!s.doubles };
    });
  }
  var seen = {}, out = [];
  (season.matches || []).forEach(function (m) {
    (m.sets || []).forEach(function (st) {
      if (!st.slot || seen[st.slot]) return;
      seen[st.slot] = true;
      out.push({ key: st.slot, label: st.label || st.slot, doubles: !!st.doubles });
    });
  });
  // A slot every team has a record for but no card produced a set for: rare, but
  // dropping it would silently shorten the per-slot tables.
  (season.teams || []).forEach(function (t) {
    Object.keys(t.by_slot || {}).sort().forEach(function (k) {
      if (seen[k]) return;
      seen[k] = true;
      out.push({ key: k, label: k, doubles: false });
    });
  });
  return out;
}

function slotOf(key) {
  var found = IDX.slots.filter(function (s) { return s.key === key; })[0];
  return found || { key: key, label: String(key), doubles: false };
}

function slotLabel(key) { return slotOf(key).label; }

/* Key and label together, for a tooltip or a table cell where the short key
 * alone ("d1") is not self-explanatory. */
function slotTitle(key) {
  var slot = slotOf(key);
  return slot.label === slot.key ? slot.key : slot.key + ' — ' + slot.label;
}

function rules() { return (S && S.meta && S.meta.rules) || {}; }

function meta() { return (S && S.meta) || {}; }

function teamOf(slug) { return IDX.teamBySlug[slug] || null; }

function teamName(slug) {
  var t = teamOf(slug);
  if (t) return t.name;
  // Match rows carry the display name beside the slug, so a team with no ladder
  // row (a card for a team the config never declared) still reads as a name.
  var named = (S.matches || []).filter(function (m) {
    return m.home === slug || m.away === slug;
  })[0];
  if (named) return named.home === slug ? named.home_name : named.away_name;
  return String(slug);
}

function teamShort(slug) {
  var t = teamOf(slug);
  return t ? (t.short || t.name) : teamName(slug);
}

function playerName(slug) {
  var p = IDX.playerBySlug[slug];
  if (p) return p.name;
  var names = S && S.player_names ? S.player_names : {};
  return names[slug] || slug;
}

/* The two slugs sorted lexically and joined with "|", which is what export.py
 * writes. The predecessor used Math.min/Math.max, which on strings coerces to
 * NaN and produces one useless key for every pairing. */
function h2hKey(a, b) {
  return (String(a) < String(b) ? [a, b] : [b, a]).join('|');
}

function h2hFor(a, b) {
  var rec = (S.head_to_head || {})[h2hKey(a, b)];
  if (!rec) return null;
  var low = String(a) < String(b);
  return {
    played: rec.played,
    won: low ? rec.wins_low : rec.wins_high,
    lost: low ? rec.wins_high : rec.wins_low,
    drawn: rec.draws,
    /* Meetings that were played and decided nothing: in `played` and in the games,
     * in neither win column. Defaulted here rather than at each reader, so a
     * document exported before the field existed reads as none of them. */
    undecided: rec.undecided || 0,
    /* Meetings that were finals: in every count above, and in none of the ladder's
     * figures. Carried through here for the same reason as `undecided` -- this is
     * the one shape a renderer sees, so a field left out of it is a field no
     * caption can explain. */
    finals: rec.finals || 0,
    gamesFor: low ? rec.games_low : rec.games_high,
    gamesAgainst: low ? rec.games_high : rec.games_low
  };
}

/* Whether any of these pairings has a meeting that reached no result, and so
 * whether the table drawn from them owes its reader a sentence: the games of such a
 * night are in the cells and cannot be in a W–L beside them.
 *
 * Given the records rather than reading the season, so the grid can ask about every
 * pairing and a team's page about its own -- one table, one answer, no column of
 * dashes on the page of a team whose every night reached a result. `> 0` rather
 * than truthiness, so an older document leaves the sentence out. */
function anyUndecidedMeetings(records) {
  return records.some(function (rec) { return rec && rec.undecided > 0; });
}

/* How many of these pairings' meetings were finals rounds.
 *
 * Summed rather than answered yes-or-no, because the number is what makes the
 * sentence checkable: "one meeting below was a final" sends a club to one row of the
 * Matches list, "some were finals" sends them through the whole season. `|| 0` for
 * the same reason as `undecided` above -- a document exported before the field
 * existed reads as none of them rather than as NaN.
 *
 * Each record must appear once. The grid holds two cells per pairing and would
 * otherwise count every final twice. */
function finalsMeetings(records) {
  return records.reduce(function (n, rec) {
    return n + (rec ? rec.finals || 0 : 0);
  }, 0);
}

/* Whether the ruleset in force separates two level teams on their meetings. Every
 * preset here does; a hand-written `ladder_by` need not, and where it does not, the
 * difference below is a curiosity rather than something that moved a position. */
function laddersTiebreakIsHeadToHead() {
  return (rules().ladder_by || []).indexOf('head_to_head') >= 0;
}

/* The other half of what a pairwise record owes its reader, in one sentence shared
 * by the grid and by a team's own page -- for the reason the run report and the
 * rules banner share theirs: they are the same fact about the same pairings, and
 * worded twice is how they come to disagree.
 *
 * The fact is that finals are counted here and not in the ladder (F11). Both are
 * right about their own question -- a semi-final is a night two teams met, and is
 * not a round of the season being ranked -- but it is not a difference a reader can
 * be expected to guess, and it is sharp: two teams can be level on this table and
 * first and last on the ladder above it.
 *
 * Louder where the ruleset actually breaks ties on head-to-head, because there it
 * is not a curiosity. It decided a ladder position, and a club checking that
 * position against this table is checking it against the wrong matches. */
function finalsMeetingsNote(count) {
  if (!count) return '';
  var text = ' ' + (count === 1 ? 'One meeting below was a final'
                                : count + ' meetings below were finals') +
    ', counted here and not in the ladder, which ranks the regular season only.';
  if (laddersTiebreakIsHeadToHead()) {
    text += ' So where the ladder separates two level teams on head-to-head, it ' +
      'does so on their regular-season meetings, not on the record shown here.';
  }
  return text;
}

function matchesOfTeam(slug) {
  return (S.matches || []).filter(function (m) {
    return m.home === slug || m.away === slug;
  }).sort(byRound);
}

function byRound(a, b) {
  var c = compareRounds(a.round, b.round);
  if (c !== 0) return c;
  return String(a.id).localeCompare(String(b.id));
}

/* A round's place in the season: its index in S.rounds, which the exporter writes
 * in competition.round_sort_key's order -- 1, 2, 10, then any other name, then the
 * finals as a series runs, SF before GF (4.37), and one the draw types under a name
 * of its own where the draw puts it (4.71). The page keeps no copy of that
 * rule. Every round a document holds is in the list; a label that is not goes
 * after all of them. */
function roundPosition(label) {
  var rounds = (S && S.rounds) || [];
  for (var i = 0; i < rounds.length; i++) {
    if (rounds[i].number === String(label)) return i;
  }
  return rounds.length;
}

function compareRounds(a, b) {
  return roundPosition(a) - roundPosition(b);
}

/* Whether the build read a round's label as a number, so that it is titled
 * "Round 3" and "R3". Asked of the document: export.py writes the answer of
 * competition.round_number, the rule the build orders and warns by, as the round's
 * `numbered` (4.215). The page keeps no test of its own, which took a round printed
 * in fullwidth digits, a number to the build, for a name. Strictly `true`, as
 * countsAsPlayed is, so a label no round has, and every round of a document older
 * than the field, is titled as printed. */
function isNumericRound(label) {
  var round = ((S && S.rounds) || [])[roundPosition(label)];
  return !!round && round.numbered === true;
}

/* "R3" for a numbered round, "SF" for a finals one — never "RSF". */
function roundTick(label) {
  return isNumericRound(label) ? 'R' + label : String(label);
}

function roundTitle(label) {
  return isNumericRound(label) ? 'Round ' + label : String(label);
}

/* Whether a match happened, and so has numbers worth counting. Asked of the
 * document: Match.counts_as_played in model.py answers it, export.py writes the
 * answer on every match, and this reads it rather than keeping a list of statuses.
 * The aggregates in season.json are computed from that answer, so anything this
 * page counts for itself has to skip the same matches or the tiles disagree with
 * the ladder beside them.
 *
 * Part of a cancelled night's card is often filled in before play stops — a
 * washout can leave two and a half rubbers scored — and those games belong
 * nowhere.
 *
 * Strictly `true`. Only a document older than the field lacks it, and the data
 * health section already tells that one to rebuild. Counting nothing there would
 * still be a claim with nothing behind it, every card saying it counts for
 * nothing when all the page knows is that the document does not say, so render()
 * draws no view of such a document at all (4.68). */
function countsAsPlayed(match) {
  return !!match && match.counts_as_played === true;
}

/* Every set a player appeared in, derived from the match list — the document
 * keeps per-player aggregates but not their per-set trail, and the player view
 * needs the trail to show results rather than only totals.
 *
 * Which means it has to skip what the aggregates skipped, and `not_credited` is the
 * document's word for that: a card exported twice names the same people as its
 * twin, so reading both lists their night twice under a Matches tile that counts it
 * once. Asked as one field rather than reason by reason — a copy, a clash and a
 * night named two ways all credit nobody — because a list of reasons here silently
 * keeps whichever reason is added next. */
function setsOfPlayer(slug) {
  var out = [];
  (S.matches || []).slice().sort(byRound).forEach(function (m) {
    if (!countsAsPlayed(m) || m.not_credited) return;
    (m.sets || []).forEach(function (st) {
      var side = null;
      if ((st.home_players || []).indexOf(slug) >= 0) side = 'home';
      else if ((st.away_players || []).indexOf(slug) >= 0) side = 'away';
      if (!side) return;
      var mine = side === 'home' ? st.home_games : st.away_games;
      var theirs = side === 'home' ? st.away_games : st.home_games;
      var partner = (side === 'home' ? st.home_players : st.away_players)
        .filter(function (s) { return s !== slug; });
      out.push({
        match: m, set: st, side: side,
        gamesFor: mine, gamesAgainst: theirs,
        partners: partner,
        opponents: (side === 'home' ? st.away_players : st.home_players).slice(),
        won: st.winner === side,
        decided: !!st.winner,
        opponentTeam: side === 'home' ? m.away : m.home
      });
    });
  });
  return out;
}

/* ============================================================ provenance == */

/* F7. meta.provenance says where each kind of identity came from: "declared" (a
 * human wrote it in config.toml), "card" (printed on a scorecard), "derived"
 * (this program worked it out) or "mixed". The first two are evidence and are
 * shown as fact. The other two get a visible marker and, beside it, the one line
 * that tells the reader how to replace a guess with a declaration — because a
 * derived name is neither a fact nor an error, it is an invitation.
 *
 * The competition id is interpolated into the advice so it names the actual
 * section to edit; nothing here is a literal from any club's config. */
var PROVENANCE_ADVICE = {
  teams: 'Team names were read off the cards rather than declared. Put your own ' +
    'spelling in [team_names] in config.toml.',
  players: 'Player names came from the spelling printed on a card, or were ' +
    'title-cased from it. Correct any in [player_names] in config.toml.',
  captains: 'The captain was worked out from a roster mark rather than declared. ' +
    'Set captains under [competitions."{id}"] in config.toml.',
  season: 'No season label was declared, so this one was worked out from the ' +
    'cards. Set season under [competitions."{id}"] in config.toml.',
  rounds_total: 'Nobody has declared how many rounds this season runs, so no ' +
    'percentage and no progress bar are shown. Set rounds_total under ' +
    '[competitions."{id}"] in config.toml.',
  draw: 'The draw was reconstructed from the cards that exist, so it lists no ' +
    'fixture that has not been played. Declare rounds under ' +
    '[competitions."{id}"] in config.toml to see the whole schedule.',
  format: 'The match format was worked out rather than declared. Set format ' +
    'under [competitions."{id}"] in config.toml if it is wrong.'
};

/* The order the derived-values list reads in: identity first, because a wrong
 * name is what a club notices, then the two absences that change arithmetic. */
var PROVENANCE_ORDER = ['teams', 'players', 'captains', 'format', 'season',
                        'draw', 'rounds_total'];

function provenanceOf(aspect) {
  var prov = meta().provenance || {};
  return prov[aspect] || '';
}

/* True for "derived" and for "mixed": some of a mixed collection was guessed, and
 * the reader still has to be told which. */
function isDerived(aspect) {
  var v = provenanceOf(aspect);
  return v === 'derived' || v === 'mixed';
}

function provenanceAdvice(aspect) {
  var text = PROVENANCE_ADVICE[aspect] || '';
  return text.replace('{id}', meta().competition || '');
}

/* The marker itself: small, adjacent to the value it qualifies, and carrying the
 * advice as its title so it is reachable without leaving the number. */
function provenanceFlag(aspect) {
  if (!isDerived(aspect)) return null;
  var mixed = provenanceOf(aspect) === 'mixed';
  return el('span', {
    class: 'provenance-flag provenance-flag--derived',
    title: provenanceAdvice(aspect),
    text: mixed ? 'part derived' : 'derived'
  });
}

/* Every derived aspect, each with its marker and its instruction, for the health
 * strip. Permanent rather than per-view: the reader should not have to find the
 * right page to learn that a name on it was a guess. */
function provenanceBlock() {
  var derived = PROVENANCE_ORDER.filter(isDerived);
  if (!derived.length) return null;
  var box = el('details', { class: 'note' });
  box.appendChild(el('summary', {
    text: derived.length + ' ' + plural(derived.length, 'value') +
      ' we worked out — our best reading, not the club\'s word'
  }));
  var list = el('ul');
  derived.forEach(function (aspect) {
    var li = el('li');
    // "mixed" and "derived" want different words: some team names may be the
    // club's own and only the rest a reading, and a reader who is told all of
    // them are guesses will start correcting entries that were already right.
    var mixed = provenanceOf(aspect) === 'mixed';
    li.appendChild(el('span', {
      class: 'provenance-flag provenance-flag--derived',
      text: aspect.replace(/_/g, ' ') + (mixed ? ' (part derived)' : '')
    }));
    li.appendChild(document.createTextNode(' ' + provenanceAdvice(aspect)));
    list.appendChild(li);
  });
  box.appendChild(list);
  return box;
}

/* ================================================================ tooltip == */

var TIP = null;

function tipNode() {
  if (!TIP) {
    TIP = el('div', { id: 'tooltip', role: 'status', 'aria-live': 'polite', hidden: true });
    document.body.appendChild(TIP);
  }
  return TIP;
}

/* rows: [{color, name, value, kind:'line'|'rect'}] — value leads, name follows. */
function showTip(title, rows, x, y) {
  var t = tipNode();
  clear(t);
  t.appendChild(el('div', { class: 'tt-title', text: title }));
  (rows || []).forEach(function (r) {
    var row = el('div', { class: 'tt-row' });
    if (r.color) {
      row.appendChild(el('span', {
        class: 'tt-key' + (r.kind === 'rect' ? ' rect' : ''),
        style: keyStyle(r.color, r.dash)
      }));
    }
    row.appendChild(el('span', { class: 'tt-name', text: r.name }));
    row.appendChild(el('span', { class: 'tt-val', text: r.value }));
    t.appendChild(row);
  });
  t.hidden = false;
  var box = t.getBoundingClientRect();
  var left = Math.min(Math.max(8, x + 14), window.innerWidth - box.width - 8);
  var top = Math.min(Math.max(8, y - box.height - 12), window.innerHeight - box.height - 8);
  t.style.left = left + 'px';
  t.style.top = top + 'px';
}

function hideTip() { if (TIP) TIP.hidden = true; }

/* Hover and keyboard focus produce the same readout. */
function bindTip(node, get) {
  node.addEventListener('pointerenter', function (e) { place(e.clientX, e.clientY); });
  node.addEventListener('pointermove', function (e) { place(e.clientX, e.clientY); });
  node.addEventListener('pointerleave', hideTip);
  node.addEventListener('focus', function () {
    var b = node.getBoundingClientRect();
    place(b.left + b.width / 2, b.top + b.height / 2);
  });
  node.addEventListener('blur', hideTip);
  function place(x, y) {
    var data = get();
    if (data) showTip(data.title, data.rows, x, y);
  }
}

/* ============================================================ chart shells == */

function chartCard(opts) {
  var fig = el('figure', { class: 'card chart-card' });
  var cap = el('figcaption');
  cap.appendChild(el('h3', { text: opts.title }));
  if (opts.sub) cap.appendChild(el('p', { class: 'sub', text: opts.sub }));
  var plot = el('div', { class: 'plot' });
  var tableWrap = el('div', { class: 'table-wrap', hidden: true });

  if (opts.table) cap.appendChild(tableToggle(tableWrap, tableWrap, 'Values', 'Hide values'));
  fig.appendChild(cap);
  if (opts.legend) fig.appendChild(opts.legend);
  fig.appendChild(plot);
  if (opts.table) tableWrap.appendChild(opts.table);
  fig.appendChild(tableWrap);
  if (opts.footer) fig.appendChild(opts.footer);

  if (opts.draw) CHARTS.push({ figure: fig, plot: plot, draw: opts.draw });
  return fig;
}

/* The button that puts on the screen a table kept off it until it is asked for: a
 * chart's values, and the matches page's table of every rubber. `box` is what is
 * hidden and `wrap` the scrolling wrapper inside it, which are one element for a
 * chart and two for a table that sits in a card of its own. */
function tableToggle(box, wrap, shut, open) {
  var toggle = el('button', {
    class: 'btn table-toggle', type: 'button', 'aria-expanded': 'false', text: shut
  });
  toggle.addEventListener('click', function () {
    var show = box.hidden;
    box.hidden = !show;
    toggle.setAttribute('aria-expanded', show ? 'true' : 'false');
    toggle.textContent = show ? open : shut;
    // A hidden box has no width, so it measured as fitting and lost its tab
    // stop. Now that it is on the screen, ask again.
    markScrollRegion(wrap);
  });
  return toggle;
}

function legendBar(items, onToggle) {
  var wrap = el('div', { class: 'legend' });
  items.forEach(function (it) {
    var key = el('span', {
      class: it.kind === 'line' ? 'key-line' : 'key-rect',
      style: keyStyle(it.color, it.dash)
    });
    if (onToggle) {
      var btn = el('button', {
        class: 'legend-item', type: 'button',
        'aria-pressed': it.on === false ? 'false' : 'true',
        title: 'Show or hide ' + it.name
      }, [key, el('span', { text: it.name })]);
      if (it.on === false) btn.classList.add('off');
      btn.addEventListener('click', function () { onToggle(it, btn); });
      wrap.appendChild(btn);
    } else {
      wrap.appendChild(el('span', { class: 'legend-item' }, [key, el('span', { text: it.name })]));
    }
  });
  return wrap;
}

function emptyPlot(plot, message) {
  clear(plot);
  plot.appendChild(el('p', { class: 'empty', text: message }));
}

/* The words behind the abbreviated headings, for the columns more than one table
 * prints. Diff is a column on seven of them, GF and GA on four, Sets on three;
 * worded at each site they drift, and then a reader comparing a team's page with the
 * ladder is comparing two sentences instead of one column.
 *
 * Only where it really is the same column. A dictionary keyed by the letters would
 * have to pick one meaning each for `D`, which is a drawn match on the ladder and a
 * doubles rating on a team page, and for `W–L`, which is sets on a player's row and
 * meetings on a team's -- so those two are worded where they are used. The sortable
 * players table keeps its own list (PLAYER_COLS) for the same reason it has one at
 * all: its columns carry a sort key and a type as well. What is asked of it is that
 * it says the same thing, not that it says it in the same literal. */
var COL = {
  matchesPlayed: { label: 'P', title: 'Matches played' },
  matchesWon: { label: 'W', title: 'Matches won' },
  matchesLost: { label: 'L', title: 'Matches lost' },
  matchesDrawn: { label: 'D', title: 'Matches drawn' },
  gamesFor: { label: 'GF', title: 'Games for' },
  gamesAgainst: { label: 'GA', title: 'Games against' },
  gamesDiff: { label: 'Diff', title: 'Games differential' },
  setsPlayed: { label: 'Sets', title: 'Sets played' },
  setsWonLost: { label: 'W–L', title: 'Sets won–lost' },
  meetingsWonLost: { label: 'W–L', title: 'Meetings won–lost' }
};

/* A heading cell, and the key that prints what its letters stand for.
 *
 * `title` puts the words in a hover, and a hover is nothing on a phone, nothing on
 * the printout pinned to the clubhouse wall, and nothing to a reader arriving by
 * keyboard, whose tab stop in a sortable table is the button inside the cell. So
 * every table's caption carries the same words in its own line of text, built from
 * the same strings the hover uses -- one wording, two places it can be read.
 *
 * The rejected alternative was sr-only text in each heading cell, which is read out
 * by a screen reader and still invisible in print, and would say "Games
 * differential" once per table for a sighted reader who wanted it too. */
function headCell(h) {
  var cell = el('th', { scope: 'col' });
  if (typeof h === 'object' && h !== null) {
    if (h.sortable) {
      cell.className = 'sortable';
      var btn = el('button', { type: 'button' }, [
        el('span', { text: h.label }),
        el('span', { class: 'arrow', text: h.arrow || '' })
      ]);
      btn.addEventListener('click', h.onSort);
      cell.appendChild(btn);
      if (h.ariaSort) cell.setAttribute('aria-sort', h.ariaSort);
    } else {
      cell.textContent = h.label;
    }
    if (h.title) cell.setAttribute('title', h.title);
  } else {
    cell.textContent = h;
  }
  return cell;
}

function columnKey(head) {
  var seen = {};
  var parts = [];
  head.forEach(function (h) {
    // A heading that is already its own explanation is left alone: "Team — Team" is
    // noise, and the match card heads two columns `G` for the two sides' games, which
    // the key should explain once rather than twice.
    if (!h || typeof h !== 'object' || !h.title || h.title === h.label) return;
    var part = h.label + ' — ' + h.title;
    if (seen[part]) return;
    seen[part] = true;
    parts.push(part);
  });
  return parts.join(' · ');
}

/* The sentence and the key are separate elements so the sentence can still be the
 * table's name where markScrollRegion needs one -- a caption of sentence-then-key
 * read whole makes a long and strange announcement.
 *
 * The export buttons come first in the caption so the float takes its top right
 * corner, and so a reader arriving by keyboard meets them before the table rather
 * than after the last of 24 rows. A table that exports gets a caption even with
 * nothing to say, which is why the emptiness test asks about all three. */
function tableCaption(sentence, head, tools) {
  var key = columnKey(head);
  if (!sentence && !key && !tools) return null;
  var cap = el('caption');
  if (tools) cap.appendChild(tools);
  if (sentence) cap.appendChild(el('span', { class: 'caption-text', text: sentence }));
  if (key) cap.appendChild(el('span', { class: 'table-key', text: key }));
  return cap;
}

function table(head, rows, opts) {
  var o = opts || {};
  var t = el('table');
  var cap = tableCaption(o.caption, head, o.csv ? tableTools(t, o.csv) : null);
  if (cap) t.appendChild(cap);
  var thead = el('thead');
  var tr = el('tr');
  head.forEach(function (h) {
    tr.appendChild(headCell(h));
  });
  thead.appendChild(tr);
  t.appendChild(thead);
  var tbody = el('tbody');
  rows.forEach(function (r) {
    var row = el('tr');
    var cells = Array.isArray(r) ? r : r.cells;
    cells.forEach(function (c, i) {
      var tag = i === 0 && o.rowHeader !== false ? 'th' : 'td';
      var cell = el(tag, i === 0 && o.rowHeader !== false ? { scope: 'row' } : null);
      if (c && typeof c === 'object' && c.nodeType) cell.appendChild(c);
      else if (c && typeof c === 'object') {
        cell.textContent = c.text === undefined ? '' : String(c.text);
        if (c.cls) cell.className = c.cls;
        if (c.style) applyAttrs(cell, { style: c.style });
      } else {
        cell.textContent = c === null || c === undefined ? '—' : String(c);
      }
      row.appendChild(cell);
    });
    if (!Array.isArray(r) && r.href) {
      row.classList.add('clickable');
      row.addEventListener('click', function (e) {
        if (e.target.closest('a, button')) return;
        location.hash = r.href;
      });
    }
    tbody.appendChild(row);
  });
  t.appendChild(tbody);
  return t;
}

/* ======================================================= a table off the page == */

/* A club that wants the ladder in a spreadsheet, or the players table in an email,
 * had no way to get either: no CSV, no clipboard, nothing but selecting 24 rows with
 * a mouse and hoping the columns survive. Two buttons in each table's caption now,
 * Copy and CSV, and they read the table out of the document rather than rebuilding it
 * from the season -- so what leaves the page is what is on it, in the order the reader
 * sorted it into, and there is no second copy of the arithmetic to drift.
 *
 * What a spreadsheet makes of this page's own characters, measured rather than
 * assumed: the file was written, opened with LibreOffice, saved, and the saved
 * document read back for `office:value-type`, which is the spreadsheet's own answer to
 * "is this a number".
 *
 *   as printed      what the spreadsheet made of it       so
 *   −66  U+2212     text                                  translated to -66
 *   -66             the number -66
 *   —    U+2014     text                                  an empty cell instead
 *   (empty)         an empty cell
 *   57.1%           text, or 0.571 shown as 57.1% if the  left as printed
 *                   importer is detecting special numbers
 *   6–7  U+2013     text either way                        left as printed
 *   =SUM(1+1)       the number 2, with the formula still   a space in front
 *                   attached: the spreadsheet ran it
 *
 * That last row is why exportCell puts a space in front of a cell that opens with
 * `=`, `+`, `-` or `@` and is not a number. Player names come off somebody's
 * scorecard and team names out of a config file, so a cell that reads as a formula is
 * a cell somebody else wrote. Measured, a leading space leaves `=SUM(1+1)` as text
 * and leaves `+80` and `5.47` numbers; the apostrophe the same advice usually
 * recommends ends up inside the cell, visible, part of the name.
 *
 * Copy sends tab-separated text, CSV sends commas: tabs are what a spreadsheet splits
 * pasted text on, and quoting `"Cordwainer, Bartholomew"` for a paste would put the
 * quotes on the screen. The CSV leads with a byte-order mark, which LibreOffice was
 * measured to ignore and which is what Excel reads UTF-8 by.
 *
 * The headings go out as the reader sees them -- `GF`, not `Games for`. Spelling them
 * out was rejected: it makes the file disagree with the screen, and the abbreviations
 * are already spelled out in the caption directly above these buttons. */
var NO_VALUE = '—';
var SAID_MS = 4000;
// A blob URL held for ever is a copy of the table held for ever, so it is released.
// The delay is insurance, not a fix: Chrome here was measured downloading the file
// whether the URL was revoked in the same turn as the click or ten seconds later, so
// the honest reason for the timer is the browsers that cannot be measured on this
// machine, where the click is said to only start the read. Ten seconds is long after
// any such read and long before a reader closes the tab.
var REVOKE_MS = 10000;

/* The cell as a reader reads it. The sort arrow is a span of its own inside the
 * heading's button, so it comes out here the way it is already kept out of the
 * accessible name -- a column called `Player▲` is a column nothing can look up.
 *
 * A part of a cell built from the season can say what it leaves as, in
 * `data-export`, where its text alone would say it wrongly. The ladder's Form run is
 * five badges with nothing between them, so its text is `LLLDW`: measured, one
 * column when LibreOffice splits it on spaces, where `L L L D W` is five. The value
 * replaces the part on the copy and before the text is read, so it meets the dash
 * rule and the formula guard below exactly as a value on the screen does.
 * `innerText` was the alternative and is an answer that depends on layout: `L W W W
 * W` on the live cell, and `LWWWW` both on this copy and with the table hidden. */
function cellText(cell) {
  var copy = cell.cloneNode(true);
  Array.prototype.forEach.call(copy.querySelectorAll('.arrow'), function (n) {
    n.parentNode.removeChild(n);
  });
  Array.prototype.forEach.call(copy.querySelectorAll('[data-export]'), function (n) {
    n.parentNode.replaceChild(document.createTextNode(n.getAttribute('data-export')), n);
  });
  return (copy.textContent || '').replace(/\s+/g, ' ').trim();
}

function exportCell(text) {
  if (text === NO_VALUE) return '';
  var out = text.replace(/−/g, '-');
  if (/^[=+\-@]/.test(out) && !/^[+\-]?[0-9]/.test(out)) out = ' ' + out;
  return out;
}

function exportRows(t) {
  var rows = [];
  var head = [];
  Array.prototype.forEach.call(t.querySelectorAll('thead th'), function (th) {
    head.push(cellText(th));
  });
  if (head.length) rows.push(head);
  Array.prototype.forEach.call(t.querySelectorAll('tbody tr'), function (tr) {
    var row = [];
    Array.prototype.forEach.call(tr.children, function (cell) {
      row.push(cellText(cell));
    });
    rows.push(row);
  });
  return rows;
}

function tableText(t, sep) {
  var csv = sep === ',';
  var eol = csv ? '\r\n' : '\n';
  return exportRows(t).map(function (row) {
    return row.map(function (cell) {
      var out = exportCell(cell);
      if (!csv) return out;
      return /[",\r\n]/.test(out) ? '"' + out.replace(/"/g, '""') + '"' : out;
    }).join(sep);
  }).join(eol) + eol;
}

function tableCsv(t) { return '\ufeff' + tableText(t, ','); }

function tableTsv(t) { return tableText(t, '\t'); }

/* The competition in front of the table's own name, because a folder of downloads is
 * where these land and `ladder.csv` there is a file about nothing in particular. And
 * the season between them in an archive of several, because measured on five seasons
 * of one division every one of them downloaded its ladder as
 * `wednesday-division-3-ladder.csv`: the competition's label and the season's label
 * were the same in all five, so five files meant for one folder had one name between
 * them. The key rather than the season's label for that reason -- the key is the
 * season's folder, and two seasons cannot share one. */
function exportFileName(name) {
  var stem = slugifyName([meta().label, seasonKey(), name].filter(function (s) {
    return !!s;
  }).join(' '));
  return (stem || 'table') + '.csv';
}

function downloadText(name, text, type) {
  var url = URL.createObjectURL(new Blob([text], { type: type }));
  // In the document rather than detached: a detached link is enough for Chrome,
  // measured, and was not always enough for Firefox.
  var a = el('a', { href: url, download: name });
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(function () { URL.revokeObjectURL(url); }, REVOKE_MS);
}

/* Three routes to the clipboard, in the order of how much they are allowed to do.
 * navigator.clipboard is defined on a file:// page -- measured, because a page with no
 * origin is exactly where a permission-gated API would be expected to be missing --
 * and execCommand is behind it for the browsers that do not have it and for the ones
 * that refuse. A refusal that says nothing is a button that looks broken, so the last
 * word is the reader's: the CSV button does not need the clipboard at all. */
function copyText(text, say) {
  function fallback() {
    var box = el('textarea', { 'aria-hidden': 'true', style: {
      position: 'fixed', top: '0', left: '-2000px'
    } });
    box.value = text;
    document.body.appendChild(box);
    box.select();
    var done = false;
    try { done = document.execCommand('copy'); } catch (e) { done = false; }
    document.body.removeChild(box);
    say(done ? 'Copied' : 'Nothing was copied — the CSV button still works');
  }
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(function () { say('Copied'); }, fallback);
  } else {
    fallback();
  }
}

/* The visible words are `Copy` and `CSV` for the room they take in a caption; the
 * accessible name carries the table's own name, because a page with 21 tables on it is
 * a page with 21 buttons called Copy, and a list of them is no use to anybody. */
function tableTools(t, name) {
  var said = el('span', { class: 'tools-said', role: 'status', 'aria-live': 'polite' });
  var timer = null;
  function say(words) {
    said.textContent = words;
    if (timer) clearTimeout(timer);
    timer = setTimeout(function () { said.textContent = ''; }, SAID_MS);
  }
  var copy = el('button', {
    class: 'btn', type: 'button', text: 'Copy',
    'aria-label': 'Copy ' + name + ' to the clipboard'
  });
  copy.addEventListener('click', function () { copyText(tableTsv(t), say); });
  var csv = el('button', {
    class: 'btn', type: 'button', text: 'CSV',
    'aria-label': 'Download ' + name + ' as CSV'
  });
  csv.addEventListener('click', function () {
    downloadText(exportFileName(name), tableCsv(t), 'text/csv;charset=utf-8');
  });
  return el('div', { class: 'table-tools' }, [copy, csv, said]);
}

/* Thirteen tables on this page are built inside a `.table-wrap`, which styles.css
 * makes `overflow-x: auto` so a table wider than the screen scrolls sideways
 * instead of pushing the page out. On a phone that is most of them.
 *
 * A div that scrolls cannot be scrolled from a keyboard. There is nothing to focus,
 * so the arrow keys have nothing to act on, and the columns past the right edge are
 * not awkward to reach but unreachable -- with nothing on the page saying they are
 * there. A tab stop on the wrapper fixes that, and creates the second half of the
 * problem: a focusable div with no name, which a screen reader can only announce as
 * a div. So it is named in the same breath, and the name is not copy written for the
 * occasion -- it is the heading of the chart it sits in, or the table's own caption,
 * both already on the screen. Nothing to keep in step, and what is announced is what
 * a sighted reader beside them is looking at.
 *
 * Only while the table really is too wide. A tab stop in front of a table that fits
 * is a keystroke that does nothing, and there would be thirteen of them on a wide
 * screen -- paid for by exactly the reader this is for. Which means measuring, which
 * means after the view is in the document, since scrollWidth is 0 before that.
 *
 * `role="region"` rather than `role="group"`: a region is a landmark, so the table
 * is in the list a screen reader can jump between, which is the point of the
 * exercise. It is claimed only with a name, because an unnamed region announces
 * itself as "region" and nothing else -- a signpost with no writing on it, worse
 * than no role at all.
 */
var REGION_SEQ = 0;

function markScrollRegions() {
  Array.prototype.forEach.call(document.querySelectorAll('.table-wrap'),
    markScrollRegion);
}

function markScrollRegion(wrap) {
  // One pixel of tolerance: a fractional layout width can round scrollWidth up by
  // one at some zoom levels, and a tab stop that scrolls nothing is the thing this
  // is measuring to avoid. A hidden wrapper measures 0 against 0 and falls here,
  // which is why tableToggle re-marks the one it shows.
  if (wrap.scrollWidth - wrap.clientWidth < 2) {
    wrap.removeAttribute('tabindex');
    wrap.removeAttribute('role');
    wrap.removeAttribute('aria-labelledby');
    return;
  }
  var name = scrollRegionName(wrap);
  if (name) {
    if (!name.id) name.id = 'region-name-' + (++REGION_SEQ);
    wrap.setAttribute('role', 'region');
    wrap.setAttribute('aria-labelledby', name.id);
  }
  wrap.setAttribute('tabindex', '0');
}

/* The chart's heading before the table's caption, where a table has both: the
 * heading is what names this table among the others ("Points average by round"),
 * while the caption there is a note about the arithmetic, which makes a poor name
 * and a good sentence.
 *
 * The caption's sentence rather than the whole caption, because the caption also
 * carries the key to the abbreviated headings, and a name ending in "GF — Games for ·
 * GA — Games against · Diff — Games differential" is a name nobody can listen to.
 *
 * There was a third fallback here, naming a caption with no sentence in it after the
 * whole caption. The export buttons are now the first thing in every caption, and
 * aria-labelledby reads an element whole, so what that fallback produced was measured
 * as "CopyCSVM — Matches played · Sets — Sets played · ...". Deleted rather than
 * guarded: over the five routes not one wrapper ever reached it -- every one is named
 * by a chart's heading or by a caption sentence -- and the only caption shape that can
 * reach it is the buttons and a key, whose words the paragraph above already refuses
 * as a name. A wrapper with neither keeps its tab stop and is left with no role, which
 * is what markScrollRegion does with an unnamed region anyway. */
function scrollRegionName(wrap) {
  var figure = wrap.closest('figure');
  var heading = figure ? figure.querySelector('figcaption h3') : null;
  if (heading) return heading;
  return wrap.querySelector('caption .caption-text');
}

/* =========================================================== chart: hbars == */

/* Horizontal bars in two modes:
 *   single    — one measure from a zero baseline (magnitude ranking)
 *   diverging — a for/against pair sharing one games axis around a zero line
 * Both label the value at the tip, outside the bar when it fits there. */
function hbarCard(opts) {
  var rows = opts.rows || [];
  var mode = opts.mode || 'single';

  var tableRows = rows.map(function (r) { return opts.tableRow(r); });
  var card = chartCard({
    title: opts.title,
    sub: opts.sub,
    legend: opts.legend,
    table: rows.length ? table(opts.tableHead, tableRows,
      { caption: opts.tableCaption, csv: opts.tableCsv || opts.title }) : null,
    footer: opts.footer,
    draw: function (plot, width) {
      if (!rows.length) { emptyPlot(plot, opts.empty || 'Nothing to plot yet.'); return; }
      draw(plot, width);
    }
  });
  return card;

  function draw(plot, width) {
    clear(plot);
    // Widest value text sets the gutter, so a tip label can never reach the row
    // label. Diverging needs the gutter on both sides — the left arm's label
    // grows leftward out of the plot.
    var labelW = 0, valueW = 22;
    rows.forEach(function (r) {
      labelW = Math.max(labelW, textWidth(r.label, FONT_LABEL));
      valueW = Math.max(valueW, textWidth(r.valueText, FONT_VALUE),
                        textWidth(r.negText || '', FONT_VALUE));
    });
    valueW = Math.ceil(valueW) + 8;
    labelW = Math.min(Math.ceil(labelW) + 10, Math.max(56, width * 0.34));
    // padBottom carries the tick row AND the axis title on its own line, so the
    // container never crops the axis band (and the two never collide).
    var padTop = 6, padBottom = opts.axisLabel ? 38 : 22;
    var gutters = labelW + valueW * (mode === 'diverging' ? 2 : 1);
    var plotW = Math.max(40, width - gutters);
    var height = padTop + rows.length * ROW_H + padBottom;
    var x0 = mode === 'diverging' ? labelW + valueW : labelW;

    var maxAbs = 0;
    rows.forEach(function (r) {
      maxAbs = Math.max(maxAbs, Math.abs(r.value || 0), Math.abs(r.neg || 0));
    });
    var scaleMax = niceMax(maxAbs || 1);
    var zeroX = mode === 'diverging' ? x0 + plotW / 2 : x0;
    var unit = mode === 'diverging' ? (plotW / 2) / scaleMax : plotW / scaleMax;

    var root = svg('svg', {
      width: width, height: height, viewBox: '0 0 ' + width + ' ' + height,
      role: 'img', 'aria-label': opts.title + '. ' + (opts.sub || '')
    });

    // gridlines: solid hairlines, one shade off the surface
    var gridVals = ticks(scaleMax, mode === 'diverging' ? 2 : 4, opts.integer);
    var decimals = gridVals.length > 1 && gridVals[1] < 1 ? 1 : 0;
    var axisY = padTop + rows.length * ROW_H;
    gridVals.forEach(function (v) {
      // zero sits on the shared baseline, so it is labelled once, not per arm
      var positions = (mode === 'diverging' && v !== 0)
        ? [zeroX - v * unit, zeroX + v * unit]
        : [zeroX + v * unit];
      positions.forEach(function (x) {
        if (v !== 0) {
          root.appendChild(svg('line', {
            x1: x, y1: padTop, x2: x, y2: axisY,
            stroke: PAL.grid, 'stroke-width': 1, 'shape-rendering': 'crispEdges'
          }));
        }
        root.appendChild(svg('text', {
          x: x, y: axisY + 14, fill: PAL.muted, 'font-size': 11,
          'text-anchor': 'middle', text: num(v, decimals)
        }));
      });
    });
    root.appendChild(svg('line', {
      x1: zeroX, y1: padTop, x2: zeroX, y2: axisY,
      stroke: PAL.axis, 'stroke-width': 1, 'shape-rendering': 'crispEdges'
    }));
    if (opts.axisLabel) {
      root.appendChild(svg('text', {
        x: x0 + plotW / 2, y: axisY + 32,
        fill: PAL.muted, 'font-size': 11,
        'text-anchor': 'middle', text: opts.axisLabel
      }));
    }

    rows.forEach(function (r, i) {
      var bandY = padTop + i * ROW_H;
      var thick = Math.min(BAR_MAX, ROW_H - 8);
      var barY = bandY + (ROW_H - thick) / 2;
      var group = r.href ? svg('a', { href: r.href, tabindex: 0 }) : svg('g', { tabindex: 0 });
      group.setAttribute('role', r.href ? 'link' : 'img');
      group.setAttribute('aria-label', r.aria || (r.label + ': ' + r.valueText));
      group.setAttribute('class', 'mark');

      var shown = ellipsize(r.label, labelW - 10, FONT_LABEL);
      var labelText = svg('text', {
        x: labelW - 8, y: bandY + ROW_H / 2 + 4, 'text-anchor': 'end',
        fill: r.dim ? PAL.muted : PAL.text2, 'font-size': 12, text: shown
      });
      if (shown !== r.label) labelText.appendChild(svg('title', { text: r.label }));
      group.appendChild(labelText);

      var posColor = r.color || (mode === 'diverging' ? PAL.divPos : PAL.series[0]);
      var negColor = r.negColor || PAL.divNeg;

      if (mode === 'diverging') {
        var pw = Math.abs(r.value || 0) * unit;
        var nw = Math.abs(r.neg || 0) * unit;
        if (pw > 0) {
          group.appendChild(svg('path', {
            d: barPath(zeroX + GAP / 2, barY, Math.max(pw - GAP / 2, 0.5), thick, 4, 'right'),
            fill: posColor
          }));
        }
        if (nw > 0) {
          group.appendChild(svg('path', {
            d: barPath(zeroX - GAP / 2 - Math.max(nw - GAP / 2, 0.5), barY,
                       Math.max(nw - GAP / 2, 0.5), thick, 4, 'left'),
            fill: negColor
          }));
        }
        group.appendChild(svg('text', {
          x: zeroX + pw + 6, y: barY + thick / 2 + 4, fill: PAL.text,
          'font-size': 12, 'font-weight': 600, text: r.valueText
        }));
        group.appendChild(svg('text', {
          x: zeroX - nw - 6, y: barY + thick / 2 + 4, fill: PAL.text2,
          'font-size': 12, 'text-anchor': 'end', text: r.negText
        }));
      } else {
        var w = Math.max((r.value || 0) * unit, r.value ? 0.5 : 0);
        if (w > 0) {
          group.appendChild(svg('path', {
            d: barPath(zeroX, barY, w, thick, 4, 'right'), fill: posColor
          }));
        }
        /* Label outside the tip when there is room; never clipped inside a short bar.
         *
         * A dimmed row's number is dimmed with it. Measured on eight rows of `Points
         * average by team`, where seven are dimmed as context for the one being read:
         * every value was drawn in `--text-primary` at 600, so the rows held back
         * still had the loudest numbers on the chart. The gutter is still measured at
         * FONT_VALUE's 600 — it is a maximum over every row, so measuring the heavier
         * weight leaves a hair of slack rather than a clipped word.
         *
         * Inside a bar the ink stays `inkOn`, which is chosen for contrast against
         * whatever the bar is filled with; only the weight gives way there. */
        var tw = textWidth(r.valueText, FONT_VALUE);
        var outside = zeroX + w + 6 + tw <= width - 2;
        if (outside) {
          group.appendChild(svg('text', {
            x: zeroX + w + 6, y: barY + thick / 2 + 4,
            fill: r.dim ? PAL.muted : PAL.text,
            'font-size': 12, 'font-weight': r.dim ? 400 : 600, text: r.valueText
          }));
        } else if (w > tw + 16) {
          group.appendChild(svg('text', {
            x: zeroX + w - 6, y: barY + thick / 2 + 4, fill: inkOn(posColor),
            'font-size': 12, 'font-weight': r.dim ? 400 : 600,
            'text-anchor': 'end', text: r.valueText
          }));
        }
      }

      // hit target spans the whole band, comfortably bigger than the mark
      var hit = svg('rect', {
        class: 'hit', x: 0, y: bandY, width: width, height: ROW_H
      });
      group.appendChild(hit);
      bindTip(group, function () { return r.tip; });
      root.appendChild(group);
    });

    plot.appendChild(root);
  }
}

/* ============================================================ chart: line == */

/* Multi-series line with an inverted y (ladder position 1 at the top), a
 * crosshair that snaps to the nearest round, direct labels at the right edge and
 * a toggle-to-isolate legend. Hue is the team's own, derived from its slug, so
 * hiding a series never repaints the survivors. */
function ladderLineCard(opts) {
  var series = opts.series || [];
  var hidden = {};
  var legend = legendBar(series.map(function (s) {
    return { name: s.label, color: s.color, dash: s.dash, kind: 'line' };
  }), function (item, btn) {
    var match = series.filter(function (s) { return s.label === item.name; })[0];
    if (!match) return;
    hidden[match.key] = !hidden[match.key];
    btn.setAttribute('aria-pressed', hidden[match.key] ? 'false' : 'true');
    btn.classList.toggle('off', !!hidden[match.key]);
    redraw();
  });

  var xs = opts.xs || [];
  var tableHead = ['Team'].concat(xs.map(roundTick));
  var tableRows = series.map(function (s) {
    return [s.label].concat(xs.map(function (x) {
      var pt = s.points.filter(function (p) { return p.x === x; })[0];
      return pt ? String(pt.y) : '—';
    }));
  });

  var plotRef = null, widthRef = 0;
  var card = chartCard({
    title: opts.title, sub: opts.sub, legend: legend,
    table: series.length ? table(tableHead, tableRows,
      { caption: opts.tableCaption, csv: opts.tableCsv || opts.title }) : null,
    draw: function (plot, width) {
      plotRef = plot; widthRef = width;
      if (!series.length || !xs.length) {
        emptyPlot(plot, opts.empty || 'No rounds played yet.');
        return;
      }
      draw(plot, width);
    }
  });
  return card;

  function redraw() { if (plotRef) draw(plotRef, widthRef); }

  function draw(plot, width) {
    clear(plot);
    var maxPos = 1;
    series.forEach(function (s) {
      s.points.forEach(function (p) { maxPos = Math.max(maxPos, p.y); });
    });
    var labelW = 0;
    series.forEach(function (s) {
      if (!hidden[s.key]) labelW = Math.max(labelW, textWidth(s.label, FONT_LABEL) + 24);
    });
    // The right gutter holds the direct labels; cap it so the plot keeps most of
    // the width, then trim the labels to whatever the cap left them.
    var padL = 30, padR = Math.min(labelW + 12, Math.max(48, width * 0.34));
    var directLabelW = padR - 33;
    var padT = 10, padB = 30;
    var rowH = 26;
    var plotH = Math.max(rowH * (maxPos - 1), rowH);
    var height = padT + plotH + padB;
    var plotW = Math.max(40, width - padL - padR);
    var stepX = xs.length > 1 ? plotW / (xs.length - 1) : 0;

    function xAt(x) { return padL + xs.indexOf(x) * stepX + (xs.length > 1 ? 0 : plotW / 2); }
    function yAt(pos) { return padT + (pos - 1) * (plotH / Math.max(maxPos - 1, 1)); }

    var root = svg('svg', {
      width: width, height: height, viewBox: '0 0 ' + width + ' ' + height,
      role: 'img', 'aria-label': opts.title + '. ' + (opts.sub || '')
    });

    for (var pos = 1; pos <= maxPos; pos++) {
      root.appendChild(svg('line', {
        x1: padL, y1: yAt(pos), x2: padL + plotW, y2: yAt(pos),
        stroke: PAL.grid, 'stroke-width': 1, 'shape-rendering': 'crispEdges'
      }));
      root.appendChild(svg('text', {
        x: padL - 8, y: yAt(pos) + 4, 'text-anchor': 'end',
        fill: PAL.muted, 'font-size': 11, text: String(pos)
      }));
    }
    xs.forEach(function (x) {
      root.appendChild(svg('text', {
        x: xAt(x), y: height - 12, 'text-anchor': 'middle',
        fill: PAL.muted, 'font-size': 11, text: roundTick(x)
      }));
    });
    root.appendChild(svg('text', {
      x: padL, y: height - 1, fill: PAL.muted, 'font-size': 11,
      text: 'Round →   (1 = top of the ladder)'
    }));

    var crosshair = svg('line', {
      y1: padT, y2: padT + plotH, stroke: PAL.axis, 'stroke-width': 1,
      'shape-rendering': 'crispEdges', opacity: 0
    });
    root.appendChild(crosshair);

    var visible = series.filter(function (s) { return !hidden[s.key]; });
    visible.forEach(function (s) {
      var pts = s.points.filter(function (p) { return xs.indexOf(p.x) >= 0; });
      if (!pts.length) return;
      var d = pts.map(function (p, i) {
        return (i ? 'L' : 'M') + xAt(p.x) + ' ' + yAt(p.y);
      }).join(' ');
      var stroke = s.dim ? PAL.deemph : s.color;
      root.appendChild(svg('path', {
        d: d, fill: 'none', stroke: stroke, 'stroke-width': 2,
        'stroke-dasharray': s.dash || false,
        'stroke-linejoin': 'round', 'stroke-linecap': s.dash ? 'butt' : 'round',
        opacity: s.dim ? 0.9 : 1
      }));
      pts.forEach(function (p) {
        root.appendChild(svg('circle', {
          cx: xAt(p.x), cy: yAt(p.y), r: 4, fill: stroke,
          stroke: PAL.surface, 'stroke-width': GAP
        }));
      });
      var last = pts[pts.length - 1];
      // direct label: a short line-key carries the hue and the dash, the text
      // stays in ink
      root.appendChild(svg('line', {
        x1: xAt(last.x) + 10, y1: yAt(last.y), x2: xAt(last.x) + 24, y2: yAt(last.y),
        stroke: stroke, 'stroke-width': 2, 'stroke-dasharray': s.dash || false,
        'stroke-linecap': s.dash ? 'butt' : 'round'
      }));
      var shown = ellipsize(s.label, directLabelW, FONT_LABEL);
      var labelText = svg('text', {
        x: xAt(last.x) + 29, y: yAt(last.y) + 4, fill: s.dim ? PAL.muted : PAL.text2,
        'font-size': 12, text: shown
      });
      if (shown !== s.label) labelText.appendChild(svg('title', { text: s.label }));
      root.appendChild(labelText);
    });

    // one crosshair layer: the reader aims at a round, not at a 2px line.
    // It pads half a step past each end round so the edges stay easy to hit,
    // without ever reaching outside the drawn width.
    var overlayX = Math.max(0, padL - stepX / 2);
    var overlayW = Math.min(width - overlayX, plotW + stepX);
    var overlay = svg('rect', {
      class: 'hit', x: overlayX, y: padT,
      width: overlayW, height: plotH, tabindex: 0,
      role: 'img', 'aria-label': 'Ladder positions by round. Use left and right arrow keys.'
    });
    var cursor = 0;
    function readout(clientX, clientY) {
      var x = xs[cursor];
      crosshair.setAttribute('x1', xAt(x));
      crosshair.setAttribute('x2', xAt(x));
      crosshair.setAttribute('opacity', 1);
      var rows = visible.map(function (s) {
        var pt = s.points.filter(function (p) { return p.x === x; })[0];
        return {
          color: s.dim ? PAL.deemph : s.color, dash: s.dash,
          kind: 'line', name: s.label,
          value: pt ? opts.valueText(pt) : '—'
        };
      }).sort(function (a, b) { return a.value.localeCompare(b.value, undefined, { numeric: true }); });
      showTip(roundTitle(x), rows, clientX, clientY);
    }
    overlay.addEventListener('pointermove', function (e) {
      var box = root.getBoundingClientRect();
      var rel = e.clientX - box.left - padL;
      cursor = Math.max(0, Math.min(xs.length - 1, Math.round(stepX ? rel / stepX : 0)));
      readout(e.clientX, e.clientY);
    });
    overlay.addEventListener('pointerleave', function () {
      crosshair.setAttribute('opacity', 0);
      hideTip();
    });
    overlay.addEventListener('focus', function () {
      var box = overlay.getBoundingClientRect();
      readout(box.left + box.width / 2, box.top + 20);
    });
    overlay.addEventListener('blur', function () {
      crosshair.setAttribute('opacity', 0);
      hideTip();
    });
    overlay.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowRight') cursor = Math.min(xs.length - 1, cursor + 1);
      else if (e.key === 'ArrowLeft') cursor = Math.max(0, cursor - 1);
      else return;
      e.preventDefault();
      var box = overlay.getBoundingClientRect();
      readout(box.left + box.width / 2, box.top + 20);
    });
    root.appendChild(overlay);

    plot.appendChild(root);
  }
}

/* ========================================================= chart: h2h grid == */

/* A heatmap that IS a table: one hue light->dark on games won, the number
 * printed in every cell in ink chosen by the fill's luminance, so the colour
 * never carries the value alone. */
function h2hGridCard() {
  var teams = IDX.teamsOrdered;
  var maxGames = 1;
  var records = [];
  /* One entry per pairing, not per cell: the grid draws each pairing twice, once
   * from each team's side, and a count of finals summed over the cells would be
   * double. Keyed by the same `h2hKey` the document is. */
  var pairings = {};
  teams.forEach(function (row) {
    teams.forEach(function (col) {
      if (row.slug === col.slug) return;
      var rec = h2hFor(row.slug, col.slug);
      if (!rec) return;
      records.push(rec);
      pairings[h2hKey(row.slug, col.slug)] = rec;
      maxGames = Math.max(maxGames, rec.gamesFor);
    });
  });
  var showUndecided = anyUndecidedMeetings(records);
  var finals = finalsMeetings(Object.keys(pairings).map(function (k) {
    return pairings[k];
  }));

  var wrap = el('div', { class: 'table-wrap' });
  var t = el('table');
  /* The column headings are the short names, which is all a grid this wide has room
   * for, so the key under it is where the full names are written out. */
  var head = [{ label: 'Team' }].concat(teams.map(function (col) {
    return { label: col.short || col.name, title: col.name };
  })).concat([COL.meetingsWonLost]);
  var caption = 'Games won by the row team against the column team. Blank where the fixture has not been played.';
  if (showUndecided) {
    // In the caption rather than only in the cell's hover: a hover is nothing on a
    // phone and nothing on the printout pinned to the clubhouse wall, and without
    // this sentence the row does not add up -- the cell counts the night and the
    // W–L at the end of the row has nowhere to put it.
    caption += ' A meeting that reached no result shows its games here; the W–L column counts the decided meetings only.';
  }
  caption += finalsMeetingsNote(finals);
  t.appendChild(tableCaption(caption, head));
  var thead = el('thead');
  var hrow = el('tr');
  head.forEach(function (h) {
    hrow.appendChild(headCell(h));
  });
  thead.appendChild(hrow);
  t.appendChild(thead);

  var tbody = el('tbody');
  teams.forEach(function (row) {
    var tr = el('tr');
    tr.appendChild(el('th', { scope: 'row' }, [teamLink(row)]));
    var w = 0, l = 0, d = 0;
    teams.forEach(function (col) {
      var td = el('td');
      if (row.slug === col.slug) {
        td.className = 'muted';
        td.textContent = '—';
        td.style.setProperty('background', PAL.surface2);
        tr.appendChild(td);
        return;
      }
      var rec = h2hFor(row.slug, col.slug);
      if (!rec || !rec.played) {
        td.className = 'muted';
        td.textContent = '·';
        td.setAttribute('title', row.name + ' v ' + col.name + ': not played yet');
        tr.appendChild(td);
        return;
      }
      w += rec.won; l += rec.lost; d += rec.drawn;
      var frac = rec.gamesFor / maxGames;
      var step = Math.max(0, Math.min(PAL.seq.length - 1, Math.round(frac * (PAL.seq.length - 1))));
      var fill = PAL.seq[step];
      td.textContent = rec.gamesFor + '–' + rec.gamesAgainst;
      td.style.setProperty('background', fill);
      td.style.setProperty('color', inkOn(fill));
      td.style.setProperty('font-weight', '600');
      var title = row.name + ' won ' + rec.gamesFor + ' games, ' +
        col.name + ' won ' + rec.gamesAgainst;
      if (rec.undecided) {
        // Of this pairing, not of the table: the games in this cell are the ones
        // the reader is asking about.
        title += rec.undecided === rec.played
          ? ' · no result on the card'
          : ' · ' + rec.undecided + ' of the ' + rec.played +
            ' meetings reached no result on the card';
      }
      td.setAttribute('title', title);
      tr.appendChild(td);
    });
    tr.appendChild(el('td', { text: w + '–' + l + (d ? '–' + d : '') }));
    tbody.appendChild(tr);
  });
  t.appendChild(tbody);
  wrap.appendChild(t);

  var scale = el('div', { class: 'scale-legend' }, [
    el('span', { text: '0 games' }),
    el('span', { class: 'scale-ramp', 'aria-hidden': 'true' }),
    el('span', { text: maxGames + ' games' })
  ]);

  var fig = el('figure', { class: 'card chart-card' });
  var cap = el('figcaption');
  cap.appendChild(el('h3', { text: 'Head to head' }));
  // "Every completed meeting" was true of these figures and false of the fixture
  // list beside them, which is what made a blank cell read as deliberate.
  cap.appendChild(el('p', { class: 'sub', text: 'Every meeting played, games for and against.' }));
  fig.appendChild(cap);
  fig.appendChild(wrap);
  fig.appendChild(scale);
  return fig;
}

/* ================================================================= pieces == */

function teamLink(team, opts) {
  var o = opts || {};
  return el('a', { class: 'entity', href: '#/team/' + team.slug, title: team.name }, [
    el('span', { class: 'chip', style: keyStyle(teamColor(team.slug), teamDash(team.slug)), 'aria-hidden': 'true' }),
    el('span', { class: 'name', text: o.long ? team.name : (team.short || team.name) })
  ]);
}

/* A link for a team named only by slug — from a match row, a fixture or a bye.
 * A team with no ladder row still gets its printed name and its own colour. */
function teamLinkBySlug(slug, opts) {
  var t = teamOf(slug);
  if (t) return teamLink(t, opts);
  if (!slug) return el('span', { class: 'muted', text: '—' });
  return el('span', { class: 'entity', title: 'No ladder row for this team' }, [
    el('span', { class: 'chip', style: keyStyle(teamColor(slug), teamDash(slug)), 'aria-hidden': 'true' }),
    el('span', { class: 'name', text: teamName(slug) })
  ]);
}

function playerLink(slug) {
  var p = IDX.playerBySlug[slug];
  var name = playerName(slug);
  if (!p) return el('span', { text: name, title: 'Not in the registered player list' });
  return el('a', { class: 'entity', href: '#/player/' + p.slug }, [
    el('span', { class: 'chip', style: keyStyle(teamColor(p.team), teamDash(p.team)), 'aria-hidden': 'true' }),
    el('span', { class: 'name', text: name })
  ]);
}

function streakWords(streak) {
  if (!streak || !streak.length) return '—';
  var word = streak.type === 'W' ? plural(streak.length, 'win')
    : streak.type === 'L' ? plural(streak.length, 'loss', 'losses')
    : plural(streak.length, 'draw');
  return streak.length + ' ' + word;
}

function formRun(form) {
  if (!form || !form.length) return el('span', { class: 'muted', text: '—' });
  // The results as the season lists them, a space between each: the badges carry no
  // text between them, so without this the ladder's Form column leaves the page as
  // one word (see cellText).
  var run = el('span', { class: 'form-run', 'data-export': form.join(' ') });
  form.forEach(function (r) {
    run.appendChild(el('span', {
      class: 'res ' + r, text: r,
      title: r === 'W' ? 'Win' : r === 'L' ? 'Loss' : 'Draw'
    }));
  });
  return run;
}

function tile(label, value, foot) {
  return el('div', { class: 'card tile' }, [
    el('div', { class: 'label', text: label }),
    el('div', { class: 'value', text: value }),
    foot ? el('div', { class: 'foot', text: foot }) : null
  ]);
}

function note(kind, label, message) {
  return el('p', { class: 'note ' + kind }, [
    el('span', { class: 'ico', 'aria-hidden': 'true', text: kind === 'critical' ? '✕' : '!' }),
    el('span', {}, [el('strong', { text: label + ': ' }), document.createTextNode(message)])
  ]);
}

function sectionTitle(text) { return el('h2', { class: 'section', text: text }); }

/* An element the page shell may or may not provide. Both this file and the shell
 * agree on the names; if a shell is older than a feature, the feature still has
 * to appear rather than vanish silently, so the hook is created and inserted
 * where it belongs. Returns the element, emptied. */
function hook(id, className, host, before) {
  var node = document.getElementById(id);
  if (node) {
    clear(node);
    if (className) {
      // The shell may have styled it already; adding is safe, replacing is not.
      className.split(' ').forEach(function (c) { if (c) node.classList.add(c); });
    }
    return node;
  }
  if (!host) return null;
  node = el('div', { id: id, class: className });
  if (before && before.parentNode === host) host.insertBefore(node, before);
  else host.appendChild(node);
  return node;
}

/* ============================================================ match render == */

function setRow(m, st) {
  function names(list) {
    var wrap = el('span');
    (list || []).forEach(function (slug, i) {
      if (i) wrap.appendChild(document.createTextNode(' + '));
      wrap.appendChild(playerLink(slug));
    });
    if (!list || !list.length) wrap.appendChild(el('span', { class: 'muted', text: 'not recorded' }));
    return wrap;
  }
  /* Whether the night these rubbers were played on is in no figure anywhere: the
   * same question the card around them asks, asked through the same `countsAsPlayed`,
   * so the table and the sentence above it cannot come to disagree about one match.
   *
   * Somebody did win these rubbers, so the mark stays — the cell is about the rubber
   * and the card's sentence is about the night. What stops is its weight. Measured at
   * 900px, once the summary row above was quietened these two cells were the only
   * figures in a washed-out card still set at 600 — which left a rubber that earned
   * nothing printed in the weight of one that earned something, inside a card that
   * twice says the night is counted nowhere. Demoted
   * and not dropped: the winner and loser are still told apart by `.lose`'s colour
   * and by 6 against 0. */
  var unscored = !countsAsPlayed(m);
  var win = unscored ? 'win win--unscored' : 'win';
  var homeCls = st.winner === 'home' ? win : 'lose';
  var awayCls = st.winner === 'away' ? win : 'lose';
  var status = st.status && st.status !== 'played' ? st.status : (st.completed ? '' : 'unfinished');
  return [
    { text: slotLabel(st.slot), cls: '' },
    names(st.home_players),
    { text: num(st.home_games), cls: homeCls },
    { text: num(st.away_games), cls: awayCls },
    names(st.away_players),
    { text: status ? upperFirst(status) : '—',
      cls: status ? '' : 'muted' }
  ];
}

function matchCard(m, opts) {
  var o = opts || {};
  /* Whether every number on this card is in no figure anywhere on the page: a bye,
   * a washout, a week called off. Asked through `countsAsPlayed`, which reads the
   * answer the aggregates were computed with, rather than compared against
   * 'cancelled' here — a hand comparison would miss 'unplayed' and start
   * contradicting the ladder.
   *
   * Not `m.not_credited` or `m.counts_for_ladder`: those are about a card the data
   * cannot trust, and export.py leaves both clean on a washout, because there is
   * nothing wrong with the card. The night just did not happen. */
  var unscored = !countsAsPlayed(m);
  var d = el('details', { class: 'match' });
  if (o.open) d.setAttribute('open', '');
  var summary = el('summary');
  summary.appendChild(el('span', { class: 'side' }, [
    el('span', { class: 'chip', style: keyStyle(teamColor(m.home), teamDash(m.home)), 'aria-hidden': 'true' }),
    el('span', { class: m.result === 'home' ? 'win' : '', text: teamShort(m.home) })
  ]));
  /* Demoted rather than hidden. The games are on the PDF and a captain looking for
   * them should find them; what has to stop is their being the heaviest thing on a
   * row whose numbers are counted nowhere. The row's other bold element is the
   * winner's name, and an unscored night has no winner, so leaving this at 600 made
   * the uncountable figure the only loud thing on the line. */
  summary.appendChild(el('span', { class: unscored ? 'score score--unscored' : 'score' }, [
    document.createTextNode(num(m.home_games) + ' – ' + num(m.away_games))
  ]));
  summary.appendChild(el('span', { class: 'side' }, [
    el('span', { class: 'chip', style: keyStyle(teamColor(m.away), teamDash(m.away)), 'aria-hidden': 'true' }),
    el('span', { class: m.result === 'away' ? 'win' : '', text: teamShort(m.away) })
  ]));
  var bits = [];
  /* First, because it qualifies the sets and the points behind it and a qualifier
   * read after the figures is read after they have been believed. The words are
   * needed as well as the position: the document's own token, lowercase and alone,
   * says what happened to the night and not what became of the numbers printed
   * beside it — and a weight and a colour are not a statement (WCAG 2.2 1.4.1
   * bounds the fix, whatever it did about the original).
   *
   * Asked of the words rather than of `unscored`, which is the status's answer and
   * false on both cards of a night they disagree about: each of them was played. */
  var uncounted = uncountedWords(m);
  if (uncounted) bits.push(uncounted);
  bits.push('sets ' + num(m.home_sets) + '–' + num(m.away_sets));
  bits.push('points ' + num(m.home_points, 1) + ' / ' + num(m.away_points, 1));
  // Why the points beside it are blank, where they are: the card is level and the
  // competition has no draws. Its rubbers count, so this is not the words above.
  if (m.not_counted === 'level') bits.push('level, and this competition has no draws — not in the ladder');
  // The bare token is now the other statuses' business only: 'forfeit' is a result,
  // and a card that reached this page by any other unusual route still needs saying.
  // Without the guard an unscored row reads "cancelled — counts for nothing ·
  // ... · cancelled".
  if (!unscored && m.status !== 'played') bits.push(m.status);
  if (m.forfeited_by) bits.push('forfeited by ' + teamShort(m.forfeited_by));
  if (m.disputed) bits.push('disputed');
  // A different thing from the chip above it, which is the "Match Disputed" box
  // ticked on the card — the club disputing the result. This is the dashboard
  // saying that the names on this card are one of two readings of the night, so
  // none of them were credited with it. Worded apart from "disputed" for that
  // reason: two meanings for one word, two chips apart, is a reader's problem.
  if (m.not_credited === 'names') bits.push('players not credited');
  // Only when a basis actually broke the tie. `tied_on_games` alone is true of a
  // genuine draw, where nothing broke it, and the exporter keeps it false on a
  // match nobody played -- 0-0 is not a tie, it is a night that did not happen.
  // A draw gets nothing extra here: it is level on every basis the ruleset has,
  // which is what the result already says.
  if (m.tied_on_games && m.decided_by === 'sets') {
    bits.push('level on games, decided on sets');
  }
  if ((m.warnings || []).length) bits.push((m.warnings || []).length + ' note' +
    ((m.warnings || []).length === 1 ? '' : 's'));
  summary.appendChild(el('span', { class: 'meta', text: bits.join(' · ') }));
  d.appendChild(summary);

  var body = el('div', { class: 'match-body' });
  body.appendChild(el('div', { class: 'table-wrap' }, [
    // Both sides' games column is headed `G` and worded the same, so the key under
    // the table explains it once.
    table(['Set', teamShort(m.home), { label: 'G', title: 'Games won in the set' },
           { label: 'G', title: 'Games won in the set' }, teamShort(m.away), 'Status'],
      (m.sets || []).map(function (st) { return setRow(m, st); }),
      { caption: dotted([longDate(m.date), 'court ' + (m.court || '—'),
                         matchIdWords(m)]) })
  ]));
  /* Said under the table rather than over it, for the same reason as the note below:
   * the table is a list of real names with real games beside them, and it is what the
   * PDF prints. This is a note on it, not a heading over it.
   *
   * Three nouns because they are counted in three different places, and a reader
   * arrives looking for one of them: a captain for the games, anyone reading the
   * ladder for the sets, a player for their own appearance. And then what the round
   * *is* worth, which is not nothing in every competition — under
   * `unplayed = "average"` it is credited, so "nothing here is counted" on its own
   * would mislead exactly the clubs that pay for a night off. The clause comes from
   * the exporter because scoring.py owns that sentence.
   *
   * In the register of the `Source:` line below rather than note('warning'): a
   * warning says somebody has something to do, and rain is not a data fault.
   *
   * Not on a card of a night the cards disagree about, washed out or not: that round
   * is credited nothing under either preset, and the warning below says what it is
   * worth. Measured under `unplayed = "average"`, a washout card of a clash was told
   * its round was credited, over a team row that was the same under "zero". */
  if (unscored && m.not_counted !== 'conflict') {
    body.appendChild(el('p', { class: 'foot muted', style: { 'font-size': '12px' },
      text: 'Nothing on this card is counted anywhere: not a game, not a set, ' +
            'not an appearance. The round itself is ' + rules().unplayed_worth +
            '.' }));
  }
  /* A night the cards disagree about, said as a warning because somebody has a file
   * to take out, and naming the others because that is the whole of what they need.
   * `otherCards` finds each by its place in the season, and it is named by its
   * `source`, as the exporter writes it on every row. A document without the field
   * names nobody rather than being told the list is empty. Under the table, as the
   * washout's sentence is: it is about these rubbers, and every figure in them is
   * what the PDF prints. What brings the night back is every wrong card gone and not
   * the one: three cards can give three readings, and taking one out leaves two that
   * still disagree. */
  /* Every note about another card of the night names it in this one sentence: the
   * clash's, the copy's and the one for a night named two ways. Built before them,
   * so none of them finds it undefined. */
  var others = otherCards(m).map(function (x) { return x.source; });
  var othersSaid = others.length ?
    plural(others.length, 'The other card is ', 'The other cards are ') +
    others.join('; ') + '. ' : '';
  if (m.not_counted === 'conflict') {
    body.appendChild(note('warning', 'Not counted',
      'The cards for this fixture do not agree about it, so ' +
      plural(others.length, 'neither', 'none of them') + ' is counted: not in the ' +
      'ladder, not in either team’s row, not in anybody’s playing record. Which one ' +
      'is right is not something this page can work out. ' + othersSaid +
      'The night is counted once every card that is wrong has left the data folder.'));
  }
  /* A copy: the night is counted from another card, so taking this file out
   * changes nothing on the page. The card it is counted from says nothing, as
   * there is nothing wrong with it. */
  if (m.not_totalled === 'duplicate') {
    body.appendChild(note('warning', 'Copy',
      'Another card for this fixture says the same, so the night is counted once, ' +
      'from one card, and nothing on this one is counted again. ' + othersSaid +
      'Every figure on the page stays as it is when this file leaves the data folder.'));
  }
  /* Said under the table it is about, because that table is a list of names and
   * every one of them is right — it is what the PDF under `Source:` prints. What is
   * not right is reading it as who was credited for the night. */
  if (m.not_credited === 'names') {
    body.appendChild(note('warning', 'Players not credited',
      'Another card for this fixture shows the same result with a different ' +
      'player in it, so the night counts for the teams and nobody’s playing ' +
      'record includes it. The names above are this card’s reading. ' + othersSaid +
      'Everybody gets the night back when the card that is wrong leaves the ' +
      'data folder.'));
  }
  (m.fill_ins || []).forEach(function (slug) {
    body.appendChild(note('warning', 'Fill-in', playerName(slug) +
      ' played outside their registered roster.'));
  });
  (m.warnings || []).forEach(function (w) {
    body.appendChild(note('warning', 'Parse note', typeof w === 'string' ? w : (w.message || String(w))));
  });
  if (m.source) {
    body.appendChild(el('p', { class: 'foot muted', style: { 'font-size': '12px' },
      text: 'Source: ' + m.source }));
  }
  /* This night's own address, in the body and not in the summary above it. Measured:
   * a link beside the score is folded into the accessible name of the control that
   * opens the card -- `Quolls 12 - 5 Gnats · sets 4-0 match #4200003` -- and takes a
   * focus stop on every one of the 56 rows. In the body it is out of the tab order
   * until the reader opens the night they are reading, because a closed card hides
   * its content through `::details-content`. (A link inside a `<summary>` does not
   * toggle the card in Chrome, measured; the announcement and the tab stops are the
   * reason, not a swallowed click.)
   *
   * `o.own` is the card on that page, where this would be a link to the page the
   * reader is standing on, and a card with no printed id is addressed by nothing. */
  if (m.id && !o.own) {
    body.appendChild(el('p', { class: 'foot match-link', style: { 'font-size': '12px' } }, [
      el('a', { href: '#/match/' + encodeURIComponent(m.id),
                text: 'This match on its own page' })
    ]));
  }
  d.appendChild(body);
  return d;
}

/* What a night that is in no figure anywhere is called: by the card's summary, and by
 * every row of it in the season's table of rubbers, where the card's sentence is not
 * there to say it. One spelling, because two on one page is how two parts of it came
 * to word the same rule differently.
 *
 * And `''` for a night that is in them, so both callers ask this one function whether
 * there is anything to say: two kinds of night count for nothing, and only one of them
 * is a status. A night two cards disagree about was played, on each card, and
 * `countsAsPlayed` says so; the exporter's `not_totalled` is what says it is counted
 * nowhere. Measured before, on the demo with a second round 2 card: its summary read
 * `sets 3–1 · points 6.3 / 3.5`, and its rubbers `Played` with their winners, on a
 * night no figure on the page includes.
 *
 * The same field says which card of a night exported twice is the copy, and which
 * of two naming it differently is the reading not used: counted once, from the
 * other card, and measured, both copies read `sets 4–0 · points 6.4 / 3.4` and all
 * eight rows `Played`. Not `not_counted` or `not_credited`, which also hold a level
 * night, a final and the kept card of a night named two ways, all in the totals.
 * Words that claim no order: the card kept is the one read first, and a browser's
 * second download, `name (1).pdf`, reads before `name.pdf`, so `second copy` was
 * said of the file the club exported first.
 *
 * Both reasons when both hold, the card's own status first, as a washout's words
 * have always begun: one card of a clash can be the washout, and measured, the
 * clash's words in place of its status left `cancelled` nowhere on that card or on
 * its rows, so nothing said which card of the night was the washout. */
function uncountedWords(m) {
  var why = countsAsPlayed(m) ? [] : [m.status];
  if (m.not_totalled === 'conflict') why.push('cards disagree');
  if (m.not_totalled === 'duplicate') why.push('copy of another card');
  if (m.not_totalled === 'names') why.push('reading not used');
  return why.length ? why.join(', ') + ' — counts for nothing' : '';
}

/* The id Match Centre printed on the scorecard, in words. A card that printed none
 * keeps `""` -- `export.py` invents nothing -- and the caption used to read `match #`
 * with nothing after it, which reads as a page that lost the number rather than as a
 * card that never had one. Said in one place because the card's caption and a match's
 * own page both say it. */
function matchIdWords(m) {
  return m.id ? 'match #' + m.id : 'no match id printed';
}

/* A round's bye. Shown rather than left as a gap: the team is not missing a card,
 * it is resting. Whether resting is worth anything is the ruleset's decision, so
 * the line says which it is instead of asserting one.
 *
 * A bye only ever reaches this document when the team list was declared (F1). The
 * predecessor inferred one from whoever had no card that round, which is how a
 * team that played and won was paid for a bye. */
function byeRow(slug) {
  var row = el('div', { class: 'match' });
  var line = el('div', { class: 'summary', style: { display: 'flex', 'flex-wrap': 'wrap',
    'align-items': 'center', gap: '6px 12px', padding: '10px 4px 10px 20px' } });
  line.appendChild(el('span', { class: 'side' }, [teamLinkBySlug(slug)]));
  /* The clause is the exporter's, from scoring.unplayed_worth(), and this line does
   * not branch on `[rules] unplayed` itself: branching here is how it came to have
   * two sentences of its own to keep in step with Python's, which it had already
   * stopped doing. One of the pair never said what a credited round counts *in* —
   * the whole point of the rule — and the other never said the round is not a match
   * played, so the dashboard's account of one rule differed from the run report's
   * and which a club believed depended on where they read it. Both are quoted in
   * that function's docstring, which is the one place this rule is worded. */
  line.appendChild(el('span', { class: 'meta',
    text: 'bye · ' + rules().unplayed_worth }));
  row.appendChild(line);
  return row;
}

/* A fixture with no card is one of two unlike things, and this row said "scheduled"
 * about both: a round in November nobody has reached, and last Tuesday's card that
 * nobody exported. Which it is, `card_missing` on the fixture already answers —
 * worked out in Python, where the draw and the rule that decides it live. Doing it
 * here would need the whole draw in a second language, and the two copies would
 * differ. The data health section names the card; this is the row a reader is
 * looking at when they wonder where it went. */
function scheduledRow(fx) {
  var row = el('div', { class: 'match' });
  var line = el('div', { class: 'summary', style: { display: 'flex', 'flex-wrap': 'wrap',
    'align-items': 'center', gap: '6px 12px', padding: '10px 4px 10px 20px' } });
  line.appendChild(el('span', { class: 'side' }, [teamLinkBySlug(fx.home)]));
  line.appendChild(el('span', { class: 'vs', text: 'v' }));
  line.appendChild(el('span', { class: 'side' }, [teamLinkBySlug(fx.away)]));
  // Through dotted() rather than concatenated, because a round declared without a
  // date and with no card to take one from printed "scheduled · " and stopped -- a
  // separator with nothing after it, which reads as something that failed to load.
  var said = dotted([fx.card_missing ? 'no card yet' : 'scheduled', longDate(fx.date)]);
  // One span either way, so the wording, the colour and the hover cannot drift apart
  // into three answers about one fixture. `applyAttrs` drops a null, so a fixture
  // nobody is waiting on carries no tooltip rather than an empty one.
  line.appendChild(el('span', {
    class: fx.card_missing ? 'meta missing' : 'meta',
    title: fx.card_missing
      ? 'The draw lists this fixture and no scorecard was read for it.' : null,
    text: said }));
  row.appendChild(line);
  return row;
}

/* ============================================================== view: home == */

function renderOverview(main) {
  var m = meta();
  var ladder = IDX.ladder;
  // Two filters, because a match row is a card and these tiles count nights. The
  // first drops the nights nobody played; `not_totalled` drops the cards the
  // document's own aggregates were not built from — a night exported twice, and both
  // readings of a night two cards disagree about. Without it a duplicated card was a
  // second night in every figure in this view: one more match played than there are
  // fixtures in the draw, and that night's sets and games counted twice in tiles
  // sitting directly above a ladder that counted them once.
  //
  // Not `counts_for_ladder`, which is false on a final and on a level night in a
  // grade that cannot draw: both were played, so both belong in tennis played. Not
  // `not_credited` either, which is set on the kept card of a night named two ways —
  // nobody's record, and still the night both teams played.
  var played = (S.matches || []).filter(function (x) {
    return countsAsPlayed(x) && !x.not_totalled;
  });
  var scheduled = (S.fixtures || []).length;
  var drawDeclared = m.draw_source === 'declared';

  var setsPlayed = 0, gamesPlayed = 0, tightest = null, widest = null;
  played.forEach(function (x) {
    setsPlayed += (x.sets || []).filter(function (st) { return st.completed; }).length;
    gamesPlayed += (x.home_games || 0) + (x.away_games || 0);
    if (x.result === 'home' || x.result === 'away') {
      if (!tightest || x.margin < tightest.margin) tightest = x;
      if (!widest || x.margin > widest.margin) widest = x;
    }
  });
  var appeared = (S.players || []).filter(function (p) { return p.matches > 0; }).length;

  var head = el('div', { class: 'view-head' });
  head.appendChild(el('h1', { text: m.label || m.competition || 'Competition' }));
  var line = el('p', {}, [
    document.createTextNode(
      (m.cards_scored || 0) + ' ' + plural(m.cards_scored || 0, 'scorecard') + ' read · ' +
      ladder.length + ' ' + plural(ladder.length, 'team') + ' · ' +
      (S.players || []).length + ' registered ' + plural((S.players || []).length, 'player'))
  ]);
  head.appendChild(line);
  if (m.season_label) {
    var seasonLine = el('p', { class: 'sub muted' }, [
      document.createTextNode(m.season_label + ' ')
    ]);
    var seasonFlag = provenanceFlag('season');
    if (seasonFlag) seasonLine.appendChild(seasonFlag);
    head.appendChild(seasonLine);
  }
  main.appendChild(head);

  // Hero: the one number the season leads with.
  var leader = ladder[0];
  var hero = el('div', { class: 'card hero' });
  hero.appendChild(el('div', { class: 'hero-figure' }, [
    el('div', { class: 'label', text: 'Ladder leader — points average' }),
    el('div', { class: 'value', text: leader && leader.points_average !== null &&
      leader.points_average !== undefined ? num(leader.points_average, 2) : '—' }),
    el('div', { class: 'foot', text: leader
      ? leader.name + ' · ' + num(leader.points, 1) + ' points from ' +
        leader.played + ' ' + plural(leader.played, 'match', 'matches')
      : 'No results yet' })
  ]));
  hero.appendChild(seasonProgress(drawDeclared));
  main.appendChild(hero);

  var slotKeys = IDX.slots.map(function (s) { return s.key; });
  main.appendChild(el('div', { class: 'grid tiles', style: { 'margin-top': '12px' } }, [
    tile('Matches played', String(played.length),
      drawDeclared ? 'of ' + scheduled + ' in the draw' : 'read from scorecards'),
    tile('Sets completed', String(setsPlayed),
      slotKeys.length ? slotKeys.join(' · ') + ' each match' : 'no set slots recorded'),
    tile('Games played', String(gamesPlayed), played.length
      ? num(gamesPlayed / played.length, 1) + ' per match' : 'no matches yet'),
    tile('Tightest match', tightest ? num(tightest.margin) + ' ' +
      plural(tightest.margin, 'game') : '—',
      tightest ? teamShort(tightest.home) + ' v ' + teamShort(tightest.away) +
        ', ' + roundTitle(tightest.round).toLowerCase() : 'no decided matches'),
    tile('Biggest margin', widest ? num(widest.margin) + ' games' : '—',
      widest ? teamShort(widest.result === 'home' ? widest.home : widest.away) +
        ', ' + roundTitle(widest.round).toLowerCase() : 'no decided matches'),
    tile('Players used', appeared + ' of ' + (S.players || []).length,
      (S.players || []).length - appeared + ' yet to play')
  ]));

  main.appendChild(sectionTitle('Ladder'));
  main.appendChild(ladderNote());
  main.appendChild(el('div', { class: 'card' }, [ladderTable()]));

  var charts = el('div', { class: 'grid charts', style: { 'margin-top': '12px' } });
  charts.appendChild(pointsAverageChart(null));
  charts.appendChild(gamesForAgainstChart(null));
  charts.appendChild(ladderHistoryChart(null));
  charts.appendChild(el('div', { class: 'span-2' }, [h2hGridCard()]));
  main.appendChild(charts);

  main.appendChild(sectionTitle('Latest round'));
  var lastRound = (S.rounds || []).filter(function (r) { return r.played; }).sort(function (a, b) {
    return compareRounds(a.number, b.number);
  }).pop();
  var box = el('div', { class: 'card' });
  if (!lastRound) {
    box.appendChild(el('p', { class: 'empty-note', text: 'No round has been played yet.' }));
  } else {
    box.appendChild(el('p', { class: 'sub muted', style: { margin: '0 0 4px' },
      text: dotted([roundTitle(lastRound.number), longDate(lastRound.date)]) }));
    cardsOf(lastRound.keys).forEach(function (match) {
      box.appendChild(matchCard(match));
    });
  }
  main.appendChild(box);
}

/* Season progress, in the two shapes the evidence allows (F3).
 *
 * With meta.rounds_total declared there is a denominator, so there is a meter and
 * a percentage. With it null there is neither, anywhere on the page: the
 * predecessor filled the denominator with the number of rounds played and its
 * meter read 100% complete at round three of ten. "Three rounds played" is the
 * whole of what is known, and it is said as a count.
 *
 * The rounds counted are the regular ones, as the season's length is (4.69), so
 * the finals played are named on a line of their own beneath either shape. The
 * foot's fixtures are those rounds' too, played of listed, as Python counted them
 * (4.194); the Matches played tile below it counts the whole season's. */
function seasonProgress(drawDeclared) {
  var m = meta();
  var playedRounds = m.rounds_played || 0;
  var total = m.rounds_total;
  // num() prints a dash for a count a document built before the key does not have.
  var regular = m.regular_fixtures || {};
  var box = el('div', { class: 'progress' });
  var finals = finalsFoot();

  if (total === null || total === undefined) {
    var label = el('div', { class: 'label' }, [
      el('span', { text: 'Rounds played' }),
      el('span', { text: playedRounds + ' ' + plural(playedRounds, 'round') })
    ]);
    var flag = provenanceFlag('rounds_total');
    if (flag) label.appendChild(flag);
    box.appendChild(label);
    box.appendChild(el('div', { class: 'foot',
      text: num(regular.played) + ' ' + plural(regular.played, 'match', 'matches') +
        ' scored. ' + noLengthReason() }));
    if (finals) box.appendChild(finals);
    return box;
  }

  var percent = Math.max(0, Math.min(100, Math.round((playedRounds / total) * 100)));
  box.appendChild(el('div', { class: 'label' }, [
    el('span', { text: 'Season progress' }),
    el('span', { text: 'Round ' + playedRounds + ' of ' + total })
  ]));
  box.appendChild(el('div', { class: 'meter', role: 'img',
    'aria-label': playedRounds + ' of ' + total + ' rounds played' }, [
    el('span', { style: { width: percent + '%' } })
  ]));
  box.appendChild(el('div', { class: 'foot',
    text: (drawDeclared
      ? num(regular.played) + ' of ' + num(regular.total) + ' fixtures played · '
      : num(regular.played) + ' ' + plural(regular.played, 'match', 'matches') +
        ' scored · ') +
      percent + '% of the declared rounds' }));
  if (finals) box.appendChild(finals);
  return box;
}

/* Why a season has no percentage. meta.rounds_total is null where nobody declared
 * a length, and where F3 refused the one declared as shorter than the regular
 * rounds already played (4.72); provenance still says "declared" then, and the two
 * read together are how Python says which. Told it had not been declared, the club
 * that typed it would look for a typo. */
function noLengthReason() {
  if (provenanceOf('rounds_total') === 'declared') return 'More regular rounds have ' +
    'been played than this season was declared to run, so no percentage is shown.';
  return 'How long this season runs has not been declared, so no percentage is shown.';
}

/* The finals with cards, by the flags season.json's rounds carry: is_finals as the
 * club or the round's own label declared it, played where a card has that round.
 * Named, never counted, and never a final the draw lists that nobody has played. */
function finalsPlayed() {
  return (S.rounds || []).filter(function (r) { return r.is_finals && r.played; })
    .map(function (r) { return roundTitle(r.number); });
}

/* The progress box's line naming them, or null on a season with none. */
function finalsFoot() {
  var finals = finalsPlayed();
  if (!finals.length) return null;
  return el('div', { class: 'foot', text: 'Finals played: ' + finals.join(', ') + '.' });
}

/* Where the ladder breaks ties on head-to-head and two teams have met in the finals,
 * the ladder and the head-to-head grid are worked out over different matches. Said
 * here because this is the line that lists "head to head" as a basis, and that is
 * the phrase a club takes to the grid to check a position with -- where the record
 * they find includes a night the ladder could not use.
 *
 * Both conditions, and asked of the finals actually played rather than of the
 * season having a finals round: a note about a difference that has not arisen yet
 * is a line every club pays for so that one of them is told something true. */
function ladderHeadToHeadNote() {
  if (!laddersTiebreakIsHeadToHead()) return '';
  var byKey = S.head_to_head || {};
  var played = Object.keys(byKey).map(function (k) { return byKey[k]; });
  if (!finalsMeetings(played)) return '';
  return ' Head-to-head there means the regular-season meetings only; the ' +
    'head-to-head grid on this page counts the finals as well.';
}

/* F10. The ladder always states its basis; when teams have played unequal numbers
 * of counted matches it also says so, because a table that looks settled and is
 * not is the failure this rule exists to stop. */
function ladderNote() {
  var m = meta();
  var order = (rules().ladder_by || []).map(function (k) { return k.replace(/_/g, ' '); });
  var basis = order.length ? 'Ordered by ' + order.join(', then ') + '.' : '';
  basis += ladderHeadToHeadNote();
  if (m.ladder_complete) {
    return el('p', { class: 'ladder-note', text: basis });
  }
  return el('p', { class: 'ladder-note ladder-note--incomplete' }, [
    el('span', { class: 'ico', 'aria-hidden': 'true', text: '!' }),
    el('span', { text: ' In progress — teams have played unequal numbers of ' +
      'matches, so this order is a snapshot and not a standing. ' + basis })
  ]);
}

/* Whether the average the ladder is ordered on divides by more matches than the P
 * column counts, which happens only under a ruleset that credits an unplayed round
 * the team's own average, and only once a round has actually gone unplayed. Where
 * it does, Points ÷ P is not Avg and nothing on the page said why.
 *
 * Asked of the exported figures and not of `rules().average_unplayed`: the flag
 * says what the club's rules do with a night off, not whether anybody has had one.
 * Asked of the ladder as a whole and not of one row, because a column present for
 * some teams and absent for others is not a column.
 *
 * Greater rather than merely different, deliberately. The divisor cannot be
 * smaller than the played column -- it is those matches plus the credited ones --
 * and a document written before this field existed answers `undefined > 3` with
 * false, so an old file leaves the column out rather than filling it with dashes,
 * under the format warning that is already shouting about it. */
function averagedRoundsCounted() {
  return IDX.ladder.some(function (t) { return t.counted > t.played; });
}

function ladderTable() {
  var showCounted = averagedRoundsCounted();
  var head = [{ label: '#', title: 'Ladder position' }, 'Team',
      COL.matchesPlayed, COL.matchesWon, COL.matchesLost, COL.matchesDrawn, 'Points']
    .concat(showCounted ? [{ label: 'Counted',
      title: 'Matches counted: what the points average divides by — matches ' +
        'played plus the rounds credited the team’s own average' }] : [])
    .concat([
      { label: 'Avg', title: 'Points average: points divided by matches counted' },
      COL.gamesFor, COL.gamesAgainst, COL.gamesDiff,
      { label: 'Form', title: 'Last five results, oldest first' }]);
  var rows = IDX.ladder.map(function (t) {
    return {
      href: '#/team/' + t.slug,
      cells: [
        { text: t.position === null || t.position === undefined ? '—' : t.position, cls: 'pos' },
        teamLink(t, { long: true }),
        t.played, t.won, t.lost, t.drawn,
        num(t.points, 1)
      ].concat(showCounted ? [t.counted] : []).concat([
        t.points_average === null || t.points_average === undefined
          ? { text: '—', cls: 'muted' } : num(t.points_average, 2),
        t.games_won, t.games_lost, signed(t.games_diff),
        formRun(t.form)
      ])
    };
  });
  /* Counted used to be explained again by hand here, because a title attribute is a
   * hover and a hover is nothing on a phone. Every table's caption now prints the key
   * to its own headings, so the sentence is written once, in the column's spec. */
  return el('div', { class: 'table-wrap' }, [
    table(head, rows, { caption: 'Select a row for the team page.', csv: 'ladder' })
  ]);
}

/* ============================================================ team charts == */

function pointsAverageChart(selectedSlug) {
  // The caption below promises the reader an arithmetic they can check, and the
  // divisor is the ladder's Counted column rather than its P wherever the two
  // differ. Asked once, for the same reason the ladder asks once.
  var showCounted = averagedRoundsCounted();
  var rows = IDX.ladder.filter(function (t) {
    return t.points_average !== null && t.points_average !== undefined;
  }).map(function (t) {
    var isSel = !!selectedSlug && t.slug === selectedSlug;
    return {
      label: t.short || t.name,
      value: t.points_average,
      valueText: num(t.points_average, 2),
      href: '#/team/' + t.slug,
      // Emphasis, not recolour-by-rank: one hue for the series, grey for context.
      color: !selectedSlug ? PAL.series[0] : (isSel ? teamColor(t.slug) : PAL.deemph),
      dim: !!selectedSlug && !isSel,
      tip: {
        title: t.name,
        rows: [
          { name: 'Points average', value: num(t.points_average, 2), kind: 'rect',
            color: isSel ? teamColor(t.slug) : PAL.series[0] },
          { name: 'Points', value: num(t.points, 1) },
          { name: 'Played', value: String(t.played) }
        ].concat(showCounted ? [{ name: 'Counted', value: String(t.counted) }] : [])
          .concat([{ name: 'Ladder position', value: String(t.position) }])
      }
    };
  });
  return hbarCard({
    title: 'Points average by team',
    sub: !selectedSlug
      ? 'The measure the ladder is ordered on.'
      : 'The selected team in its own colour, the rest as context.',
    rows: rows,
    mode: 'single',
    axisLabel: 'Points average',
    empty: 'No team has a points average yet.',
    tableHead: ['Team', 'Points average', 'Points', 'Played']
      .concat(showCounted ? ['Counted'] : []),
    tableCaption: 'Points average = points ÷ matches counted.',
    tableRow: function (r) {
      var t = IDX.teamBySlug[r.href.split('/').pop()];
      return [r.label, num(t.points_average, 2), num(t.points, 1), String(t.played)]
        .concat(showCounted ? [String(t.counted)] : []);
    }
  });
}

function gamesForAgainstChart(selectedSlug) {
  var rows = IDX.ladder.map(function (t) {
    var isSel = !!selectedSlug && t.slug === selectedSlug;
    var dim = !!selectedSlug && !isSel;
    return {
      label: t.short || t.name,
      value: t.games_won, neg: t.games_lost,
      valueText: num(t.games_won), negText: num(t.games_lost),
      href: '#/team/' + t.slug,
      color: dim ? PAL.deemph : PAL.divPos,
      negColor: dim ? PAL.deemph : PAL.divNeg,
      dim: dim,
      aria: t.name + ': ' + t.games_won + ' games won, ' + t.games_lost + ' games lost',
      tip: {
        title: t.name,
        rows: [
          { name: 'Games won', value: num(t.games_won), kind: 'rect', color: PAL.divPos },
          { name: 'Games lost', value: num(t.games_lost), kind: 'rect', color: PAL.divNeg },
          { name: 'Differential', value: signed(t.games_diff) },
          { name: 'Sets', value: num(t.sets_won) + '–' + num(t.sets_lost) }
        ]
      }
    };
  });
  return hbarCard({
    title: 'Games for and against',
    sub: 'Won to the right, lost to the left, on one shared games axis.',
    rows: rows,
    mode: 'diverging',
    axisLabel: 'Games',
    integer: true,
    legend: legendBar([
      { name: 'Games won', color: PAL.divPos, kind: 'rect' },
      { name: 'Games lost', color: PAL.divNeg, kind: 'rect' }
    ]),
    empty: 'No games recorded yet.',
    tableHead: ['Team', 'Games won', 'Games lost', 'Differential', 'Sets won', 'Sets lost'],
    tableRow: function (r) {
      var t = IDX.teamBySlug[r.href.split('/').pop()];
      return [r.label, num(t.games_won), num(t.games_lost), signed(t.games_diff),
        num(t.sets_won), num(t.sets_lost)];
    }
  });
}

function ladderHistoryChart(selectedSlug) {
  var xsSet = {};
  IDX.teamsOrdered.forEach(function (t) {
    (t.ladder_history || []).forEach(function (h) { xsSet[h.round] = true; });
  });
  var xs = Object.keys(xsSet).sort(compareRounds);
  var series = IDX.teamsOrdered.map(function (t) {
    return {
      key: t.slug,
      label: t.short || t.name,
      color: teamColor(t.slug),
      dash: teamDash(t.slug),
      dim: !!selectedSlug && t.slug !== selectedSlug,
      points: (t.ladder_history || []).map(function (h) {
        return { x: h.round, y: h.position, avg: h.points_average };
      })
    };
  });
  return ladderLineCard({
    title: 'Ladder position by round',
    sub: 'One line per team, position 1 at the top.',
    xs: xs,
    series: series,
    empty: 'Positions appear once a round has been played.',
    tableCaption: 'Ladder position after each round.',
    valueText: function (pt) {
      return 'P' + pt.y + (pt.avg === null || pt.avg === undefined ? '' : ' · ' + num(pt.avg, 2));
    }
  });
}

/* Per-slot strength, over whatever slots this format has — two, four or six. The
 * predecessor iterated one club's four fixed slot keys, so a club running any
 * other format saw four empty rows and none of its real ones. */
function slotStrengthChart(t) {
  var rows = IDX.slots.map(function (slot) {
    var rec = (t.by_slot || {})[slot.key] || { won: 0, lost: 0, games_won: 0, games_lost: 0 };
    return {
      label: slot.key,
      value: rec.games_won, neg: rec.games_lost,
      valueText: num(rec.games_won), negText: num(rec.games_lost),
      color: PAL.divPos, negColor: PAL.divNeg,
      aria: slot.label + ': ' + rec.games_won + ' games won, ' + rec.games_lost +
        ' lost, record ' + rec.won + '–' + rec.lost,
      tip: {
        title: slotTitle(slot.key),
        rows: [
          { name: 'Games won', value: num(rec.games_won), kind: 'rect', color: PAL.divPos },
          { name: 'Games lost', value: num(rec.games_lost), kind: 'rect', color: PAL.divNeg },
          { name: 'Sets', value: rec.won + '–' + rec.lost }
        ]
      }
    };
  });
  return hbarCard({
    title: 'Strength by set slot',
    // The team's name in the file name, because the heading does not carry it: every
    // team's page has this chart, so without it two teams' tables download as the
    // same file and the second arrives as a copy of the first.
    tableCsv: t.name + ' strength by set slot',
    sub: 'Which of this format’s sets the team actually wins.',
    rows: rows,
    mode: 'diverging',
    axisLabel: 'Games',
    integer: true,
    legend: legendBar([
      { name: 'Games won', color: PAL.divPos, kind: 'rect' },
      { name: 'Games lost', color: PAL.divNeg, kind: 'rect' }
    ]),
    empty: 'No sets played yet.',
    tableHead: ['Slot', 'Sets won', 'Sets lost', 'Games won', 'Games lost'],
    tableRow: function (r) {
      var rec = (t.by_slot || {})[r.label] || {};
      return [slotTitle(r.label), num(rec.won), num(rec.lost),
        num(rec.games_won), num(rec.games_lost)];
    }
  });
}

function pointsByRoundChart(t, matches) {
  /* The rounds nobody played, kept in the chart and marked in it. The list handed in
   * is the one the ladder counted, which is right and is an answer to a different
   * question: `counts_for_ladder` is about a card no figure could use, and a washout
   * is not a data fault, so the exporter leaves that flag true and the round arrives
   * here. Nothing on its row used to say so — measured at 1100px, the row of the
   * washed-out night carried no bar, the words `not scored` in `--text-primary` at
   * weight 600, and an undimmed label, which made the one round that was never played
   * the loudest row in a chart of thirteen that were.
   *
   * Dropping it instead would have the chart agree with the tiles beside it and
   * misdescribe the season: the fifth round came round, and to nobody. */
  var unplayed = matches.filter(function (m) { return !countsAsPlayed(m); });
  var rows = matches.map(function (m) {
    var isHome = m.home === t.slug;
    var pts = isHome ? m.home_points : m.away_points;
    var mine = isHome ? m.home_games : m.away_games;
    var theirs = isHome ? m.away_games : m.home_games;
    var oppSlug = isHome ? m.away : m.home;
    var unscored = !countsAsPlayed(m);
    /* Two states, two wordings, and not one word for both: a round that was played
     * with no points recorded is something to go and chase, and a round nobody played
     * is not. Said once and handed to the eye, the screen reader and the table below
     * from here — the accessible name used to be built from `num(pts, 1)` of its own
     * and read "— points" where the screen said "not scored".
     *
     * "not played" rather than "counts for nothing", because under
     * `unplayed = "average"` it is credited, at this team's own average. What it
     * earned is the exporter's clause, under the plot. */
    var says = unscored ? 'not played'
      : (pts === null || pts === undefined ? 'not scored' : num(pts, 1));
    return {
      label: roundTick(m.round),
      value: pts === null || pts === undefined ? 0 : pts,
      valueText: says,
      dim: unscored,
      color: PAL.series[0],
      aria: unscored
        ? roundTitle(m.round) + ' v ' + teamName(oppSlug) + ': ' + says + ' — ' +
          m.status + ', ' + rules().unplayed_worth
        : roundTitle(m.round) + ': ' + says + ' points against ' + teamName(oppSlug),
      tip: {
        title: roundTitle(m.round) + ' v ' + teamShort(oppSlug),
        rows: [
          // No swatch on a round with no bar: a key beside "not played" points at
          // nothing drawn.
          { name: 'Points', value: says, kind: 'rect',
            color: unscored ? null : PAL.series[0] },
          { name: 'Games', value: num(mine) + '–' + num(theirs) },
          { name: 'Result', value: resultWord(m, t.slug) },
          { name: isHome ? 'At home' : 'Away', value: longDate(m.date) || '—' }
        ].concat(unscored
          ? [{ name: 'The round', value: rules().unplayed_worth }]
          : [])
      }
    };
  });
  return hbarCard({
    title: 'Points earned by round',
    tableCsv: t.name + ' points earned by round',
    // The active scheme, spelled out by export.py from the Ruleset itself. The
    // predecessor printed one club's numbers here as a literal.
    sub: rules().scoring_detail || 'Points as this competition scores them.',
    rows: rows,
    mode: 'single',
    axisLabel: 'Competition points',
    empty: 'This team has not played yet.',
    // One sentence for the rounds that were not played, because the five under the
    // match list below cannot cover them: every one of those is keyed on
    // `counts_for_ladder === false`, which a washout does not satisfy. The clause is
    // the exporter's, from scoring.unplayed_worth(), for the reason that function's
    // docstring gives — the page kept its own pair of sentences for this rule once and
    // they had already drifted from Python's and from each other.
    //
    // Which is also why our half of it is about the card and not about the round. This
    // said "so it has no points to plot" first, and the demo season built under
    // `unplayed = "average"` answers that: the same team goes from 55.3 points over 13
    // counted rounds to 59.553846 over 14, so the round it never played earned it 4.25.
    // The card is empty under either preset; only the clause knows what the round was
    // worth, and it is the only thing here that says.
    footer: unplayed.length ? el('p', { class: 'foot muted',
      style: { 'font-size': '12px', margin: '8px 0 0' },
      text: unplayed.map(function (m) { return roundTick(m.round); }).join(', ') +
        (unplayed.length === 1 ? ' was ' : ' were ') + 'not played, so there is nothing ' +
        (unplayed.length === 1 ? 'on its card ' : 'on their cards ') + 'to plot. ' +
        (unplayed.length === 1 ? 'The round itself is ' : 'Each round itself is ') +
        rules().unplayed_worth + '.' }) : null,
    tableHead: ['Round', 'Opponent', 'Result', 'Games', 'Points'],
    tableRow: function (r) {
      var m = matches[rows.indexOf(r)];
      var isHome = m.home === t.slug;
      // The points cell is the words the bar was labelled with, not a second reading
      // of the same field: the plot said `not scored` and this said `—` for one round
      // of the demo season, which is two accounts of one night in one card.
      return [roundTick(m.round), teamShort(isHome ? m.away : m.home),
        resultWord(m, t.slug),
        num(isHome ? m.home_games : m.away_games) + '–' +
        num(isHome ? m.away_games : m.home_games),
        r.valueText];
    }
  });
}

function resultWord(m, slug) {
  /* "Level", not "Draw", where the ladder has no draw to record: the scoreline is
   * level and the competition is set to decide every match, so this row is a result
   * nobody wrote down. Read off the reason the exporter wrote beside the row rather
   * than off the rules block, so this word and the sentence under the list below
   * cannot describe the same card two ways. */
  if (m.result === 'draw') return m.not_counted === 'level' ? 'Level' : 'Draw';
  if (m.result === 'none' || !m.result) return m.status || 'Not played';
  var won = (m.result === 'home' && m.home === slug) ||
            (m.result === 'away' && m.away === slug);
  return won ? 'Win' : 'Loss';
}

/* ============================================================== view: team == */

function renderTeam(main, slug) {
  var t = IDX.teamBySlug[slug];
  if (!t) { renderMissing(main, 'No team with the id "' + slug + '".'); return; }
  var matches = matchesOfTeam(t.slug);
  setPageName(t.name);

  main.appendChild(el('a', { class: 'crumb', href: '#/', text: '← Ladder' }));
  // Geometry inline as well as colour: this chip is bigger than a table chip and
  // sits in an <h1> the stylesheet does not know about.
  var chipStyle = keyStyle(teamColor(t.slug), teamDash(t.slug));
  chipStyle.display = 'inline-block';
  chipStyle.width = '12px';
  chipStyle.height = '12px';
  chipStyle['border-radius'] = '3px';
  chipStyle['margin-right'] = '8px';
  var h1 = el('h1', {}, [
    el('span', { class: 'chip', 'aria-hidden': 'true', style: chipStyle }),
    document.createTextNode(t.name)
  ]);
  var nameFlag = provenanceFlag('teams');
  if (nameFlag) h1.appendChild(nameFlag);

  // Draw code is display metadata and may be absent entirely: a code appears on
  // no scorecard, so this dashboard shows one only where a club declared it.
  var facts = el('p', {});
  if (t.code !== null && t.code !== undefined) {
    facts.appendChild(document.createTextNode('Draw code ' + t.code + ' · '));
  }
  facts.appendChild(document.createTextNode('captain ' + (t.captain || '—')));
  var captainFlag = t.captain ? provenanceFlag('captains') : null;
  if (captainFlag) {
    facts.appendChild(document.createTextNode(' '));
    facts.appendChild(captainFlag);
  }
  facts.appendChild(document.createTextNode(' · home court ' +
    (t.home_court === null || t.home_court === undefined ? '—' : t.home_court)));
  main.appendChild(el('div', { class: 'view-head' }, [h1, facts]));

  var pos = t.position === null || t.position === undefined ? '—' : String(t.position);
  main.appendChild(el('div', { class: 'grid tiles' }, [
    // The divisor, where it is not the played count in the tile beside this one.
    // Every other figure on this page can be checked against the row it came from;
    // the average could not be checked against anything.
    tile('Ladder position', pos, t.played
      ? num(t.points_average, 2) + ' points average' + (t.counted > t.played
        ? ' over ' + t.counted + ' counted' : '')
      : 'no matches counted'),
    tile('Record', t.won + '–' + t.lost + (t.drawn ? '–' + t.drawn : ''),
      t.played + ' ' + plural(t.played, 'match', 'matches') + ' played'),
    tile('Points', num(t.points, 1),
      (t.points_breakdown && t.points_breakdown.averaged_matches
        ? num(t.points_breakdown.earned, 1) + ' earned + ' +
          num(t.points_breakdown.averaged, 1) + ' averaged'
        : 'all earned on court')),
    tile('Games', num(t.games_won) + '–' + num(t.games_lost),
      signed(t.games_diff) + ' differential'),
    tile('Sets', num(t.sets_won) + '–' + num(t.sets_lost),
      (t.sets_won + t.sets_lost) ? pct(t.win_pct) + ' of sets won' : 'no sets yet'),
    tile('Streak', streakWords(t.streak),
      'best round ' + (t.best_round ? roundTick(t.best_round.round) + ' (' +
        num(t.best_round.points, 1) + ')' : '—'))
  ]));

  var splitBox = el('div', { class: 'card' }, [
    el('div', { class: 'table-wrap' }, [
      table(['Split', COL.matchesPlayed, COL.matchesWon, COL.matchesLost,
             COL.matchesDrawn, COL.gamesFor, COL.gamesAgainst, COL.gamesDiff], [
        ['Home', t.home.played, t.home.won, t.home.lost, t.home.drawn,
          t.home.games_won, t.home.games_lost, signed(t.home.games_won - t.home.games_lost)],
        ['Away', t.away.played, t.away.won, t.away.lost, t.away.drawn,
          t.away.games_won, t.away.games_lost, signed(t.away.games_won - t.away.games_lost)],
        ['Total', t.played, t.won, t.lost, t.drawn, t.games_won, t.games_lost, signed(t.games_diff)]
      ], { caption: 'Home and away split', csv: t.name + ' home and away' })
    ]),
    el('p', { class: 'foot muted', style: { 'font-size': '12px', margin: '8px 0 0' } }, [
      document.createTextNode('Form (oldest first): '), formRun(t.form)
    ])
  ]);

  // Every figure in the tiles and the split above is the regular season (F11) and
  // one card per fixture (F9), so the chart beside them is too: a bar for a match
  // no total includes would be a round the figure it sits under never counted. The
  // document says which matches those are, and why; this re-derives neither.
  var counted = matches.filter(function (m) { return m.counts_for_ladder !== false; });
  var missed = matches.filter(function (m) { return m.counts_for_ladder === false; });
  /* One equality per reason, never "none of the others". Counted by exclusion, the
   * finals bucket adopted every reason added after it: a level card in a grade that
   * cannot draw was counted here and explained to the club as a finals match, which
   * earns nothing for the ladder either. A reason this page has no sentence for prints
   * nothing, which is what the schema-version warning in the data health section is
   * for -- the only way to get one is an app.js older than its season.json. */
  var finals = missed.filter(function (m) { return m.not_counted === 'finals'; }).length;
  var repeats = missed.filter(function (m) { return m.not_counted === 'duplicate'; }).length;
  var clashes = missed.filter(function (m) { return m.not_counted === 'conflict'; }).length;
  var levels = missed.filter(function (m) { return m.not_counted === 'level'; }).length;
  var named = missed.filter(function (m) { return m.not_counted === 'names'; }).length;

  var charts = el('div', { class: 'grid charts', style: { 'margin-top': '12px' } });
  charts.appendChild(slotStrengthChart(t));
  charts.appendChild(pointsByRoundChart(t, counted));
  charts.appendChild(ladderHistoryChart(t.slug));
  charts.appendChild(pointsAverageChart(t.slug));

  main.appendChild(sectionTitle('Splits'));
  main.appendChild(splitBox);
  main.appendChild(charts);

  main.appendChild(sectionTitle('Players'));
  main.appendChild(el('div', { class: 'card' }, [teamPlayersTable(t)]));

  main.appendChild(sectionTitle('Head to head'));
  main.appendChild(el('div', { class: 'card' }, [teamH2HTable(t)]));

  main.appendChild(sectionTitle('Matches'));
  var box = el('div', { class: 'card' });
  if (!matches.length) {
    box.appendChild(el('p', { class: 'empty-note',
      text: 'No matches played yet. Anything in the draw is on the Matches page.' }));
  } else {
    matches.forEach(function (m) { box.appendChild(matchCard(m)); });
  }
  // Said where the difference shows: the list below is every card this team has,
  // and the record above counts three of four. One sentence per reason, because
  // "not in the figures above" is the only thing the five have in common -- a
  // final is the rules working, a repeated card is something to go and fix, and the
  // spare reading of a night named two ways is a night that *was* counted, from the
  // card beside it, with nobody credited for playing it.
  if (finals) {
    box.appendChild(el('p', { class: 'foot muted',
      style: { 'font-size': '12px', 'margin-top': '10px' },
      text: finals + ' finals ' + plural(finals, 'match', 'matches') + ' here ' +
        (finals === 1 ? 'earns' : 'earn') + ' no ladder points, so ' +
        (finals === 1 ? 'it is' : 'they are') + ' not in the record, points, ' +
        'games or form above. The players in ' + (finals === 1 ? 'it' : 'them') +
        ' are credited as usual.' }));
  }
  if (repeats) {
    box.appendChild(el('p', { class: 'foot muted',
      style: { 'font-size': '12px', 'margin-top': '10px' },
      text: repeats + ' ' + plural(repeats, 'card') + ' listed here ' +
        (repeats === 1 ? 'is a second copy' : 'are second copies') +
        ' of a night already above: the same fixture, the same result, exported ' +
        'twice. Each night is counted once, so the figures above are right and ' +
        (repeats === 1 ? 'the spare card' : 'the spare cards') +
        ' can be deleted from the data folder.' }));
  }
  if (clashes) {
    box.appendChild(el('p', { class: 'foot muted',
      style: { 'font-size': '12px', 'margin-top': '10px' },
      text: clashes + ' ' + plural(clashes, 'card') + ' listed here ' +
        (clashes === 1 ? 'shares its fixture' : 'share their fixture') +
        ' with another card that says something different about it. Which one is ' +
        'right is not something this page can work out, so the ' +
        plural(clashes, 'fixture') + ' ' + (clashes === 1 ? 'is' : 'are') +
        ' in none of the figures above until the wrong card is removed from the ' +
        'data folder. The data health section names the files.' }));
  }
  /* The fourth reason, and the only one the club's own settings can create: this
   * competition is marked as one that decides every match, so a card with no
   * winner on it is a result somebody did not write down. Both halves are said --
   * what the card shows and where the rule came from -- because either one of them
   * can be the thing that is wrong. */
  if (levels) {
    box.appendChild(el('p', { class: 'foot muted',
      style: { 'font-size': '12px', 'margin-top': '10px' },
      text: levels + ' ' + plural(levels, 'night') + ' listed here ended level, ' +
        'and this competition is set to have no draws (draws = false in ' +
        'config.toml), so either a tiebreak went unrecorded or a row was misread. ' +
        (levels === 1 ? 'It is' : 'They are') + ' in none of the figures above ' +
        'until the card is fixed, though the players in ' +
        (levels === 1 ? 'it' : 'them') + ' are credited as usual. The data health ' +
        'section names the ' + plural(levels, 'card') + '.' }));
  }
  /* The fifth reason, and the only one whose fixture *is* in the figures above: two
   * cards for one night agree about the result and disagree about who played, so the
   * result is settled and the appearances are not. Counted from the reading that was
   * not used — one such card per night, whichever of the two it turned out to be —
   * and worded around what the club has to do, which is the same as for a copy
   * except that until they do it somebody's season is short a match. */
  if (named) {
    box.appendChild(el('p', { class: 'foot muted',
      style: { 'font-size': '12px', 'margin-top': '10px' },
      text: named + ' ' + plural(named, 'card') + ' listed here ' +
        (named === 1 ? 'agrees' : 'agree') + ' with another card about the result ' +
        'and not about who played. The ' + plural(named, 'night') + ' ' +
        (named === 1 ? 'is' : 'are') + ' counted once in the record, points and ' +
        'games above — both cards say the same about the score — and ' +
        (named === 1 ? 'is' : 'are') + ' in nobody’s playing record, because ' +
        'crediting one card’s names would put a real person in a rubber the other ' +
        'card gives to somebody else. Everybody gets the night back when the card ' +
        'that is wrong leaves the data folder. The data health section names the ' +
        plural(named, 'file') + '.' }));
  }
  var upcoming = (S.fixtures || []).filter(function (f) {
    return !f.played && (f.home === t.slug || f.away === t.slug);
  });
  if (upcoming.length) {
    box.appendChild(el('p', { class: 'foot muted', style: { 'font-size': '12px', 'margin-top': '10px' },
      text: upcoming.length + ' ' + plural(upcoming.length, 'fixture') + ' still to play, next: ' +
        roundTitle(upcoming[0].round).toLowerCase() + ' v ' +
        teamShort(upcoming[0].home === t.slug ? upcoming[0].away : upcoming[0].home) +
        ' on ' + longDate(upcoming[0].date) }));
  }
  main.appendChild(box);
}

function teamPlayersTable(t) {
  var roster = (t.players || []).map(function (slug) { return IDX.playerBySlug[slug]; })
    .filter(Boolean);
  if (!roster.length) {
    return el('p', { class: 'empty-note', text: 'No registered players recorded for this team.' });
  }
  var rows = roster.map(function (p) {
    return {
      href: '#/player/' + p.slug,
      cells: [
        playerLink(p.slug),
        p.is_captain ? 'C' : { text: '—', cls: 'muted' },
        p.matches, p.sets_played,
        p.sets_won + '–' + p.sets_lost,
        p.win_pct === null || p.win_pct === undefined ? { text: '—', cls: 'muted' } : pct(p.win_pct),
        num(p.games_won), num(p.games_lost), signed(p.games_diff),
        p.contribution && p.contribution.share_pct !== null &&
          p.contribution.share_pct !== undefined
          ? pct(p.contribution.share_pct) : { text: '—', cls: 'muted' },
        p.rating && p.rating.singles !== null && p.rating.singles !== undefined
          ? num(p.rating.singles, 2) : { text: '—', cls: 'muted' },
        p.rating && p.rating.doubles !== null && p.rating.doubles !== undefined
          ? num(p.rating.doubles, 2) : { text: '—', cls: 'muted' }
      ]
    };
  });
  /* Where the Share column and the Games column disagree, and why: this team's own
   * `contribution_gap`, which is the games between them, the percentage they come to,
   * and a count of each thing that caused it.
   *
   * Read, not worked out here. This page used to walk the team's cards for all of it
   * and re-decide, in JavaScript, which rubbers pay a share -- the night, the slot's
   * shape, the roster, the finals, the pair of cards that name a night differently.
   * That is a second implementation of the arithmetic the Share cells are the first,
   * in a second language, and two of them is how the two come to disagree: the
   * exporter's scope moves and only one of them follows it.
   *
   * Defaulted once, here, because a document can be older than any of this: one built
   * before the record existed has no gap at all, and one built before the rubbers that
   * pay twice were counted has no count of them. Every read below is of a local that
   * is then zero, which is already the say-nothing case. Reading through a missing
   * record instead would lose the whole table over a sentence about it. */
  var gap = t.contribution_gap || {};
  var caption = 'Contribution of each registered player.';
  /* A night credited to nobody is absent from every row of this table, which is
   * where a reader meets it first: the M column is short for the whole squad and the
   * Share column no longer adds to a hundred. Said in this caption rather than left
   * to the Matches box at the bottom of the page, because this is the table the
   * figures are missing from. */
  var uncredited = gap.nights_not_credited || 0;
  if (uncredited) {
    caption += ' ' + uncredited + ' ' + plural(uncredited, 'night') + ' ' +
      (uncredited === 1 ? 'is' : 'are') + ' missing from every row: two cards for ' +
      (uncredited === 1 ? 'it' : 'them') + ' agree about the result and not about ' +
      'who played, so the ' + plural(uncredited, 'night') + ' ' +
      (uncredited === 1 ? 'counts' : 'count') + ' for the team and for nobody in it.';
  }
  /* The Share column is a share of the team's games won, and item 1.2 decided it may
   * come to less than the whole rather than credit a team's games to a person this
   * table has no row for. Which is honest and, unsaid, indistinguishable from a
   * broken sum: the demo prints 22.0%, 15.2%, 49.2% for one team and nothing on the
   * page accounts for the rest. So the caption accounts for it, in games first,
   * because games are what the shortfall is made of, and then in the document's own
   * percentage -- which is taken there from the games, not here from the cells the
   * page rounded to print them.
   *
   * Both directions, because a rubber with more names on it than the format has places
   * pays each of them a whole place's share, and then the column a reader adds up comes
   * to more than a hundred. One figure with a sign on it rather than two fields: it is
   * one subtraction, and what the reader's own addition shows is its net.
   *
   * Guarded on the games and not on that percentage, which rounds both ways: a team
   * four hundredths of a game short would be told it falls 0.0% short of a hundred,
   * and one demo team's complete column sums to 99.9%. */
  var off = gap.games || 0;
  if (off > 0) {
    caption += ' The Share column falls ' + pct(gap.share_pct) +
      ' short of 100%: ' + shareNum(off) + ' of the ' +
      shareNum(gap.team_games_won) + ' games the team won belong to no row above.';
  } else if (off < 0) {
    caption += ' The Share column runs ' + pct(-gap.share_pct) +
      ' past 100%: the rows above are credited with ' + shareNum(-off) +
      ' games more than the ' + shareNum(gap.team_games_won) + ' the team won.';
  }
  /* And then why, in the counts the same walk made while it was deciding what to
   * credit: a rubber with somebody in it this table has no row for, a rubber the card
   * is a name short of, and a rubber the card has a name too many on. Counted there per
   * rubber rather than per name, because a rubber is what a reader would go and look
   * at.
   *
   * Beside the sentence above rather than inside it, because the two are about different
   * things and one can be silent while the others are not. A season with a rubber short
   * a name and a rubber paying twice nets to zero -- measured: 94 of 94 games, a column
   * adding to exactly 100.0% -- and nesting these under the sum took all three reasons
   * down with it, leaving a page that said nothing about three rubbers a club can fix. */
  var strangers = gap.rubbers_with_outsiders || 0;
  var blanks = gap.rubbers_short_a_name || 0;
  var crowded = gap.rubbers_overfull || 0;
  /* One clause per cause, each on its own count: a season can have all three, and "or
   * else it must be the other one" is how a page states the cause it did not find. */
  if (strangers) {
    caption += ' ' + strangers + ' ' + plural(strangers, 'rubber') + ' ' +
      (strangers === 1 ? 'was' : 'were') +
      ' played by somebody with no row in this table — borrowed for the ' +
      'night, or registered to another squad.';
  }
  if (blanks) {
    caption += ' ' + blanks + ' ' + plural(blanks, 'rubber') + ' ' +
      (blanks === 1 ? 'is' : 'are') + ' short a name on the scorecard, so half of ' +
      (blanks === 1 ? 'it' : 'each') + ' belongs to nobody.';
  }
  if (crowded) {
    caption += ' ' + crowded + ' ' + plural(crowded, 'rubber') + ' ' +
      (crowded === 1 ? 'has' : 'have') + ' more names on the scorecard than places ' +
      'to play ' + (crowded === 1 ? 'it' : 'them') + ', so ' +
      (crowded === 1 ? 'its' : 'their') + ' games are credited more than once.';
  }
  /* Only where a rubber was found to point at. A night credited to nobody is listed
   * below as the pair of files it came from and not as a rubber, so on a season
   * whose whole shortfall is that night this would send a reader looking for
   * something the section does not list. */
  var found = strangers + blanks + crowded;
  if (found) {
    caption += ' The data health section names ' + (found === 1 ? 'it' : 'them') + '.';
  }
  return el('div', { class: 'table-wrap' }, [
    table(['Player', { label: 'C', title: 'Captain' },
      { label: 'M', title: 'Matches played' }, COL.setsPlayed, COL.setsWonLost,
      { label: 'Win %', title: 'Sets won as a percentage of completed sets' },
      COL.gamesFor, COL.gamesAgainst, COL.gamesDiff,
      { label: 'Share', title: 'Share of the team’s games won' },
      { label: 'S', title: 'Singles rating' }, { label: 'D', title: 'Doubles rating' }],
      rows, { caption: caption, csv: t.name + ' players' })
  ]);
}

function teamH2HTable(t) {
  var others = IDX.teamsOrdered.filter(function (o) { return o.slug !== t.slug; });
  var records = others.map(function (o) { return h2hFor(t.slug, o.slug); });
  var showUndecided = anyUndecidedMeetings(records);
  var rows = others.map(function (o, i) {
    var rec = records[i];
    var scheduled = (S.fixtures || []).filter(function (f) {
      return !f.played && ((f.home === t.slug && f.away === o.slug) ||
        (f.home === o.slug && f.away === t.slug));
    });
    return {
      href: '#/team/' + o.slug,
      cells: [
        teamLink(o, { long: true }),
        rec ? rec.played : 0,
        rec ? rec.won + '–' + rec.lost + (rec.drawn ? '–' + rec.drawn : '') : { text: '—', cls: 'muted' }
      ].concat(showUndecided ? [
        rec && rec.undecided ? rec.undecided : { text: '—', cls: 'muted' }
      ] : []).concat([
        rec ? num(rec.gamesFor) + '–' + num(rec.gamesAgainst) : { text: '—', cls: 'muted' },
        rec ? signed(rec.gamesFor - rec.gamesAgainst) : { text: '—', cls: 'muted' },
        scheduled.length ? scheduled.map(function (f) { return roundTick(f.round); }).join(', ')
          : { text: '—', cls: 'muted' }
      ])
    };
  });
  var caption = 'Record against each other team in this competition.';
  if (showUndecided) {
    caption += ' A meeting that reached no result counts in Met and in Games, and in neither side of W–L.';
  }
  caption += finalsMeetingsNote(finalsMeetings(records));
  return el('div', { class: 'table-wrap' }, [
    table(['Opponent', { label: 'Met', title: 'Meetings played' },
      COL.meetingsWonLost]
      .concat(showUndecided ? [{ label: 'No result',
        title: 'Meetings played that reached no result: their games are counted, ' +
          'their outcome is not' }] : [])
      .concat([
        { label: 'Games', title: 'Games for–against' }, COL.gamesDiff,
        { label: 'To come', title: 'Rounds where this fixture is still to be played' }]),
      rows, { caption: caption, csv: t.name + ' head to head' })
  ]);
}

/* ============================================================ view: player == */

function renderPlayer(main, slug) {
  var p = IDX.playerBySlug[slug];
  if (!p) { renderMissing(main, 'No player with the id "' + slug + '".'); return; }
  var team = teamOf(p.team);
  var sets = setsOfPlayer(p.slug);
  setPageName(p.name);

  main.appendChild(el('a', { class: 'crumb', href: '#/players', text: '← All players' }));
  var h1 = el('h1', { text: p.name + (p.is_captain ? ' (captain)' : '') });
  var nameFlag = provenanceFlag('players');
  if (nameFlag) h1.appendChild(nameFlag);
  var head = [
    h1,
    el('p', {}, [
      team ? teamLink(team, { long: true })
           : document.createTextNode(p.team_name || p.team || ''),
      document.createTextNode(' · rating ' +
        (p.rating && p.rating.singles !== null && p.rating.singles !== undefined
          ? num(p.rating.singles, 2) : '—') + ' singles / ' +
        (p.rating && p.rating.doubles !== null && p.rating.doubles !== undefined
          ? num(p.rating.doubles, 2) : '—') + ' doubles')
    ])
  ];
  var career = careersHref(p.slug);
  if (career) head.push(el('p', { class: 'career-link' }, [
    el('a', { href: career, text: 'Career across seasons →' })
  ]));
  main.appendChild(el('div', { class: 'view-head' }, head));
  if (meta().players) {
    main.appendChild(el('p', { class: 'muted', text: meta().players_detail }));
  }

  if (!p.matches) {
    main.appendChild(note('warning', 'No appearances yet',
      p.name + ' is registered for ' + (p.team_name || 'the team') +
      ' but has not played a set so far. Everything below will fill in once they do.'));
  }

  var streakWord = p.streak && p.streak.length
    ? p.streak.length + ' ' + (p.streak.type === 'W' ? 'won' : p.streak.type === 'L' ? 'lost' : 'drawn')
    : '—';
  main.appendChild(el('div', { class: 'grid tiles' }, [
    tile('Matches', String(p.matches), 'of ' + p.available + ' the team has played'),
    tile('Sets', p.sets_won + '–' + p.sets_lost, p.sets_played + ' played'),
    tile('Win rate', p.win_pct === null || p.win_pct === undefined ? '—' : pct(p.win_pct),
      'of completed sets'),
    tile('Games', num(p.games_won) + '–' + num(p.games_lost),
      signed(p.games_diff) + ' differential'),
    tile('Set streak', streakWord, 'longest win run ' + num(p.longest_win_streak)),
    // A doubles rubber's games are shared by the pair, so this figure can be a
    // half and is printed to one place. Rounding it to a whole number would show
    // a column of figures that does not add up to the total beside it.
    tile('Team share', p.contribution ? pct(p.contribution.share_pct) : '—',
      !p.team ? 'on no team’s roster'
        : p.contribution ? shareNum(p.contribution.games_won) + ' of ' +
          num(p.contribution.team_games_won) + ' team games won' : '—')
  ]));

  var charts = el('div', { class: 'grid charts', style: { 'margin-top': '12px' } });
  charts.appendChild(disciplineChart(p));
  charts.appendChild(opponentDiffChart(p));
  main.appendChild(charts);

  main.appendChild(sectionTitle('Where they play'));
  main.appendChild(el('div', { class: 'card' }, [slotAppearanceTable(p)]));

  main.appendChild(sectionTitle('Doubles partners'));
  main.appendChild(el('div', { class: 'card' }, [pairTable(p.partners, 'partner',
    'No doubles set played yet.', p.name)]));

  main.appendChild(sectionTitle('Opponents'));
  main.appendChild(el('div', { class: 'card' }, [pairTable(p.opponents, 'opponent',
    'No opponent faced yet.', p.name)]));

  if ((p.rating_history || []).length > 1) {
    main.appendChild(sectionTitle('Rating as printed each round'));
    main.appendChild(el('div', { class: 'card' }, [
      el('div', { class: 'table-wrap' }, [
        table(['Round', 'Singles', 'Doubles'], (p.rating_history || []).map(function (h) {
          return [roundTick(h.round),
            h.singles === null || h.singles === undefined ? { text: '—', cls: 'muted' } : num(h.singles, 2),
            h.doubles === null || h.doubles === undefined ? { text: '—', cls: 'muted' } : num(h.doubles, 2)];
        }), { caption: 'Ratings come off the scorecard, so a mid-season change shows up here.',
          csv: p.name + ' ratings' })
      ])
    ]));
  }

  main.appendChild(sectionTitle('Every set played'));
  var box = el('div', { class: 'card' });
  if (!sets.length) {
    box.appendChild(el('p', { class: 'empty-note', text: 'No sets recorded yet.' }));
  } else {
    var rows = sets.map(function (s) {
      var withNode = el('span');
      if (s.partners.length) {
        s.partners.forEach(function (pl, i) {
          if (i) withNode.appendChild(document.createTextNode(' + '));
          withNode.appendChild(playerLink(pl));
        });
      } else {
        withNode.appendChild(el('span', { class: 'muted', text: '—' }));
      }
      var vsNode = el('span');
      s.opponents.forEach(function (pl, i) {
        if (i) vsNode.appendChild(document.createTextNode(' + '));
        vsNode.appendChild(playerLink(pl));
      });
      return [
        roundTick(s.match.round),
        teamLinkBySlug(s.opponentTeam),
        slotLabel(s.set.slot),
        withNode,
        vsNode,
        num(s.gamesFor) + '–' + num(s.gamesAgainst),
        { text: s.decided ? (s.won ? 'Won' : 'Lost') : (s.set.status || 'unfinished'),
          cls: s.decided ? (s.won ? 'win' : 'lose') : 'muted' }
      ];
    });
    box.appendChild(el('div', { class: 'table-wrap' }, [
      table(['Round', 'Opponent team', 'Slot', 'With', 'Against', 'Games', 'Result'], rows,
        { caption: 'Derived from the set rows on each scorecard.', csv: p.name + ' sets' })
    ]));
  }
  main.appendChild(box);
}

function disciplineChart(p) {
  var defs = [
    { key: 'singles', label: 'Singles', rec: p.singles || {} },
    { key: 'doubles', label: 'Doubles', rec: p.doubles || {} }
  ];
  var rows = defs.map(function (d) {
    return {
      label: d.label,
      value: d.rec.games_won || 0, neg: d.rec.games_lost || 0,
      valueText: num(d.rec.games_won || 0), negText: num(d.rec.games_lost || 0),
      color: PAL.divPos, negColor: PAL.divNeg,
      aria: d.label + ': ' + (d.rec.games_won || 0) + ' games won, ' +
        (d.rec.games_lost || 0) + ' lost',
      tip: {
        title: d.label,
        rows: [
          { name: 'Games won', value: num(d.rec.games_won || 0), kind: 'rect', color: PAL.divPos },
          { name: 'Games lost', value: num(d.rec.games_lost || 0), kind: 'rect', color: PAL.divNeg },
          { name: 'Sets', value: (d.rec.won || 0) + '–' + (d.rec.lost || 0) },
          { name: 'Sets played', value: num(d.rec.played || 0) }
        ]
      }
    };
  });
  return hbarCard({
    title: 'Singles and doubles',
    tableCsv: p.name + ' singles and doubles',
    sub: 'Games won and lost in each discipline.',
    rows: rows,
    mode: 'diverging',
    axisLabel: 'Games',
    integer: true,
    legend: legendBar([
      { name: 'Games won', color: PAL.divPos, kind: 'rect' },
      { name: 'Games lost', color: PAL.divNeg, kind: 'rect' }
    ]),
    empty: 'No sets played yet.',
    tableHead: ['Discipline', 'Sets played', 'Won', 'Lost', 'Games won', 'Games lost'],
    tableRow: function (r) {
      var d = defs[rows.indexOf(r)];
      return [d.label, num(d.rec.played || 0), num(d.rec.won || 0), num(d.rec.lost || 0),
        num(d.rec.games_won || 0), num(d.rec.games_lost || 0)];
    }
  });
}

function opponentDiffChart(p) {
  var list = (p.opponents || []).slice().sort(function (a, b) {
    return (b.games_won - b.games_lost) - (a.games_won - a.games_lost);
  });
  var rows = list.map(function (o) {
    var diff = o.games_won - o.games_lost;
    return {
      label: o.name,
      value: diff > 0 ? diff : 0,
      neg: diff < 0 ? -diff : 0,
      valueText: diff > 0 ? signed(diff) : '',
      negText: diff < 0 ? signed(diff) : (diff === 0 ? '0' : ''),
      color: PAL.divPos, negColor: PAL.divNeg,
      href: '#/player/' + o.slug,
      aria: o.name + ': games differential ' + signed(diff),
      tip: {
        title: o.name,
        rows: [
          { name: 'Games differential', value: signed(diff), kind: 'rect',
            color: diff < 0 ? PAL.divNeg : PAL.divPos },
          { name: 'Games', value: num(o.games_won) + '–' + num(o.games_lost) },
          { name: 'Sets', value: o.won + '–' + o.lost },
          { name: 'Sets met', value: num(o.sets) }
        ]
      }
    };
  });
  return hbarCard({
    title: 'Games differential by opponent',
    tableCsv: p.name + ' games differential by opponent',
    sub: 'Above the line to the right, behind to the left.',
    rows: rows,
    mode: 'diverging',
    axisLabel: 'Games differential',
    integer: true,
    legend: legendBar([
      { name: 'Ahead', color: PAL.divPos, kind: 'rect' },
      { name: 'Behind', color: PAL.divNeg, kind: 'rect' }
    ]),
    empty: 'No opponent faced yet.',
    tableHead: ['Opponent', 'Sets met', COL.setsWonLost, 'Games won', 'Games lost',
      COL.gamesDiff],
    tableRow: function (r) {
      var o = list[rows.indexOf(r)];
      return [o.name, num(o.sets), o.won + '–' + o.lost, num(o.games_won),
        num(o.games_lost), signed(o.games_won - o.games_lost)];
    }
  });
}

/* The rate is the Python's, out of the sets decided in the slot as the Win rate
 * tile's is. Worked out here as won out of played, it counted a retired set against
 * the player in this table and nowhere else. A slot with none decided, and every
 * slot of a document written before the field, gets the muted dash, never a figure
 * of the page's own. */
function slotAppearanceTable(p) {
  var rows = IDX.slots.map(function (slot) {
    var rec = (p.by_slot || {})[slot.key] || { played: 0, won: 0 };
    return [slotTitle(slot.key), num(rec.played), num(rec.won),
      rec.win_pct === null || rec.win_pct === undefined
        ? { text: '—', cls: 'muted' } : pct(rec.win_pct)];
  });
  var out = el('div');
  out.appendChild(el('div', { class: 'table-wrap' }, [
    table(['Slot', 'Played', 'Won',
      { label: 'Win rate', title: 'Sets won as a percentage of completed sets' }], rows,
      { caption: 'Which of this format’s set slots the player is used in.',
        csv: p.name + ' set slots' })
  ]));
  // Matches, not sets: stats.py counts fill_in_appearances as the number of
  // distinct match keys the player appeared in off their own roster -- the same
  // keys it counts as match_ids -- so a player used in three rubbers of one
  // night is one appearance here, not three. The word said "set" until it was
  // checked against what the field holds.
  if (p.fill_in_appearances) {
    out.appendChild(note('warning', 'Fill-in', p.name + ' has played ' +
      p.fill_in_appearances + ' ' + plural(p.fill_in_appearances, 'match', 'matches') +
      ' outside their registered roster.'));
  }
  return out;
}

function pairTable(list, kind, emptyText, who) {
  if (!list || !list.length) return el('p', { class: 'empty-note', text: emptyText });
  var rows = list.slice().sort(function (a, b) { return b.sets - a.sets; }).map(function (r) {
    return {
      href: '#/player/' + r.slug,
      cells: [playerLink(r.slug), num(r.sets), r.won + '–' + r.lost,
        num(r.games_won), num(r.games_lost), signed(r.games_won - r.games_lost)]
    };
  });
  return el('div', { class: 'table-wrap' }, [
    table([kind === 'partner' ? 'Partner' : 'Opponent', COL.setsPlayed,
      COL.setsWonLost, COL.gamesFor, COL.gamesAgainst, COL.gamesDiff],
      rows, { caption: kind === 'partner'
        ? 'Record in doubles sets played together.'
        : 'Record in every set contested against this player.',
        // The player's name as well as the kind: a folder of downloads holding
        // `partners.csv` twice is a folder holding one of them under a number.
        csv: (who ? who + ' ' : '') + (kind === 'partner' ? 'partners' : 'opponents') })
  ]);
}

/* =========================================================== view: matches == */

/* The other cards of *m*'s night, as the exporter names them: by their places in the
 * season's `matches`, which the page never reorders. By place and not by `source`,
 * because a page that withholds names withholds every file name holding one, and
 * those all read the same: joined on them, every such card was every other's, and a
 * night of two cards showed four. A place the season does not hold is nobody. */
function otherCards(m) {
  var all = S.matches || [];
  return (m.other_cards || []).map(function (at) { return all[at]; })
    .filter(function (x) { return x !== undefined; });
}

/* The cards a list of keys names, in its order: a fixture's or a round's, as the
 * exporter lists them. Every card has a key and no two share one, so a card that
 * printed no match id is found, and so is each card of two that printed one. A key
 * the season does not hold is nobody, rather than a card drawn from `undefined`. */
function cardsOf(keys) {
  return (keys || []).map(function (key) { return IDX.matchByKey[key]; })
    .filter(function (x) { return x !== undefined; });
}

function renderMatches(main) {
  setPageName('Matches');
  // A derived draw describes only rounds that happened (F2), so promising "every
  // fixture in the draw" would read as a missing schedule rather than as an
  // absent one.
  var drawDeclared = meta().draw_source === 'declared';
  var head = el('div', { class: 'view-head' }, [
    el('h1', { text: 'Matches' }),
    el('p', { text: drawDeclared
      ? 'Every fixture in the declared draw. Played matches expand to their set ' +
        'scores; the rest are shown as scheduled.'
      : 'Every match played so far, read off the scorecards. No draw has been ' +
        'declared for this competition, so rounds appear here once they are played ' +
        'and nothing is claimed about a fixture that has not been.' })
  ]);
  var drawFlag = provenanceFlag('draw');
  if (drawFlag) head.appendChild(el('p', { class: 'sub muted' }, [drawFlag]));
  main.appendChild(head);

  var rounds = (S.rounds || []).slice().sort(function (a, b) {
    return compareRounds(a.number, b.number);
  });
  if (!rounds.length) {
    main.appendChild(el('p', { class: 'empty-note', text: 'No rounds recorded yet.' }));
    return;
  }
  // Every card this walk puts on the page, in the order it puts them there, which is
  // what the table of every rubber is built from.
  var shown = [];
  rounds.forEach(function (r) {
    var fixtures = (S.fixtures || []).filter(function (f) { return f.round === r.number; });
    // A round with no cards is either one the season has not reached or one it went
    // past leaving a card behind, and `r.played` cannot tell the two apart: it said
    // a round had not been played over rows for cards from that very round. The rows'
    // own answer settles the heading, so the two cannot disagree on one screen.
    var absent = fixtures.filter(function (f) { return f.card_missing; }).length;
    var state = r.played ? 'played' : (absent ? 'no cards yet' : 'not played yet');
    var head2 = el('div', { class: 'round-head' }, [
      // A finals round never contributes ladder points (F11), and it is the
      // round's own non-numeric label that says it is one.
      el('h3', { text: r.is_finals ? 'Finals — ' + roundTitle(r.number) : roundTitle(r.number) }),
      // The draw's own parenthetical against a date — "(mid-semester break)" —
      // which is the reader's answer to "why is this round in the holidays?".
      el('span', { class: 'date',
        text: longDate(r.date) + (r.note ? ' (' + r.note + ')' : '') }),
      el('span', { class: 'pill ' + (r.played ? 'good' : (absent ? 'warning' : '')) }, [
        el('span', { class: 'dot', 'aria-hidden': 'true' }),
        el('span', { text: state })
      ])
    ]);
    main.appendChild(head2);
    var box = el('div', { class: 'card' });
    if (!fixtures.length) {
      box.appendChild(el('p', { class: 'empty-note',
        text: r.is_finals
          ? 'Pairings are set by ladder position once the regular rounds are in.'
          : 'No fixtures listed for this round.' }));
    }
    if (r.is_finals) box.appendChild(finalsNote());
    // Every card of the fixture, in the order the build read them, which is the
    // order the finding and the data health section name them in: a clash's, so the
    // page does not choose a reading, and a copy's, which says it is one. The
    // exporter puts every card under exactly one fixture, so this is every card.
    fixtures.forEach(function (f) {
      var cards = cardsOf(f.keys);
      if (!cards.length) box.appendChild(scheduledRow(f));
      cards.forEach(function (card) {
        box.appendChild(matchCard(card));
        shown.push(card);
      });
    });
    (r.byes || []).forEach(function (slug) { box.appendChild(byeRow(slug)); });
    main.appendChild(box);
  });
  // Under the heading, where the reader arrives, rather than after a page as long as
  // the season. Built after the walk because it is the walk's list.
  var every = rubbersCard(shown);
  if (every) main.insertBefore(every, head.nextSibling);
}

/* Every rubber on the cards this page shows, in one table, so that a season of set
 * rows can leave the page the way every other table does. The set rows inside each
 * card have no Copy or CSV button of their own, deliberately, since a pair on every
 * card is two buttons to every three or four rows; and until this there was no other
 * way off the page for any of them: measured, 15 tables and 60 rows on the demo
 * season's matches page, none exporting.
 *
 * Kept off the screen until asked for, because it more than doubles the page:
 * measured at 1280px, the demo season's matches page is 1585px tall with it shut and
 * 3860px open. The button is `.table-toggle`, so it is off the printed sheet with the
 * charts' Values buttons, and the table prints only when it is open. */
function rubbersCard(matches) {
  var rows = [];
  matches.forEach(function (m) {
    (m.sets || []).forEach(function (st) { rows.push(rubberRow(m, st)); });
  });
  if (!rows.length) return null;
  var wrap = el('div', { class: 'table-wrap' }, [
    table(['Date', 'Round', 'Match id', 'Home', 'Away', 'Set', 'Home players',
           'Home games', 'Away games', 'Away players', 'Status',
           { label: 'Winner', title: 'The side the rubber went to, if it went to ' +
             'either' },
           { label: 'Night', title: 'What became of the night the rubber was ' +
             'played on' }],
      rows,
      { caption: 'Every rubber on the cards below, in the order they are listed.',
        csv: 'all rubbers' })
  ]);
  var box = el('div', { class: 'card all-rubbers', hidden: true }, [wrap]);
  var label = 'All ' + rows.length + ' ' + plural(rows.length, 'rubber') + ' in one table';
  return el('div', { class: 'rubbers' }, [
    el('p', { class: 'rubbers-toggle' }, [
      tableToggle(box, wrap, label, 'Hide the table of every rubber')
    ]),
    box
  ]);
}

/* One row of the season's table of rubbers: the night it belongs to, then the row the
 * night's own card prints -- through `setRow`, so the file and the card cannot come to
 * disagree about a rubber's players, games or status -- then the two things a row
 * loses when it leaves its card.
 *
 * Who won, because the card says it with a weight and a colour and a file keeps
 * neither. From the season, which decides it with the forfeit ticks and the
 * retirements in hand: measured, working it out from the games here would disagree
 * once in each season measured, giving a 5-4 rubber that was retired to the side that
 * was ahead. A 0-0 rubber nobody came for, which neither season had, would go to
 * nobody.
 *
 * And what became of the night, because a washout's rubbers were won and count for
 * nothing, and on the card only the sentence above them says so.
 *
 * The date is shown the long way, as every date on the page is, and leaves as ISO:
 * measured, LibreOffice read `Wed 19 Aug 2026` as text and `2026-08-19` as a date,
 * and a column of text sorts Fri before Wed. */
function rubberRow(m, st) {
  return [
    m.date ? el('span', { text: longDate(m.date), 'data-export': m.date }) : null,
    m.round, m.id || null, teamName(m.home), teamName(m.away)
  ].concat(setRow(m, st)).concat([
    st.winner === 'home' ? teamName(m.home)
      : st.winner === 'away' ? teamName(m.away) : null,
    upperFirst(uncountedWords(m) || m.status)
  ]);
}

/* What a finals night is worth, in one place. The matches list says it on the round
 * and a single match's page says it on the card, and two copies of one sentence is how
 * two pages of this dashboard came to word the same rule differently. */
function finalsNote() {
  return el('p', { class: 'foot muted', style: { 'font-size': '12px' },
    text: 'Finals do not contribute ladder points.' });
}

/* =========================================================== view: match == */

/* One night, at an address a club can send somebody.
 *
 * The id is the association's: Match Centre prints it on the scorecard, the reader
 * transcribes it and the exporter writes it through, so `#/match/4200003` still opens
 * the same night after a rebuild and after the next round lands. A position in the
 * read order would not -- the tree is walked in directory order, and a round arriving
 * in a folder that sorts earlier moves every id after it.
 *
 * Every card with that id, not the one the index kept: two cards can print one id --
 * the same night exported twice, or two clubs' readings of one fixture -- and
 * validate.py reports it as something to go and fix. An index by id holds whichever
 * was written last, so answering from it would show one card and hide the other
 * without saying so. A card that printed no id is addressed by nothing, which is also
 * what makes `#/match/` with nothing after it a page that was not found rather than
 * whichever card had no number. */
function matchesWithId(id) {
  return (S.matches || []).filter(function (m) { return !!m.id && m.id === id; });
}

function renderMatch(main, id) {
  var found = matchesWithId(id);
  if (!found.length) {
    // Nothing is named before this returns, so the sheet is still the club's: the
    // router has already put the season in the title. The id is said back to the
    // reader because it is the one thing they can check against the card in front
    // of them.
    renderMissing(main, id
      ? 'No card in this competition prints match id ' + id + '. Every card here ' +
        'shows its own, beside the date under its set scores.'
      : 'That address names no match. A match is addressed by the id printed on ' +
        'its scorecard.');
    return;
  }
  var m = found[0];
  var fixture = teamName(m.home) + ' v ' + teamName(m.away);
  setPageName(fixture);
  main.appendChild(el('a', { class: 'crumb', href: '#/matches', text: '← All matches' }));
  main.appendChild(el('div', { class: 'view-head' }, [
    el('h1', { text: fixture }),
    el('p', { text: dotted([roundTitle(m.round), longDate(m.date), matchIdWords(m)]) })
  ]));
  // Where a reader who arrived from a link goes next: this night is one of eighteen
  // each of these two teams played, and nothing else on the page leads to either.
  main.appendChild(el('p', { class: 'sub' }, [
    document.createTextNode('Both teams: '),
    teamLinkBySlug(m.home, { long: true }),
    document.createTextNode(' · '),
    teamLinkBySlug(m.away, { long: true })
  ]));
  var box = el('div', { class: 'card' });
  var round = (S.rounds || []).filter(function (r) { return r.number === m.round; })[0];
  if (round && round.is_finals) box.appendChild(finalsNote());
  if (found.length > 1) {
    box.appendChild(el('p', { class: 'foot muted', style: { 'font-size': '12px' },
      text: found.length + ' cards print this match id, so all of them are here. The ' +
            'data health section on the front page lists them as a fault to fix.' }));
  }
  // Open, because the card is the page: a shared link that arrives holding one row to
  // press has sent the reader somewhere they still have to look for the night.
  found.forEach(function (x) { box.appendChild(matchCard(x, { open: true, own: true })); });
  main.appendChild(box);
}

/* =========================================================== view: players == */

/* Its own list rather than the shared COL specs, because these columns carry a sort
 * key and a type as well -- but the same words, so a reader comparing this table with
 * a team's page is reading one wording of GF and not two. */
var PLAYER_COLS = [
  { key: 'name', label: 'Player', type: 'text' },
  { key: 'team', label: 'Team', type: 'text' },
  { key: 'matches', label: 'M', type: 'num', title: 'Matches played' },
  { key: 'sets_played', label: 'Sets', type: 'num', title: 'Sets played' },
  { key: 'sets_won', label: 'Won', type: 'num', title: 'Sets won' },
  { key: 'win_pct', label: 'Win %', type: 'num',
    title: 'Sets won as a percentage of completed sets' },
  { key: 'games_won', label: 'GF', type: 'num', title: 'Games for' },
  { key: 'games_lost', label: 'GA', type: 'num', title: 'Games against' },
  { key: 'games_diff', label: 'Diff', type: 'num', title: 'Games differential' },
  { key: 'singles_pct', label: 'S W-L', type: 'text', title: 'Singles record' },
  { key: 'doubles_pct', label: 'D W-L', type: 'text', title: 'Doubles record' },
  { key: 'share', label: 'Share', type: 'num',
    title: 'Share of the team’s games won' },
  { key: 'rating_s', label: 'S rating', type: 'num', title: 'Singles rating' },
  { key: 'rating_d', label: 'D rating', type: 'num', title: 'Doubles rating' }
];

function playerSortValue(p, key) {
  switch (key) {
    case 'name': return p.name;
    case 'team': return p.team_name || '';
    case 'win_pct': return p.win_pct === null || p.win_pct === undefined ? -1 : p.win_pct;
    case 'share': return p.contribution && p.contribution.share_pct !== null &&
      p.contribution.share_pct !== undefined ? p.contribution.share_pct : -1;
    case 'rating_s': return p.rating && p.rating.singles !== null &&
      p.rating.singles !== undefined ? p.rating.singles : -1;
    case 'rating_d': return p.rating && p.rating.doubles !== null &&
      p.rating.doubles !== undefined ? p.rating.doubles : -1;
    case 'singles_pct': return (p.singles || {}).won || 0;
    case 'doubles_pct': return (p.doubles || {}).won || 0;
    default: return p[key] === null || p[key] === undefined ? -1 : p[key];
  }
}

function renderPlayers(main) {
  setPageName('Players');
  var head = el('div', { class: 'view-head' }, [
    el('h1', { text: 'Players' }),
    el('p', { text: 'Every registered player, including those yet to play. ' +
      'Select a column heading to sort.' })
  ]);
  var nameFlag = provenanceFlag('players');
  if (nameFlag) head.appendChild(el('p', { class: 'sub muted' }, [nameFlag]));
  main.appendChild(head);

  var players = (S.players || []).slice();
  var wrap = el('div', { class: 'card' });
  var host = el('div');
  wrap.appendChild(host);
  main.appendChild(wrap);
  drawPlayerTable();

  var toCheck = playersToCheckCard(players);
  if (toCheck) {
    main.appendChild(sectionTitle('Players to check'));
    main.appendChild(toCheck);
  }

  main.appendChild(sectionTitle('Games differential'));
  var chartHost = el('div', { class: 'grid charts' });
  chartHost.appendChild(playerDiffChart(players));
  main.appendChild(chartHost);

  function drawPlayerTable() {
    clear(host);
    var sorted = players.slice().sort(function (a, b) {
      var va = playerSortValue(a, PLAYER_SORT.key), vb = playerSortValue(b, PLAYER_SORT.key);
      // Numeric, so that a withheld page's "Brindle Magpies #10" follows #9.
      var cmp = typeof va === 'string'
        ? String(va).localeCompare(String(vb), undefined, { numeric: true })
        : (va - vb);
      if (cmp === 0) return String(a.name).localeCompare(String(b.name), undefined, { numeric: true });
      return cmp * PLAYER_SORT.dir;
    });
    var head2 = PLAYER_COLS.map(function (c) {
      var active = PLAYER_SORT.key === c.key;
      return {
        label: c.label, sortable: true, title: c.title,
        arrow: active ? (PLAYER_SORT.dir === 1 ? '▲' : '▼') : '',
        ariaSort: active ? (PLAYER_SORT.dir === 1 ? 'ascending' : 'descending') : 'none',
        onSort: function () {
          if (PLAYER_SORT.key === c.key) PLAYER_SORT.dir = -PLAYER_SORT.dir;
          else PLAYER_SORT = { key: c.key, dir: c.type === 'text' ? 1 : -1 };
          drawPlayerTable();
        }
      };
    });
    var rows = sorted.map(function (p) {
      var s = p.singles || {}, d = p.doubles || {};
      return {
        href: '#/player/' + p.slug,
        cells: [
          playerLink(p.slug),
          teamLinkBySlug(p.team),
          p.matches, p.sets_played, p.sets_won,
          p.win_pct === null || p.win_pct === undefined ? { text: '—', cls: 'muted' } : pct(p.win_pct),
          num(p.games_won), num(p.games_lost), signed(p.games_diff),
          (s.won || 0) + '–' + (s.lost || 0),
          (d.won || 0) + '–' + (d.lost || 0),
          p.contribution && p.contribution.share_pct !== null &&
            p.contribution.share_pct !== undefined
            ? pct(p.contribution.share_pct) : { text: '—', cls: 'muted' },
          p.rating && p.rating.singles !== null && p.rating.singles !== undefined
            ? num(p.rating.singles, 2) : { text: '—', cls: 'muted' },
          p.rating && p.rating.doubles !== null && p.rating.doubles !== undefined
            ? num(p.rating.doubles, 2) : { text: '—', cls: 'muted' }
        ]
      };
    });
    host.appendChild(el('div', { class: 'table-wrap' }, [
      table(head2, rows, { caption: (S.players || []).length + ' registered ' +
        plural((S.players || []).length, 'player') + '. A dash means nothing recorded yet.',
        csv: 'players' })
    ]));
  }
}

/* Players who have appeared once or twice.
 *
 * Not a fault, and deliberately not written as one: one or two appearances is
 * what a fill-in, a late arrival or a long injury looks like, and all three are
 * ordinary. It is also the only visible symptom of one player recorded under two
 * spellings — the record splits in two, each half is internally consistent, and
 * the ladder is unaffected either way, so nothing else on the page would ever
 * mention it.
 *
 * Nothing is merged here. Whether two names are one person is a fact about
 * people, which this program has no access to and the reader of this page has;
 * so it lists the evidence and names the config section to declare the answer in.
 * The rounds column is the evidence that settles it: two spellings of one player
 * can never appear in the same round, and two real squad members usually do.
 *
 * Withheld until somebody has had enough matches available for "once or twice" to
 * single anyone out. Two rounds into a season every player qualifies and the list
 * says nothing at all. */
var CHECK_AT_MOST = 2;      // appearances, at or below which a player is listed
var CHECK_NEEDS = 4;        // matches available to the busiest player

function playersToCheck(players) {
  // The scale is taken across the whole competition rather than per player,
  // because a name misspelt in a set row belongs to no roster and so has no team
  // to count matches for — and that is the case this list most wants to show.
  var scale = players.reduce(function (n, p) {
    return Math.max(n, p.available || 0, p.matches || 0);
  }, 0);
  if (scale < CHECK_NEEDS) return [];
  return players.filter(function (p) {
    var played = p.matches || 0;
    return played >= 1 && played <= CHECK_AT_MOST;
  }).sort(function (a, b) {
    var byPlayed = (a.matches || 0) - (b.matches || 0);
    if (byPlayed) return byPlayed;
    return String(a.name).localeCompare(String(b.name), undefined, { numeric: true });
  });
}

/* Distinct rounds a player appeared in, in the order the matches are played. */
function roundsPlayedIn(slug) {
  var labels = [];
  setsOfPlayer(slug).forEach(function (s) {
    var label = s.match ? s.match.round : null;
    if (label === null || label === undefined) return;
    if (labels.indexOf(label) < 0) labels.push(label);
  });
  return labels.map(roundTick);
}

function playersToCheckCard(players) {
  var flagged = playersToCheck(players);
  if (!flagged.length) return null;
  var rows = flagged.map(function (p) {
    var rounds = roundsPlayedIn(p.slug);
    var played = p.matches || 0;
    var available = p.available || 0;
    return {
      href: '#/player/' + p.slug,
      cells: [
        playerLink(p.slug),
        teamLinkBySlug(p.team),
        available > played ? played + ' of ' + available : String(played),
        num(p.sets_played),
        rounds.length ? rounds.join(', ') : { text: '—', cls: 'muted' },
        !p.team
          ? 'on no team’s roster'
          : p.fill_in_appearances
            ? 'played outside their own roster'
            : { text: '—', cls: 'muted' }
      ]
    };
  });
  return el('div', { class: 'card' }, [
    el('p', { text: 'These players appear on one or two of the scorecards their ' +
      'team played. Usually that means a fill-in, a late arrival or an injury. ' +
      'It is also what one player looks like when two scorecards spell their ' +
      'name differently: the record splits in two, and neither half looks wrong ' +
      'on its own.' }),
    el('p', { class: 'muted', text: 'Read the rounds column first — two ' +
      'spellings of one player never share a round. If two rows are the same ' +
      'person, map one spelling onto the other under [aliases] in config.toml ' +
      'and run python3 build.py again. Nothing here is merged for you.' }),
    el('div', { class: 'table-wrap' }, [
      table(['Player', 'Team', 'Played', 'Sets', 'Rounds', 'Note'], rows,
        { caption: flagged.length + ' of ' + players.length + ' ' +
          plural(players.length, 'player') + ' have played at most ' +
          CHECK_AT_MOST + ' ' + plural(CHECK_AT_MOST, 'match', 'matches') +
          '. Everyone else is in the table above.', csv: 'players to check' })
    ])
  ]);
}

function playerDiffChart(players) {
  var list = players.filter(function (p) { return p.sets_played > 0; })
    .slice().sort(function (a, b) { return b.games_diff - a.games_diff; });
  var rows = list.map(function (p) {
    var diff = p.games_diff;
    return {
      label: p.name,
      value: diff > 0 ? diff : 0,
      neg: diff < 0 ? -diff : 0,
      valueText: diff > 0 ? signed(diff) : '',
      negText: diff < 0 ? signed(diff) : (diff === 0 ? '0' : ''),
      color: PAL.divPos, negColor: PAL.divNeg,
      href: '#/player/' + p.slug,
      aria: p.name + ': games differential ' + signed(diff),
      tip: {
        title: p.name,
        rows: [
          { name: 'Games differential', value: signed(diff), kind: 'rect',
            color: diff < 0 ? PAL.divNeg : PAL.divPos },
          { name: 'Games', value: num(p.games_won) + '–' + num(p.games_lost) },
          { name: 'Sets', value: p.sets_won + '–' + p.sets_lost },
          { name: 'Team', value: p.team_name || '' }
        ]
      }
    };
  });
  return hbarCard({
    title: 'Games differential by player',
    sub: 'Everyone who has played a set, best to worst.',
    rows: rows,
    mode: 'diverging',
    axisLabel: 'Games won minus games lost',
    integer: true,
    legend: legendBar([
      { name: 'Ahead', color: PAL.divPos, kind: 'rect' },
      { name: 'Behind', color: PAL.divNeg, kind: 'rect' }
    ]),
    empty: 'Nobody has played a set yet.',
    tableHead: ['Player', 'Team', 'Games won', 'Games lost', COL.gamesDiff],
    tableRow: function (r) {
      var p = list[rows.indexOf(r)];
      return [p.name, p.team_name || '', num(p.games_won), num(p.games_lost), signed(p.games_diff)];
    }
  });
}

/* ============================================================ rules banner == */

/* The scoring scheme, the set rule and the match format each silently reorder
 * the ladder when they are wrong, and none of them can be
 * read off a scoreline. So all three are named here, permanently, with the detail
 * strings export.py spells out from the Ruleset and the SetRule — a club running
 * the wrong preset can recognise it without reading any code.
 *
 * In two parts, and the split is the whole of item 3.22. The names are what a club
 * recognises its competition by, so they are always on screen; the sentence that
 * explains each one, and the sentence saying which config keys to change, are what
 * a reader needs once. Together they cost 250px of a 640px phone — 39% of the
 * screen, and the dashboard itself started 635px down, which is 5px of it visible
 * before a scroll. Measured with `/tmp/bannerh.py`: the explanations are what wrap
 * each line to two, four lines of them, and the closing advisory is three lines on
 * its own. Behind a disclosure they cost one line, and nothing is hidden: a
 * `<details>` is a control a reader can open, the print sheet forces it open (the
 * banner is a caveat, and paper outlives the page), and hiding the banner itself on
 * a phone — the cheap way to buy the same height — is what the tests for 3.12
 * forbid. */
function renderRulesBanner() {
  var main = document.getElementById('view');
  var host = hook('rules-banner', 'rules-banner', main ? main.parentNode : document.body, main);
  if (!host) return;
  var r = rules();
  var pairs = [
    ['Scoring', r.scoring, r.scoring_detail],
    ['Sets', r.set_rule, r.set_rule_detail],
    /* Spelled out because it is invisible in the ladder's order and plain in its
     * points column: crediting every team its own average cannot move any team's
     * average. A club comparing totals with its association has no other way to
     * see which convention produced the number in front of it. */
    ['Unplayed', r.unplayed, r.unplayed_detail],
    /* Empty for almost every club, and the loop below drops a pair with nothing in
     * it, so this row appears only where draws = false is set. There it is the
     * reason a night is missing from the ladder on this very page, which is not
     * something a reader should have to find in the terminal output of whoever ran
     * the build. */
    ['Draws', r.draws, r.draws_detail],
    ['Format', r.format, r.format_detail]
  ];
  var more = el('details', { class: 'rules-more' });
  more.appendChild(el('summary', { text: 'What these mean' }));
  pairs.forEach(function (bits) {
    if (!bits[1] && !bits[2]) return;
    var span = el('span', {}, [el('strong', { text: bits[0] + ': ' })]);
    /* The explanation on the line itself where there is no name to put there. A
     * label with nothing after its colon is worse than a long line, and it is the
     * shape `draws` would take if the exporter ever wrote one of its pair without
     * the other. */
    span.appendChild(document.createTextNode(bits[1] || bits[2]));
    host.appendChild(span);
    if (bits[1] && bits[2]) {
      /* Labelled again inside, because an explanation reached by opening something
       * is read on its own: "a bye or a washout is scored as nothing" has to say
       * which of the five rules above it is about. */
      more.appendChild(el('p', { class: 'rules-detail' }, [
        el('strong', { text: bits[0] + ': ' }),
        document.createTextNode(bits[2])
      ]));
    }
  });
  var slotNames = IDX.slots.map(function (s) { return s.key + ' ' + s.label; });
  if (slotNames.length) {
    host.appendChild(el('span', {}, [
      el('strong', { text: 'Sets played: ' }),
      document.createTextNode(slotNames.join(', '))
    ]));
  }
  var formatFlag = provenanceFlag('format');
  if (formatFlag) host.appendChild(formatFlag);
  more.appendChild(el('p', { class: 'rules-detail muted',
    text: 'These decide every points and ladder number on this page. ' +
      'If they are not this competition’s rules, set scoring, sets, unplayed, draws and format in config.toml.' }));
  host.appendChild(more);
}

/* ============================================================ data health == */

/* n refusals, apart: the folders among them are the build's count, and never a
 * card, because how many cards a folder it could not list holds is what nobody
 * knows. A document without the count was built before a folder was refused. */
function refusedApart(n) {
  var folders = meta().folders_unlisted || 0;
  return { cards: n - folders, folders: folders };
}

/* The same n in the report's words, cards and then folders, one clause each for
 * inWords to join into the sentence around them. */
function refusedWords(n, card, unscored) {
  var apart = refusedApart(n), said = [];
  if (apart.cards) said.push(apart.cards + ' ' + plural(apart.cards, card) + unscored);
  if (apart.folders) {
    said.push(apart.folders + ' ' + plural(apart.folders, 'folder') + ' not opened');
  }
  return said;
}

/* items as a sentence lists them, "a", "a and b", "a, b and c": the build's
 * scope.in_words, so the page and the report join the same count alike. */
function inWords(items) {
  if (items.length < 2) return items.join('');
  return items.slice(0, -1).join(', ') + ' and ' + items[items.length - 1];
}

function renderHealth() {
  var host = document.getElementById('health');
  if (!host) return;
  clear(host);
  var m = meta();
  var v = (S && S.validation) || { ok: true, errors: [], warnings: [] };
  var errors = v.errors || [], warnings = v.warnings || [], rejected = v.rejected || [];
  /* The competition's own errors, counted by the build: every document carries
   * every refused card as an F4 error, and those are counted once, by the
   * "seen and not scored" pill. A document written before the count existed
   * has every error counted, which states a refusal twice and hides nothing. */
  var own = typeof v.own_errors === 'number' ? v.own_errors : errors.length;
  var row = el('div', { class: 'health-row' });

  var state = own ? 'critical' : warnings.length ? 'warning' : 'good';
  var label = own
    ? own + ' validation ' + plural(own, 'error')
    : warnings.length
      ? 'Validated with ' + warnings.length + ' ' + plural(warnings.length, 'warning')
      : rejected.length ? 'No errors in the cards scored' : 'Validated clean';
  row.appendChild(el('span', { class: 'pill ' + state }, [
    el('span', { class: 'dot', 'aria-hidden': 'true' }),
    el('span', { text: (own ? '✕ ' : warnings.length ? '! ' : '✓ ') + label })
  ]));
  // Every PDF found is scored or named (F4), so the count is stated as a
  // fraction rather than as a total: "6 cards read" cannot distinguish six from
  // seven-minus-one.
  row.appendChild(el('span', { class: 'pill' }, [
    el('span', { class: 'dot', 'aria-hidden': 'true' }),
    el('span', { text: (m.cards_scored || 0) + ' of ' + (m.cards_seen || 0) +
      ' ' + plural(m.cards_seen || 0, 'card') + ' scored' })
  ]));
  // Neutral: a page that withholds names is doing what it was asked to.
  if (m.players) {
    row.appendChild(el('span', { class: 'pill' }, [
      el('span', { class: 'dot', 'aria-hidden': 'true' }),
      el('span', { text: 'names withheld' })
    ]));
  }
  if (rejected.length) {
    row.appendChild(el('span', { class: 'pill critical' }, [
      el('span', { class: 'dot', 'aria-hidden': 'true' }),
      el('span', { text: '✕ ' + inWords(refusedWords(rejected.length, 'card',
        ' seen and not scored')) })
    ]));
  }
  var mismatched = (S.matches || []).filter(function (x) { return (x.warnings || []).length; }).length;
  if (mismatched) {
    row.appendChild(el('span', { class: 'pill warning' }, [
      el('span', { class: 'dot', 'aria-hidden': 'true' }),
      el('span', { text: mismatched + ' ' + plural(mismatched, 'match', 'matches') + ' with parse notes' })
    ]));
  }
  if (!m.ladder_complete) {
    row.appendChild(el('span', { class: 'pill warning' }, [
      el('span', { class: 'dot', 'aria-hidden': 'true' }),
      el('span', { text: 'ladder in progress' })
    ]));
  }
  row.appendChild(el('span', { class: 'muted', text: SOURCE_NOTE }));
  host.appendChild(row);

  renderRejected(host, rejected);

  var provenance = provenanceBlock();
  if (provenance) host.appendChild(provenance);
  /* On every page, because the panel is. The second sentence is what makes a
   * message's config advice readable: it names a label, and the club finds the
   * player it stands for in the report the build printed. */
  if (m.players) {
    host.appendChild(el('p', { class: 'muted', text: 'Players: ' + m.players_detail +
      '. Messages on this page use the same labels. The report the build printed ' +
      'names each player, for whoever keeps config.toml.' }));
  }

  /* Two ways to be out of step, and both are said out loud. A document naming no
   * format at all was written before the marker existed, which makes it older
   * than format 1 rather than current -- the `S.format !== undefined &&` guard
   * this replaced read that silence as agreement, and left a genuinely old
   * document to render blanks with nothing on the page to explain them. */
  if (S.format !== SEASON_FORMAT) {
    host.appendChild(note('warning', 'Schema version',
      'This page reads season document format ' + SEASON_FORMAT + ' and the data ' +
      (S.format === undefined
        ? 'names none, so it was built before this page started recording one'
        : 'says ' + S.format) +
      '. The season is not shown, as some of its values may be missing or mean ' +
      'something else. Re-run ' +
      'python3 build.py to regenerate the dashboard beside its data.'));
  }

  if (errors.length || warnings.length) {
    var det = el('details');
    /* Counted as the pill counts. The list's other errors are the refused cards
     * every document carries, named apart, as the report's own line names them,
     * and they do not open it: the "seen and not scored" list already shows each.
     * The errors and the warnings are one clause, so a page with no refusal reads
     * as it always did. */
    var refused = errors.length - own;
    if (own) det.setAttribute('open', '');
    det.appendChild(el('summary', { text: 'Data health detail (' + inWords([own + ' ' +
      plural(own, 'error') + ', ' + warnings.length + ' ' +
      plural(warnings.length, 'warning')].concat(refused ?
        refusedWords(refused, 'card', ' not scored') : [])) + ')' }));
    var list = el('ul');
    errors.concat(warnings).forEach(function (item, i) {
      var isError = i < errors.length;
      var li = el('li');
      li.appendChild(el('strong', { text: (isError ? 'Error' : 'Warning') +
        (item.rule ? ' · ' + item.rule : '') + ': ' }));
      li.appendChild(document.createTextNode(item.message || String(item)));
      // Not where the message already begins with it: the build decided which.
      if (item.subject && !item.subject_said) li.appendChild(el('code', { text: ' [' + item.subject + ']' }));
      if (item.detail) li.appendChild(el('span', { class: 'muted', text: ' ' + item.detail }));
      list.appendChild(li);
    });
    det.appendChild(list);
    host.appendChild(det);
  }
}

/* F4, given the loudest treatment on the page. A rejected card is a scorecard
 * that exists, was found, and produced no result: the ladder below it is short a
 * match and looks exactly as plausible as a complete one. That is worse than an
 * error about data we do have, so it is listed by path, never folded into a
 * count, and never behind a closed disclosure. */
function renderRejected(host, rejected) {
  if (!rejected || !rejected.length) return;
  var box = hook('rejected', 'rejected-list', host);
  if (!box) return;
  var apart = refusedApart(rejected.length), said = [];
  if (apart.cards) said.push(
    'Each file below was found under the data folder and produced no result, so ' +
    'every number on this page is missing whatever it contained. Run ' +
    'python3 tools/doctor.py on a path to see why it was refused.');
  if (apart.folders) said.push('Each folder below could not be opened, so every ' +
    'card in it is missing from every number on this page. The account that ' +
    'runs the build needs permission to read it.');
  box.appendChild(note('critical', inWords(refusedWords(rejected.length, 'scorecard',
    ' seen and not scored')), said.join(' ')));
  rejected.forEach(function (r) {
    var item = el('p', { class: 'rejected-item' });
    item.appendChild(el('code', { text: r.path + (r.page ? ' page ' + (r.page + 1) : '') }));
    item.appendChild(document.createTextNode(' — ' + (r.reason || 'refused')));
    if (r.detail) item.appendChild(el('span', { class: 'muted', text: ' (' + r.detail + ')' }));
    box.appendChild(item);
  });
}

/* ================================================================= router == */

function renderMissing(main, message) {
  main.appendChild(el('div', { class: 'view-head' }, [
    el('h1', { text: 'Not found' }),
    el('p', { text: message })
  ]));
  main.appendChild(el('div', { class: 'card' }, [
    el('p', { class: 'empty-note', text: 'Try the ladder, the matches or the player list.' })
  ]));
}

/* What the page draws in place of any view when the document is of another format
 * than this page's, under the Schema version note (4.68). Measured on the demo's
 * document rewritten to older shapes, every view drawn anyway read a missing key
 * as a verdict: at format 2 the match list said "scheduled" over 15 nights with
 * cards, and at format 1 a team's page said "counts for nothing" 5 times. A later
 * format may have changed anything. So nothing of the season is drawn, and this
 * says so, pointing at the note rather than saying its numbers and remedy twice. */
function renderWithheld(main) {
  main.appendChild(el('div', { class: 'view-head' }, [
    el('h1', { text: 'Season not shown' }),
    el('p', { text: 'The Schema version note above says why, and what to run to ' +
      'show it again.' })
  ]));
}

/* `decodeURIComponent` throws on a malformed escape, and the router runs inside
 * render() with nothing above it to catch: `#/team/%` took the whole dashboard blank,
 * and a fragment gets typed, truncated in an email and pasted back by hand. A slug
 * that will not decode is not a slug any team has, so handing the raw text on reaches
 * the same not-found page by the route that does not throw. */
function decodeOr(text) {
  try {
    return decodeURIComponent(text);
  } catch (e) {
    return text;
  }
}

function parseRoute() {
  var hash = location.hash.replace(/^#\/?/, '');
  var parts = hash.split('/').filter(function (s) { return s.length; });
  if (!parts.length) return { view: 'overview' };
  if (parts[0] === 'team') return { view: 'team', slug: decodeOr(parts[1] || '') };
  if (parts[0] === 'player') return { view: 'player', slug: decodeOr(parts[1] || '') };
  if (parts[0] === 'match') return { view: 'match', id: decodeOr(parts[1] || '') };
  if (parts[0] === 'matches') return { view: 'matches' };
  if (parts[0] === 'players') return { view: 'players' };
  return { view: 'overview' };
}

function markTabs(route) {
  var map = { overview: '#/', matches: '#/matches', players: '#/players' };
  var want = map[route.view] || '';
  Array.prototype.forEach.call(document.querySelectorAll('.tabs a'), function (a) {
    if (a.getAttribute('href') === want) a.setAttribute('aria-current', 'page');
    else a.removeAttribute('aria-current');
  });
  var picker = document.getElementById('entity-picker');
  if (picker) {
    picker.value = route.view === 'team' ? 'team:' + route.slug
      : route.view === 'player' ? 'player:' + route.slug : '';
    // Setting .value fires no event, so the button beside it would otherwise keep
    // whatever state the reader's last choice left it in.
    markJumpReady(picker);
  }
}

/* What every printed sheet says, and what the browser tab says.
 *
 * The topbar prints, so sheet one of a printout names the club, the competition and the
 * season. Sheets two onwards named nothing: 23 of 28 sheets across the five routes were
 * numbers with nothing saying whose. CSS cannot repeat the bar in Chrome -- `position:
 * fixed` does repeat it, into the page area where the content starts, and `@page` margins
 * move the bar and the content down together -- but the browser's own print header
 * repeats `document.title` on every sheet, and is ticked by default in the print dialog.
 *
 * So the title is the thing that has to carry the identity, and there is one of it. The
 * five views used to write their own -- `Matches — ` + the competition, and four more
 * wordings of that -- which is how all five came to be missing the club and the season:
 * there was nowhere to add them once. The order matters because Chrome ellipsises the
 * tail at about 110 characters of 8pt: the page's own name leads, since nothing else on
 * the sheet repeats it, and the season is last, since every match sheet is covered in
 * dates. Empty parts are dropped rather than joined, by dotted(), or a season file with
 * no club in it prints its separators with nothing between them.
 */
function setPageName(what) {
  var m = meta();
  document.title = dotted([what, m.label, m.club, m.season_label]);
}

function render() {
  var main = document.getElementById('view');
  CHARTS = [];
  clear(main);
  hideTip();
  readPalette();
  var route = parseRoute();
  // The season's own name, before the dispatch: the overview's title is this and
  // nothing more, and it is also what a mistyped team or player slug is left with,
  // since those two answer with renderMissing() and return before naming anything.
  setPageName('');
  if (S.format !== SEASON_FORMAT) renderWithheld(main);
  else if (route.view === 'team') renderTeam(main, route.slug);
  else if (route.view === 'player') renderPlayer(main, route.slug);
  else if (route.view === 'match') renderMatch(main, route.id);
  else if (route.view === 'matches') renderMatches(main);
  else if (route.view === 'players') renderPlayers(main);
  else renderOverview(main);
  markTabs(route);
  drawCharts();
  // Last, and here rather than inside each view: the wrappers are in the document
  // by now, which is the first moment any of them can be measured.
  markScrollRegions();
  markStickyOffset();
  window.scrollTo(0, 0);
}

function drawCharts() {
  CHARTS.forEach(function (c) {
    var w = c.plot.clientWidth || c.figure.clientWidth || 600;
    c.draw(c.plot, Math.max(240, Math.floor(w)));
  });
}

var resizeTimer = null;
function onResize() {
  if (resizeTimer) clearTimeout(resizeTimer);
  resizeTimer = setTimeout(function () {
    readPalette();
    drawCharts();
    // The window is the thing a table is too wide for, so the answer changes here
    // in both directions: a tab stop appears on the way narrow and goes again on
    // the way back. The bar's height changes with the width too, and below the
    // narrow breakpoint it stops sticking altogether.
    markScrollRegions();
    markStickyOffset();
  }, 150);
}

/* What a scroll has to clear. The topbar is `position: sticky; top: 0`, so the page
 * scrolls underneath it -- and every scroll the browser performs itself puts its
 * target at the top of the page, which is exactly where the bar is. Measured at 360
 * wide: Shift+Tab onto a control above the screen landed it 186px behind the bar, an
 * anchor jump the same, and at 1280 an anchor jump lands 82px behind it. WCAG 2.2
 * 2.4.11 (Focus Not Obscured (Minimum), Level AA) names the focus half of it; Find in
 * page and every future in-page link are the same scroll.
 *
 * `scroll-padding-top` on the scrollport is the fix in one property: it tells the
 * browser that the top of the page is not the top of the page, and the browser applies
 * it to the scrolls it does on the reader's behalf without any of them being listed
 * here.
 *
 * Measured rather than written down, because there is no one right number: the bar is
 * 82px at 1280, 128px at 700 where its three rows wrap to two, 187px on a phone, and
 * taller again with a long club name in it. And the question asked is `position`, not
 * the width, so the stylesheet keeps the single decision about where the bar stops
 * sticking: below that width it scrolls away like anything else, and a page still
 * holding 187px clear would open every jump with a gap that nothing is in.
 */
function markStickyOffset() {
  var bar = document.querySelector('.topbar');
  if (!bar) return;
  var stuck = getComputedStyle(bar).position === 'sticky';
  // Rounded up: half a pixel of bar left over is still bar.
  document.documentElement.style.scrollPaddingTop =
    stuck ? Math.ceil(bar.getBoundingClientRect().height) + 'px' : '';
}

/* ================================================================== theme == */

/* The storage key is per club, not per program: two clubs' dashboards can end up
 * served from one origin, and the predecessor's single hardcoded key meant either
 * would silently overwrite the other's choice. site.py derives the same key for
 * the picker page from the club name, so the two agree and one choice covers the
 * whole site. */
function slugifyName(text) {
  var s = String(text === null || text === undefined ? '' : text);
  if (typeof s.normalize === 'function') {
    // Strip combining marks so an accented club name slugs the way slugs.py
    // slugs it. Guarded because String.prototype.normalize is not ES5.
    s = s.normalize('NFKD').replace(/[̀-ͯ]/g, '');
  }
  // Lowered after, as slugs.py folds: NFKD makes a capital of a superscript or a
  // mathematical letter, which lowering first would leave to be dropped.
  return s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
}

function themeKey() {
  var slug = slugifyName(meta().club);
  return slug ? slug + '-theme' : 'matchcentre-theme';
}

function applyTheme(mode) {
  if (mode === 'system') document.documentElement.removeAttribute('data-theme');
  else document.documentElement.setAttribute('data-theme', mode);
  try { localStorage.setItem(THEME_KEY, mode); } catch (e) { /* file:// or private mode */ }
  var btn = document.getElementById('theme-toggle');
  if (btn) {
    var label = mode === 'system' ? 'Theme: system' : mode === 'dark' ? 'Theme: dark' : 'Theme: light';
    btn.textContent = label;
    btn.setAttribute('aria-label', label + '. Select to change.');
  }
  readPalette();
  drawCharts();
}

function initTheme() {
  THEME_KEY = themeKey();
  var stored = null;
  try { stored = localStorage.getItem(THEME_KEY); } catch (e) { stored = null; }
  var order = ['system', 'light', 'dark'];
  var current = order.indexOf(stored) >= 0 ? stored : 'system';
  applyTheme(current);
  var btn = document.getElementById('theme-toggle');
  if (btn) {
    btn.addEventListener('click', function () {
      current = order[(order.indexOf(current) + 1) % order.length];
      applyTheme(current);
    });
  }
  if (window.matchMedia) {
    var mq = window.matchMedia('(prefers-color-scheme: dark)');
    var handler = function () { if (current === 'system') { readPalette(); drawCharts(); } };
    if (mq.addEventListener) mq.addEventListener('change', handler);
    else if (mq.addListener) mq.addListener(handler);
  }
}

/* ============================================================ entity picker */

function fillPicker() {
  var picker = document.getElementById('entity-picker');
  if (!picker) return;
  clear(picker);
  picker.appendChild(el('option', { value: '', text: 'Jump to a team or player…' }));
  var gt = el('optgroup', { label: 'Teams' });
  IDX.ladder.forEach(function (t) {
    gt.appendChild(el('option', { value: 'team:' + t.slug, text: t.name }));
  });
  picker.appendChild(gt);
  var gp = el('optgroup', { label: 'Players' });
  (S.players || []).slice().sort(function (a, b) {
    return String(a.name).localeCompare(String(b.name), undefined, { numeric: true });
  }).forEach(function (p) {
    gp.appendChild(el('option', { value: 'player:' + p.slug,
      text: p.name + (p.team_name ? ' — ' + teamShort(p.team) : '') }));
  });
  picker.appendChild(gp);

  function jump() {
    var v = picker.value;
    // Enter with the prompt still selected asks to be taken nowhere, and '#/' is
    // somewhere: the overview, arrived at by accident.
    if (!v) return;
    var bits = v.split(':');
    location.hash = '#/' + bits[0] + '/' + bits[1];
  }

  /* Choosing is not asking to be taken there. This used to navigate on `change`,
   * and a closed <select> fires `change` on every arrow keypress -- so a reader
   * looking for the twentieth player was taken to the nineteen before it on the
   * way, each one a re-render, a scroll back to the top and a history entry, with
   * the page they were reading gone before they had finished choosing. Back was
   * then nineteen presses from where they started. A reader with a mouse sees the
   * popup and commits once, and never meets any of it.
   *
   * WCAG 2.2 3.2.2 (On Input, Level A) draws the line for everybody rather than
   * for the keyboard alone: changing the setting of a control must not change the
   * context unless the reader was told beforehand that it would. So the list
   * chooses, and the button goes -- one extra press for a mouse, and the first
   * press that does what it says for everybody else. */
  picker.addEventListener('change', function () { markJumpReady(picker); });
  picker.addEventListener('keydown', function (e) {
    // Enter reaches this only while the list is closed, because an open popup
    // consumes its own keys -- and the closed list is exactly the case that had
    // no way to commit. Two keystrokes for a mouse's one click would be this fix
    // charging its cost to the reader it is for.
    if (e.key !== 'Enter') return;
    e.preventDefault();
    jump();
  });
  var go = document.getElementById('entity-go');
  if (go) go.addEventListener('click', jump);
}

/* Read off the list rather than remembered beside it: the value changes from two
 * directions -- the reader choosing, and each render syncing it to the route --
 * and a second copy of "is anything chosen" would be wrong in whichever direction
 * it was not updated from. */
function markJumpReady(picker) {
  var go = document.getElementById('entity-go');
  if (go) go.disabled = !picker.value;
}

/* ============================================================ season picker */

/* Where the site's own root page is, relative to this competition directory.
 * site.py writes site/<competition>/ in single mode and
 * site/<season>/<competition>/ in multi, so the picker is one level up or two.
 * Derived rather than assumed: a hardcoded "../" sent every multi-season
 * dashboard's "all competitions" link to a directory with no page in it.
 *
 * The page itself is named, not just the directory holding it. Ending at "../"
 * relies on a server resolving the directory to its index page; opened as a file,
 * which is how this dashboard is meant to be opened, the browser shows a listing
 * of the folder instead. */
function rootHref() {
  return seasonKey() ? '../../index.html' : '../index.html';
}

/* Which season of an archive this page is: its key, which is the folder the season
 * was built into, or '' on a site of one season. Asked in one place because two
 * things turn on it -- how far up the front page is, and the name of every file this
 * page exports -- and two readings of one question are two chances to disagree. */
function seasonKey() {
  var m = meta();
  return m.seasons_mode === 'multi' ? (m.season_key || '') : '';
}

/* Whether this page is the whole dashboard: written by `build.py --single-file`
 * with the stylesheet, this script and the season folded into it, and reaching its
 * reader with no site around it. The folded page sets the flag itself, above this
 * script, because the question is about the file and not about the season.
 *
 * Two things follow, and both are about not sending a reader somewhere that is not
 * there: no link out to a front page (site.py removes the one in the markup; this
 * covers the one the season picker builds), and a footer that names this file
 * rather than a season-data.js nobody can open. */
function isSingleFile() { return window.SINGLE_FILE === true; }

/* This player's entry on the careers page, which build.py writes at the site root
 * in multi-season mode only, and only by a build that prints players' names; ''
 * where there is no such page to reach. meta.players is there only on a document
 * that withholds them, and a withheld slug is a label for one season, which no
 * career can be looked up by. Never from a one-file dashboard, which travels
 * without the site around it. Built from rootHref(), so it is exactly as deep as
 * the link to the front page beside it. */
function careersHref(slug) {
  if (meta().seasons_mode !== 'multi' || isSingleFile() || meta().players) return '';
  return rootHref().replace(/index\.html$/, 'careers.html') + '#p-' + slug;
}

/* In multi mode the reader needs to know which season this ladder belongs to and
 * how to reach the others; in single mode the word "season" does not appear at
 * all, which is why this returns early rather than rendering an empty control.
 *
 * One dashboard directory holds one competition's data and nothing else, so the
 * only season this page can name from evidence is its own. The other seasons are
 * listed on the picker at the site root, which is what the link goes to — the
 * same discovery the predecessor did, done where the whole tree is visible. */
function fillSeasonPicker() {
  var m = meta();
  if (m.seasons_mode !== 'multi') return;
  var controls = document.querySelector('.controls') ||
                 document.querySelector('.topbar') || document.body;
  var node = hook('season-picker', 'season-picker', controls,
                  document.getElementById('theme-toggle'));
  if (!node) return;
  var label = m.season_label || m.season_key || '';

  if (node.tagName === 'SELECT') {
    // The shell offered a select, so behave like one: this season, then the way
    // out to every other. A .provenance-flag cannot live inside a <select>, so a
    // derived season label says so in the option text and carries the same
    // advice as the control's title — F7 does not get to lapse because the
    // element it would hang off is an option.
    var derivedSeason = isDerived('season');
    node.appendChild(el('option', {
      value: '',
      text: (label ? 'Season: ' + label : 'This season') +
            (derivedSeason ? ' (worked out, not declared)' : '')
    }));
    if (derivedSeason) node.setAttribute('title', provenanceAdvice('season'));
    if (!isSingleFile()) {
      node.appendChild(el('option', { value: rootHref(),
        text: 'All seasons and competitions…' }));
    }
    node.addEventListener('change', function () {
      if (node.value) location.href = node.value;
    });
    return;
  }
  node.appendChild(el('span', { class: 'label', text: 'Season' }));
  var value = el('span', { class: 'value', text: label || 'not declared' });
  node.appendChild(value);
  var flag = provenanceFlag('season');
  if (flag) node.appendChild(flag);
  if (!isSingleFile()) {
    node.appendChild(el('a', { class: 'home-link', href: rootHref(),
      text: 'All seasons →' }));
  }
}

/* ================================================================== boot === */

function fetchJson(url) {
  if (typeof fetch !== 'function') return Promise.reject(new Error('no fetch'));
  return fetch(url, { cache: 'no-store' }).then(function (r) {
    if (!r.ok) throw new Error(url + ': HTTP ' + r.status);
    return r.json();
  });
}

/* Priority: the copy the page is already holding, then a request for season.json,
 * then a clear message.
 *
 * season-data.js is a plain <script> and nothing here runs until it has been read,
 * so by this line the whole season is in memory. It used to fetch season.json
 * anyway and prefer it -- the same document, written out a second time. Measured
 * in Chrome over http on an eight-team home-and-away season (/tmp/payload.py):
 * 415KB of island, 414KB of season.json, 829KB of a 1,033KB page spent on 415KB of
 * season. The fetch was asking the network for something already on the heap.
 *
 * It is kept as the fallback, because the island is not guaranteed: a folder can
 * be published with season.json beside a shell that is served from somewhere else,
 * and the case where neither arrives has to end at showLoadFailure()'s page rather
 * than a blank screen.
 *
 * Whichever is read is named in the footer. That is not decoration: with the
 * island preferred, hand-editing season.json and reloading changes nothing, and
 * the page has to be able to say so.
 *
 * There is deliberately no sample/fixture fallback. Both sources here are written
 * by the same build run, so they cannot disagree; a third checked-in copy would
 * go stale the moment a round landed and then render plausible-but-wrong numbers
 * on the very failure it was meant to soften. Showing nothing, with instructions,
 * is the safer failure — a wrong ladder looks exactly like a right one. */
function loadSeason() {
  if (window.SEASON_DATA) {
    return Promise.resolve({ data: window.SEASON_DATA,
                             note: isSingleFile() ? 'source: this file'
                                                  : 'source: season-data.js' });
  }
  return fetchJson('season.json')
    .then(function (data) { return { data: data, note: 'source: season.json' }; });
}

function showLoadFailure(err) {
  var main = document.getElementById('view');
  clear(main);
  main.appendChild(el('div', { class: 'view-head' }, [
    el('h1', { text: 'No season data found' }),
    el('p', { text: 'The dashboard needs season.json (or a season-data.js that assigns ' +
      'window.SEASON_DATA) beside this page.' })
  ]));
  var card = el('div', { class: 'card' });
  card.appendChild(el('p', { text: 'Browsers block reading a sibling file over file://, so ' +
    'one of these will fix it:' }));
  var list = el('ol');
  [
    'Run the build, which writes both carriers: python3 build.py',
    'Or serve the folder: python3 -m http.server 8000 from this directory, then open http://localhost:8000/',
    'Or open the page over http from wherever season.json is published.'
  ].forEach(function (s) { list.appendChild(el('li', { text: s })); });
  card.appendChild(list);
  card.appendChild(el('p', { class: 'foot muted', style: { 'font-size': '12px' },
    text: 'Loader said: ' + (err && err.message ? err.message : String(err)) }));
  main.appendChild(card);
  var host = document.getElementById('health');
  if (host) {
    clear(host);
    host.appendChild(el('div', { class: 'health-row' }, [
      el('span', { class: 'pill critical' }, [
        el('span', { class: 'dot', 'aria-hidden': 'true' }),
        el('span', { text: '✕ No data loaded' })
      ])
    ]));
  }
}

/* The first tab stop on the page, and it used to be the one control that lost a
 * reader's place. A skip link has to name the id of the region it skips to -- so
 * its href is `#view`, and this page keeps its route in the fragment too. The
 * browser's own jump therefore set location.hash to something the router reads as
 * a route and cannot parse, parseRoute() fell through to the overview, and a
 * reader on Matches who pressed Tab then Enter was handed the overview, scrolled
 * to the top, with a URL that no longer said where they had been.
 *
 * There is no href that is both a valid skip target and a valid route, so the
 * jump is refused and focus is moved here instead. The href stays in the markup,
 * because a link needs one to be focusable at all, and because it is still the
 * right behaviour in the one case this handler cannot cover: a press that lands
 * before app.js has run.
 *
 * Rejected: teaching the router to ignore a fragment it does not recognise. That
 * leaves the address bar saying `#view` after the press, which is a route the
 * next reload cannot restore, and it adds a branch no shipped page can reach.
 */
function initSkipLink() {
  var link = document.querySelector('.skip-link');
  if (!link) return;
  link.addEventListener('click', function (e) {
    var id = (link.getAttribute('href') || '').replace(/^#/, '');
    var target = id ? document.getElementById(id) : null;
    // Nothing to focus: leave the browser to do whatever it would have done,
    // which is at worst what this page did before.
    if (!target) return;
    e.preventDefault();
    // focus() scrolls by "nearest", and this target is taller than the screen, so
    // nearest is "do nothing": the press moved focus and left the reader looking at
    // the middle of the content, or -- from the top -- at a first heading 169px behind
    // the sticky bar. So the scroll is asked for in its own right, at the default
    // block alignment of `start`, which is the one markStickyOffset() offsets.
    target.focus({ preventScroll: true });
    target.scrollIntoView();
  });
}

function boot() {
  readPalette();
  // Before the season, not after: a dashboard whose season.json is missing still
  // has a page, and the reader who most needs to reach the message explaining
  // that is the one who cannot use a mouse to get there.
  initSkipLink();
  loadSeason().then(function (res) {
    S = res.data;
    SOURCE_NOTE = res.note;
    IDX = indexSeason(S);
    var m = meta();
    // index.html ships neutral wording because site.py copies it into every
    // competition's directory unchanged; everything identifying is filled in
    // here, from the document.
    var title = document.getElementById('brand-title');
    if (title) title.textContent = m.label || m.competition || 'Season dashboard';
    var brand = document.getElementById('brand-sub');
    if (brand) {
      brand.textContent = dotted([m.club, m.season_label, m.generated
        ? 'generated ' + String(m.generated).replace('T', ' ') : '']);
    }
    var home = document.querySelector('.home-link');
    if (home) home.setAttribute('href', rootHref());
    fillPicker();
    fillSeasonPicker();
    renderRulesBanner();
    renderHealth();
    initTheme();
    render();
    window.addEventListener('hashchange', render);
    window.addEventListener('resize', onResize);
    var footer = document.getElementById('foot-note');
    if (footer) {
      // The version leads, because this line is what somebody reads back when
      // they report that a figure on this page looks wrong. Falls back rather
      // than printing 'undefined' for a season.json written before the key
      // existed -- an older document still renders, and saying the version is
      // unrecorded is the honest form of not knowing it.
      footer.textContent = 'matchcentre-dashboard ' +
        (m.version || 'version not recorded') + ' · generated ' +
        (m.generated || '—') + ' from ' +
        (m.source_dir || 'the scorecard folder') + ' · ' + SOURCE_NOTE +
        ' · points, ladder and averages computed by matchcentre/stats.py.';
    }
  }).catch(function (err) {
    initTheme();
    showLoadFailure(err);
  });
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', boot);
} else {
  boot();
}
