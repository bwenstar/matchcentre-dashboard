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

/* Bumped by export.py when a key changes meaning. Checked rather than assumed, so
 * a dashboard opened beside an older or newer season.json says so instead of
 * rendering blanks. */
var SEASON_FORMAT = 1;

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

function plural(n, one, many) { return n === 1 ? one : (many || one + 's'); }

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

function inkOn(hex) { return luminance(hex) > 0.42 ? '#0b0b0b' : '#ffffff'; }

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
    teamBySlug: {}, playerBySlug: {}, matchById: {},
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
  (season.matches || []).forEach(function (m) { idx.matchById[m.id] = m; });
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
    gamesFor: low ? rec.games_low : rec.games_high,
    gamesAgainst: low ? rec.games_high : rec.games_low
  };
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

/* Rounds sort 1, 2, 10, then anything non-numeric — the same three-part key
 * competition.round_sort_key uses, because a finals label ("F", "SF", "GF") is
 * not a number and must not sort as one (F11). */
function roundSortKey(label) {
  var s = String(label === null || label === undefined ? '' : label).replace(/^\s+|\s+$/g, '');
  if (/^[0-9]+$/.test(s)) return [0, parseInt(s, 10), ''];
  return [1, 0, s.toLowerCase()];
}

function compareRounds(a, b) {
  var ka = roundSortKey(a), kb = roundSortKey(b);
  if (ka[0] !== kb[0]) return ka[0] - kb[0];
  if (ka[1] !== kb[1]) return ka[1] - kb[1];
  return ka[2] < kb[2] ? -1 : ka[2] > kb[2] ? 1 : 0;
}

function isNumericRound(label) { return roundSortKey(label)[0] === 0; }

/* "R3" for a numbered round, "SF" for a finals one — never "RSF". */
function roundTick(label) {
  return isNumericRound(label) ? 'R' + label : String(label);
}

function roundTitle(label) {
  return isNumericRound(label) ? 'Round ' + label : String(label);
}

/* Whether a match happened, and so has numbers worth counting. Mirrors
 * Match.counts_as_played in model.py, and must keep mirroring it: the aggregates
 * in season.json are computed there, so anything this page counts for itself has
 * to skip the same matches or the tiles disagree with the ladder beside them.
 *
 * Part of a cancelled night's card is often filled in before play stops — a
 * washout can leave two and a half rubbers scored — and those games belong
 * nowhere. */
function countsAsPlayed(match) {
  var status = match && match.status;
  return status !== 'bye' && status !== 'cancelled' && status !== 'unplayed';
}

/* Every set a player appeared in, derived from the match list — the document
 * keeps per-player aggregates but not their per-set trail, and the player view
 * needs the trail to show results rather than only totals. */
