# Changelog

Newest first. Users see the entries they have not read yet the next time they open the page after an update.

To make a release: add an entry here, raise `VERSION` to the same number, and commit them together. Entries are `## <version> - <date>` with optional `### <group>` headings and `- ` bullets; the page shows plain text only.

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
