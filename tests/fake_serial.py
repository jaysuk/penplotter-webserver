"""A fake pyserial so the tests need no hardware (and work even when pyserial is not installed)."""
import time
import types


class SerialException(Exception):
    pass


class Serial:
    # Test hooks
    instances = []        # every port opened
    fail_open = False     # make opening raise SerialException
    no_reply = False      # read() returns nothing (plotter not answering)
    on_data = None        # called with (port, data) for every chunk of plot data written
    on_write = None       # called with (port, data) for every write (except a CalComp status request), HP-GL queries too
    break_at = None       # the next port raises SerialException on its Nth write (an unplugged cable)
    open_failures = 0     # the next N attempts to open a port fail (the adapter is not back yet)
    failures_after_break = 0   # open_failures is set to this when a port breaks
    status_answer = None  # called with the port for a CalComp buffer status request (Ctrl-Q); returns the answer byte
    polls = 0             # how many buffer status requests were made
    backlog = 0           # bytes every port reports as written but not yet sent (a plotter that holds the line)

    def __init__(self, *args, **kwargs):
        # Like pyserial: without a port nothing is opened until open() is called
        self.kwargs = kwargs
        self.port = kwargs.get('port')
        self.baudrate = kwargs.get('baudrate')
        self.dtr = None         # set before open() by the caller, or never
        self.rts = None
        self.buf = b''
        self.written = []
        self.closed = False
        self.flushed = 0        # times the unsent output was discarded
        self.writes_left = None
        self.is_open = False
        if self.port is not None:
            self.open()

    def open(self):
        if Serial.fail_open:
            raise SerialException('could not open port')
        if Serial.open_failures > 0:
            Serial.open_failures -= 1
            raise SerialException('could not open port')
        self.writes_left = Serial.break_at
        Serial.break_at = None
        self.is_open = True
        Serial.instances.append(self)

    def write(self, data):
        if self.writes_left is not None:
            self.writes_left -= 1
            if self.writes_left < 0:
                Serial.open_failures = Serial.failures_after_break
                raise SerialException('device disconnected')
        self.written.append(data)
        if Serial.on_write and data != b'\x11':
            Serial.on_write(self, data)
        # Answer the queries send2serial makes: buffer size, buffer space, plotter id
        if data in (b'\033.L', b'\033.B'):
            self.buf += b'1024\r'
        elif data == b'IN;OI;':
            self.buf += b'7475A\r'
        elif data == b'OA;':
            self.buf += b'4019,-2009,1\r'
        elif data == b'\x11':       # CalComp: request buffer status. Default answer: Ctrl-A, "buffer empty"
            Serial.polls += 1
            self.buf += Serial.status_answer(self) if Serial.status_answer else b'\x01'
        elif data[:1] not in (b'\033', b'I', b'P'):
            time.sleep(0.0002)
            if Serial.on_data:
                Serial.on_data(self, data)

    def read(self, size=1):
        if Serial.no_reply:
            return b''
        out, self.buf = self.buf[:size], self.buf[size:]
        return out

    def read_until(self, *args, **kwargs):
        if Serial.no_reply:
            return b''
        out, self.buf = self.buf, b''
        return out

    def getCTS(self):
        return True

    def reset_input_buffer(self):
        self.buf = b''

    @property
    def out_waiting(self):
        return Serial.backlog

    def reset_output_buffer(self):
        self.flushed += 1
        Serial.backlog = 0

    def close(self):
        self.closed = True


class _Port:
    def __init__(self, device):
        self.device = device


def reset():
    Serial.instances = []
    Serial.fail_open = False
    Serial.no_reply = False
    Serial.on_data = None
    Serial.on_write = None
    Serial.break_at = None
    Serial.open_failures = 0
    Serial.failures_after_break = 0
    Serial.status_answer = None
    Serial.polls = 0
    Serial.backlog = 0


def make_modules():
    """Build the module objects to put in sys.modules in place of pyserial."""
    serial = types.ModuleType('serial')
    serial.Serial = Serial
    serial.SerialException = SerialException
    serial.PARITY_NONE, serial.STOPBITS_ONE, serial.EIGHTBITS = 'N', 1, 8

    tools = types.ModuleType('serial.tools')
    list_ports = types.ModuleType('serial.tools.list_ports')
    list_ports.comports = lambda: [_Port('/dev/ttyUSB1'), _Port('/dev/ttyAMA0')]
    serial.tools = tools
    tools.list_ports = list_ports
    return {'serial': serial, 'serial.tools': tools, 'serial.tools.list_ports': list_ports}