function setsOfPlayer(slug) {
  var out = [];
  (S.matches || []).slice().sort(byRound).forEach(function (m) {
    if (!countsAsPlayed(m)) return;
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

  if (opts.table) {
    var toggle = el('button', {
      class: 'btn table-toggle', type: 'button', 'aria-expanded': 'false',
      text: 'Values'
    });
    toggle.addEventListener('click', function () {
      var open = tableWrap.hidden;
      tableWrap.hidden = !open;
      toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
      toggle.textContent = open ? 'Hide values' : 'Values';
    });
    cap.appendChild(toggle);
  }
  fig.appendChild(cap);
  if (opts.legend) fig.appendChild(opts.legend);
  fig.appendChild(plot);
  if (opts.table) tableWrap.appendChild(opts.table);
  fig.appendChild(tableWrap);
  if (opts.footer) fig.appendChild(opts.footer);

  if (opts.draw) CHARTS.push({ figure: fig, plot: plot, draw: opts.draw });
  return fig;
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

function table(head, rows, opts) {
  var o = opts || {};
  var t = el('table');
  if (o.caption) t.appendChild(el('caption', { text: o.caption }));
  var thead = el('thead');
  var tr = el('tr');
  head.forEach(function (h) {
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
    tr.appendChild(cell);
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
    table: rows.length ? table(opts.tableHead, tableRows, { caption: opts.tableCaption }) : null,
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
        // Label outside the tip when there is room; never clipped inside a short bar.
        var tw = textWidth(r.valueText, FONT_VALUE);
        var outside = zeroX + w + 6 + tw <= width - 2;
        if (outside) {
          group.appendChild(svg('text', {
            x: zeroX + w + 6, y: barY + thick / 2 + 4, fill: PAL.text,
            'font-size': 12, 'font-weight': 600, text: r.valueText
          }));
        } else if (w > tw + 16) {
          group.appendChild(svg('text', {
            x: zeroX + w - 6, y: barY + thick / 2 + 4, fill: inkOn(posColor),
            'font-size': 12, 'font-weight': 600, 'text-anchor': 'end', text: r.valueText
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
    table: series.length ? table(tableHead, tableRows, { caption: opts.tableCaption }) : null,
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
  teams.forEach(function (row) {
    teams.forEach(function (col) {
      if (row.slug === col.slug) return;
      var rec = h2hFor(row.slug, col.slug);
      if (rec) maxGames = Math.max(maxGames, rec.gamesFor);
    });
  });

  var wrap = el('div', { class: 'table-wrap' });
  var t = el('table');
  t.appendChild(el('caption', {
    text: 'Games won by the row team against the column team. Blank where the fixture has not been played.'
  }));
  var thead = el('thead');
  var hrow = el('tr');
  hrow.appendChild(el('th', { scope: 'col', text: 'Team' }));
  teams.forEach(function (col) {
    hrow.appendChild(el('th', { scope: 'col', title: col.name, text: col.short || col.name }));
  });
  hrow.appendChild(el('th', { scope: 'col', text: 'W–L' }));
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
      td.setAttribute('title', row.name + ' won ' + rec.gamesFor + ' games, ' +
        col.name + ' won ' + rec.gamesAgainst);
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
  cap.appendChild(el('p', { class: 'sub', text: 'Every completed meeting, games for and against.' }));
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
  var run = el('span', { class: 'form-run' });
  if (!form || !form.length) return el('span', { class: 'muted', text: '—' });
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
  var homeCls = st.winner === 'home' ? 'win' : 'lose';
  var awayCls = st.winner === 'away' ? 'win' : 'lose';
  var status = st.status && st.status !== 'played' ? st.status : (st.completed ? '' : 'unfinished');
  return [
    { text: slotLabel(st.slot), cls: '' },
    names(st.home_players),
    { text: num(st.home_games), cls: homeCls },
    { text: num(st.away_games), cls: awayCls },
    names(st.away_players),
    { text: status ? status.charAt(0).toUpperCase() + status.slice(1) : '—',
      cls: status ? '' : 'muted' }
  ];
}

function matchCard(m, opts) {
  var o = opts || {};
  var d = el('details', { class: 'match' });
  if (o.open) d.setAttribute('open', '');
  var summary = el('summary');
  summary.appendChild(el('span', { class: 'side' }, [
    el('span', { class: 'chip', style: keyStyle(teamColor(m.home), teamDash(m.home)), 'aria-hidden': 'true' }),
    el('span', { class: m.result === 'home' ? 'win' : '', text: teamShort(m.home) })
  ]));
  summary.appendChild(el('span', { class: 'score' }, [
    document.createTextNode(num(m.home_games) + ' – ' + num(m.away_games))
  ]));
  summary.appendChild(el('span', { class: 'side' }, [
    el('span', { class: 'chip', style: keyStyle(teamColor(m.away), teamDash(m.away)), 'aria-hidden': 'true' }),
    el('span', { class: m.result === 'away' ? 'win' : '', text: teamShort(m.away) })
  ]));
  var bits = [];
  bits.push('sets ' + num(m.home_sets) + '–' + num(m.away_sets));
  bits.push('points ' + num(m.home_points, 1) + ' / ' + num(m.away_points, 1));
  if (m.status !== 'played') bits.push(m.status);
  if (m.forfeited_by) bits.push('forfeited by ' + teamShort(m.forfeited_by));
  if (m.disputed) bits.push('disputed');
  if (m.tied_on_games) bits.push('tied on games, decided on sets');
  if ((m.warnings || []).length) bits.push((m.warnings || []).length + ' note' +
    ((m.warnings || []).length === 1 ? '' : 's'));
  summary.appendChild(el('span', { class: 'meta', text: bits.join(' · ') }));
  d.appendChild(summary);

  var body = el('div', { class: 'match-body' });
  body.appendChild(el('div', { class: 'table-wrap' }, [
    table(['Set', teamShort(m.home), 'G', 'G', teamShort(m.away), 'Status'],
      (m.sets || []).map(function (st) { return setRow(m, st); }),
      { caption: longDate(m.date) + ' · court ' + (m.court || '—') +
                 ' · match #' + m.id })
  ]));
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
  d.appendChild(body);
  return d;
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
  line.appendChild(el('span', { class: 'meta',
    text: rules().average_unplayed
      ? 'bye · scored at the team’s own average'
      : 'bye · no points, by this competition’s rules' }));
  row.appendChild(line);
  return row;
}

function scheduledRow(fx) {
  var row = el('div', { class: 'match' });
  var line = el('div', { class: 'summary', style: { display: 'flex', 'flex-wrap': 'wrap',
    'align-items': 'center', gap: '6px 12px', padding: '10px 4px 10px 20px' } });
  line.appendChild(el('span', { class: 'side' }, [teamLinkBySlug(fx.home)]));
  line.appendChild(el('span', { class: 'vs', text: 'v' }));
  line.appendChild(el('span', { class: 'side' }, [teamLinkBySlug(fx.away)]));
  line.appendChild(el('span', { class: 'meta', text: 'scheduled · ' + longDate(fx.date) }));
  row.appendChild(line);
  return row;
}

/* ============================================================== view: home == */

function renderOverview(main) {
  var m = meta();
  var ladder = IDX.ladder;
  var played = (S.matches || []).filter(countsAsPlayed);
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
  hero.appendChild(seasonProgress(played.length, scheduled, drawDeclared));
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
      text: roundTitle(lastRound.number) + ' · ' + longDate(lastRound.date) }));
    (lastRound.matches || []).forEach(function (id) {
      var match = IDX.matchById[id];
      if (match) box.appendChild(matchCard(match));
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
 * whole of what is known, and it is said as a count. */
function seasonProgress(matchesPlayed, scheduled, drawDeclared) {
  var m = meta();
  var playedRounds = m.rounds_played || 0;
  var total = m.rounds_total;
  var box = el('div', { class: 'progress' });

  if (total === null || total === undefined) {
    var label = el('div', { class: 'label' }, [
      el('span', { text: 'Rounds played' }),
      el('span', { text: playedRounds + ' ' + plural(playedRounds, 'round') })
    ]);
    var flag = provenanceFlag('rounds_total');
    if (flag) label.appendChild(flag);
    box.appendChild(label);
    box.appendChild(el('div', { class: 'foot',
      text: matchesPlayed + ' ' + plural(matchesPlayed, 'match', 'matches') + ' scored. ' +
        'How long this season runs has not been declared, so no percentage is shown.' }));
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
    text: (drawDeclared ? matchesPlayed + ' of ' + scheduled + ' fixtures played · '
                        : matchesPlayed + ' matches scored · ') +
      percent + '% of the declared rounds' }));
  return box;
}

/* F10. The ladder always states its basis; when teams have played unequal numbers
 * of counted matches it also says so, because a table that looks settled and is
 * not is the failure this rule exists to stop. */
function ladderNote() {
  var m = meta();
  var order = (rules().ladder_by || []).map(function (k) { return k.replace(/_/g, ' '); });
  var basis = order.length ? 'Ordered by ' + order.join(', then ') + '.' : '';
  if (m.ladder_complete) {
    return el('p', { class: 'ladder-note', text: basis });
  }
  return el('p', { class: 'ladder-note ladder-note--incomplete' }, [
    el('span', { class: 'ico', 'aria-hidden': 'true', text: '!' }),
    el('span', { text: ' In progress — teams have played unequal numbers of ' +
      'matches, so this order is a snapshot and not a standing. ' + basis })
  ]);
}

function ladderTable() {
  var head = ['#', 'Team', 'P', 'W', 'L', 'D', 'Points',
    { label: 'Avg', title: 'Points average: points divided by matches counted' },
    { label: 'GF', title: 'Games for' }, { label: 'GA', title: 'Games against' },
    { label: 'Diff', title: 'Games differential' }, 'Form'];
  var rows = IDX.ladder.map(function (t) {
    return {
      href: '#/team/' + t.slug,
      cells: [
        { text: t.position === null || t.position === undefined ? '—' : t.position, cls: 'pos' },
        teamLink(t, { long: true }),
        t.played, t.won, t.lost, t.drawn,
        num(t.points, 1),
        t.points_average === null || t.points_average === undefined
          ? { text: '—', cls: 'muted' } : num(t.points_average, 2),
        t.games_won, t.games_lost, signed(t.games_diff),
        formRun(t.form)
      ]
    };
  });
  return el('div', { class: 'table-wrap' }, [
    table(head, rows, { caption: 'Select a row for the team page.' })
  ]);
}

/* ============================================================ team charts == */

function pointsAverageChart(selectedSlug) {
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
          { name: 'Played', value: String(t.played) },
          { name: 'Ladder position', value: String(t.position) }
        ]
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
    tableHead: ['Team', 'Points average', 'Points', 'Played'],
    tableCaption: 'Points average = points ÷ matches counted.',
    tableRow: function (r) {
      var t = IDX.teamBySlug[r.href.split('/').pop()];
      return [r.label, num(t.points_average, 2), num(t.points, 1), String(t.played)];
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
  var rows = matches.map(function (m) {
    var isHome = m.home === t.slug;
    var pts = isHome ? m.home_points : m.away_points;
    var mine = isHome ? m.home_games : m.away_games;
    var theirs = isHome ? m.away_games : m.home_games;
    var oppSlug = isHome ? m.away : m.home;
    return {
      label: roundTick(m.round),
      value: pts === null || pts === undefined ? 0 : pts,
      valueText: pts === null || pts === undefined ? 'not scored' : num(pts, 1),
      color: PAL.series[0],
      aria: roundTitle(m.round) + ': ' + num(pts, 1) + ' points against ' + teamName(oppSlug),
      tip: {
        title: roundTitle(m.round) + ' v ' + teamShort(oppSlug),
        rows: [
          { name: 'Points', value: num(pts, 1), kind: 'rect', color: PAL.series[0] },
          { name: 'Games', value: num(mine) + '–' + num(theirs) },
          { name: 'Result', value: resultWord(m, t.slug) },
          { name: isHome ? 'At home' : 'Away', value: longDate(m.date) }
        ]
      }
    };
  });
  return hbarCard({
    title: 'Points earned by round',
    // The active scheme, spelled out by export.py from the Ruleset itself. The
    // predecessor printed one club's numbers here as a literal.
    sub: rules().scoring_detail || 'Points as this competition scores them.',
    rows: rows,
    mode: 'single',
    axisLabel: 'Competition points',
    empty: 'This team has not played yet.',
    tableHead: ['Round', 'Opponent', 'Result', 'Games', 'Points'],
    tableRow: function (r) {
      var m = matches[rows.indexOf(r)];
      var isHome = m.home === t.slug;
      return [roundTick(m.round), teamShort(isHome ? m.away : m.home),
        resultWord(m, t.slug),
        num(isHome ? m.home_games : m.away_games) + '–' +
        num(isHome ? m.away_games : m.home_games),
        num(isHome ? m.home_points : m.away_points, 1)];
    }
  });
}

function resultWord(m, slug) {
  if (m.result === 'draw') return 'Draw';
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
  document.title = t.name + ' — ' + (meta().label || '');

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
    tile('Ladder position', pos, t.played
      ? num(t.points_average, 2) + ' points average' : 'no matches counted'),
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
      (t.sets_won + t.sets_lost) ? num(100 * t.sets_won / (t.sets_won + t.sets_lost), 0) +
        '% of sets won' : 'no sets yet'),
    tile('Streak', streakWords(t.streak),
      'best round ' + (t.best_round ? roundTick(t.best_round.round) + ' (' +
        num(t.best_round.points, 1) + ')' : '—'))
  ]));

  var splitBox = el('div', { class: 'card' }, [
    el('div', { class: 'table-wrap' }, [
      table(['Split', 'P', 'W', 'L', 'D', 'GF', 'GA', 'Diff'], [
        ['Home', t.home.played, t.home.won, t.home.lost, t.home.drawn,
          t.home.games_won, t.home.games_lost, signed(t.home.games_won - t.home.games_lost)],
        ['Away', t.away.played, t.away.won, t.away.lost, t.away.drawn,
          t.away.games_won, t.away.games_lost, signed(t.away.games_won - t.away.games_lost)],
        ['Total', t.played, t.won, t.lost, t.drawn, t.games_won, t.games_lost, signed(t.games_diff)]
      ], { caption: 'Home and away split' })
    ]),
    el('p', { class: 'foot muted', style: { 'font-size': '12px', margin: '8px 0 0' } }, [
      document.createTextNode('Form (oldest first): '), formRun(t.form)
    ])
  ]);

  var charts = el('div', { class: 'grid charts', style: { 'margin-top': '12px' } });
  charts.appendChild(slotStrengthChart(t));
  charts.appendChild(pointsByRoundChart(t, matches));
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
        p.contribution ? pct(p.contribution.share_pct) : '—',
        p.rating && p.rating.singles !== null && p.rating.singles !== undefined
          ? num(p.rating.singles, 2) : { text: '—', cls: 'muted' },
        p.rating && p.rating.doubles !== null && p.rating.doubles !== undefined
          ? num(p.rating.doubles, 2) : { text: '—', cls: 'muted' }
      ]
    };
  });
  return el('div', { class: 'table-wrap' }, [
    table(['Player', { label: 'C', title: 'Captain' }, 'M', 'Sets', 'W–L', 'Win %',
      'GF', 'GA', 'Diff',
      { label: 'Share', title: 'Share of the team’s games won' },
      { label: 'S', title: 'Singles rating' }, { label: 'D', title: 'Doubles rating' }],
      rows, { caption: 'Contribution of each registered player.' })
  ]);
}

