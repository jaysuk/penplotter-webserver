![Install script test](https://github.com/ithinkido/penplotter-webserver/actions/workflows/install_test.yml/badge.svg)


[![Image of WebPlot - A Web interface for Pen Plotter](https://raw.githubusercontent.com/ithinkido/penplotter-webserver/PiPlot/docs/img/Demo.gif)](https://github.com/ithinkido/penplotter-webserver/tree/flowcontrol)

This branch has been modifed for use with the [Pi Plot shiled](https://github.com/ithinkido/PiPlot)

### Changes made in this version

- Add folowing plotters : HP7440a, HP7550, Roland DXY 1xxx, Roland Sketchmate, Houston Instrument DMP-161, Calcomp Designmate, and Calcomp Artisan. 
- Support for HP-IB through Plug n Plot.
- Plot optimization options through Vpype.
- Add Buffer space info graph when plotting (Not availible when using XON/XOFF nor HP-IB)
- Auto baud rate detection function.
- Simple resizing of input SVG when converting to HPGL
- Add fix for pyserial bug when using hardware flow control over CTS /RTS.
- Plot speed definition + support for other custom vpype commands
- Telegram notifications
- HPGL preview


# WebPlot - A Web interface for Pen Plotters

Python webservice to simplify working with pen plotters:
- Supported plotters: Graphtec MP4200, HP7475a
- Created for Raspberry Pi.
- Upload *.SVG and *.HPGL files.
- Convert *.SVG into *.HPGL files using [vpype](https://github.com/abey79/vpype)
- Notifications (start, finish, errors, pen or paper changes, progress) to Telegram, a webhook or MQTT
- Power off your plotter on print end using a Tasmota-enabled Sonoff controller   


## Installation

This quick and easy install script is intended to be used with Raspberry Pi OS (Bullseye, Bookworm or Trixie, 32 or 64 bit).
It needs Python 3.9.2 or newer, and it installs from this repository's `PiPlot` branch whatever the OS version.

From the home directory, run:

```bash
curl -sSL https://raw.githubusercontent.com/jaysuk/penplotter-webserver/PiPlot/install.sh | bash
```
This will install the Pen Plotter Web Server and reboot the Raspberry Pi once installation is completed.
Running it again updates an existing install and keeps your uploaded files and *config.ini*.

Environment variables for the script: `WEBPLOTTER_REPO` and `WEBPLOTTER_BRANCH` install from another repository or branch, `WEBPLOTTER_NO_REBOOT=1` skips the reboot.

## Usage

After the install, open a browser and got to:
```bash
http://{{your Raspberry-Pi IP address}}:5000
```

Optional:
Configure options in *config.ini* using the web interface (settings icon, bottom of the sidebar). Everything in *config.ini* can be changed there:
- Plotter name, default device, port, baud rate and flow control.
- Telegram token and chat ID, a webhook URL and an MQTT broker for notifications, and which events to send.
- Tasmota device IP, and how long to wait after switching the plotter on and before switching it off.
- Timelapse settings (stored, but not used by this version yet).
- A login (see Security).

The *Plot History* card lists recent plots with how they ended (completed, stopped, failed, or interrupted when the server stopped mid-plot). It is kept in *history.db* next to *config.ini*, and an update keeps it.

USB serial adapters are listed by their stable `/dev/serial/by-id/...` name, which does not change when the adapter is unplugged and plugged back in (unlike `/dev/ttyUSB0`). Prefer that entry for the default port. The Pi's own serial port (`/dev/ttyAMA0`) has no such name.

The "Shutdown when plot is completed" option switches the plotter off through Tasmota after the *wait before switching off* (30 seconds by default, as the plotter can still be drawing when the last byte is sent). Press Stop during that wait to switch off at once.

While a plot is running you can pause, resume or stop it. If you refresh the page, or open it on another device, it shows the file being plotted, the progress and the log so far, and the same buttons.

## Development

The Python sources are `main.py`, `send2serial.py`, `convert_vpype.py`, `config.py`, `notification.py`, `tasmota.py` and `globals.py`. Run it from a checkout with `python3 main.py` (needs the packages in *requirements.txt*).

Flow control options (Settings -> Flow Control):
- **CTS/RTS**, **Software**: the plotter reports its free buffer space and the buffer chart is shown.
- **XON/XOFF**: handshaking is done by the serial driver. **None**: no handshaking. **HP-IB**: through Plug n Plot. No buffer information is available for these three.

HPGL files in the file list have a preview (picture icon) that draws the pen movements, colored per pen.

## Security

By default anyone who can reach port 5000 can upload and delete files, start plots and reboot the Pi.
Only run this on a trusted network, or enable a login by adding an `[auth]` section to *config.ini*:

```ini
[auth]
username = admin
password = change-me
```

You can also set or clear the login in the web interface (settings, Login). It applies immediately, no restart needed, and the password is never sent back to the browser. If you lock yourself out, delete the `[auth]` section from *config.ini* over SSH and restart the service (`sudo systemctl restart webplotter`).
The login uses HTTP basic auth, so use it behind HTTPS if the network is not trusted.

Custom vpype commands are run by vpype, so they can only contain letters, numbers, spaces and `. _ = + -`, and cannot use `eval`, `script`, `read`, `write`, `forfile`, `include` or `show`.

Set `WEBPLOTTER_DEBUG=1` to start Flask in debug mode (development only: it exposes an interactive debugger).

## ToDO

- [x] Fix Mobile UI
- [x] Add plotter name to toolbar
- [x] Add defaults to configuration file
- [x] Stop print via UI?
- [x] List current printing filename

- [ ] More plotter options?

See *ToDo.md* for the full list of open items.

## Contributing
Pull requests are welcome. For major changes, please open an issue first to discuss what you would like to change.

## License
[MIT](https://choosealicense.com/licenses/mit/)  

![visitors](https://vbr.nathanchung.dev/badge?page_id=ithinkido.PenPlotterWebServer)

