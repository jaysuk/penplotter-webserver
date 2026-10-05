# Plan: what is left

Written for the project owner. Everything built so far (queue, resume, reconnect, viewer, notifications,
storage, backup, status API, theme) is described in `CLAUDE.md`; the finished plans are in git history
(`git show 7a38cfe:PLAN.md` for phases 0 to 4, `git show 5259d4a:PLAN.md` for the Pi session results).
`ToDo.md` is the short list; this file is the reasoning.

**State on 2026-10-05:** `PiPlot` is pushed and in sync with `origin`. CI is green (tests on Python 3.9,
3.11 and 3.13; JavaScript tests; the installer on Zero W, Zero 2 W and Zero 2 W 64-bit). The Pi Zero 2 W
runs the code, 535 tests pass there, and the real serial code has been exercised against a simulated
plotter (`tests/pi_serial_check.py`, 25 of 25 checks). Nothing has run on a real plotter.

Sizes: S under an hour, M a few hours, L a day or more.

## Order

1. **When a plotter is connected:** the checklist in section A. It decides whether several features work.
2. **Needs a decision from you** (section B): timelapse, MP4200 and page sizes, the GPIO buttons, "more options".
3. **Can be done now, no input needed** (section C): Telegram token, name length guard, schema version, small UI gaps, test hygiene, CI dependency check.
4. **Later, only if wanted** (section D): conversion in its own process, resume after a power cut, drag and drop queue.

---

## A. Needs a real plotter

None of this can be proved without hardware. The simulator (`tests/sim_plotter.py`) answers the queries
and drains a buffer, and has no CTS line and no pen, paper or mechanics. Run `tests/pi_serial_check.py`
first as a smoke test, then the real thing; each line below says what to look for.

| # | Check | What to look for | If it fails |
|---|---|---|---|
| A1 | **Stop** (`ESC.K` then `PU;`) in each flow control mode | The pen lifts and the plotter goes quiet within a second. Buffer modes (CTS/RTS, Software) abort the buffer; XON/XOFF, None and HP-IB do not send `ESC.K`, so the plotter keeps drawing what it holds: is that what you want? | Send `PU;` (and `ESC.K` where the plotter supports it) in the other modes too. |
| A2 | **Pause and Resume** | The plotter finishes its buffer, then idles; Resume carries on with no gap or repeat. | Note how much it draws after Pause; consider lowering the chunk size while paused. |
| A3 | **Pen change pause** with a hand-fitted holder | Pause happens at the pen boundary, the pen lifts (`PU;`), the dialog names the pen, and the plot continues in the new pen. Never `SP0`. | Check `SP` handling on that model. |
| A4 | **Jog, pen up/down, pick pen, trace plot area, where is the pen?** | The distances match the step size (this depends on the HP-GL unit size: 40 or 40.2 units per mm), the trace draws the real bounds, and `OA;` answers. | Wrong distances mean the unit constant (`UNITS_PER_MM`) is wrong for that plotter: make it a device setting. |
| A5 | **Resume accuracy** | Stop a plot half way, change nothing, Resume with rewind 0: is there a gap or an overdraw at the join? Try rewind 256 B and 1 KB without buffer feedback. | Tune the defaults in the resume dialog (0 with buffer feedback, 1 KB without). |
| A6 | **Reconnect** | Pull the USB adapter mid-plot, replug: the plot holds, reconnects, and after Resume the join is clean. | Tune `REWIND_NO_FEEDBACK` (1024) and `REWIND_HPIB` (64). |
| A7 | **Queue** | Two files with Tasmota on: the plotter is switched on once and off after the last, and a paper change pause holds between them. | See the Tasmota notes in `CLAUDE.md`. |
| A8 | **Tasmota power off** | The delay (default 30 s) is long enough for the last strokes. There is no verified HP-GL query for "finished drawing". | Try `OA;` polling for position stability, or `ESC.B` returning the full buffer plus a quiet period. |
| A9 | **CTS/RTS** | The hand-polled CTS (`getCTS`) works, since the simulator cannot test it. | Never enable pyserial's `rtscts` here (known bug); see `CLAUDE.md`. |
| A10 | **Software flow control throughput** | The sender sleeps 0.1 s per 30 byte chunk once the plotter's buffer is more than half full, which caps it at about 300 B/s. A fast plotter could be held back. Compare the speed of a dense plot with another sender. | Shorter sleep or larger chunk when the buffer is nearly empty; measure first. |
| A11 | **Resume after a power cut** | Whether the plotter keeps its origin across a power cycle decides how useful D2 would be. | Skip D2. |
| A12 | **HP-IB (Plug n Plot)** | 1 byte at a time at 9600 baud works and is not impossibly slow. | Larger chunks if the adapter buffers. |

