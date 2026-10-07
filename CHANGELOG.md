# Changelog

Newest first. Users see the entries they have not read yet the next time they open the page after an update.

To make a release: add an entry here, raise `VERSION` to the same number, and commit them together. Entries are `## <version> - <date>` with optional `### <group>` headings and `- ` bullets; the page shows plain text only.

## 1.1.0 - 2026-10-07

### Added
- Plot adjustments for one plot: pen speed, force and acceleration, moving the drawing on the paper, and tracing the drawing's area first so you can check the paper before anything is drawn.
- Copies: add a plot to the queue several times at once. Drag the waiting files of the queue into a new order.
- A plot that a power cut or a restart cut short can now be resumed from the history, from the last place noted (about every 30 seconds).
- Pen use: how far each pen has drawn, with a New pen button, to know when a pen is worn out.
- Optional HTTPS (a certificate and key in the settings), and an address that sends too many wrong passwords is refused for a while.
- "Plot only the pens shown" in the preview now also works for a file that is not the selected one.
- The backup dialog shows how big the uploaded files are.

### Changed
- Files are converted in a separate process at a low priority, so a plot that is running is disturbed much less, and a Cancel button stops a conversion that is taking too long. A setting in the configuration turns this off if the Pi runs short of memory.
- Only one conversion runs at a time, and the convert dialog warns while a plot is running.
- A conversion whose file name would be too long for the file system is refused with a clear message.
- The history database keeps a version number, so later changes to it are applied once and in order. A database or backup made by a newer web plotter is left alone instead of being half used.

## 1.0.0 - 2026-10-06

The first numbered release.

### Added
- The Console page: a bar that always shows the plot (state, file, progress, pen, time left, Start, Pause and Stop) and panels that you can drag between columns, fold, hide and resize. Dark and light themes.
- A drawing preview in the page with zoom, pan, a replay and a cursor that follows the plot (Watch the plot).
- Time left for a plot, which learns from your earlier plots. Choose which pens to plot, and pause at each pen change.
- A plot queue with optional paper changes, and a plot history with Plot again and Resume.
- Plotter profiles (with the serial line options), vpype devices of your own, presets for conversions, and vpype plugins.
- PDF import, drawing typed text, and CalComp .cal files with a CalComp flow control.
- Notifications to Telegram, a webhook and MQTT, with a switch for each kind of message.
- A timelapse video of a plot, and the two buttons of the Pi Plot shield.
- Backup and restore, disk space and clean-up tools, and a status address for other programs (/api/status).
- If the serial connection drops the plot is held, the port is opened again and you choose when to carry on.
- A notice when a newer version is out, a Changelog that is shown after an update, and an Update button that installs the new version from the page.
- The installer offers to let a user other than pi use sudo without a password, which the Update, Reboot and Power off buttons need.

### Changed
- The settings of the forms are kept on the server, so a reload or another device shows the same values.

### Removed
- HP-IB flow control. It needs a separate shield for the Pi, so it is no longer offered.
