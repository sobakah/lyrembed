// The editor's LRC functions (lyrembed/static/lrc.js). Run by pytest through
// test_lrc_editor_functions, or directly: node --test tests/lrc.test.mjs
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parse, currentLines, applyShift, stampLine, formatStamp, playTime, nearestTime } from '../lyrembed/static/lrc.js';

test('formats and clamps timestamps', () => {
  assert.equal(formatStamp(83.456), '[01:23.46]');
  assert.equal(formatStamp(83.456, 3), '[01:23.456]');
  assert.equal(formatStamp(-1.2), '[00:00.00]');
  assert.equal(formatStamp(59.999), '[01:00.00]');
});

test('reads the shapes text.py accepts', () => {
  const { lines, offset, digits } = parse('[ti:Song]\n[offset:+300]\n[00:01.5]a\n[1:02:25]b\n[00:03.250][00:40.00]c\nplain');
  assert.equal(offset, 300);
  assert.equal(digits, 3);
  assert.deepEqual(lines.map((l) => l.meta), [true, true, false, false, false, false]);
  assert.deepEqual(lines[2].times, [1.5]);
  assert.deepEqual(lines[3].times, [62.25]);
  assert.deepEqual(lines[4].times, [3.25, 40]);
  assert.deepEqual(lines[5].times, []);
});

test('lights up the latest line reached, together with a romanized line below', () => {
  const parsed = parse('[00:01.00]夜明け\n[00:01.00]yoake\n[00:03.00]街\n[00:10.00][00:20.00]chorus');
  assert.deepEqual([...currentLines(parsed, 0.5, 0)], []);
  assert.deepEqual([...currentLines(parsed, 2, 0)], [0, 1]);
  assert.deepEqual([...currentLines(parsed, 2, 1500)], []);     // shifted later
  assert.deepEqual([...currentLines(parsed, 25, 0)], [3]);      // repeated line
});

test('the file offset counts: positive shows lyrics earlier', () => {
  const parsed = parse('[offset:500]\n[00:02.00]a');
  assert.equal(playTime(2, parsed, 0), 1.5);
  assert.deepEqual([...currentLines(parsed, 1.6, 0)], [1]);
});

test('shifting rewrites the timestamps and folds the offset tag in', () => {
  assert.equal(applyShift('[ar:X]\n[00:01.00]a\n[00:02.50][00:30.00]b\nplain', -250),
               '[ar:X]\n[00:00.75]a\n[00:02.25][00:29.75]b\nplain');
  assert.equal(applyShift('[offset:+300]\n[00:01.00]a', 0), '[00:00.70]a');
  assert.equal(applyShift('[00:00.10]a', -500), '[00:00.00]a');
  assert.equal(applyShift('[00:01.000]a', 5), '[00:01.005]a');
});

test('stamping sets one line and keeps its romanization with it', () => {
  const text = '[00:01.00]夜明け\n[00:01.00]yoake\n[00:05.00]next';
  assert.equal(stampLine(text, 0, 1.4), '[00:01.40]夜明け\n[00:01.40]yoake\n[00:05.00]next');
  assert.equal(stampLine(text, 2, 6), '[00:01.00]夜明け\n[00:01.00]yoake\n[00:06.00]next');
});

test('stamping a repeated line changes the nearest occurrence', () => {
  assert.equal(stampLine('[00:10.00][01:20.00]chorus', 0, 81), '[00:10.00][01:21.00]chorus');
});

test('stamping a plain line gives it a timestamp; meta lines stay', () => {
  assert.equal(stampLine('first\n  second', 1, 4.2), 'first\n[00:04.20]second');
  assert.equal(stampLine('[ti:Song]', 0, 3), '[ti:Song]');
});

test('Windows line ends keep their timestamps', () => {
  const crlf = '[00:01.00]one\r\n[00:02.00]two\r\n';
  assert.deepEqual(parse(crlf).lines.map((l) => l.times), [[1], [2], []]);
  assert.equal(parse(crlf).lines[0].text, 'one');
  assert.equal(applyShift(crlf, 500), '[00:01.50]one\n[00:02.50]two\n');
  assert.equal(stampLine(crlf, 0, 1.4), '[00:01.40]one\n[00:02.00]two\n');
});

test('clicking a repeated line picks the repeat by play time', () => {
  // [offset:-20000]: 00:30 plays at 00:50, 01:00 at 01:20. At 00:52 the
  // first repeat plays; by written time the second looked closer.
  const parsed = parse('[offset:-20000]\n[00:30.00][01:00.00]chorus');
  assert.equal(nearestTime(parsed.lines[1], 52, parsed, 0), 30);
  const plain = parse('[00:30.00][01:00.00]x');
  assert.equal(nearestTime(plain.lines[0], 52, plain, 20000), 30);   // the same with a shift
});
