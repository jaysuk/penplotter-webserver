# Plan: the Console interface

Replaces the card grid, left sidebar and off-canvas menu with the "Plotter Console" design chosen from the
three mockups: a transport bar that always shows the plot, and three columns of panels that can be dragged
between columns, folded, hidden and resized. The mockup is a throwaway: it simulates a plot and draws fake
HPGL. Nothing from its JavaScript is reused except the layout ideas; the real panels keep their markup and
`static/main.js` handlers.

Sizes: S under an hour, M a few hours, L a day or more.

**Progress:** all four phases are built and checked in desktop Chrome (not committed). Phase 4 deleted `static/css/main.css`, `sass/`, `webpack.mix.js` and the two unused images (`bg.jpg`, `user.png`); `#modal-wait` was kept (D3): a held plot still opens it and also shows in the transport bar, and dropping the dialog is a one-line decision for later. Decisions D4, D5 and D6 were taken as recommended; D6's deletion of `main.css`, `sass/` and `webpack.mix.js` waits for phase 4. Phase 3 changes from the plan: the preview dialog is gone and the panel is in the centre column's first place; selecting a file loads it only when the panel is on screen, and does not replace an unsaved conversion; Watch the plot is a toggle ("Stop watching"); the viewer file is unchanged (the panel refits it with a `ResizeObserver` in `main.js`). A bug from phase 1 was fixed on the way: the bytes sent in the transport bar were parsed as a number, but the server sends text. Phase 2 changes from the plan: dragging uses UIkit's sortable (D2 turned out fine across columns, including with touch events); hidden panels move to an off-screen container instead of being hidden in place, because UIkit's sortable did not accept a drop into a column whose only children were hidden; the layout stores each column's full order including hidden panels, so a panel comes back where it was; the chips keep a fixed order. The Reset button is in the chips bar and in the settings dialog. Phase 1 changes from the plan: the Preview panel is not there yet, so the centre column holds Print status and Plot history only; the panel header has no move, fold or hide buttons until phase 2; fonts are four files (Barlow 400 and 600, Barlow Semi Condensed 600, DM Mono 400), about 83 KB.

## What changes and what does not

**No backend change.** No route, socket event, config field or Python module is touched. The page is built
from the same `plot_state`, `eta`, `queue_state`, `pen_change`, `lock_edit` and other events it handles now
(`templates/index.html` lines 1329 onward), so serial and plot behaviour cannot regress.

**Kept as they are:** every panel's inner markup and class names (`.startPlot`, `.pausePlot`, `.jogButton`,
`#fileList`, `#queueList`, `#historyList`, `#statusLog` and so on), all of `main.js`'s delegated handlers,
`utility.js`, `hpgl_viewer.js`, the Smoothie buffer chart, and every UIkit modal (convert, config, text
drawing, wait, resume, reboot, power off). The modals are restyled, not rebuilt.

**Replaced:** the `<header>`, `<aside id="left-col">`, `#offcanvas-nav`, the `uk-sortable` card grid, the
`mac-card` card chrome, and the open/close card buttons (`closeCard`/`showCard`).

**New:** a transport bar, a panel shell with a handle, fold and hide buttons, a panel chip bar, column
dividers, a saved layout (`static/layout.js`), and `static/css/console.css`.

## Decisions (recommendations are what the plan assumes)

| # | Question | Recommendation | Why |
|---|---|---|---|
| D1 | Preview: a permanent panel, or keep the modal? | **Permanent panel.** The viewer moves from `#modal-previewFile` into the panel. Convert's Preview button shows its result there, with Change options and Save as HPGL under it. | It is the centre of the mockup, and "Watch the plot" needs it visible. It is the largest piece of work (phase 3), so phases 1 and 2 ship without it. |
| D2 | Drag implementation | **UIkit sortable with `group`** (already vendored, handles touch), after a half-hour spike. Fall back to the pointer-event code in the mockup if columns misbehave. | No new drag code to maintain. Not yet checked against UIkit 3.7.2 in columns. |
| D3 | Pen/paper change notices | **Keep the blocking modal for now**, and also show the message in the transport bar. Drop the modal later if the bar proves enough. | A held plot is the one moment the user may be away from the screen. |
| D4 | Fonts | **Self-host two woff2 files** (a condensed label face and a mono face, both OFL) in `static/vendor/fonts/`, with system fallbacks. | The Pi is often on a workshop network with no internet; every other library here is self-hosted. |
| D5 | Default theme | **Change the default from Light to Auto.** Needs one assertion changed in `tests/theme.test.js`. | The Console is dark-first, and Auto follows the device. The saved choice is untouched for anyone who already picked. |
| D6 | Stylesheet | **New hand-written `console.css`; retire `main.css`, `sass/` and `webpack.mix.js` at the end** (phase 4). | `main.css` is compiled from sass with no `package.json` here, which is awkward to edit. `theme.css` is already hand-written. |
| D7 | Where the layout is saved | **Per browser (`localStorage`).** | A phone and a desktop want different layouts. The same reason the theme choice is per browser. |

## Panel map

