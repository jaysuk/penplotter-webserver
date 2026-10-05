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

// ---- circles and arcs ---------------------------------------------------------------------

test('a circle is drawn round the pen whatever the pen state', () => {
  const { paths, unsupported } = parse('IN;SP1;PU100,100;CI50;');
  assert.deepStrictEqual(unsupported, []);
  assert.strictEqual(paths.length, 1);
  assert.strictEqual(paths[0].pts.length / 2, 73);              // 72 chords of 5 degrees
  for (let i = 0; i < paths[0].pts.length; i += 2) {
    assert.ok(Math.abs(Math.hypot(paths[0].pts[i] - 100, paths[0].pts[i + 1] - 100) - 50) < 1e-6);
  }
});

test('the chord angle of a circle can be given', () => {
  assert.strictEqual(parse('PU0,0;CI10,30;').paths[0].pts.length / 2, 13);
});

test('an arc starts at the pen and goes counter-clockwise for a positive sweep', () => {
  const { paths, route } = parse('PU50,0;AA0,0,90;PD5,5;');
  const pts = paths[0].pts;
  assert.deepStrictEqual(pts.slice(0, 2), [50, 0]);
  assert.ok(Math.abs(pts[pts.length - 2]) < 1e-6 && Math.abs(pts[pts.length - 1] - 50) < 1e-6);
  // and the pen is where the arc ended: the next move starts there
  const last = route.seg.length - 4;
  assert.ok(Math.abs(route.seg[last]) < 1e-6 && Math.abs(route.seg[last + 1] - 50) < 1e-6);
});

test('a relative arc takes its centre from the pen position', () => {
  const { paths } = parse('PA100,0;AR-50,0,180;');
  const pts = paths[0].pts;
  assert.ok(Math.abs(pts[pts.length - 2]) < 1e-6 && Math.abs(pts[pts.length - 1]) < 1e-6);
});

test('degenerate curves draw nothing', () => {
  for (const text of ['CI0;', 'CI;', 'CI-5;', 'PU5,5;AA5,5,90;', 'PU5,5;AA0,0,0;', 'AA1,1;']) {
    assert.strictEqual(parse(text).paths.length, 0, text);
  }
});

// ---- the route ----------------------------------------------------------------------------

test('the route lists every move in order, drawn or not', () => {
  const { route } = parse('SP2;PU10,0;PD10,10;PU20,20;');
  assert.deepStrictEqual(route.seg, [0, 0, 10, 0, 10, 0, 10, 10, 10, 10, 20, 20]);
  assert.deepStrictEqual(route.meta, [4, 5, 4]);               // pen 2: up, down, up
  assert.deepStrictEqual(route.cum.map((n) => Math.round(n * 1000) / 1000), [10, 20, 34.142]);
});

test('moves with no length are not part of the route', () => {
  assert.strictEqual(parse('PU5,5;PU5,5;PD5,5;').route.meta.length, 1);
});

test('offsets say where each command starts in the text', () => {
  const text = 'IN;SP1;PU1,1;PD2,2;';
  const { route } = parse(text);
  assert.deepStrictEqual(route.off, [text.indexOf('PU1,1'), text.indexOf('PD2,2')]);
  assert.strictEqual(HPGLViewer.routeIndexAtOffset(route, 0), -1);
  assert.strictEqual(HPGLViewer.routeIndexAtOffset(route, text.indexOf('PU1,1')), 0);
  assert.strictEqual(HPGLViewer.routeIndexAtOffset(route, text.length), 1);
});

test('a command without a final terminator is still read', () => {
  assert.strictEqual(parse('PU0,0;PD5,5').paths.length, 1);
});

test('the route is searched by distance', () => {
  const { route } = parse('PU10,0;PD10,10;PU20,20;');
  assert.strictEqual(HPGLViewer.routeIndexAt(route, 0), 0);
  assert.strictEqual(HPGLViewer.routeIndexAt(route, 10), 0);
  assert.strictEqual(HPGLViewer.routeIndexAt(route, 15), 1);
  assert.strictEqual(HPGLViewer.routeIndexAt(route, 1000), 2);
});

// ---- paper --------------------------------------------------------------------------------

test('paper sizes follow the orientation and rotation of the conversion', () => {
  assert.deepStrictEqual(HPGLViewer.paperSizeMm('a4', 'portrait', '0'), { width: 210, height: 297 });
  assert.deepStrictEqual(HPGLViewer.paperSizeMm('A4', 'landscape', '0'), { width: 297, height: 210 });
  assert.deepStrictEqual(HPGLViewer.paperSizeMm('a3', 'portrait', '90'), { width: 420, height: 297 });
  assert.deepStrictEqual(HPGLViewer.paperSizeMm('a3', 'portrait', '180'), { width: 297, height: 420 });
  assert.strictEqual(HPGLViewer.paperSizeMm('a9', 'portrait', '0'), null);
});

