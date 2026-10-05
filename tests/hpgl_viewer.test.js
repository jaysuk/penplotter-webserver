// Tests for the HPGL parser used by the preview. Run with: node --test tests/hpgl_viewer.test.js
const test = require('node:test');
const assert = require('node:assert');
const path = require('node:path');

const HPGLViewer = require(path.join(__dirname, '..', 'static', 'hpgl_viewer.js'));
const parse = (text) => HPGLViewer.parse(text);

test('pen down moves build a polyline', () => {
  const { paths } = parse('IN;SP1;PU0,0;PD100,0,100,100,0,100,0,0;PU;');
  assert.strictEqual(paths.length, 1);
  assert.deepStrictEqual(paths[0].pts, [0, 0, 100, 0, 100, 100, 0, 100, 0, 0]);
  assert.strictEqual(paths[0].pen, 1);
});

test('relative moves accumulate from the current position', () => {
  const { paths } = parse('IN;PU10,10;PR;PD5,0,0,5;');
  assert.deepStrictEqual(paths[0].pts, [10, 10, 15, 10, 15, 15]);
});

test('pen up and pen down start separate paths', () => {
  const { paths } = parse('PA0,0;PD;PA10,10;PU;PA20,20;PD;PA30,30;');
  assert.strictEqual(paths.length, 2);
  assert.deepStrictEqual(paths[1].pts, [20, 20, 30, 30]);
});

test('a pen change splits the path and keeps the pen number', () => {
  const { paths } = parse('SP2;PU0,0;PD1,1;SP3;PD2,2;');
  assert.deepStrictEqual(paths.map((p) => p.pen), [2, 3]);
});

test('unsupported commands are reported, harmless ones (PS, VS, ...) are not', () => {
  const { paths, unsupported } = parse('IN;PS4;VS10;LB hello\x03;PU1,1;PD2,2;');
  assert.deepStrictEqual(unsupported, ['LB']);
  assert.strictEqual(paths.length, 1);
});

test('malformed or unpaired arguments are ignored, never NaN', () => {
  assert.strictEqual(parse('PU1,2,3;PD5,x;').paths.length, 0);
  const { paths } = parse('PU0,0;PD1,1,2;');   // trailing unpaired value
  assert.deepStrictEqual(paths[0].pts, [0, 0, 1, 1]);
});

test('bounds', () => {
  assert.deepStrictEqual(HPGLViewer.getBounds(parse('PU0,0;PD100,50;').paths), { minX: 0, minY: 0, maxX: 100, maxY: 50 });
  assert.strictEqual(HPGLViewer.getBounds([]), null);
});
