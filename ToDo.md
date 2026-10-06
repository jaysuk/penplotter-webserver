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
- [ ] Stop (`ESC.K` + `PU;`) and pause have not been tried on a real plotter. They work against a simulated one on a real serial port (`tests/pi_serial_check.py`).
- [x] Timelapse: pictures every few seconds while a plot runs (picture address, Pi camera or USB webcam), a video
      made with ffmpeg, a list to play, download and delete them. Untested with a real camera and on a Pi Zero.
- [x] Page size filter per plotter (`updatePageSize`, from the paper sizes of the chosen vpype device). The convert dialog
      does not list the MP4200 on purpose: vpype 1.15 has no such device, so a conversion would fail. Add it as a
      device of your own (vpype devices) with that id and it appears in every list.
- [x] Pi Plot shield buttons (GPIO 27 start, GPIO 22 stop): each can resume or start the queue, stop, pause or do nothing.
      Needs the shield to try (rising edge on a pulled down input, as the shield's example).
- [x] The Telegram token is write-only, like the passwords (an empty field keeps it; a box removes it).
- [x] Plot queue: several files in a row, with an optional paper change between them. Stop holds it. Untested on a real plotter, like the other plot control.
- [x] Plot history: "Plot again" from a history row (a deleted file stays listed by name, without the button)
- [x] Resume a stopped or failed plot from the history (carries on from the byte the plotter reached; untested on hardware)
- [x] A dropped serial connection holds the plot and offers to reconnect and continue (untested on hardware)
- [x] Notifications: per-event toggles, progress every N percent, webhook and MQTT, a test button
- [x] Storage: disk space, file sizes and ages, delete files older than N days, clear the cache
- [x] Backup and restore (settings, history, presets, queue and optionally the uploaded files)
- [x] GET /api/status for other programs
- [x] More plotter options: plotter profiles, the serial line options and vpype devices of your own cover them. New ideas go here as they come up.
