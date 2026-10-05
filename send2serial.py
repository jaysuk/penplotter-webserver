import time
import math
import os
import serial
import serial.tools.list_ports
from serial import SerialException

import notification
import globals
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


def listComPorts():
    ports = dict(name='ports', content=[])
    for port in sorted(serial.tools.list_ports.comports(), key=lambda p: p.device):
        ports['content'].append(port.device)
    return ports


def getBaudRate(t_port):
    baud_dict = [9600, 19200, 38400, 4800, 2400, 1200]
    message = 'IN;OI;OE'
    try:
        ser = serial.Serial(port=t_port, timeout=0.3)
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


def open_port(socketio, port, baud, flowControl):
    """Open and initialise the serial port for the given flow control. Returns None on failure."""
    try:
        if flowControl == 'XON/XOFF':
            tty = serial.Serial(port=port, baudrate=baud, parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_ONE, bytesize=serial.EIGHTBITS, xonxoff=True, timeout=2.0)
            tty.write(b'IN;\033.I80;;17:\033.N10;19:\033.@;0:')
        elif flowControl == 'HP-IB':
            tty = serial.Serial(port=port, baudrate=9600, parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_ONE, bytesize=serial.EIGHTBITS, rtscts=True, timeout=2.0)
        else:
            # CTS is polled by hand (see sendToPlotter) because of a pyserial bug with rtscts
            tty = serial.Serial(port=port, baudrate=baud, parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_ONE, bytesize=serial.EIGHTBITS, timeout=2.0)
            tty.write(b'IN;\033.R')
            time.sleep(0.2)
        return tty
    except PORT_ERRORS as e:
        socketio.emit('error', {'data': repr(e)})
        print(repr(e))
        return None


def abort_plot(tty):
    """Stop the plotter drawing what is already in its buffer (best effort)."""
    try:
        tty.write(b'\033.K')  # abort graphics instruction, flushes the plotter's buffer
        tty.write(b'PU;')     # pen up
    except PORT_ERRORS as e:
        print(repr(e))


def sendToPlotter(socketio, hpglfile, port, baud, flowControl):
    """Stream an HPGL file to the plotter. Returns True if the plot finished or was stopped."""

    PLOTTER_NAME = config.get('plotter', 'name', fallback='Plotter')
    notify_name = PLOTTER_NAME.replace(' ', '-')

    flowControl = str(flowControl)
    if flowControl.upper() == 'NONE':
        flowControl = 'NONE'

    # Only plotters that can report their buffer size and free space support buffer based flow control
    use_buffer = flowControl not in ('HP-IB', 'XON/XOFF', 'NONE')

    globals.printing = True
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

        tty = open_port(socketio, port, baud, flowControl)
        if tty is None:
            return False

        try:
            plotter_id = getReplySTR(tty, b'IN;OI;')
            socketio.emit('status_log', {'data': 'Plotter identifies as ' + plotter_id})
        except HPGLError as e:
            socketio.emit('status_log', {'data': 'Plotter did not reply, sending plot anyway!'})

        print('Configured for ', flowControl, ' flow control.')
        socketio.emit('status_log', {'data': 'Configured for ' + str(flowControl) + ' flow control.'})

        total_bytes_written = 0

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
                notification.telegram_sendNotification(notify_name + ': Error initializing the plotter')
                return False

            print('Size of plotter buffer is ', bufsz, ' bytes.')
            socketio.emit('status_log', {'data': 'Size of plotter buffer is ' + str(bufsz) + ' bytes.'})
            socketio.emit('buffer_size', {'data': str(bufsz)})

        globals.current_file = hpglfile.replace('uploads/', '').replace('.hpgl', '')
        globals.start_stamp = time.time()
        notification.telegram_sendNotification(notify_name + ': ' + globals.current_file + ': Starting')

        prev_percent = 0

        while globals.printing == True:

            if flowControl == 'HP-IB':
                data = hpgl.read(1)
            elif bufsz < 80:
                data = hpgl.read(10)
            else:
                data = hpgl.read(30)
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

            tty.write(data)
            total_bytes_written += bufsz_read

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
                notification.telegram_sendNotification(notify_name + ': ' + globals.current_file + ': Finished' + ': ' + str(minutes) + ' Minutes Total')
                globals.current_file = 'None'
                globals.start_stamp = 0
                socketio.emit('bytes_written', {'data': f'**EOP** - {total_bytes_written} bytes sent. Exiting.'})
                socketio.emit('print_progress', {'data': 100})
                socketio.emit('end_of_print', {'data': 'True'})
                finished = True
                break

            percent = int(100.0 * total_bytes_written / input_bytes)
            if percent != prev_percent:
                socketio.emit('bytes_written', {'data': f'{percent:.0f}%, {total_bytes_written} bytes written.'})
                socketio.emit('print_progress', {'data': percent})
                prev_percent = percent

        if not finished:
            # The plot was stopped from the UI
            if use_buffer:
                abort_plot(tty)
            globals.current_file = 'None'
            globals.start_stamp = 0
            socketio.emit('status_log', {'data': '*** Plot stopped.'})
            socketio.emit('end_of_print', {'data': 'True'})

        return True

    finally:
        # Runs on every exit: end of print, stop button, error or exception.
        # Without this the serial port can stay open and "printing" stays True.
        globals.printing = False
        if hpgl is not None:
            hpgl.close()
        if tty is not None:
            tty.close()