// ---- the viewer on a fake canvas ----------------------------------------------------------

function fakeContext() {
  const calls = [];
  const ctx = new Proxy({}, {
    get(target, name) {
      if (name === 'calls') return calls;
      if (!(name in target)) target[name] = (...args) => { calls.push([name, ...args]); };
      return target[name];
    },
    set(target, name, value) { target[name] = value; return true; },
  });
  return ctx;
}

function fakeCanvas() {
  const listeners = {};
  const ctx = fakeContext();
  return {
    ctx, listeners, style: {}, width: 0, height: 0,
    parentElement: { clientWidth: 500 },
    getContext: () => ctx,
    addEventListener: (name, fn) => { listeners[name] = fn; },
    getBoundingClientRect: () => ({ left: 0, top: 0 }),
    setPointerCapture() {},
  };
}

function withBrowser(fn) {
  const frames = [];
  globalThis.window = {
    innerHeight: 800, devicePixelRatio: 2,
    getComputedStyle: () => ({ paddingLeft: '0', paddingRight: '0' }),
    requestAnimationFrame: (callback) => frames.push(callback),
    cancelAnimationFrame: () => { frames.length = 0; },
  };
  globalThis.document = { createElement: () => fakeCanvas() };
  try {
    return fn(frames);
  } finally {
    delete globalThis.window;
    delete globalThis.document;
  }
}

const TWO_PENS = 'IN;SP1;PU0,0;PD400,0,400,400;PU;SP2;PU100,100;PD300,300;PU;SP0;';

test('the viewer loads a file, fits it and draws each pen', () => withBrowser(() => {
  const canvas = fakeCanvas();
  const viewer = new HPGLViewer(canvas);
  const stats = viewer.loadHPGL(TWO_PENS);
  assert.strictEqual(stats.paths, 2);
  assert.ok(stats.travelMm > 0);
  assert.strictEqual(canvas.width, 1000);                   // 500 CSS pixels at a pixel ratio of 2
  assert.deepStrictEqual(viewer.penList().map((p) => [p.pen, p.paths, p.visible]), [[1, 1, true], [2, 1, true]]);
  assert.ok(Math.abs(viewer.penList()[0].lengthMm - 800 * 0.02488) < 0.01);
  const strokes = canvas.ctx.calls.filter((c) => c[0] === 'stroke').length;
  assert.strictEqual(strokes, 2);                           // one stroke per colour
}));

test('hiding a pen and showing travel change what is drawn', () => withBrowser(() => {
  const canvas = fakeCanvas();
  const viewer = new HPGLViewer(canvas);
  viewer.loadHPGL(TWO_PENS);
  canvas.ctx.calls.length = 0;
  viewer.setPenVisible(2, false);
  assert.strictEqual(canvas.ctx.calls.filter((c) => c[0] === 'stroke').length, 1);
  assert.strictEqual(viewer.penList()[1].visible, false);
  canvas.ctx.calls.length = 0;
  viewer.setShowTravel(true);
  assert.ok(canvas.ctx.calls.some((c) => c[0] === 'setLineDash' && c[1].length === 2));
  viewer.setPenVisible(2, true);
  assert.strictEqual(viewer.penList()[1].visible, true);
}));

test('zoom keeps the point under the pointer, pan moves, fit comes back', () => withBrowser(() => {
  const viewer = new HPGLViewer(fakeCanvas());
  viewer.loadHPGL(TWO_PENS);
  const fitted = { ...viewer.view };
  const [x, y] = viewer.toUnits(300, 120);
  viewer.zoomBy(2, 300, 120);
  assert.strictEqual(viewer.view.scale, fitted.scale * 2);
  const [px, py] = viewer.toPixels(x, y);
  assert.ok(Math.abs(px - 300) < 1e-6 && Math.abs(py - 120) < 1e-6);
  const zoomed = { ...viewer.view };
  viewer.panBy(10, -5);
  assert.strictEqual(viewer.view.tx, zoomed.tx + 10);
  assert.strictEqual(viewer.view.ty, zoomed.ty - 5);
  viewer.zoomBy(1e9);                                       // limited
  assert.ok(viewer.view.scale <= fitted.scale * 400 + 1e-9);
  viewer.zoomBy(1e-9);
  assert.ok(viewer.view.scale >= fitted.scale * 0.5 - 1e-9);
  viewer.fit();
  assert.deepStrictEqual(viewer.view, fitted);
}));

