# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Flask + Flask-SocketIO web UI for HPGL pen plotters, run on a Raspberry Pi (the "PiPlot" branch, adapted for the [Pi Plot shield](https://github.com/ithinkido/PiPlot)). It uploads SVG/HPGL files, converts SVG to HPGL with vpype, and streams HPGL to a serial plotter with several flow control schemes. Originally https://github.com/henrytriplette/penplotter-webserver (since rewritten there with a Vite/TypeScript frontend, so changes do not merge cleanly); `ithinkido/penplotter-webserver` hosts the per-OS install branches.

## Commands

```bash
pip install -r requirements.txt -r requirements-dev.txt   # vpype needs Python >= 3.9
python3 main.py                  # serves on :5000; WEBPLOTTER_DEBUG=1 enables the Flask debugger
pytest                           # whole Python suite (no hardware needed)
pytest tests/test_send2serial.py -k stop      # a single test
node --test tests/hpgl_viewer.test.js         # JS parser tests (Node >= 18)
bash -n install.sh               # the installer can only really be tested on a Pi / the arm CI image
```

`tests/test_convert.py` runs real vpype and is skipped when vpype is not installed. There is no linter configured. `static/css/main.css` is compiled from `sass/main.sass` with laravel-mix (`webpack.mix.js`), but there is no `package.json`, so install it yourself if you need to change styles.

## Architecture

**Request flow.** `main.py` is the only Flask app. It `chdir`s to its own directory on import because `config.py`, `send2serial.py` and `convert_vpype.py` use relative paths (`config.ini`, `uploads/`). Importing `config.py` has side effects: it prints, and creates `config.ini` with defaults if missing.

**Plotting.** `/start_plot` validates input, takes `plot_lock` (one plot at a time) and runs `plot()` in a Socket.IO background task, which calls `send2serial.sendToPlotter`. `plot()` always releases the lock and emits `lock_edit: off` in a `finally`. `/stop_plot` only sets `globals.printing = False`; the send loop notices and exits. `/pause_plot` and `/resume_plot` set `globals.paused`, which makes the send loop idle (the plotter still draws what is in its buffer). Do not hold the HTTP request open for the duration of a plot.

**Plot state for late clients.** `plot()` passes a `PlotEvents` object (not the raw `socketio`) to `sendToPlotter` and tasmota; it forwards each event and records the log, progress, byte count and buffer size in `globals`. The `connect` handler emits `plot_state` (running, paused, file, progress, buffer size, log) so a refreshed page or a second device can show and stop the running plot; `start`, `end`, `pause` and `resume` broadcast `plot_state` without the log. `static/main.js` `applyPlotState` applies it. New plot-related events from `send2serial` must go through the object it is given, or a reconnecting page will not see them.

**`send2serial.py` flow control** decides what is possible, and several branches depend on it:
- `CTS/RTS` and `Software`: the plotter is asked for its buffer size and free space (`ESC.L`, `ESC.B`); this feeds the buffer chart. `use_buffer` is true only for these.
- `XON/XOFF` (driver handshaking), `NONE` (none) and `HP-IB` (Plug n Plot, fixed 9600 baud, 1 byte at a time) have no buffer feedback. The UI sends `None`; `sendToPlotter` normalises it to `NONE`.
- CTS is polled by hand because of a pyserial bug with `rtscts`, so never enable `rtscts` for CTS/RTS mode.
- Stop sends `ESC.K` + `PU;` only when `use_buffer` is true. This is untested on real hardware.

