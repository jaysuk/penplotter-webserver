# Plan: what is left

Written for the project owner. It covers items 11 to 20 of the outstanding list. The earlier plan
(phases 0 to 4, all built) is in git history: `git show 7a38cfe:PLAN.md`.

Items 1 to 6 of that list need a plotter. Items 7 to 10 (the pages in a browser, PDF import) were done on
a Pi on 2026-10-05: see "Pi session: results" at the end, which also changed items 16, 17a and 19 below.

## Found while writing this plan (do first)

| What | Detail |
|---|---|
| **CI is red on `PiPlot`** | Two separate causes, both seen on the push of 7a38cfe. |
| Tests, Python 3.9 job | `tests/test_backup.py` used `sqlite3.Connection.deserialize`, which needs Python 3.11. The app code is fine. **Fixed locally, not pushed yet** (3.11 and 3.13 jobs passed). |
| Installer job, all three Pi images | `install.sh` stops with "Do not run this installer with sudo". The workflow runs it through `sudo -E chroot`, which passes `SUDO_USER=runner` into the chroot, and the check at `install.sh:153` reads that as `sudo bash install.sh`. The check was added in 2657c5c and CI has failed on every push since (last green: 0563e81). It is not caused by Phase 3 or 4. One run (4954213, Zero_W) also failed with apt exit code 100, which may be a separate problem. See item 16. |

## Order

1. Fix CI (item 16, first two steps). Everything else is easier to trust when CI is green.
2. Cheap and independent: 14 (Telegram token), 18c/18d (small UI gaps), 17b (file name length guard), 17c (schema version).
3. Pi session (see the end), which unlocks 16 (real installer run), 17a (conversion load) and 19 (flaky tests on Linux).
4. Decisions that need you: 11 (timelapse), 12 (MP4200 and page sizes), 13 (GPIO buttons), 15 (more options).
5. Bigger features: 17a (conversion in its own process), 18a/18b (drag and drop queue, checkpointed resume).

Sizes: S under an hour, M a few hours, L a day or more.

---

## 11. Timelapse (decision needed)

**Now:** `[timelapse]` settings, three fields in the config modal and a `/timelapse/<file>` route exist,
but nothing records. The modal already says "not used by this version yet".

**Recommendation: remove it for now (S).** Dead settings are worse than none, and it needs a camera
you do not have yet. Delete the config fields, the modal block and the route; keep reading an old
`[timelapse]` section harmlessly so old config files still load. Bring it back when a camera exists.

**If you want it (L):**
- Capture: a configurable snapshot source, either a URL (mjpg-streamer or a webcam server) or a command (`libcamera-still`, `fswebcam`). Nothing hard-coded to one camera.
- When to capture: every N seconds, or every N% of progress, plus once at each pen lift of a pen change. Run it in a background task so a slow camera never delays the serial loop.
- Store frames in `cache/timelapse/<job id>/`; assemble with `ffmpeg` after the plot (`apt install ffmpeg`, add to the installer, non-fatal like poppler); keep the mp4 in `uploads/` or a `timelapse/` folder; count it in `/storage`.
- Tests: fake snapshot source, assert frame count and that a failing camera never stops a plot.

**Needs from you:** keep or remove; if keep, which camera.

## 12. Page-size filter and the MP4200 device (decision needed)

**Now:** `updatePageSize` in `main.js` is an empty stub. The convert dialog has no MP4200 although
the plot settings and config do, and vpype 1.15 has no `mp4200` profile, so picking it fails.

**Steps (M):**
1. Check first (S): `pip index versions vpype` and the vpype changelog. A newer vpype may have an MP4200 profile; the project does not pin vpype (`requirements.txt`), so a Pi may already have a newer one than this machine.
2. If there is no profile: ship our own. vpype reads a user config (`--config`); add `devices/mp4200.toml` (page size, units, origin) and pass it from `convert_vpype.py`. Values must come from the Graphtec MP4200 manual or a real file from your plotter, not guessed. **Needs from you:** the plotter's HP-GL unit size and bed size, or a sample HPGL it produced.
3. Page-size filter: each device has a maximum paper size. Put the limits in one table next to `DEVICES` in `main.py`, send it with the page, and have `updatePageSize` hide sizes the device cannot take (e.g. A3 plotters offer A3 and below). Validate on the server too (`conversion_options`), since the page is only a convenience.
4. Tests: every device in `DEVICES` can be converted (skipped if vpype is missing); an oversize page is refused with 400; the select options follow the device.

