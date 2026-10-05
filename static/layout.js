// Where the panels of the page are: which column each one is in and in what order, which are hidden or
// folded up, and the widths of the side columns. Kept in this browser (localStorage), like the theme.
// This file is the pure part (no DOM), so node can test it; static/layout-ui.js applies it to the page.
(function (global) {
  const KEY = "webplotter-layout";
  const VERSION = 1;
  const COLUMNS = ["left", "center", "right"];
  const MIN_WIDTH = 220;
  const MAX_WIDTH = 560;

  // Every panel on the page (data-panel in index.html) and the column it starts in
  const DEFAULT_COLUMNS = {
    left: ["files", "upload", "queue"],
    center: ["preview", "status", "history"],
    right: ["settings", "control", "buffer", "system"],
  };
  const PANELS = [].concat(DEFAULT_COLUMNS.left, DEFAULT_COLUMNS.center, DEFAULT_COLUMNS.right);

  function defaults() {
    return {
      v: VERSION,
      cols: { left: DEFAULT_COLUMNS.left.slice(), center: DEFAULT_COLUMNS.center.slice(), right: DEFAULT_COLUMNS.right.slice() },
      hidden: [],
      folded: {},
      lw: 300,
      rw: 330,
    };
  }

  function clampWidth(value, fallback) {
    const n = Number(value);
    if (!isFinite(n)) return fallback;
    return Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, Math.round(n)));
  }

  // Anything stored or built by hand becomes a valid layout: unknown and repeated panels are dropped, a panel
  // that is missing (a newer version of the page has one more) goes back to the column it starts in
  function normalise(saved) {
    const layout = defaults();
    if (!saved || typeof saved !== "object" || saved.v !== VERSION) return layout;

    const seen = new Set();
    const cols = { left: [], center: [], right: [] };
    for (const name of COLUMNS) {
      const list = saved.cols && Array.isArray(saved.cols[name]) ? saved.cols[name] : [];
      for (const id of list) {
        if (PANELS.indexOf(id) >= 0 && !seen.has(id)) {
          seen.add(id);
          cols[name].push(id);
        }
      }
    }
    for (const name of COLUMNS) {
      for (const id of DEFAULT_COLUMNS[name]) {
        if (!seen.has(id)) cols[name].push(id);
      }
    }
    layout.cols = cols;
    layout.hidden = Array.isArray(saved.hidden) ? saved.hidden.filter((id, i, all) => PANELS.indexOf(id) >= 0 && all.indexOf(id) === i) : [];
    if (saved.folded && typeof saved.folded === "object") {
      for (const id of PANELS) if (saved.folded[id] === true) layout.folded[id] = true;
    }
    layout.lw = clampWidth(saved.lw, layout.lw);
    layout.rw = clampWidth(saved.rw, layout.rw);
    return layout;
  }

  function copy(layout) {
    return normalise(JSON.parse(JSON.stringify(layout)));
  }

  // The panels of a column that can be seen
  function visible(layout, column) {
    return layout.cols[column].filter((id) => layout.hidden.indexOf(id) < 0);
  }

  function columnOf(layout, id) {
    return COLUMNS.find((name) => layout.cols[name].indexOf(id) >= 0);
  }

  // The order found in the page after a drag: only the panels that can be seen are in the page, so the hidden
  // ones are put back in their column right after the panel they used to follow
  function withVisible(layout, shown) {
    const cols = {};
    for (const name of COLUMNS) {
      const list = (shown && Array.isArray(shown[name]) ? shown[name] : []).filter((id) => layout.hidden.indexOf(id) < 0);
      const result = list.slice();
      let anchor = -1;
      for (const id of layout.cols[name]) {
        if (layout.hidden.indexOf(id) >= 0) {
          result.splice(anchor + 1, 0, id);
          anchor += 1;
        } else if (result.indexOf(id) >= 0) {
          anchor = result.indexOf(id);
        }
      }
      cols[name] = result;
    }
    return normalise(Object.assign({}, layout, { cols: cols }));
  }

  function setHidden(layout, id, hidden) {
    const next = copy(layout);
    if (PANELS.indexOf(id) < 0) return next;
    next.hidden = next.hidden.filter((other) => other !== id);
    if (hidden) next.hidden.push(id);
    return next;
  }

  function setFolded(layout, id, folded) {
    const next = copy(layout);
    if (PANELS.indexOf(id) < 0) return next;
    if (folded) next.folded[id] = true;
    else delete next.folded[id];
    return next;
  }

  function setWidth(layout, side, pixels) {
    const next = copy(layout);
    if (side === "lw" || side === "rw") next[side] = clampWidth(pixels, next[side]);
    return next;
  }

  // Move a panel one step: up and down swap it with the visible neighbour, left and right put it in the next
  // column at the same height (or last if that column is shorter)
  function shift(layout, id, direction) {
    const next = copy(layout);
    const column = columnOf(next, id);
    if (!column) return next;
    const list = next.cols[column];

    if (direction === "up" || direction === "down") {
      const shown = visible(next, column);
      const at = shown.indexOf(id);
      const other = shown[at + (direction === "up" ? -1 : 1)];
      if (at < 0 || other === undefined) return next;
      const a = list.indexOf(id);
      const b = list.indexOf(other);
      list[a] = other;
      list[b] = id;
      return next;
    }

    const to = COLUMNS[COLUMNS.indexOf(column) + (direction === "right" ? 1 : -1)];
    if (!to) return next;
    const row = Math.max(0, visible(next, column).indexOf(id));
    list.splice(list.indexOf(id), 1);
    const target = next.cols[to];
    const shown = visible(next, to);
    // Before the panel that is at that height now, or at the end
    const before = shown[row];
    target.splice(before === undefined ? target.length : target.indexOf(before), 0, id);
    return next;
  }

  function load(storage) {
    try {
      const raw = (storage || global.localStorage).getItem(KEY);
      return raw ? normalise(JSON.parse(raw)) : defaults();
    } catch (e) {
      return defaults();          // blocked storage, or something that is not JSON
    }
  }

  function save(layout, storage) {
    try {
      (storage || global.localStorage).setItem(KEY, JSON.stringify(layout));
    } catch (e) { /* the layout then only lasts until the page is reloaded */ }
  }

  function forget(storage) {
    try {
      (storage || global.localStorage).removeItem(KEY);
    } catch (e) { /* nothing to forget */ }
  }

  const api = {
    KEY: KEY, COLUMNS: COLUMNS, PANELS: PANELS, MIN_WIDTH: MIN_WIDTH, MAX_WIDTH: MAX_WIDTH,
    defaults: defaults, normalise: normalise, visible: visible, columnOf: columnOf, withVisible: withVisible,
    setHidden: setHidden, setFolded: setFolded, setWidth: setWidth, shift: shift,
    load: load, save: save, forget: forget,
  };
  global.WebPlotterLayout = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