function teamH2HTable(t) {
  var others = IDX.teamsOrdered.filter(function (o) { return o.slug !== t.slug; });
  var rows = others.map(function (o) {
    var rec = h2hFor(t.slug, o.slug);
    var scheduled = (S.fixtures || []).filter(function (f) {
      return !f.played && ((f.home === t.slug && f.away === o.slug) ||
        (f.home === o.slug && f.away === t.slug));
    });
    return {
      href: '#/team/' + o.slug,
      cells: [
        teamLink(o, { long: true }),
        rec ? rec.played : 0,
        rec ? rec.won + '–' + rec.lost + (rec.drawn ? '–' + rec.drawn : '') : { text: '—', cls: 'muted' },
        rec ? num(rec.gamesFor) + '–' + num(rec.gamesAgainst) : { text: '—', cls: 'muted' },
        rec ? signed(rec.gamesFor - rec.gamesAgainst) : { text: '—', cls: 'muted' },
        scheduled.length ? scheduled.map(function (f) { return roundTick(f.round); }).join(', ')
          : { text: '—', cls: 'muted' }
      ]
    };
  });
  return el('div', { class: 'table-wrap' }, [
    table(['Opponent', 'Met', 'W–L', 'Games', 'Diff',
      { label: 'To come', title: 'Rounds where this fixture is still to be played' }],
      rows, { caption: 'Record against each other team in this competition.' })
  ]);
}

