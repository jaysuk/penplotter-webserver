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

    def __init__(self, *args, **kwargs):
        if Serial.fail_open:
            raise SerialException('could not open port')
        self.kwargs = kwargs
        self.baudrate = kwargs.get('baudrate')
        self.buf = b''
        self.written = []
        self.closed = False
        Serial.instances.append(self)

    def write(self, data):
        self.written.append(data)
        # Answer the queries send2serial makes: buffer size, buffer space, plotter id
        if data in (b'\033.L', b'\033.B'):
            self.buf += b'1024\r'
        elif data == b'IN;OI;':
            self.buf += b'7475A\r'
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
        out, self.buf = self.buf, b''
        return out

    def getCTS(self):
        return True

    def reset_input_buffer(self):
        self.buf = b''

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