## B. Needs a decision from you

### B1. Timelapse (decision)
**Now:** `[timelapse]` settings, three fields in the config modal and a `/timelapse/<file>` route exist, but nothing records. The modal says "not used by this version yet".
**Recommendation: remove it for now (S).** Dead settings are worse than none, and it needs a camera you do not have. Delete the config fields, the modal block and the route; keep reading an old `[timelapse]` section harmlessly so old config files still load.
**If you want it (L):**
- Capture from a configurable source: a snapshot URL (mjpg-streamer, a webcam server) or a command (`libcamera-still`, `fswebcam`). Nothing hard-coded to one camera.
- When: every N seconds or every N% of progress, plus each pen change. In a background task, so a slow camera never delays the serial loop.
- Frames in `cache/timelapse/<job id>/`, assembled with `ffmpeg` after the plot (add to the installer, non-fatal like poppler), the mp4 counted in `/storage`.
- Tests: a fake snapshot source; a failing camera never stops a plot.
**Needs from you:** keep or remove; if keep, which camera.

### B2. Page-size filter and the MP4200 device (decision)
**Now:** `updatePageSize` in `main.js` is an empty stub. The convert dialog has no MP4200 although the plot settings and config do, and vpype 1.15 has no `mp4200` profile, so choosing it fails.
**Steps (M):**
1. Check first (S): whether a newer vpype has an MP4200 profile (`pip index versions vpype`, the changelog). The project does not pin vpype, so a fresh Pi may already have a newer one; the Pi here has 1.15.0.
2. If not, ship our own profile: vpype reads a user config (`--config`), so add `devices/mp4200.toml` (page size, units, origin) and pass it from `convert_vpype.py`. The values must come from the MP4200 manual or a real HPGL file from your plotter, not guessed.
3. Page-size filter: a table of maximum paper size per device next to `DEVICES` in `main.py`, sent with the page; `updatePageSize` hides sizes a device cannot take; `conversion_options` refuses them too.
4. Tests: every device in `DEVICES` converts (skipped without vpype); an oversize page is 400; the select follows the device.
**Without the manual:** do 1 and 3 only, and remove MP4200 from the convert dialog.
**Needs from you:** the plotter's HP-GL unit size and bed size, or a sample HPGL it produced.

### B3. Pi Plot shield buttons (decision, optional)
**Now:** the GPIO code in `main.py` is commented out and only logs. The shield is https://github.com/ithinkido/PiPlot, pins 27 and 22 in the old code.
**Plan (M):** `gpiozero` imported lazily (so a Pi without it, or Windows, is unaffected; add it to `requirements.txt`); `[gpio] enable = false`, `start_pin`, `stop_pin` in `CONFIG_FIELDS` and the modal. **Stop** = exactly `/stop_plot` (so the queue is held). **Start** = resume if the plot is paused for a pen change, paper change or reconnect, otherwise start the queue if it has files, otherwise nothing. 300 ms debounce; ignored while a restore runs. Both go through the same functions as the HTTP routes. Tests use gpiozero's mock pin factory.
**Needs from you:** whether 27 and 22 are right for your shield revision, and which button does what. Verify on the Pi if the shield is attached.

### B4. "More plotter options?" (decision)
Too vague to plan. **Recommendation: delete the line** in `ToDo.md` unless you name something. Candidates: pen force (`FS`) and acceleration (`AS`) per plot; a speed override at plot time (`VS` inserted by the sender); copies (a queue shortcut); an origin offset at plot time. Each is S to M, and experimental until tried on a plotter.

## C. Can be done now

### C1. Telegram token is returned to the browser (S)
Same pattern as the MQTT and login passwords. Add `telegram_token` to `WRITE_ONLY_FIELDS`; `GET /save_configfile` returns `telegram_token_set`; the modal field becomes a password field with the placeholder "Leave empty to keep the current token" (the sample `XXXX...` value counts as not set); an explicit "Remove the token" checkbox, because empty already means keep. Tests: the GET and the page source never contain the token; empty keeps it; the checkbox clears it; a backup still restores it. Update `CLAUDE.md`; remove the `ToDo.md` line. Once done, rotate the token (BotFather `/revoke`) if the page was ever open to other people, because the old behaviour sent it to every browser that opened the dialog.

### C2. File name length guard (S)
Conversion options are encoded in the output file name (presets, previews and tests rely on that, so no sidecar file). The only real risk is a name over the filesystem limit (255 bytes). If the computed name is over 200 characters return 400 with a clear message, and test it with the longest allowed custom command. Revisit a sidecar only if that message is ever hit.