| Mockup panel | Real source today | Default column |
|---|---|---|
| Files | "Uploaded Files" card: `#fileList`, storage line, delete old, clear cache | left |
| Upload | "File Upload" card: dropzone, text drawing button | left |
| Plot queue | "Plot Queue" card | left |
| Preview | viewer from `#modal-previewFile` (phase 3) | centre |
| Print status | "Print Status" card: log, Watch the plot. Progress, ETA, Pause and Stop move to the transport bar | centre |
| Plot history | "Plot History" card | centre |
| Plotter settings | "Plotter Settings" card incl. Start plot, Add to queue, pen choices, the Tasmota shutdown checkbox (`#tasmota_control`) | right |
| Plotter control | "Plotter Control" card | right |
| Buffer space | sidebar `#chartContents` (Smoothie, keep it; restyle the colours) | right |
| System | sidebar ACTIONS (Tasmota toggle, Edit config, Reboot, Power off) and the bottom icon bar | right |

The transport bar reuses the existing classes (`.printProgress`, `.etaText`, `.selectedFilename`,
`.pausePlot`, `.resumePlot`, `.stopPlot`, `.startPlot`) so `applyPlotState` and `setEta` keep working. Because
those classes are currently duplicated in the sidebar and the off-canvas menu, removing both leaves one set.

## Phase 1: the shell (L)

Goal: the page looks like the Console with a fixed layout. Nothing is draggable yet.

1. `static/css/console.css`: design tokens as custom properties under `:root[data-theme="dark"]` and
   `:root[data-theme="light"]`. `theme.js` always sets one of the two, so the `prefers-color-scheme`
   blocks from the mockup are not needed. Component rules (panel, pill, progress bar, log, list rows,
   table) are scoped under a `.console` class on `<body>`, so the old CSS keeps working until phase 4.
2. Fonts (D4) and an inline SVG sprite for the icons. UIkit 3.7.2 has no grip, eye or chart icon
   (`CLAUDE.md` warns about this), so the mockup's small icon set becomes `<symbol>`s at the top of
   `index.html`. UIkit icons stay where they already work.
3. `index.html`: replace the header, sidebar and off-canvas with the transport bar, then three
   `<div class="console-col" data-col="...">` in the default arrangement. Each existing card becomes
   `<section class="panel" data-panel="id">` with a header (`h2`, handle, fold, hide) around its existing
   body. Drop the `chart-container`, `mac-*` and `uk-card` classes from the panels.
4. Transport bar wiring in `main.js`: `applyPlotState` also sets the state pill (idle, plotting, paused,
   pen_change, paper_change, disconnected, reconnect, taken from `running`, `paused` and `wait_reason`),
   the pen, the bytes sent, and the message line. `setEta` also fills a separate `.etaClock` with
   `m:ss`. Start, Pause, Resume and Stop show and hide as they do now.
5. Restyle the modals (convert, preview, config, text, wait, resume, reboot, power off) to the new tokens
   by overriding `.uk-modal-dialog` and the `mac-browser-bar` title bars. Their markup stays.
6. Remove `data-card` ids and the `closeCard`/`showCard` handlers; panels get fold and hide in phase 2.
7. Small screens (under about 860 px): columns become `display: contents` and each panel gets an `order`
   from one fixed list (transport bar, preview, status, settings, control, files, upload, queue, history,
   buffer, system), so a phone shows the useful panels first instead of the left column first.

Done when: every existing action works from the new page (upload, convert, preview, start, pause, resume,
stop, queue, history, jog, config, reboot), checked by hand at desktop, tablet and phone widths in both
themes. `pytest` and the node tests still pass.

## Phase 2: arrangeable layout (M)

1. `static/layout.js`, a pure module with the same UMD pattern as `theme.js` so node can test it:
   - `DEFAULT`: columns `left: files, upload, queue`; `center: preview, status, history`;
     `right: settings, control, buffer, system`; `hidden: []`; `folded: {}`; `lw: 300`; `rw: 330`.
   - `normalise(saved)`: drops unknown panel ids, removes duplicates, puts any panel missing from the saved
     layout back in its default column (so a later release can add a panel), clamps the widths to 220–560,
     and falls back to `DEFAULT` for anything malformed or from a different `v`.
   - `move(layout, id, column, index)`, `setHidden`, `setFolded`, `reset`, and `load`/`save` with the same
     blocked-storage `try/catch` as `theme.js`. Key: `webplotter-layout`, schema `{"v":1,"cols":{...},"hidden":[],"folded":{},"lw":300,"rw":330}`.
2. `static/layout-ui.js` (needs the DOM): applies a layout to the page by moving the existing panel
   elements, wires fold and hide, the chip bar (one chip per panel, pressed means shown), the two column
   dividers (pointer drag, and arrow keys when focused), the drag between columns (D2) and keyboard moving
   (arrow keys on the handle: up and down within a column, left and right across columns). It runs right
   after the columns markup, before `main.js`, so the saved layout is in place before first paint.
3. "Reset layout" button in the config modal next to the Appearance choice, and in the chip bar.
4. A panel that is hidden or folded has no size: anything that draws into it (the buffer chart, and the
   viewer in phase 3) must skip drawing at zero size and redraw when shown.