/* ============================================================ view: player == */

function renderPlayer(main, slug) {
  var p = IDX.playerBySlug[slug];
  if (!p) { renderMissing(main, 'No player with the id "' + slug + '".'); return; }
  var team = teamOf(p.team);
  var sets = setsOfPlayer(p.slug);
  document.title = p.name + ' — ' + (meta().label || '');

  main.appendChild(el('a', { class: 'crumb', href: '#/players', text: '← All players' }));
  var h1 = el('h1', { text: p.name + (p.is_captain ? ' (captain)' : '') });
  var nameFlag = provenanceFlag('players');
  if (nameFlag) h1.appendChild(nameFlag);
  main.appendChild(el('div', { class: 'view-head' }, [
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
  ]));

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
    tile('Team share', p.contribution ? pct(p.contribution.share_pct) : '—',
      p.contribution ? num(p.contribution.games_won) + ' of ' +
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
    'No doubles set played yet.')]));

  main.appendChild(sectionTitle('Opponents'));
  main.appendChild(el('div', { class: 'card' }, [pairTable(p.opponents, 'opponent',
    'No opponent faced yet.')]));

  if ((p.rating_history || []).length > 1) {
    main.appendChild(sectionTitle('Rating as printed each round'));
    main.appendChild(el('div', { class: 'card' }, [
      el('div', { class: 'table-wrap' }, [
        table(['Round', 'Singles', 'Doubles'], (p.rating_history || []).map(function (h) {
          return [roundTick(h.round),
            h.singles === null || h.singles === undefined ? { text: '—', cls: 'muted' } : num(h.singles, 2),
            h.doubles === null || h.doubles === undefined ? { text: '—', cls: 'muted' } : num(h.doubles, 2)];
        }), { caption: 'Ratings come off the scorecard, so a mid-season change shows up here.' })
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
        { caption: 'Derived from the set rows on each scorecard.' })
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
    tableHead: ['Opponent', 'Sets met', 'W–L', 'Games won', 'Games lost', 'Diff'],
    tableRow: function (r) {
      var o = list[rows.indexOf(r)];
      return [o.name, num(o.sets), o.won + '–' + o.lost, num(o.games_won),
        num(o.games_lost), signed(o.games_won - o.games_lost)];
    }
  });
}

function slotAppearanceTable(p) {
  var rows = IDX.slots.map(function (slot) {
    var rec = (p.by_slot || {})[slot.key] || { played: 0, won: 0 };
    return [slotTitle(slot.key), num(rec.played), num(rec.won),
      rec.played ? pct(100 * rec.won / rec.played) : { text: '—', cls: 'muted' }];
  });
  var out = el('div');
  out.appendChild(el('div', { class: 'table-wrap' }, [
    table(['Slot', 'Played', 'Won', 'Win rate'], rows,
      { caption: 'Which of this format’s set slots the player is used in.' })
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

function pairTable(list, kind, emptyText) {
  if (!list || !list.length) return el('p', { class: 'empty-note', text: emptyText });
  var rows = list.slice().sort(function (a, b) { return b.sets - a.sets; }).map(function (r) {
    return {
      href: '#/player/' + r.slug,
      cells: [playerLink(r.slug), num(r.sets), r.won + '–' + r.lost,
        num(r.games_won), num(r.games_lost), signed(r.games_won - r.games_lost)]
    };
  });
  return el('div', { class: 'table-wrap' }, [
    table([kind === 'partner' ? 'Partner' : 'Opponent', 'Sets', 'W–L', 'GF', 'GA', 'Diff'],
      rows, { caption: kind === 'partner'
        ? 'Record in doubles sets played together.'
        : 'Record in every set contested against this player.' })
  ]);
}

/* =========================================================== view: matches == */

function renderMatches(main) {
  document.title = 'Matches — ' + (meta().label || '');
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
  rounds.forEach(function (r) {
    var fixtures = (S.fixtures || []).filter(function (f) { return f.round === r.number; });
    var head2 = el('div', { class: 'round-head' }, [
      // A finals round never contributes ladder points (F11), and it is the
      // round's own non-numeric label that says it is one.
      el('h3', { text: r.is_finals ? 'Finals — ' + roundTitle(r.number) : roundTitle(r.number) }),
      // The draw's own parenthetical against a date — "(mid-semester break)" —
      // which is the reader's answer to "why is this round in the holidays?".
      el('span', { class: 'date',
        text: longDate(r.date) + (r.note ? ' (' + r.note + ')' : '') }),
      el('span', { class: 'pill ' + (r.played ? 'good' : '') }, [
        el('span', { class: 'dot', 'aria-hidden': 'true' }),
        el('span', { text: r.played ? 'played' : 'not played yet' })
      ])
    ]);
    main.appendChild(head2);
    var box = el('div', { class: 'card' });
    if (!fixtures.length && !(r.matches || []).length) {
      box.appendChild(el('p', { class: 'empty-note',
        text: r.is_finals
          ? 'Pairings are set by ladder position once the regular rounds are in.'
          : 'No fixtures listed for this round.' }));
    }
    if (r.is_finals) {
      box.appendChild(el('p', { class: 'foot muted', style: { 'font-size': '12px' },
        text: 'Finals do not contribute ladder points.' }));
    }
    fixtures.forEach(function (f) {
      var m = f.match_id ? IDX.matchById[f.match_id] : null;
      if (m) box.appendChild(matchCard(m));
      else box.appendChild(scheduledRow(f));
    });
    // A card whose match id is not in any fixture still gets shown.
    (r.matches || []).forEach(function (id) {
      var known = fixtures.some(function (f) { return f.match_id === id; });
      if (!known && IDX.matchById[id]) box.appendChild(matchCard(IDX.matchById[id]));
    });
    (r.byes || []).forEach(function (slug) { box.appendChild(byeRow(slug)); });
    main.appendChild(box);
  });
}

/* =========================================================== view: players == */

var PLAYER_COLS = [
  { key: 'name', label: 'Player', type: 'text' },
  { key: 'team', label: 'Team', type: 'text' },
  { key: 'matches', label: 'M', type: 'num', title: 'Matches played' },
  { key: 'sets_played', label: 'Sets', type: 'num' },
  { key: 'sets_won', label: 'Won', type: 'num' },
  { key: 'win_pct', label: 'Win %', type: 'num' },
  { key: 'games_won', label: 'GF', type: 'num', title: 'Games won' },
  { key: 'games_lost', label: 'GA', type: 'num', title: 'Games lost' },
  { key: 'games_diff', label: 'Diff', type: 'num' },
  { key: 'singles_pct', label: 'S W-L', type: 'text', title: 'Singles record' },
  { key: 'doubles_pct', label: 'D W-L', type: 'text', title: 'Doubles record' },
  { key: 'share', label: 'Share', type: 'num', title: 'Share of team games won' },
  { key: 'rating_s', label: 'S rating', type: 'num' },
  { key: 'rating_d', label: 'D rating', type: 'num' }
];

function playerSortValue(p, key) {
  switch (key) {
    case 'name': return p.name;
    case 'team': return p.team_name || '';
    case 'win_pct': return p.win_pct === null || p.win_pct === undefined ? -1 : p.win_pct;
    case 'share': return p.contribution ? p.contribution.share_pct : -1;
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
  document.title = 'Players — ' + (meta().label || '');
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
      var cmp = typeof va === 'string'
        ? String(va).localeCompare(String(vb))
        : (va - vb);
      if (cmp === 0) return String(a.name).localeCompare(String(b.name));
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
          p.contribution ? pct(p.contribution.share_pct) : { text: '—', cls: 'muted' },
          p.rating && p.rating.singles !== null && p.rating.singles !== undefined
            ? num(p.rating.singles, 2) : { text: '—', cls: 'muted' },
          p.rating && p.rating.doubles !== null && p.rating.doubles !== undefined
            ? num(p.rating.doubles, 2) : { text: '—', cls: 'muted' }
        ]
      };
    });
    host.appendChild(el('div', { class: 'table-wrap' }, [
      table(head2, rows, { caption: (S.players || []).length + ' registered ' +
        plural((S.players || []).length, 'player') + '. A dash means nothing recorded yet.' })
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
    return String(a.name).localeCompare(String(b.name));
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
          '. Everyone else is in the table above.' })
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
    tableHead: ['Player', 'Team', 'Games won', 'Games lost', 'Diff'],
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
 * the wrong preset can recognise it without reading any code. */
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
    ['Format', r.format, r.format_detail]
  ];
  pairs.forEach(function (bits) {
    if (!bits[1] && !bits[2]) return;
    var span = el('span', {}, [el('strong', { text: bits[0] + ': ' })]);
    span.appendChild(document.createTextNode(bits[1] || ''));
    if (bits[2]) span.appendChild(el('span', { class: 'muted', text: ' — ' + bits[2] }));
    host.appendChild(span);
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
  host.appendChild(el('span', { class: 'muted',
    text: 'These decide every points and ladder number on this page. ' +
      'If they are not this competition’s rules, set scoring, sets, unplayed and format in config.toml.' }));
}

/* ============================================================ data health == */

function renderHealth() {
  var host = document.getElementById('health');
  if (!host) return;
  clear(host);
  var m = meta();
  var v = (S && S.validation) || { ok: true, errors: [], warnings: [] };
  var errors = v.errors || [], warnings = v.warnings || [], rejected = v.rejected || [];
  var row = el('div', { class: 'health-row' });

  var state = (errors.length || rejected.length) ? 'critical' : warnings.length ? 'warning' : 'good';
  var label = errors.length
    ? errors.length + ' validation ' + plural(errors.length, 'error')
    : warnings.length
      ? 'Validated with ' + warnings.length + ' ' + plural(warnings.length, 'warning')
      : 'Validated clean';
  row.appendChild(el('span', { class: 'pill ' + state }, [
    el('span', { class: 'dot', 'aria-hidden': 'true' }),
    el('span', { text: (errors.length ? '✕ ' : warnings.length ? '! ' : '✓ ') + label })
  ]));
  // Every PDF found is scored or named (F4), so the count is stated as a
  // fraction rather than as a total: "6 cards read" cannot distinguish six from
  // seven-minus-one.
  row.appendChild(el('span', { class: 'pill' }, [
    el('span', { class: 'dot', 'aria-hidden': 'true' }),
    el('span', { text: (m.cards_scored || 0) + ' of ' + (m.cards_seen || 0) +
      ' ' + plural(m.cards_seen || 0, 'card') + ' scored' })
  ]));
  if (rejected.length) {
    row.appendChild(el('span', { class: 'pill critical' }, [
      el('span', { class: 'dot', 'aria-hidden': 'true' }),
      el('span', { text: '✕ ' + rejected.length + ' ' + plural(rejected.length, 'card') +
        ' seen and not scored' })
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

  if (S.format !== undefined && S.format !== SEASON_FORMAT) {
    host.appendChild(note('warning', 'Schema version',
      'This page reads season document format ' + SEASON_FORMAT + ' and the data says ' +
      S.format + '. Some values may be missing or mean something else. Re-run ' +
      'python3 build.py to regenerate the dashboard beside its data.'));
  }

  if (errors.length || warnings.length) {
    var det = el('details');
    if (errors.length) det.setAttribute('open', '');
    det.appendChild(el('summary', { text: 'Data health detail (' + errors.length + ' ' +
      plural(errors.length, 'error') + ', ' + warnings.length + ' ' +
      plural(warnings.length, 'warning') + ')' }));
    var list = el('ul');
    errors.concat(warnings).forEach(function (item, i) {
      var isError = i < errors.length;
      var li = el('li');
      li.appendChild(el('strong', { text: (isError ? 'Error' : 'Warning') +
        (item.rule ? ' · ' + item.rule : '') + ': ' }));
      li.appendChild(document.createTextNode(item.message || String(item)));
      if (item.subject) li.appendChild(el('code', { text: ' [' + item.subject + ']' }));
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
  box.appendChild(note('critical', rejected.length + ' ' +
    plural(rejected.length, 'scorecard') + ' seen and not scored',
    'Each file below was found under the data folder and produced no result, so ' +
    'every number on this page is missing whatever it contained. Run ' +
    'python3 tools/doctor.py on a path to see why it was refused.'));
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

function parseRoute() {
  var hash = location.hash.replace(/^#\/?/, '');
  var parts = hash.split('/').filter(function (s) { return s.length; });
  if (!parts.length) return { view: 'overview' };
  if (parts[0] === 'team') return { view: 'team', slug: decodeURIComponent(parts[1] || '') };
  if (parts[0] === 'player') return { view: 'player', slug: decodeURIComponent(parts[1] || '') };
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
  }
}

function render() {
  var main = document.getElementById('view');
  CHARTS = [];
  clear(main);
  hideTip();
  readPalette();
  var route = parseRoute();
  document.title = (meta().label || 'Season') + ' — dashboard';
  if (route.view === 'team') renderTeam(main, route.slug);
  else if (route.view === 'player') renderPlayer(main, route.slug);
  else if (route.view === 'matches') renderMatches(main);
  else if (route.view === 'players') renderPlayers(main);
  else renderOverview(main);
  markTabs(route);
  drawCharts();
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
  resizeTimer = setTimeout(function () { readPalette(); drawCharts(); }, 150);
}

/* ================================================================== theme == */

/* The storage key is per club, not per program: two clubs' dashboards can end up
 * served from one origin, and the predecessor's single hardcoded key meant either
 * would silently overwrite the other's choice. site.py derives the same key for
 * the picker page from the club name, so the two agree and one choice covers the
 * whole site. */
function slugifyName(text) {
  var s = String(text === null || text === undefined ? '' : text).toLowerCase();
  if (typeof s.normalize === 'function') {
    // Strip combining marks so an accented club name slugs the way slugs.py
    // slugs it. Guarded because String.prototype.normalize is not ES5.
    s = s.normalize('NFKD').replace(/[̀-ͯ]/g, '');
  }
  return s.replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
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
    return String(a.name).localeCompare(String(b.name));
  }).forEach(function (p) {
    gp.appendChild(el('option', { value: 'player:' + p.slug,
      text: p.name + (p.team_name ? ' — ' + teamShort(p.team) : '') }));
  });
  picker.appendChild(gp);
  picker.addEventListener('change', function () {
    var v = picker.value;
    if (!v) return;
    var bits = v.split(':');
    location.hash = '#/' + bits[0] + '/' + bits[1];
  });
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
  var m = meta();
  return (m.seasons_mode === 'multi' && m.season_key) ? '../../index.html'
                                                     : '../index.html';
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
    node.appendChild(el('option', { value: rootHref(),
      text: 'All seasons and competitions…' }));
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
  node.appendChild(el('a', { class: 'home-link', href: rootHref(),
    text: 'All seasons →' }));
}

/* ================================================================== boot === */

function fetchJson(url) {
  if (typeof fetch !== 'function') return Promise.reject(new Error('no fetch'));
  return fetch(url, { cache: 'no-store' }).then(function (r) {
    if (!r.ok) throw new Error(url + ': HTTP ' + r.status);
    return r.json();
  });
}

/* Priority: the real file over http, then the season-data.js carrier (the file://
 * escape hatch, since browsers block fetch of a sibling file), then a clear
 * message.
 *
 * There is deliberately no sample/fixture fallback. Both sources here are written
 * by the same build run, so they cannot disagree; a third checked-in copy would
 * go stale the moment a round landed and then render plausible-but-wrong numbers
 * on the very failure it was meant to soften. Showing nothing, with instructions,
 * is the safer failure — a wrong ladder looks exactly like a right one. */
function loadSeason() {
  return fetchJson('season.json')
    .then(function (data) { return { data: data, note: 'source: season.json' }; })
    .catch(function (err) {
      if (window.SEASON_DATA) {
        return { data: window.SEASON_DATA, note: 'source: season-data.js' };
      }
      throw err;
    });
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

function boot() {
  readPalette();
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
      brand.textContent = [m.club, m.season_label, m.generated
        ? 'generated ' + String(m.generated).replace('T', ' ') : '']
        .filter(function (s) { return !!s; }).join(' · ');
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