**History and power-off.** `history.py` is a SQLite log (`history.db`, next to `config.ini`, copied by the installer's update path). `plot()` records start and end (completed / stopped / failed; `init()` marks plots left `running` as `interrupted`). History errors are printed and swallowed: they must never stop a plot. `plot()` decides the outcome right after `sendToPlotter` returns, then waits `tasmota.off_delay()` before switching off; `globals.stop_requested` and `globals.plot_finished` let `/stop_plot` skip that wait (and not send a "Cancelled" message for a plot that already finished) or cancel the plot during the start-up wait. The delay is a delay, not an idle check: no verified HP-GL query tells when drawing has finished.

**Serial ports.** `send2serial.listComPorts` lists `/dev/serial/by-id/` names (via `byIdPorts`) instead of the `/dev/ttyUSBx` they point to; `/update_ports` also appends the saved default port if it is missing, so an unplugged adapter or an old `/dev/ttyUSB0` setting stays selectable.

**Realtime events to the UI** (all payloads are `{'data': ...}`, the UI reads `msg.data`): `status_log`, `error`, `bytes_written`, `print_progress`, `buffer_size`, `buffer_space`, `end_of_print`, `lock_edit`. The `connect` handler sends the current `lock_edit` state so a refreshed page is correct.

**Conversion.** `convert_vpype.convert_file` builds a vpype command line and runs it in-process (`vpype_cli.execute`). The output name encodes the options (`<file>-<size>-<orientation>[-options]-<device>.hpgl`). `layout -m 0` deliberately scales the drawing to fit the page. Custom vpype commands are inserted into the command line, so `main.py` only allows `[A-Za-z0-9 ._=+-]` and blocks `eval`, `script`, `read`, `write`, `forfile`, `include`, `show`.

**Configuration.** `config.config` is one shared `ConfigParser`. `notification.py`, `tasmota.py` and `send2serial.py` read it at call time, so a save from the UI applies without a restart. `save_configfile` validates against `CONFIG_FIELDS`, escapes `%` (configparser interpolation), and writes atomically. Keep new settings in `CONFIG_FIELDS` and add a matching `name="<field>"` input to the config modal in `index.html` (a test fails if one is missing). The login (`[auth]`) is edited there too: the password is write-only (`WRITE_ONLY_FIELDS`, never returned by GET, empty means keep), and an empty user name removes the `[auth]` section. Read config values that may be hand edited through `config_value()`, which tolerates a single `%`.

**Frontend** has no build step: `templates/index.html` loads pinned, self-hosted libraries from `static/vendor/` (UIkit 3.7.2, jQuery, Dropzone, axios, socket.io 3.1.3) then `static/main.js` (actions), `utility.js` (helpers, `escapeHtml`, `errorMessage`), `hpgl_viewer.js` (preview) and `buffer_chart.js`. UIkit 3.7.2 has a limited icon set (`eye` does not exist and renders invisibly), so check `static/vendor/uikit-icons.min.js` before using an icon. Always escape filenames and server messages (`notify` escapes; use `.text()` rather than `.html()` for user data).

## Security model (keep it intact)

- Never build a path from request data directly: use `upload_file_path()` in `main.py`, which keeps names inside `uploads/`.
- Validate with `fullmatch`, not `match`: `$` accepts a trailing newline.
- Optional HTTP basic auth via an `[auth]` section in `config.ini`; `before_request` also refuses cross-origin state-changing requests. Socket.IO connections bypass `before_request`, so `on_connect` checks auth separately. State-changing routes are POST-only.
- Debug mode, the reloader and a fixed secret key are intentionally off.

## Install and deployment

`install.sh` is run as `curl | bash`, so use `exit`, never `return`. It always installs the `PiPlot` branch of `jaysuk/penplotter-webserver` whatever the OS release (the sources run on any Python >= 3.9.2); override with `WEBPLOTTER_REPO` / `WEBPLOTTER_BRANCH`, and `WEBPLOTTER_NO_REBOOT=1` skips the final reboot. Installer output goes through the helpers at the top (`step`, `run_spin`, `ok`, `warn`, `note`): numbered steps, a spinner with elapsed time, and command output logged to `~/webplotter-install.log` (shown on failure). `ensure_sudo` asks for the password in the foreground and keeps it alive, because a prompt hidden behind a spinner looks like a hang. The installer refuses `sudo bash install.sh` and a `$HOME` not owned by the current user (the service file is written from `whoami` and `$HOME`; CI legitimately runs as root, so root only warns). `/action_reboot` and `/action_poweroff` check `sudo -n -l <cmd>` first and return a 500 with an explanation when passwordless sudo is missing. Never run `sudo` for the first time inside `run_spin`, and keep colours/cursor codes behind `ISTTY` (CI is not a terminal). All Python dependencies, including vpype, must be listed in `requirements.txt`: the installer installs that file line by line and nothing else. The update path clones into `webplotter.new` first, copies `uploads/` and `config.ini` across, and keeps `webplotter.old` on failure. `.github/workflows/install_test.yml` installs the pushed commit on emulated Pi Zero images (32 and 64 bit), starts the server, converts an SVG through it, then runs the installer again to check an update keeps uploads and config; it can only run on GitHub. `.gitattributes` forces LF for shell, service and Python files: the Pi breaks on CRLF.

## Testing notes

New Python modules must be added to `SOURCES` and `APP_MODULES` in `tests/conftest.py`. `tests/conftest.py` copies the sources into a temp dir and imports them there with a fake pyserial (`tests/fake_serial.py`) and a stub converter, so tests never touch the repo's `config.ini` or `uploads/`. The `app` fixture resets uploads, config, serial state and `globals` for each test. When changing the plot lifecycle, drive it with the `slow_plot` fixture rather than sleeping.

`config.py`, `send2serial.py` and `convert_vpype.py` were originally committed only as compiled Python 3.11 `.pyc` files and were rebuilt from the bytecode (verified bytecode-identical before any fix). The upstream per-OS install branches (`bookworm_64` etc. on `ithinkido/penplotter-webserver`) still ship compiled copies, but this repository no longer installs from them.