Done when: a panel can be dragged to another column with the mouse and with touch, moved with the
keyboard, folded, hidden and restored from the chips; widths survive a reload; a corrupt saved value gives
the default layout; blocked storage still gives a working page.

## Phase 3: inline preview (M to L)

1. Move `#hpglCanvas` and the viewer controls (coordinates, Fit, pen-up travel, legend, replay bar,
   "Plot only the pens shown") into the Preview panel. `HPGLViewer` already sizes itself from its
   container (`resizeCanvas`), so add a resize observer on the panel rather than rewriting it.
2. Selecting a file in the list loads its preview into the panel (today it only fills the info box).
   `previewFile`, `showPreview` and `loadPreview` change from "open a modal" to "fill the panel"; if the
   panel is hidden or folded, show it first.
3. Convert → Preview closes the convert modal and shows the unsaved result in the panel, with the
   Change options and Save as HPGL buttons under it (the current `#previewActions`). Change options
   reopens the convert modal. The paper outline stays specific to conversion previews, as now.
4. "Watch the plot" becomes a toggle on the panel instead of opening a modal; the live cursor still
   depends on `plot_state.cursor_ok`.
5. Empty state for an SVG ("convert it to see the plot") and for no file.

Done when: every viewer feature still works in the panel (zoom, pan, pen legend, travel, replay, hover
coordinates, watch the plot, use these pens), and `tests/hpgl_viewer.test.js` passes unchanged.

## Phase 4: cleanup (S to M)

1. Remove the old sidebar, off-canvas and card markup that is no longer used, and the `.mac-*`, sidebar and
   `#content` rules. With D6: delete `static/css/main.css`, `sass/main.sass` and `webpack.mix.js`, and
   point the page at `console.css` alone. Keep `theme.css` only if anything is still in it.
2. Optional (D3): show held-plot messages only in the transport bar and delete `#modal-wait`.
3. Update `CLAUDE.md` (Frontend, Theme and the stylesheet note in Commands) and tick this off in `PLAN.md`.
4. Replace the stale footer ("Copyright 2021 ... Henry Triplette") only if the owner wants; it is not part
   of this work.

## Tests

- **Python:** `tests/test_routes.py` already checks that the page loads, that every config field has an
  input (`name="<field>"`) and that `theme.css`, `theme.js` and `id="themeChoice"` are present; keep those
  ids. Add one test that the page contains a `data-panel` section for each panel id in a list that
  `layout.js` also uses, so the markup and the module cannot drift apart. `tests/test_pdf.py` checks the
  `acceptedFiles` string in the page; keep that text.
- **JavaScript:** `tests/layout.test.js` for `layout.js` (normalise, move, hidden, folded, clamping, corrupt
  and unavailable storage). If D5 is taken, change the default assertion in `tests/theme.test.js`.
  `.github/workflows/tests.yml` only runs `tests/hpgl_viewer.test.js`; add `tests/theme.test.js` and
  `tests/layout.test.js` there (`CLAUDE.md` already lists theme).
- **By hand, in a real browser** (nothing here has been seen in one yet, and `CLAUDE.md` says the same of the
  current theme): desktop, tablet and phone widths; light and dark; mouse, touch and keyboard drag; a full
  plot with a pen change pause; a refresh and a second device mid-plot; the queue with a paper change; a
  stopped plot resumed from history.
- `cypress/integration/webplot_test.spec.js` is not maintained (it expects a file the repo does not have).
  Keep the class names it uses (`.updateFiles`, `.convertFile`, `.startConversion`, `.selectFile`) and leave it.

## Risks and things not yet checked

- **UIkit sortable across containers (D2)** is untested here. The spike in phase 2 decides it.
- **`color-mix()`**, used in the mockup for tinted backgrounds, needs a browser from 2023 or later. If an
  old tablet matters, write the tints out as plain colours in the tokens.
- **Hidden panels that draw** (chart, viewer): the main source of bugs in phase 2 and 3. Each one skips
  zero-size draws and redraws on show.
- **Many panel moves while a plot runs** move DOM nodes that `main.js` updates by selector; that is safe
  because the nodes are moved, never recreated, but the log scroll position is lost when a panel is
  re-attached. Keep the log's scroll-to-bottom behaviour in `scrollLog`.
- **Layout flash:** the saved layout must be applied before first paint (phase 2, step 2), or the default
  layout shows for a moment on every load.
- **Mockup versus real content:** the real panels are denser than the mockup's (convert and preview buttons
  on every file row, the history table's Resume and Plot again buttons, queue pause markers). Expect to
  adjust spacing once real data is in.

## Out of scope

Server-side layout storage, changing any panel's behaviour, the convert, config and text-drawing forms
themselves (restyled only), the Python side, timelapse, and any change to plot, queue, resume or reconnect
logic.

## Order of work

Phase 1 → phase 2 → merge (the page is then the Console with a working layout) → phase 3 → phase 4. Do it
on a branch; phase 1 on its own changes the look but not the behaviour, so it is also a safe first commit.