test('the pointer reports millimetres from the lower left corner of the drawing', () => withBrowser(() => {
  const canvas = fakeCanvas();
  const seen = [];
  const viewer = new HPGLViewer(canvas, { onHover: (info) => seen.push(info) });
  viewer.loadHPGL(TWO_PENS);
  const [px, py] = viewer.toPixels(200, 100);
  canvas.listeners.pointermove({ pointerId: 1, clientX: px, clientY: py });
  assert.ok(Math.abs(seen[0].xMm - 200 * 0.02488) < 1e-6 && Math.abs(seen[0].yMm - 100 * 0.02488) < 1e-6);
  assert.deepStrictEqual(viewer.crosshair, [px, py]);
  canvas.listeners.pointerleave({ pointerId: 1 });
  assert.strictEqual(seen[1], null);
  assert.strictEqual(viewer.crosshair, null);
}));

test('dragging pans the drawing and does not move the crosshair', () => withBrowser(() => {
  const canvas = fakeCanvas();
  const viewer = new HPGLViewer(canvas);
  viewer.loadHPGL(TWO_PENS);
  const before = { ...viewer.view };
  canvas.listeners.pointerdown({ pointerId: 1, clientX: 100, clientY: 100 });
  canvas.listeners.pointermove({ pointerId: 1, clientX: 130, clientY: 90 });
  canvas.listeners.pointerup({ pointerId: 1 });
  assert.strictEqual(viewer.view.tx, before.tx + 30);
  assert.strictEqual(viewer.view.ty, before.ty - 10);
}));

test('the paper outline is part of the fitted view', () => withBrowser(() => {
  const viewer = new HPGLViewer(fakeCanvas());
  viewer.loadHPGL(TWO_PENS);
  const without = viewer.fitScale;
  viewer.setPaper({ width: 210, height: 297 });
  assert.ok(viewer.fitScale < without);                     // zoomed out to show the page
  const b = viewer.viewBounds();
  assert.ok(b.maxY - b.minY >= 297 / 0.02488 - 1e-6);
  viewer.setPaper(null);
  assert.strictEqual(viewer.fitScale, without);
}));

test('the cursor draws what has been done so far, and a replay runs to the end', () => withBrowser((frames) => {
  const canvas = fakeCanvas();
  const progress = [];
  const viewer = new HPGLViewer(canvas, { onReplay: (fraction, seconds) => progress.push([fraction, seconds]) });
  viewer.loadHPGL(TWO_PENS);
  viewer.setCursor(0.5);
  assert.strictEqual(viewer.cursorFraction(), 0.5);
  assert.ok(canvas.ctx.calls.some((c) => c[0] === 'drawImage'));
  viewer.setCursor(null);
  assert.strictEqual(viewer.cursor, null);

  viewer.setCursorOffset(TWO_PENS.indexOf('PD400'));
  assert.ok(viewer.cursor > 0 && viewer.cursor < viewer.routeLength());

  viewer.setCursor(0);
  viewer.startReplay(1000);
  assert.ok(viewer.isReplaying());
  let time = 0;
  while (frames.length > 0 && time < 5000) {
    time += 50;
    frames.shift()(time);
  }
  assert.ok(!viewer.isReplaying());
  assert.strictEqual(viewer.cursorFraction(), 1);
  assert.strictEqual(progress[progress.length - 1][0], 1);
}));

test('scrubbing back redraws the progress from the start', () => withBrowser(() => {
  const viewer = new HPGLViewer(fakeCanvas());
  viewer.loadHPGL(TWO_PENS);
  viewer.setCursor(0.9);
  const before = viewer.progress.upTo;
  assert.ok(before > 0);
  viewer.setCursor(0.1);
  assert.ok(viewer.progress.upTo < before);
}));

test('an empty file draws nothing and does not throw', () => withBrowser(() => {
  const viewer = new HPGLViewer(fakeCanvas());
  const stats = viewer.loadHPGL('IN;');
  assert.strictEqual(stats.paths, 0);
  viewer.fit(); viewer.zoomBy(2); viewer.setCursor(0.5); viewer.startReplay(10);
  assert.ok(!viewer.isReplaying());
}));

// ---- CalComp .cal files -------------------------------------------------------------------
// Coordinates are steps of 0.1 mm, drawn in HPGL units
const calParse = (text) => HPGLViewer.parseCAL(text);
const S = HPGLViewer.CAL_SCALE;

