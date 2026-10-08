import collections
import configparser
import time
import math
import os
import serial
import serial.tools.list_ports
from serial import SerialException

import notification
import globals
import plotlog
import hpgl_analysis
import plotter_control
# Shared, live configuration object (updated when settings are saved in the UI)
from config import config

ERRORS = {
    -1: 'Timeout, connection issues ?',
    -2: 'Parse error of decimal return from plotter',

    0: 'no error',
    10: 'overlapping output instructions',
    11: 'invalid byte after <ESC>.',
    12: 'invalid byte while parsing device control instruction',
    13: 'parameter out of range',
    14: 'too many parameters received',
    15: 'framing error, parity error or overrun',
    16: 'input buffer has overflowed'
}

# Errors that can happen while opening a serial port
PORT_ERRORS = (SerialException, ValueError, OSError)


class HPGLError(Exception):
    def __init__(self, n, cause=None):
        self.errcode = n
        if cause:
            self.causes = [cause]
        else:
            self.causes = []

    def add_cause(self, cause):
        self.causes.append(cause)

    def __repr__(self):
        if type(self.errcode) is str:
            errstr = self.errcode
        else:
            errstr = f'Error {self.errcode}: {ERRORS.get(self.errcode)}'

        if self.causes:
            cstr = ', '.join(self.causes)
            return f'HPGLError: {errstr}, caused by {cstr}'
        else:
            return f'HPGLError: {errstr}'

    def __str__(self):
        return repr(self)


def read_answer(tty):
    buf = bytearray()
    while True:
        c = tty.read(1)
        if not c:
            raise HPGLError(-1)
        if c == b'\r':
            break
        buf += c
    try:
        return int(buf)
    except ValueError as e:
        print(repr(e))
        raise HPGLError(-2)


def chk_error(tty):
    tty.write(b'\033.E')
    ret = None
    try:
        ret = read_answer(tty)
    except HPGLError as e:
        e.add_cause('ESC.E (Output extended error code).')
        raise e
    if ret:
        raise HPGLError(ret)


def getReplySTR(tty, cmd):
    tty.write(cmd)
    reply = str(tty.read_until(b'\r').decode('utf-8', errors='replace'))
    if len(reply) > 1:
        return reply
    else:
        raise HPGLError(-1)


def plotter_cmd(tty, cmd, get_answer=True):
    tty.write(cmd)

    try:
        if get_answer:
            answ = read_answer(tty)

        if get_answer:
            return answ
    except HPGLError as e:
        e.add_cause(f'after sending {repr(cmd)[1:]}')
        raise e


# Linux names USB serial adapters here after their make and serial number, so the name survives
# unplugging and replugging (/dev/ttyUSB0 may become /dev/ttyUSB1)
SERIAL_BY_ID = '/dev/serial/by-id'


def byIdPorts():
    """Stable names of the USB serial adapters: {'/dev/serial/by-id/usb-...': '/dev/ttyUSB0'}."""
    links = {}
    try:
        names = sorted(os.listdir(SERIAL_BY_ID))
    except OSError:
        return links
    for name in names:
        path = SERIAL_BY_ID + '/' + name
        links[path] = os.path.realpath(path)
    return links


def listComPorts():
    """The stable by-id name of each USB adapter, then the other ports (such as the Pi's own
    UART). An adapter is listed once, under its stable name."""
    links = byIdPorts()
    aliased = set(links.values())
    others = sorted(port.device for port in serial.tools.list_ports.comports()
                    if port.device not in aliased)
    return dict(name='ports', content=list(links) + others)


# Serial line options (see plotters.LINE_FIELDS). None leaves a line to what the flow control needs.
LINE_DEFAULTS = {'bytesize': serial.EIGHTBITS, 'parity': serial.PARITY_NONE, 'stopbits': serial.STOPBITS_ONE,
                 'xonxoff': None, 'rtscts': None, 'dsrdtr': None, 'dtr': None, 'rts': None,
                 'timeout': None, 'open_delay': 0}