**Without the manual:** do steps 1 and 3 only, and remove MP4200 from the convert dialog's list (it already cannot work there).

## 13. Pi Plot shield buttons

**Now:** `GPIO.add_event_detect(27/22, ...)` is commented out in `main.py`; the callbacks only log.
The shield is https://github.com/ithinkido/PiPlot.

**Plan (M), optional and off unless the hardware is there:**
- Use `gpiozero` (pure Python, on Pi OS; on Bookworm/Trixie `RPi.GPIO` is not the supported route). Import it lazily, so a Pi without it (or a Windows dev machine) is unaffected; add it to `requirements.txt`.
- Config `[gpio] enable = false`, `start_pin = 27`, `stop_pin = 22`, in `CONFIG_FIELDS` and the modal.
- Button actions (confirm these with you): **Stop** = exactly `/stop_plot` (so the queue is held); **Start** = resume if paused for a pen/paper change or reconnect, otherwise start the queue if it has files, otherwise nothing. Debounce 300 ms; ignore presses while a conversion or restore runs. Both buttons go through the same functions as the HTTP routes, never a copy of their logic.
- Tests: gpiozero's mock pin factory (`GPIOZERO_PIN_FACTORY=mock`) presses the buttons; assert the same state changes as the routes.
- Verify on the Pi in the Pi session if the shield is attached; otherwise label "untested on hardware".

**Needs from you:** are 27 and 22 right for your shield revision, and which button does what.

## 14. Telegram token is returned to the browser

**Plan (S).** Same pattern as the MQTT and login passwords:
- Add `telegram_token` to `WRITE_ONLY_FIELDS`; `GET /save_configfile` returns `telegram_token_set` instead.
- Modal: password-type field, empty placeholder "Leave empty to keep the current token". The "XXXX..." placeholder value in a fresh `config.ini` counts as "not set".
- A way to clear it: an explicit "Remove the token" checkbox, because empty already means keep.
- Tests: GET never contains the token (also not in the page source); empty keeps; the checkbox clears; a backup still restores it. Update `CLAUDE.md` and remove the `ToDo.md` item.
- Note: the token has been sent to every browser that opened the config dialog, so rotate it (BotFather `/revoke`) once this is done if the page was ever open to other people.

## 15. "More plotter options?"

Too vague to plan. **Recommendation: delete the line** unless you name something. Candidates, if you want one:
- Pen force (`FS`) and acceleration (`AS`) per plot, for plotters that support them (HP 7475A does `FS` on some models).
- Speed override at plot time instead of only at conversion (`VS` inserted by the sender).
- Copies: plot the same file N times (a queue shortcut, small once the queue exists).
- Origin offset: move the drawing on the paper at plot time (`IP`/`SC`, or a `PA` offset in the preamble).

Each is S to M plus an experimental label until tried on a plotter.

## 16. Installer CI and the installer itself

**Steps:**
1. **Fix the CI failure (S).** In `.github/workflows/install_test.yml`, `unset SUDO_USER` before running `$installer` (the workflow already knows it runs as root in a chroot). Keep the real check in `install.sh` untouched, because it protects people running `sudo bash install.sh`. Add a one-line comment in the workflow saying why.
2. **Fix my test (done locally)** and push both together, then watch all four jobs.
3. Re-run the Zero_W job and read the apt exit code 100 from 4954213 (it may be a transient mirror error, or `poppler-utils` failing). `install.sh` is supposed to treat poppler as non-fatal; confirm that.
4. Make sure the new dependency `paho-mqtt` installs on the 32-bit image (pure Python, so it should) and that `import paho.mqtt.publish` works there. Add that import to the workflow's smoke test as a non-fatal warning, like other optional pieces.
5. Add a workflow step that checks `requirements.txt` against the imports in the app (every third-party import has a line), so a missing dependency fails CI instead of a user's Pi.
6. ~~Real install on your Pi~~ **Done by you at 13:18 on 2026-10-05:** the update path ran on the Pi Zero 2 W (Trixie 64-bit, Python 3.13), `paho-mqtt` 2.1.0 installed from piwheels, `poppler-utils` was present, the service restarted on 7a38cfe and `uploads/`, `config.ini` and `history.db` were intact. So the installer works on real hardware; only the CI job is broken (steps 1 to 3).

