// LRC for the editor: parse, follow playback, shift, stamp single lines.
// Pure functions on the text, so the text stays the one source of truth and
// the tests run in Node (tests/lrc.test.mjs).

// The same shapes text.py accepts: TIMESTAMP_REGEX and _LRC_META_REGEX.
const LEAD = /^(\s*(?:\[\d{1,3}:\d{2}(?:[.:]\d{1,3})?\]\s*)+)(.*)$/;
const STAMP = /\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\]/g;
const META = /^\s*\[[a-z]{2,}:[^\]]*\]\s*$/i;
const OFFSET = /^\s*\[offset:\s*([+-]?\d+)\s*\]\s*$/i;

function seconds(min, sec, frac) {
  return Number(min) * 60 + Number(sec) + (frac ? Number(frac) / 10 ** frac.length : 0);
}

// [mm:ss.xx], or [mm:ss.xxx] when the lyrics use milliseconds.
export function formatStamp(value, digits = 2) {
  const unit = 10 ** digits;
  const total = Math.max(0, Math.round(value * unit));
  const min = Math.floor(total / (60 * unit));
  const sec = Math.floor((total - min * 60 * unit) / unit);
  const frac = total - min * 60 * unit - sec * unit;
  return `[${String(min).padStart(2, '0')}:${String(sec).padStart(2, '0')}.${String(frac).padStart(digits, '0')}]`;
}

// Lines with their timestamps in seconds as written, plus the file's
// [offset:] in milliseconds. A positive offset shows lyrics earlier: a line
// plays at time - offset.
export function parse(text) {
  let offset = 0;
  let digits = 2;
  const lines = text.split('\n').map((raw, n) => {
    const tag = raw.match(OFFSET);
    if (tag) {
      offset = parseInt(tag[1], 10);
      return { n, times: [], text: raw, meta: true };
    }
    if (META.test(raw)) return { n, times: [], text: raw, meta: true };
    const lead = raw.match(LEAD);
    if (!lead) return { n, times: [], text: raw, meta: false };
    const times = [...lead[1].matchAll(STAMP)].map((m) => {
      if (m[3] && m[3].length === 3) digits = 3;
      return seconds(m[1], m[2], m[3]);
    });
    return { n, times, text: lead[2], meta: false };
  });
  return { lines, offset, digits };
}

// When a written time plays, with the file's offset and the editor's shift.
export function playTime(time, parsed, shiftMs) {
  return time - parsed.offset / 1000 + shiftMs / 1000;
}

// The lines to light up at *position*: those whose timestamp is the latest
// one already reached. Lines sharing it (a romanized line below its
// original) light up together.
export function currentLines(parsed, position, shiftMs) {
  let best = null;
  for (const line of parsed.lines) {
    for (const t of line.times) {
      const at = playTime(t, parsed, shiftMs);
      if (at <= position + 1e-6 && (best === null || at > best)) best = at;
    }
  }
  const lit = new Set();
  if (best === null) return lit;
  for (const line of parsed.lines) {
    if (line.times.some((t) => Math.abs(playTime(t, parsed, shiftMs) - best) < 1e-6)) lit.add(line.n);
  }
  return lit;
}

// Moves every timestamp by *shiftMs* and folds the file's [offset:] in, so
// the result needs no offset tag: players treat that tag differently.
// Times below zero become zero.
export function applyShift(text, shiftMs) {
  const parsed = parse(text);
  const delta = shiftMs / 1000 - parsed.offset / 1000;
  return text.split('\n')
    .filter((raw) => !OFFSET.test(raw))
    .map((raw) => {
      if (META.test(raw)) return raw;
      const lead = raw.match(LEAD);
      if (!lead) return raw;
      const stamps = lead[1].replace(STAMP, (_, m, s, f) => formatStamp(seconds(m, s, f) + delta, parsed.digits));
      return stamps + lead[2];
    })
    .join('\n');
}

// Sets line *n* to start at *time* (seconds as written in the file). A line
// with several timestamps (a repeated chorus) changes the one closest to
// *time*; the lines right below that share it move along, so a romanized
// line stays with its original. A line without one gets one.
export function stampLine(text, n, time) {
  const parsed = parse(text);
  const rows = text.split('\n');
  const line = parsed.lines[n];
  if (!line || line.meta) return text;
  const stamp = formatStamp(time, parsed.digits);
  if (!line.times.length) {
    rows[n] = stamp + rows[n].replace(/^\s+/, '');
    return rows.join('\n');
  }
  let pick = 0;
  line.times.forEach((t, i) => { if (Math.abs(t - time) < Math.abs(line.times[pick] - time)) pick = i; });
  const old = line.times[pick];
  const replaceAt = (row, index) => {
    let seen = -1;
    return row.replace(STAMP, (whole) => { seen += 1; return seen === index ? stamp : whole; });
  };
  rows[n] = replaceAt(rows[n], pick);
  for (let k = n + 1; k < parsed.lines.length; k += 1) {
    const below = parsed.lines[k];
    const same = below.times.findIndex((t) => Math.abs(t - old) < 1e-6);
    if (same < 0) break;
    rows[k] = replaceAt(rows[k], same);
  }
  return rows.join('\n');
}
