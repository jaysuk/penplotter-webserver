// HPGL preview: parses a (simple) HPGL file and draws the pen movements on a canvas.
//
// Adapted from the HPGLViewer in https://github.com/henrytriplette/penplotter-webserver
// and ported to plain JavaScript so no build step is needed.
//
// A path is a polyline in plotter units: { pen, pts: [x0, y0, x1, y1, ...] }. The first point is
// a move, the rest are drawn. Pen-up movement starts a new path rather than extending one.

(function (global) {
  const CANVAS_PADDING = 20;
  const DEFAULT_CANVAS_WIDTH = 400;
  const MIN_CANVAS_WIDTH = 240;
  const MIN_CANVAS_HEIGHT = 200;
  // A preview taller than this share of the viewport would need scrolling.
  const MAX_CANVAS_HEIGHT_RATIO = 0.6;
  // Standard HPGL plotter units: 40 per millimetre.
  const UNITS_PER_MM = 40;

  // Stroke colours for pens 1, 2, 3, ... (repeating if a file uses more pens)
  const PEN_COLORS = [
    "#222222", "#d62728", "#1f77b4", "#2ca02c",
    "#ff7f0e", "#9467bd", "#8c564b", "#e377c2",
  ];

  // Commands that don't change what is drawn (paper size, speed, line type, ...), so they are not
  // worth reporting as ignored.
  const HARMLESS_COMMANDS = new Set(["PS", "VS", "VN", "FS", "LT", "CA", "CS", "AS"]);

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

  // Turn HPGL text into paths. Returns { paths, unsupported }.
  function parseHPGL(hpgl) {
    const commands = hpgl
      .split(/;|\n/)
      .map((cmd) => cmd.trim())
      .filter(Boolean);

    const paths = [];
    const unsupported = new Set();

    let currentPath = null;
    let pen = 1;
    let penDown = false;
    let isAbsolute = true;
    let currX = 0;
    let currY = 0;

    // Move the pen to (x, y). With the pen down this extends the current path, starting a new one
    // from the pen's previous position when needed. With the pen up it only relocates, which ends
    // the current path.
    const moveTo = (x, y) => {
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
    const applyCoordinates = (args) => {
      for (let i = 0; i + 1 < args.length; i += 2) {
        const x = isAbsolute ? args[i] : currX + args[i];
        const y = isAbsolute ? args[i + 1] : currY + args[i + 1];
        moveTo(x, y);
      }
    };

    for (const cmd of commands) {
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
          applyCoordinates(args);
          break;
        case "PR":
          isAbsolute = false;
          applyCoordinates(args);
          break;
        case "PU":
          penDown = false;
          currentPath = null;
          applyCoordinates(args);
          break;
        case "PD":
          penDown = true;
          currentPath = null;
          applyCoordinates(args);
          break;
        case "SP":
          // Select pen: a pen change always breaks the current path.
          currentPath = null;
          pen = args.length > 0 ? args[0] : 0;
          break;
        default:
          if (!HARMLESS_COMMANDS.has(code)) unsupported.add(code);
      }
    }

    return { paths: paths, unsupported: [...unsupported] };
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

  class HPGLViewer {
    constructor(canvas) {
      this.canvas = canvas;
      this.ctx = canvas.getContext("2d");

      this.paths = [];

      // Canvas size in CSS pixels. The backing store is this multiplied by the device pixel
      // ratio, so all drawing below works in CSS pixels.
      this.viewWidth = DEFAULT_CANVAS_WIDTH;
      this.viewHeight = MIN_CANVAS_HEIGHT;
    }

    // Parse and draw an HPGL file. Returns statistics about the drawing.
    loadHPGL(text) {
      const parsed = parseHPGL(text);
      this.paths = parsed.paths;

      const bounds = getBounds(this.paths);
      this.drawOnCanvas(bounds);

      return {
        paths: this.paths.length,
        points: this.paths.reduce((total, path) => total + path.pts.length / 2, 0),
        bounds: bounds,
        widthMm: bounds ? (bounds.maxX - bounds.minX) / UNITS_PER_MM : 0,
        heightMm: bounds ? (bounds.maxY - bounds.minY) / UNITS_PER_MM : 0,
        unsupported: parsed.unsupported,
      };
    }

    // Fit the canvas to its container, giving it a height that matches the drawing's own aspect
    // ratio so the result is not letterboxed. Called before every draw, so a resized window or a
    // differently shaped drawing is picked up on the next preview.
    resizeCanvas(bounds) {
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

      // Draw at device resolution, then scale back down with CSS, so the preview is not blurry on
      // a HiDPI screen.
      const dpr = window.devicePixelRatio || 1;
      this.canvas.width = Math.round(this.viewWidth * dpr);
      this.canvas.height = Math.round(this.viewHeight * dpr);
      this.canvas.style.display = "block";
      this.canvas.style.width = this.viewWidth + "px";
      this.canvas.style.height = this.viewHeight + "px";

      // Assigning width/height above resets the context, so apply the transform that lets
      // everything below work in CSS pixels afterwards.
      this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }

    drawOnCanvas(bounds) {
      // Size first: changing the canvas dimensions clears it and resets the context, so anything
      // drawn beforehand would be discarded.
      this.resizeCanvas(bounds);

      const ctx = this.ctx;
      const width = this.viewWidth;
      const height = this.viewHeight;

      ctx.clearRect(0, 0, width, height);
      ctx.fillStyle = "white";
      ctx.fillRect(0, 0, width, height);

      if (!bounds) return;

      const dx = bounds.maxX - bounds.minX;
      const dy = bounds.maxY - bounds.minY;

      // Plotter units per pixel: the larger ratio wins so the whole drawing fits. Falls back to 1
      // for a drawing with no extent, which would divide by zero.
      const usableWidth = Math.max(width - 2 * CANVAS_PADDING, 1);
      const usableHeight = Math.max(height - 2 * CANVAS_PADDING, 1);
      const scale = Math.max(dx / usableWidth, dy / usableHeight) || 1;

      // Centre the drawing on the canvas.
      const offsetX = (width - dx / scale) / 2;
      const offsetY = (height - dy / scale) / 2;

      ctx.lineWidth = 1;
      ctx.lineCap = "round";
      ctx.lineJoin = "round";

      // One stroke per pen colour keeps drawing fast for files with many paths.
      const byPen = new Map();
      for (const path of this.paths) {
        // Pen 1 is the first colour; pen 0 (no pen) and anything else falls back to it too
        const color = PEN_COLORS[path.pen > 0 ? (path.pen - 1) % PEN_COLORS.length : 0];
        if (!byPen.has(color)) byPen.set(color, []);
        byPen.get(color).push(path);
      }

      for (const [color, paths] of byPen) {
        ctx.strokeStyle = color;
        ctx.beginPath();
        for (const path of paths) {
          const pts = path.pts;
          for (let i = 0; i < pts.length; i += 2) {
            const x = offsetX + (pts[i] - bounds.minX) / scale;
            // Flip Y: HPGL's origin is bottom left, the canvas' is top left.
            const y = height - offsetY - (pts[i + 1] - bounds.minY) / scale;

            if (i === 0) {
              ctx.moveTo(x, y);
            } else {
              ctx.lineTo(x, y);
            }
          }
        }
        ctx.stroke();
      }
    }
  }

  HPGLViewer.parse = parseHPGL;
  HPGLViewer.getBounds = getBounds;

  global.HPGLViewer = HPGLViewer;
  if (typeof module !== "undefined" && module.exports) module.exports = HPGLViewer;
})(typeof window !== "undefined" ? window : globalThis);