### C3. Schema version (S)
Replace "add the missing columns" in `history.py` with `PRAGMA user_version` and an ordered list of migrations (today's columns are migration 1; the queue and presets tables stay `CREATE IF NOT EXISTS`). A database from a newer version is refused with a clear message instead of being half used. `/restore` already calls the init functions, so it gets migrations for free: test restoring a version 0 database and a too-new one. Do this before the next schema change, not after.

### C4. Small UI gaps (S each)
- **Pen selection from a preview of another file:** "Plot only the pens shown" only works if the previewed file is already selected. Make it select the file (`selectFile`), wait for `loadFileInfo`, then apply the ticks.
- **Restore size:** the global 200 MB upload limit also caps a restore that includes uploads. First show the size of `uploads/` in the backup dialog ("about N MB"); if real uploads stay well under 200 MB, document it and stop; otherwise give `/restore` its own limit (a `Request` subclass whose `max_content_length` depends on the path).
- **Confirm over the config dialog:** UIkit closes the config dialog when a confirm opens over it (restore). Acceptable; confirm inside the dialog if it bothers you.
- **Convert while plotting:** warn in the convert dialog that converting slows a running plot a little (measured: longest gap between writes 715 ms against 200 ms), and refuse a second conversion while one runs.

### C5. CI dependency checks (S)
- Import `paho.mqtt.publish` in the installer workflow's smoke test as a non-fatal warning (the jobs prove it installs, not that it imports).
- A step that compares `requirements.txt` with the app's third-party imports, so a missing dependency fails CI instead of a user's Pi.
- If the Raspbian mirror outage (apt exit 100, seen once) recurs, retry `apt-get install` in the workflow.

### C6. Test hygiene (M)
- Add `pytest-timeout` to `requirements-dev.txt` and `timeout = 60` to `pytest.ini`, so a hang is a failure with a stack dump, not a stuck CI run.
- Run the suite 30 times on the Pi and on CI's Linux (3 of 30 done on the Pi: 535, 535, 534 passed; the one failure was a fixture race, fixed) to see whether anything else flakes.
- Thread-based tests (`tests/test_reconnect.py`, `tests/test_queue.py`) should assert in teardown that their sender thread has ended, so a leaked thread fails the test that leaked it (a leaked thread caused a Windows `PermissionError` once).
- Replace "wait for `plot_lock.locked()`" before `printing` is set with a `wait_until_plotting(app)` helper (that pattern hid the Stop race).
- Add `node --test tests/theme.test.js` to the JavaScript job in `.github/workflows/tests.yml` (it only runs `hpgl_viewer.test.js`).
- Decide whether the browser flows used on 2026-10-05 (Chrome driven by `puppeteer-core` through an SSH tunnel; they now live outside the repo) should go in `tests/ui/` with a README. They need Chrome and a running instance, so they would not run in CI.

## D. Later, only if wanted

### D1. Conversion in its own process (M, low priority)
`convert_file` runs vpype inside the request thread and holds the GIL. **Measured on the Pi Zero 2 W:** with six conversions running during a 15 KB plot the longest gap between writes was 715 ms (7 gaps over 300 ms) against 200 ms without, and the plot took 2.4 s longer. Real but small; it matters mostly for fast CTS/RTS plotters with a small buffer, and for how quickly Stop reacts. If wanted: run it in a child process at `nice 10` with a timeout and a kill on cancel, progress as `status_log` events plus a `conversion_done` event, one conversion at a time (a second gets 409), the in-process path kept for tests, the preview route using the same runner. C4's warning is the cheap first step.

### D2. Resume after a restart or power cut (M, needs A11)
An `interrupted` plot has no offset, so it cannot be resumed. Checkpoint the offset into the job row while plotting (at most every 30 s and every 256 KB, to spare the SD card), let `can_resume` accept `interrupted` rows with a stronger warning (after a power cut the plotter has lost its state and the paper may have moved), and default the rewind to the larger value. Worth doing only if A11 shows the plotter keeps its origin.

### D3. Drag and drop queue (S/M)
UIkit 3.7.2 has `uk-sortable`. Add `POST /queue/order` (`ids=3,1,2`) that must list exactly the waiting items (none running), reject anything else with 400, and renumber `position` in one transaction. Keep the up/down arrows as the touch fallback. Tests: reorder, wrong id set, running item, concurrent removal.

### D4. Other things noticed
- Replay was smooth on 15 to 46 KB files; nothing here was a multi-megabyte vpype file. Try one before relying on it for large plots.
- `install.sh` is untouched by the CI fixes: its two guards (no `sudo bash install.sh`, `$HOME` must belong to the user) protect real users.

---

## Housekeeping

Keep `PLAN.md` while items are open and delete it when the list is empty. Update `CLAUDE.md` in the same commit as each feature, and tick the matching line in `ToDo.md`.