**Needs from you:** nothing.

## 17. Phase 0 leftovers

### 17a. Conversion in its own process (M, low priority)
**Why:** `convert_file` runs vpype inside the request thread. vpype is CPU-bound Python, so it holds the GIL.
**Measured on a Pi Zero 2 W (2026-10-05):** a plot of 15 KB through a simulated plotter, with six vpype conversions running at the same time, had a longest gap between writes of 715 ms (7 gaps over 300 ms, p99 454 ms) against 200 ms (none over 300 ms) without them, and took 2.4 s longer (15.4 s against 12.8 s). That is real but small: a plotter with a 1 KB buffer should not run dry in 0.7 s. It matters mainly for fast plotters on CTS/RTS with a small buffer, and for how quickly Stop reacts.
**Plan if you want it:** run the conversion in a child process (`subprocess` running a small entry point in `convert_vpype.py`) at low priority (`nice 10`), with a timeout and a kill on cancel. Progress and the result come back as `status_log` events and a `conversion_done` event; one conversion at a time (second request gets 409). Keep the in-process path for tests. The preview route uses the same runner. **Cheaper first step (S):** refuse a second conversion while one runs, and while a plot is running warn in the dialog ("converting slows the plot a little").

### 17b. File name length (S)
**Why not a sidecar file:** presets, previews and tests all rely on the name encoding the options, and a sidecar adds a second file to keep in step (delete, backup, restore). The real risk is only a name over the filesystem limit (255 bytes).
**Plan:** compute the name, and if it is over 200 characters return 400 with a message ("too many options for the file name: shorten the file name or drop custom commands"). Test with the longest allowed custom command. Revisit the sidecar only if you ever hit that message.

### 17c. Schema version (S)
**Plan:** replace "add the missing columns" with `PRAGMA user_version` and an ordered list of migrations (the current columns are migration 1, the queue table and presets table stay `CREATE IF NOT EXISTS`). A database from a newer version than the code is refused with a clear message instead of being half used. Restore (`/restore`) already calls the init functions, so it gets migrations for free; add a test that restores a version-0 database and a version-too-new one. Do it before the next schema change, not after.

## 18. Feature gaps

### 18a. Queue drag and drop (S/M)
UIkit 3.7.2 has `uk-sortable`. Add `POST /queue/order` (`ids=3,1,2`): must list exactly the waiting items, none running, rejects anything else with 400, and renumbers `position` in one transaction. Keep the up/down arrows as the touch fallback (sortable is awkward on phones). Tests: reorder, wrong id set, running item, concurrent removal.

### 18b. Resume after a restart or power cut (M, needs hardware to prove)
**Now:** an `interrupted` plot has no offset, so it cannot be resumed.
**Plan:** checkpoint the offset into the job row while plotting, rate-limited (at most every 30 s and every 256 KB, to spare the SD card). `can_resume` then accepts `interrupted` rows, with a stronger warning in the dialog: after a power cut the plotter has lost its state and the paper may have moved. Default the rewind to the larger value because the buffer contents are unknown. **Real value needs a plotter:** whether an HP plotter keeps its origin across a power cycle decides how useful this is.

### 18c. Pen selection from a preview of another file (S)
Today "Plot only the pens shown" only works when the previewed file is already selected. Change it to: select that file (`selectFile`), wait for `loadFileInfo` to finish, then apply the ticks. Test in the viewer harness with a fake `selectFile`.

### 18d. Restore size limit (S)
The global 200 MB upload limit also caps a restore that includes uploads. First check whether it matters: add the total size of `uploads/` to the backup dialog ("this backup will be about N MB"). If real uploads stay well under 200 MB, document it and stop. If not, give `/restore` its own limit with a `Request` subclass whose `max_content_length` depends on the path.

## 19. Flaky tests and test hygiene

**What happened:** two intermittent failures, both on Windows. One was a real race (a Stop pressed just before the sender started was lost), now fixed. The other: a run that hung while two test loops ran at the same time, and a `PermissionError` when a sender thread from an earlier test still held `r.hpgl` open. These were not reproduced in 30 or so single runs.