test('C stores a pair and K moves to it; the pen state decides what is drawn', () => {
  const { paths, route } = calParse('R2;H;F1;C100,200;K;I;C300,200;K;C300,400;K;H;');
  assert.strictEqual(paths.length, 1);
  assert.deepStrictEqual(paths[0].pts, [100 * S, 200 * S, 300 * S, 200 * S, 300 * S, 400 * S]);
  assert.strictEqual(paths[0].pen, 1);
  assert.strictEqual(route.meta.length, 3);                    // the move with the pen up, then two strokes
  assert.deepStrictEqual(route.meta.map((m) => m & 1), [0, 1, 1]);
});

test('C alone moves nothing', () => {
  const { paths, route } = calParse('R2;I;C100,100;C200,200;');
  assert.strictEqual(paths.length, 0);
  assert.strictEqual(route.meta.length, 0);
});

test('a step is 0.1 mm', () => {
  const { paths } = calParse('H;C0,0;K;I;C2870,0;K;');
  const mm = (paths[0].pts[2] - paths[0].pts[0]) / HPGLViewer.UNITS_PER_MM;
  assert.ok(Math.abs(mm - 287) < 1e-9);
});

test('J moves relative to the pen, K is absolute', () => {
  const { paths } = calParse('C100,100;K;I;C50,0;J;C0,25;J;C10,10;K;');
  const expected = [100, 100, 150, 100, 150, 125, 10, 10].map((v) => v * S);
  assert.strictEqual(paths[0].pts.length, expected.length);
  paths[0].pts.forEach((v, i) => assert.ok(Math.abs(v - expected[i]) < 1e-9));
});

test('F n changes the pen and splits the path; F 10,n (the speed) does not', () => {
  const { paths } = calParse('C0,0;K;I;F2;C10,0;K;F10,16;C20,0;K;F3;C30,0;K;');
  assert.deepStrictEqual(paths.map((p) => p.pen), [2, 3]);
  assert.deepStrictEqual(paths[0].pts, [0, 0, 10 * S, 0, 20 * S, 0]);      // the speed did not split it
  assert.deepStrictEqual(paths[1].pts, [20 * S, 0, 30 * S, 0]);
});

test('a dot is a pen-down move that stays where it is', () => {
  const { paths } = calParse('H;C500,500;K;I;C500,500;K;H;C600,500;K;I;C600,500;K;H;');
  assert.strictEqual(paths.length, 2);
  assert.deepStrictEqual(paths[0].pts, [500 * S, 500 * S, 500 * S, 500 * S]);
  assert.deepStrictEqual(HPGLViewer.getBounds(paths), { minX: 500 * S, minY: 500 * S, maxX: 600 * S, maxY: 500 * S });
});

test('pen up moves are travel, and R2 lifts the pen', () => {
  const { paths, route } = calParse('I;C10,0;K;R2;C20,0;K;');
  assert.strictEqual(paths.length, 1);
  assert.deepStrictEqual(route.meta.map((m) => m & 1), [1, 0]);
});

test('separators and terminators: spaces, commas, semicolons, line ends', () => {
  const { paths } = calParse('H\r\nC100 200\r\nK\r\nI\r\nC300 200\r\nK\r\n');
  assert.deepStrictEqual(paths[0].pts, [100 * S, 200 * S, 300 * S, 200 * S]);
  assert.deepStrictEqual(calParse('C0,0;K;I;C1,1;K').paths[0].pts.length, 4);   // no final terminator
});

test('offsets say where each command starts in the text', () => {
  const { route } = calParse('H;C1,1;K;I;C2,2;K;');
  assert.deepStrictEqual(route.off, [7, 16]);                                        // the two K commands
});

test('commands that would place the drawing wrongly are reported, never guessed at', () => {
  const { unsupported, paths } = calParse('R2;T 4,100,100;N 5,5;W 0,0,10,10;M 1;J;C0,0;K;I;C5,5;K;');
  assert.deepStrictEqual(unsupported.sort(), ['M', 'N', 'T', 'W']);
  assert.strictEqual(paths.length, 1);
});

test('malformed arguments are ignored, never NaN', () => {
  assert.strictEqual(calParse('C1,x;K;I;C2;K;').paths.length, 0);
  for (const path of calParse('C0,0;K;I;C5,5;K;F x;C9,9;K;').paths) assert.ok(path.pts.every(Number.isFinite));
});

test('the viewer loads a .cal file', () => withBrowser(() => {
  const viewer = new HPGLViewer(fakeCanvas());
  const stats = viewer.loadHPGL('R2;H;F2;C100,100;K;I;C2870,100;K;H;', 'cal');
  assert.strictEqual(stats.paths, 1);
  assert.ok(Math.abs(stats.widthMm - 277) < 1e-6);
  assert.deepStrictEqual(viewer.penList().map((p) => p.pen), [2]);
  assert.strictEqual(viewer.loadHPGL('R2;H;', 'cal').paths, 0);
}));
