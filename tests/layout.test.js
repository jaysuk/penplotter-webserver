// Run with: node --test tests/layout.test.js
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const layout = require(path.join(__dirname, '..', 'static', 'layout.js'));

// A tiny localStorage
function memory(initial) {
  const data = Object.assign({}, initial);
  return {
    getItem: (k) => (k in data ? data[k] : null),
    setItem: (k, v) => { data[k] = String(v); },
    removeItem: (k) => { delete data[k]; },
    data: data,
  };
}

const all = (l) => [].concat(l.cols.left, l.cols.center, l.cols.right);

test('the default layout has every panel exactly once and nothing hidden', () => {
  const l = layout.defaults();
  assert.deepStrictEqual(all(l).slice().sort(), layout.PANELS.slice().sort());
  assert.deepStrictEqual(l.hidden, []);
  assert.deepStrictEqual(l.folded, {});
  assert.deepStrictEqual(layout.normalise(l), l);
});

test('the page has a panel for every id in the module, and no other', () => {
  const html = fs.readFileSync(path.join(__dirname, '..', 'templates', 'index.html'), 'utf8');
  const inPage = [...html.matchAll(/data-panel="([a-z]+)"/g)].map((m) => m[1]).sort();
  assert.deepStrictEqual(inPage, layout.PANELS.slice().sort());
  // and each one starts in the column the module says
  for (const name of layout.COLUMNS) {
    const column = html.split('data-col="' + name + '"')[1].split('data-col=')[0];
    const ids = [...column.matchAll(/data-panel="([a-z]+)"/g)].map((m) => m[1]);
    assert.deepStrictEqual(ids, layout.defaults().cols[name], name);
  }
});

test('normalise accepts nothing that is not a layout', () => {
  const d = layout.defaults();
  for (const junk of [null, undefined, 5, 'x', [], {}, { v: 2 }, { v: 1, cols: 'no' }]) {
    const l = layout.normalise(junk);
    assert.deepStrictEqual(all(l).slice().sort(), layout.PANELS.slice().sort());
  }
  assert.deepStrictEqual(layout.normalise(null), d);
  assert.deepStrictEqual(layout.normalise({ v: 2, cols: { left: ['status'] } }), d);   // another version
});

test('normalise drops unknown and repeated panels and puts missing ones back in their own column', () => {
  const l = layout.normalise({
    v: 1,
    cols: { left: ['status', 'nope', 'status', 'files'], center: 'x', right: ['settings', 'files'] },
    hidden: ['control', 'control', 'ghost', 5],
    folded: { files: true, status: 'yes', ghost: true },
    lw: 5000, rw: 'wide',
  });
  assert.deepStrictEqual(l.cols.left, ['status', 'files', 'upload', 'queue']);
  assert.deepStrictEqual(l.cols.center, ['preview', 'history']);
  assert.deepStrictEqual(l.cols.right, ['settings', 'control', 'buffer', 'system']);
  assert.deepStrictEqual(all(l).slice().sort(), layout.PANELS.slice().sort());
  assert.deepStrictEqual(l.hidden, ['control']);
  assert.deepStrictEqual(l.folded, { files: true });
  assert.strictEqual(l.lw, layout.MAX_WIDTH);
  assert.strictEqual(l.rw, 330);
});

test('widths are kept between the limits', () => {
  let l = layout.defaults();
  assert.strictEqual(layout.setWidth(l, 'lw', 10).lw, layout.MIN_WIDTH);
  assert.strictEqual(layout.setWidth(l, 'rw', 9999).rw, layout.MAX_WIDTH);
  assert.strictEqual(layout.setWidth(l, 'lw', 301.6).lw, 302);
  assert.strictEqual(layout.setWidth(l, 'lw', NaN).lw, 300);
  assert.deepStrictEqual(layout.setWidth(l, 'center', 400), l);
});

test('hiding and folding change a copy, and undo cleanly', () => {
  const l = layout.defaults();
  const h = layout.setHidden(layout.setHidden(l, 'files', true), 'files', true);
  assert.deepStrictEqual(h.hidden, ['files']);
  assert.deepStrictEqual(l.hidden, []);
  assert.deepStrictEqual(layout.setHidden(h, 'files', false), l);
  assert.deepStrictEqual(layout.setHidden(l, 'ghost', true), l);
  const f = layout.setFolded(l, 'queue', true);
  assert.deepStrictEqual(f.folded, { queue: true });
  assert.deepStrictEqual(layout.setFolded(f, 'queue', false), l);
});

test('a hidden panel keeps its place for when it is shown again', () => {
  let l = layout.setHidden(layout.defaults(), 'upload', true);
  assert.deepStrictEqual(layout.visible(l, 'left'), ['files', 'queue']);
  assert.deepStrictEqual(l.cols.left, ['files', 'upload', 'queue']);
});