def serial_settings(baud, flowControl, line=None):
    """The pyserial settings for a flow control, with the user's line options on top. Returns
    (keyword arguments for serial.Serial, the options used)."""
    line = dict(LINE_DEFAULTS, **{key: value for key, value in (line or {}).items() if key in LINE_DEFAULTS})
    # What each flow control needs. CTS is polled by hand (see sendToPlotter) because of a pyserial bug
    # with rtscts, and a CalComp plot asks the plotter itself, with a short timeout to stay responsive.
    needs = {'xonxoff': flowControl == 'XON/XOFF', 'rtscts': flowControl == 'HP-IB', 'dsrdtr': False}
    timeout = 0.1 if flowControl == CAL_POLL else 2.0
    if line['timeout'] is not None and flowControl != CAL_POLL:
        timeout = line['timeout']
    kwargs = {'baudrate': 9600 if flowControl == 'HP-IB' else baud, 'bytesize': line['bytesize'],
              'parity': line['parity'], 'stopbits': line['stopbits'], 'timeout': timeout}
    for key, needed in needs.items():
        value = needed if line[key] is None else line[key]
        if value or line[key] is not None:
            kwargs[key] = value
    return kwargs, line


def getBaudRate(t_port, line=None):
    baud_dict = [9600, 19200, 38400, 4800, 2400, 1200]
    message = 'IN;OI;OE'
    line = dict(LINE_DEFAULTS, **(line or {}))
    try:
        ser = serial.Serial(port=t_port, timeout=0.3, bytesize=line['bytesize'], parity=line['parity'],
                            stopbits=line['stopbits'])
    except PORT_ERRORS as e:
        print(repr(e))
        return None

    try:
        for baud_rate in baud_dict:
            ser.baudrate = baud_rate
            ser.reset_input_buffer()
            ser.write(message.encode())
            read_val = ser.read(size=64)
            # Wrong baud rates return garbage that is not valid text
            if len(read_val) > 0 and read_val.decode(errors='replace') != message:
                return baud_rate
    except PORT_ERRORS as e:
        print(repr(e))
    finally:
        ser.close()
    return None


def open_port(socketio, port, baud, flowControl, init=True, line=None):
    """Open and initialise the serial port for the given flow control. Returns None on failure.

    `line` holds the serial line options (bits, parity, stop bits, handshake lines, DTR and RTS,
    timeout and a pause after opening); what it leaves out follows from the flow control.

    With `init` false the plotter is not set up (no IN;), so it keeps its state: that is for
    moving the pen by hand."""
    try:
        kwargs, line = serial_settings(baud, flowControl, line)
        # Without a port pyserial does not open yet, so DTR and RTS can be set first (some adapters
        # and boards reset or power up when DTR rises). Linux still raises both for an instant on open.
        tty = serial.Serial(**kwargs)
        tty.port = port
        if line['dtr'] is not None:
            tty.dtr = line['dtr']
        if line['rts'] is not None:
            tty.rts = line['rts']
        tty.open()
        if line['open_delay']:
            time.sleep(line['open_delay'])
        if init:
            if flowControl == 'XON/XOFF':
                tty.write(b'IN;.I80;;17:.N10;19:.@;0:')
            elif flowControl not in ('HP-IB', CAL_POLL):
                tty.write(b'IN;.R')
                time.sleep(0.2)
        return tty
    except PORT_ERRORS as e:
        socketio.emit('error', {'data': repr(e)})
        print(repr(e))
        return None


# pyserial's write() cannot be interrupted once the port's output queue is full: it waits (or, with a
# non-blocking port, spins) until the plotter takes data, and never looks at the Stop button. A plotter
# that holds the line (XOFF, switched off, out of paper) would leave the sender, and the plot lock, stuck
# for good. So the queue is kept short (the kernel's holds about 4 KB) and every write waits for room first.
WRITE_BACKLOG = 1024


STALL_LOG_AFTER = 2     # seconds the plotter may refuse data before the plot log says so


