// HPGL preview: parses an HPGL file and draws the pen movements on a canvas, with zoom and pan,
// a legend of the pens, pen-up travel, the paper outline, a crosshair that reads out millimetres,
// and a cursor (for replaying the plot, or following the bytes sent to the plotter).
//
// Adapted from the HPGLViewer in https://github.com/henrytriplette/penplotter-webserver
// and ported to plain JavaScript so no build step is needed.
//
// A path is a polyline in plotter units: { pen, pts: [x0, y0, x1, y1, ...] }. The first point is
// a move, the rest are drawn. Pen-up movement starts a new path rather than extending one.
//
// The route is every movement in file order, drawn or not, as parallel arrays:
//   seg  [x0, y0, x1, y1] per move      meta  pen * 2 + (1 when the pen was down)
//   off  where the command starts in the text    cum  length travelled at the end of the move
// It is what the cursor and the replay walk along.

(function (global) {
  const CANVAS_PADDING = 20;
  const DEFAULT_CANVAS_WIDTH = 400;
  const MIN_CANVAS_WIDTH = 240;
  const MIN_CANVAS_HEIGHT = 200;
  // A preview taller than this share of the viewport would need scrolling.
  const MAX_CANVAS_HEIGHT_RATIO = 0.6;
  // vpype's HP plotter profiles use 0.02488 mm per plotter unit (the same as hpgl_analysis.py)
  const UNITS_PER_MM = 1 / 0.02488;
  // How far the view can zoom in beyond "fit", and out
  const MAX_ZOOM = 400;
  const MIN_ZOOM = 0.5;
  // A circle or arc is drawn with straight pieces of this many degrees unless the file says otherwise
  const DEFAULT_CHORD = 5;
  // How fast the replay moves at "x1": a plotter's pen speed
  const REPLAY_MM_PER_S = 380;

  // Stroke colours for pens 1, 2, 3, ... (repeating if a file uses more pens)
  const PEN_COLORS = [
    "#222222", "#d62728", "#1f77b4", "#2ca02c",
    "#ff7f0e", "#9467bd", "#8c564b", "#e377c2",
  ];
  const TRAVEL_COLOR = "#9aa0a6";
  const CURSOR_COLOR = "#e11d48";

  // Commands that don't change what is drawn (paper size, speed, line type, ...), so they are not
  // worth reporting as ignored.
  const HARMLESS_COMMANDS = new Set(["PS", "VS", "VN", "FS", "LT", "CA", "CS", "AS"]);

  function penColor(pen) {
    // Pen 1 is the first colour; pen 0 (no pen) and anything else falls back to it too
    return PEN_COLORS[pen > 0 ? (pen - 1) % PEN_COLORS.length : 0];
  }

  // Parse the numeric arguments of a single command. Returns an empty list for argument-less
  // commands (a bare "PU;") and for anything that does not parse cleanly, so callers never have to
  // deal with NaN or a missing pair member.
  function parseArgs(raw) {
    const trimmed = raw.trim();
    if (!trimmed) return [];

    const args = [];
    for (const part of trimmed.split(",")) {
      const value = Number(part.trim());
      if (!Number.isFinite(value)) return [];
      args.push(value);
    }
    return args;
  }

  // Points along an arc round (cx, cy) from (x0, y0), `sweep` degrees (counter-clockwise when
  // positive), without the start point. Same as arc_points in hpgl_analysis.py.
  function arcPoints(cx, cy, x0, y0, sweep, chord) {
    const radius = Math.hypot(x0 - cx, y0 - cy);
    if (radius === 0 || sweep === 0) return [];
    const piece = Math.min(Math.max(Math.abs(chord), 0.5), 90);
    const steps = Math.max(1, Math.ceil(Math.abs(sweep) / piece));
    const start = Math.atan2(y0 - cy, x0 - cx);
    const points = [];
    for (let i = 1; i <= steps; i++) {
      const angle = start + ((sweep * Math.PI) / 180) * (i / steps);
      points.push([cx + radius * Math.cos(angle), cy + radius * Math.sin(angle)]);
    }
    return points;
  }

  // Turn HPGL text into paths and a route. Returns { paths, unsupported, route }.
  function parseHPGL(hpgl) {
    const paths = [];
    const unsupported = new Set();
    const route = { seg: [], meta: [], off: [], cum: [] };

    let currentPath = null;
    let pen = 1;
    let penDown = false;
    let isAbsolute = true;
    let currX = 0;
    let currY = 0;
    let travelled = 0;

    const record = (x0, y0, x1, y1, down, offset) => {
      if (x0 === x1 && y0 === y1) return;
      travelled += Math.hypot(x1 - x0, y1 - y0);
      route.seg.push(x0, y0, x1, y1);
      route.meta.push(pen * 2 + (down ? 1 : 0));
      route.off.push(offset);
      route.cum.push(travelled);
    };

    // Move the pen to (x, y). With the pen down this extends the current path, starting a new one
    // from the pen's previous position when needed. With the pen up it only relocates, which ends
    // the current path.
    const moveTo = (x, y, offset) => {
      record(currX, currY, x, y, penDown, offset);
      if (penDown) {
        if (!currentPath) {
          currentPath = { pen: pen, pts: [currX, currY] };
          paths.push(currentPath);
        }
        currentPath.pts.push(x, y);
      } else {
        currentPath = null;
      }
      currX = x;
      currY = y;
    };

    // PA, PR, PU and PD may all carry coordinate pairs. A trailing unpaired value is not a
    // position, so it is ignored.
    const applyCoordinates = (args, offset) => {
      for (let i = 0; i + 1 < args.length; i += 2) {
        const x = isAbsolute ? args[i] : currX + args[i];
        const y = isAbsolute ? args[i + 1] : currY + args[i + 1];
        moveTo(x, y, offset);
      }
    };

    // A circle (CI) or an arc (AA with an absolute centre, AR with a relative one). The plotter
    // lowers the pen for them whatever the pen state is, and they leave the pen where the arc
    // ends (a circle: back at its centre).
    const curve = (code, args, offset) => {
      currentPath = null;
      if (code === "CI") {
        if (args.length === 0 || args[0] <= 0) return;
        const cx = currX;
        const cy = currY;
        record(cx, cy, cx + args[0], cy, false, offset);
        let x0 = cx + args[0];
        let y0 = cy;
        const path = { pen: pen, pts: [x0, y0] };
        for (const [x, y] of arcPoints(cx, cy, x0, y0, 360, args.length > 1 ? args[1] : DEFAULT_CHORD)) {
          record(x0, y0, x, y, true, offset);
          path.pts.push(x, y);
          x0 = x;
          y0 = y;
        }
        record(x0, y0, cx, cy, false, offset);
        if (path.pts.length > 2) paths.push(path);
        return;
      }
      if (args.length < 3) return;
      const cx = code === "AA" ? args[0] : currX + args[0];
      const cy = code === "AA" ? args[1] : currY + args[1];
      let x0 = currX;
      let y0 = currY;
      const path = { pen: pen, pts: [x0, y0] };
      for (const [x, y] of arcPoints(cx, cy, x0, y0, args[2], args.length > 3 ? args[3] : DEFAULT_CHORD)) {
        record(x0, y0, x, y, true, offset);
        path.pts.push(x, y);
        x0 = x;
        y0 = y;
      }
      if (path.pts.length > 2) paths.push(path);
      currX = x0;
      currY = y0;
    };

    const run = (text, offset) => {
      const cmd = text.trim();
      if (!cmd) return;
      const code = cmd.slice(0, 2).toUpperCase();
      const args = parseArgs(cmd.slice(2));

      switch (code) {
        case "IN":
        case "DF":
          // Initialise / set defaults: absolute mode, pen up.
          isAbsolute = true;
          penDown = false;
          currentPath = null;
          break;
        case "PA":
          isAbsolute = true;
          applyCoordinates(args, offset);
          break;
        case "PR":
          isAbsolute = false;
          applyCoordinates(args, offset);
          break;
        case "PU":
          penDown = false;
          currentPath = null;
          applyCoordinates(args, offset);
          break;
        case "PD":
          penDown = true;
          currentPath = null;
          applyCoordinates(args, offset);
          break;
        case "SP":
          // Select pen: a pen change always breaks the current path.
          currentPath = null;
          pen = args.length > 0 ? args[0] : 0;
          break;
        case "CI":
        case "AA":
        case "AR":
          curve(code, args, offset);
          break;
        default:
          if (!HARMLESS_COMMANDS.has(code)) unsupported.add(code);
      }
    };

    // Commands end at ; or a newline
    const commandEnd = /[^;\n]*[;\n]/g;
    let last = 0;
    let match;
    while ((match = commandEnd.exec(hpgl)) !== null) {
      run(match[0].slice(0, -1), match.index);
      last = commandEnd.lastIndex;
    }
    if (last < hpgl.length) run(hpgl.slice(last), last);

    return { paths: paths, unsupported: [...unsupported], route: route };
  }

  function getBounds(paths) {
    let minX = Infinity;
    let minY = Infinity;
    let maxX = -Infinity;
    let maxY = -Infinity;

    for (const path of paths) {
      const pts = path.pts;
      for (let i = 0; i < pts.length; i += 2) {
        if (pts[i] < minX) minX = pts[i];
        if (pts[i] > maxX) maxX = pts[i];
        if (pts[i + 1] < minY) minY = pts[i + 1];
        if (pts[i + 1] > maxY) maxY = pts[i + 1];
      }
    }

    if (minX === Infinity) return null;
    return { minX: minX, minY: minY, maxX: maxX, maxY: maxY };
  }

  // Length of a path, in plotter units
  function pathLength(path) {
    let length = 0;
    const pts = path.pts;
    for (let i = 2; i < pts.length; i += 2) {
      length += Math.hypot(pts[i] - pts[i - 2], pts[i + 1] - pts[i - 1]);
    }
    return length;
  }

  // The first move of the route that ends after `distance` (index into route.cum)
  function routeIndexAt(route, distance) {
    let lo = 0;
    let hi = route.cum.length - 1;
    if (hi < 0) return -1;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (route.cum[mid] < distance) lo = mid + 1;
      else hi = mid;
    }
    return lo;
  }

  // The last move that starts at or before `offset` in the text
  function routeIndexAtOffset(route, offset) {
    let lo = 0;
    let hi = route.off.length - 1;
    let found = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (route.off[mid] <= offset) {
        found = mid;
        lo = mid + 1;
      } else {
        hi = mid - 1;
      }
    }
    return found;
  }

  // Paper (width x height, mm) for a page size name, orientation and rotation of the conversion form
  const PAGE_MM = { a0: [841, 1189], a1: [594, 841], a2: [420, 594], a3: [297, 420], a4: [210, 297] };

  function paperSizeMm(size, orientation, rotate) {
    const page = PAGE_MM[String(size).toLowerCase()];
    if (!page) return null;
    let [w, h] = page;
    if (orientation === "landscape") [w, h] = [h, w];
    if (Number(rotate) === 90 || Number(rotate) === 270) [w, h] = [h, w];
    return { width: w, height: h };
  }

  class HPGLViewer {
    // options: onHover(info | null) while the pointer is over the drawing, onView() after the view
    // changes, onReplay(fraction, seconds) while a replay runs
    constructor(canvas, options) {
      this.canvas = canvas;
      this.ctx = canvas.getContext("2d");
      this.options = options || {};

      this.paths = [];
      this.route = { seg: [], meta: [], off: [], cum: [] };
      this.bounds = null;
      this.hiddenPens = new Set();
      this.showTravel = false;
      this.paper = null;            // { width, height } in mm, centred on the drawing
      this.cursor = null;           // distance along the route, or null for no cursor
      this.crosshair = null;        // pointer position in CSS pixels
      this.view = { scale: 1, tx: 0, ty: 0 };
      this.fitScale = 1;

      // Canvas size in CSS pixels. The backing store is this multiplied by the device pixel
      // ratio, so all drawing below works in CSS pixels.
      this.viewWidth = DEFAULT_CANVAS_WIDTH;
      this.viewHeight = MIN_CANVAS_HEIGHT;

      this.progress = null;         // the part of the route already drawn, for the cursor
      this.replay = null;
      this.pointers = new Map();
      this.listen();
    }

    // Parse and draw an HPGL file. Returns statistics about the drawing.
    loadHPGL(text) {
      this.stopReplay();
      const parsed = parseHPGL(text);
      this.paths = parsed.paths;
      this.route = parsed.route;
      this.hiddenPens = new Set();
      this.cursor = null;
      this.crosshair = null;

      this.bounds = getBounds(this.paths);
      this.resizeCanvas();
      this.fit();

      const bounds = this.bounds;
      return {
        paths: this.paths.length,
        points: this.paths.reduce((total, path) => total + path.pts.length / 2, 0),
        bounds: bounds,
        widthMm: bounds ? (bounds.maxX - bounds.minX) / UNITS_PER_MM : 0,
        heightMm: bounds ? (bounds.maxY - bounds.minY) / UNITS_PER_MM : 0,
        unsupported: parsed.unsupported,
        travelMm: this.routeMm(false),
      };
    }

    // ---- what the drawing holds ------------------------------------------------------------

    // Pens in order of first use: { pen, color, paths, lengthMm, visible }
    penList() {
      const pens = new Map();
      for (const path of this.paths) {
        if (!pens.has(path.pen)) {
          pens.set(path.pen, { pen: path.pen, color: penColor(path.pen), paths: 0, lengthMm: 0, visible: !this.hiddenPens.has(path.pen) });
        }
        const entry = pens.get(path.pen);
        entry.paths += 1;
        entry.lengthMm += pathLength(path) / UNITS_PER_MM;
      }
      return [...pens.values()];
    }

    // Length of the pen-down (true) or pen-up (false) moves, in mm
    routeMm(down) {
      let length = 0;
      const { seg, meta } = this.route;
      for (let i = 0; i < meta.length; i++) {
        if ((meta[i] & 1) === (down ? 1 : 0)) {
          length += Math.hypot(seg[4 * i + 2] - seg[4 * i], seg[4 * i + 3] - seg[4 * i + 1]);
        }
      }
      return length / UNITS_PER_MM;
    }

    routeLength() {
      const cum = this.route.cum;
      return cum.length ? cum[cum.length - 1] : 0;
    }

    // ---- options ---------------------------------------------------------------------------

    setPenVisible(pen, visible) {
      if (visible) this.hiddenPens.delete(pen);
      else this.hiddenPens.add(pen);
      this.progress = null;
      this.draw();
    }

    setShowTravel(show) {
      this.showTravel = !!show;
      this.progress = null;
      this.draw();
    }

    // The paper, as { width, height } in mm, or null. It is drawn centred on the drawing, which is
    // how the conversion places it (write --center).
    setPaper(paper) {
      this.paper = paper;
      this.resizeCanvas();
      this.fit();
    }

    // ---- view ------------------------------------------------------------------------------

    // The area the view shows when fitted: the drawing, and the paper if there is one
    viewBounds() {
      const b = this.bounds;
      if (!b) return null;
      if (!this.paper) return b;
      const cx = (b.minX + b.maxX) / 2;
      const cy = (b.minY + b.maxY) / 2;
      const halfW = (this.paper.width * UNITS_PER_MM) / 2;
      const halfH = (this.paper.height * UNITS_PER_MM) / 2;
      return {
        minX: Math.min(b.minX, cx - halfW),
        maxX: Math.max(b.maxX, cx + halfW),
        minY: Math.min(b.minY, cy - halfH),
        maxY: Math.max(b.maxY, cy + halfH),
      };
    }

    // Fit the canvas to its container, giving it a height that matches the drawing's own aspect
    // ratio so the result is not letterboxed. Called before every fit, so a resized window or a
    // differently shaped drawing is picked up on the next preview.
    resizeCanvas() {
      const bounds = this.viewBounds();
      let cssWidth = DEFAULT_CANVAS_WIDTH;

      // clientWidth includes the container's padding, which is not usable space.
      const container = this.canvas.parentElement;
      if (container) {
        const style = window.getComputedStyle(container);
        const padding = parseFloat(style.paddingLeft) + parseFloat(style.paddingRight);
        const available = container.clientWidth - padding;
        // Zero when the container is still hidden; fall back to the default width.
        if (available > 0) cssWidth = available;
      }
      cssWidth = Math.max(cssWidth, MIN_CANVAS_WIDTH);

      const maxHeight = Math.max(window.innerHeight * MAX_CANVAS_HEIGHT_RATIO, MIN_CANVAS_HEIGHT);

      let cssHeight = maxHeight;
      if (bounds) {
        const dx = bounds.maxX - bounds.minX;
        const dy = bounds.maxY - bounds.minY;
        if (dx > 0 && dy > 0) {
          cssHeight = (cssWidth - 2 * CANVAS_PADDING) * (dy / dx) + 2 * CANVAS_PADDING;
        }
      }
      cssHeight = Math.min(Math.max(cssHeight, MIN_CANVAS_HEIGHT), maxHeight);

      this.viewWidth = Math.round(cssWidth);
      this.viewHeight = Math.round(cssHeight);
      this.dpr = window.devicePixelRatio || 1;

      // Draw at device resolution, then scale back down with CSS, so the preview is not blurry on
      // a HiDPI screen.
      this.canvas.width = Math.round(this.viewWidth * this.dpr);
      this.canvas.height = Math.round(this.viewHeight * this.dpr);
      this.canvas.style.display = "block";
      this.canvas.style.width = this.viewWidth + "px";
      this.canvas.style.height = this.viewHeight + "px";
      this.canvas.style.touchAction = "none";      // dragging pans the drawing rather than the page
      this.progress = null;
    }

    // Show the whole drawing
    fit() {
      const bounds = this.viewBounds();
      if (!bounds) {
        this.draw();
        return;
      }
      const dx = bounds.maxX - bounds.minX;
      const dy = bounds.maxY - bounds.minY;
      // Plotter units per pixel: the larger ratio wins so the whole drawing fits. Falls back to 1
      // for a drawing with no extent, which would divide by zero.
      const usableWidth = Math.max(this.viewWidth - 2 * CANVAS_PADDING, 1);
      const usableHeight = Math.max(this.viewHeight - 2 * CANVAS_PADDING, 1);
      const unitsPerPixel = Math.max(dx / usableWidth, dy / usableHeight) || 1;
      const scale = 1 / unitsPerPixel;
      this.fitScale = scale;
      this.view = {
        scale: scale,
        tx: (this.viewWidth - dx * scale) / 2 - bounds.minX * scale,
        ty: this.viewHeight - (this.viewHeight - dy * scale) / 2 + bounds.minY * scale,
      };
      this.progress = null;
      this.draw();
      if (this.options.onView) this.options.onView();
    }

    // Zoom by `factor` keeping the point (px, py) of the canvas where it is
    zoomBy(factor, px, py) {
      if (!this.bounds) return;
      if (px === undefined) {
        px = this.viewWidth / 2;
        py = this.viewHeight / 2;
      }
      const scale = Math.min(Math.max(this.view.scale * factor, this.fitScale * MIN_ZOOM), this.fitScale * MAX_ZOOM);
      const ratio = scale / this.view.scale;
      this.view = {
        scale: scale,
        tx: px - (px - this.view.tx) * ratio,
        ty: py - (py - this.view.ty) * ratio,
      };
      this.progress = null;
      this.draw();
      if (this.options.onView) this.options.onView();
    }

    panBy(dx, dy) {
      this.view = { scale: this.view.scale, tx: this.view.tx + dx, ty: this.view.ty + dy };
      this.progress = null;
      this.draw();
    }

    // Canvas pixels to plotter units and back
    toUnits(px, py) {
      return [(px - this.view.tx) / this.view.scale, (this.view.ty - py) / this.view.scale];
    }

    toPixels(x, y) {
      return [this.view.tx + x * this.view.scale, this.view.ty - y * this.view.scale];
    }

    // ---- pointer ---------------------------------------------------------------------------

    listen() {
      const canvas = this.canvas;
      const local = (event) => {
        const rect = canvas.getBoundingClientRect();
        return [event.clientX - rect.left, event.clientY - rect.top];
      };

      canvas.addEventListener("wheel", (event) => {
        if (!this.bounds) return;
        event.preventDefault();
        const [px, py] = local(event);
        this.zoomBy(Math.pow(1.0015, -event.deltaY), px, py);
      }, { passive: false });

      canvas.addEventListener("pointerdown", (event) => {
        this.pointers.set(event.pointerId, local(event));
        this.pinch = null;
        try { canvas.setPointerCapture(event.pointerId); } catch (e) { /* not every pointer can be captured */ }
      });

      canvas.addEventListener("pointermove", (event) => {
        const point = local(event);
        if (this.pointers.has(event.pointerId)) {
          const before = this.pointers.get(event.pointerId);
          this.pointers.set(event.pointerId, point);
          if (this.pointers.size === 1) {
            this.panBy(point[0] - before[0], point[1] - before[1]);
          } else if (this.pointers.size === 2) {
            const [a, b] = [...this.pointers.values()];
            const distance = Math.hypot(a[0] - b[0], a[1] - b[1]);
            if (this.pinch) this.zoomBy(distance / this.pinch, (a[0] + b[0]) / 2, (a[1] + b[1]) / 2);
            this.pinch = distance;
          }
          return;
        }
        // Only a mouse hovers: a finger that is not down has no position
        this.crosshair = point;
        this.draw();
        this.reportHover(point);
      });

      const release = (event) => {
        this.pointers.delete(event.pointerId);
        this.pinch = null;
      };
      canvas.addEventListener("pointerup", release);
      canvas.addEventListener("pointercancel", release);
      canvas.addEventListener("pointerleave", (event) => {
        if (this.pointers.has(event.pointerId)) return;
        this.crosshair = null;
        this.draw();
        if (this.options.onHover) this.options.onHover(null);
      });
      canvas.addEventListener("dblclick", () => this.fit());
    }

    // Where the crosshair is, in mm from the lower left corner of the drawing
    reportHover(point) {
      if (!this.options.onHover || !this.bounds) return;
      const [x, y] = this.toUnits(point[0], point[1]);
      this.options.onHover({
        xMm: (x - this.bounds.minX) / UNITS_PER_MM,
        yMm: (y - this.bounds.minY) / UNITS_PER_MM,
        x: x,
        y: y,
      });
    }

    // ---- cursor and replay -----------------------------------------------------------------

    // Put the cursor at a fraction (0 to 1) of the route, or remove it with null
    setCursor(fraction) {
      if (fraction === null || fraction === undefined) {
        this.cursor = null;
      } else {
        this.cursor = Math.min(Math.max(fraction, 0), 1) * this.routeLength();
      }
      this.draw();
    }

    // Put the cursor where the plotter has got to after `offset` characters of the file were sent
    setCursorOffset(offset) {
      const index = routeIndexAtOffset(this.route, offset);
      if (index < 0) {
        this.cursor = 0;
      } else {
        this.cursor = this.route.cum[index];
      }
      this.draw();
    }

    cursorFraction() {
      const total = this.routeLength();
      return this.cursor === null || total === 0 ? 0 : this.cursor / total;
    }

    // Seconds the replay takes at "x1"
    replaySeconds() {
      return this.routeLength() / UNITS_PER_MM / REPLAY_MM_PER_S;
    }

    // Replay the plot: the cursor moves along the route `speed` times faster than the plotter
    startReplay(speed) {
      if (this.routeLength() === 0) return;
      if (this.cursor === null || this.cursor >= this.routeLength()) this.cursor = 0;
      this.stopReplay();
      this.replay = { speed: speed, last: null, frame: null };
      const step = (now) => {
        if (!this.replay) return;
        const replay = this.replay;
        if (replay.last !== null) {
          const seconds = Math.min((now - replay.last) / 1000, 0.1);
          this.cursor = Math.min(this.cursor + seconds * replay.speed * REPLAY_MM_PER_S * UNITS_PER_MM, this.routeLength());
        }
        replay.last = now;
        this.draw();
        if (this.options.onReplay) this.options.onReplay(this.cursorFraction(), this.cursor / UNITS_PER_MM / REPLAY_MM_PER_S);
        if (this.cursor >= this.routeLength()) {
          this.replay = null;
          return;
        }
        replay.frame = window.requestAnimationFrame(step);
      };
      this.replay.frame = window.requestAnimationFrame(step);
    }

    setReplaySpeed(speed) {
      if (this.replay) this.replay.speed = speed;
    }

    stopReplay() {
      if (this.replay && this.replay.frame) window.cancelAnimationFrame(this.replay.frame);
      this.replay = null;
    }

    isReplaying() {
      return this.replay !== null;
    }

    // ---- drawing ---------------------------------------------------------------------------

    // Stroke the route moves with `from <= index < to` that pass `wanted(meta)`, in one colour
    strokeRoute(ctx, from, to, wanted, color) {
      const { seg, meta } = this.route;
      ctx.strokeStyle = color;
      ctx.beginPath();
      const { scale, tx, ty } = this.view;
      for (let i = from; i < to; i++) {
        if (!wanted(meta[i])) continue;
        ctx.moveTo(tx + seg[4 * i] * scale, ty - seg[4 * i + 1] * scale);
        ctx.lineTo(tx + seg[4 * i + 2] * scale, ty - seg[4 * i + 3] * scale);
      }
      ctx.stroke();
    }

    drawPaper(ctx) {
      if (!this.paper || !this.bounds) return;
      const b = this.bounds;
      const cx = (b.minX + b.maxX) / 2;
      const cy = (b.minY + b.maxY) / 2;
      const halfW = (this.paper.width * UNITS_PER_MM) / 2;
      const halfH = (this.paper.height * UNITS_PER_MM) / 2;
      const [x0, y0] = this.toPixels(cx - halfW, cy + halfH);
      const [x1, y1] = this.toPixels(cx + halfW, cy - halfH);
      ctx.save();
      ctx.strokeStyle = "#3b82f6";
      ctx.lineWidth = 1;
      ctx.setLineDash([6, 4]);
      ctx.strokeRect(x0, y0, x1 - x0, y1 - y0);
      ctx.setLineDash([]);
      ctx.fillStyle = "#3b82f6";
      ctx.font = "11px sans-serif";
      ctx.fillText("paper " + Math.round(this.paper.width) + " x " + Math.round(this.paper.height) + " mm", x0 + 4, y0 + 13);
      ctx.restore();
    }

    // The drawing as it is when nothing is being replayed
    drawDrawing(ctx, alpha) {
      ctx.globalAlpha = alpha;
      ctx.lineWidth = 1;
      ctx.lineCap = "round";
      ctx.lineJoin = "round";

      if (this.showTravel) {
        ctx.save();
        ctx.lineWidth = 0.6;
        ctx.setLineDash([3, 3]);
        this.strokeRoute(ctx, 0, this.route.meta.length, (m) => (m & 1) === 0 && !this.hiddenPens.has(m >> 1), TRAVEL_COLOR);
        ctx.restore();
      }

      // One stroke per pen colour keeps drawing fast for files with many paths.
      const byColor = new Map();
      for (const path of this.paths) {
        if (this.hiddenPens.has(path.pen)) continue;
        const color = penColor(path.pen);
        if (!byColor.has(color)) byColor.set(color, []);
        byColor.get(color).push(path);
      }
      const { scale, tx, ty } = this.view;
      for (const [color, paths] of byColor) {
        ctx.strokeStyle = color;
        ctx.beginPath();
        for (const path of paths) {
          const pts = path.pts;
          for (let i = 0; i < pts.length; i += 2) {
            const x = tx + pts[i] * scale;
            // Flip Y: HPGL's origin is bottom left, the canvas' is top left.
            const y = ty - pts[i + 1] * scale;
            if (i === 0) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);
          }
        }
        ctx.stroke();
      }
      ctx.globalAlpha = 1;
    }

    // What the cursor has drawn so far, kept on its own canvas so a replay only adds the new moves
    updateProgress(index) {
      if (!this.progress) {
        const canvas = document.createElement("canvas");
        canvas.width = this.canvas.width;
        canvas.height = this.canvas.height;
        const ctx = canvas.getContext("2d");
        ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
        ctx.lineCap = "round";
        this.progress = { canvas: canvas, ctx: ctx, upTo: 0 };
      }
      const progress = this.progress;
      if (index < progress.upTo) {
        progress.ctx.clearRect(0, 0, this.viewWidth, this.viewHeight);
        progress.upTo = 0;
      }
      const meta = this.route.meta;
      const hidden = this.hiddenPens;
      // Same colour for runs of the same pen, so a long stretch is one stroke
      let from = progress.upTo;
      while (from < index) {
        const pen = meta[from] >> 1;
        const down = meta[from] & 1;
        let to = from + 1;
        while (to < index && meta[to] === meta[from]) to++;
        if (!hidden.has(pen) && (down || this.showTravel)) {
          progress.ctx.lineWidth = down ? 1 : 0.6;
          this.strokeRoute(progress.ctx, from, to, () => true, down ? penColor(pen) : TRAVEL_COLOR);
        }
        from = to;
      }
      progress.upTo = index;
    }

    draw() {
      const ctx = this.ctx;
      const width = this.viewWidth;
      const height = this.viewHeight;
      if (!width) return;

      ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
      ctx.clearRect(0, 0, width, height);
      ctx.fillStyle = "white";
      ctx.fillRect(0, 0, width, height);

      this.drawPaper(ctx);
      if (!this.bounds) return;

      if (this.cursor === null) {
        this.drawDrawing(ctx, 1);
      } else {
        // The whole drawing faintly, what has been drawn so far in full, and the pen on top
        this.drawDrawing(ctx, 0.15);
        const index = this.cursor <= 0 ? 0 : routeIndexAt(this.route, this.cursor) + 1;
        this.updateProgress(index);
        ctx.drawImage(this.progress.canvas, 0, 0, width, height);
        this.drawPen(ctx);
      }

      if (this.crosshair) {
        ctx.save();
        ctx.strokeStyle = "rgba(0, 0, 0, 0.35)";
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(this.crosshair[0] + 0.5, 0);
        ctx.lineTo(this.crosshair[0] + 0.5, height);
        ctx.moveTo(0, this.crosshair[1] + 0.5);
        ctx.lineTo(width, this.crosshair[1] + 0.5);
        ctx.stroke();
        ctx.restore();
      }
    }

    // The marker at the cursor's position
    drawPen(ctx) {
      const route = this.route;
      const index = routeIndexAt(route, this.cursor);
      if (index < 0) return;
      const before = index > 0 ? route.cum[index - 1] : 0;
      const length = route.cum[index] - before;
      const t = length > 0 ? Math.min(Math.max((this.cursor - before) / length, 0), 1) : 1;
      const x = route.seg[4 * index] + (route.seg[4 * index + 2] - route.seg[4 * index]) * t;
      const y = route.seg[4 * index + 1] + (route.seg[4 * index + 3] - route.seg[4 * index + 1]) * t;
      const [px, py] = this.toPixels(x, y);
      ctx.save();
      ctx.strokeStyle = CURSOR_COLOR;
      ctx.fillStyle = CURSOR_COLOR;
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.arc(px, py, 5, 0, 2 * Math.PI);
      ctx.stroke();
      ctx.beginPath();
      ctx.arc(px, py, 1.5, 0, 2 * Math.PI);
      ctx.fill();
      ctx.restore();
    }
  }

  HPGLViewer.parse = parseHPGL;
  HPGLViewer.getBounds = getBounds;
  HPGLViewer.arcPoints = arcPoints;
  HPGLViewer.routeIndexAt = routeIndexAt;
  HPGLViewer.routeIndexAtOffset = routeIndexAtOffset;
  HPGLViewer.paperSizeMm = paperSizeMm;
  HPGLViewer.UNITS_PER_MM = UNITS_PER_MM;

  global.HPGLViewer = HPGLViewer;
  if (typeof module !== "undefined" && module.exports) module.exports = HPGLViewer;
})(typeof window !== "undefined" ? window : globalThis);