test('withVisible takes the order found in the page after a drag', () => {
  const l = layout.withVisible(layout.defaults(), { left: ['upload'], center: ['preview', 'status', 'files', 'history'], right: ['settings', 'control', 'buffer', 'system', 'queue'] });
  assert.deepStrictEqual(l.cols.center, ['preview', 'status', 'files', 'history']);
  assert.deepStrictEqual(l.cols.right.slice(-1), ['queue']);
  assert.deepStrictEqual(l.cols.left, ['upload']);
  // a panel lost in the page comes back in its own column
  const lost = layout.withVisible(layout.defaults(), { left: [], center: [], right: [] });
  assert.deepStrictEqual(lost.cols, layout.defaults().cols);
  assert.deepStrictEqual(layout.withVisible(layout.defaults(), null).cols, layout.defaults().cols);
});

test('withVisible puts hidden panels back after the panel they followed', () => {
  let l = layout.setHidden(layout.setHidden(layout.defaults(), 'upload', true), 'system', true);
  // the left column now shows files and queue; queue is dragged above files, and a panel is dragged in at the end
  const next = layout.withVisible(l, { left: ['queue', 'files', 'status'], center: ['history'], right: ['settings', 'control', 'buffer'] });
  assert.deepStrictEqual(next.cols.left, ['queue', 'files', 'upload', 'status']);   // upload followed files
  assert.deepStrictEqual(next.cols.right, ['settings', 'control', 'buffer', 'system']);
  assert.deepStrictEqual(next.hidden, ['upload', 'system']);
  // a hidden panel at the top of a column stays at the top
  l = layout.setHidden(layout.defaults(), 'files', true);
  const top = layout.withVisible(l, { left: ['queue', 'upload'], center: ['status', 'history'], right: ['settings', 'control', 'buffer', 'system'] });
  assert.deepStrictEqual(top.cols.left, ['files', 'queue', 'upload']);
  // a hidden panel is never taken from the page's list, even if it is still in there
  const stale = layout.withVisible(l, { left: ['files', 'upload', 'queue'], center: ['status', 'history'], right: ['settings', 'control', 'buffer', 'system'] });
  assert.deepStrictEqual(stale.cols.left, ['files', 'upload', 'queue']);
  assert.deepStrictEqual(all(stale).slice().sort(), layout.PANELS.slice().sort());
});

test('shift moves a panel past its visible neighbour, and between columns', () => {
  let l = layout.setHidden(layout.defaults(), 'upload', true);
  l = layout.shift(l, 'files', 'down');                       // upload is hidden: it goes past queue
  assert.deepStrictEqual(layout.visible(l, 'left'), ['queue', 'files']);
  assert.deepStrictEqual(layout.shift(l, 'queue', 'up'), l);  // already first
  l = layout.shift(layout.defaults(), 'queue', 'right');      // third in left: goes before the third in the centre
  assert.deepStrictEqual(l.cols.center, ['preview', 'status', 'queue', 'history']);
  assert.deepStrictEqual(l.cols.left, ['files', 'upload']);
  l = layout.shift(layout.defaults(), 'upload', 'right');     // second row: before the second in the centre
  assert.deepStrictEqual(l.cols.center, ['preview', 'upload', 'status', 'history']);
  l = layout.shift(layout.shift(layout.shift(layout.defaults(), 'files', 'right'), 'queue', 'right'), 'upload', 'right');
  assert.deepStrictEqual(layout.visible(l, 'left'), []);        // a column can end up empty
  assert.deepStrictEqual(layout.shift(layout.defaults(), 'files', 'left'), layout.defaults());
  assert.deepStrictEqual(layout.shift(layout.defaults(), 'system', 'right'), layout.defaults());
  assert.deepStrictEqual(layout.shift(layout.defaults(), 'ghost', 'left'), layout.defaults());
  assert.deepStrictEqual(all(l).slice().sort(), layout.PANELS.slice().sort());
});

test('a panel moved into an empty-looking column lands in it', () => {
  let l = layout.defaults();
  for (const id of ['preview', 'status', 'history']) l = layout.setHidden(l, id, true);
  l = layout.shift(l, 'files', 'right');
  assert.deepStrictEqual(layout.visible(l, 'center'), ['files']);
});

test('load and save round trip, and bad or blocked storage gives the default layout', () => {
  const store = memory();
  assert.deepStrictEqual(layout.load(store), layout.defaults());
  const l = layout.setFolded(layout.shift(layout.defaults(), 'queue', 'right'), 'status', true);
  layout.save(l, store);
  assert.deepStrictEqual(layout.load(store), l);
  layout.forget(store);
  assert.deepStrictEqual(layout.load(store), layout.defaults());
  assert.deepStrictEqual(layout.load(memory({ [layout.KEY]: '{not json' })), layout.defaults());
  assert.deepStrictEqual(layout.load(memory({ [layout.KEY]: '"hello"' })), layout.defaults());
  const blocked = { getItem() { throw new Error('blocked'); }, setItem() { throw new Error('blocked'); }, removeItem() { throw new Error('blocked'); } };
  assert.deepStrictEqual(layout.load(blocked), layout.defaults());
  assert.doesNotThrow(() => layout.save(l, blocked));
  assert.doesNotThrow(() => layout.forget(blocked));
});