def wait_for_room(tty):
    """Wait until the port's output queue has room. False when Stop was pressed meanwhile."""
    since = None
    reported = False
    while globals.printing:
        if getattr(tty, 'out_waiting', 0) <= WRITE_BACKLOG:
            if reported:
                plotlog.log('The plotter takes data again after {:.0f} s'.format(time.time() - since))
            return True
        if since is None:
            since = time.time()
        elif not reported and time.time() - since >= STALL_LOG_AFTER:
            reported = True
            plotlog.log('The plotter is not taking data: {} bytes wait in the port (handshaking hold, plotter off or '
                        'out of paper, or a cable problem)'.format(getattr(tty, 'out_waiting', 0)))
        time.sleep(0.02)
    if reported:
        plotlog.log('Gave up waiting for the plotter after {:.0f} s: the plot was stopped'.format(time.time() - since))
    return False


def discard_unsent(tty):
    """Drop what the port has not sent yet (best effort): a stopped plot must not wait for it, and
    a plotter that is holding the line would never take it."""
    try:
        tty.reset_output_buffer()
    except PORT_ERRORS + (AttributeError,) as e:
        print(repr(e))


def abort_plot(tty):
    """Stop the plotter drawing what is already in its buffer (best effort)."""
    discard_unsent(tty)       # the abort must not queue up behind plot data
    try:
        tty.write(b'\033.K')  # abort graphics instruction, flushes the plotter's buffer
        tty.write(b'PU;')     # pen up
    except PORT_ERRORS as e:
        print(repr(e))


def is_cal(path):
    """CalComp .cal files (R2; H; F1; Cx,y; K; I; ...) are not HP-GL: nothing HP-GL is sent with them."""
    return str(path).lower().endswith('.cal')


# A .cal plot is a plain stream, so there are no buffer queries: either the plotter's own XON/XOFF handshake, or
# 'CalComp', where the server asks the plotter how full its buffer is (see cal_buffer_empty) before every chunk
CAL_POLL = 'CalComp'
CAL_FLOW_CONTROLS = ('XON/XOFF', 'NONE', CAL_POLL)
CAL_PEN_UP = b'H;'

# The Model 84's default I/O characters (handbook 3.14.1 and 3.14.4). Its buffer is 1024 bytes and it answers a
# Ctrl-Q request with Ctrl-A when less than 25 % is used and Ctrl-Z when 75 % or more is. It also sends Ctrl-S and
# Ctrl-Q by itself as the buffer fills and drains; those are not answers.
CAL_STATUS_REQUEST = b'\x11'
CAL_REPLY_EMPTY, CAL_REPLY_FULL = 0x01, 0x1A
CAL_POLL_CHUNK = 256        # bytes to send after an "empty" answer: 1024 - 768, safe even if "empty" only means "under 75 %"
CAL_ANSWER_S = 1.0          # how long to wait for the answer to one request
CAL_RETRY_S = 0.05          # pause before asking again after "full"


def cal_flow_allowed(flowControl):
    """Whether a .cal file may be sent with this flow control (the UI's 'None' counts as 'NONE')."""
    flowControl = str(flowControl)
    return ('NONE' if flowControl.upper() == 'NONE' else flowControl) in CAL_FLOW_CONTROLS


def cal_buffer_empty(tty, answer_s=None):
    """Ask a CalComp plotter how full its buffer is: True for "empty", False for "full", None for no answer."""
    answer_s = CAL_ANSWER_S if answer_s is None else answer_s
    tty.reset_input_buffer()          # an old answer, or the plotter's own Ctrl-S / Ctrl-Q, is not this answer
    tty.write(CAL_STATUS_REQUEST)
    deadline = time.time() + answer_s
    while time.time() < deadline:
        got = tty.read(1)
        if got:
            if got[0] == CAL_REPLY_EMPTY:
                return True
            if got[0] == CAL_REPLY_FULL:
                return False
    return None


def run_commands(socketio, port, baud, flowControl, commands, query=None, line=None):
    """Send a few short commands to an idle plotter, such as moving the pen.

    `query` (for example b'OA;') is sent last and its answer returned. Raises HPGLError when the
    plotter does not answer; returns None when the port cannot be opened (the reason goes to
    `socketio` as an error event). The caller must make sure no plot is running."""
    tty = open_port(socketio, port, baud, flowControl, init=False, line=line)
    if tty is None:
        return None
    try:
        for command in commands:
            tty.write(command)
        if query is not None:
            tty.reset_input_buffer()
            return getReplySTR(tty, query)
        return ''
    except PORT_ERRORS as e:
        socketio.emit('error', {'data': repr(e)})
        return None
    finally:
        tty.close()


