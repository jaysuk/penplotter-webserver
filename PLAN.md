# Plan

Recovered from the planning session on 2026-10-05 and updated after reviewing
https://github.com/NEWTech-Creative/Roland-DXY-Plotter-WebUI (see "Viewer" below).
`CLAUDE.md` describes what is built; this file is what is left.

## Status

| Phase | State |
|---|---|
| 0 Foundations | Mostly done: `hpgl_analysis.py`, offset-aware sender, `run_commands` session helper, `plot_state` sync. **Not done:** background conversion (still runs in the request thread), conversion sidecar (options still in the file name), `schema_version` migrations in `history.py`. |
| 1 Estimate/ETA, jog panel, pens and pen-change pauses | Done (`de7ad21`, `1a6dea5`) |
| 2 Margin/rotate/mirror, preview, presets, text, PDF | Done (`4fd3358` to `4954213`). G-code input dropped: vpype 1.15 has no reader. PDF was added through poppler rather than `vpype-pdf`. |
| 3 Job control | Done: plot again, queue, resume, serial reconnect. Deviations: the queue module is `plot_queue.py` (`queue` is a stdlib module), and the queue is edited with up/down buttons rather than drag and drop. |
| 4 Operations and polish | **Next**, now includes the viewer work |

## Phase 3: job control

| Item | Detail |
|---|---|
| **Re-plot from history, S** | Add an `options` JSON column to `jobs` (port, baud, flow control, Tasmota, pens, pen change mode). "Plot again" re-posts to `/start_plot` if the file still exists. Needs a guarded `ALTER TABLE` (see the migrations item above). |
| **Job queue, M-L** | `queue` table plus `queue.py`. A runner calls `plot()` per entry. `plot()` takes `power_on` (first job only) and `power_off` (last job only) so Tasmota is not toggled between jobs. Optional "pause for paper change" per entry reuses `paused` and `wait_reason`. Stop cancels the current job and holds the queue. UI: list with reorder and remove. |
| **Resume after stop or failure, L** | Save `resume_offset` in history. Buffer modes: bytes written minus buffered bytes (`bufsz - bufsp`), rounded back to a safe boundary. Other modes: imprecise, offer "rewind N KB". The resume file is the preamble (`IN;`, `SP`, `VS`) plus pen state at the offset, then the rest. Remember vpype output is in `PR` mode: resync with an absolute move (as `filter_pens` does). The user confirms the carriage has not moved. |
| **Serial reconnect, M** | Builds on resume. On `SerialException` mid-plot, auto-pause with `wait_reason = "disconnected"`, retry with backoff, offer "Reconnect and continue". No silent re-send. |

Open question: should Stop during a queue hold it (planned) or clear it?

## Phase 4: operations and polish

| Item | Detail |
|---|---|
| Notifications | Per-event toggles in `CONFIG_FIELDS` (start, finish, error, pen change, every N%) with config modal inputs. Optional webhook and MQTT (`paho-mqtt`, pure Python). Send from a background task with short timeouts. |
| Storage | `shutil.disk_usage`, size and age in the file list, "delete older than N days" (confirm, skip the running file). |
| Backup and restore | Zip of `config.ini`, `history.db` (sqlite backup API), optionally uploads. Restore refuses while plotting, validates via `CONFIG_FIELDS`, integrity-checks the db, guards against zip-slip. |
| `/api/status` | Read-only GET: `plot_state`, ETA, queue. Behind the existing auth. |
| Dark mode and mobile | Hand-written `static/theme.css` with CSS variables. Check phone width. |
| **Viewer** (new, below) | Replaces the old single "pen cursor on preview" item. |

### Viewer: what the Roland DXY WebUI showed, and what we take

Its standalone `hpgl_plotter_viewer.html` is simpler than ours (single colour, no legend, 0.2-4x zoom slider,
no crosshair). The main app's visualiser adds pen layers, a crosshair in mm, and a motion simulation with
a speed multiplier. Our viewer (`static/hpgl_viewer.js`) already has per-pen colours and a HiDPI canvas, but
no zoom, no travel moves, no legend and no cursor. Worth adding, in this order (one commit each):

1. **Pen legend with show/hide per pen.** Swatch, pen number, drawn length from `analysis.segments`.
   Also lets the user tick pens for `/start_plot`'s `pens` field straight from the preview.
2. **Pen-up travel toggle.** Dashed grey lines. The parser records travel moves, which makes wasted travel
   visible next to the time estimate. Off by default (large files); sampled or capped over a point limit.
3. **Zoom and pan.** Wheel/pinch zoom around the pointer, drag to pan, "fit" button. Redraw from the
   already-parsed paths, no re-parse. Needed for dense vpype output on a phone.
4. **Hover crosshair with coordinates in mm** (and the plotter units). Read-only: no click-to-move, because a
   misclick would drive the pen.
5. **Paper outline.** In the conversion preview, draw the page rectangle, so the placement from margin,
   rotate and mirror is visible. Skipped for uploaded HPGL where the page size is unknown.
6. **Pen cursor** (was already planned). Parser keeps source offsets; a marker follows `bytes_written`,
   labelled "sent position" because the plotter lags by its buffer. Shares code with item 7.
7. **Replay** with a speed multiplier and a scrub bar, driven by `analysis.marks` (offset to seconds).
   Lowest priority: the time model is a guess, so this is for looking, not for timing.
8. **Arcs and circles (`CI`, `AA`, `AR`).** Not from the DXY repo's viewer (it lacks them too) but a real gap:
   the parser lists them as unsupported, so HPGL uploaded from other software draws incomplete. Add to both
   `hpgl_viewer.js` and `hpgl_analysis.py`, with tests in `tests/hpgl_viewer.test.js` and the Python suite.

Not taken: the drawing editor, fills and patterns, handwriting, image vectorising, live tracker and Web Serial
(this app is server side and converts with vpype), the DXY unit/bed defaults (ours come from the device).

## Cross-cutting rules

- New modules go in `SOURCES` and `APP_MODULES` in `tests/conftest.py`.
- New settings go in `CONFIG_FIELDS` with a matching `name="..."` input in `index.html`.
- State-changing routes are POST, use `upload_file_path`, validate with `fullmatch`.
- Plot events go through `PlotEvents`.
- Update `CLAUDE.md` in the same commit as each feature; commit per feature.
- No hardware here: jog, pen change, resume and reconnect are tested against `fake_serial` only and are labelled experimental.
- Pi Zero: stream files, cap preview sizes.