**Pi result (2026-10-05):** three runs of the suite on the Pi Zero 2 W (Linux, ARM, real vpype so no tests skipped): 535, 535 and 534 passed. The one failure was a race in the `slow_plot` fixture (it emitted `print_progress`, which tests wait for, before `bytes_written`); fixed by emitting them the other way round. So the slower machine exposed a flake that Windows did not, which is the point of step 1.

**Plan (M):**
1. Run the suite 30 times on the Pi and on CI's Linux (a `for` loop in a throwaway workflow) to see whether anything else flakes. (3 of 30 done on the Pi.)
2. Add `pytest-timeout` to `requirements-dev.txt` and `timeout = 60` to `pytest.ini`, so a hang becomes a failure with a stack dump instead of a stuck CI run.
3. Make the thread-based tests clean up properly: the `run` fixture in `tests/test_reconnect.py` and the fakes in `tests/test_queue.py` should assert the thread has ended in teardown (not just join with a timeout), so a leaked sender fails the test that leaked it.
4. Audit tests that wait for `plot_lock.locked()` before `printing` is set (the pattern that exposed the Stop race). Replace with a helper `wait_until_plotting(app)`.
5. Add `node --test tests/theme.test.js` to the JavaScript job in `.github/workflows/tests.yml` (it currently only runs `hpgl_viewer.test.js`).

## 20. What to do with this file

Keep `PLAN.md` while items are open; delete it when the list is empty. `ToDo.md` stays the short list; this file is the reasoning. `CLAUDE.md` is updated in the same commit as each feature, as before.

---

## Pi session: results (2026-10-05, Pi Zero 2 W, Trixie 64-bit, Python 3.13)

Everything ran in a throwaway copy on port 5001 with its own config, history and uploads; the live instance was not touched and everything was removed afterwards.

**Checked and working (previously unchecked):**
- **Real serial port** (`tests/sim_plotter.py` + `tests/pi_serial_check.py`, a simulated plotter on a pseudo-terminal): a whole plot, the pen change pause with buffer feedback, stop then resume, a cable pulled and replugged (hold, 409 on resume while unplugged, reconnect, resume, the whole drawing arrives), and a queue of two. 25 of 25 checks. This also settled my worry about `.B` in the pen change wait: the source line holds a raw ESC byte, so it is correct.
- **The pages in a browser** (Chrome driven by `puppeteer-core` through an SSH tunnel): desktop and phone, light and dark, no horizontal overflow, no console errors. Preview tools (legend, travel, zoom, drag, crosshair, replay, scrub), "Watch the plot" following a real plot, pen change dialog, a second page joining a running plot, stop and Resume dialog, "Plot again", the queue with a paper change pause, config dialog (notification fields, theme), backup download and restore through the page (including a non-zip), storage line, delete-old and clear cache.
- **Real vpype conversion** of a two-layer svg: two pens in the legend and the A4 paper outline in the preview.
- **PDF import with the real poppler:** Chrome-made PDF with text, a rectangle and a circle became an svg (25 paths, 42 glyph references) and converted to 55 paths, 0.92 m.

**Bugs this found, all fixed (46878d3):**
1. With Tasmota switched off, every plot still paused 2 s before sending and held the plot lock for 30 s afterwards (the ticked "shut down when finished" box is submitted from a hidden block). Next plot or queue start got 409 for 30 s.
2. "Watch the plot" stayed hidden until some later event (`cursor_ok` was decided after the first state broadcast).
3. Phone layout: jog controls, pen select and "Add to queue" were squeezed or wrapped.
4. History rows: the buttons were clipped and the time wrapped.
5. A test fixture race (see item 19) and a Python 3.9 test failure (see the top).

**Still open from the session:**
- UIkit closes the config dialog when a confirm dialog opens over it (restore). Acceptable; it could be avoided by confirming inside the dialog.
- Software flow control sends at most about 300 B/s once the plotter's buffer is half full (`sleep(0.1)` per 30 byte chunk). That is the original behaviour, not new, but a fast plotter would be held back by it. Worth a look when you have a plotter: it may deserve a smaller sleep or a larger chunk.
- The browser flows are not in the repo (they live in a scratch folder). If you want them kept, they could go in `tests/ui/` with a README; they need Chrome and an SSH tunnel, so they would not run in CI.
- Item 9 (replay on a large real plot) was only tried on 15 to 46 KB files: it was smooth, but nothing here is a multi-megabyte vpype file.