CHUNK_MAX = 1024        # the most bytes per write that the settings accept
HEARTBEAT_EVERY = 30    # seconds between progress lines in the plot log


def chunk_setting():
    """Bytes per write chosen in the settings; 0 when the sender should decide."""
    try:
        value = int(config.get('plotter', 'chunk_size', raw=True, fallback='') or 0)
    except (ValueError, configparser.Error):
        return 0
    return value if 1 <= value <= CHUNK_MAX else 0


def chunk_size(flowControl, bufsz, use_buffer):
    """How many bytes of the file go into one write. HP-IB takes one at a time. With buffer flow control
    a chosen size is held to half of the plotter's buffer, because CTS and the free space are only looked at
    between writes."""
    if flowControl == 'HP-IB':
        return 1
    chosen = chunk_setting()
    if not chosen:
        return 10 if bufsz < 80 else 30
    return min(chosen, max(bufsz // 2, 1)) if use_buffer else chosen


ETA_EVERY = 5   # seconds between time left updates (they are also sent when the percentage moves)

# Seconds between attempts to open the port again after the connection dropped (the last one repeats),
# and how long to try before the plot is given up
RECONNECT_DELAYS = (1, 2, 5, 10)
RECONNECT_GIVE_UP = 3600
# Without buffer feedback nobody knows how much the plotter still had to draw when the connection
# dropped, so a plot carries on from this many bytes earlier (drawing a little twice, not leaving a gap)
REWIND_NO_FEEDBACK = 1024
REWIND_HPIB = 64


class _Quiet:
    """Stands in for socketio when a failure is expected and should not be reported."""

    def emit(self, *args, **kwargs):
        pass


def sleep_while_printing(seconds):
    end = time.time() + seconds
    while time.time() < end and globals.printing:
        time.sleep(min(0.05, max(end - time.time(), 0)))


def reconnect(socketio, notify_name, reason, tty, port, baud, flowControl, use_buffer, hpglfile, target, line=None):
    """The serial connection dropped in the middle of a plot (the adapter was unplugged, the cable
    came loose). Hold the plot, open the port again with growing pauses, then wait until the user
    has checked the plotter and pressed Resume.

    `target` is the byte of the file to carry on from. Returns (tty, buffer size, offset, preamble):
    the new port, and what to send first (see hpgl_analysis.resume_preamble) before reading the file
    from `offset` (the start of the command at `target`). Returns 'stopped' when Stop was pressed
    and 'failed' when the plotter could not be reached again."""
    socketio.emit('error', {'data': 'Lost the connection to the plotter: ' + str(reason)})
    notification.send('error', '{}: {}: Connection lost'.format(notify_name, globals.current_file),
                      file=globals.current_file)
    try:
        tty.close()
    except PORT_ERRORS:
        pass
    globals.wait_reason = 'disconnected'
    globals.paused = True
    socketio.emit('status_log', {'data': 'Plot held. Trying to connect to the plotter again...'})
    socketio.emit('wait_change', {'data': {'reason': 'disconnected'}})

    started = time.time()
    attempt = 0
    new_tty = None
    while globals.printing and new_tty is None:
        sleep_while_printing(RECONNECT_DELAYS[min(attempt, len(RECONNECT_DELAYS) - 1)])
        attempt += 1
        if not globals.printing:
            break
        new_tty = open_port(_Quiet(), port, baud, flowControl, line=line)
        if new_tty is None and time.time() - started > RECONNECT_GIVE_UP:
            socketio.emit('error', {'data': 'Could not connect to the plotter again: the plot is given up.'})
            globals.clear_wait()
            return 'failed'
    if new_tty is None:
        globals.clear_wait()
        return 'stopped'

    # The port is back. The plotter may have lost its place or its power, so the user confirms
    globals.wait_reason = 'reconnect'
    globals.paused = True
    socketio.emit('status_log', {'data': 'The plotter is connected again. Check that the paper and the pen '
                                         'carriage have not moved, then press Resume to carry on.'})
    socketio.emit('wait_change', {'data': {'reason': 'reconnect'}})
    notification.send('attention', '{}: {}: Connected again, press Resume'.format(
        notify_name, globals.current_file), file=globals.current_file)
    while globals.printing and globals.paused:
        time.sleep(0.1)
    if not globals.printing:
        new_tty.close()
        globals.clear_wait()
        return 'stopped'

    bufsz = 1024
    if use_buffer:
        try:
            bufsz = plotter_cmd(new_tty, b'\033.L', True)
        except HPGLError as e:
            socketio.emit('error', {'data': 'The plotter does not answer: ' + str(e)})
            new_tty.close()
            return 'failed'
        socketio.emit('buffer_size', {'data': str(bufsz)})
    try:
        preamble, offset = hpgl_analysis.resume_preamble(hpglfile, target)
    except ValueError:
        preamble, offset = b'', target      # everything had been sent: only the end of the file is left
    socketio.emit('status_log', {'data': 'Carrying on from byte {} of the file.'.format(offset)})
    return new_tty, bufsz, offset, preamble


def sendToPlotter(socketio, hpglfile, port, baud, flowControl, analysis=None, pen_pause=False, correction=1.0, line=None,
                  frame=None):
    """Stream an HPGL file to the plotter. Returns True if the plot finished or was stopped.

    `analysis` (from hpgl_analysis) gives the time left. With `pen_pause` the plot also holds
    back at every pen change until it is resumed, so the pen can be swapped by hand. `line` holds the
    serial line options (see open_port). With `frame` (the drawing's [minx, miny, maxx, maxy] in plotter
    units) the pen, lifted, first goes round that rectangle and the plot waits for Resume, so the
    paper can be checked before anything is drawn."""

    PLOTTER_NAME = config.get('plotter', 'name', fallback='Plotter')
    notify_name = PLOTTER_NAME.replace(' ', '-')

    flowControl = str(flowControl)
    if flowControl.upper() == 'NONE':
        flowControl = 'NONE'

    cal = is_cal(hpglfile)
    if cal and not cal_flow_allowed(flowControl):
        socketio.emit('error', {'data': '.cal files need XON/XOFF, CalComp or no flow control'})
        return False
    if flowControl == CAL_POLL and not cal:
        socketio.emit('error', {'data': 'CalComp flow control is for .cal files'})
        return False

    # Only plotters that can report their buffer size and free space support buffer based flow control
    use_buffer = flowControl not in ('HP-IB', 'XON/XOFF', 'NONE', CAL_POLL)

    # A Stop that arrived before the sender got going must not be undone by starting
    globals.printing = not globals.stop_requested
    tty = None
    hpgl = None
    finished = False

    try:
        try:
            input_bytes = os.stat(hpglfile).st_size
        except OSError as e:
            print('Error stat\'ing file', hpglfile, str(e))
            socketio.emit('error', {'data': 'Error stat\'ing file ' + str(hpglfile)})
            return False

        if input_bytes == 0:
            socketio.emit('error', {'data': 'File is empty: ' + str(hpglfile)})
            return False

        hpgl = open(hpglfile, 'rb')

        # A CalComp plotter would not understand the HP-GL set-up or the identification query
        tty = open_port(socketio, port, baud, flowControl, init=not cal, line=line)
        if tty is None:
            return False

        if not cal:
            try:
                plotter_id = getReplySTR(tty, b'IN;OI;')
                socketio.emit('status_log', {'data': 'Plotter identifies as ' + plotter_id})
            except HPGLError as e:
                socketio.emit('status_log', {'data': 'Plotter did not reply, sending plot anyway!'})

        print('Configured for ', flowControl, ' flow control.')
        socketio.emit('status_log', {'data': 'Configured for ' + str(flowControl) + ' flow control.'})

        total_bytes_written = 0

        if analysis is not None and analysis.get('bytes') != input_bytes:
            analysis = None     # the file changed after it was analysed
        pen_stops = collections.deque(hpgl_analysis.pen_changes(analysis) if analysis and pen_pause else ())
        total_estimate = analysis['seconds'] * correction if analysis else None
        last_eta = 0
        paused_since = None
        paused_time = 0.0
        pending = b''           # set-up commands to send before the file carries on (after a reconnect)
        frame_hold = False      # the area is being traced: wait for Resume once that is sent
        if frame and not cal:
            pending = b''.join(plotter_control.trace_bounds(frame))
            frame_hold = True
        last_len = 0            # size of the chunk written last
        last_stop = 0           # offset of the last pen change that was passed

        remaining = None
        cal_room = 0            # CalComp flow control: bytes the plotter has said it has room for

        def send_eta(offset):
            nonlocal last_eta, remaining
            if total_estimate is None:
                return
            remaining = max(total_estimate - hpgl_analysis.time_at(analysis, offset) * correction, 0)
            socketio.emit('eta', {'data': {'remaining': round(remaining), 'total': round(total_estimate)}})
            last_eta = time.time()

        # Without buffer feedback this only decides the chunk size
        bufsz = 1024
        bufsp = bufsz

        if use_buffer:
            try:
                bufsz = plotter_cmd(tty, b'\033.L', True)
            except HPGLError as e:
                print('*** Error initializing the plotter!')
                print(e)

                socketio.emit('error', {'data': '*** Error initializing the plotter!'})
                socketio.emit('error', {'data': str(e)})
                return False

            print('Size of plotter buffer is ', bufsz, ' bytes.')
            socketio.emit('status_log', {'data': 'Size of plotter buffer is ' + str(bufsz) + ' bytes.'})
            socketio.emit('buffer_size', {'data': str(bufsz)})

        chunk = chunk_size(flowControl, bufsz, use_buffer)
        plotlog.log('Sending {} ({} bytes) to {} at {} baud, {} flow control, {} bytes per write{}'.format(
            os.path.basename(hpglfile), input_bytes, port, baud, flowControl, chunk,
            ', plotter buffer {} bytes'.format(bufsz) if use_buffer else ''))
        last_beat = time.time()

        globals.current_file = hpglfile.replace('uploads/', '')
        globals.current_file = globals.current_file[:-4] if cal else globals.current_file.replace('.hpgl', '')
        globals.start_stamp = time.time()
        notification.send('start', notify_name + ': ' + globals.current_file + ': Starting', file=globals.current_file)

        prev_percent = 0
        send_eta(0)

        while globals.printing == True:
            try:
                if time.time() - last_beat >= HEARTBEAT_EVERY:
                    last_beat = time.time()
                    plotlog.log('Still going: {} of {} bytes sent{}{}, {} bytes queued in the port'.format(
                        total_bytes_written, input_bytes,
                        ', plotter buffer {} of {} free'.format(bufsp, bufsz) if use_buffer else '',
                        ', held back ({})'.format(globals.wait_reason or 'paused') if globals.paused else '',
                        getattr(tty, 'out_waiting', 0)))

                if globals.paused:
                    # Hold back the data (the plotter finishes what is in its buffer), but stay
                    # responsive to the stop button
                    if paused_since is None:
                        paused_since = time.time()
                    time.sleep(0.1)
                    continue
                if paused_since is not None:
                    paused_time += time.time() - paused_since
                    paused_since = None

                if frame_hold and not pending:
                    frame_hold = False
                    globals.wait_reason = 'frame_check'
                    globals.paused = True
                    socketio.emit('status_log', {'data': 'The pen has gone round the area the drawing will cover. '
                                                         'Check the paper, then press Resume to start drawing.'})
                    socketio.emit('wait_change', {'data': {'reason': 'frame_check'}})
                    notification.send('attention', '{}: {}: Check the paper, then press Resume'.format(
                        notify_name, globals.current_file), file=globals.current_file)
                    continue

                if pen_stops and total_bytes_written >= pen_stops[0][0]:
                    last_stop, pen = pen_stops.popleft()
                    if use_buffer:
                        # Let the plotter draw what is in its buffer before asking for the pen change
                        while globals.printing:
                            bufsp = plotter_cmd(tty, b'.B', True)
                            if bufsp == bufsz:
                                break
                            time.sleep(0.5)
                            socketio.emit('buffer_space', {'data': str(bufsp)})
                        if not globals.printing:
                            break
                    if not wait_for_room(tty):
                        break
                    tty.write(b'PU;')
                    globals.wait_reason = 'pen_change'
                    globals.wait_pen = pen
                    globals.paused = True
                    socketio.emit('status_log', {'data': 'Pen change: wait for the plotter to stop, load pen {}, '
                                                         'then press Resume.'.format(pen)})
                    socketio.emit('pen_change', {'data': {'pen': pen}})
                    notification.send('attention', '{}: {}: Load pen {}'.format(
                        notify_name, globals.current_file, pen), file=globals.current_file, pen=pen)
                    continue

                size = chunk
                if pen_stops:
                    size = min(size, pen_stops[0][0] - total_bytes_written)    # stop exactly at the pen change
                if flowControl == CAL_POLL:
                    if cal_room <= 0:
                        # Ask before sending: after an "empty" answer 256 bytes always fit, however much the USB
                        # adapter or the driver still has queued, so the plotter's buffer cannot overflow
                        state = False
                        while globals.printing and not globals.paused:
                            state = cal_buffer_empty(tty)
                            if state is not False:
                                break
                            time.sleep(CAL_RETRY_S)
                        if not globals.printing or globals.paused:
                            continue            # Stop or Pause arrived while waiting: the top of the loop deals with it
                        if state is None:
                            socketio.emit('error', {'data': 'The plotter did not answer the buffer status request '
                                                            '(Ctrl-Q). Check the cable, the baud rate and that it is '
                                                            'online, or use XON/XOFF flow control.'})
                            return False
                        cal_room = CAL_POLL_CHUNK
                    size = min(size, cal_room)
                if pending:
                    data, pending = pending[:size], pending[size:]
                    from_file = False
                else:
                    data = hpgl.read(size)
                    from_file = True
                bufsz_read = len(data)

                if flowControl in ('CTS/RTS', 'HP-IB'):
                    if not tty.getCTS():
                        time.sleep(0.05)
                        # Wait for the plotter, but stay responsive to the stop button
                        while not tty.getCTS() and globals.printing:
                            time.sleep(0.0005)
                        if not globals.printing:
                            break

                if use_buffer:
                    try:
                        bufsp = plotter_cmd(tty, b'\033.B', True)
                    except HPGLError as e:
                        print('*** Error initializing the plotter!')
                        print(e)

                        socketio.emit('error', {'data': '*** Error on buffer space query!'})
                        socketio.emit('error', {'data': str(e)})
                        return False

                    print('### BUFFER SPACE : ' + str(bufsp))
                    socketio.emit('buffer_space', {'data': str(bufsp)})

                if flowControl == 'Software':
                    if bufsp < bufsz / 2:
                        time.sleep(0.1)

                if not wait_for_room(tty):
                    break           # Stop was pressed while the plotter was not taking data
                tty.write(data)
                cal_room -= len(data)
                if from_file:
                    total_bytes_written += bufsz_read
                last_len = bufsz_read
                # Where a stopped or failed plot can carry on: what the plotter has not drawn yet is
                # in its buffer (known with buffer flow control) or was in this last chunk
                globals.sent_offset = total_bytes_written
                globals.buffer_used = (bufsz - bufsp) + bufsz_read if use_buffer else 0

                if bufsz_read == 0:
                    # Wait for the plotter to work through its buffer
                    if use_buffer:
                        while bufsp != bufsz and globals.printing:
                            bufsp = plotter_cmd(tty, b'\033.B', True)
                            time.sleep(0.5)
                            socketio.emit('buffer_space', {'data': str(bufsp)})
                            print('### BUFFER SPACE : ' + str(bufsp))

                    if not globals.printing:
                        break

                    print('*** End of Print, exiting.')
                    minutes = math.ceil((time.time() - globals.start_stamp) / 60)
                    notification.send('finish', notify_name + ': ' + globals.current_file + ': Finished' + ': ' + str(minutes) + ' Minutes Total',
                                  file=globals.current_file, minutes=minutes, progress=100)
                    globals.drawn_seconds = time.time() - globals.start_stamp - paused_time
                    globals.current_file = 'None'
                    globals.start_stamp = 0
                    send_eta(input_bytes)
                    socketio.emit('bytes_written', {'data': f'**EOP** - {total_bytes_written} bytes sent. Exiting.'})
                    socketio.emit('print_progress', {'data': 100})
                    socketio.emit('end_of_print', {'data': 'True'})
                    finished = True
                    break

                percent = int(100.0 * total_bytes_written / input_bytes)
                if percent != prev_percent:
                    socketio.emit('bytes_written', {'data': f'{percent:.0f}%, {total_bytes_written} bytes written.'})
                    socketio.emit('print_progress', {'data': percent})
                    send_eta(total_bytes_written)
                    every = notification.progress_every()
                    if every and percent < 100 and percent // every > prev_percent // every:
                        left = ', about {} min left'.format(math.ceil(remaining / 60)) if remaining is not None else ''
                        notification.send('progress', '{}: {}: {}% done{}'.format(
                            notify_name, globals.current_file, percent, left),
                            file=globals.current_file, progress=percent, remaining=round(remaining) if remaining is not None else None)
                    prev_percent = percent
                elif time.time() - last_eta >= ETA_EVERY:
                    send_eta(total_bytes_written)

            except PORT_ERRORS as e:
                plotlog.log('Serial error after {} bytes{}: {!r}'.format(
                    total_bytes_written, '' if globals.printing else ' (the plot was being stopped)', e))
                if not globals.printing:
                    break
                if cal:
                    # The state of the plotter (pen, stall, speed) cannot be put back from here
                    socketio.emit('error', {'data': 'Lost the connection to the plotter: ' + str(e)})
                    return False
                lost_at = time.time()
                if use_buffer:
                    unprocessed = (bufsz - bufsp) + last_len
                else:
                    unprocessed = REWIND_HPIB if flowControl == 'HP-IB' else REWIND_NO_FEEDBACK
                result = reconnect(socketio, notify_name, e, tty, port, baud, flowControl, use_buffer, hpglfile,
                                   max(total_bytes_written - unprocessed, last_stop, 0), line=line)
                if result == 'failed':
                    return False
                if result == 'stopped':
                    tty = None
                    break
                tty, bufsz, total_bytes_written, pending = result
                bufsp = bufsz
                hpgl.seek(total_bytes_written)
                globals.sent_offset = total_bytes_written
                globals.buffer_used = 0
                last_len = 0
                paused_time += time.time() - lost_at
                socketio.emit('status_log', {'data': 'Plot carrying on.'})
        if not finished:
            # The plot was stopped from the UI
            if use_buffer and tty is not None:
                abort_plot(tty)
            elif cal and tty is not None:
                discard_unsent(tty)
                try:
                    tty.write(CAL_PEN_UP)   # queued behind what the plotter has buffered
                except PORT_ERRORS as e:
                    print(repr(e))
            globals.current_file = 'None'
            globals.start_stamp = 0
            socketio.emit('status_log', {'data': '*** Plot stopped.'})
            socketio.emit('end_of_print', {'data': 'True'})

        return True

    finally:
        # Runs on every exit: end of print, stop button, error or exception.
        # Without this the serial port can stay open and "printing" stays True.
        plotlog.log('Sender ended: {}, {} bytes sent, stop requested: {}'.format(
            'the whole file was sent' if finished else 'not finished', globals.sent_offset,
            'yes' if globals.stop_requested else 'no'))
        globals.printing = False
        if hpgl is not None:
            hpgl.close()
        if tty is not None:
            if not finished:
                discard_unsent(tty)     # close() would wait for it (up to 30 s) while the plot lock is held
            try:
                tty.close()
            except PORT_ERRORS as e:
                print(repr(e))
