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

**Plotting.** `/start_plot` validates input, takes `plot_lock` (one plot at a time) and runs `plot()` in a Socket.IO background task, which calls `send2serial.sendToPlotter`. `plot()` always releases the lock and emits `lock_edit: off` in a `finally`. `/stop_plot` only sets `globals.printing = False`; the send loop notices and exits. Do not hold the HTTP request open for the duration of a plot.

**`send2serial.py` flow control** decides what is possible, and several branches depend on it:
- `CTS/RTS` and `Software`: the plotter is asked for its buffer size and free space (`ESC.L`, `ESC.B`); this feeds the buffer chart. `use_buffer` is true only for these.
- `XON/XOFF` (driver handshaking), `NONE` (none) and `HP-IB` (Plug n Plot, fixed 9600 baud, 1 byte at a time) have no buffer feedback. The UI sends `None`; `sendToPlotter` normalises it to `NONE`.
- CTS is polled by hand because of a pyserial bug with `rtscts`, so never enable `rtscts` for CTS/RTS mode.
- Stop sends `ESC.K` + `PU;` only when `use_buffer` is true. This is untested on real hardware.

**Realtime events to the UI** (all payloads are `{'data': ...}`, the UI reads `msg.data`): `status_log`, `error`, `bytes_written`, `print_progress`, `buffer_size`, `buffer_space`, `end_of_print`, `lock_edit`. The `connect` handler sends the current `lock_edit` state so a refreshed page is correct.

**Conversion.** `convert_vpype.convert_file` builds a vpype command line and runs it in-process (`vpype_cli.execute`). The output name encodes the options (`<file>-<size>-<orientation>[-options]-<device>.hpgl`). `layout -m 0` deliberately scales the drawing to fit the page. Custom vpype commands are inserted into the command line, so `main.py` only allows `[A-Za-z0-9 ._=+-]` and blocks `eval`, `script`, `read`, `write`, `forfile`, `include`, `show`.

**Configuration.** `config.config` is one shared `ConfigParser`. `notification.py`, `tasmota.py` and `send2serial.py` read it at call time, so a save from the UI applies without a restart. `save_configfile` validates against `CONFIG_FIELDS`, escapes `%` (configparser interpolation), and writes atomically. Keep new settings in `CONFIG_FIELDS`.

**Frontend** has no build step: `templates/index.html` loads pinned, self-hosted libraries from `static/vendor/` (UIkit 3.7.2, jQuery, Dropzone, axios, socket.io 3.1.3) then `static/main.js` (actions), `utility.js` (helpers, `escapeHtml`, `errorMessage`), `hpgl_viewer.js` (preview) and `buffer_chart.js`. UIkit 3.7.2 has a limited icon set (`eye` does not exist and renders invisibly), so check `static/vendor/uikit-icons.min.js` before using an icon. Always escape filenames and server messages (`notify` escapes; use `.text()` rather than `.html()` for user data).

## Security model (keep it intact)

- Never build a path from request data directly: use `upload_file_path()` in `main.py`, which keeps names inside `uploads/`.
- Validate with `fullmatch`, not `match`: `$` accepts a trailing newline.
- Optional HTTP basic auth via an `[auth]` section in `config.ini`; `before_request` also refuses cross-origin state-changing requests. Socket.IO connections bypass `before_request`, so `on_connect` checks auth separately. State-changing routes are POST-only.
- Debug mode, the reloader and a fixed secret key are intentionally off.

## Install and deployment

`install.sh` is run as `curl | bash`, so use `exit`, never `return`. It clones the branch `<os codename>_<32|64>` (for example `bookworm_64`) from the upstream repo; override with `WEBPLOTTER_REPO` / `WEBPLOTTER_BRANCH` (this fork only has `PiPlot`). The update path clones into `webplotter.new` first and keeps `webplotter.old` on failure. `.github/workflows/install_test.yml` rewrites the script with `sed` (`sudo reboot` and `-q `), so keep a literal `sudo reboot` line and the `-q ` flags. `.gitattributes` forces LF for shell, service and Python files: the Pi breaks on CRLF.

## Testing notes

`tests/conftest.py` copies the sources into a temp dir and imports them there with a fake pyserial (`tests/fake_serial.py`) and a stub converter, so tests never touch the repo's `config.ini` or `uploads/`. The `app` fixture resets uploads, config, serial state and `globals` for each test. When changing the plot lifecycle, drive it with the `slow_plot` fixture rather than sleeping.

`config.py`, `send2serial.py` and `convert_vpype.py` were originally committed only as compiled Python 3.11 `.pyc` files and were rebuilt from the bytecode (verified bytecode-identical before any fix). The upstream per-OS install branches may still ship compiled copies, so fixes made here do not reach them automatically.
