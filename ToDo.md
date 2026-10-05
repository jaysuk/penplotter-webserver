- [x] File Upload
  - [x] Also file management? Add, Delete
- [x] File Conversion
- [x] Print interface
  - [x] Setting print params
  - [x] List com ports
  - [x] Set file
- [x] Fix vpype conversion isse when running as a service
- [x] Autoscroll console
- [x] Remote pi shutdown
- [x] Add tasmota control in sidebar
- [x] Update config.ini via web interface
  - [x] Every setting in config.ini can be edited (login, timelapse included)
- [x] Disable file deletion while printing
- [x] Stop print via UI
- [x] List current printing filename
- [x] A refreshed page, or a second device, picks up a running plot (file, progress, log, stop)
- [x] Pause / resume a plot
- [x] Plot history (kept in history.db, survives restarts and installer updates)
- [x] Tasmota: configurable wait before switching off (default 30 s) and after switching on (default 2 s); Stop skips the wait
- [x] USB serial adapters are listed by their stable /dev/serial/by-id/ name

Open:

- [ ] Tasmota power off is a delay, not a check that the plotter is idle. I found no documented HP-GL
      query that reliably says "finished drawing", so this needs real hardware to improve on.
- [ ] Stop (`ESC.K` + `PU;`) and pause have not been tried on real hardware.
- [ ] Timelapse: the settings exist in config.ini (and the UI) and there is a `/timelapse/<file>`
      route, but nothing records anything. Implement it or remove it.
- [ ] Page size filter per plotter (stub `updatePageSize` in main.js, not used). The convert dialog
      also lacks the MP4200 device that the plot settings and config have.
- [ ] Pi Plot shield buttons: the GPIO code in main.py is commented out and only logs a message.
- [ ] The Telegram token is still sent back to the browser in clear text by `GET /save_configfile`
      (the login password is not).
- [x] Plot queue: several files in a row, with an optional paper change between them. Stop holds it. Untested on a real plotter, like the other plot control.
- [x] Plot history: "Plot again" from a history row (a deleted file stays listed by name, without the button)
- [x] Resume a stopped or failed plot from the history (carries on from the byte the plotter reached; untested on hardware)
- [x] A dropped serial connection holds the plot and offers to reconnect and continue (untested on hardware)
- [x] Notifications: per-event toggles, progress every N percent, webhook and MQTT, a test button
- [x] Storage: disk space, file sizes and ages, delete files older than N days, clear the cache
- [x] Backup and restore (settings, history, presets, queue and optionally the uploaded files)
- [x] GET /api/status for other programs
- [ ] More plotter options?
